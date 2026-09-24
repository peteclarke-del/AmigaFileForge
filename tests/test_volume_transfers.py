"""Copying files between two open volumes, as dragging between panes does.

The copy used to go to the engine's command line whenever the target was a
partition of a hard drive, which addresses the whole drive and so failed on
its partition table, and the direct path for single volumes called a method
that did not exist. These drive both, including from an SFS partition, whose
longer names an FFS target cannot always hold.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amiganut.filesystem.blocks import BlockReader
from amiganut.filesystem.rdb import partition_reader, read_rigid_disk, write_rigid_disk
from amiganut.filesystem.sfs_write import format_sfs_volume
from app.amiganut_internals import collect_copy_items
from app.disk_service import DiskService
from app.errors import DiskError

MIB = 1024 * 1024


class VolumeTransferTests(unittest.TestCase):
    def setUp(self) -> None:
        fsync = patch("amiganut.filesystem.blocks.os.fsync")
        fsync.start()
        self.addCleanup(fsync.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.service = DiskService(self.folder / "work")

    def sfs_drive(self):
        image = self.folder / "sfs.hdf"
        with image.open("wb") as handle:
            handle.truncate(24 * MIB)
        with BlockReader(image, writable=True) as reader:
            write_rigid_disk(reader, [{"name": "DH0", "dosType": b"SFS\x00", "sizeBytes": 20 * MIB}])
            window = partition_reader(reader, read_rigid_disk(reader).partitions[0])
            format_sfs_volume(window, label="Data", block_size=1024)
            window.close()
        session = self.service.create_from_path(image)
        self.service.select_partition(session, 0)
        return session

    def ffs_drive(self, size: str = "20MB"):
        session = self.service.create_blank("ffs-hard", "Work", size)
        self.service.select_partition(session, 0)
        return session

    def put(self, session, path: str, data: bytes) -> None:
        source = self.folder / "upload"
        source.write_bytes(data)
        self.service.put(session, path, source)

    def fill(self, session) -> None:
        self.service.make_directory(session, "Games")
        self.service.make_directory(session, "Games/Deep")
        for index in range(20):
            self.put(session, f"Games/Deep/file{index:02d}", bytes([index]) * (index * 700))
        self.put(session, "Games/ReadMe", b"read me")

    def test_a_tree_copies_from_an_sfs_partition_into_an_ffs_partition(self) -> None:
        source, target = self.sfs_drive(), self.ffs_drive()
        self.fill(source)
        reported = []

        self.service.copy(source, "Games", target, "Games", True, progress=lambda *args: reported.append(args))

        for index in range(20):
            self.assertEqual(
                self.service.read_file(target, f"Games/Deep/file{index:02d}"),
                bytes([index]) * (index * 700),
            )
        self.assertEqual(len(reported), 21)
        self.assertEqual(self.service.validate(target), "No structural errors found")

    def test_a_name_the_target_cannot_hold_stops_the_copy_before_it_starts(self) -> None:
        source, target = self.sfs_drive(), self.ffs_drive()
        self.fill(source)
        self.put(source, "Games/A name well beyond thirty characters", b"x")

        with self.assertRaises(DiskError) as caught:
            self.service.copy(source, "Games", target, "Games", True)

        self.assertIn("Nothing was copied", str(caught.exception))
        with self.service.ffs_mount(target) as mount:
            self.assertFalse(mount.exists("Games"))

    def test_a_copy_too_large_for_the_target_stops_before_it_starts(self) -> None:
        source, target = self.sfs_drive(), self.ffs_drive("1MB")
        self.service.make_directory(source, "Big")
        for index in range(4):
            self.put(source, f"Big/part{index}", bytes(range(256)) * 2048)

        with self.assertRaises(DiskError) as caught:
            self.service.copy(source, "Big", target, "Big", True)

        self.assertIn("Nothing was copied", str(caught.exception))
        with self.service.ffs_mount(target) as mount:
            self.assertFalse(mount.exists("Big"))

    def two_partition_drive(self):
        image = self.folder / "two.hdf"
        with image.open("wb") as handle:
            handle.truncate(40 * MIB)
        with BlockReader(image, writable=True) as reader:
            write_rigid_disk(reader, [
                {"name": "DH0", "dosType": b"SFS\x00", "sizeBytes": 16 * MIB},
                {"name": "DH1", "dosType": b"SFS\x00", "sizeBytes": 16 * MIB},
            ])
            for partition in read_rigid_disk(reader).partitions:
                window = partition_reader(reader, partition)
                format_sfs_volume(window, label=partition.name, block_size=1024)
                window.close()
        return self.service.create_from_path(image)

    def test_a_whole_partition_copies_into_another_on_the_same_drive(self) -> None:
        drive = self.two_partition_drive()
        self.service.select_partition(drive, 0)
        self.fill(drive)

        # The two panes share one session; the source's partition is named
        # explicitly and the target's is the one selected.
        self.service.select_partition(drive, 1)
        self.service.copy(drive, "", drive, "FromDH0", True, source_partition=0)

        self.assertEqual(self.service.read_file(drive, "FromDH0/Games/ReadMe"), b"read me")
        self.service.select_partition(drive, 0)
        with self.service.ffs_mount(drive) as mount:
            self.assertFalse(mount.exists("FromDH0"))

    def test_a_whole_partition_copies_into_the_root_of_another(self) -> None:
        source, target = self.sfs_drive(), self.ffs_drive()
        self.fill(source)

        self.service.copy(source, "", target, "", True)

        self.assertEqual(self.service.read_file(target, "Games/ReadMe"), b"read me")

    def test_a_file_that_would_be_replaced_stops_the_copy_before_it_starts(self) -> None:
        source, target = self.sfs_drive(), self.ffs_drive()
        self.fill(source)
        self.service.make_directory(target, "Games")
        self.put(target, "Games/ReadMe", b"keep me")

        with self.assertRaises(DiskError) as caught:
            self.service.copy(source, "", target, "", True)

        self.assertIn("already exists", str(caught.exception))
        self.assertEqual(self.service.read_file(target, "Games/ReadMe"), b"keep me")
        with self.service.ffs_mount(target) as mount:
            self.assertFalse(mount.exists("Games/Deep"))

    def test_collecting_a_copy_can_leave_the_file_data_unread(self) -> None:
        source = self.sfs_drive()
        self.fill(source)
        with self.service.ffs_mount(source) as mount:
            items = collect_copy_items(mount, "Games", recursive=True, load_data=False)
        files = [item for item in items if item["kind"] == "file"]
        self.assertTrue(files)
        self.assertTrue(all("data" not in item for item in files))
        self.assertEqual(
            sorted(item["size"] for item in files),
            sorted([index * 700 for index in range(20)] + [7]),
        )


if __name__ == "__main__":
    unittest.main()
