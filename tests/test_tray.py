"""Tray + control-window logic, tested with fake pystray / tkinter modules (no display needed)."""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

from organizer.service import tray
from organizer.service import service as svc_mod
from organizer.service.service import Service
from organizer.service.store import Store


def make_service(tmp: Path) -> Service:
    os.environ["FILE_ORGANIZER_HOME"] = str(tmp / "app")
    return Service(Store(tmp / "app"))


class FakePystray(types.ModuleType):
    """Records what the app builds; Icon.run() calls setup(), like the real one does."""

    def __init__(self, fail_run=False):
        super().__init__("pystray")
        outer = self
        outer.icons = []

        class MenuItem:
            def __init__(self, text, action, default=False, enabled=True):
                self.text, self.action, self.default, self.enabled = text, action, default, enabled

        class Menu:
            SEPARATOR = object()
            def __init__(self, *items):
                self.items = items

        class Icon:
            def __init__(self, name, image, title, menu):
                self.name, self.image, self.title, self.menu = name, image, title, menu
                self.visible, self.notified, self.stopped = False, [], False
                outer.icons.append(self)
            def run(self, setup=None):
                if fail_run:
                    raise RuntimeError("no tray on this desktop")
                if setup:
                    setup(self)
            def notify(self, message, title=None):
                self.notified.append((title, message))
            def stop(self):
                self.stopped = True

        self.Menu, self.MenuItem, self.Icon = Menu, MenuItem, Icon


class TestDescribe(unittest.TestCase):
    def test_status_texts(self):
        with tempfile.TemporaryDirectory() as t:
            svc = make_service(Path(t).resolve())
            self.assertEqual(tray.describe(svc), "No folders added yet")
            with mock.patch.object(svc_mod, "check_folder_allowed", lambda p: None):
                svc.add_folder(t)
            self.assertTrue(tray.describe(svc).startswith("Watching 1 folder"))
            svc.set_paused(True)
            self.assertEqual(tray.describe(svc), "Paused")
            svc.set_paused(False)
            svc._live = {"phase": "working", "label": "Analyzing files", "done": 3, "total": 9, "folder": "Downloads"}
            self.assertEqual(tray.describe(svc), "Analyzing files 3/9 (Downloads)")


