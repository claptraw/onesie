from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .errors import SafetyError

STATE_VERSION = 3
LEGACY_STATE_VERSION = 2


class StateStore:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": STATE_VERSION, "tracks": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SafetyError(f"Could not read state file safely: {self.path}: {exc}") from exc
        if not isinstance(data.get("tracks"), dict):
            raise SafetyError(f"Unsupported or invalid state file: {self.path}")
        if data.get("version") == LEGACY_STATE_VERSION:
            data["version"] = STATE_VERSION
            return data
        if data.get("version") != STATE_VERSION:
            raise SafetyError(f"Unsupported or invalid state file: {self.path}")
        return data

    def save(self, data: dict[str, Any]) -> None:
        data["version"] = STATE_VERSION
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + ".tmp")
        with temp.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, self.path)
