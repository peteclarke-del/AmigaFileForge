"""Copying a drive opened in place out to an image file.

An ordinary image file stands in for the drive, as it does in the other
attached-drive tests. It is made larger than its partition table declares,
the way a card prepared for a smaller drive is, so the difference between the
drive as described and every byte of the device is real.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amiganut.filesystem.blocks import BlockReader
from amiganut.filesystem.rdb import partition_reader, read_rigid_disk, write_rigid_disk
from amiganut.filesystem.sfs_write import format_sfs_volume
from app.attached_drives import AttachedDrive
from app.disk_service import DiskService
from app.drive_export import check_destination, copy_range, export_drive, export_scopes
from app.errors import DiskError
from app.operations import OperationCancelled

try:
    from app.server import create_app
except ModuleNotFoundError:  # Flask and Werkzeug are container dependencies.
    create_app = None

MIB = 1024 * 1024


def ignore(*_args) -> None:
    return None


class DriveFixture(unittest.TestCase):
    def setUp(self) -> None:
        fsync = patch("amiganut.filesystem.blocks.os.fsync")
        fsync.start()
        self.addCleanup(fsync.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.drive = self.folder / "drive.img"
        with self.drive.open("wb") as handle:
            handle.truncate(24 * MIB)
        with BlockReader(self.drive, writable=True) as reader:
            write_rigid_disk(reader, [{"name": "DH0", "dosType": b"SFS\x00", "sizeBytes": 20 * MIB}])
            window = partition_reader(reader, read_rigid_disk(reader).partitions[0])
            format_sfs_volume(window, label="Data", block_size=1024)
            window.close()
        # The card is larger than the drive it was prepared for.
        with self.drive.open("r+b") as handle:
            handle.truncate(40 * MIB)
            handle.seek(36 * MIB)
            handle.write(b"beyond the partition table")


class DriveExportTests(DriveFixture):
    def scopes(self) -> dict:
        return {scope.scope: scope for scope in export_scopes(self.drive, True)}

    def test_the_drive_as_described_stops_where_its_partition_table_ends(self) -> None:
        scopes = self.scopes()

        self.assertEqual(list(scopes), ["drive", "device", "partition:0"])
        self.assertLessEqual(scopes["drive"].length, 24 * MIB)
        self.assertEqual(scopes["device"].length, 40 * MIB)
        self.assertIsNotNone(scopes["partition:0"].geometry)

    def test_a_drive_holding_one_volume_is_offered_whole(self) -> None:
        scopes = export_scopes(self.drive, False)
        self.assertEqual([(scope.scope, scope.length) for scope in scopes], [("drive", 40 * MIB)])

    def test_the_copy_is_exact_and_skips_what_is_empty(self) -> None:
        target = self.folder / "copy.hdf"
        written = copy_range(self.drive, target, 0, 40 * MIB, ignore)

        self.assertEqual(target.read_bytes(), self.drive.read_bytes())
        self.assertLess(written, 40 * MIB)
        self.assertFalse(target.with_name("copy.hdf.part").exists())

    def test_one_partition_becomes_a_hardfile_that_opens_with_its_geometry(self) -> None:
        target = self.folder / "Data.hdf"
        result = export_drive(self.drive, True, "partition:0", str(target), ignore)

        self.assertEqual(result["geometry"], str(self.folder / "Data.geo"))
        session = DiskService(self.folder / "work").create_from_path(target)
        self.assertEqual(session.kind, "ffs")
        self.assertEqual(session.ffs_capabilities["format"], "SFS")

    def test_a_stopped_copy_leaves_nothing_behind(self) -> None:
        target = self.folder / "stopped.hdf"
        calls = []

        def cancel_after_start(*args) -> None:
            calls.append(args)
            if len(calls) > 1:
                raise OperationCancelled("stopped")

        with patch("app.drive_export.PROGRESS_EVERY", 4 * MIB), self.assertRaises(OperationCancelled):
            copy_range(self.drive, target, 0, 40 * MIB, cancel_after_start)

        self.assertEqual(list(self.folder.glob("stopped*")), [])

    def test_a_destination_must_be_an_ordinary_file_in_an_existing_folder(self) -> None:
        refused = [
            "",
            "relative/drive.hdf",
            "/dev/sdz",
            str(self.folder),
            str(self.folder / "missing" / "drive.hdf"),
            str(self.drive),
        ]
        for destination in refused:
            with self.subTest(destination=destination), self.assertRaises(DiskError):
                check_destination(destination, self.drive)
        self.assertEqual(
            check_destination(str(self.folder / "new.hdf"), self.drive),
            self.folder / "new.hdf",
        )

    def test_an_unknown_scope_is_refused(self) -> None:
        with self.assertRaises(DiskError):
            export_drive(self.drive, True, "partition:9", str(self.folder / "x.hdf"), ignore)


@unittest.skipIf(create_app is None, "Flask is available in the application container")
class DriveExportRouteTests(DriveFixture):
    TOKEN = "d" * 32

    def setUp(self) -> None:
        super().setUp()
        app = create_app(
            work_dir=self.folder / "work",
            platform="desktop",
            desktop_token=self.TOKEN,
            desktop_owner="o" * 32,
        )
        self.client = app.test_client()
        listed = AttachedDrive(
            id="ata-QUANTUM_FIREBALL_1234",
            device="/dev/sdz",
            stable_path=str(self.drive),
            model="QUANTUM FIREBALL",
            size=self.drive.stat().st_size,
            readable=True,
            writable=True,
            read_only_switch=False,
            contents="Amiga drive, 1 partition (SFS)",
        )
        finder = patch("app.routes.desktop.find_attached_drive", return_value=listed)
        finder.start()
        self.addCleanup(finder.stop)

    def request(self, method: str, url: str, body: dict | None = None):
        return self.client.open(
            url, method=method, json=body, headers={"X-Amiga-Desktop-Token": self.TOKEN}
        )

    def test_a_drive_is_exported_to_the_path_chosen(self) -> None:
        opened = self.request("POST", "/api/desktop/attached-drives/open", {"id": "ata-QUANTUM_FIREBALL_1234"})
        image = opened.get_json()["image"]
        url = f"/api/desktop/images/{image['id']}/drive-export"

        options = self.request("GET", url).get_json()
        self.assertEqual([scope["scope"] for scope in options["scopes"]], ["drive", "device", "partition:0"])

        target = self.folder / "Fireball.hdf"
        saved = self.request("POST", url, {"scope": "device", "destination": str(target)})
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(target.read_bytes(), self.drive.read_bytes())

    def test_an_image_file_is_not_exported_this_way(self) -> None:
        opened = self.client.post(
            "/api/desktop/open-path",
            json={"path": str(self.drive)},
            headers={"X-Amiga-Desktop-Token": self.TOKEN},
        )
        image = opened.get_json()["image"]
        refused = self.request("GET", f"/api/desktop/images/{image['id']}/drive-export")
        self.assertEqual(refused.status_code, 400)


if __name__ == "__main__":
    unittest.main()