class TestTray(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.svc = make_service(Path(self._tmp.name).resolve())
        self.quit_event = threading.Event()
        self.calls = []

    def tearDown(self):
        self.quit_event.set()
        os.environ.pop("FILE_ORGANIZER_HOME", None)
        self._tmp.cleanup()

    def run_tray(self, fake):
        modes = []
        with mock.patch.dict(sys.modules, {"pystray": fake}):
            ok = tray.run_tray(self.svc, lambda: self.calls.append("open"), lambda: self.calls.append("quit"),
                               self.quit_event, modes.append)
        return ok, modes

    def test_menu_has_default_open_action_and_live_items(self):
        fake = FakePystray()
        ok, modes = self.run_tray(fake)
        icon = fake.icons[0]
        self.assertTrue(ok)
        self.assertEqual(modes, ["tray"])
        self.assertTrue(icon.visible, "setup() must make the icon visible")
        self.assertTrue(icon.notified, "user is told once where the icon is")
        items = [i for i in icon.menu.items if i is not fake.Menu.SEPARATOR]
        default = [i for i in items if i.default]
        self.assertEqual([d.text for d in default], ["Open dashboard"], "left-click must open the dashboard")
        default[0].action()
        self.assertEqual(self.calls, ["open"])
        status_line = next(i for i in items if not i.enabled)
        self.assertIn("No folders", status_line.text(None))
        pause = next(i for i in items if callable(i.text) and "monitoring" in i.text(None))
        self.assertEqual(pause.text(None), "Pause monitoring")
        pause.action()
        self.assertTrue(self.svc.store.config["paused"])
        self.assertEqual(pause.text(None), "Resume monitoring")

    def test_quit_stops_icon_and_signals_app(self):
        fake = FakePystray()
        self.run_tray(fake)
        icon = fake.icons[0]
        quit_item = [i for i in icon.menu.items if i is not fake.Menu.SEPARATOR and i.text == "Quit File Organizer"][0]
        quit_item.action(icon)
        self.assertTrue(icon.stopped)
        self.assertEqual(self.calls, ["quit"])

    def test_dashboard_quit_closes_the_icon(self):
        fake = FakePystray()
        self.run_tray(fake)
        self.quit_event.set()
        deadline = time.time() + 3
        while time.time() < deadline and not fake.icons[0].stopped:
            time.sleep(0.05)
        self.assertTrue(fake.icons[0].stopped)

    def test_falls_back_when_pystray_missing_or_broken(self):
        with mock.patch.dict(sys.modules, {"pystray": None}):          # import raises ImportError
            self.assertFalse(tray.run_tray(self.svc, lambda: None, lambda: None, self.quit_event))
        ok, modes = self.run_tray(FakePystray(fail_run=True))
        self.assertFalse(ok)


class FakeTk(types.ModuleType):
    def __init__(self, fail=False):
        super().__init__("tkinter")
        outer, self.created = self, []

        class Widget:
            def __init__(self, *a, **k):
                self.opts = dict(k)
                outer.created.append(self)
            def pack(self, **k): pass
            def configure(self, **k): self.opts.update(k)
        class Tk(Widget):
            def __init__(self):
                if fail:
                    raise RuntimeError("no display")
                super().__init__()
                self.protocols, self.iconified, self.destroyed, self.afters = {}, False, False, []
            def title(self, t): self.opts["title"] = t
            def resizable(self, *a): pass
            def protocol(self, name, fn): self.protocols[name] = fn
            def iconify(self): self.iconified = True
            def after(self, ms, fn): self.afters.append(fn)
            def destroy(self): self.destroyed = True
            def mainloop(self): pass
        self.Tk, self.Label, self.Button, self.Frame = Tk, Widget, Widget, Widget


class TestControlWindow(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.svc = make_service(Path(self._tmp.name).resolve())
        self.quit_event = threading.Event()

    def tearDown(self):
        os.environ.pop("FILE_ORGANIZER_HOME", None)
        self._tmp.cleanup()

    def test_window_stays_alive_when_closed_and_quit_works(self):
        fake, modes, opened = FakeTk(), [], []
        with mock.patch.dict(sys.modules, {"tkinter": fake}):
            ok = tray.run_control_window(self.svc, lambda: opened.append(1), self.quit_event, modes.append)
        self.assertTrue(ok)
        self.assertEqual(modes, ["window"])
        root = next(w for w in fake.created if hasattr(w, "protocols"))
        root.protocols["WM_DELETE_WINDOW"]()
        self.assertTrue(root.iconified and not self.quit_event.is_set(), "closing must only minimize")
        buttons = {w.opts.get("text"): w.opts.get("command") for w in fake.created if "command" in w.opts}
        buttons["Open dashboard"]()
        self.assertEqual(opened, [1])
        buttons["Pause monitoring"]()
        self.assertTrue(self.svc.store.config["paused"])
        buttons["Quit"]()
        self.assertTrue(self.quit_event.is_set())
        root.afters[0]()                                   # the refresh tick notices the quit and closes
        self.assertTrue(root.destroyed)

    def test_no_display_means_no_window(self):
        with mock.patch.dict(sys.modules, {"tkinter": FakeTk(fail=True)}):
            self.assertFalse(tray.run_control_window(self.svc, lambda: None, self.quit_event))
        with mock.patch.dict(sys.modules, {"tkinter": None}):
            self.assertFalse(tray.run_control_window(self.svc, lambda: None, self.quit_event))


if __name__ == "__main__":
    unittest.main()
