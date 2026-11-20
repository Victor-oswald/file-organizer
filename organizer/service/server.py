"""The local web server behind the dashboard.

Security (it only listens on 127.0.0.1, but a web page you visit could still try
to talk to it, so):
  * the Host header must be 127.0.0.1 / localhost         (blocks DNS-rebinding)
  * every /api call needs a random per-run token header    (blocks cross-site requests)
  * the token is only embedded in the page served to a valid Host
  * strict Content-Security-Policy, no framing
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import __version__, config
from ..modules.crawler import snapshot
from . import autostart
from .paths import app_data_dir, common_folders, drives
from .service import Service, check_folder_allowed

DEFAULT_PORT = 8765
STATIC = Path(__file__).parent / "static" / "index.html"
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def list_dir(path_str: str) -> dict:
    """Folder-picker data. Only directory NAMES are returned, never file contents."""
    shortcuts = [{"name": n, "path": str(p)} for n, p in common_folders()]
    if not path_str:
        return {"path": "", "parent": None, "dirs": [], "shortcuts": shortcuts, "drives": drives(), "allowed": False}
    p = Path(path_str).expanduser()
    if not p.is_dir():
        raise ApiError("That folder does not exist.")
    p = p.resolve()
    dirs = []
    try:
        for e in sorted(os.scandir(p), key=lambda e: e.name.lower()):
            if e.name.startswith(".") or e.name in config.MANAGED_DIRS:
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    dirs.append({"name": e.name, "path": str(Path(e.path))})
            except OSError:
                continue
    except OSError:
        raise ApiError("This folder cannot be opened (permission denied).")
    try:
        check_folder_allowed(p)
        allowed = True
    except ValueError:
        allowed = False
    parent = "" if p.parent == p else str(p.parent)
    crumbs = [{"name": part, "path": str(Path(*p.parts[: i + 1]))} for i, part in enumerate(p.parts)]
    return {"path": str(p), "parent": parent, "crumbs": crumbs, "dirs": dirs[:500], "shortcuts": shortcuts, "drives": drives(),
            "allowed": allowed}


def open_in_file_manager(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))                         # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


class App:
    """Everything the handler needs; one instance per running server."""

    def __init__(self, service: Service, token: str | None = None) -> None:
        self.service = service
        self.token = token or secrets.token_urlsafe(24)
        self.quit_event = threading.Event()
        self.access = "none"                 # how the user can reach the app: tray | window | none
        self.html = STATIC.read_text(encoding="utf-8")

    # ---- API implementations: (body, query, *groups) -> JSON-able
    def state(self, *_):
        s = self.service.status()
        s["autostart"] = autostart.is_enabled()
        s["access"] = self.access
        return s

    def pause(self, body, *_):
        self.service.set_paused(bool(body.get("paused")))
        return self.state()

    def settings(self, body, *_):
        if "interval" in body:
            self.service.set_interval(int(body["interval"]))
        if "autostart" in body:
            (autostart.enable if body["autostart"] else autostart.disable)()
        return self.state()

    def fs(self, body, query, *_):
        return list_dir(query.get("path", [""])[0])

    def fs_info(self, body, query, *_):
        """How many files does this folder already hold? (shown while adding a folder)"""
        path = Path(query.get("path", [""])[0]).expanduser()
        if not path.is_dir():
            raise ApiError("That folder does not exist.")
        snap = snapshot(path.resolve(), recursive=query.get("recursive", ["1"])[0] != "0", skip_projects=True)
        try:
            check_folder_allowed(path)
            allowed = True
        except ValueError:
            allowed = False
        return {"files": len(snap), "bytes": sum(size for size, _ in snap.values()), "allowed": allowed}

    def add_folder(self, body, *_):
        return self.service.add_folder(body.get("path", ""), **{k: v for k, v in body.items() if k != "path"})

    def edit_folder(self, body, query, fid):
        return self.service.update_folder(fid, **body)

    def delete_folder(self, body, query, fid):
        self.service.remove_folder(fid)
        return {"ok": True}

    def scan_folder(self, body, query, fid):
        self.service.get_folder(fid)
        self.service.request_scan(fid, everything=bool(body.get("everything")))
        return {"ok": True}

    def open_folder(self, body, query, fid):
        folder = self.service.get_folder(fid)
        target = Path(folder["path"])
        if body.get("review"):
            target = target / config.REVIEW_DIR
        if not target.is_dir():
            raise ApiError("That folder does not exist yet.")
        open_in_file_manager(target)
        return {"ok": True}

    def events(self, body, query, *_):
        return self.service.events_since(int(query.get("since", ["0"])[0] or 0))

    def batches(self, *_):
        return self.service.list_batches()[:100]

    def batch(self, body, query, bid):
        return self.service.batch_detail(bid)

    def apply_batch(self, body, query, bid):
        ids = body.get("ids")
        return self.service.apply_batch(bid, [int(i) for i in ids] if ids is not None else None)

    def reject_batch(self, body, query, bid):
        self.service.reject_batch(bid)
        return {"ok": True}

    def undo_batch(self, body, query, bid):
        return self.service.undo_batch(bid)

    def quit(self, *_):
        self.quit_event.set()
        return {"ok": True}

    def routes(self):
        R = re.compile
        return [
            ("GET", R(r"^/api/state$"), self.state), ("POST", R(r"^/api/pause$"), self.pause),
            ("POST", R(r"^/api/settings$"), self.settings), ("GET", R(r"^/api/fs$"), self.fs), ("GET", R(r"^/api/fs/info$"), self.fs_info),
            ("POST", R(r"^/api/folders$"), self.add_folder),
            ("POST", R(r"^/api/folders/(\w+)$"), self.edit_folder),
            ("POST", R(r"^/api/folders/(\w+)/delete$"), self.delete_folder),
            ("POST", R(r"^/api/folders/(\w+)/scan$"), self.scan_folder),
            ("POST", R(r"^/api/folders/(\w+)/open$"), self.open_folder),
            ("GET", R(r"^/api/events$"), self.events), ("GET", R(r"^/api/batches$"), self.batches), ("GET", R(r"^/api/batches/(\w+)$"), self.batch),
            ("POST", R(r"^/api/batches/(\w+)/apply$"), self.apply_batch),
            ("POST", R(r"^/api/batches/(\w+)/reject$"), self.reject_batch),
            ("POST", R(r"^/api/batches/(\w+)/undo$"), self.undo_batch),
            ("POST", R(r"^/api/quit$"), self.quit),
        ]


def make_handler(app: App):
    routes = app.routes()

    class Handler(BaseHTTPRequestHandler):
        server_version = "FileOrganizer"

        def log_message(self, *_):             # keep the console quiet
            pass

        # ---- helpers
        def _host_ok(self) -> bool:
            host = self.headers.get("Host", "")
            name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.rsplit("]:", 1)[0] + "]"
            return name.lower() in ALLOWED_HOSTS

        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; "
                             "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj) -> None:
            self._send(status, json.dumps(obj).encode("utf-8"), "application/json; charset=utf-8")

        # ---- dispatch
        def _handle(self, method: str) -> None:
            if not self._host_ok():
                return self._json(403, {"error": "forbidden host"})
            url = urlparse(self.path)
            if method == "GET" and url.path == "/":
                page = app.html.replace("{{TOKEN}}", app.token).replace("{{VERSION}}", __version__)
                return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            if method == "GET" and url.path == "/api/ping":
                return self._json(200, {"app": "file-organizer", "version": __version__})
            if not url.path.startswith("/api/"):
                return self._json(404, {"error": "not found"})
            if not hmac.compare_digest(self.headers.get("X-Auth-Token", ""), app.token):
                return self._json(401, {"error": "missing or wrong token; reload the page"})
            body: dict = {}
            if method == "POST":
                if "application/json" not in self.headers.get("Content-Type", ""):
                    return self._json(415, {"error": "JSON required"})
                length = int(self.headers.get("Content-Length") or 0)
                if length > 1_000_000:
                    return self._json(413, {"error": "too large"})
                try:
                    body = json.loads(self.rfile.read(length) or b"{}")
                except ValueError:
                    return self._json(400, {"error": "invalid JSON"})
            for m, pattern, fn in routes:
                match = pattern.match(url.path)
                if m == method and match:
                    try:
                        return self._json(200, fn(body, parse_qs(url.query), *match.groups()))
                    except ApiError as exc:
                        return self._json(exc.status, {"error": str(exc)})
                    except ValueError as exc:
                        return self._json(400, {"error": str(exc)})
                    except KeyError:
                        return self._json(404, {"error": "not found"})
                    except Exception as exc:       # never crash the server on one bad request
                        return self._json(500, {"error": f"{type(exc).__name__}: {exc}"})
            self._json(404, {"error": "not found"})

        def do_GET(self):
            self._handle("GET")

        def do_POST(self):
            self._handle("POST")

    return Handler


# ---- starting / finding the app -------------------------------------------------
def _ping(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1.5) as r:
            return json.load(r).get("app") == "file-organizer"
    except (urllib.error.URLError, OSError, ValueError):
        return False


def running_port() -> int | None:
    try:
        port = json.loads((app_data_dir() / "run.json").read_text())["port"]
    except (OSError, ValueError, KeyError):
        port = DEFAULT_PORT
    return port if _ping(port) else None


def create_server(app: App, port: int = DEFAULT_PORT) -> ThreadingHTTPServer:
    for candidate in range(port, port + 20):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), make_handler(app))
            server.daemon_threads = True
            (app_data_dir() / "run.json").write_text(json.dumps({"port": candidate, "pid": os.getpid()}))
            return server
        except OSError:
            continue
    raise OSError("No free port found between %d and %d" % (port, port + 19))


def run(open_browser: bool = True, use_tray: bool = True, port: int = DEFAULT_PORT) -> int:
    existing = running_port()
    if existing:
        print(f"File Organizer is already running: http://127.0.0.1:{existing}")
        if open_browser:
            webbrowser.open(f"http://127.0.0.1:{existing}")
        return 0
    service = Service()
    app = App(service)
    server = create_server(app, port)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    service.start()
    threading.Thread(target=server.serve_forever, name="http", daemon=True).start()
    print(f"File Organizer is running at {url}  (Ctrl+C to quit)")
    if open_browser:
        webbrowser.open(url)

    def shutdown() -> None:
        app.quit_event.set()

    def report(mode: str) -> None:
        app.access = mode

    try:
        shown = False
        if use_tray:
            shown = _reach_user(service, url, shutdown, app.quit_event, report)
        if not shown:
            print("No tray icon or control window could be shown. Start File Organizer again to reopen the dashboard.")
            app.quit_event.wait()
    except KeyboardInterrupt:
        pass
    finally:
        service.stop()
        server.shutdown()
        (app_data_dir() / "run.json").unlink(missing_ok=True)
    return 0


def _reach_user(service, url, shutdown, quit_event, report) -> bool:
    """Tray icon first; if that is impossible, a small control window. Blocks until quit."""
    from .tray import run_control_window, run_tray
    open_dashboard = lambda: webbrowser.open(url)           # noqa: E731
    if run_tray(service, open_dashboard, shutdown, quit_event, report):
        return True
    return run_control_window(service, open_dashboard, quit_event, report)
