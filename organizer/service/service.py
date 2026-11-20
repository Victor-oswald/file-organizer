"""The background brain: watches folders, creates batches of proposed changes,
applies / declines / undoes them, and reports status to the UI.

Vocabulary
    folder  : a directory the user asked us to watch (with its own settings)
    scan    : one look at a folder; finds files that are new and have settled
    batch   : the changes proposed for one scan (stored as a normal manifest file)
    seen    : per-folder memory of files already handled, so nothing is proposed twice
"""

from __future__ import annotations

import collections
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .. import __version__, config
from ..modules import manifest
from ..modules.crawler import crawl, snapshot
from ..modules.pipeline import Pipeline
from .paths import app_data_dir
from .store import Store

# Files a browser/app is still writing: never touch these.
TEMP_EXTENSIONS = frozenset({".crdownload", ".part", ".tmp", ".temp", ".download", ".partial", ".opdownload"})
SETTLE_SECONDS = 3                    # a file must be unchanged this long before we touch it
FAST_RECHECK = 1.5                    # while files are settling, look again this soon

CARE_LEVELS = {"cautious": 0.80, "balanced": 0.60, "eager": 0.45}
FOLDER_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "mode": "ask",                    # ask | auto
    "organize_existing": False,
    "dupes": "flag",                  # flag | move
    "care": "balanced",
    "recursive": True,
}

_FORBIDDEN_UNIX = ("/bin", "/boot", "/dev", "/etc", "/lib", "/lib64", "/proc", "/sbin", "/sys", "/usr", "/var",
                   "/System", "/Library", "/Applications", "/private", "/opt")


def check_folder_allowed(path: Path) -> None:
    """Refuse folders that would be dangerous to reorganize."""
    p = path.resolve()
    if p == Path(p.anchor):
        raise ValueError("That is the top of a drive. Please pick a specific folder, such as Downloads.")
    if p == Path.home():
        raise ValueError("Your whole home folder is too broad. Pick a folder inside it, such as Downloads.")
    protected = [Path(x) for x in _FORBIDDEN_UNIX]
    for var in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        if os.environ.get(var):
            protected.append(Path(os.environ[var]))
    protected.append(app_data_dir())
    for bad in protected:
        try:
            if p == bad.resolve() or bad.resolve() in p.parents:
                raise ValueError(f"'{p}' is a system or program folder, so it is not safe to organize.")
        except OSError:
            continue


def plural_changes(n: int) -> str:
    return f"{n} change" + ("" if n == 1 else "s")


def plural_files(n: int) -> str:
    return f"{n} file" + ("" if n == 1 else "s")


