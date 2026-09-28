"""Drives and volumes of many gigabytes.

Everything here is made as a sparse file, so a drive of 128 GB costs the test
machine a few megabytes. That is also the property under test: a formatter
that wrote every block, or built the image in memory first, would make these
tests take minutes and the working storage overflow.

The volumes are written to and read back through the ordinary mounts, and
checked block by block by each filing system's own validator. Where
``AMITOOLS_PATH`` names a checkout of amitools, the partition table and the
FFS volumes are read by that independent implementation as well.
"""

from __future__ import annotations

import json
import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amiganut.disc.cli import main as adisc
from amiganut.disc.mount import mount_image
from amiganut.errors import ConfigurationError, DataError
from amiganut.filesystem.amigados import format_volume
from amiganut.filesystem.blocks import BlockReader
from amiganut.filesystem.drive import (
    add_formatted_partition,
    allocate_image,
    create_drive,
    create_volume,
    dos_type_for,
    handlers_for,
    initialise_drive,
    make_handler,
    missing_handlers,
    needs_handler,
    reformat_partition,
)
from amiganut.filesystem.rdb import read_rigid_disk

from tests import amitools_reference

MIB = 1024 * 1024
GIB = 1024 * MIB

HANDLER = (
    struct.pack(">6I", 0x3F3, 0, 1, 0, 0, 64)
    + struct.pack(">2I", 0x3E9, 64)
    + b"$VER: Professional-File-System-III 19.2 test".ljust(256, b"\0")
    + struct.pack(">I", 0x3F2)
)


def allocated(path: Path) -> int:
    return path.stat().st_blocks * 512


