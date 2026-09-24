"""Copy a drive opened in place onto another attached drive.

This is how a card is duplicated for a second machine, or moved to a new card
before the old one fails. It is the one operation in the workbench that erases
a drive the operator has not opened, so everything about the target is decided
here rather than by the page: the target is chosen from the list of drives
attached through USB right now, never from a path, and it has to be large
enough, not mounted, not write-protected, writable by this account, not the
drive being copied and not open in a pane.

Only the whole-drive shapes are offered. A partition on its own written to the
start of a card would leave a card with no partition table for a machine to
find, which is a hardfile's job, not a drive's.

Unlike an export to a file, every byte is written, zeros included. A file
starts empty, so skipping a run of zeros leaves zeros; a drive starts with
whatever it held before, and skipping would leave that behind inside the new
volumes. Once written, the target's cached pages are dropped and it is read
back from the drive itself and compared with the source, so the result is
checked against what the target actually holds.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from .attached_drives import AttachedDrive
from .drive_export import CHUNK, PROGRESS_EVERY, export_scopes
from .errors import DiskError

#: The shapes a drive can be copied onto another drive in.
CLONE_SCOPES = ("drive", "device")


def clone_scopes(device: str | Path, partitioned: bool) -> list:
    """Return the export shapes that make sense on a whole drive."""
    return [
        scope for scope in export_scopes(device, partitioned)
        if scope.scope in CLONE_SCOPES
    ]


def target_problem(
    target: AttachedDrive,
    source: str | Path,
    needed: int,
    open_devices: set[str],
) -> str:
    """Say why a drive cannot receive the copy, or return an empty string."""
    real = os.path.realpath(target.stable_path)
    if real == os.path.realpath(source):
        return "This is the drive being copied."
    if real in open_devices:
        return "It is open in a pane. Close that pane first."
    if target.mounted:
        return f"Linux has it mounted at {', '.join(target.mounted)}. Unmount it first."
    if target.read_only_switch:
        return "Its write-protect switch is on."
    if not target.readable or not os.access(target.stable_path, os.W_OK):
        return (
            "This account cannot write to it. Install the udev rule that comes "
            "with Amiga File Forge, then attach it again."
        )
    if target.size < needed:
        return "It is too small for this copy."
    return ""


def clone_range(
    source: str | Path,
    target: str | Path,
    length: int,
    progress: Callable[..., None],
) -> None:
    """Write the first ``length`` bytes of ``source`` over ``target``, then verify.

    ``progress`` may raise to stop the copy. A copy stopped part way leaves
    the target incomplete, which is said plainly rather than hidden.
    """
    mib = 1024 * 1024
    total = max(1, -(-length // mib))

    def report(stage: str, done: int) -> None:
        progress(f"{stage} {done / 1e9:.1f} of {length / 1e9:.1f} GB", done // mib, total)

    try:
        with open(source, "rb") as reader:
            descriptor = os.open(target, os.O_WRONLY)
            try:
                _advise(reader.fileno(), length)
                done = reported = 0
                report("Written", 0)
                while done < length:
                    chunk = reader.read(min(CHUNK, length - done))
                    if not chunk:
                        raise DiskError(
                            "The drive being copied ended early. It may have "
                            "been disconnected."
                        )
                    view = memoryview(chunk)
                    while view:
                        view = view[os.write(descriptor, view):]
                    done += len(chunk)
                    if done - reported >= PROGRESS_EVERY:
                        reported = done
                        report("Written", done)
                os.fsync(descriptor)
                # Reading back from the page cache would only prove that
                # memory holds what was written, not that the drive does.
                os.posix_fadvise(descriptor, 0, length, os.POSIX_FADV_DONTNEED)
            finally:
                os.close(descriptor)
        _verify(source, target, length, report)
    except OSError as exc:
        raise DiskError(
            f"The copy failed: {exc.strerror or exc}. The drive being written "
            "is incomplete and should not be used until it is copied again."
        ) from exc
    report("Verified", length)


def _advise(descriptor: int, length: int) -> None:
    try:
        os.posix_fadvise(descriptor, 0, length, os.POSIX_FADV_SEQUENTIAL)
    except (AttributeError, OSError):
        pass


def _verify(source, target, length: int, report: Callable[[str, int], None]) -> None:
    with open(source, "rb") as original, open(target, "rb") as copy:
        _advise(copy.fileno(), length)
        done = reported = 0
        report("Verified", 0)
        while done < length:
            size = min(CHUNK, length - done)
            expected = original.read(size)
            actual = copy.read(size)
            if expected != actual:
                raise DiskError(
                    f"The copy does not match the original {done / 1e9:.2f} GB "
                    "in. The drive being written may be failing, and should not "
                    "be used."
                )
            done += size
            if done - reported >= PROGRESS_EVERY:
                reported = done
                report("Verified", done)


def clone_drive(
    source: str | Path,
    partitioned: bool,
    scope: str,
    target: AttachedDrive,
    open_devices: set[str],
    progress: Callable[..., None],
) -> dict:
    """Copy one whole-drive shape of ``source`` onto ``target`` and verify it."""
    chosen = {entry.scope: entry for entry in clone_scopes(source, partitioned)}.get(str(scope or ""))
    if chosen is None:
        raise DiskError("Choose how much of the drive to copy.")
    problem = target_problem(target, source, chosen.length, open_devices)
    if problem:
        raise DiskError(f"{target.model} cannot receive the copy. {problem}")
    clone_range(source, target.stable_path, chosen.length, progress)
    return {"target": target.model, "device": target.device, "bytes": chosen.length}


__all__ = ["CLONE_SCOPES", "clone_drive", "clone_range", "clone_scopes", "target_problem"]
