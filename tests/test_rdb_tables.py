"""Writing a Rigid Disk Block, and changing one that is already there.

A partition table is read by things this project does not control: the
machine's own boot code, HDToolBox, and every other tool that opens a drive.
So the fields are asserted at the offsets ``devices/hardblocks.h`` gives them
rather than by reading back what was written, which would agree with itself
however wrong it was.
"""

from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amiganut.errors import ConfigurationError
from amiganut.filesystem.blocks import BlockReader
from amiganut.filesystem.rdb import (
    FileSystemHandler,
    add_partition,
    change_partition,
    extend_to_media,
    geometry_for_drive,
    handler_version,
    read_rigid_disk,
    remove_handler,
    remove_partition,
    set_handler,
    write_rigid_disk,
)

MIB = 1024 * 1024

#: A load file in miniature: a hunk header, one code hunk and its end, with
#: the version string a real handler carries.
HANDLER = (
    struct.pack(">6I", 0x3F3, 0, 1, 0, 0, 64)
    + struct.pack(">2I", 0x3E9, 64)
    + b"$VER: Professional-File-System-III 19.2 test".ljust(256, b"\0")
    + struct.pack(">I", 0x3F2)
)


def long_at(block: bytes, offset: int) -> int:
    return struct.unpack_from(">I", block, offset)[0]


