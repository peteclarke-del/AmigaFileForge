"""Volumes other than FFS, opened as images, as partitions and as drives.

An SFS or PFS3 volume can sit alone in a file, as a partitionless emulator
hardfile or a memory card does, or inside one partition of a drive. Either
way the workbench has to choose the driver from the volume's own signature,
take its name limit from the volume, and keep the repairs that only make
sense for AmigaDOS blocks away from it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amiganut.filesystem.blocks import BlockReader
from amiganut.filesystem.pfs3_write import format_pfs3_volume
from amiganut.filesystem.rdb import partition_reader, read_rigid_disk, write_rigid_disk
from amiganut.filesystem.sfs_write import format_sfs_volume
from app.disk_service import DiskService
from app.errors import DiskError

LONG_NAME = "A drawer name far longer than thirty characters"


class VolumeFilesystemTests(unittest.TestCase):
    def setUp(self) -> None:
        fsync = patch("amiganut.filesystem.blocks.os.fsync")
        fsync.start()
        self.addCleanup(fsync.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.service = DiskService(self.folder / "work")

    def bare_sfs(self, name: str = "card.hdf") -> Path:
        image = self.folder / name
        with image.open("wb") as handle:
            handle.truncate(20 * 1024 * 1024)
        with BlockReader(image, writable=True) as reader:
            format_sfs_volume(reader, label="Card", block_size=512)
        return image

    def bare_pfs3(self, name: str = "pfs.hdf") -> Path:
        image = self.folder / name
        with image.open("wb") as handle:
            handle.truncate(20 * 1024 * 1024)
        with BlockReader(image, writable=True) as reader:
            format_pfs3_volume(reader, label="Work", name_length=106)
        return image

    def drive(self, partitions: list[dict]) -> Path:
        image = self.folder / "drive.hdf"
        with image.open("wb") as handle:
            handle.truncate(48 * 1024 * 1024)
        with BlockReader(image, writable=True) as reader:
            write_rigid_disk(reader, partitions)
            for partition in read_rigid_disk(reader).partitions:
                if partition.dos_type == b"SFS\x00":
                    window = partition_reader(reader, partition)
                    format_sfs_volume(window, label=partition.name, block_size=1024)
                    window.close()
                elif partition.dos_type == b"PFS\x03":
                    window = partition_reader(reader, partition)
                    format_pfs3_volume(window, label=partition.name)
                    window.close()
        return image

    def test_a_bare_sfs_hardfile_opens_as_one_volume_with_its_own_name_limit(self) -> None:
        session = self.service.create_from_path(self.bare_sfs())

        self.assertEqual(session.kind, "ffs")
        self.assertEqual(session.ffs_capabilities["map"], "sfs")
        self.assertEqual(session.ffs_capabilities["nameLimit"], 100)
        # SFS records its own geometry, so no GEO sidecar is asked for.
        self.service.make_directory(session, LONG_NAME)
        source = self.folder / "loader"
        source.write_bytes(b"loader bytes")
        self.service.put(session, f"{LONG_NAME}/Loader", source)
        self.service.mutate(session, ["mv", "", f"{{image}}:{LONG_NAME}/Loader", "Loader"])

        self.assertEqual(self.service.read_file(session, "Loader"), b"loader bytes")
        self.assertEqual(self.service.validate(session), "No structural errors found")

    def test_a_bare_pfs3_hardfile_opens_with_the_name_length_it_was_formatted_for(self) -> None:
        session = self.service.create_from_path(self.bare_pfs3())

        self.assertEqual(session.kind, "ffs")
        self.assertEqual(session.ffs_capabilities["map"], "pfs3")
        self.assertEqual(session.ffs_capabilities["nameLimit"], 106)
        self.service.make_directory(session, LONG_NAME)
        source = self.folder / "loader"
        source.write_bytes(b"loader bytes")
        self.service.put(session, f"{LONG_NAME}/Loader", source)
        self.service.mutate(session, ["mv", "", f"{{image}}:{LONG_NAME}/Loader", "Loader"])

        self.assertEqual(self.service.read_file(session, "Loader"), b"loader bytes")
        self.assertEqual(self.service.validate(session), "No structural errors found")

    def test_the_amigados_repairs_never_touch_an_sfs_or_pfs3_volume(self) -> None:
        for image in (self.bare_sfs(), self.bare_pfs3()):
            session = self.service.create_from_path(image)
            before = session.path.read_bytes()

            self.assertEqual(self.service._finalise_hardfile_directories(session), 0)
            self.assertFalse(self.service._advance_hardfile_disc_id(session))
            self.assertEqual(session.path.read_bytes(), before)

    def test_a_partition_is_described_by_its_own_filing_system(self) -> None:
        session = self.service.create_from_path(self.drive([
            {"name": "DH0", "dosType": b"DOS\x03", "sizeBytes": 8 * 1024 * 1024},
            {"name": "DH1", "dosType": b"SFS\x00", "sizeBytes": 16 * 1024 * 1024},
            {"name": "DH2", "dosType": b"PFS\x03", "sizeBytes": 16 * 1024 * 1024},
        ]))

        self.service.select_partition(session, 1)
        self.assertEqual(session.ffs_capabilities["format"], "SFS")
        self.service.make_directory(session, LONG_NAME)

        self.service.select_partition(session, 2)
        self.assertEqual(session.ffs_capabilities["format"], "PFS3")
        self.service.make_directory(session, "Games")
        self.assertEqual(self.service.validate(session), "No structural errors found")

        self.service.select_partition(session, None)
        self.assertEqual(session.ffs_capabilities, {})

    def test_a_partition_for_another_system_is_refused_by_name(self) -> None:
        session = self.service.create_from_path(self.drive([
            {"name": "DH0", "dosType": b"SFS\x00", "sizeBytes": 20 * 1024 * 1024},
            {"name": "PC0", "dosType": b"MSD\x00", "sizeBytes": 16 * 1024 * 1024},
        ]))
        self.service.select_partition(session, 1)

        with self.assertRaises(DiskError) as caught:
            self.service.list_directory(session, "")

        self.assertIn("PC0", str(caught.exception))
        self.assertIn("MSD\\0", str(caught.exception))
        self.assertEqual(session.warnings, [])

    def test_a_drive_holding_one_sfs_volume_opens_in_place(self) -> None:
        card = self.bare_sfs("card.img")
        session = self.service.open_attached_drive(str(card), "Memory card")

        self.assertEqual(session.kind, "ffs")
        self.assertEqual(session.ffs_capabilities["map"], "sfs")
        with self.assertRaises(DiskError):
            self.service.make_directory(session, "Games")
        self.service.allow_drive_writes(session, True)
        self.service.make_directory(session, "Games")
        with self.service.ffs_mount(session) as mount:
            self.assertTrue(mount.exists("Games"))


if __name__ == "__main__":
    unittest.main()
