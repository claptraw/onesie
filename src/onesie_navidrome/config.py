from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from .errors import ConfigError

DEFAULT_AUDIO_EXTENSIONS = frozenset(
    {".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".aiff", ".aif", ".alac", ".wma"}
)
DEFAULT_CLEANUP_FILES = ("cover.jpg", "cover.webp", "cover.mp4")


def _as_bool(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    raise ConfigError(f"{name} must be a boolean")


def _as_int(value: Any, name: str, minimum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if parsed < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return parsed


def _duration_seconds(value: Any, name: str) -> int:
    if isinstance(value, int):
        return _as_int(value, name, 1)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a duration such as 7d, 12h, or 30m")
    text = value.strip().lower()
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    suffix = text[-1]
    if suffix not in units:
        raise ConfigError(f"{name} has unsupported duration unit: {suffix}")
    try:
        amount = int(text[:-1])
    except ValueError as exc:
        raise ConfigError(f"{name} must be a duration such as 7d, 12h, or 30m") from exc
    if amount < 1:
        raise ConfigError(f"{name} must be greater than zero")
    return amount * units[suffix]


def _mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{key} must be a mapping")
    return value


@dataclass(frozen=True)
class NavidromeConfig:
    url: str
    username: str
    password: str
    client: str
    api_version: str
    server_music_root: PurePosixPath
    verify_tls: bool
    request_timeout: int
    page_size: int
    trigger_scan: bool


@dataclass(frozen=True)
class PolicyConfig:
    delete_rating: int
    grace_period_seconds: int
    max_deletions_per_run: int
    dry_run: bool
    strict_validation: bool


@dataclass(frozen=True)
class PathMappingConfig:
    server_root: PurePosixPath
    local_root: Path


@dataclass(frozen=True)
class FilesystemConfig:
    music_root: Path
    path_mappings: tuple[PathMappingConfig, ...]
    allowed_extensions: frozenset[str]
    sidecars: tuple[str, ...]
    prune_empty_dirs: bool
    cleanup_files: tuple[str, ...]


@dataclass(frozen=True)
class BeetsConfig:
    executable: str
    config_file: Path | None


@dataclass(frozen=True)
class NotificationConfig:
    enabled: bool
    apprise_config: Path | None
    tag: str
    notify_on_noop: bool
    notify_on_dry_run: bool
    notify_before_deletion: bool
    warning_before_deletion_seconds: int
    warning_retry_interval_seconds: int
    final_warning_window_seconds: int
    warning_failure_postpone_seconds: int
    notify_after_deletion: bool


@dataclass(frozen=True)
class RuntimeConfig:
    state_file: Path
    audit_log: Path


@dataclass(frozen=True)
class Config:
    navidrome: NavidromeConfig
    policy: PolicyConfig
    filesystem: FilesystemConfig
    backend: str
    beets: BeetsConfig
    notifications: NotificationConfig
    runtime: RuntimeConfig

    @classmethod
    def load(cls, path: Path) -> "Config":
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except FileNotFoundError as exc:
            raise ConfigError(f"Configuration file not found: {path}") from exc
        except yaml.YAMLError as exc:
            raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError("Top-level configuration must be a mapping")

        nav = _mapping(raw, "navidrome")
        policy = _mapping(raw, "policy")
        delete = _mapping(raw, "delete")
        filesystem = _mapping(raw, "filesystem")
        beets = _mapping(raw, "beets")
        notifications = _mapping(raw, "notifications")
        runtime = _mapping(raw, "runtime")

        url = str(os.getenv("ONESIE_NAVIDROME_URL", nav.get("url", ""))).strip().rstrip("/")
        username = str(os.getenv("ONESIE_NAVIDROME_USERNAME", nav.get("username", ""))).strip()
        password = os.getenv("ONESIE_NAVIDROME_PASSWORD")
        if password is None:
            password_env = str(nav.get("password_env", "")).strip()
            if password_env:
                password = os.getenv(password_env)
            if password is None:
                password = str(nav.get("password", ""))
        if not url:
            raise ConfigError("navidrome.url is required")
        if not username:
            raise ConfigError("navidrome.username is required")
        if not password:
            raise ConfigError(
                "Navidrome password is required via ONESIE_NAVIDROME_PASSWORD, navidrome.password_env, or navidrome.password"
            )

        server_root = PurePosixPath(str(nav.get("server_music_root", "/music")))
        if not server_root.is_absolute():
            raise ConfigError("navidrome.server_music_root must be an absolute POSIX path")

        music_root = Path(str(filesystem.get("music_root", ""))).expanduser()
        raw_mappings = filesystem.get("path_mappings")
        mappings: list[PathMappingConfig] = []
        if raw_mappings is not None:
            if not isinstance(raw_mappings, list) or not raw_mappings:
                raise ConfigError("filesystem.path_mappings must be a non-empty list")
            for index, entry in enumerate(raw_mappings):
                if not isinstance(entry, dict):
                    raise ConfigError(f"filesystem.path_mappings[{index}] must be a mapping")
                server = PurePosixPath(str(entry.get("server_root", "")))
                local = Path(str(entry.get("local_root", ""))).expanduser()
                if not server.is_absolute():
                    raise ConfigError(f"filesystem.path_mappings[{index}].server_root must be absolute")
                if not local.is_absolute():
                    raise ConfigError(f"filesystem.path_mappings[{index}].local_root must be absolute")
                mappings.append(PathMappingConfig(server_root=server, local_root=local))
            music_root = mappings[0].local_root
        else:
            if not str(music_root):
                raise ConfigError("filesystem.music_root is required when path_mappings is not configured")
            if not music_root.is_absolute():
                raise ConfigError("filesystem.music_root must be an absolute local path")
            mappings.append(PathMappingConfig(server_root=server_root, local_root=music_root))

        if len({m.server_root.as_posix() for m in mappings}) != len(mappings):
            raise ConfigError("filesystem.path_mappings contains duplicate server_root entries")

        backend = str(delete.get("backend", "filesystem")).strip().lower()
        if backend not in {"filesystem", "beets"}:
            raise ConfigError("delete.backend must be 'filesystem' or 'beets'")

        delete_rating = _as_int(policy.get("delete_rating", 1), "policy.delete_rating", 1)
        if delete_rating > 5:
            raise ConfigError("policy.delete_rating must be between 1 and 5")
        grace_period_seconds = _duration_seconds(policy.get("grace_period", "7d"), "policy.grace_period")

        raw_exts = filesystem.get("allowed_extensions")
        if raw_exts is None:
            extensions = DEFAULT_AUDIO_EXTENSIONS
        elif isinstance(raw_exts, list):
            parsed = set()
            for ext in raw_exts:
                text = str(ext).strip().lower()
                if not text:
                    continue
                parsed.add(text if text.startswith(".") else f".{text}")
            extensions = frozenset(parsed)
        else:
            raise ConfigError("filesystem.allowed_extensions must be a list")
        if not extensions:
            raise ConfigError("filesystem.allowed_extensions may not be empty")

        raw_sidecars = filesystem.get("sidecars", [".lrc"])
        if not isinstance(raw_sidecars, list):
            raise ConfigError("filesystem.sidecars must be a list")
        sidecars: list[str] = []
        for suffix in raw_sidecars:
            text = str(suffix).strip().lower()
            if not text:
                continue
            if not text.startswith(".") or "/" in text or "\\" in text:
                raise ConfigError(f"Invalid sidecar suffix: {suffix!r}")
            sidecars.append(text)

        raw_cleanup_files = filesystem.get("cleanup_files", list(DEFAULT_CLEANUP_FILES))
        if not isinstance(raw_cleanup_files, list):
            raise ConfigError("filesystem.cleanup_files must be a list")
        cleanup_files: list[str] = []
        for filename in raw_cleanup_files:
            text = str(filename).strip()
            if not text:
                continue
            if text in {".", ".."} or "/" in text or "\\" in text:
                raise ConfigError(f"Invalid cleanup filename: {filename!r}")
            cleanup_files.append(text.lower())

        warning_before_seconds = _duration_seconds(
            notifications.get("warning_before_deletion", "2d"),
            "notifications.warning_before_deletion",
        )
        warning_retry_seconds = _duration_seconds(
            notifications.get("warning_retry_interval", "12h"),
            "notifications.warning_retry_interval",
        )
        final_warning_window_seconds = _duration_seconds(
            notifications.get("final_warning_window", "12h"),
            "notifications.final_warning_window",
        )
        warning_failure_postpone_seconds = _duration_seconds(
            notifications.get("warning_failure_postpone", "1d"),
            "notifications.warning_failure_postpone",
        )
        if warning_before_seconds >= grace_period_seconds:
            raise ConfigError("notifications.warning_before_deletion must be shorter than policy.grace_period")
        if final_warning_window_seconds > warning_before_seconds:
            raise ConfigError(
                "notifications.final_warning_window must not be longer than notifications.warning_before_deletion"
            )

        beets_config_file = beets.get("config_file")
        apprise_config = notifications.get("apprise_config")

        return cls(
            navidrome=NavidromeConfig(
                url=url,
                username=username,
                password=str(password),
                client=str(nav.get("client", "onesie")).strip() or "onesie",
                api_version=str(nav.get("api_version", "1.16.1")).strip() or "1.16.1",
                server_music_root=server_root,
                verify_tls=_as_bool(nav.get("verify_tls", True), "navidrome.verify_tls"),
                request_timeout=_as_int(nav.get("request_timeout", 20), "navidrome.request_timeout", 1),
                page_size=_as_int(nav.get("page_size", 500), "navidrome.page_size", 1),
                trigger_scan=_as_bool(nav.get("trigger_scan", True), "navidrome.trigger_scan"),
            ),
            policy=PolicyConfig(
                delete_rating=delete_rating,
                grace_period_seconds=grace_period_seconds,
                max_deletions_per_run=_as_int(
                    policy.get("max_deletions_per_run", 20), "policy.max_deletions_per_run", 1
                ),
                dry_run=_as_bool(policy.get("dry_run", True), "policy.dry_run"),
                strict_validation=_as_bool(policy.get("strict_validation", True), "policy.strict_validation"),
            ),
            filesystem=FilesystemConfig(
                music_root=music_root,
                path_mappings=tuple(mappings),
                allowed_extensions=extensions,
                sidecars=tuple(sidecars),
                prune_empty_dirs=_as_bool(
                    filesystem.get("prune_empty_dirs", False), "filesystem.prune_empty_dirs"
                ),
                cleanup_files=tuple(cleanup_files),
            ),
            backend=backend,
            beets=BeetsConfig(
                executable=str(beets.get("executable", "beet")).strip() or "beet",
                config_file=Path(str(beets_config_file)).expanduser() if beets_config_file else None,
            ),
            notifications=NotificationConfig(
                enabled=_as_bool(notifications.get("enabled", False), "notifications.enabled"),
                apprise_config=Path(str(apprise_config)).expanduser() if apprise_config else None,
                tag=str(notifications.get("tag", "")).strip(),
                notify_on_noop=_as_bool(notifications.get("notify_on_noop", False), "notifications.notify_on_noop"),
                notify_on_dry_run=_as_bool(
                    notifications.get("notify_on_dry_run", False), "notifications.notify_on_dry_run"
                ),
                notify_before_deletion=_as_bool(
                    notifications.get("notify_before_deletion", True), "notifications.notify_before_deletion"
                ),
                warning_before_deletion_seconds=warning_before_seconds,
                warning_retry_interval_seconds=warning_retry_seconds,
                final_warning_window_seconds=final_warning_window_seconds,
                warning_failure_postpone_seconds=warning_failure_postpone_seconds,
                notify_after_deletion=_as_bool(
                    notifications.get("notify_after_deletion", True), "notifications.notify_after_deletion"
                ),
            ),
            runtime=RuntimeConfig(
                state_file=Path(str(runtime.get("state_file", "./state/onesie-state.json"))).expanduser(),
                audit_log=Path(str(runtime.get("audit_log", "./state/onesie-audit.jsonl"))).expanduser(),
            ),
        )
