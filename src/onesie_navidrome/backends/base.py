from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..models import BackendTarget, DeleteResult, MappedSong


class DeletionBackend(ABC):
    name: str

    @abstractmethod
    def preflight(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def prepare(self, mapped: MappedSong) -> BackendTarget:
        raise NotImplementedError

    @abstractmethod
    def delete(self, target: BackendTarget) -> DeleteResult:
        raise NotImplementedError

    @staticmethod
    def verify_removed(path: Path) -> None:
        if path.exists():
            raise RuntimeError(f"Backend reported success but audio file still exists: {path}")