class TableFixture(unittest.TestCase):
    def setUp(self) -> None:
        fsync = patch("amiganut.filesystem.blocks.os.fsync")
        fsync.start()
        self.addCleanup(fsync.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.image = Path(folder.name) / "drive.hdf"

    def drive(self, size: int = 64 * MIB, partitions=None, **options) -> None:
        with self.image.open("wb") as handle:
            handle.truncate(size)
        with BlockReader(self.image, writable=True) as reader:
            write_rigid_disk(
                reader,
                partitions or [
                    {"name": "DH0", "sizeBytes": 16 * MIB, "bootable": True},
                    {"name": "DH1", "sizeBytes": 16 * MIB},
                    {"name": "DH2"},
                ],
                **options,
            )

    def table(self):
        with BlockReader(self.image) as reader:
            return read_rigid_disk(reader)


class RigidDiskLayoutTests(TableFixture):
    def test_the_fields_sit_where_the_amiga_includes_put_them(self) -> None:
        self.drive()
        raw = self.image.read_bytes()[:512]

        self.assertEqual(raw[:4], b"RDSK")
        self.assertEqual(long_at(raw, 16), 512)
        self.assertEqual(long_at(raw, 28), 1, "the first PART block follows the RDSK")
        cylinders = 64 * MIB // 512 // (16 * 63)
        self.assertEqual(long_at(raw, 64), cylinders)
        self.assertEqual(long_at(raw, 68), 63)
        self.assertEqual(long_at(raw, 72), 16)
        self.assertEqual(long_at(raw, 128), 0, "RDBBlocksLo")
        self.assertEqual(long_at(raw, 132), 16 * 63 - 1, "RDBBlocksHi")
        self.assertEqual(long_at(raw, 136), 1, "LoCylinder")
        self.assertEqual(long_at(raw, 140), cylinders - 1, "HiCylinder")
        self.assertEqual(long_at(raw, 144), 16 * 63, "CylBlocks")
        self.assertEqual(long_at(raw, 152), 3, "HighRDSKBlock: the RDSK and three PARTs")
        self.assertEqual(raw[160:168], b"AMIGA   ")
        self.assertEqual(raw[168:184], b"FILE FORGE HDF  ")

    def test_every_block_of_the_table_sums_to_zero(self) -> None:
        self.drive(handlers=[FileSystemHandler(b"PFS\x03", HANDLER, handler_version(HANDLER))])
        data = self.image.read_bytes()
        for number in range(1 + 3 + 2):
            block = data[number * 512 : (number + 1) * 512]
            longs = long_at(block, 4)
            total = sum(struct.unpack_from(f">{longs}I", block)) & 0xFFFFFFFF
            self.assertEqual(total, 0, f"block {number} ({block[:4]!r})")

    def test_partitions_are_safe_for_a_real_ide_port(self) -> None:
        self.drive()
        for partition in self.table().partitions:
            self.assertEqual(partition.max_transfer, 0x1FE00)
            self.assertEqual(partition.mask, 0x7FFFFFFE)

    def test_a_partition_with_no_size_takes_what_is_left(self) -> None:
        self.drive()
        first, second, rest = self.table().partitions
        self.assertEqual(first.size_bytes, second.size_bytes)
        self.assertGreater(rest.size_bytes, first.size_bytes)
        self.assertEqual(rest.high_cylinder, self.table().cylinders - 1)
        self.assertEqual(self.table().free_ranges(), [])

    def test_a_large_drive_is_described_in_cylinders_of_a_mebibyte(self) -> None:
        self.assertEqual(geometry_for_drive(512 * MIB // 512), (16, 63))
        self.assertEqual(geometry_for_drive(128 * 1024 * MIB // 512), (16, 128))

    def test_a_table_written_by_an_earlier_release_is_still_read(self) -> None:
        """Releases up to 1.6 put the vendor text and the last block elsewhere."""
        self.drive()
        with self.image.open("r+b") as handle:
            raw = bytearray(handle.read(512))
            for offset in range(128, 188, 4):
                struct.pack_into(">I", raw, offset, 0)
            struct.pack_into(">I", raw, 92, 3)
            struct.pack_into(">I", raw, 100, long_at(bytes(raw), 64))
            raw[128:136] = b"AMIGA   "
            raw[136:152] = b"FILE FORGE HDF  "
            raw[152:156] = b"1.1 "
            struct.pack_into(">I", raw, 8, 0)
            total = sum(struct.unpack_from(">64I", raw)) & 0xFFFFFFFF
            struct.pack_into(">I", raw, 8, (-total) & 0xFFFFFFFF)
            handle.seek(0)
            handle.write(raw)

        table = self.table()
        self.assertTrue(table.legacy_layout)
        self.assertEqual(table.disk_vendor, "AMIGA")
        self.assertEqual(table.high_rdb_block, 3)
        self.assertEqual(len(table.partitions), 3)

        # Changing it puts it right.
        with BlockReader(self.image, writable=True) as reader:
            change_partition(reader, 1, {"name": "Work"})
        self.assertFalse(self.table().legacy_layout)
        self.assertEqual(self.image.read_bytes()[160:168], b"AMIGA   ")


class HandlerTests(TableFixture):
    def test_a_handler_is_carried_whole_and_found_by_its_dos_type(self) -> None:
        handler = FileSystemHandler(b"PFS\x03", HANDLER, handler_version(HANDLER))
        self.drive(handlers=[handler])

        carried = self.table().handlers
        self.assertEqual(len(carried), 1)
        self.assertEqual(carried[0].dos_type, b"PFS\x03")
        self.assertEqual(carried[0].version, (19 << 16) | 2)
        self.assertEqual(carried[0].seglist[: len(HANDLER)], HANDLER)
        self.assertEqual(carried[0].seglist[len(HANDLER):].strip(b"\0"), b"")
        self.assertEqual(self.table().filesystems[0]["version"], "19.2")

    def test_the_segment_list_field_names_the_first_lseg_block(self) -> None:
        self.drive(handlers=[FileSystemHandler(b"PFS\x03", HANDLER, 1)])
        data = self.image.read_bytes()
        header = long_at(data, 32)
        block = data[header * 512 : (header + 1) * 512]
        self.assertEqual(block[:4], b"FSHD")
        first = long_at(block, 72)
        self.assertEqual(data[first * 512 : first * 512 + 4], b"LSEG")

    def test_a_large_handler_reserves_the_cylinders_it_needs(self) -> None:
        handler = FileSystemHandler(b"SFS\x00", HANDLER + bytes(700 * 1024), 1)
        self.drive(handlers=[handler], partitions=[{"name": "DH0", "dosType": b"SFS\x00"}])
        table = self.table()
        needed = 1 + 1 + handler.blocks_needed
        self.assertGreaterEqual(table.partitions[0].start_block, needed)
        self.assertEqual(table.handlers[0].seglist[: len(handler.seglist)], handler.seglist)

    def test_a_handler_can_be_added_replaced_and_removed(self) -> None:
        self.drive()
        with BlockReader(self.image, writable=True) as reader:
            set_handler(reader, FileSystemHandler(b"PFS\x03", HANDLER, 1))
            set_handler(reader, FileSystemHandler(b"PDS\x03", HANDLER, 1))
            set_handler(reader, FileSystemHandler(b"PFS\x03", HANDLER, 2))
        kinds = {handler.dos_type: handler.version for handler in self.table().handlers}
        self.assertEqual(kinds, {b"PFS\x03": 2, b"PDS\x03": 1})

        with BlockReader(self.image, writable=True) as reader:
            remove_handler(reader, b"PDS\x03")
            with self.assertRaises(ConfigurationError):
                remove_handler(reader, b"SFS\x00")
        self.assertEqual([h.dos_type for h in self.table().handlers], [b"PFS\x03"])
        self.assertEqual(len(self.table().partitions), 3)


class TableEditingTests(TableFixture):
    def test_removing_a_partition_leaves_its_room_unused(self) -> None:
        self.drive()
        before = self.table().partitions
        with BlockReader(self.image, writable=True) as reader:
            remove_partition(reader, 1)

        table = self.table()
        self.assertEqual([part.name for part in table.partitions], ["DH0", "DH2"])
        self.assertEqual(
            table.free_ranges(), [(before[1].low_cylinder, before[1].high_cylinder)]
        )
        # The others have not moved.
        self.assertEqual(table.partitions[1].low_cylinder, before[2].low_cylinder)

    def test_removing_the_first_partition_does_not_give_its_room_to_the_table(self) -> None:
        self.drive()
        before = self.table().partitions[0]
        with BlockReader(self.image, writable=True) as reader:
            remove_partition(reader, 0)
        self.assertEqual(
            self.table().free_ranges(), [(before.low_cylinder, before.high_cylinder)]
        )
        # And it is still free once the table has been written and read again.
        with BlockReader(self.image, writable=True) as reader:
            change_partition(reader, 0, {"bootable": True})
        self.assertEqual(
            self.table().free_ranges(), [(before.low_cylinder, before.high_cylinder)]
        )

    def test_a_partition_is_added_where_there_is_room(self) -> None:
        self.drive()
        with BlockReader(self.image, writable=True) as reader:
            remove_partition(reader, 1)
            added = add_partition(reader, {"name": "Games", "dosType": b"PFS\x03", "sizeBytes": 4 * MIB})

        self.assertEqual(added.dos_type, b"PFS\x03")
        self.assertEqual(added.size_bytes, -(-4 * MIB // (16 * 63 * 512)) * 16 * 63 * 512)
        self.assertEqual(len(self.table().free_ranges()), 1)
        with BlockReader(self.image, writable=True) as reader:
            with self.assertRaises(ConfigurationError):
                add_partition(reader, {"name": "Huge", "sizeBytes": 60 * MIB})
            with self.assertRaises(ConfigurationError):
                add_partition(reader, {"name": "games"})

    def test_a_partition_is_renamed_without_moving(self) -> None:
        self.drive()
        before = self.table().partitions[1]
        with BlockReader(self.image, writable=True) as reader:
            changed = change_partition(
                reader, 1, {"name": "Work", "bootable": True, "bootPriority": 3}
            )
            with self.assertRaises(ConfigurationError):
                change_partition(reader, 1, {"name": "DH0"})
        self.assertEqual(changed.name, "Work")
        self.assertTrue(changed.bootable)
        self.assertEqual(changed.boot_priority, 3)
        self.assertEqual(changed.low_cylinder, before.low_cylinder)
        self.assertEqual(changed.high_cylinder, before.high_cylinder)

    def test_a_table_on_a_larger_drive_can_claim_the_rest_of_it(self) -> None:
        self.drive(size=32 * MIB, partitions=[{"name": "DH0", "sizeBytes": 8 * MIB}, {"name": "DH1"}])
        before = self.table()
        with self.image.open("r+b") as handle:
            handle.truncate(96 * MIB)
        with BlockReader(self.image, writable=True) as reader:
            extend_to_media(reader)

        table = self.table()
        self.assertEqual(table.cylinders, 96 * MIB // 512 // (16 * 63))
        self.assertEqual(
            [(part.low_cylinder, part.high_cylinder) for part in table.partitions],
            [(part.low_cylinder, part.high_cylinder) for part in before.partitions],
        )
        self.assertEqual(table.free_ranges(), [(before.cylinders, table.cylinders - 1)])

    def test_a_table_too_large_for_its_reserved_room_is_refused(self) -> None:
        self.drive()
        with BlockReader(self.image, writable=True) as reader:
            with self.assertRaises(ConfigurationError) as raised:
                set_handler(reader, FileSystemHandler(b"SFS\x00", bytes(600 * 1024), 1))
        self.assertIn("reserved", str(raised.exception))
        # Nothing was written: the table is as it was.
        self.assertEqual(len(self.table().partitions), 3)
        self.assertEqual(self.table().handlers, [])


if __name__ == "__main__":
    unittest.main()
