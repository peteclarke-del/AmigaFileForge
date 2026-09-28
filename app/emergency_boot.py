"""Starting a machine from the system an AmigaOS release CD carries.

The AmigaOS 3.5 and 3.9 discs each hold a complete system in a drawer called
``Emergency-Boot``. It is what a machine that can boot from CD starts from,
and what the disc's own installer means when it says to "boot from your
Emergency-Disk to make the update or full installation". With it, the
installer's full installation goes onto a drive that has nothing on it at all,
which is the state a newly partitioned card is in.

An emulated A1200 does not boot from its CD drive, so the drawer is copied
onto a small drive of its own and attached beside the one being installed
onto, with a higher boot priority. The machine starts from it, finds the
release CD in the CD drive and the empty drive waiting, and the installer does
the rest. Nothing from the emergency system is put on the target drive: the
installer decides what goes there.

The small drive is a boot medium, as a floppy would be on a real machine, and
is discarded when the emulator closes.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import progress as progress_module
from .errors import DiskError
from .iso9660 import Iso9660Error, Iso9660Image

#: The drawer on the disc that holds the system.
EMERGENCY_DRAWER = "Emergency-Boot"

#: What makes that drawer a system a machine can start from.
EMERGENCY_STARTUP = f"{EMERGENCY_DRAWER}/S/Startup-Sequence"

#: The device and volume the boot drive appears as. The device name is one no
#: partition of a drive being installed onto is likely to have.
BOOT_DEVICE = "EBOOT"
BOOT_VOLUME = "Emergency-Boot"

#: Above any partition of the drive being installed onto, which a new drive
#: gives priority 0, and above the floppy drive's 5.
BOOT_PRIORITY = 20

MIB = 1024 * 1024


def has_emergency_system(image: Iso9660Image) -> bool:
    """Whether this disc carries a system a machine can be started from."""
    try:
        return any(
            not entry.directory and entry.name.casefold() == "startup-sequence"
            for entry in image.list_directory(f"{EMERGENCY_DRAWER}/S")
        )
    except Iso9660Error:
        return False


def _walk(image: Iso9660Image, drawer: str):
    """Yield every entry under a drawer, each drawer before what is in it."""
    for entry in image.list_directory(drawer):
        yield entry
        if entry.directory:
            yield from _walk(image, entry.path)


def without_floppy(script: str) -> str:
    """Take out of a startup script what it expects of a disk in DF0:.

    The disc's startup was written for a machine started from the emergency
    floppy, and adds that floppy's libraries and monitors to its own. With no
    disk in the drive each of those lines stops the boot with a requester
    asking for one. They are commented out, so the script still says what it
    was, and a block that tests for something on the floppy goes with it.
    """
    kept: list[str] = []
    depth = 0
    for line in script.splitlines():
        words = line.split()
        first = words[0].casefold() if words else ""
        if depth:
            if first == "if":
                depth += 1
            elif first == "endif":
                depth -= 1
            kept.append(f"; {line}")
            continue
        if "df0:" in line.casefold() and not line.lstrip().startswith(";"):
            if first == "if":
                depth = 1
            kept.append(f"; {line}")
            continue
        kept.append(line)
    return "\n".join(kept) + "\n"


def _moment(text: str) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if text else None
    except ValueError:
        return None


def build_boot_drive(
    disc: Path | str,
    destination: Path | str,
    progress: progress_module.Progress | None = None,
) -> dict:
    """Write a small drive holding the disc's emergency system.

    No CD mountlist is added to it. The emulator mounts the disc in its CD
    drive as ``CD0:`` itself, before AmigaDOS starts, so the release CD is on
    the Workbench without the system having to know how to reach it.
    """
    try:
        from amiganut.disc.mount import mount_image
        from amiganut.file import AmigaMeta
        from amiganut.filesystem.drive import create_drive
    except ImportError as exc:  # pragma: no cover - packaging failure
        raise DiskError("The Amiganut drive API is unavailable.") from exc

    report = progress_module.reporter(progress)
    destination = Path(destination)
    try:
        image = Iso9660Image(disc)
    except Iso9660Error as exc:
        raise DiskError(str(exc)) from exc
    try:
        if not has_emergency_system(image):
            raise DiskError(
                "This disc carries no Emergency-Boot system, so a machine cannot "
                "be started from it."
            )
        entries = list(_walk(image, EMERGENCY_DRAWER))
        files = [entry for entry in entries if not entry.directory]
        held = sum(entry.length for entry in files)
        # Room for the files, a block of header for each of them, and as much
        # again, because the system writes to its boot volume while it runs.
        size = max(32 * MIB, (held + len(entries) * 1024) * 2)
        size += -size % MIB
        create_drive(
            destination,
            size,
            [{
                "name": BOOT_DEVICE,
                "label": BOOT_VOLUME,
                "filesystem": "ffs-intl",
                "bootable": True,
                "bootPriority": BOOT_PRIORITY,
            }],
        )
        mount, _name = mount_image(destination, writable=True, partition=0, filesystem="rdb")
        try:
            prefix = len(EMERGENCY_DRAWER) + 1
            for position, entry in enumerate(entries):
                inner = entry.path[prefix:]
                if position % 100 == 0:
                    report(f"Preparing the emergency system: {inner}", position, len(entries))
                if entry.directory:
                    mount.mkdir(inner)
                    continue
                meta = AmigaMeta(
                    protection=entry.protection or 0,
                    comment=entry.comment or "",
                    datestamp=_moment(entry.datestamp),
                )
                data = image.read_file(entry.path)
                if inner.casefold() == "s/startup-sequence":
                    data = without_floppy(data.decode("latin-1")).encode("latin-1")
                mount.write_bytes(inner, data, meta)
            problems = mount.validate()
        finally:
            mount.close()
    except DiskError:
        destination.unlink(missing_ok=True)
        raise
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise DiskError(f"The emergency system could not be prepared: {exc}") from exc
    finally:
        image.close()
    if problems:
        destination.unlink(missing_ok=True)
        raise DiskError(
            "The emergency system was copied but its volume did not check clean: "
            + "; ".join(str(problem) for problem in problems[:3])
        )
    report("The emergency system is ready", len(entries), len(entries))
    return {
        "path": str(destination),
        "files": len(files),
        "bytes": held,
        "device": BOOT_DEVICE,
        "volume": BOOT_VOLUME,
    }


__all__ = [
    "BOOT_DEVICE",
    "BOOT_PRIORITY",
    "BOOT_VOLUME",
    "EMERGENCY_DRAWER",
    "build_boot_drive",
    "has_emergency_system",
    "without_floppy",
]
