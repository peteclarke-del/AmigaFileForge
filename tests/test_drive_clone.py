"""Copying a drive opened in place onto another attached drive.

Image files stand in for both drives. The target starts full of other data,
as a used card does, so a copy that skipped the source's empty space would
leave that data behind and fail these tests.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from amiganut.filesystem.blocks import BlockReader
from amiganut.filesystem.rdb import partition_reader, read_rigid_disk, write_rigid_disk
from amiganut.filesystem.sfs_write import format_sfs_volume
from app.attached_drives import AttachedDrive
from app.drive_clone import _verify, clone_drive, clone_scopes, target_problem
from app.errors import DiskError

try:
    from app.server import create_app
except ModuleNotFoundError:  # Flask and Werkzeug are container dependencies.
    create_app = None

MIB = 1024 * 1024


def ignore(*_args) -> None:
    return None


class CloneFixture(unittest.TestCase):
    def setUp(self) -> None:
        fsync = patch("amiganut.filesystem.blocks.os.fsync")
        fsync.start()
        self.addCleanup(fsync.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.source = self.folder / "source.img"
        with self.source.open("wb") as handle:
            handle.truncate(24 * MIB)
        with BlockReader(self.source, writable=True) as reader:
            write_rigid_disk(reader, [{"name": "DH0", "dosType": b"SFS\x00", "sizeBytes": 20 * MIB}])
            window = partition_reader(reader, read_rigid_disk(reader).partitions[0])
            format_sfs_volume(window, label="Data", block_size=1024)
            window.close()
        with self.source.open("r+b") as handle:
            handle.truncate(32 * MIB)
        self.target = self.folder / "target.img"
        self.target.write_bytes(b"\xa5" * (40 * MIB))

    def drive(self, path: Path, **changes) -> AttachedDrive:
        listed = AttachedDrive(
            id=f"usb-{path.stem}",
            device=f"/dev/sd{path.stem[0]}",
            stable_path=str(path),
            model=path.stem.upper(),
            size=path.stat().st_size,
            readable=True,
            writable=True,
            read_only_switch=False,
            contents="Amiga drive",
        )
        return replace(listed, **changes)


class DriveCloneTests(CloneFixture):
    def test_only_the_whole_drive_shapes_are_offered(self) -> None:
        self.assertEqual([scope.scope for scope in clone_scopes(self.source, True)], ["drive", "device"])

    def test_the_copy_replaces_everything_the_target_held(self) -> None:
        result = clone_drive(self.source, True, "device", self.drive(self.target), set(), ignore)

        self.assertEqual(result["bytes"], 32 * MIB)
        copied = self.target.read_bytes()
        self.assertEqual(copied[: 32 * MIB], self.source.read_bytes())
        # Beyond the copy the target keeps what it had; the copy is only as
        # long as the shape chosen.
        self.assertEqual(copied[32 * MIB :], b"\xa5" * (8 * MIB))

    def test_a_target_that_cannot_take_the_copy_says_why(self) -> None:
        target = self.drive(self.target)
        cases = {
            "being copied": (self.drive(self.source), set()),
            "open in a pane": (target, {os.path.realpath(self.target)}),
            "mounted": (replace(target, mounted=["/media/card"]), set()),
            "write-protect": (replace(target, read_only_switch=True), set()),
            "too small": (replace(target, size=MIB), set()),
            "cannot write": (replace(target, readable=False), set()),
        }
        for reason, (candidate, open_devices) in cases.items():
            with self.subTest(reason=reason):
                problem = target_problem(candidate, self.source, 32 * MIB, open_devices)
                self.assertIn(reason, problem)
                with self.assertRaises(DiskError):
                    clone_drive(self.source, True, "device", candidate, open_devices, ignore)
        self.assertEqual(target_problem(target, self.source, 32 * MIB, set()), "")

    def test_a_partition_alone_is_not_copied_onto_a_drive(self) -> None:
        with self.assertRaises(DiskError):
            clone_drive(self.source, True, "partition:0", self.drive(self.target), set(), ignore)

    def test_a_copy_that_reads_back_differently_is_reported(self) -> None:
        other = self.folder / "other.img"
        other.write_bytes(self.source.read_bytes()[:-1] + b"\x01")
        with self.assertRaises(DiskError) as caught:
            _verify(self.source, other, 32 * MIB, lambda *_args: None)
        self.assertIn("does not match", str(caught.exception))


@unittest.skipIf(create_app is None, "Flask is available in the application container")
class DriveCloneRouteTests(CloneFixture):
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
        drives = [self.drive(self.source), self.drive(self.target)]
        lister = patch("app.routes.desktop.list_attached_drives", return_value=drives)
        lister.start()
        self.addCleanup(lister.stop)
        finder = patch(
            "app.routes.desktop.find_attached_drive",
            side_effect=lambda wanted: next(drive for drive in drives if drive.id == wanted),
        )
        finder.start()
        self.addCleanup(finder.stop)
        opened = self.request("POST", "/api/desktop/attached-drives/open", {"id": "usb-source"})
        self.url = f"/api/desktop/images/{opened.get_json()['image']['id']}/drive-clone"

    def request(self, method: str, url: str, body: dict | None = None):
        return self.client.open(
            url, method=method, json=body, headers={"X-Amiga-Desktop-Token": self.TOKEN}
        )

    def test_each_drive_is_listed_with_why_it_can_or_cannot_take_the_copy(self) -> None:
        options = self.request("GET", self.url).get_json()
        problems = {drive["id"]: drive["problems"] for drive in options["targets"]}

        self.assertIn("being copied", problems["usb-source"]["device"])
        self.assertEqual(problems["usb-target"]["device"], "")

    def test_the_target_has_to_be_confirmed_before_it_is_erased(self) -> None:
        before = self.target.read_bytes()
        refused = self.request("POST", self.url, {"scope": "device", "target": "usb-target"})
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(self.target.read_bytes(), before)

        copied = self.request(
            "POST", self.url, {"scope": "device", "target": "usb-target", "confirm": "usb-target"}
        )
        self.assertEqual(copied.status_code, 200, copied.get_json())
        self.assertEqual(self.target.read_bytes()[: 32 * MIB], self.source.read_bytes())


if __name__ == "__main__":
    unittest.main()
