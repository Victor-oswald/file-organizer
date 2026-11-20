"""Stage 5: the dry-run preview file and the reversible undo log.

A manifest records, for every file, OLD PATH -> NEW PATH plus the confidence and
the reason. `apply_manifest` executes exactly what was previewed; `rollback_manifest`
puts every applied move back.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Callable

from .. import __version__, config
from ..models import Decision
from .directory_manager import DirectoryManager

MANIFEST_VERSION = 1


def build_manifest(dm: DirectoryManager, decisions: list[Decision], *, threshold: float,
                   dupes_mode: str) -> dict:
    ops = []
    for i, d in enumerate(decisions, 1):
        f = d.features
        ops.append({
            "id": i,
            "old_path": dm.relative(f.record.path),
            "new_path": dm.relative(d.dest),
            "action": d.action,
            "confidence": d.confidence,
            "reason": d.reason,
            "category": f.category,
            "magic_kind": f.magic_kind,
            "size": f.record.size,
            "partial_hash": f.partial_hash,
            "sha256": f.sha256,
            "duplicate_of": dm.relative(d.duplicate_of) if d.duplicate_of else None,
            "signals": d.signals,
            "status": "planned",
            "error": None,
        })
    return {
        "manifest_version": MANIFEST_VERSION,
        "tool_version": __version__,
        "created": datetime.now().isoformat(timespec="seconds"),
        "root": str(dm.root),
        "dry_run": True,
        "threshold": threshold,
        "dupes_mode": dupes_mode,
        "operations": ops,
    }


def save_manifest(data: dict, path: Path) -> None:
    """Atomic write: a crash can never leave a half-written manifest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def load_manifest(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("manifest_version") != MANIFEST_VERSION:
        raise ValueError(f"unsupported manifest version: {data.get('manifest_version')}")
    return data


def _abs(root: Path, rel: str) -> Path:
    return root.joinpath(*rel.split("/"))


def _move(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and os.path.samefile(src, dest):
        # case-only rename on a case-insensitive file system: go through a temp name
        tmp = src.with_name(src.name + ".organizer_tmp")
        os.rename(src, tmp)
        os.rename(tmp, dest)
        return
    try:
        os.rename(src, dest)
    except OSError:
        shutil.move(str(src), str(dest))


def decline_operations(path: Path, ids: set[int] | None = None) -> None:
    """Mark planned operations as declined (the user said no). They are never applied."""
    data = load_manifest(path)
    for op in data["operations"]:
        if op["status"] == "planned" and (ids is None or op["id"] in ids):
            op["status"] = "declined"
    save_manifest(data, path)


def apply_manifest(path: Path, log: Callable[[str], None] = print,
                   include_ids: set[int] | None = None) -> dict[str, int]:
    """Execute a previewed manifest. With `include_ids`, only those operations run;
    the others are marked declined."""
    data = load_manifest(path)
    root = Path(data["root"])
    counts = {"applied": 0, "skipped": 0, "failed": 0, "unchanged": 0, "declined": 0}
    try:
        for op in data["operations"]:
            if op["old_path"] == op["new_path"]:
                counts["unchanged"] += 1
                continue
            if op["status"] in ("applied", "declined"):
                counts["declined"] += op["status"] == "declined"
                continue
            if include_ids is not None and op["id"] not in include_ids:
                op["status"] = "declined"
                counts["declined"] += 1
                continue
            src, dest = _abs(root, op["old_path"]), _abs(root, op["new_path"])
            if not src.is_file():
                op["status"], op["error"] = "skipped", "source no longer exists"
            elif src.stat().st_size != op["size"]:
                op["status"], op["error"] = "skipped", "source changed since preview"
            elif dest.exists() and not os.path.samefile(src, dest):
                op["status"], op["error"] = "skipped", "destination already exists"
            else:
                try:
                    _move(src, dest)
                    op["status"], op["error"] = "applied", None
                except OSError as exc:
                    op["status"], op["error"] = "failed", f"{type(exc).__name__}: {exc}"
            key = {"applied": "applied", "skipped": "skipped"}.get(op["status"], "failed")
            counts[key] += 1
            if op["status"] != "applied":
                log(f"  ! {op['old_path']}: {op['error']}")
    finally:
        data["dry_run"] = False
        data["applied_at"] = datetime.now().isoformat(timespec="seconds")
        save_manifest(data, path)          # always persist, even after a crash
    return counts


def rollback_manifest(path: Path, log: Callable[[str], None] = print) -> dict[str, int]:
    data = load_manifest(path)
    root = Path(data["root"])
    counts = {"restored": 0, "missing": 0, "conflict": 0, "failed": 0}
    touched_dirs: set[Path] = set()
    try:
        for op in reversed(data["operations"]):
            if op["status"] != "applied":
                continue
            current, original = _abs(root, op["new_path"]), _abs(root, op["old_path"])
            if not current.is_file():
                op["status"], op["error"] = "rollback_missing", "file not found at its new location"
                counts["missing"] += 1
            elif original.exists() and not os.path.samefile(current, original):
                op["status"], op["error"] = "rollback_conflict", "something else now occupies the original path"
                counts["conflict"] += 1
            else:
                try:
                    _move(current, original)
                    op["status"], op["error"] = "rolled_back", None
                    counts["restored"] += 1
                    touched_dirs.add(current.parent)
                except OSError as exc:
                    op["status"], op["error"] = "rollback_failed", f"{type(exc).__name__}: {exc}"
                    counts["failed"] += 1
            if op["status"] != "rolled_back":
                log(f"  ! {op['new_path']}: {op['error']}")
    finally:
        data["rolled_back_at"] = datetime.now().isoformat(timespec="seconds")
        save_manifest(data, path)
    _remove_empty_managed_dirs(root, touched_dirs)
    return counts


def _remove_empty_managed_dirs(root: Path, dirs: set[Path]) -> None:
    for d in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
        while d != root:
            try:
                top = d.relative_to(root).parts[0]
            except ValueError:
                break
            if top not in config.MANAGED_DIRS:
                break
            try:
                d.rmdir()                  # only succeeds if empty
            except OSError:
                break
            d = d.parent
