"""Planning a hard drive before anything is written.

A drive is described by a size and a list of partitions, each with a device
name, a volume name, a filing system and a size. This module turns that
description into exactly what would be written, cylinder by cylinder, and says
what is wrong with it or worth knowing about it, without touching a file or a
card. The same plan drives the editor's preview and the write that follows, so
what the editor showed is what the drive gets.

Sizes are counted in powers of 1024 however they are spelt, which is how
AmigaDOS and HDToolBox count: ``128GB`` here is 128 times 1024 cubed. A card
sold as 128 GB holds 128 thousand million bytes, which is about 119 of those,
so an image meant for a card is sized from the card and not from its label.
"""

from __future__ import annotations

import re

from .errors import DiskError

KIB = 1024
MIB = 1024 * KIB
GIB = 1024 * MIB
TIB = 1024 * GIB

#: The smallest drive worth a partition table.
SMALLEST_DRIVE = 2 * MIB

#: AmigaDOS addresses the blocks of a drive with a 32-bit number.
LARGEST_DRIVE = 2 * TIB

#: The largest FFS partition the editor lays out. The FastFileSystem in a
#: Kickstart 3.1 ROM cannot reach past 4 GB, and the later ones that can are
#: slow to validate and easy to damage at that size. The engine formats
#: larger FFS volumes when asked to; the editor does not ask it to.
LARGEST_FFS_PARTITION = 4 * GIB

#: Where the editor starts recommending another filing system.
COMFORTABLE_FFS_PARTITION = 2 * GIB

#: A partition wholly inside this much of the drive is reachable by the
#: ``scsi.device`` of Kickstart 3.1 and earlier, which counts in bytes with a
#: 32-bit number.
CLASSIC_DEVICE_REACH = 4 * GIB

LARGEST_SFS_PARTITION = 127 * GIB

SIZE = re.compile(r"\s*([0-9]+(?:[.,][0-9]+)?)\s*([kmgt]?)(i?b)?\s*", re.IGNORECASE)

#: The filing systems the editor offers, in the order it lists them.
FILESYSTEMS = [
    {
        "id": "pfs3",
        "label": "Professional File System 3",
        "dosType": "PFS\\3",
        "family": "pfs3",
        "handler": "pfs3",
        "largest": 1600 * GIB,
        "note": (
            "For a machine whose device driver reaches the whole drive: "
            "AmigaOS 3.1.4 or later, a PiStorm, an accelerator with its own "
            "driver, or an emulator."
        ),
    },
    {
        "id": "pds3",
        "label": "Professional File System 3, direct SCSI",
        "dosType": "PDS\\3",
        "family": "pfs3",
        "handler": "pfs3",
        "largest": 1600 * GIB,
        "note": (
            "Talks to the drive directly, which is how a machine with the "
            "Kickstart 3.1 scsi.device reaches a partition past the first 4 GB."
        ),
    },
    {
        "id": "sfs",
        "label": "Smart File System",
        "dosType": "SFS\\0",
        "family": "sfs",
        "handler": "sfs",
        "largest": LARGEST_SFS_PARTITION,
        "note": "Needs the SmartFilesystem handler, which you supply.",
    },
    {
        "id": "ffs-intl",
        "label": "FFS International",
        "dosType": "DOS\\3",
        "family": "ffs",
        "handler": "",
        "largest": LARGEST_FFS_PARTITION,
        "note": "In every Kickstart from 2.0. The usual choice for a system partition.",
    },
    {
        "id": "ffs",
        "label": "FFS",
        "dosType": "DOS\\1",
        "family": "ffs",
        "handler": "",
        "largest": LARGEST_FFS_PARTITION,
        "note": "In every Kickstart from 2.0.",
    },
    {
        "id": "ffs-dc",
        "label": "FFS Directory Cache",
        "dosType": "DOS\\5",
        "family": "ffs",
        "handler": "",
        "largest": LARGEST_FFS_PARTITION,
        "note": "In Kickstart 3.0 and later.",
    },
    {
        "id": "ffs-lnfs",
        "label": "FFS Long Filenames",
        "dosType": "DOS\\7",
        "family": "ffs",
        "handler": "ffs",
        "largest": LARGEST_FFS_PARTITION,
        "note": "Needs the FastFileSystem of AmigaOS 3.1.4 or later.",
    },
    {
        "id": "ofs",
        "label": "OFS",
        "dosType": "DOS\\0",
        "family": "ffs",
        "handler": "",
        "largest": LARGEST_FFS_PARTITION,
        "note": "The original filing system. Slow on a hard drive.",
    },
    {
        "id": "ofs-intl",
        "label": "OFS International",
        "dosType": "DOS\\2",
        "family": "ffs",
        "handler": "",
        "largest": LARGEST_FFS_PARTITION,
        "note": "In every Kickstart from 2.0. Slow on a hard drive.",
    },
]

