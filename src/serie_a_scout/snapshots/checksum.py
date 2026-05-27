"""SHA-256 hashing utilities for snapshot integrity verification."""
from __future__ import annotations

import hashlib
from pathlib import Path


CHUNK_SIZE = 1 << 20  # 1 MiB


def sha256_file(path: Path, chunk_size: int = CHUNK_SIZE) -> str:
    """
    Compute SHA-256 of a file streaming `chunk_size` bytes at a time.
    Stable across runs; suitable for manifest integrity verification.
    """
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def sha256_bytes(data: bytes) -> str:
    """SHA-256 of an in-memory byte sequence."""
    return hashlib.sha256(data).hexdigest()
