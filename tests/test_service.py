"""Tests for the background service: settling, baselines, ask/auto modes, undo, safety."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from organizer.modules import crawler
from organizer.service import service as svc_mod
from organizer.service.service import Service, check_folder_allowed
from organizer.service.store import Store

from . import helpers


class ServiceCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name).resolve()
        os.environ["FILE_ORGANIZER_HOME"] = str(tmp / "appdata")
        self.root = tmp / "Downloads"
        self.root.mkdir()
        self.offset = 0.0
        self.svc = Service(Store(tmp / "appdata"), clock=lambda: time.time() + self.offset)
        self._p = mock.patch.object(svc_mod, "check_folder_allowed", lambda p: None)   # tmp dirs may live under /var
        self._p.start()

    def tearDown(self):
        self._p.stop()
        os.environ.pop("FILE_ORGANIZER_HOME", None)
        self._tmp.cleanup()

    def add(self, **opts):
        folder = self.svc.add_folder(str(self.root), **opts)
        self.svc._requests.clear()
        return folder

    def settle(self):
        """Two scans, 10 'seconds' apart: how a file becomes stable."""
        folder = self.svc.folders[0]
        self.svc.scan_folder(folder)
        self.offset += 10
        return self.svc.scan_folder(folder)

    def messy(self, name="a1b2c3d4e5f6a7b8.txt", text=helpers.SALES_TEXT):
        (self.root / name).write_text(text)


class TestWatching(ServiceCase):
    def test_existing_files_are_left_alone_by_default(self):
        self.messy()
        self.add()                                   # organize_existing = False -> baseline
        self.assertIsNone(self.settle())
        self.assertTrue((self.root / "a1b2c3d4e5f6a7b8.txt").exists())

    def test_new_file_needs_two_quiet_scans(self):
        self.add()
        self.messy()
        folder = self.svc.folders[0]
        self.assertIsNone(self.svc.scan_folder(folder))            # first sighting: not stable yet
        self.offset += 10
        batch = self.svc.scan_folder(folder)                       # unchanged + old enough: proposed
        self.assertEqual(batch["state"], "pending")
        self.assertTrue((self.root / "a1b2c3d4e5f6a7b8.txt").exists(), "ask mode must not touch the file")

    def test_file_still_growing_is_not_touched(self):
        self.add()
        self.messy()
        folder = self.svc.folders[0]
        self.svc.scan_folder(folder)
        self.offset += 10
        with open(self.root / "a1b2c3d4e5f6a7b8.txt", "a") as fh:      # a download still writing
            fh.write(" more data")
        self.assertIsNone(self.svc.scan_folder(folder))            # signature changed since last look

    def test_temp_download_extensions_are_ignored(self):
        self.add()
        (self.root / "movie.mp4.crdownload").write_bytes(b"partial" * 100)
        self.assertIsNone(self.settle())

    def test_organize_existing_option(self):
        self.messy()
        self.add(organize_existing=True)
        batch = self.svc.scan_folder(self.svc.folders[0], everything=True)
        self.assertEqual(batch["state"], "pending")
        self.assertEqual(batch["files"], 1)

    def test_nothing_proposed_twice(self):
        self.add()
        self.messy()
        self.assertEqual(self.settle()["state"], "pending")
        self.offset += 10
        self.assertIsNone(self.svc.scan_folder(self.svc.folders[0]))

    def test_code_projects_are_skipped(self):
        proj = self.root / "myapp"
        proj.mkdir()
        (proj / "package.json").write_text("{}")
        (proj / "a1b2c3d4.py").write_text(helpers.CODE_TEXT)
        self.add(organize_existing=True)
        self.assertIsNone(self.svc.scan_folder(self.svc.folders[0], everything=True))
        self.assertTrue(crawler.is_project_dir(str(proj)))

    def test_non_recursive_ignores_subfolders(self):
        (self.root / "sub").mkdir()
        (self.root / "sub" / "a1b2c3d4e5f6a7b8.txt").write_text(helpers.SALES_TEXT)
        self.add(organize_existing=True, recursive=False)
        self.assertIsNone(self.svc.scan_folder(self.svc.folders[0], everything=True))

    def test_new_duplicate_of_existing_file_is_detected(self):
        (self.root / "Meeting notes.txt").write_text(helpers.SALES_TEXT)
        os.utime(self.root / "Meeting notes.txt", (1_000_000_000, 1_000_000_000))
        self.add()
        (self.root / "Meeting notes (1).txt").write_text(helpers.SALES_TEXT)
        batch = self.settle()
        self.assertEqual(batch["counts"]["flagged_duplicates"], 1)
        self.assertEqual(batch["state"], "info")


class TestModes(ServiceCase):
    def test_auto_mode_applies_and_undo_is_respected(self):
        self.add(mode="auto")
        self.messy()
        batch = self.settle()
        self.assertEqual(batch["state"], "applied")
        self.assertEqual(batch["applied"], 1)
        self.assertFalse((self.root / "a1b2c3d4e5f6a7b8.txt").exists())
        self.svc.undo_batch(batch["id"])
        self.assertTrue((self.root / "a1b2c3d4e5f6a7b8.txt").exists())
        self.offset += 10
        self.assertIsNone(self.svc.scan_folder(self.svc.folders[0]), "an undone file must not be proposed again")
        self.assertEqual(self.svc.status()["organized_total"], 0 if self.svc.list_batches()[0]["state"] == "undone" else 1)

    def test_ask_mode_apply_selected_only(self):
        self.add()
        self.messy("a1b2c3d4e5f6a7b8.txt", helpers.SALES_TEXT)
        self.messy("c0ffee00deadbeef.txt", helpers.RECIPE_TEXT)
        batch = self.settle()
        detail = self.svc.batch_detail(batch["id"])
        keep_ids = [i["id"] for i in detail["items"] if i["old"].startswith("a1b2")]
        result = self.svc.apply_batch(batch["id"], keep_ids)
        self.assertEqual(result["applied"], 1)
        self.assertEqual(result["declined"], 1)
        self.assertTrue((self.root / "c0ffee00deadbeef.txt").exists(), "unselected file is untouched")
        self.assertFalse((self.root / "a1b2c3d4e5f6a7b8.txt").exists())
        self.offset += 10
        self.assertIsNone(self.svc.scan_folder(self.svc.folders[0]))
        with self.assertRaises(ValueError):
            self.svc.apply_batch(batch["id"], keep_ids)            # cannot apply twice

    def test_reject_leaves_files_and_does_not_ask_again(self):
        self.add()
        self.messy()
        batch = self.settle()
        self.svc.reject_batch(batch["id"])
        self.assertTrue((self.root / "a1b2c3d4e5f6a7b8.txt").exists())
        self.offset += 10
        self.assertIsNone(self.svc.scan_folder(self.svc.folders[0]))
        self.assertEqual(self.svc.status()["pending"], 0)

    def test_stale_apply_is_skipped_if_file_changed(self):
        self.add()
        self.messy()
        batch = self.settle()
        (self.root / "a1b2c3d4e5f6a7b8.txt").write_text("changed after the preview " * 40)
        result = self.svc.apply_batch(batch["id"])
        self.assertEqual(result["applied"], 0)
        self.assertEqual(self.svc.list_batches()[0]["state"], "failed")


class TestOrganizeExisting(ServiceCase):
    """Regression tests: 'Organize existing files' silently did nothing for baselined folders."""

    def existing(self):
        (self.root / "a1b2c3d4e5f6a7b8.txt").write_text(helpers.SALES_TEXT)
        (self.root / "c0ffee00deadbeef.txt").write_text(helpers.RECIPE_TEXT)
        (self.root / "Meeting notes.txt").write_text("agenda " * 40)

    def test_button_works_on_a_folder_added_with_default_settings(self):
        self.existing()
        self.add()                                              # default: existing files are baselined
        self.assertIsNone(self.settle())                        # ...so nothing happens automatically
        batch = self.svc.scan_folder(self.svc.folders[0], everything=True)     # the button
        self.assertIsNotNone(batch, "Organize existing files did nothing")
        self.assertEqual(batch["state"], "pending")
        self.assertEqual(batch["files"], 2)                     # the two machine-named files

    def test_existing_files_are_organized_when_chosen_while_adding(self):
        self.existing()
        self.add(organize_existing=True, mode="auto")
        batch = self.svc.scan_folder(self.svc.folders[0], everything=True)
        self.assertEqual(batch["state"], "applied")
        self.assertFalse((self.root / "a1b2c3d4e5f6a7b8.txt").exists())
        self.assertTrue((self.root / "Meeting notes.txt").exists(), "clean names are preserved")

    def test_through_the_worker_queue(self):
        """The real path: request_scan(everything=True) then the watcher loop consumes it."""
        self.existing()
        self.add()
        with mock.patch.object(svc_mod, "SETTLE_SECONDS", 0):
            self.svc.clock = time.time
            self.svc.start()
            try:
                self.svc.request_scan(self.svc.folders[0]["id"], everything=True)
                deadline = time.time() + 10
                while time.time() < deadline and not self.svc.list_batches():
                    time.sleep(0.2)
                self.assertTrue(self.svc.list_batches(), "worker never produced a batch")
            finally:
                self.svc.stop()

    def test_running_it_twice_does_not_create_a_second_proposal(self):
        self.existing()
        self.add()
        folder = self.svc.folders[0]
        self.assertIsNotNone(self.svc.scan_folder(folder, everything=True))
        self.assertIsNone(self.svc.scan_folder(folder, everything=True), "files already awaiting approval were proposed again")
        self.assertEqual(len([b for b in self.svc.list_batches() if b["state"] == "pending"]), 1)

    def test_result_is_always_reported_even_when_nothing_changes(self):
        (self.root / "Meeting notes.txt").write_text("agenda " * 40)
        self.add()
        self.assertIsNone(self.svc.scan_folder(self.svc.folders[0], everything=True))
        infos = [e["msg"] for e in self.svc.events_since(0)["events"] if e["kind"] == "info"]
        self.assertTrue(any(m.startswith("Checked 1 existing file") for m in infos), infos)

    def test_big_batches_do_not_flood_the_log(self):
        for i in range(80):
            (self.root / f"{i:012x}.txt").write_text(helpers.SALES_TEXT + f" extra{i}x" * 3)
        self.add()
        self.svc.scan_folder(self.svc.folders[0], everything=True)
        events = self.svc.events_since(0)["events"]
        self.assertLess(len(events), 70)
        self.assertTrue(any("more" in e["msg"] for e in events), "a summary line is expected")


class TestVisibility(ServiceCase):
    """The activity log must explain what the software is doing, including why it did NOTHING."""

    def kinds(self):
        return [(e["kind"], e["file"]) for e in self.svc.events_since(0)["events"]]

    def test_every_stage_is_reported(self):
        self.messy()
        self.add()                                                   # baseline
        self.assertTrue(any(k == "info" for k, _ in self.kinds()), "baseline explained on add")
        self.messy("c0ffee00deadbeef.txt", helpers.RECIPE_TEXT)
        folder = self.svc.folders[0]
        self.svc.scan_folder(folder)
        self.assertIn(("detected", "c0ffee00deadbeef.txt"), self.kinds())
        self.offset += 10
        self.svc.scan_folder(folder)
        self.assertIn(("proposed", "c0ffee00deadbeef.txt"), self.kinds())

    def test_kept_files_say_why(self):
        self.add()
        (self.root / "Meeting notes.txt").write_text(helpers.SALES_TEXT)
        self.settle()
        kept = [e for e in self.svc.events_since(0)["events"] if e["kind"] == "kept"]
        self.assertEqual(len(kept), 1)
        self.assertIn("clean", kept[0]["msg"])

    def test_temp_downloads_are_explained_once(self):
        self.add()
        (self.root / "movie.mp4.crdownload").write_bytes(b"x" * 100)
        folder = self.svc.folders[0]
        for _ in range(3):
            self.svc.scan_folder(folder)
        self.assertEqual(sum(1 for k, _ in self.kinds() if k == "ignored"), 1)

    def test_auto_apply_undo_and_incremental_events(self):
        self.add(mode="auto")
        self.messy()
        batch = self.settle()
        first_id = self.svc.events_since(0)["last_id"]
        self.assertIn("renamed", [k for k, _ in self.kinds()])
        self.svc.undo_batch(batch["id"])
        new = self.svc.events_since(first_id)["events"]
        self.assertEqual([e["kind"] for e in new], ["undone"])

    def test_live_status_exposes_tracking(self):
        self.add()
        self.messy()
        self.svc.scan_folder(self.svc.folders[0])
        f = self.svc.status()["folders"][0]
        self.assertEqual((f["tracked"], f["settling"]), (1, 1))
        self.assertEqual(self.svc.status()["live"]["phase"], "idle")

    def test_threaded_and_serial_pipelines_agree(self):
        from organizer.modules.pipeline import Pipeline
        for i in range(24):
            (self.root / f"{i:012x}.txt").write_text(helpers.SALES_TEXT if i % 2 else helpers.RECIPE_TEXT)
        for i in range(10):
            helpers.photo_like(i, base_seed=i // 2).save(self.root / f"p{i}.jpg")
        def ops(workers):
            plan = Pipeline(self.root, workers=workers, manifest_path=self.root.parent / f"m{workers}.json").run()
            return [(d.features.record.name, d.action, d.dest.name, d.confidence) for d in plan.decisions]
        self.assertEqual(ops(1), ops(4))


class TestFolders(ServiceCase):
    def test_validation(self):
        with self.assertRaises(ValueError):
            self.svc.add_folder(str(self.root / "nope"))
        self.add()
        with self.assertRaises(ValueError):
            self.svc.add_folder(str(self.root))                    # duplicate
        (self.root / "inner").mkdir()
        with self.assertRaises(ValueError):
            self.svc.add_folder(str(self.root / "inner"))          # overlaps
        with self.assertRaises(ValueError):
            self.svc.update_folder(self.svc.folders[0]["id"], mode="chaos")

    def test_remove_rejects_pending_and_forgets(self):
        self.add()
        self.messy()
        self.settle()
        fid = self.svc.folders[0]["id"]
        self.svc.remove_folder(fid)
        self.assertEqual(self.svc.folders, [])
        self.assertEqual(self.svc.status()["pending"], 0)

    def test_settings_persist_across_restart(self):
        self.add(mode="auto", care="cautious")
        self.svc.set_paused(True)
        again = Service(Store(self.svc.store.base))
        self.assertTrue(again.status()["paused"])
        self.assertEqual((again.folders[0]["mode"], again.folders[0]["care"]), ("auto", "cautious"))

    def test_cautious_care_sends_more_files_to_review(self):
        self.add(care="cautious")
        (self.root / "a1b2c3d4e5f6a7b8.txt").write_text("quarterly sales report " * 8)   # ~16 tokens: mid confidence
        batch = self.settle()
        self.assertEqual(batch["counts"]["quarantine"], 1)


class TestSafetyRules(unittest.TestCase):
    def test_dangerous_folders_are_refused(self):
        for bad in (Path(Path.home().anchor), Path.home(), Path("/usr"), Path("/etc")):
            if bad.exists():
                with self.assertRaises(ValueError, msg=str(bad)):
                    check_folder_allowed(bad)


class TestBackgroundThread(ServiceCase):
    def test_watcher_thread_finds_and_proposes_new_file(self):
        with mock.patch.object(svc_mod, "SETTLE_SECONDS", 0):
            self.svc.clock = time.time
            self.svc.store.config["interval"] = 5
            self.add()
            self.svc.start()
            try:
                self.messy()
                os.utime(self.root / "a1b2c3d4e5f6a7b8.txt", (time.time() - 30,) * 2)
                deadline = time.time() + 12
                while time.time() < deadline and not self.svc.list_batches():
                    self.svc.request_scan()
                    time.sleep(0.4)
                self.assertTrue(self.svc.list_batches(), "background thread never proposed the new file")
                self.assertEqual(self.svc.list_batches()[0]["state"], "pending")
            finally:
                self.svc.stop()


if __name__ == "__main__":
    unittest.main()
