from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..config import BeetsConfig
from ..errors import BackendError, SafetyError
from ..models import BackendTarget, DeleteResult, MappedSong
from .base import DeletionBackend


class BeetsCliBackend(DeletionBackend):
    """Use the user's own beet executable/config instead of opening its DB directly."""

    name = "beets"
    _FORMAT = "$id\t$path"

    def __init__(self, config: BeetsConfig, music_roots: tuple[Path, ...]):
        self.config = config
        self.music_roots = music_roots
        self._inventory: dict[str, str] | None = None

    def _base_command(self) -> list[str]:
        command = [self.config.executable]
        if self.config.config_file is not None:
            command.extend(["-c", str(self.config.config_file)])
        return command

    def _run(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [*self._base_command(), *args],
                capture_output=True,
                text=True,
                check=False,
                env=os.environ.copy(),
            )
        except OSError as exc:
            raise BackendError(f"Could not execute Beets command: {exc}") from exc

    def preflight(self) -> None:
        executable = self.config.executable
        if os.sep not in executable and shutil.which(executable) is None:
            raise SafetyError(f"Beets backend selected but executable was not found: {executable}")
        if self.config.config_file is not None and not self.config.config_file.is_file():
            raise SafetyError(f"Beets config file not found: {self.config.config_file}")
        result = self._run(["version"])
        if result.returncode != 0:
            raise SafetyError(f"Beets preflight failed: {result.stderr.strip() or result.stdout.strip()}")

    def _load_inventory(self) -> dict[str, str]:
        if self._inventory is not None:
            return self._inventory
        result = self._run(["ls", "-f", self._FORMAT])
        if result.returncode != 0:
            raise SafetyError(f"Could not enumerate Beets library: {result.stderr.strip()}")
        inventory: dict[str, str] = {}
        duplicate_paths: set[str] = set()
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            if "\t" not in line:
                raise SafetyError("Beets inventory output was not parseable; refusing deletion")
            item_id, raw_path = line.split("\t", 1)
            item_id = item_id.strip()
            raw_path = raw_path.strip()
            if not item_id.isdigit() or not raw_path:
                raise SafetyError("Beets inventory contained an invalid item id/path")
            parsed_path = Path(raw_path).expanduser()
            if not parsed_path.is_absolute():
                if len(self.music_roots) != 1:
                    raise SafetyError(
                        "Beets returned a relative path while multiple local roots are configured; mapping is ambiguous"
                    )
                parsed_path = self.music_roots[0] / parsed_path
            normalized = os.path.normcase(os.path.abspath(str(parsed_path)))
            if normalized in inventory:
                duplicate_paths.add(normalized)
            inventory[normalized] = item_id
        if duplicate_paths:
            raise SafetyError("Beets library contains duplicate exact paths; refusing deletion")
        self._inventory = inventory
        return inventory

    def prepare(self, mapped: MappedSong) -> BackendTarget:
        inventory = self._load_inventory()
        normalized = os.path.normcase(os.path.abspath(str(mapped.path)))
        item_id = inventory.get(normalized)
        if item_id is None:
            raise SafetyError(f"No exact Beets item matches Navidrome path: {mapped.path}")
        return BackendTarget(mapped=mapped, backend_ref=item_id)

    def delete(self, target: BackendTarget) -> DeleteResult:
        if not target.backend_ref or not target.backend_ref.isdigit():
            raise SafetyError(f"Invalid Beets item id for {target.mapped.path}")
        result = self._run(["remove", "-d", "-f", f"id:{target.backend_ref}"])
        if result.returncode != 0:
            raise BackendError(
                f"beet remove failed for item {target.backend_ref}: {result.stderr.strip() or result.stdout.strip()}"
            )
        if target.mapped.path.exists():
            raise BackendError(f"Beets returned success but audio file still exists: {target.mapped.path}")
        self._inventory = None
        return DeleteResult(target=target, deleted=True, detail=f"beet remove id:{target.backend_ref}")
