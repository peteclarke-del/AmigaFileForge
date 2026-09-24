"""Drives attached to the host through USB, opened in place.

A hard drive or memory card taken from an Amiga, or from a PiStorm, is often
easier to work on attached to the Linux machine through a USB adapter than
copied off and back. This module finds such drives and describes them. It
only reads: opening one as a pane, and the rules for writing to it, are the
disk service's.

Only disks behind a USB controller are offered. The machine's own disks never
are, whatever a request asks for, because a request names a drive by an
identifier from the list this module builds and the matching entry is used,
so no text from a request ever reaches the filesystem.

Linux gives whole-disk device nodes to root and the ``disk`` group. The
package ships a udev rule that lets the logged-in desktop user open USB disks;
until it is installed a drive is listed but marked as needing access, with the
reason, rather than silently missing.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

SYS_BLOCK = Path("/sys/block")
BY_ID = Path("/dev/disk/by-id")
PROC_MOUNTS = Path("/proc/mounts")
UDEV_DATA = Path("/run/udev/data")

#: Whole-disk device names that can sit behind a USB adapter. Partitions,
#: loop devices, device-mapper volumes and the like are never offered.
_DISK_NAME = re.compile(r"^(sd[a-z]+|mmcblk\d+)$")

#: Stable names for the same disk, most specific first. The ``usb-`` name is
#: built from the adapter, which often reports a generic serial shared by
#: every drive put in it, so the drive's own identity is preferred.
_ID_PREFERENCE = ("ata-", "scsi-", "nvme-", "mmc-", "wwn-", "usb-")

UDEV_RULE_NAME = "70-amiga-file-forge-usb-drives.rules"


@dataclass(frozen=True)
class AttachedDrive:
    """One USB disk as the pane chooser shows it."""

    id: str
    device: str
    stable_path: str
    model: str
    size: int
    readable: bool
    writable: bool
    read_only_switch: bool
    mounted: list[str] = field(default_factory=list)
    contents: str = ""
    detail: str = ""

    @property
    def amiga(self) -> bool:
        return self.contents.startswith("Amiga")

    def to_dict(self) -> dict:
        row = asdict(self)
        row["stablePath"] = row.pop("stable_path")
        row["readOnlySwitch"] = row.pop("read_only_switch")
        row["amiga"] = self.amiga
        return row


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def _is_usb(name: str, sys_block: Path) -> bool:
    try:
        return "/usb" in os.path.realpath(sys_block / name)
    except OSError:
        return False


def _stable_path(name: str, by_id: Path) -> str | None:
    """Return the most specific /dev/disk/by-id name for a whole disk."""
    try:
        links = [
            link for link in by_id.iterdir()
            if "-part" not in link.name and os.path.realpath(link) == f"/dev/{name}"
        ]
    except OSError:
        return None
    if not links:
        return None

    def rank(link: Path) -> tuple[int, str]:
        for index, prefix in enumerate(_ID_PREFERENCE):
            if link.name.startswith(prefix):
                return index, link.name
        return len(_ID_PREFERENCE), link.name

    return str(sorted(links, key=rank)[0])


def _mounted_partitions(name: str, proc_mounts: Path) -> list[str]:
    """Mount points on the host that belong to this disk or its partitions."""
    found = []
    for line in _read_text(proc_mounts).splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        source = os.path.realpath(parts[0]) if parts[0].startswith("/dev/") else parts[0]
        if re.fullmatch(rf"/dev/{re.escape(name)}(p?\d+)?", source):
            found.append(parts[1].replace("\\040", " "))
    return found


def _model(name: str, sys_block: Path, udev_data: Path) -> str:
    """The drive's own model name, rather than its USB adapter's.

    Through a USB bridge the kernel reports the bridge, such as "JMicron
    Generic". udev asks the drive itself, so its record is preferred.
    """
    numbers = _read_text(sys_block / name / "dev")
    record = _read_text(udev_data / f"b{numbers}") if numbers else ""
    properties = dict(
        line[2:].split("=", 1) for line in record.splitlines() if line.startswith("E:") and "=" in line
    )
    model = properties.get("ID_MODEL", "").replace("_", " ").strip()
    if model:
        return model
    device = sys_block / name / "device"
    vendor = _read_text(device / "vendor")
    model = _read_text(device / "model") or _read_text(device / "name")
    text = " ".join(part for part in (vendor, model) if part and part.lower() != "generic")
    return text or model or name


def describe_contents(device: str) -> str:
    """Say what Amiga structures the start of a drive holds, reading 8 KiB.

    A Rigid Disk Block can sit in any of the first sixteen blocks. Without
    one, a drive can still hold a single volume from its first block, as a
    memory card formatted without a partition table does.
    """
    try:
        with open(device, "rb") as handle:
            head = handle.read(16 * 512)
    except OSError:
        return ""
    for block in range(16):
        if head[block * 512 : block * 512 + 4] == b"RDSK":
            try:
                from amiganut.filesystem import reader_for
                from amiganut.filesystem.rdb import read_rigid_disk

                with reader_for(device) as reader:
                    disk = read_rigid_disk(reader)
                formats = sorted({partition.format for partition in disk.partitions})
                count = len(disk.partitions)
                return (
                    f"Amiga drive, {count} partition{'s' if count != 1 else ''} "
                    f"({', '.join(formats)})"
                )
            except Exception:
                return "Amiga drive with a damaged partition table"
    signature = head[:4]
    if signature[:3] in (b"DOS", b"SFS", b"PFS"):
        from amiganut.filesystem.blocks import DOS_TYPES

        return f"Amiga {DOS_TYPES.get(signature, 'volume')} volume without a partition table"
    return ""


def list_attached_drives(
    *,
    sys_block: Path = SYS_BLOCK,
    by_id: Path = BY_ID,
    proc_mounts: Path = PROC_MOUNTS,
    udev_data: Path = UDEV_DATA,
) -> list[AttachedDrive]:
    """Describe every USB-attached disk with media in it."""
    try:
        names = sorted(entry.name for entry in sys_block.iterdir() if _DISK_NAME.match(entry.name))
    except OSError:
        return []
    drives = []
    for name in names:
        if not _is_usb(name, sys_block):
            continue
        try:
            size = int(_read_text(sys_block / name / "size") or 0) * 512
        except ValueError:
            size = 0
        if not size:
            continue  # A card reader with no card in it.
        stable = _stable_path(name, by_id)
        device = f"/dev/{name}"
        readable = os.access(device, os.R_OK)
        writable = os.access(device, os.W_OK)
        switch = _read_text(sys_block / name / "ro") == "1"
        model = _model(name, sys_block, udev_data)
        if stable:
            # The identity a request uses: stable across replugging, and the
            # same whichever /dev/sdX the kernel hands out this time.
            drive_id = Path(stable).name
        else:
            drive_id = name
        contents = describe_contents(device) if readable else ""
        detail = ""
        if not readable:
            detail = (
                "Linux has not given this account access to the drive. Install "
                f"the {UDEV_RULE_NAME} rule that comes with Amiga File Forge, "
                "then unplug the drive and attach it again."
            )
        drives.append(
            AttachedDrive(
                id=drive_id,
                device=device,
                stable_path=stable or device,
                model=model,
                size=size,
                readable=readable,
                writable=writable and not switch,
                read_only_switch=switch,
                mounted=_mounted_partitions(name, proc_mounts),
                contents=contents,
                detail=detail,
            )
        )
    return drives


def find_attached_drive(identifier: object, **locations) -> AttachedDrive:
    """Return the listed drive a request names, or refuse.

    The identifier is compared with the list built from the system, and the
    matching entry is what the caller then uses, so a request can only ever
    select a drive that is attached through USB right now.
    """
    wanted = str(identifier or "").strip()
    for drive in list_attached_drives(**locations):
        if drive.id == wanted:
            return drive
    raise LookupError(
        "That drive is no longer attached. Attach it again and reopen the list."
    )


__all__ = [
    "AttachedDrive",
    "UDEV_RULE_NAME",
    "describe_contents",
    "find_attached_drive",
    "list_attached_drives",
]