class LargeFixture(unittest.TestCase):
    def setUp(self) -> None:
        fsync = patch("amiganut.filesystem.blocks.os.fsync")
        fsync.start()
        self.addCleanup(fsync.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.image = self.folder / "drive.hdf"
        probe = self.folder / "probe"
        allocate_image(probe, 64 * MIB)
        if allocated(probe) > MIB:
            self.skipTest("the temporary directory cannot hold sparse files")
        probe.unlink()

    def exercise(self, partition: int | None = None) -> None:
        """Write a small tree, read it back and validate the volume."""
        mount, _name = mount_image(self.image, writable=True, partition=partition)
        try:
            mount.mkdir("Devs")
            mount.mkdir("Devs/Keymaps")
            payload = os.urandom(70_000)
            for number in range(12):
                mount.write_bytes(f"Devs/file{number}", payload[number:])
            mount.write_bytes("Devs/Keymaps/gb", b"keymap" * 300)
        finally:
            mount.close()
        mount, _name = mount_image(self.image, partition=partition)
        try:
            self.assertEqual(mount.read_bytes("Devs/file3"), payload[3:])
            self.assertEqual(mount.read_bytes("Devs/Keymaps/gb"), b"keymap" * 300)
            self.assertEqual(mount.validate(), [])
        finally:
            mount.close()


class LargeFastFileSystemTests(LargeFixture):
    def test_a_volume_past_fifty_megabytes_gets_bitmap_extension_blocks(self) -> None:
        """The root block names 25 bitmap blocks, which cover about 50 MB."""
        allocate_image(self.image, 60 * MIB)
        with BlockReader(self.image, writable=True) as reader:
            volume = format_volume(reader, label="Sixty", dos_type=b"DOS\x03")
            pages = (reader.total_blocks - 2 + 4063) // 4064
            self.assertEqual(len(volume._load_bitmap()), reader.total_blocks - 2)
            self.assertEqual(len(volume._bitmap_blocks), pages)
            root = reader.read_block(volume.root_block)
            extension = struct.unpack_from(">I", root, 512 - 96)[0]
            self.assertEqual(extension, volume.root_block + 1 + pages)
            self.assertEqual(
                struct.unpack_from(">I", reader.read_block(extension), 0)[0],
                volume.root_block + 1 + 25,
            )
        self.exercise()

    def test_a_volume_of_gigabytes_is_formatted_without_writing_all_of_it(self) -> None:
        allocate_image(self.image, 3 * GIB)
        with BlockReader(self.image, writable=True) as reader:
            volume = format_volume(reader, label="Big", dos_type=b"DOS\x07")
            # 1549 bitmap blocks: 25 named by the root block, and the rest by
            # twelve extension blocks of 127 each.
            volume._load_bitmap()
            self.assertEqual(len(volume._bitmap_blocks), 1549)
            used = 1 + 1549 + 12
            self.assertEqual(volume.free_bytes(), (reader.total_blocks - 2 - used) * 512)
        self.assertLess(allocated(self.image), 4 * MIB)
        self.exercise()

    def test_a_floppy_is_still_cleared_from_end_to_end(self) -> None:
        self.image.write_bytes(b"\xa5" * 880 * 1024)
        with BlockReader(self.image, writable=True) as reader:
            format_volume(reader, label="Floppy", dos_type=b"DOS\x00")
        data = self.image.read_bytes()
        self.assertNotIn(b"\xa5", data)
        mount, _name = mount_image(self.image, writable=True)
        try:
            mount.write_bytes("Startup-Sequence", b"Echo hello\n")
            self.assertEqual(mount.validate(), [])
        finally:
            mount.close()

    def test_an_unformatted_partition_is_refused_without_reading_all_of_it(self) -> None:
        allocate_image(self.image, 2 * GIB)
        with self.image.open("r+b") as handle:
            handle.write(b"DOS\x03")
        reads = 0
        original = BlockReader.read_block

        def counting(reader, number):
            nonlocal reads
            reads += 1
            return original(reader, number)

        with patch.object(BlockReader, "read_block", counting):
            with self.assertRaises(DataError):
                mount_image(self.image, filesystem="ffs")
        self.assertLess(reads, 64)

    @unittest.skipUnless(amitools_reference.AVAILABLE, "AMITOOLS_PATH is not set")
    def test_amitools_reads_a_volume_with_bitmap_extension_blocks(self) -> None:
        allocate_image(self.image, 300 * MIB)
        with BlockReader(self.image, writable=True) as reader:
            format_volume(reader, label="Reference", dos_type=b"DOS\x03")
        self.exercise()
        volume, blkdev = amitools_reference.open_volume(self.image)
        try:
            self.assertEqual(len(volume.bitmap.bitmap_blks), 152)
            self.assertEqual(len(volume.bitmap.ext_blks), 1)
            self.assertGreater(volume.get_free_blocks(), 600_000)
        finally:
            volume.close()
            blkdev.close()


class LargeVolumeTests(LargeFixture):
    def test_a_professional_volume_of_a_hundred_gigabytes(self) -> None:
        dos_type = create_volume(self.image, 120 * GIB, "pfs3", "Huge")
        self.assertEqual(dos_type, b"PFS\x03")
        self.assertEqual(self.image.stat().st_size, 120 * GIB)
        self.assertLess(allocated(self.image), 64 * MIB)
        self.exercise()

    def test_a_smart_volume_of_a_hundred_gigabytes(self) -> None:
        create_volume(self.image, 100 * GIB, "sfs", "Huge")
        self.assertLess(allocated(self.image), 64 * MIB)
        self.exercise()

    def test_each_filing_system_refuses_what_it_cannot_describe(self) -> None:
        with self.assertRaises(ConfigurationError) as raised:
            create_volume(self.image, 200 * GIB, "sfs", "TooBig")
        self.assertIn("127 GB", str(raised.exception))
        self.assertFalse(self.image.exists())
        with self.assertRaises(ConfigurationError):
            create_volume(self.image, 1800 * GIB, "pfs3", "TooBig")
        with self.assertRaises(ConfigurationError):
            dos_type_for("ntfs")


class LargeDriveTests(LargeFixture):
    LAYOUT = [
        {"name": "DH0", "label": "System", "filesystem": "pfs3", "sizeBytes": 2 * GIB, "bootable": True},
        {"name": "DH1", "label": "Classic", "filesystem": "ffs-intl", "sizeBytes": GIB},
        {"name": "DH2", "label": "Work", "filesystem": "pfs3", "sizeBytes": 60 * GIB},
        {"name": "DH3", "label": "Games", "filesystem": "pds3"},
    ]

    def test_a_card_of_128_gigabytes_with_four_partitions(self) -> None:
        disk, formatted = create_drive(
            self.image, 128 * GIB, self.LAYOUT, handlers=[make_handler("pfs3", HANDLER)]
        )

        self.assertEqual(self.image.stat().st_size, 128 * GIB)
        self.assertLess(allocated(self.image), 64 * MIB)
        self.assertEqual((disk.heads, disk.sectors), (16, 128))
        self.assertEqual(disk.cylinders, 128 * 1024)
        self.assertEqual(
            [(part.name, part.format) for part in disk.partitions],
            [("DH0", "PFS3"), ("DH1", "FFS-INTL"), ("DH2", "PFS3"), ("DH3", "PFS3")],
        )
        self.assertEqual([item.label for item in formatted], ["System", "Classic", "Work", "Games"])
        self.assertEqual(disk.partitions[3].high_cylinder, disk.cylinders - 1)
        self.assertTrue(disk.partitions[0].bootable)
        # One handler file is recorded for each DOS type that asks for it.
        self.assertEqual(
            sorted(handler.dos_type for handler in disk.handlers), [b"PDS\x03", b"PFS\x03"]
        )
        for index, label in enumerate(("System", "Classic", "Work", "Games")):
            mount, _name = mount_image(self.image, partition=index)
            try:
                self.assertEqual(mount.title, label)
            finally:
                mount.close()
            self.exercise(index)

    def test_partitions_that_do_not_fit_are_refused_rather_than_shrunk(self) -> None:
        with self.assertRaises(ConfigurationError):
            create_drive(
                self.image,
                4 * GIB,
                [
                    {"name": "DH0", "filesystem": "pfs3", "sizeBytes": 3 * GIB},
                    {"name": "DH1", "filesystem": "pfs3", "sizeBytes": 3 * GIB},
                ],
            )
        self.assertFalse(self.image.exists())

    def test_device_names_are_checked(self) -> None:
        for names in (("DH0", "dh0"), ("Work Disk",), ("DH0:",)):
            with self.subTest(names=names):
                with self.assertRaises(ConfigurationError):
                    create_drive(
                        self.image,
                        GIB,
                        [{"name": name, "filesystem": "ffs-intl", "sizeBytes": 64 * MIB} for name in names],
                    )

    def test_a_used_card_loses_what_described_it_before(self) -> None:
        """An old partition table at the start, and a GPT's copy at the end."""
        allocate_image(self.image, 8 * GIB)
        with self.image.open("r+b") as handle:
            handle.seek(510)
            handle.write(b"\x55\xaa")
            handle.seek(512)
            handle.write(b"EFI PART")
            handle.seek(8 * GIB - 512)
            handle.write(b"EFI PART")
        initialise_drive(self.image, [{"name": "DH0", "label": "All", "filesystem": "pfs3"}])
        with self.image.open("rb") as handle:
            self.assertEqual(handle.read(4), b"RDSK")
            handle.seek(510)
            self.assertEqual(handle.read(2), bytes(2))
            self.assertEqual(handle.read(4), b"PART")
            handle.seek(8 * GIB - 512)
            self.assertEqual(handle.read(8), bytes(8))
        self.exercise(0)

    def test_a_partition_is_added_in_unused_room_and_formatted(self) -> None:
        create_drive(
            self.image,
            16 * GIB,
            [{"name": "DH0", "label": "System", "filesystem": "ffs-intl", "sizeBytes": GIB}],
        )
        added = add_formatted_partition(
            self.image,
            {"name": "DH1", "label": "Work", "filesystem": "pfs3"},
            handlers=[make_handler("pfs3", HANDLER)],
        )
        self.assertEqual(added.partition.name, "DH1")
        self.assertGreater(added.partition.size_bytes, 14 * GIB)
        with BlockReader(self.image) as reader:
            disk = read_rigid_disk(reader)
        self.assertEqual([handler.dos_type for handler in disk.handlers], [b"PFS\x03"])
        self.assertEqual(disk.free_ranges(), [])
        self.exercise(0)
        self.exercise(1)

    def test_a_partition_is_reformatted_in_another_filing_system(self) -> None:
        create_drive(
            self.image,
            8 * GIB,
            [
                {"name": "DH0", "label": "System", "filesystem": "ffs-intl", "sizeBytes": GIB},
                {"name": "DH1", "label": "Work", "filesystem": "ffs-intl", "sizeBytes": 2 * GIB},
            ],
        )
        self.exercise(1)
        changed = reformat_partition(self.image, 1, "Smart", "sfs")
        self.assertEqual(changed.partition.dos_type, b"SFS\x00")
        mount, _name = mount_image(self.image, partition=1)
        try:
            self.assertEqual(mount.title, "Smart")
            self.assertEqual(list(mount.iter_entries("")), [])
        finally:
            mount.close()
        self.exercise(1)
        # The other partition was not touched.
        mount, _name = mount_image(self.image, partition=0)
        try:
            self.assertEqual(mount.title, "System")
        finally:
            mount.close()

    @unittest.skipUnless(amitools_reference.AVAILABLE, "AMITOOLS_PATH is not set")
    def test_amitools_reads_the_partition_table(self) -> None:
        from amitools.fs.blkdev.RawBlockDevice import RawBlockDevice
        from amitools.fs.rdb.RDisk import RDisk

        create_drive(
            self.image, 128 * GIB, self.LAYOUT, handlers=[make_handler("pfs3", HANDLER)]
        )
        raw = RawBlockDevice(str(self.image), read_only=True)
        raw.open()
        try:
            disk = RDisk(raw)
            self.assertTrue(disk.open())
            self.assertEqual(disk.get_num_partitions(), 4)
            self.assertEqual(len(disk.fs), 2)
            info = "\n".join(disk.get_info())
            self.assertIn("heads=16 sectors=128", info)
            self.assertIn("cyl_blks=2048", info)
        finally:
            raw.close()


class WriteOrderTests(LargeFixture):
    """What is cleared before a volume is formatted must not arrive after it.

    A formatter for PFS3 or SFS opens the drive through a handle of its own.
    Zeros written through another handle and left in its buffer reach the file
    when that handle is closed, which is after the volume has been written,
    and take its root block with them. Whether that happens depends on how
    much the interpreter buffers, so these tests give every handle a buffer
    larger than anything written here.
    """

    def setUp(self) -> None:
        super().setUp()
        real_open = Path.open

        def buffered(path, mode="r", buffering=-1, *args, **kwargs):
            if "b" in mode and buffering == -1:
                buffering = 4 * MIB
            return real_open(path, mode, buffering, *args, **kwargs)

        opening = patch.object(Path, "open", buffered)
        opening.start()
        self.addCleanup(opening.stop)

    def test_every_partition_of_a_new_drive_keeps_its_root_block(self) -> None:
        for filesystem in ("pfs3", "sfs", "ffs-intl"):
            with self.subTest(filesystem=filesystem):
                create_drive(self.image, 4 * GIB, [
                    {"name": "DH0", "label": "First", "filesystem": filesystem, "sizeBytes": GIB},
                    {"name": "DH1", "label": "Second", "filesystem": filesystem, "sizeBytes": GIB},
                ])
                for index, label in enumerate(("First", "Second")):
                    mount, _name = mount_image(self.image, partition=index)
                    try:
                        self.assertEqual(mount.title, label)
                        self.assertEqual(mount.validate(), [])
                    finally:
                        mount.close()
                self.image.unlink()

    def test_a_volume_across_a_whole_card_keeps_both_ends(self) -> None:
        for filesystem in ("pfs3", "sfs"):
            with self.subTest(filesystem=filesystem):
                allocate_image(self.image, 2 * GIB)
                create_volume(self.image, None, filesystem, "Whole")
                mount, _name = mount_image(self.image)
                try:
                    self.assertEqual(mount.title, "Whole")
                    self.assertEqual(mount.validate(), [])
                finally:
                    mount.close()
                with self.image.open("rb") as handle:
                    first = handle.read(4)
                    handle.seek(2 * GIB - 512)
                    last = handle.read(4)
                # The Smart File System keeps a second root block at the end.
                if filesystem == "sfs":
                    self.assertEqual(last, first)
                self.image.unlink()

    def test_a_reformatted_partition_keeps_its_root_block(self) -> None:
        create_drive(self.image, 4 * GIB, [
            {"name": "DH0", "label": "First", "filesystem": "ffs-intl", "sizeBytes": GIB},
        ])
        for filesystem in ("pfs3", "sfs", "ffs-intl"):
            with self.subTest(filesystem=filesystem):
                reformat_partition(self.image, 0, "Again", filesystem)
                mount, _name = mount_image(self.image, partition=0)
                try:
                    self.assertEqual(mount.title, "Again")
                    self.assertEqual(mount.validate(), [])
                finally:
                    mount.close()


class HandlerChoiceTests(unittest.TestCase):
    def test_kickstart_carries_only_the_fast_file_system(self) -> None:
        for name in ("ofs", "ffs", "ffs-intl", "ffs-dc"):
            self.assertFalse(needs_handler(dos_type_for(name)), name)
        for name in ("ffs-lnfs", "pfs3", "pds3", "sfs"):
            self.assertTrue(needs_handler(dos_type_for(name)), name)

    def test_one_professional_handler_serves_both_of_its_dos_types(self) -> None:
        partitions = [{"dosType": b"PFS\x03"}, {"dosType": b"PDS\x03"}, {"dosType": b"SFS\x00"}]
        supplied = [make_handler("pfs3", HANDLER)]
        chosen = handlers_for(partitions, supplied)
        self.assertEqual([handler.dos_type for handler in chosen], [b"PFS\x03", b"PDS\x03"])
        self.assertEqual(missing_handlers(partitions, supplied), [b"SFS\x00"])

    def test_a_file_that_is_not_a_program_is_not_a_handler(self) -> None:
        with self.assertRaises(DataError):
            make_handler("pfs3", b"PK\x03\x04 this is an archive")


class CommandLineTests(LargeFixture):
    def test_a_drive_is_created_from_a_layout_file(self) -> None:
        (self.folder / "pfs3aio").write_bytes(HANDLER)
        layout = self.folder / "layout.json"
        layout.write_text(json.dumps({
            "size": "128GB",
            "partitions": [
                {"name": "DH0", "label": "System", "filesystem": "pfs3", "size": "2GB", "bootable": True},
                {"name": "DH1", "label": "Work", "filesystem": "pfs3"},
            ],
            "handlers": [{"filesystem": "pfs3", "path": "pfs3aio"}],
        }))
        self.assertEqual(adisc(["create", "--layout", str(layout), str(self.image)]), 0)
        with BlockReader(self.image) as reader:
            disk = read_rigid_disk(reader)
        self.assertEqual(self.image.stat().st_size, 128 * GIB)
        self.assertEqual([part.name for part in disk.partitions], ["DH0", "DH1"])
        self.assertEqual(disk.partitions[0].size_bytes, 2 * GIB)
        self.assertEqual(len(disk.handlers), 1)

    def test_sizes_are_read_with_fractions_and_larger_units(self) -> None:
        self.assertEqual(
            adisc(["create", "--filesystem", "pfs3", "--geometry", "capacity=1.5GB",
                   "--title", "Half", str(self.image)]),
            0,
        )
        self.assertEqual(self.image.stat().st_size, 3 * GIB // 2)
        self.exercise()

    def test_a_single_partition_drive_is_no_longer_built_in_memory(self) -> None:
        self.assertEqual(
            adisc(["create", "--filesystem", "rdb", "--variant", "FFS-INTL",
                   "--geometry", "capacity=2GB", str(self.image)]),
            0,
        )
        self.assertLess(allocated(self.image), 4 * MIB)
        self.exercise(0)


if __name__ == "__main__":
    unittest.main()
