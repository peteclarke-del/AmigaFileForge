from __future__ import annotations

import io
import unittest
import zipfile
from types import SimpleNamespace

from app.archive_utils import (
    MAX_ARCHIVE_MEMBERS,
    iter_upload_images,
    open_single_upload_image,
    validated_zip_members,
)
from app.disk_service import DiskError


def zip_upload(name: str, members: dict[str, bytes]):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for filename, data in members.items():
            archive.writestr(filename, data)
    stream.seek(0)
    return SimpleNamespace(filename=name, stream=stream)


class ArchiveTests(unittest.TestCase):
    def test_shared_zip_validation_rejects_excessive_member_counts(self):
        archive = SimpleNamespace(
            infolist=lambda: [SimpleNamespace() for _ in range(MAX_ARCHIVE_MEMBERS + 1)]
        )
        with self.assertRaisesRegex(DiskError, "more than"):
            validated_zip_members(archive)

    def test_hdf_import_expands_every_supported_disk_and_ignores_extras(self):
        upload = zip_upload(
            "Games (1984)(Commodore).zip",
            {
                "README.txt": b"notes",
                "disks/Game A.adf": b"A" * 204800,
                "disks/Game B.adz": b"B" * 409600,
            },
        )

        items = [
            (item.filename, len(item.stream.read()), item.metadata_names)
            for item in iter_upload_images([upload], {".adf", ".adz"})
        ]

        self.assertEqual(
            items,
            [
                (
                    "Game A.adf",
                    204800,
                    [
                        "disks/Game A.adf",
                        "Games (1984)(Commodore).zip",
                    ],
                ),
                (
                    "Game B.adz",
                    409600,
                    [
                        "disks/Game B.adz",
                        "Games (1984)(Commodore).zip",
                    ],
                ),
            ],
        )

    def test_single_image_import_explains_a_multi_image_zip(self):
        upload = zip_upload(
            "two.zip",
            {"one.adf": b"1", "two.adf": b"2"},
        )

        with self.assertRaisesRegex(DiskError, "contains 2 supported images"):
            with open_single_upload_image(upload, {".adf"}):
                pass


if __name__ == "__main__":
    unittest.main()
