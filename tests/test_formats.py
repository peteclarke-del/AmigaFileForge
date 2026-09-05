from __future__ import annotations

import unittest

from app.disk_service import DiskService
from app.formats import FFS_EXTENSIONS, HDF_EXTENSIONS, OFS_EXTENSIONS


class FormatTests(unittest.TestCase):
    def test_hard_drive_extensions_open_as_amigados_volumes(self):
        for extension in (".hda", ".img", ".raw", ".bin", ".dsk"):
            with self.subTest(extension=extension):
                self.assertIn(extension, FFS_EXTENSIONS)
                self.assertEqual(DiskService.detect_kind(f"HardDisk4{extension}"), "ffs")

    def test_partitioned_drive_extensions_open_as_a_container(self):
        for extension in (".hdf", ".hdz", ".rdsk"):
            with self.subTest(extension=extension):
                self.assertIn(extension, HDF_EXTENSIONS)
                self.assertEqual(DiskService.detect_kind(f"Library{extension}"), "hdf")

    def test_floppy_extensions_open_as_a_volume(self):
        """``.adf`` says nothing about OFS or FFS; the boot block decides."""
        for extension in OFS_EXTENSIONS:
            with self.subTest(extension=extension):
                self.assertEqual(DiskService.detect_kind(f"Games{extension}"), "ofs")

    def test_extensionless_images_are_content_detected(self):
        self.assertEqual(DiskService.detect_kind("HardDisk4"), "unknown")


if __name__ == "__main__":
    unittest.main()
