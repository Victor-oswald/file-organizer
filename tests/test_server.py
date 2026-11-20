"""Server security + API behaviour, and autostart builders."""

from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from organizer.service import autostart, server
from organizer.service import service as svc_mod
from organizer.service.service import Service
from organizer.service.store import Store

from . import helpers


class ServerCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name).resolve()
        os.environ["FILE_ORGANIZER_HOME"] = str(tmp / "appdata")
        self.root = tmp / "Downloads"
        self.root.mkdir()
        self.offset = 0.0
        self.svc = Service(Store(tmp / "appdata"), clock=lambda: time.time() + self.offset)
        self.app = server.App(self.svc, token="test-token")
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.app))
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self._p = mock.patch.object(svc_mod, "check_folder_allowed", lambda p: None)
        self._p.start()

    def tearDown(self):
        self._p.stop()
        self.httpd.shutdown()
        self.httpd.server_close()
        os.environ.pop("FILE_ORGANIZER_HOME", None)
        self._tmp.cleanup()

    def call(self, method, path, body=None, *, token="test-token", host=None, ctype="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if token is not None:
            headers["X-Auth-Token"] = token
        data = None
        if body is not None:
            data = json.dumps(body)
            headers["Content-Type"] = ctype
        elif method == "POST":
            data, headers["Content-Type"] = "{}", ctype
        conn.request(method, path, body=data, headers=headers)
        r = conn.getresponse()
        raw = r.read()
        conn.close()
        try:
            return r.status, json.loads(raw)
        except ValueError:
            return r.status, raw.decode()


class TestSecurity(ServerCase):
    def test_page_embeds_token_only_for_local_host(self):
        status, html = self.call("GET", "/", token=None)
        self.assertEqual(status, 200)
        self.assertIn("test-token", html)
        self.assertNotIn("{{TOKEN}}", html)
        status, _ = self.call("GET", "/", token=None, host="evil.example.com")      # DNS-rebinding style
        self.assertEqual(status, 403)

    def test_api_needs_token(self):
        self.assertEqual(self.call("GET", "/api/state", token=None)[0], 401)
        self.assertEqual(self.call("GET", "/api/state", token="wrong")[0], 401)
        self.assertEqual(self.call("GET", "/api/state")[0], 200)

    def test_api_rejects_foreign_host_even_with_token(self):
        self.assertEqual(self.call("GET", "/api/state", host="attacker.test:8765")[0], 403)

    def test_post_requires_json_content_type(self):
        self.assertEqual(self.call("POST", "/api/pause", {"paused": True}, ctype="text/plain")[0], 415)

    def test_ping_is_public_but_minimal(self):
        status, data = self.call("GET", "/api/ping", token=None)
        self.assertEqual((status, data["app"]), (200, "file-organizer"))
        self.assertEqual(set(data), {"app", "version"})

    def test_unknown_route_and_security_headers(self):
        self.assertEqual(self.call("GET", "/api/nothing")[0], 404)
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request("GET", "/", headers={"Host": f"127.0.0.1:{self.port}"})
        r = conn.getresponse()
        r.read()
        self.assertEqual(r.getheader("X-Frame-Options"), "DENY")
        self.assertIn("default-src 'self'", r.getheader("Content-Security-Policy"))


class TestApiFlow(ServerCase):
    def test_full_flow_add_review_apply_undo(self):
        helpers_text = helpers.SALES_TEXT
        status, folder = self.call("POST", "/api/folders", {"path": str(self.root), "mode": "ask"})
        self.assertEqual(status, 200)
        (self.root / "a1b2c3d4e5f6a7b8.txt").write_text(helpers_text)
        f = self.svc.folders[0]
        self.svc.scan_folder(f)
        self.offset += 10
        self.svc.scan_folder(f)

        _, state = self.call("GET", "/api/state")
        self.assertEqual((state["pending"], len(state["folders"])), (1, 1))
        _, batches = self.call("GET", "/api/batches")
        bid = batches[0]["id"]
        _, detail = self.call("GET", f"/api/batches/{bid}")
        self.assertEqual(len(detail["items"]), 1)
        self.assertRegex(detail["items"][0]["new"], r"Document_")

        status, result = self.call("POST", f"/api/batches/{bid}/apply", {"ids": [detail["items"][0]["id"]]})
        self.assertEqual(result["applied"], 1)
        self.assertFalse((self.root / "a1b2c3d4e5f6a7b8.txt").exists())
        status, _ = self.call("POST", f"/api/batches/{bid}/undo", {})
        self.assertEqual(status, 200)
        self.assertTrue((self.root / "a1b2c3d4e5f6a7b8.txt").exists())
        self.assertEqual(self.call("POST", f"/api/batches/{bid}/undo", {})[0], 400)   # friendly error, not a crash

    def test_bad_input_gives_friendly_errors(self):
        status, data = self.call("POST", "/api/folders", {"path": str(self.root / "missing")})
        self.assertEqual(status, 400)
        self.assertIn("does not exist", data["error"])
        self.assertEqual(self.call("POST", "/api/folders/zzzz/scan", {})[0], 404)
        self.assertEqual(self.call("GET", "/api/batches/nope")[0], 404)

    def test_filesystem_browser(self):
        (self.root / "Photos").mkdir()
        (self.root / ".secret").mkdir()
        (self.root / "file.txt").write_text("x")
        _, start = self.call("GET", "/api/fs?path=")
        self.assertEqual(start["path"], "")
        self.assertTrue(start["shortcuts"])
        _, listing = self.call("GET", f"/api/fs?path={self.root}")
        self.assertEqual([d["name"] for d in listing["dirs"]], ["Photos"])      # no hidden folders, no files
        self.assertEqual(listing["crumbs"][-1]["name"], "Downloads")
        self.assertEqual(self.call("GET", "/api/fs?path=/definitely/not/here")[0], 400)

    def test_folder_info_counts_existing_files(self):
        (self.root / "a.txt").write_text("x" * 10)
        (self.root / "sub").mkdir()
        (self.root / "sub" / "b.txt").write_text("y" * 20)
        (self.root / ".hidden").write_text("z")
        _, info = self.call("GET", f"/api/fs/info?path={self.root}")
        self.assertEqual((info["files"], info["bytes"]), (2, 30))
        _, flat = self.call("GET", f"/api/fs/info?path={self.root}&recursive=0")
        self.assertEqual(flat["files"], 1)
        self.assertEqual(self.call("GET", "/api/fs/info?path=/not/here")[0], 400)

    def test_state_tells_the_ui_how_to_reach_the_app(self):
        self.assertEqual(self.call("GET", "/api/state")[1]["access"], "none")
        self.app.access = "tray"
        self.assertEqual(self.call("GET", "/api/state")[1]["access"], "tray")

    def test_settings_and_pause(self):
        _, s = self.call("POST", "/api/pause", {"paused": True})
        self.assertTrue(s["paused"])
        _, s = self.call("POST", "/api/settings", {"interval": 0})
        self.assertEqual(s["interval"], 1)                                      # clamped to a sane minimum
        _, s = self.call("POST", "/api/settings", {"interval": 99999})
        self.assertEqual(s["interval"], 3600)

    def test_quit_sets_event(self):
        self.call("POST", "/api/quit", {})
        self.assertTrue(self.app.quit_event.is_set())


class TestAutostart(unittest.TestCase):
    def test_builders(self):
        self.assertEqual(autostart.windows_command(r"C:\Py\pythonw.exe", r"C:\x\launcher.pyw"),
                         '"C:\\Py\\pythonw.exe" "C:\\x\\launcher.pyw"')
        plist = autostart.macos_plist("/usr/bin/python3", "/x/launcher.py")
        self.assertIn("<key>RunAtLoad</key><true/>", plist)
        self.assertIn("/x/launcher.py", plist)
        desktop = autostart.linux_desktop("/usr/bin/python3", "/x/launcher.py")
        self.assertIn("Exec=\"/usr/bin/python3\" \"/x/launcher.py\"", desktop)

    @unittest.skipUnless(os.name == "posix", "file-based autostart")
    def test_enable_disable_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with mock.patch.dict(os.environ, {"FILE_ORGANIZER_HOME": str(home / "app"), "XDG_CONFIG_HOME": str(home / "cfg")}):
                self.assertFalse(autostart.is_enabled(home))
                autostart.enable(home)
                self.assertTrue(autostart.is_enabled(home))
                launcher = next((home / "app").glob("launcher.py*"))
                self.assertIn("main(['daemon'])", launcher.read_text())
                autostart.disable(home)
                self.assertFalse(autostart.is_enabled(home))


if __name__ == "__main__":
    unittest.main()
