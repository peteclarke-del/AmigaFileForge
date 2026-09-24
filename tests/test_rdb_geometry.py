"""Where a Rigid Disk Block partition sits on the drive."""

from __future__ import annotations

import unittest

from amiganut.filesystem.rdb import Partition


def partition(**overrides) -> Partition:
    """The first partition of a real SFS drive prepared on an Amiga.

    The values are its PART block's: 32 surfaces of 128 sectors, cylinders 2
    to 1995, and two sectors grouped into each 1024-byte SFS block.
    """
    fields = dict(
        index=0,
        block=1,
        name="DH0",
        flags=0,
        surfaces=32,
        blocks_per_track=128,
        sectors_per_block=2,
        reserved=2,
        low_cylinder=2,
        high_cylinder=1995,
        buffers=80,
        boot_priority=0,
        dos_type=b"SFS\x00",
        mask=0x7FFFFFFE,
        max_transfer=130560,
    )
    fields.update(overrides)
    return Partition(**fields)


class PartitionPlacementTests(unittest.TestCase):
    def test_sectors_per_block_does_not_move_the_partition(self) -> None:
        # The drive's SFS root block was found 4 MiB in, which is cylinder 2
        # at 32 x 128 sectors of 512 bytes. Linux places the partition there
        # too. Scaling by sectors per block would put it at 8 MiB, inside the
        # volume's own data.
        drive = partition()
        self.assertEqual(drive.start_block * drive.block_size, 4 * 1024 * 1024)
        self.assertEqual(drive.total_blocks, 1994 * 32 * 128)

    def test_grouped_and_single_sector_blocks_cover_the_same_cylinders(self) -> None:
        grouped = partition(sectors_per_block=2)
        single = partition(sectors_per_block=1)
        self.assertEqual(grouped.start_block, single.start_block)
        self.assertEqual(grouped.size_bytes, single.size_bytes)


if __name__ == "__main__":
    unittest.main()
