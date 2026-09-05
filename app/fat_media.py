"""Building a FAT32 card that carries one Amiga image, without mount privileges.

Two things need this. A Gotek or FlashFloppy drive reads its disk images from a
FAT-formatted USB stick, and an emulator hand-off of a whole hard drive wants
that drive presented as a single file on a card the emulator can attach. Both
are the same job: lay out a deterministic FAT32 volume with one contiguous file
in its root and enough spare clusters for the target to write its own catalogue
back.

Nothing here needs `mkfs`, a loop device or root. The layout is computed and
written directly, which is what makes it reproducible: the same input always
produces byte-identical output, so a card can be compared against a previous
build rather than only trusted.
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from pathlib import Path


SECTOR_SIZE = 512
SECTORS_PER_CLUSTER = 4
RESERVED_SECTORS = 32
FAT_COUNT = 2
ROOT_CLUSTER = 2
FIRST_FILE_CLUSTER = 3
FAT32_MIN_CLUSTERS = 65525


class FatMediaError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FatFileLayout:
    image_size: int
    data_offset: int
    file_size: int
    clusters: int


def _geometry(file_size: int) -> tuple[int, int, int, int]:
    cluster_bytes = SECTOR_SIZE * SECTORS_PER_CLUSTER
    file_clusters = max(1, math.ceil(file_size / cluster_bytes))
    # The FAT type is inferred from the data-cluster count rather than declared,
    # so even a tiny card must exceed the FAT16 ceiling to be read as FAT32.
    # Gotek firmware and every emulator agree on that rule.
    spare_clusters = max(64, math.ceil(file_clusters / 8))
    cluster_count = max(1 + file_clusters + spare_clusters, FAT32_MIN_CLUSTERS)
    if cluster_count >= 0x0FFFFFF0:
        raise FatMediaError("The image is too large for a FAT32 card.")
    fat_sectors = math.ceil((cluster_count + 2) * 4 / SECTOR_SIZE)
    total_sectors = (
        RESERVED_SECTORS + FAT_COUNT * fat_sectors
        + cluster_count * SECTORS_PER_CLUSTER
    )
    if total_sectors > 0xFFFFFFFF:
        raise FatMediaError("The requested card exceeds FAT32 geometry limits.")
    return file_clusters, cluster_count, fat_sectors, total_sectors


def build_hdf_card(hdf_path: Path, destination: Path) -> FatFileLayout:
    """Build a deterministic FAT32 card holding one contiguous ``AMIGA.HDF``.

    The file is written as one unbroken run of clusters, with spare clusters
    left after it so the target can write its own catalogue changes back. No
    mount privileges or host filesystem tools are required.
    """
    file_size = hdf_path.stat().st_size
    if file_size <= 0 or file_size > 0xFFFFFFFF:
        raise FatMediaError("The image must contain between 1 byte and 4 GiB minus one byte.")
    file_clusters, cluster_count, fat_sectors, total_sectors = _geometry(file_size)
    first_data_sector = RESERVED_SECTORS + FAT_COUNT * fat_sectors
    root_offset = first_data_sector * SECTOR_SIZE
    data_offset = (first_data_sector + SECTORS_PER_CLUSTER) * SECTOR_SIZE

    boot = bytearray(SECTOR_SIZE)
    boot[:3] = b"\xEB\x58\x90"
    boot[3:11] = b"AMIGAFF "
    struct.pack_into("<H", boot, 11, SECTOR_SIZE)
    boot[13] = SECTORS_PER_CLUSTER
    struct.pack_into("<H", boot, 14, RESERVED_SECTORS)
    boot[16] = FAT_COUNT
    struct.pack_into("<H", boot, 17, 0)
    struct.pack_into("<H", boot, 19, 0)
    boot[21] = 0xF8
    struct.pack_into("<H", boot, 22, 0)
    struct.pack_into("<H", boot, 24, 32)
    struct.pack_into("<H", boot, 26, 64)
    struct.pack_into("<I", boot, 32, total_sectors)
    struct.pack_into("<I", boot, 36, fat_sectors)
    struct.pack_into("<I", boot, 44, ROOT_CLUSTER)
    struct.pack_into("<H", boot, 48, 1)
    struct.pack_into("<H", boot, 50, 6)
    boot[64] = 0x80
    boot[66] = 0x29
    struct.pack_into("<I", boot, 67, 0xAFF0BEEF)
    boot[71:82] = b"AMIGA FORGE"
    boot[82:90] = b"FAT32   "
    boot[510:512] = b"\x55\xAA"

    fsinfo = bytearray(SECTOR_SIZE)
    fsinfo[0:4] = b"RRaA"
    fsinfo[484:488] = b"rrAa"
    free_clusters = cluster_count - file_clusters - 1
    struct.pack_into("<I", fsinfo, 488, free_clusters)
    struct.pack_into("<I", fsinfo, 492, FIRST_FILE_CLUSTER + file_clusters)
    fsinfo[510:512] = b"\x55\xAA"

    fat = bytearray(fat_sectors * SECTOR_SIZE)
    struct.pack_into("<I", fat, 0, 0x0FFFFFF8)
    struct.pack_into("<I", fat, 4, 0xFFFFFFFF)
    struct.pack_into("<I", fat, ROOT_CLUSTER * 4, 0x0FFFFFFF)
    for offset in range(file_clusters):
        cluster = FIRST_FILE_CLUSTER + offset
        following = 0x0FFFFFFF if offset == file_clusters - 1 else cluster + 1
        struct.pack_into("<I", fat, cluster * 4, following)

    root = bytearray(SECTORS_PER_CLUSTER * SECTOR_SIZE)
    root[:11] = b"AMIGA FORGE"
    root[11] = 0x08
    file_entry = 32
    # An 8.3 directory entry is exactly eleven bytes: eight for the name and
    # three for the extension, both space-padded and with no separating dot.
    root[file_entry : file_entry + 11] = b"AMIGA   HDF"
    root[file_entry + 11] = 0x20
    struct.pack_into("<H", root, file_entry + 20, FIRST_FILE_CLUSTER >> 16)
    struct.pack_into("<H", root, file_entry + 26, FIRST_FILE_CLUSTER & 0xFFFF)
    struct.pack_into("<I", root, file_entry + 28, file_size)

    with destination.open("wb") as output:
        output.write(boot)
        output.write(fsinfo)
        output.write(bytes(SECTOR_SIZE * 4))
        output.write(boot)
        output.write(bytes(SECTOR_SIZE * (RESERVED_SECTORS - 7)))
        output.write(fat)
        output.write(fat)
        output.write(root)
        with hdf_path.open("rb") as source:
            while block := source.read(1024 * 1024):
                output.write(block)
        output.truncate(total_sectors * SECTOR_SIZE)
    if root_offset + len(root) != data_offset:
        raise FatMediaError("The FAT32 root layout is inconsistent.")
    return FatFileLayout(total_sectors * SECTOR_SIZE, data_offset, file_size, file_clusters)


def read_hdf_card(card_path: Path, layout: FatFileLayout) -> bytes:
    with card_path.open("rb") as source:
        source.seek(layout.data_offset)
        data = source.read(layout.file_size)
    if len(data) != layout.file_size:
        raise FatMediaError("The card no longer contains a complete AMIGA.HDF file.")
    return data


#: The Gotek and FlashFloppy configuration a freshly built stick is given.
FLASHFLOPPY_CONFIG = b"""\
# FF.CFG written by Amiga File Forge
interface = shugart
host = amiga
display-type = auto
nav-mode = indexed
autoselect-file-order = 1
"""


__all__ = [
    "FLASHFLOPPY_CONFIG",
    "FatFileLayout",
    "FatMediaError",
    "build_hdf_card",
    "read_hdf_card",
]
