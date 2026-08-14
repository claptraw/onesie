from __future__ import annotations

from ..errors import BackendError, SafetyError
from ..models import BackendTarget, DeleteResult, MappedSong
from .base import DeletionBackend


class FilesystemBackend(DeletionBackend):
    name = "filesystem"

    def preflight(self) -> None:
        return None

    def prepare(self, mapped: MappedSong) -> BackendTarget:
        if not mapped.path.exists() or not mapped.path.is_file() or mapped.path.is_symlink():
            raise SafetyError(f"Filesystem backend refuses non-regular audio path: {mapped.path}")
        return BackendTarget(mapped=mapped)

    def delete(self, target: BackendTarget) -> DeleteResult:
        try:
            target.mapped.path.unlink()
        except OSError as exc:
            raise BackendError(f"Could not delete audio file {target.mapped.path}: {exc}") from exc
        if target.mapped.path.exists():
            raise BackendError(f"Audio file still exists after deletion: {target.mapped.path}")
        return DeleteResult(target=target, deleted=True, detail="filesystem unlink")
