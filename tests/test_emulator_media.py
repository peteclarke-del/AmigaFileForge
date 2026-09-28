"""Handing a drive to the emulator, and starting a machine from a release CD.

An installer run under emulation is only worth anything if what it writes is
kept, so the emulator has to be given the image in the pane and not a copy.
It also has to be able to read it. FS-UAE installed as a snap cannot see the
hidden folder the workbench keeps its images in, and given a path there it
opens a window onto a machine with no drive.

None of this starts an emulator. What is asserted is where the files are put,
what the emulator is told, and what state the session is left in.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.disk_service import DiskService
from app.emergency_boot import (
    BOOT_DEVICE,
    BOOT_PRIORITY,
    BOOT_VOLUME,
    build_boot_drive,
    without_floppy,
)
from app.emulator_config import emulator_command
from app.emulator_media import StagedMedia, is_confined
from app.errors import DiskError
from tests import amigaos_fixture

try:
    from app.server import create_app
except ModuleNotFoundError:  # Flask and Werkzeug are container dependencies.
    create_app = None

STARTUP = "\n".join([
    "C:SetPatch QUIET",
    "C:AddBuffers >NIL: DF0: 15",
    "Assign >NIL: LIBS: SYS:Classes ADD",
    "Assign >NIL: LIBS: DF0:Libs ADD",
    "IF EXISTS DEVS:Monitors",
    "  DEVS:Monitors/VGAOnly",
    "EndIF",
    "; RTG-Support loaded from disk",
    "IF EXISTS DF0:DEVS/Monitors",
    "  C:List >NIL: DF0:DEVS/Monitors/~(#?.info|VGAOnly) TO T:M",
    "  IF EXISTS T:M",
    "    Execute T:M",
    "  EndIF",
    "  C:Delete >NIL: T:M",
    "EndIF",
    "C:LoadWB",
    "",
])


def release_disc(folder: Path, *, emergency: bool = True) -> Path:
    return amigaos_fixture.release_disc(
        folder, emergency=emergency, emergency_startup=STARTUP
    )


class ConfinementTests(unittest.TestCase):
    def test_a_snap_is_told_from_an_ordinary_installation(self) -> None:
        self.assertTrue(is_confined("/snap/bin/fsuae.fs-uae"))
        self.assertFalse(is_confined("/usr/bin/fs-uae"))
        self.assertFalse(is_confined("/opt/fs-uae/fs-uae"))


class StagingFixture(unittest.TestCase):
    def setUp(self) -> None:
        fsync = patch("amiganut.filesystem.blocks.os.fsync")
        fsync.start()
        self.addCleanup(fsync.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.snap = self.folder / "snap-common"
        common = patch("app.emulator_media.SNAP_COMMON", self.snap)
        common.start()
        self.addCleanup(common.stop)
        self.service = DiskService(self.folder / "work")
        self.drive = self.service.create_blank("ffs-hard", "System", "8MB")
        self.finished: list[tuple[str, bool]] = []

    def staged(self, executable: str, *, writable: bool, copy_file=None) -> StagedMedia:
        return StagedMedia(
            self.drive,
            SimpleNamespace(hardware_profile={}),
            executable=executable,
            work_dir=self.service.work_dir,
            copy_file=copy_file or self.service._copy_local_file,
            writable=writable,
            finished=lambda session, wrote: self.finished.append((session.name, wrote)),
        )


class StagedMediaTests(StagingFixture):
    def test_a_confined_emulator_is_given_the_image_under_a_name_it_can_read(self) -> None:
        media = self.staged("/snap/bin/fsuae.fs-uae", writable=True)
        with media as (_launch, path):
            self.assertTrue(str(path).startswith(str(self.snap)))
            self.assertEqual(path.suffix, ".hdf")
            # The same file under a second name: nothing was copied, and what
            # is written through one name is there under the other.
            self.assertTrue(os.path.samefile(path, self.drive.path))
            with path.open("r+b") as handle:
                handle.seek(4096)
                handle.write(b"written by the emulator")
            self.assertTrue(self.drive.in_emulator)
        with self.drive.path.open("rb") as handle:
            handle.seek(4096)
            self.assertEqual(handle.read(23), b"written by the emulator")
        self.assertFalse(self.drive.in_emulator)
        self.assertEqual(self.finished, [(self.drive.name, True)])
        self.assertEqual(list(self.snap.rglob("*.hdf")), [], "nothing is left behind")

    def test_an_ordinary_emulator_is_given_the_image_where_it_is(self) -> None:
        media = self.staged("/usr/bin/fs-uae", writable=False)
        with media as (_launch, path):
            self.assertEqual(path, self.drive.path)
        self.assertEqual(self.finished, [(self.drive.name, False)])
        self.assertFalse(self.snap.exists())

    def test_where_a_link_cannot_be_made_the_image_is_copied_and_copied_back(self) -> None:
        with patch("app.emulator_media.os.link", side_effect=OSError(18, "cross-device")):
            media = self.staged("/snap/bin/fsuae.fs-uae", writable=True)
            with media as (_launch, path):
                self.assertFalse(os.path.samefile(path, self.drive.path))
                with path.open("r+b") as handle:
                    handle.seek(4096)
                    handle.write(b"written to the copy")
        with self.drive.path.open("rb") as handle:
            handle.seek(4096)
            self.assertEqual(handle.read(19), b"written to the copy")

    def test_a_read_only_run_is_not_copied_back(self) -> None:
        before = self.drive.path.read_bytes()
        with patch("app.emulator_media.os.link", side_effect=OSError(18, "cross-device")):
            media = self.staged("/snap/bin/fsuae.fs-uae", writable=False)
            with media as (_launch, path):
                with path.open("r+b") as handle:
                    handle.write(b"scribble")
        self.assertEqual(self.drive.path.read_bytes(), before)

    def test_what_travels_with_the_drive_outlives_the_session_it_came_from(self) -> None:
        disc = self.folder / "disc.iso"
        disc.write_bytes(b"the release CD")
        media = self.staged("/usr/bin/fs-uae", writable=True)
        staged = media.attach(disc, "cdrom.iso")
        disc.unlink()
        with media:
            self.assertEqual(staged.read_bytes(), b"the release CD")
        self.assertFalse(staged.exists())

    def test_a_drive_cannot_be_given_to_two_emulators(self) -> None:
        with self.staged("/usr/bin/fs-uae", writable=True):
            with self.assertRaises(DiskError):
                self.staged("/usr/bin/fs-uae", writable=True).__enter__()

    def test_a_drive_in_the_emulator_is_not_changed_or_rolled_back_here(self) -> None:
        self.service.select_partition(self.drive, 0)
        self.service.make_directory(self.drive, "Before")
        checkpoint = self.service.create_checkpoint(self.drive, "Before the install")
        with self.staged("/usr/bin/fs-uae", writable=True):
            with self.assertRaises(DiskError) as raised:
                self.service.make_directory(self.drive, "During")
            self.assertIn("running emulator", str(raised.exception))
            with self.assertRaises(DiskError):
                self.service.restore_checkpoint(self.drive, checkpoint["id"])
        self.service.make_directory(self.drive, "After")

    def test_the_undo_point_of_an_install_is_kept_though_nothing_has_changed_yet(self) -> None:
        token = self.service.begin_automatic_checkpoint(self.drive, "installing from a CD")
        with self.staged("/usr/bin/fs-uae", writable=True):
            self.service.finish_automatic_checkpoint(self.drive, token)
        self.assertEqual(
            [item["reason"] for item in self.service.list_checkpoints(self.drive)],
            ["installing from a CD"],
        )


class EmulatorCommandTests(unittest.TestCase):
    KICK = Path("/roms/kick31.rom")

    def command(self, profile: dict, **options) -> list[str]:
        session = SimpleNamespace(
            hardware_profile={"machine": "a1200", "emulator": "auto", **profile},
            target_hardware="amigaos",
        )
        with patch("app.emulator_config.Path.is_file", return_value=True), patch(
            "app.emulator_config.kickstart_for", return_value=self.KICK
        ):
            command, _cwd = emulator_command(session, "/work/drive.hdf", **options)
        return command

    def test_a_drive_to_install_onto_is_attached_for_writing(self) -> None:
        command = self.command({})
        self.assertIn("--hard_drive_0=/work/drive.hdf", command)
        self.assertNotIn("--hard_drive_0_read_only=1", command)

    def test_a_drive_to_look_at_is_attached_read_only(self) -> None:
        self.assertIn("--hard_drive_0_read_only=1", self.command({"emulatorReadOnly": True}))
        self.assertIn("--hard_drive_0_read_only=1", self.command({}, read_only=True))

    def test_a_boot_drive_is_attached_beside_the_one_installed_onto(self) -> None:
        command = self.command(
            {"emulatorBootDrive": "/work/emergency-boot.hdf"}, cdroms=["/work/cdrom.iso"]
        )
        self.assertIn("--hard_drive_0=/work/drive.hdf", command)
        self.assertIn("--hard_drive_1=/work/emergency-boot.hdf", command)
        self.assertIn("--hard_drive_1_read_only=1", command)
        self.assertIn("--cdrom_drive_0=/work/cdrom.iso", command)


class EmergencyBootTests(unittest.TestCase):
    def setUp(self) -> None:
        fsync = patch("amiganut.filesystem.blocks.os.fsync")
        fsync.start()
        self.addCleanup(fsync.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def test_what_the_startup_expects_of_a_floppy_is_taken_out(self) -> None:
        """With no disk in DF0: each of these stops the boot with a requester."""
        lines = without_floppy(STARTUP).splitlines()
        active = [line for line in lines if not line.lstrip().startswith(";")]

        self.assertFalse(any("DF0:" in line for line in active))
        self.assertIn("C:SetPatch QUIET", active)
        self.assertIn("Assign >NIL: LIBS: SYS:Classes ADD", active)
        self.assertIn("C:LoadWB", active)
        # The block that tests for the floppy goes with its test, the block
        # nested inside it included, and the one about the boot volume stays.
        self.assertIn("IF EXISTS DEVS:Monitors", active)
        self.assertEqual(active.count("EndIF"), 1)
        self.assertNotIn("    Execute T:M", active)
        self.assertEqual(len(lines), len(STARTUP.splitlines()), "commented out, not removed")

    def test_the_disc_system_becomes_a_drive_the_machine_starts_from(self) -> None:
        from amiganut.disc.mount import mount_image
        from amiganut.filesystem.blocks import BlockReader
        from amiganut.filesystem.rdb import read_rigid_disk

        target = self.folder / "emergency-boot.hdf"
        built = build_boot_drive(release_disc(self.folder), target)

        self.assertEqual(built["files"], 6)
        with BlockReader(target) as reader:
            disk = read_rigid_disk(reader)
        partition = disk.partitions[0]
        self.assertEqual(partition.name, BOOT_DEVICE)
        self.assertTrue(partition.bootable)
        self.assertEqual(partition.boot_priority, BOOT_PRIORITY)
        self.assertGreater(BOOT_PRIORITY, 5, "above a floppy, and a new drive's 0")
        self.assertEqual(partition.dos_type, b"DOS\x03", "in every Kickstart from 2.0")
        mount, _name = mount_image(target, partition=0)
        try:
            self.assertEqual(mount.title, BOOT_VOLUME)
            self.assertEqual(
                sorted(entry.name for entry in mount.iter_entries("")),
                ["C", "Devs", "Disk.info", "Libs", "S"],
            )
            script = mount.read_bytes("S/Startup-Sequence").decode("latin-1")
            self.assertIn("; Assign >NIL: LIBS: DF0:Libs ADD", script)
            self.assertTrue(mount.exists("Devs/Monitors/PAL"))
            self.assertFalse(mount.exists("Devs/DOSDrivers/CD0"), "the emulator mounts the disc")
            self.assertEqual(mount.validate(), [])
        finally:
            mount.close()

    def test_a_disc_with_no_system_makes_no_boot_drive(self) -> None:
        target = self.folder / "emergency-boot.hdf"
        with self.assertRaises(DiskError) as raised:
            build_boot_drive(release_disc(self.folder, emergency=False), target)
        self.assertIn("no Emergency-Boot system", str(raised.exception))
        self.assertFalse(target.exists())


class _Process:
    """An emulator that is running until it is told to stop."""

    def __init__(self, arguments, **_options) -> None:
        self.arguments = list(arguments)
        self.returncode = None
        self._stopped = __import__("threading").Event()

    def poll(self):
        return self.returncode

    def communicate(self):
        self._stopped.wait()
        return b"", b""

    def wait(self, timeout=None):
        self._stopped.wait(timeout)
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0
        self._stopped.set()

    kill = terminate


@unittest.skipIf(create_app is None, "Flask is available in the application container")
class InstallRouteTests(StagingFixture):
    TOKEN = "d" * 32

    def setUp(self) -> None:
        super().setUp()
        app = create_app(
            work_dir=self.folder / "routes",
            platform="desktop",
            desktop_token=self.TOKEN,
            desktop_owner="o" * 32,
        )
        self.client = app.test_client()
        self.emulator = app.extensions["amiga_interactive_emulator"]
        self.addCleanup(self.emulator.stop)
        self.started: list[_Process] = []

        import subprocess

        real = subprocess.Popen

        def start(arguments, *more, **options):
            # Only the emulator is stood in for. The module is the one every
            # other part of the application starts its own programs through.
            if "fs-uae" not in str(arguments[0]):
                return real(arguments, *more, **options)
            process = _Process(arguments, **options)
            self.started.append(process)
            return process

        for target, value in (
            ("app.routes.tools.subprocess.Popen", start),
            ("app.emulator_config.ManagedEmulator.available", True),
            ("app.emulator_config.kickstart_for", lambda _machine: Path("/roms/kick31.rom")),
            ("app.emulator_media.is_confined", lambda _executable: True),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def request(self, method: str, url: str, body: dict | None = None):
        return self.client.open(
            url, method=method, json=body, headers={"X-Amiga-Desktop-Token": self.TOKEN}
        )

    def prepare(self, *, emergency: bool = True) -> tuple[str, dict]:
        created = self.request("POST", "/api/images/create", {
            "format": "ffs-hard", "title": "Empty", "capacity": "64MB",
            "drive": {"partitions": [
                {"name": "DH0", "label": "System", "filesystem": "pfs3", "bootable": True},
            ]},
        })
        self.assertEqual(created.status_code, 200, created.get_json())
        image = created.get_json()["image"]["id"]
        disc = self.request(
            "POST", "/api/desktop/open-path",
            {"path": str(release_disc(self.folder, emergency=emergency))},
        )
        self.assertEqual(disc.status_code, 200, disc.get_json())
        return image, {
            "disc": disc.get_json()["image"]["id"],
            "partition": 0,
            "hardwareProfile": {"machine": "a1200", "addons": ["kick31"]},
        }

    def test_an_empty_drive_is_started_from_the_disc_for_its_own_installer(self) -> None:
        image, body = self.prepare()
        started = self.request("POST", f"/api/images/{image}/install/amigaos-cd/boot", body)

        self.assertEqual(started.status_code, 200, started.get_json())
        result = started.get_json()["result"]
        self.assertEqual(result["bootFrom"], "disc")
        self.assertIn("full installation", result["summary"])
        self.assertIn("DH0", result["summary"])
        command = self.started[-1].arguments
        attached = {
            argument.split("=", 1)[0]: Path(argument.split("=", 1)[1])
            for argument in command
            if argument.startswith(("--hard_drive_0=", "--hard_drive_1=", "--cdrom_drive_0="))
        }
        for path in attached.values():
            self.assertTrue(str(path).startswith(str(self.snap)), path)
            self.assertTrue(path.is_file(), path)
        session = self.client.application.extensions["amiga_disk_service"].get(image)
        self.assertTrue(os.path.samefile(attached["--hard_drive_0"], session.path))
        self.assertNotIn("--hard_drive_0_read_only=1", command)
        self.assertIn("--hard_drive_1_read_only=1", command)
        with attached["--hard_drive_1"].open("rb") as handle:
            self.assertEqual(handle.read(4), b"RDSK")
        with attached["--cdrom_drive_0"].open("rb") as handle:
            handle.seek(16 * 2048 + 1)
            self.assertEqual(handle.read(5), b"CD001")

        # While the machine runs, the drive is its to write to.
        refused = self.request("POST", f"/api/images/{image}/mkdir", {"path": "Games", "partition": 0})
        self.assertEqual(refused.status_code, 409)
        self.assertIn("running emulator", refused.get_json()["error"])
        listed = self.request("GET", f"/api/images/{image}/checkpoints").get_json()
        self.assertEqual(
            [item["reason"] for item in listed["checkpoints"]],
            ["running the installer on an AmigaOS release CD"],
        )

        self.emulator.stop()
        summary = self.request("GET", f"/api/images/{image}").get_json()["image"]
        self.assertTrue(summary["dirty"])
        self.assertEqual([path for path in self.snap.rglob("*") if path.is_file()], [])
        made = self.request("POST", f"/api/images/{image}/mkdir", {"path": "Games", "partition": 0})
        self.assertEqual(made.status_code, 200, made.get_json())

    def test_nothing_is_written_to_the_drive_to_prepare_it(self) -> None:
        image, body = self.prepare()
        session = self.client.application.extensions["amiga_disk_service"].get(image)
        before = session.path.read_bytes()
        started = self.request("POST", f"/api/images/{image}/install/amigaos-cd/boot", body)
        self.assertEqual(started.status_code, 200, started.get_json())
        self.assertEqual(session.path.read_bytes(), before)

    def test_a_drive_that_nothing_can_start_is_refused_before_anything_starts(self) -> None:
        image, body = self.prepare(emergency=False)
        checked = self.request(
            "POST", f"/api/images/{image}/install/amigaos-cd/preflight", body
        ).get_json()["preflight"]
        self.assertTrue(checked["disc"]["recognised"])
        self.assertTrue(checked["ready"], "the drive can still be installed onto directly")
        self.assertEqual(checked["bootFrom"], "")
        refused = self.request("POST", f"/api/images/{image}/install/amigaos-cd/boot", body)
        self.assertEqual(refused.status_code, 400)
        self.assertIn("no emergency system", refused.get_json()["error"])
        self.assertEqual(self.started, [])
        self.assertFalse(self.snap.exists())


if __name__ == "__main__":
    unittest.main()
