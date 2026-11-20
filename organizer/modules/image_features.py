"""Stage 2 (images): EXIF, a 64-bit difference hash (dHash), and a
"screenshot or photo?" decision from colour statistics. Needs Pillow only.
"""

from __future__ import annotations

import math
from datetime import datetime
from pathlib import Path

from PIL import Image

from .. import config
from ..models import Features

_EXIF_MAKE, _EXIF_MODEL, _EXIF_DATETIME = 0x010F, 0x0110, 0x0132
_EXIF_IFD, _GPS_IFD, _DATETIME_ORIGINAL = 0x8769, 0x8825, 0x9003


def _parse_exif_date(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip()[:19], "%Y:%m:%d %H:%M:%S")
    except ValueError:
        return None         # e.g. "0000:00:00 00:00:00"


def dhash(img: Image.Image) -> int:
    """64-bit difference hash.

    1. shrink to 9x8 grayscale (throws away detail, keeps structure)
    2. for each of the 8 rows compare 8 neighbouring pixel pairs
    3. bit = 1 if the left pixel is brighter than the right one
    Similar pictures give hashes that differ in only a few bits.
    """
    px = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS).tobytes()
    bits = 0
    for row in range(8):
        for col in range(8):
            left, right = px[row * 9 + col], px[row * 9 + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def _entropy(gray: Image.Image) -> float:
    """Shannon entropy (bits) of the grayscale histogram."""
    hist = gray.histogram()
    total = sum(hist)
    return -sum((c / total) * math.log2(c / total) for c in hist if c)


def _unique_color_ratio(rgb: Image.Image) -> float:
    """Distinct colours in a 64x64 nearest-neighbour sample / 4096 pixels."""
    colors = rgb.resize((64, 64), Image.Resampling.NEAREST).getcolors(maxcolors=4096) or []
    return len(colors) / 4096


WORK_SIZE = 256        # every statistic below is computed from a copy no larger than this


def analyze_image(f: Features) -> None:
    with Image.open(f.record.path) as img:
        f.width, f.height = img.size                      # read BEFORE draft(): draft shrinks img.size
        exif = img.getexif()                              # cheap: reads metadata, decodes no pixels
        make, model = exif.get(_EXIF_MAKE), exif.get(_EXIF_MODEL)
        try:
            ifd = exif.get_ifd(_EXIF_IFD)
            taken = _parse_exif_date(ifd.get(_DATETIME_ORIGINAL)) or _parse_exif_date(exif.get(_EXIF_DATETIME))
            f.has_gps = bool(exif.get_ifd(_GPS_IFD))
        except Exception:
            taken = _parse_exif_date(exif.get(_EXIF_DATETIME))
        if taken:
            f.taken, f.taken_source = taken, "exif"
        f.is_camera = bool(make or model or taken)

        # The speed-up: a JPEG can be decoded at 1/2, 1/4 or 1/8 scale directly (DCT scaling),
        # so a 12-megapixel photo costs about as much as a thumbnail. Then ONE small RGB copy
        # feeds the hash, the entropy and the colour statistics.
        if img.format == "JPEG":
            img.draft("RGB", (WORK_SIZE, WORK_SIZE))
        img.load()
        work = img.convert("RGB")
        work.thumbnail((WORK_SIZE, WORK_SIZE))
        gray = work.convert("L")
        f.dhash = dhash(gray)

        if not f.is_camera:
            ratio = _unique_color_ratio(work)
            entropy = _entropy(gray)
            flat = ratio < config.SCREENSHOT_UNIQUE_RATIO or entropy < config.SCREENSHOT_ENTROPY
            f.is_screenshot = flat
            f.flatness = 1.0 - min(1.0, ratio / config.SCREENSHOT_UNIQUE_RATIO) if flat else 0.0
            f.notes.append(f"unique_color_ratio={ratio:.3f} entropy={entropy:.2f}")