class Service:
    def __init__(self, store: Store | None = None, clock: Callable[[], float] = time.time) -> None:
        self.store = store or Store()
        self.clock = clock
        self.work_lock = threading.RLock()          # one pipeline/apply/undo at a time
        self._requests: dict[str, bool] = {}        # folder_id -> "everything?"
        self._observed: dict[str, dict[str, tuple]] = {}
        self._rt: dict[str, dict[str, Any]] = {}    # per-folder runtime status
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._next_due: dict[str, float] = {}
        self._announced: dict[str, set[str]] = {}
        self._events: collections.deque = collections.deque(maxlen=1500)
        self._event_id = 0
        self._live: dict[str, Any] = {"phase": "idle"}
        self._live_ts = 0.0

    # ------------------------------------------------------------------ events + live state
    def emit(self, kind: str, msg: str, *, folder: dict[str, Any] | None = None, file: str | None = None,
             to: str | None = None, conf: float | None = None) -> None:
        """Append one line to the activity log the dashboard shows."""
        with self.store.lock:
            self._event_id += 1
            self._events.append({
                "id": self._event_id, "ts": self.clock(), "kind": kind, "msg": msg, "file": file, "to": to,
                "conf": conf, "folder": Path(folder["path"]).name if folder else None,
                "folder_id": folder["id"] if folder else None})

    def events_since(self, after: int = 0, limit: int = 400) -> dict[str, Any]:
        with self.store.lock:
            items = [e for e in self._events if e["id"] > after]
            return {"events": items[-limit:], "last_id": self._event_id}

    def _live_set(self, phase: str, **info: Any) -> None:
        self._live = {"phase": phase, "since": self.clock(), **info}

    def _progress_cb(self, folder: dict[str, Any]):
        name = Path(folder["path"]).name

        def cb(stage: str, done: int, total: int, current: str) -> None:
            self._live = {"phase": "working", "folder": name, "label": stage, "done": done, "total": total,
                          "current": current, "since": self._live.get("since", self.clock())}
        return cb

    # ------------------------------------------------------------------ folders
    @property
    def folders(self) -> list[dict[str, Any]]:
        return self.store.config["folders"]

    def get_folder(self, folder_id: str) -> dict[str, Any]:
        for f in self.folders:
            if f["id"] == folder_id:
                return f
        raise KeyError(folder_id)

    @staticmethod
    def _clean_options(opts: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if "enabled" in opts:
            out["enabled"] = bool(opts["enabled"])
        if "mode" in opts:
            if opts["mode"] not in ("ask", "auto"):
                raise ValueError("mode must be 'ask' or 'auto'")
            out["mode"] = opts["mode"]
        if "dupes" in opts:
            if opts["dupes"] not in ("flag", "move"):
                raise ValueError("dupes must be 'flag' or 'move'")
            out["dupes"] = opts["dupes"]
        if "care" in opts:
            if opts["care"] not in CARE_LEVELS:
                raise ValueError("care must be cautious, balanced or eager")
            out["care"] = opts["care"]
        for key in ("organize_existing", "recursive"):
            if key in opts:
                out[key] = bool(opts[key])
        return out

    def add_folder(self, path: str, **opts: Any) -> dict[str, Any]:
        p = Path(path).expanduser()
        if not p.is_dir():
            raise ValueError("That folder does not exist.")
        p = p.resolve()
        check_folder_allowed(p)
        with self.store.lock:
            for f in self.folders:
                other = Path(f["path"])
                if other == p:
                    raise ValueError("That folder is already being watched.")
                if other in p.parents or p in other.parents:
                    raise ValueError(f"That overlaps with a folder you already watch ({other}).")
            folder = {"id": uuid.uuid4().hex[:8], "path": str(p), **FOLDER_DEFAULTS, **self._clean_options(opts),
                      "added": self.clock()}
            self.folders.append(folder)
            self.store.save_config()
        if not folder["organize_existing"]:
            n = self._baseline(folder)              # "only NEW files from now on"
            self.emit("info", f"Now watching. {n} existing files are ignored; only new files will be organized. "
                              f"Use “Organize existing files” to include them.", folder=folder)
        else:
            self.emit("info", "Now watching. Existing files will be checked first.", folder=folder)
        self.request_scan(folder["id"], everything=folder["organize_existing"])
        return folder

    def update_folder(self, folder_id: str, **opts: Any) -> dict[str, Any]:
        with self.store.lock:
            folder = self.get_folder(folder_id)
            folder.update(self._clean_options({k: v for k, v in opts.items() if k != "organize_existing"}))
            self.store.save_config()
        return folder

    def remove_folder(self, folder_id: str) -> None:
        with self.store.lock:
            folder = self.get_folder(folder_id)
            self.folders.remove(folder)
            self.store.save_config()
            for b in self.store.batches:
                if b["folder_id"] == folder_id and b["state"] == "pending":
                    b["state"] = "rejected"
            self.store.save_batches()
        self.store.forget_seen(folder_id)
        self._observed.pop(folder_id, None)
        self._rt.pop(folder_id, None)

    def _baseline(self, folder: dict[str, Any]) -> int:
        """Remember everything that is already there so only future files are processed."""
        root = Path(folder["path"])
        prefix = str(root) + os.sep
        seen = self.store.seen(folder["id"])
        snap = snapshot(root, recursive=folder["recursive"], skip_projects=True)
        for full, (size, mtime) in snap.items():
            seen[full[len(prefix):].replace(os.sep, "/")] = [size, mtime]
        self.store.save_seen(folder["id"])
        return len(snap)

    # ------------------------------------------------------------------ control
    def set_paused(self, paused: bool) -> None:
        self.store.config["paused"] = bool(paused)
        self.store.save_config()

    def set_interval(self, seconds: int) -> None:
        self.store.config["interval"] = max(1, min(3600, int(seconds)))
        self.store.save_config()

    def request_scan(self, folder_id: str | None = None, everything: bool = False) -> None:
        ids = [folder_id] if folder_id else [f["id"] for f in self.folders]
        for fid in ids:
            self._requests[fid] = self._requests.get(fid, False) or everything
        self._wake.set()

    # ------------------------------------------------------------------ the scan
    def scan_folder(self, folder: dict[str, Any], *, everything: bool = False) -> dict[str, Any] | None:
        """One look at one folder. Returns the new batch, or None if nothing to do.

        This runs every few seconds, so the common case (nothing new) must be nearly free:
        it takes a lightweight snapshot {path: (size, mtime)} and compares it with memory."""
        fid = folder["id"]
        rt = self._rt.setdefault(fid, {})
        root = Path(folder["path"])
        if not root.is_dir():
            rt["problem"] = "missing"
            return None
        rt["problem"], rt["error"] = None, None
        now = self.clock()
        prefix = str(root) + os.sep

        snap = snapshot(root, recursive=folder["recursive"], skip_projects=True)
        seen = self.store.seen(fid)
        previous = self._observed.get(fid, {})
        announced = self._announced.setdefault(fid, set())
        observed: dict[str, tuple] = {}
        candidates: list[str] = []
        settling = 0
        # An explicit "organize existing files" request must look at EVERYTHING, including files that
        # were recorded as already handled when the folder was added. (Honouring `seen` here made the
        # button silently do nothing.) Files already waiting for approval are not proposed twice.
        awaiting = self._awaiting_paths(fid) if everything else set()
        for full, (size, mtime) in snap.items():
            rel = full[len(prefix):].replace(os.sep, "/")
            if everything:
                if rel in awaiting:
                    continue
            elif seen.get(rel) == [size, mtime]:
                continue                                   # already handled
            observed[rel] = (size, mtime)
            ext = os.path.splitext(full)[1].lower()
            if ext in TEMP_EXTENSIONS:
                if rel not in announced:
                    announced.add(rel)
                    self.emit("ignored", f"Still downloading ({ext}); I’ll look again when it finishes.", folder=folder, file=rel)
                continue
            if everything or (previous.get(rel) == (size, mtime) and now - mtime >= SETTLE_SECONDS):
                candidates.append(full)
            else:
                settling += 1
                if rel not in announced:
                    announced.add(rel)
                    self.emit("detected", "New file. Waiting until it stops changing before touching it.", folder=folder, file=rel)
        self._observed[fid] = observed
        rt.update(last_scan=now, tracked=len(snap), settling=settling)
        if len(announced) > 5000:
            announced.clear()
        if not candidates:
            if everything:
                self.emit("info", f"Checked {len(snap)} existing files: nothing to do"
                                  + (f" ({len(awaiting)} already waiting for your approval)." if awaiting else "."), folder=folder)
            return None
        if everything:
            self.emit("info", f"Checking {plural_files(len(candidates))} that were already in this folder…", folder=folder)

        with self.work_lock:
            name = root.name or str(root)
            self._live_set("working", folder=name, label="Listing files", done=0, total=len(candidates), current="")
            try:
                return self._process(folder, root, candidates, now, announced, everything)
            finally:
                self._live = {"phase": "idle"}

    def _awaiting_paths(self, folder_id: str) -> set[str]:
        """Relative paths of files that already have a proposed change waiting for the user."""
        out: set[str] = set()
        for b in self.store.batches:
            if b["folder_id"] == folder_id and b["state"] == "pending":
                try:
                    ops = manifest.load_manifest(Path(b["manifest"]))["operations"]
                except (OSError, ValueError):
                    continue
                out.update(o["old_path"] for o in ops if o["old_path"] != o["new_path"])
        return out

    EVENT_CAP = 40          # individual log lines per batch; the rest is summarized

    def _process(self, folder: dict[str, Any], root: Path, candidates: list[str], now: float,
                 announced: set[str], everything: bool = False) -> dict[str, Any] | None:
        batch_id = uuid.uuid4().hex[:10]
        logs = root / config.LOGS_DIR
        manifest_path = logs / f"manifest_{datetime.fromtimestamp(now):%Y%m%d_%H%M%S}_{batch_id}.json"
        plan = Pipeline(root, dupes_mode=folder["dupes"], threshold=CARE_LEVELS[folder["care"]],
                        manifest_path=manifest_path, only={Path(c) for c in candidates},
                        recursive=folder["recursive"], skip_projects=True,
                        progress=self._progress_cb(folder)).run()
        dm = plan.dm
        for d in plan.decisions:
            announced.discard(dm.relative(d.features.record.path))
        actionable = [d for d in plan.decisions if d.dest != d.features.record.path]
        noted = [d for d in plan.decisions if d.action == "duplicate" and d.dest == d.features.record.path]

        # Explain EVERY file, including the ones left alone: this answers "why was nothing renamed?"
        kept = [d for d in plan.decisions if d.action == "keep"]
        for d in kept[: self.EVENT_CAP]:
            self.emit("kept", d.reason, folder=folder, file=dm.relative(d.features.record.path), conf=d.confidence)
        if len(kept) > self.EVENT_CAP:
            self.emit("kept", f"…and {len(kept) - self.EVENT_CAP} more files left alone "
                              f"(already well named, or not sure enough to rename them).", folder=folder)
        for d in noted[: self.EVENT_CAP]:
            self.emit("duplicate", d.reason, folder=folder, file=dm.relative(d.features.record.path), conf=d.confidence)
        if everything:
            verb = "made" if folder["mode"] == "auto" else "proposed"
            self.emit("info", f"Checked {len(candidates)} existing files: {plural_changes(len(actionable))} {verb}, "
                              f"{len(kept)} left alone" + (f", {len(noted)} duplicate(s) found." if noted else "."), folder=folder)

        if not actionable and not noted:
            manifest_path.unlink(missing_ok=True)
            self._mark_seen(folder, [d.features.record.path for d in plan.decisions])
            return None

        counts = {a: sum(1 for d in actionable if d.action == a) for a in ("rename", "move", "quarantine", "duplicate")}
        counts["flagged_duplicates"] = len(noted)
        batch = {"id": batch_id, "folder_id": folder["id"], "folder_path": str(root), "manifest": str(manifest_path),
                 "created": now, "mode": folder["mode"], "state": "pending", "files": len(actionable),
                 "counts": counts, "applied": 0}
        if not actionable:
            batch["state"] = "info"
        elif folder["mode"] == "auto":
            self._live_set("working", folder=root.name, label="Applying changes", done=0, total=len(actionable), current="")
            result = manifest.apply_manifest(manifest_path, log=lambda _m: None)
            batch["applied"] = result["applied"]
            batch["state"] = "applied" if result["applied"] else "failed"
            self._emit_results(folder, manifest.load_manifest(manifest_path)["operations"], "")
        else:
            for d in actionable[: self.EVENT_CAP]:
                self.emit("proposed", f"Waiting for your OK: {d.reason}", folder=folder,
                          file=dm.relative(d.features.record.path), to=dm.relative(d.dest), conf=d.confidence)
            if len(actionable) > self.EVENT_CAP:
                self.emit("proposed", f"…and {len(actionable) - self.EVENT_CAP} more changes are waiting for your OK "
                                      f"(see “Awaiting approval”).", folder=folder)
        self.store.batches.append(batch)
        self.store.save_batches()
        self._mark_ops_seen(folder, manifest.load_manifest(manifest_path)["operations"])
        return batch

    _DONE_KIND = {"rename": "renamed", "move": "moved", "quarantine": "review", "duplicate": "moved"}

    def _emit_results(self, folder: dict[str, Any], ops: list[dict], prefix: str) -> None:
        shown = 0
        for op in ops:
            if op["old_path"] == op["new_path"]:
                continue
            shown += 1
            if shown > self.EVENT_CAP:
                continue
            if op["status"] == "applied":
                self.emit(self._DONE_KIND.get(op["action"], "renamed"), prefix + op["reason"], folder=folder,
                          file=op["old_path"], to=op["new_path"], conf=op["confidence"])
            elif op["status"] in ("failed", "skipped"):
                self.emit("error", f"Could not change this file: {op['error']}", folder=folder, file=op["old_path"])
        if shown > self.EVENT_CAP:
            self.emit("info", f"…and {shown - self.EVENT_CAP} more files were handled (see History, Details).", folder=folder)

    def _mark_seen(self, folder: dict[str, Any], paths: list[Path]) -> None:
        root = Path(folder["path"])
        seen = self.store.seen(folder["id"])
        for p in paths:
            try:
                st = p.stat()
                seen[p.relative_to(root).as_posix()] = [st.st_size, st.st_mtime]
            except (OSError, ValueError):
                continue
        self.store.save_seen(folder["id"])

    def _mark_ops_seen(self, folder: dict[str, Any], ops: list[dict]) -> None:
        root = Path(folder["path"])
        paths = []
        for op in ops:
            new, old = root.joinpath(*op["new_path"].split("/")), root.joinpath(*op["old_path"].split("/"))
            paths.append(new if new.exists() else old)
        self._mark_seen(folder, paths)

    # ------------------------------------------------------------------ batches
    def list_batches(self) -> list[dict[str, Any]]:
        return list(reversed(self.store.batches))

    def _batch(self, batch_id: str) -> dict[str, Any]:
        for b in self.store.batches:
            if b["id"] == batch_id:
                return b
        raise KeyError(batch_id)

    def batch_detail(self, batch_id: str) -> dict[str, Any]:
        batch = self._batch(batch_id)
        try:
            data = manifest.load_manifest(Path(batch["manifest"]))
        except (OSError, ValueError):
            return {**batch, "items": [], "missing": True}
        items = [{"id": o["id"], "old": o["old_path"], "new": o["new_path"], "action": o["action"],
                  "confidence": o["confidence"], "reason": o["reason"], "category": o["category"],
                  "status": o["status"], "error": o["error"]}
                 for o in data["operations"] if o["old_path"] != o["new_path"] or o["action"] == "duplicate"]
        return {**batch, "items": items[:1000], "truncated": len(items) > 1000}

    def _folder_for(self, batch: dict[str, Any]) -> dict[str, Any]:
        try:
            return self.get_folder(batch["folder_id"])
        except KeyError:
            return {"id": batch["folder_id"], "path": batch["folder_path"]}

    def apply_batch(self, batch_id: str, ids: list[int] | None = None) -> dict[str, int]:
        with self.work_lock:
            batch = self._batch(batch_id)
            if batch["state"] != "pending":
                raise ValueError("These changes were already handled.")
            path = Path(batch["manifest"])
            if ids is None:
                ids = [i["id"] for i in self.batch_detail(batch_id)["items"] if i["action"] != "duplicate" or i["new"] != i["old"]]
            result = manifest.apply_manifest(path, log=lambda _m: None, include_ids=set(ids))
            batch["applied"] = result["applied"]
            batch["state"] = "applied" if result["applied"] else ("failed" if result["failed"] or result["skipped"] else "rejected")
            self.store.save_batches()
            folder = self._folder_for(batch)
            ops = manifest.load_manifest(path)["operations"]
            self._emit_results(folder, ops, "You approved: ")
            if result["declined"]:
                self.emit("skipped", f"You left {plural_files(result['declined'])} as they were.", folder=folder)
            self._mark_ops_seen(folder, ops)
            return result

    def reject_batch(self, batch_id: str) -> None:
        with self.work_lock:
            batch = self._batch(batch_id)
            if batch["state"] != "pending":
                raise ValueError("These changes were already handled.")
            manifest.decline_operations(Path(batch["manifest"]))
            batch["state"] = "rejected"
            self.store.save_batches()
            self.emit("skipped", f"You declined {plural_files(batch['files'])}; they were left as they were.",
                      folder=self._folder_for(batch))

    def undo_batch(self, batch_id: str) -> dict[str, int]:
        with self.work_lock:
            batch = self._batch(batch_id)
            if batch["state"] != "applied":
                raise ValueError("Only applied changes can be undone.")
            path = Path(batch["manifest"])
            result = manifest.rollback_manifest(path, log=lambda _m: None)
            batch["state"] = "undone"
            self.store.save_batches()
            restored = [o for o in manifest.load_manifest(path)["operations"] if o["status"] == "rolled_back"]
            folder = self._folder_for(batch)
            root = Path(folder["path"])
            self._mark_seen(folder, [root.joinpath(*o["old_path"].split("/")) for o in restored])  # respect the undo
            for o in restored:
                self.emit("undone", "Put back the way it was.", folder=folder, file=o["new_path"], to=o["old_path"])
            return result

    # ------------------------------------------------------------------ status
    def status(self) -> dict[str, Any]:
        cfg = self.store.config
        now = self.clock()
        midnight = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        applied = [b for b in self.store.batches if b["state"] == "applied"]
        folders = []
        for f in self.folders:
            rt = self._rt.get(f["id"], {})
            if not Path(f["path"]).is_dir():
                state = "missing"
            elif not f["enabled"]:
                state = "off"
            elif cfg["paused"]:
                state = "paused"
            else:
                state = "watching"
            folders.append({**f, "state": state, "last_scan": rt.get("last_scan"), "error": rt.get("error"),
                            "tracked": rt.get("tracked"), "settling": rt.get("settling", 0),
                            "next_check": self._next_due.get(f["id"]), "problem": rt.get("problem"),
                            "pending": sum(1 for b in self.store.batches
                                           if b["folder_id"] == f["id"] and b["state"] == "pending")})
        return {
            "version": __version__, "paused": cfg["paused"], "interval": cfg["interval"], "now": now,
            "folders": folders, "live": self._live,
            "pending": sum(1 for b in self.store.batches if b["state"] == "pending"),
            "organized_today": sum(b["applied"] for b in applied if b["created"] >= midnight),
            "organized_total": sum(b["applied"] for b in applied),
        }

    # ------------------------------------------------------------------ worker thread
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="watcher", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(timeout=0.5)
            self._wake.clear()
            requests, self._requests = self._requests, {}
            for folder in list(self.folders):
                fid = folder["id"]
                asked = fid in requests
                active = folder["enabled"] and not self.store.config["paused"]
                if not asked and not (active and self.clock() >= self._next_due.get(fid, 0)):
                    continue
                try:
                    self.scan_folder(folder, everything=requests.get(fid, False))
                except Exception as exc:                       # the watcher must never die
                    self._rt.setdefault(fid, {})["error"] = f"{type(exc).__name__}: {exc}"
                    self.emit("error", f"Problem while checking this folder: {exc}", folder=folder)
                wait = self.store.config["interval"]
                if self._rt.get(fid, {}).get("settling"):
                    wait = min(wait, FAST_RECHECK)             # files are still arriving: look again soon
                self._next_due[fid] = self.clock() + wait
