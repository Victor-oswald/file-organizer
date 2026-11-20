"""Directory validation and collision-free destination paths.

Evolved from the original `Parser.parse_directory` check: the validation idea
is kept, and the class now also knows where the organizer's own folders live
and how to hand out destination paths that never overwrite anything.
"""

from __future__ import annotations

from pathlib import Path

from .. import config


def validate_directory(directory: str | Path) -> Path:
    """Return an absolute, resolved directory path or raise ValueError."""
    path = Path(directory).expanduser()
    if not path.is_dir():
        raise ValueError(f"Please provide a valid directory path: {directory!s}")
    return path.resolve()


class DirectoryManager:
    """Knows the root being organized and the folders the organizer owns."""

    def __init__(self, root: str | Path) -> None:
        self.root = validate_directory(root)

    @property
    def review_dir(self) -> Path:
        return self.root / config.REVIEW_DIR

    @property
    def duplicates_dir(self) -> Path:
        return self.root / config.DUPLICATES_DIR

    @property
    def logs_dir(self) -> Path:
        return self.root / config.LOGS_DIR

    @property
    def events_dir(self) -> Path:
        return self.root / config.EVENTS_DIR

    @property
    def groups_dir(self) -> Path:
        return self.root / config.GROUPS_DIR

    def relative(self, path: Path) -> str:
        """Path relative to root, always with forward slashes (portable manifests)."""
        return path.relative_to(self.root).as_posix()

    def absolute(self, rel: str) -> Path:
        return self.root.joinpath(*rel.split("/"))


class PathReserver:
    """Hands out destination paths that cannot collide.

    Collisions are checked case-INSENSITIVELY, because Windows and macOS file
    systems treat "A.txt" and "a.txt" as the same file.
    """

    def __init__(self, existing: list[Path] | None = None) -> None:
        self._taken: set[str] = set()
        for p in existing or []:
            self._taken.add(self._key(p))

    @staticmethod
    def _key(path: Path) -> str:
        return str(path).casefold()

    def reserve(self, wanted: Path, current: Path | None = None) -> Path:
        """Return `wanted`, or `wanted` with `_2`, `_3`... added to the stem."""
        current_key = self._key(current) if current is not None else None
        candidate = wanted
        n = 1
        while self._conflicts(candidate, current_key):
            n += 1
            candidate = wanted.with_name(f"{wanted.stem}_{n}{wanted.suffix}")
        self._taken.add(self._key(candidate))
        return candidate

    def _conflicts(self, candidate: Path, current_key: str | None) -> bool:
        key = self._key(candidate)
        if key == current_key:
            return False                       # it's the file's own location
        if key in self._taken:
            return True
        return candidate.exists()              # something we did not crawl lives there
