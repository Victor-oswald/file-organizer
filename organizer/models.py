"""The data containers that flow through the pipeline.

FileRecord  : what the file system tells us (immutable, produced by the crawler)
Features    : everything we learn by inspecting the file (mutable, enriched stage by stage)
Proposal    : a scorer's opinion: confidence + suggested name + the reasons
Decision    : the final action for one file (what the manifest records)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FileRecord:
    path: Path
    name: str
    stem: str
    extension: str          # lowercase, includes the dot, may be ""
    size: int
    modified: float         # POSIX timestamp


@dataclass
class Features:
    record: FileRecord
    error: str | None = None             # set if the file could not be read at all

    # stage 1 - cheap fingerprints
    partial_hash: str = ""
    sha256: str | None = None            # filled lazily, only for duplicate candidates
    magic_kind: str = "unknown"          # "jpeg", "pdf", "text", ...
    category: str = "other"              # document|code|data|image|video|audio|archive|binary|empty|other
    ext_mismatch: bool = False

    # text-like features
    tokens: list[str] = field(default_factory=list)
    text_title: str | None = None

    # image / video features
    dhash: int | None = None
    width: int | None = None
    height: int | None = None
    taken: datetime | None = None        # a REAL metadata date (EXIF / MP4), not mtime
    taken_source: str = ""
    is_camera: bool = False
    has_gps: bool = False
    is_screenshot: bool = False
    flatness: float = 0.0                # 0..1, how "flat" the colours are
    duration: float | None = None
    fps: float | None = None

    # audio features
    artist: str | None = None
    title: str | None = None
    channels: int | None = None
    sample_rate: int | None = None
    is_voice_memo: bool = False

    # binary features
    vendor: str | None = None
    product: str | None = None

    notes: list[str] = field(default_factory=list)

    def best_date(self) -> datetime:
        """Real metadata date if we have one, else the file's modification time."""
        if self.taken is not None:
            return self.taken
        try:
            return datetime.fromtimestamp(self.record.modified)
        except (OverflowError, OSError, ValueError):
            return datetime(1970, 1, 1)


@dataclass
class Proposal:
    confidence: float
    reason: str
    stem: str | None = None              # proposed new name WITHOUT extension
    signals: dict[str, Any] = field(default_factory=dict)


@dataclass
class Decision:
    features: Features
    action: str                          # keep | rename | move | quarantine | duplicate
    confidence: float
    reason: str
    dest: Path                           # absolute destination (== source for no-ops)
    signals: dict[str, Any] = field(default_factory=dict)
    duplicate_of: Path | None = None
