"""Raw block access and the AmigaDOS on-disk block formats.

Everything here works in whole 512-byte blocks of big-endian longs, which is
what the Amiga's ``trackdisk.device`` and ``scsi.device`` hand to the
filesystem. Keeping the structure decoding in one module means the volume
code above it never touches an offset directly.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import DataError

BLOCK_SIZE = 512
LONGS_PER_BLOCK = BLOCK_SIZE // 4

# Primary block types.
T_HEADER = 2
T_DATA = 8
T_LIST = 16
T_DIRCACHE = 33

# Secondary block types.
ST_ROOT = 1
ST_USERDIR = 2
ST_SOFTLINK = 3
ST_LINKDIR = 4
ST_FILE = -3
ST_LINKFILE = -4

# Standard floppy geometries, in blocks.
DD_BLOCKS = 80 * 2 * 11        # 1760 blocks, 880 KiB
HD_BLOCKS = 80 * 2 * 22        # 3520 blocks, 1.76 MiB
RESERVED_BLOCKS = 2            # the two boot blocks

# DOS types. The trailing byte selects the variant.
DOS_TYPES = {
    b"DOS\x00": "OFS",
    b"DOS\x01": "FFS",
    b"DOS\x02": "OFS-INTL",
    b"DOS\x03": "FFS-INTL",
    b"DOS\x04": "OFS-DC",
    b"DOS\x05": "FFS-DC",
    b"DOS\x06": "OFS-LNFS",
    b"DOS\x07": "FFS-LNFS",
    b"PFS\x03": "PFS3",
    b"SFS\x00": "SFS",
    b"SFS\x02": "SFS2",
}

FORMAT_LABELS = {value: key for key, value in DOS_TYPES.items()}

#: Variants this build can read and write.
WRITABLE_FORMATS = ("OFS", "FFS", "OFS-INTL", "FFS-INTL", "OFS-DC", "FFS-DC")

#: Variants this build can recognise but not modify.
READ_ONLY_FORMATS = ("OFS-LNFS", "FFS-LNFS", "PFS3", "SFS", "SFS2")

MAX_NAME = 30
MAX_COMMENT = 79


def is_ffs(dos_type: bytes) -> bool:
    """FFS stores file data in whole blocks; OFS reserves a 24-byte header."""
    return bool(dos_type[3] & 1)


def is_international(dos_type: bytes) -> bool:
    """International mode folds the 8-bit Latin-1 letters when hashing."""
    return bool(dos_type[3] & 2) or bool(dos_type[3] & 4)


def is_dircache(dos_type: bytes) -> bool:
    """Directory-cache mode keeps a summary block chain for fast listings."""
    return bool(dos_type[3] & 4)


def upper_char(character: str, international: bool) -> str:
    """Fold one character exactly the way AmigaDOS hashing does."""
    code = ord(character)
    if international:
        if 97 <= code <= 122 or 224 <= code <= 254 and code != 247:
            return chr(code - 32)
        return character
    if 97 <= code <= 122:
        return chr(code - 32)
    return character


def hash_name(name: str, international: bool, table_size: int) -> int:
    """Return the hash-table slot AmigaDOS would use for this name."""
    value = len(name)
    for character in name:
        value = (value * 13 + ord(upper_char(character, international))) & 0x7FF
    return value % table_size


def names_match(left: str, right: str, international: bool) -> bool:
    """AmigaDOS name comparison: case-insensitive, with the same folding."""
    if len(left) != len(right):
        return False
    return all(
        upper_char(a, international) == upper_char(b, international)
        for a, b in zip(left, right)
    )


def block_checksum(block: bytes, checksum_offset: int = 20) -> int:
    """Return the value that makes the block's longs sum to zero."""
    total = 0
    for index in range(0, len(block), 4):
        if index == checksum_offset:
            continue
        (value,) = struct.unpack_from(">I", block, index)
        total = (total + value) & 0xFFFFFFFF
    return (-total) & 0xFFFFFFFF


def apply_checksum(block: bytearray, checksum_offset: int = 20) -> bytearray:
    struct.pack_into(">I", block, checksum_offset, block_checksum(bytes(block), checksum_offset))
    return block


def verify_checksum(block: bytes, checksum_offset: int = 20) -> bool:
    (stored,) = struct.unpack_from(">I", block, checksum_offset)
    return stored == block_checksum(block, checksum_offset)


def read_bstr(block: bytes, offset: int, limit: int) -> str:
    """Read a BCPL string: one length byte followed by its characters."""
    length = min(block[offset], limit)
    return block[offset + 1 : offset + 1 + length].decode("latin-1")


def write_bstr(block: bytearray, offset: int, value: str, limit: int) -> None:
    encoded = value.encode("latin-1", "replace")[:limit]
    block[offset] = len(encoded)
    block[offset + 1 : offset + 1 + limit] = encoded.ljust(limit, b"\0")


def long_at(block: bytes, offset: int) -> int:
    (value,) = struct.unpack_from(">I", block, offset)
    return value


def signed_long_at(block: bytes, offset: int) -> int:
    (value,) = struct.unpack_from(">i", block, offset)
    return value


def put_long(block: bytearray, offset: int, value: int) -> None:
    struct.pack_into(">I", block, offset, int(value) & 0xFFFFFFFF)


def put_signed_long(block: bytearray, offset: int, value: int) -> None:
    struct.pack_into(">i", block, offset, int(value))


