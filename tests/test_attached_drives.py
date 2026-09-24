"""Drives attached through USB, opened in place.

The drive listing is built from sysfs and udev, which these tests stand in for
with a small tree of files. Opening a drive is exercised against an ordinary
image file standing in for the device node, since the session only ever
reaches the drive through a link to it.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amiganut.filesystem.blocks import BlockReader
from amiganut.filesystem.rdb import partition_reader, read_rigid_disk, write_rigid_disk
from amiganut.filesystem.sfs_write import format_sfs_volume
from app.attached_drives import (
    AttachedDrive,
    describe_contents,
    find_attached_drive,
    list_attached_drives,
)
from app.disk_service import DiskService
from app.errors import DiskError

try:
    from app.server import create_app
except ModuleNotFoundError:  # Flask and Werkzeug are container dependencies.
    create_app = None


def sfs_drive(path: Path) -> Path:
    with path.open("wb") as handle:
        handle.truncate(24 * 1024 * 1024)
    with BlockReader(path, writable=True) as reader:
        write_rigid_disk(reader, [{"name": "DH0", "dosType": b"SFS\x00", "sizeBytes": 20 * 1024 * 1024}])
        window = partition_reader(reader, read_rigid_disk(reader).partitions[0])
        format_sfs_volume(window, label="Data", block_size=1024)
        window.close()
    return path


class FakeSystem:
    """A sysfs, /dev/disk/by-id, udev and /proc/mounts to list drives from."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.sys_block = root / "sys" / "block"
        self.by_id = root / "by-id"
        self.udev = root / "udev"
        self.mounts = root / "mounts"
        for folder in (self.sys_block, self.by_id, self.udev):
            folder.mkdir(parents=True)
        self.mounts.write_text("")

    def disk(self, name: str, *, bus: str, sectors: int, numbers: str, ro: str = "0") -> None:
        real = self.root / "devices" / "pci0000:00" / bus / "block" / name
        real.mkdir(parents=True)
        (real / "size").write_text(f"{sectors}\n")
        (real / "ro").write_text(f"{ro}\n")
        (real / "dev").write_text(f"{numbers}\n")
        device = real / "device"
        device.mkdir()
        (device / "vendor").write_text("JMicron\n")
        (device / "model").write_text("Generic\n")
        (self.sys_block / name).symlink_to(real)

    def locations(self) -> dict:
        return {
            "sys_block": self.sys_block,
            "by_id": self.by_id,
            "proc_mounts": self.mounts,
            "udev_data": self.udev,
        }


