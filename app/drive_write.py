"""Putting a drive image onto a card or drive attached through USB.

A drive built here is a sparse file: an image of a 128 GB card that holds a
Workbench install occupies a few hundred megabytes, and the rest of it is
space no partition has written to yet. Writing such an image to a card block
by block would spend most of an hour writing zeros that change nothing about
what the Amiga sees, so by default only what the image actually holds is
written, and then read back from the card and compared.

Two places are always written in full, whatever the image holds there. The
first mebibyte carries the partition table, and the card's own last mebibyte
is where a GPT keeps its second copy. Clearing both is what stops the host
finding the card's previous partitions again and offering to mount them.

Writing every byte remains a choice, for a card that is to hold nothing of
what it held before.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from .attached_drives import AttachedDrive
from .drive_clone import target_problem
from .errors import DiskError

CHUNK = 4 * 1024 * 1024
EDGE = 1024 * 1024


def image_extents(path: str | Path, length: int) -> list[tuple[int, int]]:
    """Return the runs of an image that hold data, as offset and length."""
    from amiganut.filesystem.drive import data_extents

    return data_extents(path, length)


def _with_edges(extents: list[tuple[int, int]], length: int) -> list[tuple[int, int]]:
    """Add the first mebibyte of the image to what is written, and merge."""
    runs = sorted(extents + [(0, min(EDGE, length))])
    merged: list[list[int]] = []
    for start, size in runs:
        # Runs are widened to whole 4 KiB pages so every write to the card
        # starts and ends on a block boundary.
        begin = start - start % 4096
        end = min(length, -(-(start + size) // 4096) * 4096)
        if merged and begin <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([begin, end])
    return [(begin, end - begin) for begin, end in merged]


def write_plan(source: str | Path, every_byte: bool = False) -> dict:
    """Say how much of an image would be written to a card."""
    length = os.path.getsize(source)
    extents = [(0, length)] if every_byte else _with_edges(image_extents(source, length), length)
    return {
        "imageBytes": length,
        "writtenBytes": sum(size for _start, size in extents),
        "extents": extents,
    }


def write_image(
    source: str | Path,
    target: AttachedDrive,
    open_devices: set[str],
    progress: Callable[..., None],
    *,
    every_byte: bool = False,
) -> dict:
    """Write an image to a drive, verify it, and say what was done.

    ``progress`` may raise to stop the write. A write stopped part way leaves
    the drive incomplete, which is said plainly rather than hidden.
    """
    plan = write_plan(source, every_byte)
    length = plan["imageBytes"]
    problem = target_problem(target, source, length, open_devices)
    if problem:
        raise DiskError(f"{target.model} cannot take the image. {problem}")
    extents = plan["extents"]
    total = max(1, plan["writtenBytes"])
    mib = 1024 * 1024

    def report(stage: str, done: int) -> None:
        progress(
            f"{stage} {done / 1e9:.2f} of {total / 1e9:.2f} GB",
            done // mib,
            max(1, -(-total // mib)),
        )

    try:
        with open(source, "rb") as reader:
            descriptor = os.open(target.stable_path, os.O_WRONLY)
            try:
                done = 0
                report("Written", 0)
                if not every_byte and target.size >= length + EDGE:
                    os.pwrite(descriptor, b"\0" * EDGE, target.size - EDGE)
                for start, size in extents:
                    position = 0
                    while position < size:
                        reader.seek(start + position)
                        chunk = reader.read(min(CHUNK, size - position))
                        if not chunk:
                            raise DiskError("The image ended early. It may have been changed during the write.")
                        view = memoryview(chunk)
                        offset = start + position
                        while view:
                            written = os.pwrite(descriptor, view, offset)
                            view = view[written:]
                            offset += written
                        position += len(chunk)
                        done += len(chunk)
                        report("Written", done)
                os.fsync(descriptor)
                # Reading back from the page cache would only prove that
                # memory holds what was written, not that the drive does.
                try:
                    os.posix_fadvise(descriptor, 0, length, os.POSIX_FADV_DONTNEED)
                except (OSError, AttributeError):
                    pass
            finally:
                os.close(descriptor)
            with open(target.stable_path, "rb") as written_back:
                done = 0
                for start, size in extents:
                    position = 0
                    while position < size:
                        amount = min(CHUNK, size - position)
                        reader.seek(start + position)
                        written_back.seek(start + position)
                        if reader.read(amount) != written_back.read(amount):
                            raise DiskError(
                                "The drive does not hold what was written to it, "
                                f"{(start + position) / 1e9:.2f} GB in. The drive "
                                "or its adapter is faulty, and the drive should "
                                "not be used until it has been written again."
                            )
                        position += amount
                        done += amount
                        report("Verified", done)
    except OSError as exc:
        raise DiskError(
            f"The write failed: {exc.strerror or exc}. The drive is incomplete "
            "and should not be used until it is written again."
        ) from exc
    return {
        "target": target.model,
        "device": target.device,
        "imageBytes": length,
        "writtenBytes": plan["writtenBytes"],
        "unusedBytes": max(0, target.size - length),
        "everyByte": bool(every_byte),
    }


__all__ = ["image_extents", "write_image", "write_plan"]
