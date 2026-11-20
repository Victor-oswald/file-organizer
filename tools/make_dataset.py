"""Generate a realistic messy folder for benchmarking.

    python tools/make_dataset.py <folder> --files 2000        # about 2,000 files (what you usually want)
    python tools/make_dataset.py <folder> --files 500 --no-photos   # fast: skips the slow 12-megapixel photos
    python tools/make_dataset.py <folder> --scale 0.5         # advanced: multiplier of the default set

NOTE: the old second argument was a SCALE, not a file count. `... 2000` meant 2,000x the default
(190 GB). That is now refused unless you pass --force.
"""
from __future__ import annotations

import argparse
import os
import random
import time
import struct
import sys
import wave
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image  # noqa: E402

from tests import helpers  # noqa: E402


BASE_FILES = 2399            # files at scale 1.0
BASE_MB = 94                 # disk used at scale 1.0 (with photos)
BASE_MB_NO_PHOTOS = 12


def main(dest: Path, scale: float = 1.0, photos: bool = True) -> None:
    rnd = random.Random(42)
    dest.mkdir(parents=True, exist_ok=True)
    started = time.time()
    done = [0]

    def tick(label: str) -> None:
        """Progress line so you can see it is working (and how long each part takes)."""
        print(f"  {label:<34} {time.time() - started:6.1f}s elapsed", flush=True)
    vocab = [f"{a}{b}" for a in ("sol", "ter", "nov", "pra", "mel", "quo", "ver", "lun", "fer", "gal", "hid", "kor")
             for b in ("ing", "ate", "ion", "ard", "ent", "ory", "ial", "ous", "ish", "ive", "ful", "ant", "ize", "ment")]
    topics = [rnd.sample(vocab, 25) for _ in range(30)]
    n = lambda x: max(1, int(x * scale))

    for i in range(n(1500)):                                    # documents, 30 topics
        topic = topics[i % 30]
        words = [rnd.choice(topic) if rnd.random() < .55 else rnd.choice(vocab) for _ in range(rnd.randint(300, 700))]
        ext = rnd.choice([".txt", ".txt", ".md", ".csv", ".py"])
        (dest / f"{rnd.getrandbits(48):012x}{ext}").write_text(" ".join(words))
    for i in range(n(30)):                                      # exact duplicates
        src = rnd.choice([p for p in dest.iterdir() if p.suffix == ".txt"])
        (dest / f"copy of {src.stem}.txt").write_bytes(src.read_bytes())

    tick('documents + duplicates done')
    for i in range(n(120) if photos else 0):                    # 12 MP camera photos
        img = Image.effect_noise((4000, 3000), 20).convert("RGB") if i % 40 == 0 else \
              Image.linear_gradient("L").resize((4000, 3000)).convert("RGB")
        exif = Image.Exif(); exif[0x010F] = "Cam"; exif[0x0110] = "X"; exif[0x0132] = f"2024:05:{1 + i % 28:02d} 1{i % 10}:00:00"
        img.save(dest / f"IMG_{i:04d}.jpg", "JPEG", quality=85, exif=exif)
    if photos:
        tick('12-megapixel photos done')
    for i in range(n(120) if photos else 0):                    # unlabeled 2 MP photos (similar groups of 4)
        helpers.photo_like(i, size=(1600, 1200), base_seed=i // 4).save(dest / f"p{i}.jpg", quality=85)
    if photos:
        tick('2-megapixel photos done')
    for i in range(n(150) if photos else 0):
        helpers.flat_screenshot((1920, 1080)).save(dest / f"tmp_{rnd.getrandbits(40):010x}.png")

    for i in range(n(100)):
        (dest / f"track{i:03d}.mp3").write_bytes(helpers.build_mp3(f"Artist {i % 9}", f"Song {i}"))
    for i in range(n(50)):
        helpers.build_wav(dest / f"REC_{i:04d}.wav", rate=8000, channels=1, seconds=1)
    for i in range(n(60)):
        helpers.build_zip(dest / f"dl_{i:05d}.zip", [f"proj{i % 7}/{w}.docx" for w in rnd.sample(vocab, 8)])
    for i in range(n(30)):
        (dest / f"VID_{i:04d}.mp4").write_bytes(helpers.build_mp4(datetime(2024, 5, 1 + i % 28, 10, i % 60, 0)))
    for i in range(n(40)):
        (dest / f"app{i}.exe").write_bytes(helpers.build_pe("Vendor", f"Product {i}"))
    (dest / "docs").mkdir(exist_ok=True)
    for i in range(n(200)):
        (dest / "docs" / f"{rnd.getrandbits(40):010x}.txt").write_text(" ".join(rnd.choice(topics[i % 30]) for _ in range(400)))
    print(f"{sum(1 for _ in dest.rglob('*') if _.is_file())} files, {sum(p.stat().st_size for p in dest.rglob('*') if p.is_file()) / 1e6:.0f} MB in {dest}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Generate a messy benchmark folder.")
    ap.add_argument("folder", type=Path)
    ap.add_argument("--files", type=int, help="approximate number of files to create")
    ap.add_argument("--scale", type=float, help="advanced: multiplier of the default set (1.0 = ~2,400 files)")
    ap.add_argument("--no-photos", action="store_true", help="skip photos and screenshots (much faster)")
    ap.add_argument("--force", action="store_true", help="allow very large datasets")
    # backwards compatible: `make_dataset.py <folder> 0.5`
    ap.add_argument("legacy_scale", nargs="?", type=float, help=argparse.SUPPRESS)
    a = ap.parse_args()

    scale = a.scale if a.scale is not None else a.legacy_scale if a.legacy_scale is not None else 1.0
    if a.files:
        scale = a.files / BASE_FILES
    photos = not a.no_photos
    files = int(BASE_FILES * scale)
    mb = int((BASE_MB if photos else BASE_MB_NO_PHOTOS) * scale)
    print(f"Plan: about {files:,} files, about {mb:,} MB on disk, in {a.folder}")
    if (files > 10_000 or mb > 1_500) and not a.force:
        sys.exit(f"Refusing: that is a very large dataset ({files:,} files, ~{mb:,} MB).\n"
                 f"Did you mean --files {int(scale) if scale >= 1 else files}?  (the second number is a SCALE, "
                 f"not a file count)\nPass --force if you really want this.")
    main(a.folder, scale, photos)
