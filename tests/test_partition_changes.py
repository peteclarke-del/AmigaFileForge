"""Changing files inside one partition of a hard drive.

The engine's command line addresses a whole image, and a drive with a Rigid
Disk Block has no single volume to act on, so deleting, renaming and making
drawers inside a partition used to fail however the partition was formatted.
These run through the partition's own mount instead, for FFS and SFS alike.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amiganut.filesystem.blocks import BlockReader
from amiganut.filesystem.rdb import partition_reader, read_rigid_disk, write_rigid_disk
from amiganut.filesystem.sfs_write import format_sfs_volume
from app.disk_service import DiskService
from app.errors import DiskError


class PartitionChangeTests(unittest.TestCase):
    def setUp(self) -> None:
        fsync = patch("amiganut.filesystem.blocks.os.fsync")
        fsync.start()
        self.addCleanup(fsync.stop)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.service = DiskService(Path(self.folder.name) / "work")

    def sfs_drive(self):
        image = Path(self.folder.name) / "sfs.hdf"
        with image.open("wb") as handle:
            handle.truncate(24 * 1024 * 1024)
        with BlockReader(image, writable=True) as reader:
            write_rigid_disk(reader, [{"name": "DH0", "dosType": b"SFS\x00", "sizeBytes": 20 * 1024 * 1024}])
            window = partition_reader(reader, read_rigid_disk(reader).partitions[0])
            format_sfs_volume(window, label="Data", block_size=1024)
            window.close()
        return self.service.create_from_path(image)

    def ffs_drive(self):
        return self.service.create_blank("ffs-hard", "Work", "20MB")

    def exercise(self, session) -> None:
        self.service.select_partition(session, 0)
        self.service.make_directory(session, "Games")
        self.service.mutate(session, ["mkdir", "-p", "{image}:Games/Deep/Deeper"])
        source = Path(self.folder.name) / "loader"
        source.write_bytes(b"loader bytes")
        self.service.put(session, "Games/Loader", source)
        self.service.mutate(session, ["mv", "", "{image}:Games/Loader", "Games/Deep/Renamed"])
        self.assertEqual(self.service.read_file(session, "Games/Deep/Renamed"), b"loader bytes")
        with self.assertRaises(DiskError):
            self.service.mutate(session, ["rm", "{image}:Games"])
        self.service.mutate(session, ["rm", "--force", "--recursive", "{image}:Games"])
        with self.ffs_mount(session) as mount:
            self.assertFalse(mount.exists("Games"))
        self.assertEqual(self.service.validate(session), "No structural errors found")

    def ffs_mount(self, session):
        return self.service.ffs_mount(session)

    def test_an_sfs_partition_takes_every_kind_of_change(self) -> None:
        self.exercise(self.sfs_drive())

    def test_an_ffs_partition_takes_every_kind_of_change(self) -> None:
        self.exercise(self.ffs_drive())

    def test_an_unknown_command_is_refused_rather_than_sent_to_the_whole_drive(self) -> None:
        session = self.sfs_drive()
        self.service.select_partition(session, 0)
        with self.assertRaises(DiskError):
            self.service.mutate(session, ["format", "{image}:"])


if __name__ == "__main__":
    unittest.main()
