"""Copy a drive opened in place out to an image file on the host.

A drive attached through USB is worked on where it is, but a copy of it is
still worth having: to keep before changing it, to hand to an emulator, or to
work on at leisure after the card goes back into the machine. The ordinary
export builds its result inside the working folder and hands it to the page,
which is sound for a floppy and impossible for a drive of a hundred gigabytes.
This copies straight from the drive to the file the operator chose instead,
a piece at a time, so memory use stays small whatever the size of the drive.

Three shapes are offered. The drive as its partition table describes it is
what an emulator mounts as a hard drive, and it is usually much less than the
whole device, because a card is often larger than the drive it was prepared
for. Every byte of the device is there for a complete archive. One partition
on its own becomes a hardfile, with the geometry its partition block declared
written beside it as a ``.geo``, since that description has nowhere else to
live once the partition table is gone.

Runs of zero bytes are skipped rather than written, so the file takes only
the space the drive's contents need on a filing system with sparse files. The
copy goes to a ``.part`` file that is renamed into place only once it is
complete, so an interrupted export never leaves a file that looks finished.
"""

from __future__ import annotations

import errno
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .errors import DiskError

#: Bytes read from the drive at a time.
CHUNK = 4 * 1024 * 1024
#: How often, in bytes, progress is reported and cancellation noticed.
PROGRESS_EVERY = 64 * 1024 * 1024
_ZEROS = bytes(CHUNK)

#: Places no export may be written, whatever the operator types.
_SYSTEM_TREES = (Path("/dev"), Path("/proc"), Path("/sys"), Path("/run/udev"))


@dataclass(frozen=True)
class ExportScope:
    """One shape the drive can be exported in."""

    scope: str
    label: str
    offset: int
    length: int
    suffix: str
    geometry: str | None = None

    def to_dict(self) -> dict:
        return {
            "scope": self.scope,
            "label": self.label,
            "bytes": self.length,
            "suffix": self.suffix,
            "sidecar": self.geometry is not None,
        }


def device_size(device: str | Path) -> int:
    """Return the size of a drive or image file, reading nothing from it."""
    with open(device, "rb") as handle:
        return handle.seek(0, os.SEEK_END)


def export_scopes(device: str | Path, partitioned: bool) -> list[ExportScope]:
    """List the shapes a drive can be exported in, largest useful one first."""
    total = device_size(device)
    if not partitioned:
        return [ExportScope("drive", "The whole drive, holding one volume", 0, total, ".hdf")]

    from amiganut.filesystem import reader_for
    from amiganut.filesystem.rdb import read_rigid_disk

    from .hardfile_geometry import format_geometry

    with reader_for(device) as reader:
        disk = read_rigid_disk(reader)
    declared = disk.cylinders * disk.blocks_per_cylinder * disk.block_size
    ends = [declared] + [
        partition.start_block * partition.block_size + partition.size_bytes
        for partition in disk.partitions
    ]
    described = min(total, max(ends))
    scopes = [ExportScope(
        "drive",
        "The drive as its partition table describes it",
        0,
        described,
        ".hdf",
    )]
    if total > described:
        scopes.append(ExportScope(
            "device",
            "Every byte of the device, including space no partition uses",
            0,
            total,
            ".hdf",
        ))
    for index, partition in enumerate(disk.partitions):
        name = partition.name or f"DH{index}"
        offset = partition.start_block * partition.block_size
        if offset >= total:
            continue
        scopes.append(ExportScope(
            f"partition:{index}",
            f"Partition {name} alone, as a hardfile with its .geo",
            offset,
            min(partition.size_bytes, total - offset),
            ".hdf",
            format_geometry(
                surfaces=partition.surfaces,
                blocks_per_track=partition.blocks_per_track,
                cylinders=partition.high_cylinder - partition.low_cylinder + 1,
                block_size=partition.block_size,
            ),
        ))
    return scopes


