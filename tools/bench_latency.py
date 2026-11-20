"""How long until a newly finished download shows up as a proposal? (real clock, real watcher thread)

    python tools/bench_latency.py

Two configurations are compared on the CURRENT code:
  previous defaults : check every 30 s, settle 5 s, no fast re-check   (how v1 behaved)
  current defaults  : check every 5 s,  settle 3 s, 1.5 s re-check while a file is settling
Each trial writes a file at a random moment in the check cycle, like a real download finishing.
"""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).parent))
from organizer.service import service as svc_mod  # noqa: E402
from organizer.service.service import Service  # noqa: E402
from organizer.service.store import Store  # noqa: E402
from tests import helpers  # noqa: E402


def trials(label, interval, settle, fast, n, seed):
    rnd = random.Random(seed)
    out = []
    with tempfile.TemporaryDirectory() as tmp, mock.patch.object(svc_mod, "check_folder_allowed", lambda p: None), \
            mock.patch.object(svc_mod, "SETTLE_SECONDS", settle), mock.patch.object(svc_mod, "FAST_RECHECK", fast):
        os.environ["FILE_ORGANIZER_HOME"] = str(Path(tmp) / "app")
        root = Path(tmp) / "Downloads"; root.mkdir()
        svc = Service(Store(Path(tmp) / "app"))
        svc.store.config["interval"] = interval
        svc.add_folder(str(root), mode="ask", organize_existing=False)
        svc.start()
        try:
            time.sleep(interval + 1)
            for i in range(n):
                time.sleep(rnd.uniform(0, interval))                 # a random phase of the check cycle
                before = len(svc.list_batches())
                p = root / f"{rnd.getrandbits(48):012x}.txt"
                p.write_text(helpers.SALES_TEXT + f" trial{i}x" * 5)
                t0 = time.time()                                     # the download has just finished
                while len(svc.list_batches()) == before and time.time() - t0 < 120:
                    time.sleep(0.05)
                latency = time.time() - t0
                out.append(round(latency, 2))
                print(f"  {label}: trial {i + 1}/{n}: {latency:5.1f} s", flush=True)
                for b in svc.list_batches():
                    if b["state"] == "pending":
                        svc.reject_batch(b["id"])
        finally:
            svc.stop()
    return out


def record(key, config, samples):
    out = ROOT / "docs" / "benchmarks" / "results.json"
    data = json.loads(out.read_text()) if out.exists() else {}
    data.setdefault("latency", {})[key] = {"config": config, "samples": samples}
    out.write_text(json.dumps(data, indent=1))
    s = sorted(samples)
    print(f"{key}: n={len(s)}  min {s[0]:.1f}  median {s[len(s) // 2]:.1f}  max {s[-1]:.1f} s")


if __name__ == "__main__":
    which = sys.argv[1:] or ["previous", "current"]        # each configuration is saved as soon as it finishes
    if "previous" in which:
        record("previous", "30 s interval, 5 s settle, no fast re-check", trials("previous", 30, 5, 30, 5, 1))
    if "current" in which:
        record("current", "5 s interval, 3 s settle, 1.5 s fast re-check", trials("current", 5, 3, 1.5, 8, 2))
