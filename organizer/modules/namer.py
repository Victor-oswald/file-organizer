"""Filename synthesis helpers: decide if a name is already "clean", and build
safe names that are valid on Windows, macOS and Linux."""

from __future__ import annotations

import re

from .. import config

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}

_UUID = re.compile(r"[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}", re.I)
_HEX_RUN = re.compile(r"[0-9a-f]{8,}", re.I)
_LONG_DIGITS = re.compile(r"\d{9,}")
_CAMERA_PREFIX = re.compile(r"^(img|vid|dsc[nf]?|pxl|mvimg|pano|burst|mov|pict|wp|snap)[_\- ]?\d", re.I)
_GENERIC = re.compile(r"^(untitled|new|copy( of)?|document|doc|file|image|photo|pic|picture|scan|download|output|temp|tmp|setup|install|installer|track|audio|video|clip|recording|rec|voice|sound|export|data)[\s_\-()\d]*$", re.I)


def is_clean_name(stem: str) -> bool:
    """True if a human plausibly chose this name, so we must PRESERVE it."""
    if _UUID.search(stem) or _CAMERA_PREFIX.match(stem) or _GENERIC.match(stem.strip()):
        return False
    if _LONG_DIGITS.search(stem):
        return False
    if _HEX_RUN.search(stem):
        # A long hex string means machine-generated, unless real words surround it.
        rest = _HEX_RUN.sub(" ", stem)
        words = [w for w in re.findall(r"[^\W\d_]+", rest) if len(w) >= 3]
        if len(words) < 2:
            return False
    letters = re.findall(r"[^\W\d_]+", stem)             # unicode letters, so "résumé" counts
    if not any(len(w) >= 3 for w in letters):
        return False
    alnum = [c for c in stem if c.isalnum()]
    alpha_ratio = sum(c.isalpha() for c in alnum) / len(alnum) if alnum else 0
    return alpha_ratio >= 0.5


def sanitize(name: str, max_len: int = config.MAX_NAME_LEN) -> str:
    """Make a string safe to use as a file name (stem) on every OS."""
    name = _ILLEGAL.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if name.casefold() in _RESERVED:
        name = f"_{name}"
    return name[:max_len].rstrip(" .") or "unnamed"


def title_slug(term: str) -> str:
    """'quarterly report' -> 'Quarterly-Report'"""
    return "-".join(w.capitalize() for w in term.split())
