"""Stage 2 (archives + binaries): read directory listings and executable headers."""

from __future__ import annotations

import mmap
import tarfile
import zipfile
from pathlib import Path

from ..models import Features
from .text_features import archive_member_text, tokenize

MAX_MEMBERS = 2000


def list_archive(path: Path, kind: str) -> list[str]:
    """Member names WITHOUT extracting anything (zip: central directory only)."""
    names: list[str] = []
    if kind == "zip":
        with zipfile.ZipFile(path) as z:
            names = z.namelist()[:MAX_MEMBERS]
    elif kind in ("tar", "gzip"):
        try:
            with tarfile.open(path) as t:
                while len(names) < MAX_MEMBERS and (m := t.next()) is not None:
                    names.append(m.name)
        except (tarfile.TarError, EOFError, OSError):
            pass                                              # a plain .gz is not a tar
    return names


def analyze_archive(f: Features) -> None:
    names = list_archive(f.record.path, f.magic_kind)
    if names:
        f.tokens = tokenize(archive_member_text(names))
        f.notes.append(f"{len(names)} members listed")
    else:
        f.notes.append(f"no listing available for kind={f.magic_kind}")


# ----------------------------------------------------------------------------
# PE (Windows .exe/.dll) and ELF (Linux) headers
# ----------------------------------------------------------------------------

_ELF_MACHINES = {3: "x86", 62: "x86-64", 40: "ARM", 183: "AArch64", 243: "RISC-V"}
_PE_MACHINES = {0x14C: "x86", 0x8664: "x86-64", 0x1C0: "ARM", 0xAA64: "ARM64"}


def _utf16_string_after(buf, key: str) -> str | None:
    """Find a UTF-16LE key (e.g. 'ProductName') in a PE's version resource
    and return the UTF-16LE string that follows it."""
    needle = key.encode("utf-16-le") + b"\x00\x00"
    idx = buf.find(needle)
    if idx < 0:
        return None
    data = bytes(buf[idx + len(needle): idx + len(needle) + 600])
    while data[:2] == b"\x00\x00":                            # skip alignment padding
        data = data[2:]
    end = 0
    while end + 1 < len(data) and data[end:end + 2] != b"\x00\x00":
        end += 2
    value = data[:end].decode("utf-16-le", errors="ignore").strip()
    return value or None


def analyze_binary(f: Features) -> None:
    path = f.record.path
    with open(path, "rb") as fh:
        head = fh.read(4096)
    if f.magic_kind == "elf":
        bits = {1: "32-bit", 2: "64-bit"}.get(head[4], "?")
        little = head[5] == 1
        machine = int.from_bytes(head[18:20], "little" if little else "big")
        f.notes.append(f"ELF {bits} {_ELF_MACHINES.get(machine, machine)}")
    elif f.magic_kind == "pe":
        e_lfanew = int.from_bytes(head[0x3C:0x40], "little")
        if e_lfanew + 6 <= len(head) and head[e_lfanew:e_lfanew + 4] == b"PE\x00\x00":
            machine = int.from_bytes(head[e_lfanew + 4:e_lfanew + 6], "little")
            f.notes.append(f"PE {_PE_MACHINES.get(machine, hex(machine))}")
        if f.record.size > 0:
            with open(path, "rb") as fh, mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ) as mm:
                f.vendor = _utf16_string_after(mm, "CompanyName")
                f.product = _utf16_string_after(mm, "ProductName")
