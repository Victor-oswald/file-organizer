"""End-to-end: build a messy folder, run the whole pipeline, verify the SAFETY promises.

    1. preview changes nothing
    2. apply does exactly what the manifest says
    3. rollback restores the original tree byte-for-byte
    4. the pipeline is deterministic
    5. running again after apply changes nothing (idempotent)
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from organizer import config
from organizer.modules import manifest
from organizer.modules.pipeline import Pipeline

from . import helpers


def snapshot(root: Path) -> dict[str, str]:
    """relative path -> sha256 for every file outside the organizer's log folder."""
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and config.LOGS_DIR not in p.relative_to(root).parts:
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def build_messy_folder(root: Path) -> None:
    # --- camera photos on one day => an "event" -------------------------------------
    helpers.save_camera_jpeg(root / "IMG_0001.jpg", 1, "2024:05:03 10:15:30")
    helpers.save_camera_jpeg(root / "IMG_0002.jpg", 2, "2024:05:03 10:40:00")
    helpers.save_camera_jpeg(root / "IMG_0003.jpg", 3, "2024:05:03 12:05:00")
    # --- a video in the same event ---------------------------------------------------
    (root / "VID_20240503.mp4").write_bytes(helpers.build_mp4(datetime(2024, 5, 3, 11, 0, 5)))
    # --- screenshot ------------------------------------------------------------------
    helpers.flat_screenshot().save(root / "tmp_8f3a9c1d2e.png")
    # --- two similar unlabeled photos + one unrelated lone photo ----------------------
    helpers.photo_like(11, base_seed=50).save(root / "p1.jpg")
    helpers.photo_like(12, base_seed=50).save(root / "p2.jpg")
    helpers.photo_like(13, base_seed=77).save(root / "p3.jpg")
    # --- documents ---------------------------------------------------------------------
    (root / "a1b2c3d4e5f6a7b8.txt").write_text(helpers.SALES_TEXT)
    (root / "c0ffee00deadbeef.txt").write_text(helpers.RECIPE_TEXT)
    (root / "src").mkdir()
    (root / "src" / "f93ab2.py").write_text(helpers.CODE_TEXT)
    (root / "Meeting notes.txt").write_text(helpers.SALES_TEXT + " agenda")
    # --- exact duplicate of a clean-named file (older original wins) -----------------
    shutil.copy(root / "Meeting notes.txt", root / "Meeting notes (copy).txt")
    os.utime(root / "Meeting notes.txt", (1_000_000_000, 1_000_000_000))
    # --- audio ------------------------------------------------------------------------
    (root / "track01.mp3").write_bytes(helpers.build_mp3("The Band", "Great Song"))
    helpers.build_wav(root / "REC_0001.wav", rate=8000, channels=1, seconds=1)
    # --- archive and executable ---------------------------------------------------------
    helpers.build_zip(root / "dl_88213.zip", ["invoice_templates/january_invoice.docx",
                                              "invoice_templates/february_invoice.docx",
                                              "invoice_templates/invoice_notes.txt"])
    (root / "setup.exe").write_bytes(helpers.build_pe("Acme Corp", "Super Tool"))
    # --- junk that must be quarantined or left alone ------------------------------------
    (root / "copy1.bin").write_bytes(os.urandom(200) + b"\0")
    (root / "empty.dat").write_bytes(b"")
    (root / ".hidden_secret").write_text("do not touch")


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve() / "messy"
        self.root.mkdir()
        build_messy_folder(self.root)
        self.original = snapshot(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def preview(self, **kw):
        return Pipeline(self.root, manifest_path=self.root.parent / "m.json", **kw).run()

    def ops(self, plan):
        return {self.root.joinpath(*[]).name and plan.dm.relative(d.features.record.path): d for d in plan.decisions}

    # 1 --------------------------------------------------------------------------------
    def test_preview_changes_nothing(self):
        plan = self.preview()
        self.assertEqual(snapshot(self.root), self.original)
        self.assertTrue((self.root.parent / "m.json").is_file())
        self.assertGreater(len(plan.decisions), 10)

    # 2 --------------------------------------------------------------------------------
    def test_decisions(self):
        d = self.ops(self.preview(dupes_mode="move"))

        cam = d["IMG_0001.jpg"]
        self.assertEqual(cam.action, "rename")
        self.assertEqual(cam.dest.name, "2024-05-03_101530_128x96.jpg")
        self.assertEqual(cam.dest.parent.name, "2024-05-03_1015")          # event folder
        self.assertEqual(cam.dest.parent.parent.name, config.EVENTS_DIR)
        self.assertGreaterEqual(cam.confidence, 0.92)

        vid = d["VID_20240503.mp4"]
        self.assertEqual(vid.dest.name, "2024-05-03_110005_Video_1920x1080.mp4")
        self.assertEqual(vid.dest.parent.name, "2024-05-03_1015")           # same event window

        shot = d["tmp_8f3a9c1d2e.png"]
        self.assertEqual(shot.action, "rename")
        self.assertRegex(shot.dest.name, r"^\d{4}-\d{2}-\d{2}_Screenshot_200x120\.png$")
        self.assertTrue(0.85 <= shot.confidence <= 0.92)

        # similar unlabeled photos -> a shared group folder; lone photo -> quarantine
        p1, p2, p3 = d["p1.jpg"], d["p2.jpg"], d["p3.jpg"]
        self.assertEqual(p1.dest.parent, p2.dest.parent)
        self.assertEqual(p1.dest.parent.parent.name, config.GROUPS_DIR)
        self.assertEqual(p3.action, "quarantine")
        self.assertEqual(p3.dest, self.root / config.REVIEW_DIR / "p3.jpg")

        doc = d["a1b2c3d4e5f6a7b8.txt"]
        self.assertEqual(doc.action, "rename")
        self.assertRegex(doc.dest.name, r"^\d{4}-\d{2}-\d{2}_Document_.+\.txt$")
        self.assertGreaterEqual(doc.confidence, 0.60)
        self.assertIn("top_bigrams", doc.signals)

        code = d["src/f93ab2.py"]
        self.assertRegex(code.dest.name, r"^\d{4}-\d{2}-\d{2}_Code_.+\.py$")
        self.assertEqual(code.dest.parent, self.root / "src")               # renamed in place

        # clean human name preserved; duplicate routed to _Duplicates
        self.assertEqual(d["Meeting notes.txt"].action, "keep")
        dup = d["Meeting notes (copy).txt"]
        self.assertEqual(dup.action, "duplicate")
        self.assertEqual(dup.dest.parent.name, config.DUPLICATES_DIR)
        self.assertEqual(dup.confidence, 1.0)

        self.assertEqual(d["track01.mp3"].dest.name, "The Band - Great Song.mp3")
        self.assertRegex(d["REC_0001.wav"].dest.name, r"^\d{4}-\d{2}-\d{2}_\d{6}_VoiceMemo\.wav$")
        self.assertRegex(d["dl_88213.zip"].dest.name, r"^\d{4}-\d{2}-\d{2}_Archive_.*Invoice.*\.zip$")
        self.assertEqual(d["setup.exe"].dest.name, "Acme-Corp_Super-Tool.exe")

        self.assertEqual(d["copy1.bin"].action, "quarantine")
        self.assertEqual(d["empty.dat"].action, "keep")
        self.assertNotIn(".hidden_secret", d)

    # 2+3 ------------------------------------------------------------------------------
    def test_apply_then_rollback_restores_everything(self):
        plan = self.preview(dupes_mode="move")
        counts = manifest.apply_manifest(plan.manifest_path, log=lambda _m: None)
        self.assertEqual(counts["failed"], 0)
        self.assertEqual(counts["skipped"], 0)
        after = snapshot(self.root)
        self.assertNotEqual(after, self.original)
        self.assertEqual(sorted(after.values()), sorted(h for h in self.original.values()))   # no data lost
        self.assertTrue((self.root / ".hidden_secret").exists())

        back = manifest.rollback_manifest(plan.manifest_path, log=lambda _m: None)
        self.assertEqual(back["failed"] + back["conflict"] + back["missing"], 0)
        self.assertEqual(snapshot(self.root), self.original)
        for managed in config.MANAGED_DIRS - {config.LOGS_DIR}:
            self.assertFalse((self.root / managed).exists(), f"{managed} should be cleaned up")

    # 4 --------------------------------------------------------------------------------
    def test_deterministic(self):
        def stable(plan):
            data = manifest.load_manifest(plan.manifest_path)
            return [(o["old_path"], o["new_path"], o["action"], o["confidence"], o["reason"]) for o in data["operations"]]
        a = stable(self.preview())
        b = stable(Pipeline(self.root, manifest_path=self.root.parent / "m2.json").run())
        self.assertEqual(a, b)

    # 5 --------------------------------------------------------------------------------
    def test_idempotent_after_apply(self):
        plan = self.preview(dupes_mode="move")
        manifest.apply_manifest(plan.manifest_path, log=lambda _m: None)
        second = Pipeline(self.root, dupes_mode="move", manifest_path=self.root.parent / "second.json").run()
        changes = [(second.dm.relative(d.features.record.path), d.action, second.dm.relative(d.dest))
                   for d in second.decisions if d.action not in ("keep",)]
        self.assertEqual(changes, [])

    # safety --------------------------------------------------------------------------
    def test_apply_refuses_when_file_changed_after_preview(self):
        plan = self.preview()
        target = self.root / "a1b2c3d4e5f6a7b8.txt"
        target.write_text("edited after preview, so the size differs " * 5)
        counts = manifest.apply_manifest(plan.manifest_path, log=lambda _m: None)
        self.assertGreaterEqual(counts["skipped"], 1)
        self.assertTrue(target.exists())

    def test_apply_never_overwrites(self):
        plan = self.preview()
        data = manifest.load_manifest(plan.manifest_path)
        op = next(o for o in data["operations"] if o["old_path"] == "p3.jpg")
        blocker = self.root / config.REVIEW_DIR
        blocker.mkdir()
        (blocker / "p3.jpg").write_bytes(b"precious")
        counts = manifest.apply_manifest(plan.manifest_path, log=lambda _m: None)
        self.assertEqual((blocker / "p3.jpg").read_bytes(), b"precious")
        self.assertTrue((self.root / "p3.jpg").exists())
        self.assertGreaterEqual(counts["skipped"], 1)

    def test_manifest_is_valid_json_with_audit_fields(self):
        plan = self.preview()
        data = json.loads(plan.manifest_path.read_text("utf-8"))
        op = data["operations"][0]
        for key in ("old_path", "new_path", "confidence", "reason", "action", "signals", "status"):
            self.assertIn(key, op)
        self.assertTrue(all("\\" not in o["old_path"] for o in data["operations"]))     # portable paths


if __name__ == "__main__":
    unittest.main()
