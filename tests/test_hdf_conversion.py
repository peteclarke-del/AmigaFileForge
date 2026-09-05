"""Converting a hard drive between its two shapes.

An Amiga hard drive exists in one of two forms, and both are called ``.hdf``.
One carries a Rigid Disk Block and describes its own geometry; the other is a
bare hardfile that holds a single volume and nothing else, so the host has to
be told the geometry separately. Software in the wild uses both, and these
tests cover converting either way without losing the volume.
"""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from amiganut.filesystem.blocks import BlockReader
from amiganut.filesystem.rdb import read_rigid_disk
from app.disk_service import DiskService
from app.hardfile_geometry import parse_geometry


class HardDriveConversionTests(unittest.TestCase):
    def test_a_bare_hardfile_can_be_given_a_rigid_disk_block(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(Path(folder) / "work")
            bare = service.create_blank("ffs-physical", "SYSTEM", "8MB")
            self.assertEqual(bare.kind, "ffs")

            offered = {row["format"] for row in service.export_formats(bare)}
            self.assertIn("rdb", offered)
            # It has no partition table yet, so there is nothing to strip.
            self.assertNotIn("hardfile", offered)

            output, _name = service.export_image(bare, "rdb")

            # The result is a drive that describes itself, and reopening it
            # finds the partition table rather than a bare volume.
            self.assertEqual(service.identify_kind(output, "hdf"), "hdf")
            reader = BlockReader(output)
            try:
                disk = read_rigid_disk(reader)
                self.assertEqual(len(disk.partitions), 1)
                partition = disk.partitions[0]
                self.assertTrue(partition.bootable)
                self.assertEqual(partition.dos_type[:3], b"DOS")
                # The volume's own bytes moved across untouched: its boot
                # block now sits at the partition's first block.
                self.assertEqual(
                    reader.read_block(partition.start_block)[:4],
                    bare.path.read_bytes()[:4],
                )
            finally:
                reader.close()

    def test_a_partitioned_drive_exports_one_partition_as_a_hardfile(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(Path(folder) / "work")
            drive = service.create_blank("ffs-hard", "WORKBENCH", "20MB")
            self.assertEqual(drive.kind, "hdf")

            offered = {row["format"] for row in service.export_formats(drive)}
            self.assertIn("hardfile", offered)
            # It already has a partition table, so there is none to add.
            self.assertNotIn("rdb", offered)

            output, _name = service.export_image(drive, "hardfile")
            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
                data_name = next(name for name in names if name.endswith(".hdf"))
                geo_name = next(name for name in names if name.endswith(".geo"))
                payload = archive.read(data_name)
                geometry = parse_geometry(archive.read(geo_name).decode("latin-1"))

            # Both files travel together under the directory the firmware
            # expects, and share a base name so the pair stays matched.
            self.assertTrue(all(name.startswith("Hardfile0/") for name in names))
            self.assertEqual(Path(data_name).stem, Path(geo_name).stem)

            # The volume came out whole, and the geometry multiplies back to
            # exactly its size, which is what an emulator checks before it
            # will accept the pair.
            self.assertEqual(payload[:3], b"DOS")
            self.assertEqual(
                geometry["surfaces"]
                * geometry["blocks_per_track"]
                * geometry["cylinders"]
                * geometry["block_size"],
                len(payload),
            )

    def test_a_floppy_is_offered_neither_conversion(self) -> None:
        """Neither shape applies to a disk that is not a hard drive."""
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(Path(folder) / "work")
            floppy = service.create_blank("ffs-intl", "GAMES")
            offered = {row["format"] for row in service.export_formats(floppy)}
            self.assertEqual(offered & {"rdb", "hardfile"}, set())
            self.assertIn("adz", offered)


if __name__ == "__main__":
    unittest.main()
