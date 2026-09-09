"""Recognising the AmigaOS release CDs, and refusing what cannot work.

Nothing here installs anything, because nothing can: AmigaOS 3.5 and 3.9 are
installed by a Commodore Installer script that runs on the Amiga, reads the
versions the live system has loaded and patches an existing installation in
place. What this covers is the part that can be settled before an emulator is
started, which is the part that otherwise costs an operator a long detour to
find out.

The failures worth guarding against are all of the same shape: something that
was knowable from the outset being discovered late. A disc that is not a
release, a machine that cannot run the release it is given, and a drive with
nothing on it to update are each knowable in a second and each waste minutes
when they are not checked.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.amigaos_cd import (
    NATIVE_68020_MACHINES,
    RELEASES,
    describe_releases,
    processor_ready,
    release_for_volume,
)
from app.disk_service import DiskError, DiskService
from app.image_opening import open_image_path
from tests.iso_fixture import build_iso, directory, file


class ReleaseRecognitionTests(unittest.TestCase):
    """A disc is identified by the volume name Commodore wrote on it."""

    def test_each_release_is_recognised_by_its_volume(self) -> None:
        self.assertEqual(release_for_volume("AmigaOS3.5").key, "3.5")
        self.assertEqual(release_for_volume("AmigaOS3.9").key, "3.9")

    def test_case_and_spacing_do_not_change_the_answer(self) -> None:
        self.assertEqual(release_for_volume("  amigaos3.9  ").key, "3.9")

    def test_another_amiga_cd_is_not_claimed(self) -> None:
        """A contribution disc or an OS4 CD is not a 3.x release."""
        self.assertIsNone(release_for_volume("AmigaOS 4.0 Install CD"))
        self.assertIsNone(release_for_volume("Aminet 15"))
        self.assertIsNone(release_for_volume(""))

    def test_both_releases_are_described_for_the_interface(self) -> None:
        described = describe_releases()
        self.assertEqual([item["key"] for item in described], ["3.5", "3.9"])
        for item in described:
            self.assertTrue(item["requires"])
            self.assertTrue(item["payload"].startswith("OS-Version"))


class ProcessorGateTests(unittest.TestCase):
    """Both releases need a 68020, and a plain 68000 machine cannot run them."""

    def test_a_plain_68000_machine_is_refused_with_a_reason(self) -> None:
        ready, reason = processor_ready("a500", ["kick31"])

        self.assertFalse(ready)
        self.assertIn("68020", reason)
        # The reason has to say what to change, not merely that it is wrong.
        self.assertIn("accelerator", reason)
        self.assertIn("PiStorm", reason)

    def test_a_machine_with_its_own_68020_qualifies(self) -> None:
        for machine in sorted(NATIVE_68020_MACHINES):
            with self.subTest(machine=machine):
                self.assertTrue(processor_ready(machine, [])[0])

    def test_an_accelerator_makes_an_older_machine_eligible(self) -> None:
        for addon in ("acc-68020", "acc-68030", "acc-68040", "acc-68060"):
            with self.subTest(addon=addon):
                self.assertTrue(processor_ready("a500", [addon])[0])

    def test_a_pistorm_makes_an_older_machine_eligible(self) -> None:
        """A PiStorm replaces the CPU outright, so it is well past an 020."""
        self.assertTrue(processor_ready("a500", ["pistorm"])[0])
        self.assertTrue(processor_ready("a1200", ["pistorm32"])[0])

    def test_a_floppy_drive_is_not_an_accelerator(self) -> None:
        self.assertFalse(processor_ready("a600", ["df0-internal", "fast-ram"])[0])


class PreflightTests(unittest.TestCase):
    """The whole check, against real images built in this tree."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        self.service = DiskService(self.root / "work")

    def _disc(self, volume: str, payload: str | None) -> object:
        tree = directory("")
        tree.add(file("Disk.info", b"icon"))
        if payload:
            tree.add(directory(payload)).add(file("Install", b"script"))
        path = self.root / f"{volume}.iso"
        path.write_bytes(build_iso(tree, volume=volume))
        return open_image_path(self.service, path)

    def _drive(self, machine: str, addons: list[str], *, workbench: bool) -> object:
        drive = self.service.create_blank("ffs-hard", "SYSTEM", "40MB")
        self.service.select_partition(drive, 0)
        drive.hardware_profile = {"machine": machine, "addons": addons}
        if workbench:
            self.service.make_directory(drive, "S")
            sequence = self.root / "seq"
            sequence.write_bytes(b"C:SetPatch\nC:IPrefs\nLoadWB\n")
            self.service.put(drive, "S/Startup-Sequence", sequence)
        return drive

    def test_a_prepared_drive_on_capable_hardware_is_ready(self) -> None:
        drive = self._drive("a1200", ["kick31"], workbench=True)
        disc = self._disc("AmigaOS3.9", "OS-Version3.9")

        checked = self.service.amigaos_cd_preflight(drive, disc)

        self.assertTrue(checked["ready"])
        self.assertEqual(checked["blocking"], [])
        self.assertEqual(checked["disc"]["label"], "AmigaOS 3.9")

    def test_a_drive_with_nothing_to_update_is_refused(self) -> None:
        """3.9 refuses a drive with no earlier release, so this says so first."""
        drive = self._drive("a1200", ["kick31"], workbench=False)
        disc = self._disc("AmigaOS3.9", "OS-Version3.9")

        checked = self.service.amigaos_cd_preflight(drive, disc)

        self.assertFalse(checked["ready"])
        self.assertTrue(any("Startup-Sequence" in item for item in checked["blocking"]))

    def test_a_68000_machine_is_refused_before_anything_starts(self) -> None:
        drive = self._drive("a500", ["kick31"], workbench=True)
        disc = self._disc("AmigaOS3.5", "OS-Version3.5")

        checked = self.service.amigaos_cd_preflight(drive, disc)

        self.assertFalse(checked["ready"])
        self.assertFalse(checked["processorReady"])
        self.assertTrue(any("68020" in item for item in checked["blocking"]))

    def test_every_blocking_reason_is_reported_at_once(self) -> None:
        """Fixing one at a time and rerunning is the loop this avoids."""
        drive = self._drive("a500", ["kick31"], workbench=False)
        disc = self._disc("Aminet 15", None)

        checked = self.service.amigaos_cd_preflight(drive, disc)

        self.assertGreaterEqual(len(checked["blocking"]), 3)

    def test_a_disc_named_like_a_release_but_lacking_its_files_is_refused(self) -> None:
        """The name alone is not enough to send somebody to an emulator."""
        drive = self._drive("a1200", ["kick31"], workbench=True)
        disc = self._disc("AmigaOS3.9", None)

        checked = self.service.amigaos_cd_preflight(drive, disc)

        self.assertFalse(checked["ready"])
        self.assertIn("OS-Version3.9", checked["disc"]["reason"])

    def test_a_partition_table_is_not_a_place_to_install_onto(self) -> None:
        drive = self.service.create_blank("ffs-hard", "SYSTEM", "40MB")
        drive.hardware_profile = {"machine": "a1200", "addons": []}
        disc = self._disc("AmigaOS3.9", "OS-Version3.9")

        checked = self.service.amigaos_cd_preflight(drive, disc)

        self.assertFalse(checked["ready"])
        self.assertTrue(any("partition" in item for item in checked["blocking"]))

    def test_a_release_is_not_recognised_on_something_that_is_not_a_cd(self) -> None:
        floppy = self.service.create_blank("adf", "Workbench3.1")

        found = self.service.amigaos_release_on(floppy)

        self.assertFalse(found["recognised"])
        self.assertIn("not a CD image", found["reason"])


