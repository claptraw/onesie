from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MappedSong:
    key: str
    path: Path
    navidrome: dict[str, Any]

    @property
    def id(self) -> str:
        return str(self.navidrome.get("id", ""))

    @property
    def artist(self) -> str:
        return str(self.navidrome.get("artist", ""))

    @property
    def album(self) -> str:
        return str(self.navidrome.get("album", ""))

    @property
    def title(self) -> str:
        return str(self.navidrome.get("title", ""))


@dataclass(frozen=True)
class BackendTarget:
    mapped: MappedSong
    backend_ref: str | None = None


@dataclass(frozen=True)
class DeleteResult:
    target: BackendTarget
    deleted: bool
    detail: str = ""
