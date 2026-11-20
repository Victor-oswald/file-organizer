"""Where the app keeps its own settings, per operating system."""

from __future__ import annotations

import os
import string
import sys
from pathlib import Path


def app_data_dir() -> Path:
    override = os.environ.get("FILE_ORGANIZER_HOME")
    if override:
        base = Path(override)
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "FileOrganizer"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "FileOrganizer"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "file-organizer"
    base.mkdir(parents=True, exist_ok=True)
    return base


def common_folders() -> list[tuple[str, Path]]:
    """Friendly starting points for the folder picker (only those that exist)."""
    home = Path.home()
    names = [("Downloads", "Downloads"), ("Documents", "Documents"), ("Desktop", "Desktop"),
             ("Pictures", "Pictures"), ("Music", "Music"), ("Videos", "Videos")]
    out = [(label, home / folder) for label, folder in names if (home / folder).is_dir()]
    return out + [("Home", home)]


def drives() -> list[str]:
    if sys.platform != "win32":
        return []
    return [f"{c}:\\" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")]
