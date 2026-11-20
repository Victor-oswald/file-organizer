"""Stage 2 (video + audio): parse container formats BY HAND with `struct`.

MP4/MOV  : a tree of "atoms" (size + 4-char type). We read moov > mvhd/trak.
MP3      : ID3v2 / ID3v1 text tags + the first frame header (sample rate, channels)
WAV      : RIFF chunks ('fmt ' and 'data')
"""

from __future__ import annotations

import struct
from datetime import datetime, timedelta
from pathlib import Path

from .. import config
from ..models import Features

# ----------------------------------------------------------------------------
# MP4 / MOV atoms
# ----------------------------------------------------------------------------

_MP4_EPOCH = datetime(1904, 1, 1)


def _atoms(f, start: int, end: int):
    """Yield (type, payload_start, payload_end) for each atom in [start, end)."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        header = f.read(8)
        if len(header) < 8:
            return
        size, kind = struct.unpack(">I4s", header)
        header_len = 8
        if size == 1:                                   # 64-bit "largesize" follows
            size = struct.unpack(">Q", f.read(8))[0]
            header_len = 16
        elif size == 0:                                 # atom runs to end of container
            size = end - pos
        if size < header_len:
            return
        yield kind, pos + header_len, min(pos + size, end)
        pos += size


def _child(f, span: tuple[int, int] | None, name: bytes) -> tuple[int, int] | None:
    if span is None:
        return None
    for kind, s, e in _atoms(f, *span):
        if kind == name:
            return s, e
    return None


def parse_mp4(f: Features) -> None:
    path = f.record.path
    size = f.record.size
    with open(path, "rb") as fh:
        moov = _child(fh, (0, size), b"moov")
        if moov is None:
            f.notes.append("no moov atom (fragmented or truncated mp4)")
            return

        mvhd = _child(fh, moov, b"mvhd")
        if mvhd:
            fh.seek(mvhd[0])
            data = fh.read(32)
            version = data[0]
            if version == 1:
                created, _, timescale, dur = struct.unpack(">QQIQ", data[4:32])
            else:
                created, _, timescale, dur = struct.unpack(">IIII", data[4:20])
            if created:
                f.taken = _MP4_EPOCH + timedelta(seconds=created)
                f.taken_source = "mp4"
            if timescale:
                f.duration = dur / timescale

        for kind, s, e in _atoms(fh, *moov):
            if kind != b"trak":
                continue
            mdia = _child(fh, (s, e), b"mdia")
            hdlr = _child(fh, mdia, b"hdlr")
            if not hdlr:
                continue
            fh.seek(hdlr[0] + 8)
            if fh.read(4) != b"vide":
                continue

            tkhd = _child(fh, (s, e), b"tkhd")
            if tkhd:
                fh.seek(tkhd[0])
                data = fh.read(96)
                off = 88 if data[0] == 1 else 76
                if len(data) >= off + 8:
                    f.width = struct.unpack(">I", data[off:off + 4])[0] >> 16   # 16.16 fixed point
                    f.height = struct.unpack(">I", data[off + 4:off + 8])[0] >> 16

            mdhd = _child(fh, mdia, b"mdhd")
            minf = _child(fh, mdia, b"minf")
            stbl = _child(fh, minf, b"stbl")
            stts = _child(fh, stbl, b"stts")
            if mdhd and stts:
                fh.seek(mdhd[0])
                data = fh.read(24)
                timescale = struct.unpack(">I", data[20:24])[0] if data[0] == 1 else struct.unpack(">I", data[12:16])[0]
                fh.seek(stts[0] + 4)
                (entries,) = struct.unpack(">I", fh.read(4))
                samples = total_delta = 0
                for _ in range(min(entries, 100_000)):
                    count, delta = struct.unpack(">II", fh.read(8))
                    samples += count
                    total_delta += count * delta
                if total_delta:
                    f.fps = round(timescale * samples / total_delta, 2)
            break


# ----------------------------------------------------------------------------
# ID3 tags + MP3 frame header
# ----------------------------------------------------------------------------

def _syncsafe(b: bytes) -> int:
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


def _decode_id3_text(raw: bytes) -> str:
    if not raw:
        return ""
    enc, body = raw[0], raw[1:]
    codec = {0: "latin-1", 1: "utf-16", 2: "utf-16-be", 3: "utf-8"}.get(enc, "latin-1")
    return body.decode(codec, errors="replace").replace("\x00", " ").strip()


_ID3_FRAMES = {"TPE1": "artist", "TP1": "artist", "TIT2": "title", "TT2": "title"}


def parse_id3(path: Path) -> tuple[dict[str, str], int]:
    """Return ({'artist':..,'title':..}, byte offset where audio starts)."""
    tags: dict[str, str] = {}
    audio_start = 0
    with open(path, "rb") as fh:
        head = fh.read(10)
        if head[:3] == b"ID3" and len(head) == 10:
            major = head[3]
            total = _syncsafe(head[6:10])
            audio_start = 10 + total
            body = fh.read(total)
            pos = 0
            if major == 2:                                  # ID3v2.2: 3-char ids, 3-byte sizes
                while pos + 6 <= len(body):
                    fid = body[pos:pos + 3].decode("latin-1")
                    size = int.from_bytes(body[pos + 3:pos + 6], "big")
                    if not fid.strip("\x00") or size == 0:
                        break
                    if fid in _ID3_FRAMES:
                        tags.setdefault(_ID3_FRAMES[fid], _decode_id3_text(body[pos + 6:pos + 6 + size]))
                    pos += 6 + size
            else:                                           # v2.3 / v2.4: 4-char ids
                while pos + 10 <= len(body):
                    fid = body[pos:pos + 4].decode("latin-1")
                    size = _syncsafe(body[pos + 4:pos + 8]) if major == 4 else int.from_bytes(body[pos + 4:pos + 8], "big")
                    if not fid.strip("\x00") or size == 0:
                        break
                    if fid in _ID3_FRAMES:
                        tags.setdefault(_ID3_FRAMES[fid], _decode_id3_text(body[pos + 10:pos + 10 + size]))
                    pos += 10 + size

        if "title" not in tags or "artist" not in tags:     # fall back to ID3v1 (last 128 bytes)
            try:
                fh.seek(-128, 2)
                v1 = fh.read(128)
            except OSError:
                v1 = b""
            if v1[:3] == b"TAG":
                title = v1[3:33].split(b"\x00")[0].decode("latin-1").strip()
                artist = v1[33:63].split(b"\x00")[0].decode("latin-1").strip()
                if title:
                    tags.setdefault("title", title)
                if artist:
                    tags.setdefault("artist", artist)
    return tags, audio_start


_MP3_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def parse_mp3_frame(path: Path, audio_start: int) -> tuple[int | None, int | None]:
    """Find the first frame sync after the tag; return (sample_rate, channels)."""
    with open(path, "rb") as fh:
        fh.seek(audio_start)
        chunk = fh.read(8192)
    for i in range(len(chunk) - 4):
        if chunk[i] == 0xFF and (chunk[i + 1] & 0xE0) == 0xE0:
            version = (chunk[i + 1] >> 3) & 0x3
            layer = (chunk[i + 1] >> 1) & 0x3
            rate_idx = (chunk[i + 2] >> 2) & 0x3
            if version == 1 or layer == 0 or rate_idx == 3:       # reserved values: false sync
                continue
            channels = 1 if ((chunk[i + 3] >> 6) & 0x3) == 3 else 2
            return _MP3_RATES[version][rate_idx], channels
    return None, None


# ----------------------------------------------------------------------------
# WAV
# ----------------------------------------------------------------------------

def parse_wav(path: Path) -> tuple[int | None, int | None, float | None]:
    """Walk RIFF chunks; return (sample_rate, channels, duration_seconds)."""
    rate = channels = byte_rate = data_size = None
    with open(path, "rb") as fh:
        if fh.read(4) != b"RIFF":
            return None, None, None
        fh.read(8)                                             # riff size + 'WAVE'
        while True:
            hdr = fh.read(8)
            if len(hdr) < 8:
                break
            cid, size = struct.unpack("<4sI", hdr)
            if cid == b"fmt ":
                fmt = fh.read(min(size, 16))
                _, channels, rate, byte_rate = struct.unpack("<HHII", fmt[:12])
                fh.seek(size - len(fmt) + (size & 1), 1)
            elif cid == b"data":
                data_size = size
                break
            else:
                fh.seek(size + (size & 1), 1)
    duration = data_size / byte_rate if data_size and byte_rate else None
    return rate, channels, duration


# ----------------------------------------------------------------------------
# audio entry point
# ----------------------------------------------------------------------------

def analyze_audio(f: Features) -> None:
    path, kind = f.record.path, f.magic_kind
    if kind == "mp3":
        tags, start = parse_id3(path)
        f.artist, f.title = tags.get("artist") or None, tags.get("title") or None
        f.sample_rate, f.channels = parse_mp3_frame(path, start)
    elif kind == "wav":
        f.sample_rate, f.channels, f.duration = parse_wav(path)
    elif kind == "m4a":
        parse_mp4(f)                                           # date + duration via mvhd
    if (f.channels == 1 and f.sample_rate and f.sample_rate <= config.VOICE_MEMO_MAX_RATE
            and not f.artist):
        f.is_voice_memo = True