def check_destination(destination: str | Path, device: str | Path) -> Path:
    """Refuse a destination that is not an ordinary new or existing file.

    The path comes from the page, so it is checked here rather than trusted:
    it must be absolute, in a folder that exists, not a folder itself, not a
    device or system file, and never the drive being copied.
    """
    text = str(destination or "").strip()
    if not text:
        raise DiskError("Choose where the image file should be saved.")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise DiskError("Give the full path of the file to save, starting with /.")
    parent = path.parent.resolve()
    target = parent / path.name
    if any(target == tree or tree in target.parents for tree in _SYSTEM_TREES):
        raise DiskError("An image cannot be saved among the system's device files.")
    if not parent.is_dir():
        raise DiskError(f"The folder {parent} does not exist.")
    if target.is_dir():
        raise DiskError(f"{target} is a folder. Give a name for the image file inside it.")
    if target.exists() and not target.is_file():
        raise DiskError(f"{target} is not an ordinary file, so it cannot be replaced.")
    if target.exists() and os.path.realpath(target) == os.path.realpath(device):
        raise DiskError("The image cannot be written over the drive it is copied from.")
    if not os.access(parent, os.W_OK):
        raise DiskError(f"This account cannot write to {parent}.")
    return target


def copy_range(
    device: str | Path,
    destination: Path,
    offset: int,
    length: int,
    progress: Callable[..., None],
) -> int:
    """Copy ``length`` bytes from ``offset`` into a new file, sparsely.

    Returns the number of bytes actually written, which is less than the
    length by however much of the range was zero. ``progress`` may raise to
    cancel the copy; the partial file is removed and the error passed on.
    """
    partial = destination.with_name(destination.name + ".part")
    written = 0
    try:
        with open(device, "rb") as source, open(partial, "wb") as target:
            try:
                os.posix_fadvise(source.fileno(), offset, length, os.POSIX_FADV_SEQUENTIAL)
            except (AttributeError, OSError):
                pass
            source.seek(offset)
            copied = 0
            reported = 0
            _report(progress, 0, length)
            while copied < length:
                chunk = source.read(min(CHUNK, length - copied))
                if not chunk:
                    raise DiskError(
                        "The drive ended before the copy was complete. It may "
                        "have been disconnected."
                    )
                if _all_zero(chunk):
                    target.seek(len(chunk), os.SEEK_CUR)
                else:
                    target.write(chunk)
                    written += len(chunk)
                copied += len(chunk)
                if copied - reported >= PROGRESS_EVERY:
                    reported = copied
                    _report(progress, copied, length)
            # A range that ends in zeros was skipped over, not written, so
            # the file has to be told its full length.
            target.truncate(length)
            target.flush()
            os.fsync(target.fileno())
        partial.replace(destination)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        if exc.errno == errno.ENOSPC:
            raise DiskError(
                f"The disk holding {destination.parent} ran out of space. "
                "Nothing was saved."
            ) from exc
        raise DiskError(f"The image could not be saved: {exc.strerror or exc}.") from exc
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    _report(progress, length, length)
    return written


def _all_zero(chunk: bytes) -> bool:
    # A full chunk is compared with the zero buffer, which stops at the first
    # byte that differs; only the short final chunk is counted.
    if len(chunk) == CHUNK:
        return chunk == _ZEROS
    return chunk.count(0) == len(chunk)


def _report(progress: Callable[..., None], copied: int, length: int) -> None:
    """Report progress in whole MiB, which is what the pane shows as a count."""
    mib = 1024 * 1024
    progress(
        f"Copied {copied / 1e9:.1f} of {length / 1e9:.1f} GB",
        copied // mib,
        max(1, -(-length // mib)),
    )


def export_drive(
    device: str | Path,
    partitioned: bool,
    scope: str,
    destination: str | Path,
    progress: Callable[..., None],
) -> dict:
    """Copy one shape of the drive to ``destination`` and say what was made."""
    choices = {entry.scope: entry for entry in export_scopes(device, partitioned)}
    chosen = choices.get(str(scope or ""))
    if chosen is None:
        raise DiskError("Choose which part of the drive to export.")
    target = check_destination(destination, device)
    written = copy_range(device, target, chosen.offset, chosen.length, progress)
    sidecar = None
    if chosen.geometry is not None:
        sidecar = target.with_suffix(".geo")
        sidecar.write_text(chosen.geometry, encoding="ascii")
    return {
        "path": str(target),
        "bytes": chosen.length,
        "written": written,
        "geometry": str(sidecar) if sidecar else None,
    }


__all__ = [
    "ExportScope",
    "check_destination",
    "copy_range",
    "device_size",
    "export_drive",
    "export_scopes",
]
