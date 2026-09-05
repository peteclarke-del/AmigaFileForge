from __future__ import annotations

import mmap
import os
from io import BytesIO
from pathlib import Path
from typing import BinaryIO

from .disk_service import DiskError, DiskService, ImageSession


MAX_HEX_READ = 4096
MAX_HEX_WRITE = 1024 * 1024
MAX_SEARCH_PATTERN = 256
MAX_COMPARE_BYTES = 1024 * 1024 * 1024
MAX_COMPARE_OFFSETS = 100_000
MAX_COMPARE_RANGES = 20_000


def _target_path(session: ImageSession, target: str) -> Path:
    if target == "image":
        return session.path
    if target == "descriptor" and session.descriptor_path is not None:
        return session.descriptor_path
    raise DiskError("That raw image component is not available.")


def _version(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_size:x}-{stat.st_mtime_ns:x}"


def _compare_streams(
    source: BinaryIO,
    source_size: int,
    candidate: BinaryIO,
    candidate_size: int,
    progress=None,
) -> dict:
    limit = min(source_size, candidate_size, MAX_COMPARE_BYTES)
    offset = 0
    count = abs(source_size - candidate_size)
    common_differences = 0
    offsets: list[int] = []
    ranges: list[list[int]] = []
    ranges_truncated = False
    open_start: int | None = None
    open_end = 0
    while offset < limit:
        length = min(1024 * 1024, limit - offset)
        left = source.read(length)
        right = candidate.read(length)
        compared = min(len(left), len(right))
        if not compared:
            break
        if left[:compared] == right[:compared]:
            if open_start is not None:
                if len(ranges) < MAX_COMPARE_RANGES:
                    ranges.append([open_start, open_end])
                else:
                    ranges_truncated = True
                open_start = None
            offset += compared
            if progress:
                progress(offset, limit)
            continue
        for index, (current, other) in enumerate(zip(left[:compared], right[:compared])):
            absolute = offset + index
            if current == other:
                if open_start is not None:
                    if len(ranges) < MAX_COMPARE_RANGES:
                        ranges.append([open_start, open_end])
                    else:
                        ranges_truncated = True
                    open_start = None
                continue
            count += 1
            common_differences += 1
            if len(offsets) < MAX_COMPARE_OFFSETS:
                offsets.append(absolute)
            if open_start is None:
                open_start = absolute
            open_end = absolute
        offset += compared
        if progress:
            progress(offset, limit)
    if open_start is not None and len(ranges) < MAX_COMPARE_RANGES:
        ranges.append([open_start, open_end])
    elif open_start is not None:
        ranges_truncated = True
    if source_size != candidate_size:
        tail = [min(source_size, candidate_size), max(source_size, candidate_size) - 1]
        if len(ranges) < MAX_COMPARE_RANGES:
            ranges.append(tail)
        else:
            ranges_truncated = True
    return {
        "count": count,
        "sourceSize": source_size,
        "candidateSize": candidate_size,
        "compared": offset,
        "differences": offsets,
        "ranges": ranges,
        "navigationTruncated": common_differences > len(offsets),
        "rangesTruncated": ranges_truncated,
        "truncated": limit < min(source_size, candidate_size),
    }


def compare_raw_image(session: ImageSession, candidate: BinaryIO, candidate_size: int, target: str = "image") -> dict:
    path = _target_path(session, target)
    with session.lock, path.open("rb") as source:
        report = _compare_streams(source, path.stat().st_size, candidate, candidate_size)
    report["version"] = _version(path)
    return report


def compare_data(data: bytes, candidate: BinaryIO, candidate_size: int) -> dict:
    return _compare_streams(BytesIO(data), len(data), candidate, candidate_size)


def compare_paths(source_path: Path, candidate_path: Path, progress=None) -> dict:
    """Compare two local image components without loading either into memory."""
    with source_path.open("rb") as source, candidate_path.open("rb") as candidate:
        return _compare_streams(
            source,
            source_path.stat().st_size,
            candidate,
            candidate_path.stat().st_size,
            progress,
        )


def raw_image_range(
    session: ImageSession,
    offset: int,
    length: int,
    target: str = "image",
) -> dict:
    path = _target_path(session, target)
    if length < 1 or length > MAX_HEX_READ:
        raise DiskError(f"Read between 1 and {MAX_HEX_READ:,} bytes at a time.")
    with session.lock:
        size = path.stat().st_size
        offset = max(0, min(int(offset), max(0, size - 1))) if size else 0
        with path.open("rb") as image:
            image.seek(offset)
            data = image.read(min(length, size - offset))
        return {
            "offset": offset,
            "length": len(data),
            "size": size,
            "data": data.hex().upper(),
            "version": _version(path),
            "target": target,
            "targetName": session.descriptor_name if target == "descriptor" else session.name,
            "readOnly": bool(session.hfe_read_only),
        }