FILESYSTEM_BY_ID = {entry["id"]: entry for entry in FILESYSTEMS}

#: Starting points for a layout. None is chosen for the user; a size written
#: as a fraction is worked out from the drive, and a partition with no size
#: takes what is left. ``large`` stands for whichever filing system the user
#: picks for the big partitions.
PRESETS = [
    {
        "id": "single",
        "label": "One partition",
        "description": "The whole drive as one volume.",
        "partitions": [
            {"name": "DH0", "label": "System", "filesystem": "large", "bootable": True},
        ],
    },
    {
        "id": "system-work",
        "label": "System and Work",
        "description": "A system partition to boot from, and the rest as a work partition.",
        "partitions": [
            {"name": "DH0", "label": "System", "filesystem": "large", "sizeBytes": 2 * GIB, "bootable": True},
            {"name": "DH1", "label": "Work", "filesystem": "large"},
        ],
    },
    {
        "id": "system-work-games",
        "label": "System, Work, Games and Demos",
        "description": "Four partitions, the way a PiStorm card is usually laid out.",
        "partitions": [
            {"name": "DH0", "label": "System", "filesystem": "large", "sizeBytes": 2 * GIB, "bootable": True},
            {"name": "DH1", "label": "Work", "filesystem": "large", "share": 0.4},
            {"name": "DH2", "label": "Games", "filesystem": "large", "share": 0.4},
            {"name": "DH3", "label": "Demos", "filesystem": "large"},
        ],
    },
    {
        "id": "classic",
        "label": "FFS system and a large work partition",
        "description": (
            "A small FFS partition any Kickstart from 2.0 boots without a "
            "handler, and the rest in the filing system you choose."
        ),
        "partitions": [
            {"name": "DH0", "label": "System", "filesystem": "ffs-intl", "sizeBytes": 1 * GIB, "bootable": True},
            {"name": "DH1", "label": "Work", "filesystem": "large"},
        ],
    },
]

#: What a card of each labelled size can be relied on to hold. Makers count
#: in thousands and keep a little back, and two cards with the same label
#: differ by a few megabytes, so an image is made a little smaller than the
#: label. The rest of a card can be claimed once the image is on it.
CARD_SIZES_GB = (1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1000, 2000)
CARD_MARGIN = 0.97


def card_bytes(labelled_gb: int) -> int:
    """How large an image for a card of this labelled size should be."""
    size = int(labelled_gb * 1000 ** 3 * CARD_MARGIN)
    return min(size - size % MIB, LARGEST_DRIVE - MIB)


def card_sizes() -> list[dict]:
    return [
        {
            "label": f"{labelled // 1000} TB card" if labelled >= 1000 else f"{labelled} GB card",
            "sizeBytes": card_bytes(labelled),
        }
        for labelled in CARD_SIZES_GB
    ]


