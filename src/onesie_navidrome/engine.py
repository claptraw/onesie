from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .audit import AuditLog, iso_z, parse_time
from .backends import BeetsCliBackend, DeletionBackend, FilesystemBackend
from .config import Config
from .errors import BackendError, OnesieError, SafetyError
from .models import BackendTarget, MappedSong
from .navidrome import NavidromeClient
from .notifications import Notifier
from .pathing import PathMapper
from .state import StateStore


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def rating(song: dict[str, Any]) -> int:
    try:
        return int(song.get("userRating", 0) or 0)
    except (TypeError, ValueError):
        return 0


class OnesieEngine:
    def __init__(self, config: Config, logger: logging.Logger | None = None):
        self.config = config
        self.logger = logger or logging.getLogger("onesie")
        self.audit = AuditLog(config.runtime.audit_log)
        self.state = StateStore(config.runtime.state_file)
        self.navidrome = NavidromeClient(config.navidrome)
        self.mapper = PathMapper(config.navidrome, config.filesystem)
        self.notifier = Notifier(config.notifications, self.logger)
        self.backend: DeletionBackend = (
            BeetsCliBackend(
                config.beets, tuple(mapping.local_root for mapping in config.filesystem.path_mappings)
            )
            if config.backend == "beets"
            else FilesystemBackend()
        )

    def _prepare_all(self, mapped: list[MappedSong]) -> dict[str, BackendTarget]:
        self.backend.preflight()
        targets: dict[str, BackendTarget] = {}
        errors: list[str] = []
        for song in mapped:
            try:
                targets[song.key] = self.backend.prepare(song)
            except SafetyError as exc:
                errors.append(str(exc))
        if errors and self.config.policy.strict_validation:
            raise SafetyError(
                f"{len(errors)} delete marker(s) failed {self.backend.name} validation. "
                f"No state or files changed. First error: {errors[0]}"
            )
        for error in errors:
            self.logger.warning("Skipping invalid delete marker: %s", error)
        return targets

    def _reconcile(
        self, state: dict[str, Any], mapped: list[MappedSong], now: datetime
    ) -> tuple[list[str], list[str]]:
        tracks = state["tracks"]
        current = {song.key: song for song in mapped}
        new: list[str] = []
        cancelled: list[str] = []
        for key, song in current.items():
            if key not in tracks:
                tracks[key] = {
                    "first_seen": iso_z(now),
                    "last_seen": iso_z(now),
                    "navidrome_id": song.id,
                    "artist": song.artist,
                    "album": song.album,
                    "title": song.title,
                }
                new.append(key)
                self.audit.write("queued", path=key, navidrome_id=song.id, artist=song.artist, album=song.album, title=song.title)
            else:
                tracks[key].update(
                    {
                        "last_seen": iso_z(now),
                        "navidrome_id": song.id,
                        "artist": song.artist,
                        "album": song.album,
                        "title": song.title,
                    }
                )
        for key in list(tracks):
            if key not in current:
                old = tracks.pop(key)
                cancelled.append(key)
                self.audit.write(
                    "cancelled",
                    path=key,
                    navidrome_id=old.get("navidrome_id", ""),
                    reason="rating_no_longer_delete_marker",
                )
        return new, cancelled

    def _eligible(self, state: dict[str, Any], current: dict[str, MappedSong], now: datetime) -> list[MappedSong]:
        cutoff = now - timedelta(seconds=self.config.policy.grace_period_seconds)
        eligible: list[MappedSong] = []
        for key, song in current.items():
            record = state["tracks"].get(key)
            if not record:
                continue
            if parse_time(record["first_seen"]) <= cutoff:
                eligible.append(song)
        return eligible

    def _live_verify(self, mapped: MappedSong) -> MappedSong:
        if not mapped.id:
            raise SafetyError(f"Queued song has no Navidrome id: {mapped.key}")
        live = self.navidrome.get_song(mapped.id)
        if rating(live) != self.config.policy.delete_rating:
            raise SafetyError(f"Rating changed immediately before deletion: {mapped.key}")
        remapped = self.mapper.map_song(live)
        if remapped.key != mapped.key:
            raise SafetyError(f"Path changed immediately before deletion: {mapped.key} -> {remapped.key}")
        return remapped

    def _remove_sidecars(self, audio_path: Path) -> list[str]:
        removed: list[str] = []
        for suffix in self.config.filesystem.sidecars:
            sidecar = audio_path.with_suffix(suffix)
            if not sidecar.exists():
                continue
            if sidecar.is_symlink() or not sidecar.is_file():
                self.logger.warning("Leaving unexpected sidecar type untouched: %s", sidecar)
                continue
            try:
                sidecar.unlink()
                removed.append(str(sidecar))
            except OSError as exc:
                self.logger.warning("Could not remove sidecar %s: %s", sidecar, exc)
        return removed

    def _prune_empty_parents(self, start: Path) -> list[str]:
        if not self.config.filesystem.prune_empty_dirs:
            return []
        candidates = []
        start_resolved = start.resolve(strict=False)
        for mapping in self.config.filesystem.path_mappings:
            root_candidate = mapping.local_root.resolve(strict=True)
            try:
                start_resolved.relative_to(root_candidate)
            except ValueError:
                continue
            candidates.append(root_candidate)
        if not candidates:
            raise SafetyError(f"Refusing to prune directory outside configured local roots: {start}")
        root = max(candidates, key=lambda item: len(item.parts))
        removed: list[str] = []
        current = start
        while current != root:
            try:
                current.resolve(strict=False).relative_to(root)
            except ValueError:
                raise SafetyError(f"Refusing to prune directory outside music root: {current}")
            try:
                current.rmdir()
            except OSError:
                break
            removed.append(str(current))
            current = current.parent
        return removed

    def run_once(self, *, force_dry_run: bool = False) -> int:
        now = utcnow()
        dry_run = self.config.policy.dry_run or force_dry_run
        self.logger.info("Onesie run started: backend=%s dry_run=%s", self.backend.name, dry_run)
        self.audit.write("run_started", backend=self.backend.name, dry_run=dry_run)
        self.navidrome.ping()
        songs = self.navidrome.all_songs()
        marked_raw = [s for s in songs if rating(s) == self.config.policy.delete_rating]
        self.logger.info("Navidrome returned %d songs; %d delete marker(s)", len(songs), len(marked_raw))

        mapped: list[MappedSong] = []
        mapping_errors: list[str] = []
        for song in marked_raw:
            try:
                mapped.append(self.mapper.map_song(song))
            except SafetyError as exc:
                mapping_errors.append(str(exc))
        if mapping_errors and self.config.policy.strict_validation:
            raise SafetyError(
                f"{len(mapping_errors)} delete marker(s) failed path validation. "
                f"No state or files changed. First error: {mapping_errors[0]}"
            )
        for error in mapping_errors:
            self.logger.warning("Skipping invalid delete marker: %s", error)

        targets = self._prepare_all(mapped)
        mapped = [song for song in mapped if song.key in targets]
        current = {song.key: song for song in mapped}

        state = self.state.load()
        new, cancelled = self._reconcile(state, mapped, now)
        self.state.save(state)
        eligible = self._eligible(state, current, now)
        self.logger.info(
            "Queue=%d new=%d cancelled=%d eligible=%d",
            len(state["tracks"]), len(new), len(cancelled), len(eligible),
        )

        if len(eligible) > self.config.policy.max_deletions_per_run:
            message = (
                f"{len(eligible)} tracks are eligible, exceeding max_deletions_per_run="
                f"{self.config.policy.max_deletions_per_run}. No files were deleted."
            )
            self.audit.write("guard_abort", reason="max_deletions_per_run", eligible=len(eligible))
            self.notifier.send("Onesie: batch guard aborted", message, "failure")
            raise SafetyError(message)

        if not eligible:
            if self.config.notifications.notify_on_noop:
                self.notifier.send("Onesie: nothing to delete", f"{len(state['tracks'])} track(s) queued.")
            return 0

        deleted: list[MappedSong] = []
        dry_run_items: list[MappedSong] = []
        failures: list[str] = []

        for original in eligible:
            try:
                live = self._live_verify(original)
                target = self.backend.prepare(live)
                if dry_run:
                    dry_run_items.append(live)
                    self.audit.write(
                        "dry_run",
                        path=live.key,
                        navidrome_id=live.id,
                        backend=self.backend.name,
                        backend_ref=target.backend_ref,
                    )
                    self.logger.info("DRY RUN: would delete %s - %s (%s)", live.artist, live.title, live.path)
                    continue
                result = self.backend.delete(target)
                if not result.deleted:
                    raise BackendError(f"Backend did not confirm deletion: {live.key}")
                if live.path.exists():
                    raise BackendError(f"Audio file still exists after backend deletion: {live.path}")
                sidecars = self._remove_sidecars(live.path)
                pruned = self._prune_empty_parents(live.path.parent)
                state["tracks"].pop(live.key, None)
                self.state.save(state)
                self.audit.write(
                    "deleted",
                    path=live.key,
                    navidrome_id=live.id,
                    artist=live.artist,
                    album=live.album,
                    title=live.title,
                    backend=self.backend.name,
                    backend_ref=target.backend_ref,
                    sidecars=sidecars,
                    pruned_directories=pruned,
                )
                deleted.append(live)
            except (OnesieError, OSError, RuntimeError) as exc:
                failures.append(f"{original.key}: {exc}")
                self.audit.write("delete_failed", path=original.key, detail=str(exc))
                self.logger.error("Deletion failed for %s: %s", original.key, exc)

        scan_warning = ""
        if deleted and self.config.navidrome.trigger_scan:
            try:
                self.navidrome.start_scan()
            except OnesieError as exc:
                scan_warning = f" Navidrome scan could not be started: {exc}"
                self.logger.warning(scan_warning.strip())
                self.audit.write("scan_failed", detail=str(exc))

        if failures:
            body = f"Deleted {len(deleted)} track(s); {len(failures)} failed. First failure: {failures[0]}.{scan_warning}"
            self.notifier.send("Onesie: deletion run completed with errors", body, "failure")
            return 2
        if deleted:
            self.notifier.send(
                "Onesie: deletion completed",
                f"Deleted {len(deleted)} track(s) via {self.backend.name}.{scan_warning}",
                "success",
            )
        elif dry_run_items and self.config.notifications.notify_on_dry_run:
            self.notifier.send(
                "Onesie: dry run",
                f"Would delete {len(dry_run_items)} track(s) via {self.backend.name}.",
                "info",
            )
        return 0

    def status(self) -> dict[str, Any]:
        state = self.state.load()
        now = utcnow()
        rows = []
        for key, record in sorted(state["tracks"].items()):
            first_seen = parse_time(record["first_seen"])
            age = max(0, int((now - first_seen).total_seconds()))
            rows.append({"path": key, **record, "age_seconds": age})
        return {"backend": self.backend.name, "dry_run": self.config.policy.dry_run, "tracks": rows}

    def doctor(self) -> list[str]:
        findings: list[str] = []
        for mapping in self.config.filesystem.path_mappings:
            root = mapping.local_root
            if not root.is_dir():
                raise SafetyError(f"configured local music root is not a directory: {root}")
            findings.append(f"music root mapping: OK ({mapping.server_root} -> {root})")
        self.navidrome.ping()
        findings.append("Navidrome authentication: OK")
        songs = self.navidrome.all_songs()
        findings.append(f"Navidrome catalog read: OK ({len(songs)} songs)")
        if songs:
            sample = songs[0]
            try:
                self.mapper.map_song(sample)
                findings.append("Report Real Path / path mapping: OK")
            except SafetyError as exc:
                findings.append(f"Report Real Path / path mapping: ATTENTION ({exc})")
        self.backend.preflight()
        findings.append(f"deletion backend: OK ({self.backend.name})")
        if self.config.notifications.enabled:
            self.notifier._load()
            findings.append("Apprise: OK")
        else:
            findings.append("Apprise: disabled")
        return findings
