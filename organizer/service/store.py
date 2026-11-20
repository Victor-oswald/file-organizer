"""Tiny JSON persistence: settings, history and per-folder 'already handled' lists.

All writes are atomic (temp file + os.replace), so a crash or power cut can never
leave a half-written settings file.
"""

from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any

from .paths import app_data_dir

DEFAULT_CONFIG = {"version": 1, "paused": False, "interval": 5, "folders": []}


class Store:
    def __init__(self, base: Path | None = None) -> None:
        self.base = Path(base) if base else app_data_dir()
        self.base.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.config: dict[str, Any] = {**copy.deepcopy(DEFAULT_CONFIG), **self._load("config.json", {})}
        self.batches: list[dict[str, Any]] = self._load("batches.json", [])
        self._seen: dict[str, dict[str, list]] = {}

    # -- low level -------------------------------------------------------
    def _load(self, name: str, default: Any) -> Any:
        try:
            with open(self.base / name, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return default

    def _save(self, name: str, data: Any) -> None:
        path = self.base / name
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1, ensure_ascii=False)
        os.replace(tmp, path)

    # -- public ----------------------------------------------------------
    def save_config(self) -> None:
        with self.lock:
            self._save("config.json", self.config)

    def save_batches(self) -> None:
        with self.lock:
            del self.batches[:-300]                  # keep the last 300 batches
            self._save("batches.json", self.batches)

    def seen(self, folder_id: str) -> dict[str, list]:
        with self.lock:
            if folder_id not in self._seen:
                self._seen[folder_id] = self._load(f"seen_{folder_id}.json", {})
            return self._seen[folder_id]

    def save_seen(self, folder_id: str) -> None:
        with self.lock:
            self._save(f"seen_{folder_id}.json", self._seen.get(folder_id, {}))

    def forget_seen(self, folder_id: str) -> None:
        with self.lock:
            self._seen.pop(folder_id, None)
            try:
                (self.base / f"seen_{folder_id}.json").unlink()
            except OSError:
                pass