def _search_pattern(query: str, mode: str) -> bytes:
    if mode == "hex":
        compact = "".join(str(query or "").split())
        if not compact or len(compact) % 2:
            raise DiskError("Enter complete hexadecimal byte pairs, such as 44 69 73 63.")
        try:
            pattern = bytes.fromhex(compact)
        except ValueError as exc:
            raise DiskError("The hexadecimal search contains an invalid character.") from exc
    elif mode == "text":
        try:
            pattern = str(query or "").encode("latin-1")
        except UnicodeEncodeError as exc:
            raise DiskError("Text searches must use characters available in Latin-1.") from exc
    else:
        raise DiskError("Choose hexadecimal or text search mode.")
    if not pattern:
        raise DiskError("Enter something to search for.")
    if len(pattern) > MAX_SEARCH_PATTERN:
        raise DiskError(f"Search patterns can contain at most {MAX_SEARCH_PATTERN} bytes.")
    return pattern


def search_raw_image(
    session: ImageSession,
    query: str,
    mode: str,
    start: int,
    direction: str,
    wrap: bool,
    target: str = "image",
) -> dict:
    path = _target_path(session, target)
    pattern = _search_pattern(query, mode)
    if direction not in {"forward", "backward"}:
        raise DiskError("Choose forward or backward search.")
    with session.lock, path.open("rb") as image:
        size = path.stat().st_size
        if not size:
            return {"offset": None, "wrapped": False, "version": _version(path)}
        requested_start = int(start)
        with mmap.mmap(image.fileno(), 0, access=mmap.ACCESS_READ) as view:
            if direction == "forward":
                found = -1 if requested_start >= size else view.find(pattern, max(0, requested_start))
                wrapped = found < 0 and wrap
                if wrapped:
                    found = view.find(
                        pattern,
                        0,
                        min(size, max(0, requested_start) + len(pattern) - 1),
                    )
            else:
                found = -1 if requested_start < 0 else view.rfind(
                    pattern, 0, min(size, requested_start + len(pattern))
                )
                wrapped = found < 0 and wrap
                if wrapped:
                    found = view.rfind(pattern, min(size, max(0, requested_start + 1)))
        return {
            "offset": found if found >= 0 else None,
            "wrapped": bool(wrapped and found >= 0),
            "version": _version(path),
        }


def _decode_changes(changes: object, size: int) -> list[tuple[int, bytes]]:
    if not isinstance(changes, list) or not changes:
        raise DiskError("There are no changed bytes to write.")
    decoded: list[tuple[int, bytes]] = []
    total = 0
    for change in changes:
        if not isinstance(change, dict):
            raise DiskError("The raw byte changes are malformed.")
        try:
            offset = int(change.get("offset"))
            compact = "".join(str(change.get("data") or "").split())
            data = bytes.fromhex(compact)
        except (TypeError, ValueError) as exc:
            raise DiskError("A raw byte change has an invalid offset or value.") from exc
        if not data:
            raise DiskError("Raw byte changes cannot be empty.")
        if offset < 0 or offset + len(data) > size:
            raise DiskError("A raw byte change extends beyond the image boundary.")
        total += len(data)
        if total > MAX_HEX_WRITE:
            raise DiskError(f"Write at most {MAX_HEX_WRITE:,} raw bytes in one operation.")
        decoded.append((offset, data))
    decoded.sort(key=lambda item: item[0])
    if any(
        offset < previous_offset + len(previous_data)
        for (previous_offset, previous_data), (offset, _data) in zip(decoded, decoded[1:])
    ):
        raise DiskError("Raw byte changes must not overlap.")
    return decoded


def write_raw_image(
    service: DiskService,
    session: ImageSession,
    expected_version: str,
    changes: object,
    confirmed: bool,
    target: str = "image",
) -> dict:
    if not confirmed:
        raise DiskError("Raw image writes require explicit dangerous-change confirmation.")
    if session.hfe_read_only:
        raise DiskError("This HFE working image is protected because its track data cannot be rewritten safely.")
    path = _target_path(session, target)
    with session.lock:
        size = path.stat().st_size
        if expected_version != _version(path):
            raise DiskError("The image changed after the hex editor loaded it. Reopen the editor before writing.")
        decoded = _decode_changes(changes, size)
        with path.open("r+b", buffering=0) as image:
            for offset, data in decoded:
                image.seek(offset)
                image.write(data)
            image.flush()
            os.fsync(image.fileno())

        session.invalidate_cached_views()
        session.dms = None
        session.dirty = True
        service._append_warning(
            session,
            "Raw bytes were changed with the hex editor. Run the image health checks before using the image on hardware.",
        )
        return {
            "written": sum(len(data) for _offset, data in decoded),
            "version": _version(path),
            "image": service.summary(session),
        }
