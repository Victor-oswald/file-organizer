"""'Start automatically when I sign in', for Windows, macOS and Linux.

Each platform has its own mechanism; all of them just run a tiny launcher
script that starts `organizer daemon` (the app without opening a browser).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .paths import app_data_dir

APP_ID = "com.fileorganizer.agent"


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def python_executable(win: bool | None = None) -> str:
    exe = Path(sys.executable)
    if (sys.platform == "win32") if win is None else win:
        pythonw = exe.with_name("pythonw.exe")          # no console window
        if pythonw.exists():
            return str(pythonw)
    return str(exe)


def write_launcher(base: Path | None = None) -> Path:
    base = base or app_data_dir()
    path = base / ("launcher.pyw" if sys.platform == "win32" else "launcher.py")
    path.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(project_root())!r})\n"
        "from organizer.__main__ import main\n"
        "raise SystemExit(main(['daemon']))\n", encoding="utf-8")
    return path


# ---- pure builders (unit-tested on every OS) -------------------------------
def windows_command(py: str, launcher: str) -> str:
    return f'"{py}" "{launcher}"'


def macos_plist(py: str, launcher: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{APP_ID}</string>
  <key>ProgramArguments</key><array><string>{py}</string><string>{launcher}</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><false/>
</dict></plist>
"""


def linux_desktop(py: str, launcher: str) -> str:
    return (f"[Desktop Entry]\nType=Application\nName=File Organizer\n"
            f"Exec=\"{py}\" \"{launcher}\"\nX-GNOME-Autostart-enabled=true\nTerminal=false\n")


# ---- platform glue -----------------------------------------------------------
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _linux_file(home: Path | None) -> Path:
    cfg = Path(os.environ.get("XDG_CONFIG_HOME") or (home or Path.home()) / ".config")
    return cfg / "autostart" / "file-organizer.desktop"


def _mac_file(home: Path | None) -> Path:
    return (home or Path.home()) / "Library" / "LaunchAgents" / f"{APP_ID}.plist"


def is_enabled(home: Path | None = None) -> bool:
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
                winreg.QueryValueEx(k, "FileOrganizer")
                return True
        except OSError:
            return False
    return (_mac_file(home) if sys.platform == "darwin" else _linux_file(home)).exists()


def enable(home: Path | None = None) -> None:
    launcher = str(write_launcher())
    py = python_executable()
    if sys.platform == "win32":
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            winreg.SetValueEx(k, "FileOrganizer", 0, winreg.REG_SZ, windows_command(py, launcher))
        return
    path = _mac_file(home) if sys.platform == "darwin" else _linux_file(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(macos_plist(py, launcher) if sys.platform == "darwin" else linux_desktop(py, launcher), encoding="utf-8")


def disable(home: Path | None = None) -> None:
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, "FileOrganizer")
        except OSError:
            pass
        return
    (_mac_file(home) if sys.platform == "darwin" else _linux_file(home)).unlink(missing_ok=True)
