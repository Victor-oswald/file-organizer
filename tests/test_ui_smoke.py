"""Runs the dashboard's real JavaScript (in Node, fake DOM) against a live server."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from organizer.service import server
from organizer.service import service as svc_mod
from organizer.service.service import Service
from organizer.service.store import Store

from . import helpers


@unittest.skipUnless(shutil.which("node"), "Node.js not installed")
class TestUiSmoke(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name).resolve()
        os.environ["FILE_ORGANIZER_HOME"] = str(tmp / "appdata")
        self.root = tmp / "Downloads"
        self.root.mkdir()
        self.offset = 0.0
        self.svc = Service(Store(tmp / "appdata"), clock=lambda: time.time() + self.offset)
        self.app = server.App(self.svc, token="smoke-token")
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.app))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self._p = mock.patch.object(svc_mod, "check_folder_allowed", lambda p: None)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self.httpd.shutdown()
        self.httpd.server_close()
        os.environ.pop("FILE_ORGANIZER_HOME", None)
        self._tmp.cleanup()

    def node(self, mode):
        script = Path(__file__).with_name("ui_smoke.js")
        r = subprocess.run(["node", str(script), self.base, mode], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def test_empty_state(self):
        self.assertIn("SMOKE OK (empty)", self.node("empty"))

    def test_full_state_with_hostile_filename(self):
        self.svc.add_folder(str(self.root), mode="ask")
        (self.root / "a1b2c3d4e5f6a7b8.txt").write_text(helpers.SALES_TEXT)
        try:
            (self.root / "IMG_0001<img src=x onerror=alert(1)>.txt").write_text(helpers.RECIPE_TEXT)
        except OSError:
            self.skipTest("file system does not allow this name (Windows)")
        folder = self.svc.folders[0]
        self.svc.scan_folder(folder)
        self.offset += 10
        self.assertIsNotNone(self.svc.scan_folder(folder))
        self.assertIn("SMOKE OK (full)", self.node("full"))
        self.assertTrue(self.svc._requests.get(folder["id"]) is True, "the button's request never reached the service")


if __name__ == "__main__":
    unittest.main()