class DriveListingTests(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.system = FakeSystem(Path(folder.name))

    def test_only_usb_disks_with_media_are_offered(self) -> None:
        self.system.disk("sda", bus="usb1/1-1/1-1:1.0/host6/target6:0:0/6:0:0:0", sectors=2048, numbers="8:0")
        self.system.disk("sdb", bus="ata1/host0/target0:0:0/0:0:0:0", sectors=4096, numbers="8:16")
        self.system.disk("sdc", bus="usb2/2-1/2-1:1.0/host7/target7:0:0/7:0:0:0", sectors=0, numbers="8:32")
        (self.system.sys_block / "loop0").mkdir()

        drives = list_attached_drives(**self.system.locations())

        self.assertEqual([drive.device for drive in drives], ["/dev/sda"])
        self.assertEqual(drives[0].size, 2048 * 512)

    def test_the_drive_is_named_by_its_own_identity_not_its_adapter(self) -> None:
        self.system.disk("sda", bus="usb1/1-1/host6/block", sectors=2048, numbers="8:0")
        (self.system.by_id / "usb-JMicron_Generic_0123456789ABCDEF-0:0").symlink_to("/dev/sda")
        (self.system.by_id / "ata-QUANTUM_FIREBALL_1234").symlink_to("/dev/sda")
        (self.system.by_id / "ata-QUANTUM_FIREBALL_1234-part1").symlink_to("/dev/sda1")
        (self.system.udev / "b8:0").write_text("E:ID_MODEL=QUANTUM_FIREBALL\nE:ID_BUS=ata\n")

        drive = list_attached_drives(**self.system.locations())[0]

        self.assertEqual(drive.id, "ata-QUANTUM_FIREBALL_1234")
        self.assertEqual(drive.stable_path, str(self.system.by_id / "ata-QUANTUM_FIREBALL_1234"))
        self.assertEqual(drive.model, "QUANTUM FIREBALL")

    def test_without_udev_the_generic_adapter_vendor_is_left_out(self) -> None:
        self.system.disk("sda", bus="usb1/1-1/host6/block", sectors=2048, numbers="8:0")

        drive = list_attached_drives(**self.system.locations())[0]

        self.assertEqual(drive.model, "JMicron")

    def test_mounted_partitions_and_the_write_protect_switch_are_reported(self) -> None:
        self.system.disk("sda", bus="usb1/1-1/host6/block", sectors=2048, numbers="8:0", ro="1")
        self.system.mounts.write_text(
            "/dev/sda1 /media/pete/My\\040Card vfat rw 0 0\n/dev/sdb1 / ext4 rw 0 0\n"
        )

        drive = list_attached_drives(**self.system.locations())[0]

        self.assertEqual(drive.mounted, ["/media/pete/My Card"])
        self.assertTrue(drive.read_only_switch)
        self.assertFalse(drive.writable)

    def test_a_request_can_only_name_a_listed_drive(self) -> None:
        self.system.disk("sda", bus="usb1/1-1/host6/block", sectors=2048, numbers="8:0")
        self.system.disk("sdb", bus="ata1/host0/block", sectors=4096, numbers="8:16")

        self.assertEqual(find_attached_drive("sda", **self.system.locations()).device, "/dev/sda")
        for wanted in ("sdb", "/dev/sdb", "../sdb", "", None):
            with self.assertRaises(LookupError):
                find_attached_drive(wanted, **self.system.locations())


class ContentsTests(unittest.TestCase):
    def setUp(self) -> None:
        fsync = patch("amiganut.filesystem.blocks.os.fsync")
        fsync.start()
        self.addCleanup(fsync.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def test_a_partition_table_is_described_with_its_filing_systems(self) -> None:
        text = describe_contents(str(sfs_drive(self.folder / "drive.hdf")))
        self.assertTrue(text.startswith("Amiga drive, 1 partition ("), text)

    def test_a_drive_that_is_not_amiga_is_not_described(self) -> None:
        other = self.folder / "fat.img"
        other.write_bytes(b"\xeb\x3c\x90MSDOS5.0" + bytes(8192))
        self.assertEqual(describe_contents(str(other)), "")
        self.assertEqual(describe_contents(str(self.folder / "missing")), "")


class DriveSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        fsync = patch("amiganut.filesystem.blocks.os.fsync")
        fsync.start()
        self.addCleanup(fsync.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.drive = sfs_drive(self.folder / "drive.hdf")
        self.service = DiskService(self.folder / "work")

    def test_a_drive_opens_read_only_and_changes_wait_for_permission(self) -> None:
        session = self.service.open_attached_drive(str(self.drive), "QUANTUM FIREBALL")
        self.assertTrue(session.path.is_symlink())
        self.service.select_partition(session, 0)

        summary = self.service.summary(session)
        self.assertTrue(summary["readOnly"])
        self.assertFalse(summary["attachedDrive"]["writesAllowed"])
        self.assertEqual(summary["exportFormats"], [])
        self.assertEqual(summary["size"], self.drive.stat().st_size)
        with self.assertRaises(DiskError):
            self.service.make_directory(session, "Games")

        self.service.allow_drive_writes(session, True)
        self.service.make_directory(session, "Games")
        with self.service.ffs_mount(session) as mount:
            self.assertTrue(mount.exists("Games"))

    def test_a_drive_is_mounted_without_being_read_whole(self) -> None:
        # A real drive is hundreds of gigabytes, so reading it into memory
        # exhausts the machine. The Kickstart probe once did exactly that.
        session = self.service.open_attached_drive(str(self.drive), "Card")
        self.service.select_partition(session, 0)
        drive = self.drive.resolve()
        read_bytes = Path.read_bytes

        def refuse_the_drive(path: Path) -> bytes:
            if path.resolve() == drive:
                raise AssertionError("the whole drive was read into memory")
            return read_bytes(path)

        with patch.object(Path, "read_bytes", refuse_the_drive):
            with self.service.ffs_mount(session) as mount:
                list(mount.iter_entries(""))

    def test_closing_the_pane_removes_the_link_and_never_the_drive(self) -> None:
        session = self.service.open_attached_drive(str(self.drive), "Card")
        before = self.drive.read_bytes()
        self.service.discard_session(session)
        self.assertFalse(session.path.parent.exists())
        self.assertEqual(self.drive.read_bytes(), before)

    def test_a_restored_drive_session_is_read_only_again(self) -> None:
        session = self.service.open_attached_drive(str(self.drive), "Card")
        self.service.allow_drive_writes(session, True)

        restored = DiskService(self.folder / "work").get(session.id)

        self.assertEqual(restored.attached_device, str(self.drive))
        self.assertFalse(restored.device_writes)

    def test_a_restored_session_whose_link_has_been_changed_is_refused(self) -> None:
        session = self.service.open_attached_drive(str(self.drive), "Card")
        other = sfs_drive(self.folder / "other.hdf")
        session.path.unlink()
        session.path.symlink_to(other)

        with self.assertRaises(DiskError):
            DiskService(self.folder / "work").get(session.id)

    def test_a_drive_with_nothing_amiga_on_it_is_not_opened(self) -> None:
        blank = self.folder / "blank.img"
        blank.write_bytes(bytes(1024 * 1024))
        with self.assertRaises(DiskError):
            self.service.open_attached_drive(str(blank), "Blank")
        self.assertEqual(list((self.folder / "work").iterdir()), [])


@unittest.skipIf(create_app is None, "Flask is available in the application container")
class DriveRouteTests(unittest.TestCase):
    TOKEN = "d" * 32

    def setUp(self) -> None:
        fsync = patch("amiganut.filesystem.blocks.os.fsync")
        fsync.start()
        self.addCleanup(fsync.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.drive = sfs_drive(self.folder / "drive.hdf")
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

    def post(self, url: str, body: dict | None = None):
        return self.client.post(url, json=body or {}, headers={"X-Amiga-Desktop-Token": self.TOKEN})

    def get(self, url: str):
        return self.client.get(url, headers={"X-Amiga-Desktop-Token": self.TOKEN})

    def test_changes_are_refused_until_writes_are_allowed_and_have_no_undo(self) -> None:
        opened = self.post("/api/desktop/attached-drives/open", {"id": "ata-QUANTUM_FIREBALL_1234"})
        self.assertEqual(opened.status_code, 200, opened.get_json())
        image = opened.get_json()["image"]
        self.assertTrue(image["readOnly"])
        base = f"/api/images/{image['id']}"

        refused = self.post(f"{base}/mkdir", {"path": "Games", "partition": 0})
        self.assertEqual(refused.status_code, 409)

        allowed = self.post(f"/api/desktop/images/{image['id']}/drive-writes", {"allowed": True})
        self.assertEqual(allowed.status_code, 200, allowed.get_json())
        self.assertFalse(allowed.get_json()["image"]["readOnly"])
        made = self.post(f"{base}/mkdir", {"path": "Games", "partition": 0})
        self.assertEqual(made.status_code, 200, made.get_json())
        self.assertNotEqual(made.get_json()["image"]["revision"], image["revision"])
        self.assertFalse((self.folder / "work" / image["id"] / "checkpoints").exists())

    def test_every_refused_operation_names_a_real_endpoint(self) -> None:
        from app.server import DRIVE_UNAVAILABLE

        endpoints = {rule.endpoint for rule in self.client.application.url_map.iter_rules()}
        self.assertEqual(DRIVE_UNAVAILABLE - endpoints, set())

    def test_whole_image_operations_are_refused_for_a_drive(self) -> None:
        image = self.post(
            "/api/desktop/attached-drives/open", {"id": "ata-QUANTUM_FIREBALL_1234"}
        ).get_json()["image"]
        for url in (f"/api/images/{image['id']}/download", f"/api/images/{image['id']}/hex?offset=0"):
            response = self.get(url)
            self.assertEqual(response.status_code, 400, url)
            self.assertIn("drive opened in place", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
