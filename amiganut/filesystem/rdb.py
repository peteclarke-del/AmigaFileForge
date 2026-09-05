"""Rigid Disk Block: the Amiga's on-drive partition table.

An Amiga hard drive describes itself. The first sixteen blocks hold an
``RDSK`` block, which chains to ``PART`` blocks for each partition, ``FSHD``
blocks for any filesystem the ROM does not already provide, and a bad-block
list. A ``.hdf`` that carries an RDB therefore holds several independently
mountable volumes in one file, each with its own name, DOS type, buffers and
boot priority -- which is exactly what the workbench presents as its partition
table.

An image with no RDB is a *hardfile*: one bare volume that the host has to be
told the geometry for. That case is handled by ``amiganut.filesystem.geometry``.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..errors import ConfigurationError, DataError
from .blocks import (
    BLOCK_SIZE,
    DOS_TYPES,
    BlockReader,
    Geometry,
    long_at,
    put_long,
    put_signed_long,
    read_bstr,
    signed_long_at,
    write_bstr,
)

RDSK = b"RDSK"
PART = b"PART"
FSHD = b"FSHD"
LSEG = b"LSEG"
BADB = b"BADB"

#: The RDB must appear within the first sixteen blocks of the drive.
RDB_SEARCH_LIMIT = 16

END_OF_LIST = 0xFFFFFFFF

# PART flags
PARTF_BOOTABLE = 1
PARTF_NOMOUNT = 2

# DosEnvVec field offsets inside a PART block.
DE_BASE = 128
DE_TABLESIZE = DE_BASE + 0
DE_SIZEBLOCK = DE_BASE + 4
DE_SECORG = DE_BASE + 8
DE_SURFACES = DE_BASE + 12
DE_SECTORPERBLOCK = DE_BASE + 16
DE_BLKSPERTRACK = DE_BASE + 20
DE_RESERVEDBLKS = DE_BASE + 24
DE_PREFAC = DE_BASE + 28
DE_INTERLEAVE = DE_BASE + 32
DE_LOWCYL = DE_BASE + 36
DE_HIGHCYL = DE_BASE + 40
DE_NUMBUFFERS = DE_BASE + 44
DE_BUFMEMTYPE = DE_BASE + 48
DE_MAXTRANSFER = DE_BASE + 52
DE_MASK = DE_BASE + 56
DE_BOOTPRI = DE_BASE + 60
DE_DOSTYPE = DE_BASE + 64
DE_BAUD = DE_BASE + 68
DE_CONTROL = DE_BASE + 72
DE_BOOTBLOCKS = DE_BASE + 76


def rdb_checksum(block: bytes, longs: int) -> int:
    total = 0
    for index in range(longs):
        if index == 2:
            continue
        total = (total + long_at(block, index * 4)) & 0xFFFFFFFF
    return (-total) & 0xFFFFFFFF


def apply_rdb_checksum(block: bytearray, longs: int) -> bytearray:
    put_long(block, 8, rdb_checksum(bytes(block), longs))
    return block


def verify_rdb_checksum(block: bytes, longs: int) -> bool:
    total = 0
    for index in range(longs):
        total = (total + long_at(block, index * 4)) & 0xFFFFFFFF
    return total == 0


@dataclass
class Partition:
    """One ``PART`` entry, decoded into the fields the workbench shows."""

    index: int
    block: int
    name: str
    flags: int
    surfaces: int
    blocks_per_track: int
    sectors_per_block: int
    reserved: int
    low_cylinder: int
    high_cylinder: int
    buffers: int
    boot_priority: int
    dos_type: bytes
    mask: int
    max_transfer: int
    block_size: int = BLOCK_SIZE

    @property
    def bootable(self) -> bool:
        return bool(self.flags & PARTF_BOOTABLE)

    @property
    def automount(self) -> bool:
        return not self.flags & PARTF_NOMOUNT

    @property
    def blocks_per_cylinder(self) -> int:
        return self.surfaces * self.blocks_per_track * self.sectors_per_block

    @property
    def start_block(self) -> int:
        return self.low_cylinder * self.blocks_per_cylinder

    @property
    def total_blocks(self) -> int:
        cylinders = self.high_cylinder - self.low_cylinder + 1
        return cylinders * self.blocks_per_cylinder

    @property
    def size_bytes(self) -> int:
        return self.total_blocks * self.block_size

    @property
    def format(self) -> str:
        return DOS_TYPES.get(self.dos_type, "unknown")

    def geometry(self) -> Geometry:
        return Geometry(
            surfaces=self.surfaces,
            blocks_per_track=self.blocks_per_track,
            reserved=self.reserved,
            block_size=self.block_size,
            low_cylinder=self.low_cylinder,
            high_cylinder=self.high_cylinder,
            sectors_per_block=self.sectors_per_block,
            boot_priority=self.boot_priority,
            dos_type=self.dos_type,
            mask=self.mask,
            max_transfer=self.max_transfer,
            buffers=self.buffers,
            label=self.name,
        )

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "name": self.name,
            "device": self.name,
            "bootable": self.bootable,
            "automount": self.automount,
            "bootPriority": self.boot_priority,
            "dosType": self.dos_type.decode("latin-1"),
            "format": self.format,
            "surfaces": self.surfaces,
            "blocksPerTrack": self.blocks_per_track,
            "reserved": self.reserved,
            "lowCylinder": self.low_cylinder,
            "highCylinder": self.high_cylinder,
            "buffers": self.buffers,
            "startBlock": self.start_block,
            "totalBlocks": self.total_blocks,
            "sizeBytes": self.size_bytes,
        }


@dataclass
class RigidDisk:
    """The decoded ``RDSK`` block and every partition it chains to."""

    block: int
    block_size: int
    cylinders: int
    sectors: int
    heads: int
    high_rdb_block: int
    park_cylinder: int
    partitions: list[Partition] = field(default_factory=list)
    filesystems: list[dict] = field(default_factory=list)
    disk_vendor: str = ""
    disk_product: str = ""
    disk_revision: str = ""

    @property
    def blocks_per_cylinder(self) -> int:
        return self.heads * self.sectors

    def to_dict(self) -> dict:
        return {
            "blockSize": self.block_size,
            "cylinders": self.cylinders,
            "heads": self.heads,
            "sectors": self.sectors,
            "highRdbBlock": self.high_rdb_block,
            "vendor": self.disk_vendor,
            "product": self.disk_product,
            "revision": self.disk_revision,
            "partitions": [partition.to_dict() for partition in self.partitions],
            "filesystems": list(self.filesystems),
        }


def find_rdb_block(reader: BlockReader) -> int | None:
    """Return the block holding the ``RDSK`` signature, or None."""
    limit = min(RDB_SEARCH_LIMIT, reader.total_blocks)
    for block in range(limit):
        if reader.read_block(block)[:4] == RDSK:
            return block
    return None


def read_rigid_disk(reader: BlockReader) -> RigidDisk:
    """Decode the RDB, its partitions and its filesystem headers."""
    block_number = find_rdb_block(reader)
    if block_number is None:
        raise DataError("This image does not contain a Rigid Disk Block.")
    raw = reader.read_block(block_number)
    size_longs = long_at(raw, 4)
    if not 16 <= size_longs <= reader.block_size // 4:
        raise DataError("The RDSK block declares an impossible size.")
    if not verify_rdb_checksum(raw, size_longs):
        raise DataError("The RDSK block checksum is wrong.")
    disk = RigidDisk(
        block=block_number,
        block_size=long_at(raw, 16) or BLOCK_SIZE,
        cylinders=long_at(raw, 64),
        sectors=long_at(raw, 68),
        heads=long_at(raw, 72),
        high_rdb_block=long_at(raw, 92),
        park_cylinder=long_at(raw, 100),
        disk_vendor=raw[128:136].decode("latin-1").strip("\0 "),
        disk_product=raw[136:152].decode("latin-1").strip("\0 "),
        disk_revision=raw[152:156].decode("latin-1").strip("\0 "),
    )

    partition_block = long_at(raw, 28)
    index = 0
    seen: set[int] = set()
    while partition_block not in (0, END_OF_LIST):
        if partition_block in seen or partition_block >= reader.total_blocks:
            raise DataError("The RDB partition chain is damaged.")
        seen.add(partition_block)
        entry = reader.read_block(partition_block)
        if entry[:4] != PART:
            raise DataError(f"Block {partition_block} should hold a PART entry.")
        part_longs = long_at(entry, 4)
        if not verify_rdb_checksum(entry, part_longs):
            raise DataError(f"The PART block at {partition_block} has a bad checksum.")
        disk.partitions.append(
            Partition(
                index=index,
                block=partition_block,
                name=read_bstr(entry, 36, 31),
                flags=long_at(entry, 20),
                surfaces=long_at(entry, DE_SURFACES),
                blocks_per_track=long_at(entry, DE_BLKSPERTRACK),
                sectors_per_block=long_at(entry, DE_SECTORPERBLOCK) or 1,
                reserved=long_at(entry, DE_RESERVEDBLKS) or 2,
                low_cylinder=long_at(entry, DE_LOWCYL),
                high_cylinder=long_at(entry, DE_HIGHCYL),
                buffers=long_at(entry, DE_NUMBUFFERS),
                boot_priority=signed_long_at(entry, DE_BOOTPRI),
                dos_type=entry[DE_DOSTYPE : DE_DOSTYPE + 4],
                mask=long_at(entry, DE_MASK),
                max_transfer=long_at(entry, DE_MAXTRANSFER),
                block_size=(long_at(entry, DE_SIZEBLOCK) or 128) * 4,
            )
        )
        index += 1
        partition_block = long_at(entry, 16)

    filesystem_block = long_at(raw, 32)
    seen.clear()
    while filesystem_block not in (0, END_OF_LIST):
        if filesystem_block in seen or filesystem_block >= reader.total_blocks:
            raise DataError("The RDB filesystem chain is damaged.")
        seen.add(filesystem_block)
        entry = reader.read_block(filesystem_block)
        if entry[:4] != FSHD:
            break
        dos_type = entry[32:36]
        version = long_at(entry, 36)
        disk.filesystems.append(
            {
                "block": filesystem_block,
                "dosType": dos_type.decode("latin-1"),
                "format": DOS_TYPES.get(dos_type, "unknown"),
                "version": f"{version >> 16}.{version & 0xFFFF}",
            }
        )
        filesystem_block = long_at(entry, 16)

    return disk


def partition_reader(reader: BlockReader, partition: Partition) -> BlockReader:
    """Open a nested reader covering exactly one partition."""
    return reader.window(partition.start_block, partition.total_blocks)


def write_rigid_disk(
    reader: BlockReader,
    partitions: list[dict],
    *,
    heads: int = 16,
    sectors: int = 63,
    block_size: int = BLOCK_SIZE,
    vendor: str = "AMIGA",
    product: str = "FILE FORGE HDF",
    revision: str = "1.1",
    first_partition_block: int = 1,
) -> RigidDisk:
    """Lay out a fresh RDB and its partition chain across the whole image.

    ``partitions`` entries take ``name``, ``dosType``, ``sizeBytes`` (or
    ``cylinders``), ``bootable``, ``bootPriority`` and ``buffers``. Sizes are
    rounded up to whole cylinders, because that is the only unit an RDB can
    describe.
    """
    if not partitions:
        raise ConfigurationError("An RDB needs at least one partition.")
    blocks_per_cylinder = heads * sectors
    total_cylinders = reader.total_blocks // blocks_per_cylinder
    # Reserve the first cylinder for the RDB, its partition entries and room
    # to add more later, exactly as HDToolBox does.
    reserved_cylinders = 1
    available = total_cylinders - reserved_cylinders
    if available < len(partitions):
        raise ConfigurationError(
            "The image is too small for the requested partitions."
        )

    requested = []
    for entry in partitions:
        cylinders = int(entry.get("cylinders") or 0)
        if not cylinders:
            size = int(entry.get("sizeBytes") or 0)
            cylinders = max(1, -(-size // (blocks_per_cylinder * block_size)))
        requested.append(cylinders)
    if sum(requested) > available:
        # Scale proportionally rather than refusing, so "split this drive in
        # four" always produces four usable partitions.
        scale = available / sum(requested)
        requested = [max(1, int(value * scale)) for value in requested]
        while sum(requested) > available:
            requested[requested.index(max(requested))] -= 1

    blank = b"\0" * reader.block_size
    for block in range(min(reader.total_blocks, RDB_SEARCH_LIMIT + len(partitions) + 1)):
        reader.write_block(block, blank)

    rdb_block = 0
    if first_partition_block <= rdb_block:
        raise ConfigurationError("Partition blocks must follow the RDSK block.")
    low = reserved_cylinders
    part_blocks = []
    for index, (entry, cylinders) in enumerate(zip(partitions, requested)):
        block_number = first_partition_block + index
        high = low + cylinders - 1
        dos_type = entry.get("dosType") or b"DOS\x03"
        if isinstance(dos_type, str):
            dos_type = dos_type.encode("latin-1")[:3].ljust(3, b" ") + bytes(
                [int(entry.get("dosVariant", 3))]
            ) if len(dos_type) < 4 else dos_type.encode("latin-1")[:4]
        raw = bytearray(reader.block_size)
        raw[0:4] = PART
        put_long(raw, 4, 64)
        put_long(raw, 12, 0xFFFFFFFF)
        put_long(
            raw,
            16,
            block_number + 1 if index + 1 < len(partitions) else END_OF_LIST,
        )
        flags = PARTF_BOOTABLE if entry.get("bootable") else 0
        if entry.get("automount") is False:
            flags |= PARTF_NOMOUNT
        put_long(raw, 20, flags)
        write_bstr(raw, 36, str(entry.get("name") or f"DH{index}")[:31], 31)
        put_long(raw, DE_TABLESIZE, 16)
        put_long(raw, DE_SIZEBLOCK, block_size // 4)
        put_long(raw, DE_SECORG, 0)
        put_long(raw, DE_SURFACES, heads)
        put_long(raw, DE_SECTORPERBLOCK, 1)
        put_long(raw, DE_BLKSPERTRACK, sectors)
        put_long(raw, DE_RESERVEDBLKS, 2)
        put_long(raw, DE_PREFAC, 0)
        put_long(raw, DE_INTERLEAVE, 0)
        put_long(raw, DE_LOWCYL, low)
        put_long(raw, DE_HIGHCYL, high)
        put_long(raw, DE_NUMBUFFERS, int(entry.get("buffers") or 30))
        put_long(raw, DE_BUFMEMTYPE, 0)
        put_long(raw, DE_MAXTRANSFER, 0x00FFFFFF)
        put_long(raw, DE_MASK, 0x7FFFFFFE)
        put_signed_long(raw, DE_BOOTPRI, int(entry.get("bootPriority") or 0))
        raw[DE_DOSTYPE : DE_DOSTYPE + 4] = dos_type
        put_long(raw, DE_BOOTBLOCKS, 0)
        apply_rdb_checksum(raw, 64)
        reader.write_block(block_number, bytes(raw))
        part_blocks.append(block_number)
        low = high + 1

    raw = bytearray(reader.block_size)
    raw[0:4] = RDSK
    put_long(raw, 4, 64)
    put_long(raw, 12, 0xFFFFFFFF)
    put_long(raw, 16, block_size)
    put_long(raw, 20, 0x17)
    put_long(raw, 24, END_OF_LIST)
    put_long(raw, 28, part_blocks[0])
    put_long(raw, 32, END_OF_LIST)
    put_long(raw, 36, END_OF_LIST)
    put_long(raw, 64, total_cylinders)
    put_long(raw, 68, sectors)
    put_long(raw, 72, heads)
    put_long(raw, 76, 0)
    put_long(raw, 80, 0)
    put_long(raw, 84, 0)
    put_long(raw, 88, 0)
    put_long(raw, 92, first_partition_block + len(partitions) - 1)
    put_long(raw, 96, 0)
    put_long(raw, 100, total_cylinders)
    struct.pack_into(">8s", raw, 128, vendor.encode("latin-1")[:8].ljust(8, b" "))
    struct.pack_into(">16s", raw, 136, product.encode("latin-1")[:16].ljust(16, b" "))
    struct.pack_into(">4s", raw, 152, revision.encode("latin-1")[:4].ljust(4, b" "))
    apply_rdb_checksum(raw, 64)
    reader.write_block(rdb_block, bytes(raw))
    reader.flush()
    return read_rigid_disk(reader)


__all__ = [
    "PARTF_BOOTABLE",
    "PARTF_NOMOUNT",
    "Partition",
    "RDB_SEARCH_LIMIT",
    "RigidDisk",
    "find_rdb_block",
    "partition_reader",
    "read_rigid_disk",
    "write_rigid_disk",
]
