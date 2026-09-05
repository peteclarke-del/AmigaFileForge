"""Geometry for an RDB-less hardfile, and the checks a real machine applies.

A ``.hdf`` that carries a Rigid Disk Block describes itself. A *hardfile* does
not: it is a bare volume, and the host has to be told how many surfaces and
sectors to pretend it has before AmigaDOS will mount it. Emulators keep that
in a ``.geo`` sidecar of ``key=value`` lines, and Amiga File Forge reads and
writes the same file so an image prepared for FS-UAE opens here without being
described twice.

Everything here is a pure function of bytes on disk. Nothing in this module
opens a session, so the geometry rules can be tested without the workbench.
"""

from __future__ import annotations

from pathlib import Path

BLOCK_SIZE = 512

#: A hardfile's first two blocks are its boot block, exactly as on a floppy.
RESERVED_BLOCKS = 2

#: AmigaDOS addresses blocks with a 32-bit number, and ``scsi.device`` limits
#: a single partition to 2 TiB. The workbench caps a hardfile well below that,
#: at the 4 GiB point where 32-bit byte offsets stop being safe on every host.
MAX_BLOCKS = 0x7FFFFF
MAX_SIZE = MAX_BLOCKS * BLOCK_SIZE

#: Offsets inside a root block, measured back from its end.
ROOT_BITMAP_FLAG = 200
ROOT_BITMAP_PAGES = 196
ROOT_NAME = 80
ROOT_SECONDARY_TYPE = 4

#: The root block's own field positions.
ROOT_TYPE_OFFSET = 0
ROOT_HASH_SIZE_OFFSET = 12
ROOT_CHECKSUM_OFFSET = 20
ROOT_HASH_TABLE_OFFSET = 24

T_HEADER = 2
ST_ROOT = 1

#: A hash table on a 512-byte volume holds 72 entries.
DIRECTORY_HASH_ENTRIES = BLOCK_SIZE // 4 - 56

#: Kept under their historical names because the workbench's compatibility
#: checks refer to them by position rather than by meaning.
OLD_ROOT_OFFSET = RESERVED_BLOCKS * BLOCK_SIZE
OLD_DIRECTORY_TAIL = BLOCK_SIZE - ROOT_NAME
OLD_DIRECTORY_SIZE = BLOCK_SIZE
OLD_DIRECTORY_ENTRY_OFFSET = ROOT_HASH_TABLE_OFFSET
OLD_DIRECTORY_ENTRY_SIZE = 4
OLD_DIRECTORY_MAX_ENTRIES = DIRECTORY_HASH_ENTRIES
SECTOR_SIZE = BLOCK_SIZE
SECTORS_PER_TRACK = 32
MAX_SECTORS = MAX_BLOCKS


def parse_geometry(text: str) -> dict:
    """Parse a ``.geo`` sidecar into the fields that decide a hardfile's size."""
    values: dict[str, int] = {}
    aliases = {
        "surfaces": "surfaces",
        "heads": "surfaces",
        "blockspertrack": "blocks_per_track",
        "sectorspertrack": "blocks_per_track",
        "sectors": "blocks_per_track",
        "reserved": "reserved",
        "blocksize": "block_size",
        "sectorsize": "block_size",
        "cylinders": "cylinders",
        "lowcyl": "low_cylinder",
        "highcyl": "high_cylinder",
    }
    for line in str(text).splitlines():
        line = line.split("#", 1)[0].split(";", 1)[0].strip()
        if "=" not in line:
            continue
        key, _, raw = line.partition("=")
        field = aliases.get(key.strip().lower().replace("_", "").replace(" ", ""))
        if field is None:
            continue
        try:
            values[field] = int(raw.strip(), 0)
        except ValueError:
            continue
    return values


def format_geometry(
    *,
    surfaces: int,
    blocks_per_track: int,
    cylinders: int,
    block_size: int = BLOCK_SIZE,
    reserved: int = RESERVED_BLOCKS,
) -> str:
    """Write the ``.geo`` sidecar a bare hardfile needs beside it.

    A hardfile carries no partition table, so nothing inside the file says how
    the host should divide it into cylinders, heads and sectors. That is what
    this describes, and the three values must multiply back to exactly the
    file's size or an emulator refuses the pair rather than guessing.
    """
    return (
        "# Geometry for the hardfile of the same name.\n"
        f"surfaces = {int(surfaces)}\n"
        f"blockspertrack = {int(blocks_per_track)}\n"
        f"reserved = {int(reserved)}\n"
        f"blocksize = {int(block_size)}\n"
        f"cylinders = {int(cylinders)}\n"
    )


