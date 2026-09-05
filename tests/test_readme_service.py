from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone

from app.disk_service import DiskService
from app.readme_service import build_download_readme, timestamped_archive_name


class ReadmeServiceTests(unittest.TestCase):
    def test_archive_name_uses_image_stem_and_timestamp(self) -> None:
        generated = datetime(2026, 8, 1, 14, 5, 9, tzinfo=timezone.utc)

        self.assertEqual(
            timestamped_archive_name("Games.Library.hdf", generated),
            "Games.Library-20260801-140509.zip",
        )

    def test_hard_drive_readme_lists_every_partition(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(folder)
            drive = service.create_blank("ffs-hard", "Library", capacity="4MB")

            readme = build_download_readme(
                service,
                drive,
                drive.path,
                datetime(2026, 8, 1, tzinfo=timezone.utc),
            )

            self.assertIn("## Partition table", readme)
            self.assertIn("| `DH0` |", readme)
            self.assertIn("FFS-INTL", readme)
            self.assertIn("Image SHA-256:", readme)


if __name__ == "__main__":
    unittest.main()
