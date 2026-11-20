"""Stage 0: walk a directory and yield one FileRecord per usable file.

The crawler does NO reading of file contents. It only asks the file system for
metadata (size, mtime), which is why it is fast and safe.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Iterator

from .. import config
from ..models import FileRecord

_HIDDEN_ATTRS = stat.FILE_ATTRIBUTE_HIDDEN | stat.FILE_ATTRIBUTE_SYSTEM

# A folder containing any of these is a software project. Renaming files inside
# one (utils.py -> 2026-11-16_Code_...) would break imports and builds, so a
# project folder is skipped as a whole.
_PROJECT_FILES = frozenset({
    "package.json", "pyproject.toml", "setup.py", "setup.cfg", "cargo.toml", "go.mod", "pom.xml",
    "build.gradle", "cmakelists.txt", "makefile", "composer.json", "gemfile", "manage.py",
})
_PROJECT_DIRS = frozenset({".git", ".hg", ".svn"})
_PROJECT_SUFFIXES = (".sln", ".csproj", ".xcodeproj")


def is_project_dir(full: str) -> bool:
    try:
        with os.scandir(full) as it:
            for entry in it:
                n = entry.name.lower()
                if n in _PROJECT_FILES or n in _PROJECT_DIRS or n.endswith(_PROJECT_SUFFIXES):
                    return True
    except OSError:
        return True            # cannot even list it: do not touch it
    return False


_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)   # Windows symlinks AND junctions


def _skip_dir(entry: os.DirEntry, skip_projects: bool) -> bool:
    name = entry.name
    if name.startswith(".") or name in config.MANAGED_DIRS or name.lower() in config.IGNORED_DIR_NAMES:
        return True
    try:
        if entry.is_symlink():
            return True
        attrs = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return True                       # cannot even stat it: leave it alone
    if attrs & (_HIDDEN_ATTRS | _REPARSE_POINT):
        return True
    return skip_projects and is_project_dir(entry.path)


def _walk(root: Path, recursive: bool, skip_projects: bool) -> Iterator[tuple[str, str, os.stat_result]]:
    """Yield (full_path, name, stat) for every regular, visible, non-managed file.

    Built on os.scandir: the directory listing already carries the file type (and, on
    Windows, the size and times), so each file costs one stat() call instead of three.
    Entries are sorted by name, so the order is the same on every OS."""
    stack = [os.fspath(root)]
    while stack:
        top = stack.pop()
        try:
            with os.scandir(top) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            continue                                  # unreadable folder: skip it, never crash
        subdirs: list[str] = []
        for entry in entries:
            name = entry.name
            try:
                if entry.is_dir(follow_symlinks=False):
                    if recursive and not _skip_dir(entry, skip_projects):
                        subdirs.append(entry.path)
                    continue
                if name.startswith(".") or name.startswith("~$") or name.lower() in config.IGNORED_FILE_NAMES:
                    continue
                if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                    continue
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue                              # one bad file must never stop the whole run
            if getattr(st, "st_file_attributes", 0) & (_HIDDEN_ATTRS | _REPARSE_POINT):
                continue
            yield entry.path, name, st
        stack.extend(reversed(subdirs))


def crawl(root: Path, *, recursive: bool = True, skip_projects: bool = False) -> Iterator[FileRecord]:
    """Yield a FileRecord for every usable file under root (see _walk for what is skipped).

    recursive=False       : only the files directly inside `root`
    skip_projects=True    : skip sub-folders that look like software projects"""
    for full, name, st in _walk(root, recursive, skip_projects):
        path = Path(full)
        yield FileRecord(path=path, name=name, stem=path.stem, extension=path.suffix.lower(),
                         size=st.st_size, modified=st.st_mtime)


def snapshot(root: Path, *, recursive: bool = True, skip_projects: bool = False) -> dict[str, tuple[int, float]]:
    """Cheapest possible 'what is in this folder?': {path: (size, mtime)}.

    This is what the background watcher runs every few seconds, so it builds no Path or
    FileRecord objects at all."""
    return {full: (st.st_size, st.st_mtime) for full, _name, st in _walk(root, recursive, skip_projects)}
