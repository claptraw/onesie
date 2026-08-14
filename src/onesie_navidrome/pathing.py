from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

from .config import FilesystemConfig, NavidromeConfig, PathMappingConfig
from .errors import SafetyError
from .models import MappedSong


class PathMapper:
    def __init__(self, navidrome: NavidromeConfig, filesystem: FilesystemConfig):
        self.navidrome = navidrome
        self.filesystem = filesystem

    def _select_mapping(self, raw: PurePosixPath) -> tuple[PathMappingConfig, PurePosixPath]:
        matches: list[tuple[int, PathMappingConfig, PurePosixPath]] = []
        for mapping in self.filesystem.path_mappings:
            try:
                relative = raw.relative_to(mapping.server_root)
            except ValueError:
                continue
            matches.append((len(mapping.server_root.parts), mapping, relative))
        if not matches:
            roots = ", ".join(m.server_root.as_posix() for m in self.filesystem.path_mappings)
            raise SafetyError(f"Navidrome path is outside configured server roots ({roots}): {raw}")
        _, mapping, relative = max(matches, key=lambda item: item[0])
        return mapping, relative

    def map_song(self, song: dict[str, Any], *, require_exists: bool = True) -> MappedSong:
        raw_path = song.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise SafetyError(f"Navidrome song {song.get('id', '?')} has no usable path")
        raw = PurePosixPath(raw_path)
        if ".." in raw.parts:
            raise SafetyError(f"Rejected path traversal from Navidrome: {raw_path}")
        if not raw.is_absolute():
            raise SafetyError(
                "Navidrome returned a synthetic/relative Subsonic path. Enable Report Real Path "
                f"for the Onesie player (or Navidrome Subsonic.DefaultReportRealPath): {raw_path}"
            )
        mapping, relative = self._select_mapping(raw)
        if not relative.parts or relative == PurePosixPath("."):
            raise SafetyError(f"Rejected empty music path from Navidrome: {raw_path}")

        local_root = mapping.local_root.absolute()
        root_resolved = local_root.resolve(strict=True)
        cursor = local_root
        for part in relative.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise SafetyError(f"Symlinked music path is not allowed: {cursor}")
        candidate = local_root.joinpath(*relative.parts)

        if require_exists:
            if not candidate.exists():
                raise SafetyError(f"Mapped audio file does not exist: {candidate}")
            if not candidate.is_file():
                raise SafetyError(f"Mapped path is not a regular file: {candidate}")
            resolved = candidate.resolve(strict=True)
        else:
            resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(root_resolved)
        except ValueError as exc:
            raise SafetyError(f"Resolved path escaped local music root: {resolved}") from exc
        if candidate.suffix.lower() not in self.filesystem.allowed_extensions:
            raise SafetyError(f"Unsupported audio extension for deletion: {candidate}")

        # The real server-side path is path-based and globally unique across multiple Navidrome music folders.
        return MappedSong(key=raw.as_posix(), path=candidate, navidrome=song)