class BlockReader:
    """A seekable window onto an image file, addressed in whole blocks."""

    def __init__(
        self,
        path: Path | str,
        *,
        writable: bool = False,
        offset: int = 0,
        length: int | None = None,
        block_size: int = BLOCK_SIZE,
    ):
        self.path = Path(path)
        self.writable = bool(writable)
        self.block_size = int(block_size)
        self._handle = self.path.open("r+b" if writable else "rb")
        size = self.path.stat().st_size
        self.offset = int(offset)
        if self.offset < 0 or self.offset > size:
            raise DataError("The partition starts beyond the end of the image.")
        available = size - self.offset
        self.length = int(length) if length is not None else available
        if self.length > available:
            # A partition table may describe a drive larger than the file that
            # holds it. Report the honest usable extent rather than reading
            # past the end of the file.
            self.length = available
        self.total_blocks = self.length // self.block_size

    # ---- context management ------------------------------------------
    def __enter__(self) -> "BlockReader":
        return self

    def __exit__(self, *_exception) -> None:
        self.close()

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.flush()
            self._handle.close()

    # ---- block access ------------------------------------------------
    def read_block(self, number: int) -> bytes:
        if not 0 <= number < self.total_blocks:
            raise DataError(f"Block {number} is outside this volume.")
        self._handle.seek(self.offset + number * self.block_size)
        data = self._handle.read(self.block_size)
        if len(data) < self.block_size:
            data = data.ljust(self.block_size, b"\0")
        return data

    def write_block(self, number: int, data: bytes) -> None:
        if not self.writable:
            raise DataError("This volume is open read-only.")
        if not 0 <= number < self.total_blocks:
            raise DataError(f"Block {number} is outside this volume.")
        if len(data) != self.block_size:
            raise DataError("A block write must supply exactly one block.")
        self._handle.seek(self.offset + number * self.block_size)
        self._handle.write(data)

    def flush(self) -> None:
        if self.writable:
            self._handle.flush()

    def window(self, offset_blocks: int, length_blocks: int) -> "BlockReader":
        """Open a nested reader for one partition of this device."""
        return BlockReader(
            self.path,
            writable=self.writable,
            offset=self.offset + offset_blocks * self.block_size,
            length=length_blocks * self.block_size,
            block_size=self.block_size,
        )


@dataclass
class Geometry:
    """Physical geometry for an image that does not carry its own."""

    surfaces: int = 2
    blocks_per_track: int = 11
    reserved: int = RESERVED_BLOCKS
    block_size: int = BLOCK_SIZE
    low_cylinder: int = 0
    high_cylinder: int = 79
    sectors_per_block: int = 1
    boot_priority: int = 0
    dos_type: bytes = b"DOS\x00"
    mask: int = 0x7FFFFFFE
    max_transfer: int = 0x00FFFFFF
    buffers: int = 30
    label: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def cylinders(self) -> int:
        return self.high_cylinder - self.low_cylinder + 1

    @property
    def total_blocks(self) -> int:
        return self.cylinders * self.surfaces * self.blocks_per_track

    @property
    def size_bytes(self) -> int:
        return self.total_blocks * self.block_size

    def to_dict(self) -> dict:
        return {
            "surfaces": self.surfaces,
            "blocksPerTrack": self.blocks_per_track,
            "reserved": self.reserved,
            "blockSize": self.block_size,
            "lowCylinder": self.low_cylinder,
            "highCylinder": self.high_cylinder,
            "cylinders": self.cylinders,
            "totalBlocks": self.total_blocks,
            "sizeBytes": self.size_bytes,
            "bootPriority": self.boot_priority,
            "dosType": self.dos_type.decode("latin-1"),
            "format": DOS_TYPES.get(self.dos_type, "unknown"),
            "label": self.label,
        }


DD_GEOMETRY = Geometry(surfaces=2, blocks_per_track=11, high_cylinder=79)
HD_GEOMETRY = Geometry(surfaces=2, blocks_per_track=22, high_cylinder=79)

#: Named floppy and drive geometries the workbench can create.
NAMED_GEOMETRIES = {
    "dd": DD_GEOMETRY,
    "880k": DD_GEOMETRY,
    "hd": HD_GEOMETRY,
    "1760k": HD_GEOMETRY,
}


__all__ = [
    "BLOCK_SIZE",
    "BlockReader",
    "DD_BLOCKS",
    "DD_GEOMETRY",
    "DOS_TYPES",
    "FORMAT_LABELS",
    "Geometry",
    "HD_BLOCKS",
    "HD_GEOMETRY",
    "LONGS_PER_BLOCK",
    "MAX_COMMENT",
    "MAX_NAME",
    "NAMED_GEOMETRIES",
    "READ_ONLY_FORMATS",
    "RESERVED_BLOCKS",
    "ST_FILE",
    "ST_LINKDIR",
    "ST_LINKFILE",
    "ST_ROOT",
    "ST_SOFTLINK",
    "ST_USERDIR",
    "T_DATA",
    "T_DIRCACHE",
    "T_HEADER",
    "T_LIST",
    "WRITABLE_FORMATS",
    "apply_checksum",
    "block_checksum",
    "hash_name",
    "is_dircache",
    "is_ffs",
    "is_international",
    "long_at",
    "names_match",
    "put_long",
    "put_signed_long",
    "read_bstr",
    "signed_long_at",
    "upper_char",
    "verify_checksum",
    "write_bstr",
]