def parse_size(value, *, what: str = "size") -> int:
    """Read a size such as ``128GB``, ``1.5 GiB``, ``512M`` or a number of bytes."""
    if isinstance(value, bool) or value is None:
        raise DiskError(f"Give a {what}, such as 512MB or 128GB.")
    if isinstance(value, (int, float)):
        if value < 0:
            raise DiskError(f"A {what} cannot be negative.")
        return int(value)
    text = str(value).strip()
    match = SIZE.fullmatch(text)
    if not match:
        raise DiskError(f"“{text}” is not a {what}. Write it as 512MB, 1.5GB or 128GB.")
    number = float(match.group(1).replace(",", "."))
    unit = (match.group(2) or "").lower()
    if not unit and not match.group(3) and "." not in match.group(1) and "," not in match.group(1):
        return int(number)
    return int(number * 1024 ** " kmgt".index(unit or " "))


def human_size(size: int) -> str:
    """Spell a size the way the workbench does elsewhere."""
    value = float(size)
    for unit in ("bytes", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            if unit == "bytes":
                return f"{int(value)} bytes"
            return f"{value:.2f}".rstrip("0").rstrip(".") + f" {unit}"
        value /= 1024
    return f"{size} bytes"


def filesystem(identifier) -> dict:
    entry = FILESYSTEM_BY_ID.get(str(identifier or "").strip().lower())
    if entry is None:
        raise DiskError("Choose a filing system for every partition.")
    return entry


def preset_rows(preset_id: str, large_filesystem: str, drive_bytes: int) -> list[dict]:
    """Expand a preset into editor rows for a drive of a given size."""
    preset = next((item for item in PRESETS if item["id"] == preset_id), None)
    if preset is None:
        raise DiskError("There is no such starting layout.")
    large = filesystem(large_filesystem)
    fixed = sum(int(row.get("sizeBytes") or 0) for row in preset["partitions"])
    remaining = max(0, drive_bytes - fixed)
    rows = []
    for row in preset["partitions"]:
        chosen = large["id"] if row["filesystem"] == "large" else row["filesystem"]
        size = int(row.get("sizeBytes") or 0)
        if row.get("share"):
            size = int(remaining * float(row["share"]))
            size -= size % MIB
        if size and size > drive_bytes // 2 and len(preset["partitions"]) > 1:
            # A small drive cannot spare the usual system partition.
            size = max(MIB, drive_bytes // (2 * len(preset["partitions"])))
            size -= size % MIB
        rows.append({
            "name": row["name"],
            "label": row["label"],
            "filesystem": chosen,
            "sizeBytes": size or None,
            "bootable": bool(row.get("bootable")),
            "bootPriority": 0 if row.get("bootable") else -128,
        })
    return rows


def normalise_rows(rows) -> list[dict]:
    """Check the shape of a request's partitions and give each its defaults."""
    if not isinstance(rows, list) or not rows:
        raise DiskError("A drive needs at least one partition.")
    if len(rows) > 30:
        raise DiskError("A drive can carry at most 30 partitions here.")
    cleaned = []
    seen_names: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise DiskError("Each partition is described by its own set of fields.")
        chosen = filesystem(row.get("filesystem"))
        name = str(row.get("name") or f"DH{index}").strip()
        if not re.fullmatch(r"[A-Za-z0-9_.+-]{1,30}", name):
            raise DiskError(
                f"“{name}” cannot be a device name. Use up to 30 letters and "
                "digits, such as DH0 or Work, with no space, colon or slash."
            )
        if name.lower() in seen_names:
            raise DiskError(f"Two partitions are both called {name}.")
        seen_names.add(name.lower())
        label = str(row.get("label") or name).strip()
        if not label or len(label) > 30 or any(character in label for character in ":/"):
            raise DiskError(
                f"“{label}” cannot be a volume name. Use up to 30 characters, "
                "with no colon or slash."
            )
        try:
            label.encode("latin-1")
        except UnicodeEncodeError as exc:
            raise DiskError(
                f"“{label}” holds a character an Amiga volume name cannot."
            ) from exc
        raw_size = row.get("sizeBytes", row.get("size"))
        size = 0 if raw_size in (None, "", 0, "0", "rest") else parse_size(raw_size, what="partition size")
        try:
            priority = int(row.get("bootPriority") if row.get("bootPriority") not in (None, "") else 0)
        except (TypeError, ValueError) as exc:
            raise DiskError("A boot priority is a number from -128 to 127.") from exc
        if not -128 <= priority <= 127:
            raise DiskError("A boot priority is a number from -128 to 127.")
        cleaned.append({
            "name": name,
            "label": label,
            "filesystem": chosen["id"],
            "sizeBytes": size,
            "bootable": bool(row.get("bootable")),
            "bootPriority": priority,
        })
    return cleaned


def plan(drive_bytes, rows, handlers: list[dict] | None = None) -> dict:
    """Work out what a layout would put on a drive of ``drive_bytes``.

    ``handlers`` is the handler store's description of itself. The result
    carries the partitions as they would be written, what is wrong
    (``errors``, which stop the drive being made) and what is worth knowing
    (``warnings``, which do not).
    """
    from amiganut.errors import ConfigurationError
    from amiganut.filesystem.drive import dos_type_for
    from amiganut.filesystem.rdb import geometry_for_drive, plan_rigid_disk

    from . import filesystem_handlers

    size = parse_size(drive_bytes, what="drive size")
    size -= size % 512
    if size < SMALLEST_DRIVE:
        raise DiskError(f"A partitioned drive needs at least {human_size(SMALLEST_DRIVE)}.")
    if size > LARGEST_DRIVE:
        raise DiskError("A drive can hold at most 2 TB, which is as far as AmigaDOS counts.")
    cleaned = normalise_rows(rows)
    errors: list[str] = []
    warnings: list[str] = []

    entries = [
        {
            "name": row["name"],
            "dosType": dos_type_for(row["filesystem"]),
            "sizeBytes": row["sizeBytes"],
            "bootable": row["bootable"],
            "bootPriority": row["bootPriority"],
        }
        for row in cleaned
    ]
    embedded, missing = filesystem_handlers.engine_handlers(
        [entry["dosType"] for entry in entries]
    )
    heads, sectors = geometry_for_drive(size // 512)
    try:
        disk = plan_rigid_disk(
            size // 512,
            entries,
            heads=heads,
            sectors=sectors,
            handlers=embedded,
            scale_to_fit=False,
        )
    except ConfigurationError as exc:
        raise DiskError(str(exc)) from exc

    planned = []
    for row, partition in zip(cleaned, disk.partitions):
        chosen = FILESYSTEM_BY_ID[row["filesystem"]]
        start = partition.start_block * 512
        end = start + partition.size_bytes
        if partition.size_bytes > chosen["largest"]:
            if chosen["family"] == "ffs":
                errors.append(
                    f"{row['name']} is {human_size(partition.size_bytes)}, and an FFS "
                    f"partition is kept to {human_size(LARGEST_FFS_PARTITION)} here. "
                    "FFS is slow to validate and easy to damage past that, and the "
                    "FastFileSystem in a Kickstart 3.1 ROM cannot reach it at all. "
                    "Use the Professional File System for a partition this size."
                )
            else:
                errors.append(
                    f"{row['name']} is {human_size(partition.size_bytes)}, and the "
                    f"{chosen['label']} stops at {human_size(chosen['largest'])} "
                    "for one partition."
                )
        elif chosen["family"] == "ffs" and partition.size_bytes > COMFORTABLE_FFS_PARTITION:
            warnings.append(
                f"{row['name']} is a large partition for FFS. It works, but a "
                "crash means a long validation before the volume can be written "
                "to again. The Professional File System does not have that cost."
            )
        if chosen["family"] == "ffs" and end > CLASSIC_DEVICE_REACH:
            warnings.append(
                f"{row['name']} lies past the first 4 GB of the drive. The "
                "FastFileSystem and scsi.device of Kickstart 3.1 cannot reach "
                "it; AmigaOS 3.1.4 or later, or a PiStorm, can."
            )
        planned.append({
            **row,
            "dosType": chosen["dosType"],
            "filesystemLabel": chosen["label"],
            "lowCylinder": partition.low_cylinder,
            "highCylinder": partition.high_cylinder,
            "startBytes": start,
            "sizeBytes": partition.size_bytes,
            "requestedBytes": row["sizeBytes"],
            "takesRest": not row["sizeBytes"],
        })

    if any(
        row["filesystem"] == "pfs3" and row["startBytes"] + row["sizeBytes"] > CLASSIC_DEVICE_REACH
        for row in planned
    ):
        warnings.append(
            "A PFS\\3 partition past the first 4 GB needs a device driver that "
            "counts past it: AmigaOS 3.1.4 or later, a PiStorm, an accelerator's "
            "own driver or an emulator. On the scsi.device of Kickstart 3.1, "
            "choose the direct SCSI form, PDS\\3, for those partitions."
        )
    for row in missing:
        message = (
            f"No {row['label']} handler has been supplied, so the machine would "
            f"not be able to mount those partitions. Add {row['name']} under "
            "Filing-system handlers first."
        )
        if row["family"] == "ffs":
            warnings.append(
                "The long-filename FFS needs the FastFileSystem of AmigaOS 3.1.4 "
                "or later. A machine with that Kickstart has it in ROM. For any "
                "other, supply the handler under Filing-system handlers so that "
                "it travels with the drive."
            )
        else:
            errors.append(message)
    bootable = [row for row in planned if row["bootable"]]
    if not bootable:
        warnings.append(
            "No partition is marked bootable, so a machine will not start from "
            "this drive."
        )
    unused = (disk.cylinders - 1 - max(part.high_cylinder for part in disk.partitions)) * disk.cylinder_bytes
    return {
        "driveBytes": size,
        "heads": heads,
        "sectors": sectors,
        "cylinders": disk.cylinders,
        "cylinderBytes": disk.cylinder_bytes,
        "reservedBytes": disk.low_cylinder * disk.cylinder_bytes,
        "unusedBytes": max(0, unused),
        "partitions": planned,
        "handlers": [
            {
                "dosType": handler.dos_type.decode("latin-1"),
                "version": f"{handler.version >> 16}.{handler.version & 0xFFFF}",
                "sizeBytes": len(handler.seglist),
            }
            for handler in embedded
        ],
        "missingHandlers": missing,
        "errors": errors,
        "warnings": warnings,
        "ok": not errors,
    }


def require_plan(drive_bytes, rows) -> dict:
    """Plan a layout and refuse it if anything stops the drive being made."""
    result = plan(drive_bytes, rows)
    if result["errors"]:
        raise DiskError(result["errors"][0])
    return result


def engine_partitions(planned: dict) -> list[dict]:
    """Turn a plan's partitions into what the engine's drive builder takes."""
    return [
        {
            "name": row["name"],
            "label": row["label"],
            "filesystem": row["filesystem"],
            "cylinders": row["highCylinder"] - row["lowCylinder"] + 1,
            "bootable": row["bootable"],
            "bootPriority": row["bootPriority"],
        }
        for row in planned["partitions"]
    ]


def options(drive_bytes: int | None = None) -> dict:
    """Everything the editor needs to draw itself."""
    from . import filesystem_handlers

    return {
        "filesystems": FILESYSTEMS,
        "largeFilesystems": [
            entry["id"] for entry in FILESYSTEMS if entry["family"] != "ffs"
        ],
        "presets": [
            {key: preset[key] for key in ("id", "label", "description")}
            for preset in PRESETS
        ],
        "cardSizes": card_sizes(),
        "handlers": filesystem_handlers.available(),
        "limits": {
            "smallestDrive": SMALLEST_DRIVE,
            "largestDrive": LARGEST_DRIVE,
            "largestFfsPartition": LARGEST_FFS_PARTITION,
            "largestSfsPartition": LARGEST_SFS_PARTITION,
        },
    }


__all__ = [
    "FILESYSTEMS",
    "FILESYSTEM_BY_ID",
    "LARGEST_DRIVE",
    "LARGEST_FFS_PARTITION",
    "PRESETS",
    "SMALLEST_DRIVE",
    "card_bytes",
    "card_sizes",
    "engine_partitions",
    "filesystem",
    "human_size",
    "normalise_rows",
    "options",
    "parse_size",
    "plan",
    "preset_rows",
    "require_plan",
]
