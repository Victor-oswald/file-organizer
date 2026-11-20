"""Stage 1a: fingerprints.

partial_hash : size + first 64 KB + last 64 KB  -> very cheap, used as a PRE-FILTER
full_hash    : SHA-256 of the whole file        -> used to CONFIRM duplicates

Two different files can (rarely) share a partial hash; they can never be
declared duplicates unless the full hash also matches.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .. import config


def partial_hash(path: Path, size: int) -> str:
    chunk = config.PARTIAL_HASH_CHUNK
    h = hashlib.sha256()
    h.update(size.to_bytes(8, "little"))          # size is part of the fingerprint
    with open(path, "rb") as f:
        h.update(f.read(chunk))
        if size > chunk:
            # Start the tail read after the head so small files are not hashed twice.
            f.seek(max(chunk, size - chunk))
            h.update(f.read(chunk))
    return h.hexdigest()


def full_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(config.FULL_HASH_BLOCK):
            h.update(block)
    return h.hexdigest()


def fingerprint(path: Path, size: int) -> tuple[str, bytes]:
    """partial_hash AND the first bytes of the file, from a SINGLE open().

    inspect() needs both (the hash for duplicates, the header for magic bytes); reading
    them separately opened every file twice."""
    chunk = config.PARTIAL_HASH_CHUNK
    h = hashlib.sha256()
    h.update(size.to_bytes(8, "little"))
    with open(path, "rb") as f:
        head = f.read(chunk)
        h.update(head)
        if size > chunk:
            f.seek(max(chunk, size - chunk))
            h.update(f.read(chunk))
    return h.hexdigest(), head[:config.HEADER_BYTES]
