"""Streaming checksums for image files and generated archives."""

from __future__ import annotations

import errno
import hashlib
import os
from pathlib import Path
from typing import BinaryIO, Callable


_CHUNK_SIZE = 8 * 1024 * 1024
_ZERO_CHUNK = bytes(_CHUNK_SIZE)


def sha256_bytes(data: bytes) -> str:
    """Return the SHA-256 digest for an in-memory payload."""
    return hashlib.sha256(data).hexdigest()


def sha256_stream(
    source: BinaryIO,
    progress: Callable[[int], None] | None = None,
) -> str:
    """Return the SHA-256 digest for a readable binary stream."""
    digest = hashlib.sha256()
    processed = 0
    for chunk in iter(lambda: source.read(_CHUNK_SIZE), b""):
        digest.update(chunk)
        processed += len(chunk)
        if progress:
            progress(processed)
    return digest.hexdigest()


def sha256_copy(
    source: BinaryIO,
    target: BinaryIO,
    progress: Callable[[int], None] | None = None,
) -> str:
    """Copy a binary stream while returning its SHA-256 digest."""
    digest = hashlib.sha256()
    processed = 0
    for chunk in iter(lambda: source.read(_CHUNK_SIZE), b""):
        target.write(chunk)
        digest.update(chunk)
        processed += len(chunk)
        if progress:
            progress(processed)
    return digest.hexdigest()


def _update_zeros(digest, length: int, advanced: Callable[[int], None] | None = None) -> None:
    while length:
        size = min(length, _CHUNK_SIZE)
        digest.update(_ZERO_CHUNK[:size])
        length -= size
        if advanced:
            advanced(size)


def sha256_path(
    path: Path,
    progress: Callable[[int, int], None] | None = None,
) -> str:
    """Return SHA-256 while avoiding physical reads from sparse zero ranges."""
    digest = hashlib.sha256()
    with path.open("rb") as source:
        size = path.stat().st_size
        position = 0
        processed = 0

        def advanced(length: int) -> None:
            nonlocal processed
            processed += length
            if progress:
                progress(processed, size)

        if progress:
            progress(0, size)
        try:
            while position < size:
                try:
                    data_offset = os.lseek(source.fileno(), position, os.SEEK_DATA)
                except OSError as exc:
                    if exc.errno == errno.ENXIO:
                        _update_zeros(digest, size - position, advanced)
                        position = size
                        break
                    raise
                if data_offset > position:
                    _update_zeros(digest, data_offset - position, advanced)
                hole_offset = min(
                    os.lseek(source.fileno(), data_offset, os.SEEK_HOLE),
                    size,
                )
                source.seek(data_offset)
                remaining = hole_offset - data_offset
                while remaining:
                    chunk = source.read(min(_CHUNK_SIZE, remaining))
                    if not chunk:
                        raise OSError("The image ended while its checksum was calculated.")
                    digest.update(chunk)
                    advanced(len(chunk))
                    remaining -= len(chunk)
                position = hole_offset
        except (AttributeError, OSError):
            source.seek(0)
            digest = hashlib.sha256()
            processed = 0
            for chunk in iter(lambda: source.read(_CHUNK_SIZE), b""):
                digest.update(chunk)
                advanced(len(chunk))
    return digest.hexdigest()
