"""Builders for synthetic test files (so the tests need no real media)."""

from __future__ import annotations

import random
import struct
import wave
import zipfile
from datetime import datetime
from pathlib import Path

from PIL import Image

EPOCH_1904 = datetime(1904, 1, 1)


def photo_like(seed: int, size=(128, 96), base_seed: int | None = None) -> Image.Image:
    """Blotchy low-frequency image + per-pixel noise: high entropy, many colours."""
    rnd = random.Random(base_seed if base_seed is not None else seed)
    blocks = Image.new("RGB", (8, 8))
    blocks.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(64)])
    img = blocks.resize(size, Image.Resampling.BICUBIC)
    noise = random.Random(seed + 1000)
    px = img.load()
    for y in range(size[1]):
        for x in range(size[0]):
            r, g, b = px[x, y]
            n = noise.randint(-8, 8)
            px[x, y] = (max(0, min(255, r + n)), max(0, min(255, g + n)), max(0, min(255, b + n)))
    return img


def flat_screenshot(size=(200, 120)) -> Image.Image:
    img = Image.new("RGB", size, (240, 240, 240))
    for x in range(20, 180):
        for y in range(10, 25):
            img.putpixel((x, y), (30, 60, 200))
    return img


def save_camera_jpeg(path: Path, seed: int, when: str, make: str = "TestCam") -> None:
    exif = Image.Exif()
    exif[0x010F] = make
    exif[0x0110] = "X1"
    exif[0x0132] = when                      # "YYYY:MM:DD HH:MM:SS"
    photo_like(seed).save(path, "JPEG", exif=exif)


def atom(kind: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), kind) + payload


def build_mp4(created: datetime, width=1920, height=1080, fps_samples=300, fps_timescale=30000,
              fps_delta=1000) -> bytes:
    secs = int((created - EPOCH_1904).total_seconds())
    mvhd = atom(b"mvhd", b"\0\0\0\0" + struct.pack(">IIII", secs, secs, 1000, 10_000) + b"\0" * 80)
    tkhd = atom(b"tkhd", b"\0\0\0\0" + b"\0" * 72 + struct.pack(">II", width << 16, height << 16))
    mdhd = atom(b"mdhd", b"\0\0\0\0" + struct.pack(">IIII", secs, secs, fps_timescale, 300_000) + b"\0\0\0\0")
    hdlr = atom(b"hdlr", b"\0\0\0\0" + b"\0\0\0\0" + b"vide" + b"\0" * 12 + b"VideoHandler\0")
    stts = atom(b"stts", b"\0\0\0\0" + struct.pack(">I", 1) + struct.pack(">II", fps_samples, fps_delta))
    mdia = atom(b"mdia", mdhd + hdlr + atom(b"minf", atom(b"stbl", stts)))
    moov = atom(b"moov", mvhd + atom(b"trak", tkhd + mdia))
    ftyp = atom(b"ftyp", b"isom" + b"\0\0\0\0" + b"isomiso2")
    return ftyp + moov + atom(b"mdat", b"\0" * 64)


def _syncsafe(n: int) -> bytes:
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def build_mp3(artist: str | None, title: str | None) -> bytes:
    frames = b""
    for fid, text in (("TPE1", artist), ("TIT2", title)):
        if text:
            body = b"\x03" + text.encode("utf-8")
            frames += fid.encode() + struct.pack(">I", len(body)) + b"\0\0" + body
    tag = b"ID3\x03\x00\x00" + _syncsafe(len(frames)) + frames
    audio = b"\xff\xfb\x90\x00" + b"\0" * 400
    return tag + audio


def build_wav(path: Path, rate=8000, channels=1, seconds=1) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * rate * channels * seconds)


def build_pe(vendor: str, product: str) -> bytes:
    head = bytearray(b"MZ" + b"\0" * 0x7E)
    head[0x3C:0x40] = (0x80).to_bytes(4, "little")
    head += b"PE\0\0" + (0x8664).to_bytes(2, "little") + b"\0" * 100
    def entry(k, v):
        return k.encode("utf-16-le") + b"\0\0" + b"\0\0" + v.encode("utf-16-le") + b"\0\0"
    return bytes(head) + b"\0" * 64 + entry("CompanyName", vendor) + entry("ProductName", product) + b"\0" * 64


def build_zip(path: Path, names: list[str]) -> None:
    with zipfile.ZipFile(path, "w") as z:
        for n in names:
            z.writestr(n, "x")


SALES_TEXT = """
Quarterly sales report. The quarterly sales figures show revenue growth across every region.
Sales forecast for next quarter predicts continued revenue growth, driven by enterprise customers.
The sales team exceeded the quarterly target. Revenue growth in the European region was strongest,
while customer retention improved. Marketing campaign spending increased, and the sales pipeline
expanded. Finance will review the quarterly revenue forecast and sales targets next month.
""" * 3

RECIPE_TEXT = """
Chocolate cake recipe. Mix flour, sugar and cocoa powder, then add eggs and melted butter.
Bake the chocolate cake in a hot oven until the sponge is firm. Let the cake cool before adding
chocolate frosting. This recipe makes a rich chocolate cake perfect for birthday celebrations.
Whisk the eggs with sugar until fluffy, fold in the flour gently, and pour the batter into a pan.
""" * 3

CODE_TEXT = """
# Database connection helper
def open_database_connection(connection_string):
    # Open a database connection and return a connection pool
    connection_pool = create_connection_pool(connection_string)
    return connection_pool

def close_database_connection(connection_pool):
    # Close the database connection pool and release resources
    connection_pool.release_resources()
""" * 3