class CdDriverTests(unittest.TestCase):
    """Making a disc visible, which a stock Workbench 3.1 drive cannot do.

    Everything needed is present after an ordinary Workbench install and none
    of it is switched on: the Extras disk puts the filing system in ``L:`` and
    the Storage disk puts the mountlist in ``Storage/DOSDrivers``, which is the
    drawer Workbench keeps what is not yet wanted in. AmigaDOS reads only
    ``Devs/DOSDrivers``, so the machine boots and sees no disc.
    """

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)
        self.service = DiskService(self.root / "work")
        self.drive = self.service.create_blank("ffs-hard", "SYSTEM", "40MB")
        self.service.select_partition(self.drive, 0)

    def _put(self, path: str, data: bytes) -> None:
        directory = "/".join(path.split("/")[:-1])
        if directory:
            try:
                self.service.make_directory(self.drive, directory)
            except DiskError:
                pass
        host = self.root / "payload"
        host.write_bytes(data)
        self.service.put(self.drive, path, host)

    #: The shape Commodore ships, with Device and Unit commented out.
    STOCK = (
        b"/* CD0 40.6\n * Device\t= scsi.device\n * Unit\t= 2\n */\n"
        b"FileSystem\t= L:CDFileSystem\nSectorSize\t= 2048\n"
        b"DosType\t\t= 0x43443031\n"
    )

    def test_a_stock_workbench_drive_cannot_see_a_disc(self) -> None:
        self._put("L/CDFileSystem", b"filesystem")
        self._put("Storage/DOSDrivers/CD0", self.STOCK)

        state = self.service.cd_driver_state(self.drive)

        self.assertTrue(state["filesystem"])
        self.assertTrue(state["parked"])
        self.assertFalse(state["active"])

    def test_activating_it_writes_a_mountlist_amigados_reads(self) -> None:
        self._put("L/CDFileSystem", b"filesystem")
        self._put("Storage/DOSDrivers/CD0", self.STOCK)

        result = self.service.activate_cd_driver(self.drive)

        self.assertTrue(result["changed"])
        self.assertTrue(self.service.cd_driver_state(self.drive)["active"])
        written = self.service.read_file(self.drive, "Devs/DOSDrivers/CD0").decode("latin-1")
        self.assertIn("uaescsi.device", written)
        self.assertIn("Unit", written)
        # The filing system line the mountlist came with has to survive.
        self.assertIn("L:CDFileSystem", written)

    def test_the_stock_scsi_defaults_are_not_left_active(self) -> None:
        """Commodore's defaults name a real drive at unit 2, which is not there."""
        self._put("L/CDFileSystem", b"filesystem")
        self._put("Storage/DOSDrivers/CD0", self.STOCK)

        self.service.activate_cd_driver(self.drive)

        written = self.service.read_file(self.drive, "Devs/DOSDrivers/CD0").decode("latin-1")
        active = [
            line for line in written.splitlines()
            if line.strip().lower().startswith(("device", "unit"))
        ]
        self.assertEqual(len(active), 2)
        self.assertTrue(all("scsi.device" not in line or "uaescsi" in line for line in active))

    def test_a_drive_that_already_has_it_is_left_alone(self) -> None:
        self._put("Devs/DOSDrivers/CD0", b"Device = uaescsi.device\nUnit = 0\n")

        result = self.service.activate_cd_driver(self.drive)

        self.assertFalse(result["changed"])
        self.assertIn("already active", result["detail"])

    def test_a_drive_with_no_mountlist_says_where_it_comes_from(self) -> None:
        result = self.service.activate_cd_driver(self.drive)

        self.assertFalse(result["changed"])
        self.assertIn("Storage disk", result["detail"])

    def test_the_preflight_says_the_driver_will_be_switched_on(self) -> None:
        self._put("L/CDFileSystem", b"filesystem")
        self._put("Storage/DOSDrivers/CD0", self.STOCK)
        self._put("S/Startup-Sequence", b"C:SetPatch\n")
        self.drive.hardware_profile = {"machine": "a1200", "addons": ["kick31"]}
        tree = directory("")
        tree.add(directory("OS-Version3.9")).add(file("Install", b"script"))
        path = self.root / "disc.iso"
        path.write_bytes(build_iso(tree, volume="AmigaOS3.9"))
        disc = open_image_path(self.service, path)

        checked = self.service.amigaos_cd_preflight(self.drive, disc)

        self.assertTrue(checked["ready"])
        self.assertFalse(checked["cdDriver"]["active"])
        self.assertTrue(any("activated as CD0:" in item for item in checked["warnings"]))


class EmulatorAttachmentTests(unittest.TestCase):
    """A CD is reached through a CD drive, not one of the four floppy drives."""

    def test_the_release_list_and_the_drive_limit_agree(self) -> None:
        from app.emulator_config import MAXIMUM_CD_DRIVES

        self.assertEqual(MAXIMUM_CD_DRIVES, 1)
        self.assertEqual(len(RELEASES), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
