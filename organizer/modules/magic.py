"""Stage 1b: identify what a file REALLY is by reading its first bytes.

Extensions lie (people rename things; downloads lose them). The first bytes of
a file, its "magic number", are far more trustworthy.
"""

from __future__ import annotations

from pathlib import Path

from .. import config

# (offset, signature, kind, category)
_SIGNATURES: list[tuple[int, bytes, str, str]] = [
    (0, b"%PDF-", "pdf", "document"),
    (0, b"\x89PNG\r\n\x1a\n", "png", "image"),
    (0, b"\xff\xd8\xff", "jpeg", "image"),
    (0, b"GIF87a", "gif", "image"),
    (0, b"GIF89a", "gif", "image"),
    (0, b"II*\x00", "tiff", "image"),
    (0, b"MM\x00*", "tiff", "image"),
    (0, b"\x1aE\xdf\xa3", "mkv", "video"),
    (0, b"OggS", "ogg", "audio"),
    (0, b"fLaC", "flac", "audio"),
    (0, b"ID3", "mp3", "audio"),
    (0, b"PK\x03\x04", "zip", "archive"),
    (0, b"PK\x05\x06", "zip", "archive"),
    (0, b"Rar!\x1a\x07", "rar", "archive"),
    (0, b"7z\xbc\xaf\x27\x1c", "7z", "archive"),
    (0, b"\x1f\x8b", "gzip", "archive"),
    (257, b"ustar", "tar", "archive"),
    (0, b"\x7fELF", "elf", "binary"),
    (0, b"SQLite format 3\x00", "sqlite", "data"),
]

# ZIP files that are really documents (Office / OpenDocument)
_ZIP_DOCUMENTS = {
    ".docx": ("docx", "document"), ".pptx": ("pptx", "document"),
    ".odt": ("odt", "document"), ".odp": ("odp", "document"),
    ".xlsx": ("xlsx", "data"), ".ods": ("ods", "data"),
}

_PE_EXTENSIONS = {".exe", ".dll", ".sys", ".ocx", ".scr"}

# kind -> extensions that are normal for it (used only to FLAG mismatches)
_EXPECTED_EXT = {
    "jpeg": {".jpg", ".jpeg", ".jpe", ".jfif"}, "png": {".png"}, "gif": {".gif"},
    "tiff": {".tif", ".tiff"}, "bmp": {".bmp"}, "webp": {".webp"},
    "pdf": {".pdf"}, "mp3": {".mp3"}, "wav": {".wav"}, "flac": {".flac"},
    "ogg": {".ogg", ".oga", ".opus"}, "mkv": {".mkv", ".webm"},
    "mp4": {".mp4", ".m4v"}, "mov": {".mov"}, "m4a": {".m4a"}, "avi": {".avi"},
}


def looks_like_text(sample: bytes) -> bool:
    """Heuristic: no NUL bytes, almost no undecodable or control characters."""
    if not sample:
        return False
    if sample.startswith((b"\xff\xfe", b"\xfe\xff")):          # UTF-16 BOM
        return True
    if b"\x00" in sample:
        return False
    decoded = sample.decode("utf-8", errors="replace")
    bad = decoded.count("\ufffd")
    controls = sum(1 for ch in decoded if ord(ch) < 32 and ch not in "\t\n\r\f")
    return bad <= 0.02 * len(decoded) and controls <= 0.01 * len(decoded)


def _sniff_header(head: bytes, ext: str) -> tuple[str, str] | None:
    # RIFF container: the format is named at bytes 8..12
    if head[:4] == b"RIFF" and len(head) >= 12:
        form = head[8:12]
        if form == b"WAVE":
            return "wav", "audio"
        if form == b"WEBP":
            return "webp", "image"
        if form == b"AVI ":
            return "avi", "video"
    # ISO base media (MP4 family): "ftyp" at bytes 4..8, brand at 8..12
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"M4A ", b"M4B "):
            return "m4a", "audio"
        if brand == b"qt  ":
            return "mov", "video"
        return "mp4", "video"
    # MP3 without an ID3 tag starts directly with a frame sync (11 set bits)
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return "mp3", "audio"
    # Weak 2-byte signatures: only trust them together with the extension
    if head[:2] == b"BM" and ext == ".bmp":
        return "bmp", "image"
    if head[:2] == b"MZ" and ext in _PE_EXTENSIONS:
        return "pe", "binary"
    for offset, sig, kind, category in _SIGNATURES:
        if head[offset:offset + len(sig)] == sig:
            if kind == "zip" and ext in _ZIP_DOCUMENTS:
                return _ZIP_DOCUMENTS[ext]
            return kind, category
    return None


def sniff(path: Path, ext: str, size: int) -> tuple[str, str, bool]:
    """Return (kind, category, extension_mismatch). Opens the file; see sniff_bytes."""
    if size == 0:
        return "empty", "empty", False
    with open(path, "rb") as f:
        return sniff_bytes(f.read(config.HEADER_BYTES), ext, size)


def sniff_bytes(head: bytes, ext: str, size: int) -> tuple[str, str, bool]:
    """Same as sniff(), for a header that was already read."""
    if size == 0:
        return "empty", "empty", False

    found = _sniff_header(head, ext)
    if found:
        kind, category = found
        expected = _EXPECTED_EXT.get(kind)
        mismatch = bool(expected and ext and ext not in expected)
        return kind, category, mismatch

    if looks_like_text(head):
        if ext in config.CODE_EXTENSIONS:
            return "text", "code", False
        if ext in config.DATA_EXTENSIONS:
            return "text", "data", False
        return "text", "document", False

    return "unknown", "other", False