def descriptor_size(descriptor_path: Path) -> int | None:
    """Return the device capacity a hardfile's geometry sidecar declares."""
    try:
        values = parse_geometry(descriptor_path.read_text(encoding="latin-1"))
    except OSError:
        return None
    surfaces = values.get("surfaces")
    blocks_per_track = values.get("blocks_per_track")
    block_size = values.get("block_size", BLOCK_SIZE)
    if not surfaces or not blocks_per_track or not block_size:
        return None
    cylinders = values.get("cylinders")
    if cylinders is None:
        low = values.get("low_cylinder", 0)
        high = values.get("high_cylinder")
        if high is None:
            return None
        cylinders = high - low + 1
    if cylinders <= 0:
        return None
    return cylinders * surfaces * blocks_per_track * block_size


def volume_extent(image_path: Path) -> int | None:
    """Return the volume extent implied by a hardfile's own root block.

    A volume's root block sits at the midpoint of its block count, so finding
    it is the same as measuring the volume. Scanning for it is what lets the
    workbench tell a correctly sized hardfile from one that was padded or
    truncated in transit.
    """
    try:
        size = image_path.stat().st_size
        with image_path.open("rb") as image:
            total_blocks = size // BLOCK_SIZE
            if total_blocks <= RESERVED_BLOCKS:
                return None
            for candidate in _root_candidates(total_blocks):
                image.seek(candidate * BLOCK_SIZE)
                block = image.read(BLOCK_SIZE)
                if len(block) == BLOCK_SIZE and _is_root_block(block):
                    return candidate * 2 * BLOCK_SIZE
    except OSError:
        return None
    return None


def _root_candidates(total_blocks: int):
    """Yield plausible root-block positions, most likely first."""
    midpoint = total_blocks // 2
    seen = set()
    for candidate in (midpoint, midpoint - 1, midpoint + 1):
        if RESERVED_BLOCKS <= candidate < total_blocks and candidate not in seen:
            seen.add(candidate)
            yield candidate
    # A padded image still has its root where the *real* volume's midpoint
    # was, so step back through smaller plausible volumes rather than reading
    # every block on a multi-gigabyte drive.
    blocks = total_blocks
    while blocks > RESERVED_BLOCKS + 4:
        blocks //= 2
        candidate = blocks // 2
        if candidate not in seen and RESERVED_BLOCKS <= candidate < total_blocks:
            seen.add(candidate)
            yield candidate


def _is_root_block(block: bytes) -> bool:
    if int.from_bytes(block[0:4], "big") != T_HEADER:
        return False
    if int.from_bytes(block[-4:], "big", signed=True) != ST_ROOT:
        return False
    return block_checksum(block) == int.from_bytes(
        block[ROOT_CHECKSUM_OFFSET : ROOT_CHECKSUM_OFFSET + 4], "big"
    )


def range_is_zero(
    path: Path,
    start: int,
    buffer_size: int = 8 * 1024 * 1024,
) -> bool:
    """Check a prospective compatibility tail without loading it into memory."""
    with path.open("rb") as image:
        image.seek(start)
        while chunk := image.read(buffer_size):
            if chunk.strip(b"\0"):
                return False
    return True


def block_checksum(block: bytes | bytearray, checksum_offset: int = ROOT_CHECKSUM_OFFSET) -> int:
    """Return the value that makes an AmigaDOS block's longs sum to zero."""
    total = 0
    for index in range(0, len(block) - 3, 4):
        if index == checksum_offset:
            continue
        total = (total + int.from_bytes(block[index : index + 4], "big")) & 0xFFFFFFFF
    return (-total) & 0xFFFFFFFF


def bitmap_is_valid(block: bytes) -> bool:
    """True when a root block declares its block-allocation bitmap usable.

    AmigaDOS sets this to -1 when the bitmap can be trusted and to 0 after an
    unclean shutdown, which is what makes a real machine run ``DiskDoctor``
    instead of mounting. The workbench refuses to write to a volume in that
    state for the same reason.
    """
    offset = len(block) - ROOT_BITMAP_FLAG
    return int.from_bytes(block[offset : offset + 4], "big") == 0xFFFFFFFF


__all__ = [
    "BLOCK_SIZE",
    "DIRECTORY_HASH_ENTRIES",
    "MAX_BLOCKS",
    "MAX_SECTORS",
    "MAX_SIZE",
    "OLD_DIRECTORY_ENTRY_OFFSET",
    "OLD_DIRECTORY_ENTRY_SIZE",
    "OLD_DIRECTORY_MAX_ENTRIES",
    "OLD_DIRECTORY_SIZE",
    "OLD_DIRECTORY_TAIL",
    "OLD_ROOT_OFFSET",
    "RESERVED_BLOCKS",
    "ROOT_BITMAP_FLAG",
    "ROOT_BITMAP_PAGES",
    "ROOT_CHECKSUM_OFFSET",
    "ROOT_HASH_TABLE_OFFSET",
    "ROOT_NAME",
    "SECTORS_PER_TRACK",
    "SECTOR_SIZE",
    "bitmap_is_valid",
    "block_checksum",
    "descriptor_size",
    "volume_extent",
    "format_geometry",
    "parse_geometry",
    "range_is_zero",
]
