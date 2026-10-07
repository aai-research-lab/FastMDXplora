"""A study's files as a data repository takes them, each with its SHA-256.

A checksum says the file read is the file written: a reader of a deposit
checks every file against it with `sha256sum -c SHA256SUMS`, and a
methods section can name the trajectory it analysed by it.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

__all__ = ["sha256_of"]

_SUMS: dict[tuple[str, int, int], str] = {}
_SUMS_GUARD = threading.Lock()


def sha256_of(path: Path | str) -> str:
    """A file's SHA-256, read a megabyte at a time, once a version of it."""
    path = Path(path)
    info = path.stat()
    key = (str(path.resolve()), info.st_mtime_ns, info.st_size)
    with _SUMS_GUARD:
        if key in _SUMS:
            return _SUMS[key]
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for piece in iter(lambda: handle.read(1 << 20), b""):
            digest.update(piece)
    said = digest.hexdigest()
    with _SUMS_GUARD:
        _SUMS[key] = said
    return said
