"""Installing AmigaOS 3.5 and 3.9 from the release CD, and the update packs.

The installation is made by this application and written into the drive. What
is guarded here is what makes such an installation correct: that the newest
layer supplies each file, that the destinations are the ones the installer
script uses, that what is already on the drive and is not part of the release
survives, and that a pack whose fixes could not be applied says which ones.

The discs and packs are built by ``tests.amigaos_fixture`` to the shape of the
real ones. The emulator is stood in for by a process that does what the Amiga
side does: it reads the configuration it was given, writes the files the
payload names into the drawer it was told to update, and leaves the marker.
"""

from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app import amigaos_cd, boingbag, boingbag_update
from app.disk_service import DiskError, DiskService
from app.image_opening import open_image_path
from app.iso9660 import Iso9660Image
from app.lha import LHAArchive
from app.system_tree import SystemTree
from tests import amigaos_fixture as fixture

try:
    from app.server import create_app
except ModuleNotFoundError:  # Flask is absent from the light host test env.
    create_app = None


def constant(data: bytes):
    return lambda: data


class SystemTreeTests(unittest.TestCase):
    def test_a_later_layer_replaces_the_file_an_earlier_one_supplied(self) -> None:
        tree = SystemTree()
        tree.add("C/SetPatch", constant(b"old"), 3, "Workbench 3.5 base")
        tree.add("C/SetPatch", constant(b"newer"), 5, "Workbench 3.9")

        self.assertEqual(len(tree), 1)
        self.assertEqual(tree.get("C/SetPatch").read(), b"newer")
        self.assertEqual(tree.get("c/setpatch").origin, "Workbench 3.9")
        self.assertEqual(tree.origins(), {"Workbench 3.9": 1})

    def test_names_are_compared_the_way_amigados_compares_them(self) -> None:
        tree = SystemTree()
        tree.add("C/LoadWB", constant(b"old"), 3, "base")
        tree.add("c/loadwb", constant(b"new"), 3, "overlay")

        found = tree.files()
        self.assertEqual([item.path for item in found], ["C/LoadWB"])
        self.assertEqual(found[0].read(), b"new")
        self.assertEqual(tree.drawers(), ["C"])

    def test_a_drawer_no_file_lands_in_is_still_made(self) -> None:
        tree = SystemTree()
        tree.add_drawer("Devs/Monitors")
        tree.add_drawer("Expansion")
        tree.add("Devs/system-configuration", constant(b"x"), 1, "base")

        self.assertEqual(tree.empty_drawers(), ["Expansion", "Devs/Monitors"])

    def test_a_file_and_a_drawer_of_one_name_are_reported(self) -> None:
        tree = SystemTree()
        tree.add("Tools/Commodities", constant(b"file"), 4, "base")

        self.assertFalse(tree.add_drawer("Tools/Commodities"))
        self.assertFalse(tree.add("Tools", constant(b"file"), 4, "overlay"))
        self.assertEqual(len(tree.warnings), 2)

    def test_a_path_may_arrive_in_any_spelling(self) -> None:
        tree = SystemTree()
        tree.add("Devs\\Keymaps\\gb", constant(b"k"), 1, "pack")
        self.assertIn("Devs/Keymaps/gb", tree)


class LayoutTests(unittest.TestCase):
    """Where each tree on the disc lands, read out of the installer script."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)

    def plan(self, **options) -> dict:
        release = amigaos_cd.RELEASES_BY_VOLUME[
            f"amigaos{options.get('release', '3.9')}"
        ]
        with Iso9660Image(fixture.release_disc(self.folder, **options)) as image:
            planned = amigaos_cd.plan_release(image, release)
            planned["read"] = {
                item.path: item.read() for item in planned["tree"].files()
            }
        return planned

    def test_the_overlay_wins_over_the_base_it_is_laid_on(self) -> None:
        planned = self.plan()
        tree = planned["tree"]

        self.assertEqual(planned["read"]["C/SetPatch"], b"\x00\x00\x03\xf3 setpatch 3.9")
        self.assertEqual(tree.get("Libs/icon.library").origin, "Workbench 3.9")
        self.assertEqual(tree.get("S/Startup-Sequence").origin, "Workbench 3.5 base")
        # The updated commands are a layer of their own, copied after both.
        self.assertEqual(planned["read"]["C/LoadWB"], b"\x00\x00\x03\xf3 loadwb 3.9")

    def test_the_destinations_that_are_not_the_obvious_ones(self) -> None:
        tree = self.plan()["tree"]

        self.assertIn("Prefs/Presets/Backdrops/Boing.jpg", tree)
        self.assertNotIn("Backdrops/Boing.jpg", tree)
        # Keymaps and printers are copied out of Storage into Devs, and stay
        # in Storage as well.
        self.assertIn("Devs/Keymaps/gb", tree)
        self.assertIn("Storage/Keymaps/gb", tree)
        self.assertIn("Devs/Printers/EpsonQ", tree)
        self.assertIn("Libs/68040.library", tree)
        self.assertIn("Locale/Catalogs/deutsch/Sys/workbench.catalog", tree)

    def test_nothing_from_outside_the_layers_is_installed(self) -> None:
        tree = self.plan()["tree"]

        self.assertNotIn("OS3.9Install", tree)
        self.assertNotIn("Disk.info", tree)
        self.assertFalse(any("Emergency" in item.path for item in tree.files()))

    def test_protection_bits_travel_with_the_files(self) -> None:
        tree = self.plan()["tree"]

        self.assertEqual(tree.get("C/AddBuffers").protection, fixture.PURE)
        self.assertEqual(tree.get("S/Startup-Sequence").protection, fixture.SCRIPT)

    def test_the_35_disc_lays_workbench_31_down_first(self) -> None:
        planned = self.plan(release="3.5")

        self.assertEqual(
            [layer["label"] for layer in planned["layers"]][:3],
            ["Workbench 3.1", "Extras 3.1", "Workbench 3.5"],
        )
        self.assertEqual(planned["read"]["C/SetPatch"], b"\x00\x00\x03\xf3 setpatch 3.5")
        self.assertEqual(planned["tree"].get("S/Startup-Sequence").origin, "Workbench 3.1")
        self.assertIn("Tools/Calculator", planned["tree"])
        self.assertIn("Devs/Keymaps/gb", planned["tree"])

    def test_a_missing_tree_is_named_and_a_required_one_is_marked(self) -> None:
        planned = self.plan(without=("OS-Version3.9/Workbench3.9", "OS-Version3.9/Locale"))

        missing = {item["label"]: item["required"] for item in planned["missing"]}
        self.assertTrue(missing["Workbench 3.9"])
        self.assertFalse(missing["Locale"])

    def test_a_disc_that_lost_its_name_is_recognised_by_its_trees(self) -> None:
        path = fixture.release_disc(self.folder, volume="CDROM")
        with Iso9660Image(path) as image:
            self.assertIsNone(amigaos_cd.release_for_volume(image.volume))
            self.assertEqual(amigaos_cd.release_by_layout(image).key, "3.9")


class PackTests(unittest.TestCase):
    def archives(self, *packs: tuple[str, bytes]):
        return boingbag.open_archives(list(packs))

    def test_a_file_comment_is_not_read_as_part_of_the_name(self) -> None:
        """BoingBag 1 holds a browser cache whose comments are addresses."""
        archive = LHAArchive(fixture.boingbag_1())

        member = archive.find("BoingBag3.9-1/Internet/AWeb/cache/paper.gif")
        self.assertIsNotNone(member)
        self.assertEqual(member.comment, "http://example.org/paper.gif")

    def test_packs_are_found_at_whatever_depth_and_in_published_order(self) -> None:
        archives = self.archives(
            ("all.lha", fixture.lha_archive(fixture.boingbag_34(nested="Updates/"))),
            ("two.lha", fixture.boingbag_2()),
            ("one.lha", fixture.boingbag_1()),
        )

        found = boingbag.packs_in(archives, "3.9")

        self.assertEqual([pack.bag.key for pack in found], ["3.9-1", "3.9-2", "3.9-34"])
        self.assertEqual(found[2].root, "Updates/BoingBag3.9-3&4")
        self.assertEqual(boingbag.packs_in(archives, "3.5"), [])

    def test_only_the_packs_asked_for_are_taken(self) -> None:
        archives = self.archives(("all.lha", fixture.collection()))

        found = boingbag.packs_in(archives, "3.9", ["3.9-34"])

        self.assertEqual([pack.bag.key for pack in found], ["3.9-34"])

    def test_an_archive_that_holds_no_pack_is_named(self) -> None:
        archives = self.archives(
            ("faq.lha", fixture.lha_archive({"Archives/faq.html": b"<html>"})),
            ("one.lha", fixture.boingbag_1()),
        )

        self.assertEqual(boingbag.unrecognised(archives, "3.9"), ["faq.lha"])

    def test_something_that_is_not_an_archive_is_refused_by_name(self) -> None:
        with self.assertRaises(DiskError) as raised:
            self.archives(("BoingBag39-1.lha", b"<html>Not found</html>"))
        self.assertIn("BoingBag39-1.lha", str(raised.exception))

    def test_a_locked_payload_is_known_by_its_directory_alone(self) -> None:
        found = boingbag.packs_in(self.archives(("one.lha", fixture.boingbag_1())), "3.9")[0]

        self.assertTrue(found.locked)
        self.assertEqual(found.locked_entries(), fixture.LOCKED_1)

    def test_plain_files_go_over_the_system(self) -> None:
        tree = SystemTree()
        tree.add("Locale/Catalogs/deutsch/Sys/workbench.catalog", constant(b"cd"), 2, "Locale")
        found = boingbag.packs_in(self.archives(("one.lha", fixture.boingbag_1())), "3.9")[0]

        report = boingbag.plan_pack(found, tree, "a1200", [])

        self.assertEqual(report["files"], 3)
        self.assertEqual(
            tree.get("Locale/Catalogs/deutsch/Sys/workbench.catalog").read(), b"katalog BB1"
        )
        self.assertIn("Contribution/Readme", tree)
        self.assertEqual(
            tree.get("Internet/AWeb/cache/paper.gif").comment, "http://example.org/paper.gif"
        )
        # The pack's own tools and its payload are not part of the system.
        self.assertNotIn("C/Updater", tree)
        self.assertNotIn("AmigaOS-Update", tree)

    def plan_34(self, machine: str, addons: list[str]) -> tuple[SystemTree, dict]:
        tree = SystemTree()
        found = boingbag.packs_in(self.archives(("all.lha", fixture.collection())), "3.9",
                                  ["3.9-34"])[0]
        return tree, boingbag.plan_pack(found, tree, machine, addons)

    def test_the_build_for_this_processor_is_the_one_installed(self) -> None:
        tree, report = self.plan_34("a1200", ["pistorm32"])

        self.assertEqual(tree.get("Libs/mpega.library").read(), b"mpega 040 fpu")
        self.assertEqual(tree.get("Libs/xadmaster.library").read(), b"xad 020")
        self.assertEqual(tree.get("Devs/scsi.device").read(), b"scsi")
        self.assertEqual(tree.get("C/SetPatch").read(), b"\x00\x00\x03\xf3 setpatch BB4")
        self.assertIn("Devs/Keymaps/pl", tree)
        # The builds that were not chosen are not left lying in Libs.
        self.assertNotIn("Libs/xadmaster_060.library", tree)
        self.assertNotIn("Files2/Libs/mpega040.library", tree)
        self.assertEqual(len(report["variants"]), 3)

    def test_a_68060_gets_its_own_builds(self) -> None:
        tree, _report = self.plan_34("a1200", ["acc-68060"])

        self.assertEqual(tree.get("Libs/xadmaster.library").read(), b"xad 060")
        self.assertEqual(tree.get("Libs/mpega.library").read(), b"mpega 060 fpu")

    def test_a_machine_with_no_ide_port_is_not_given_a_driver_for_one(self) -> None:
        tree, _report = self.plan_34("a4000", [])

        self.assertNotIn("Devs/scsi.device", tree)

    def test_the_processor_a_profile_describes(self) -> None:
        self.assertEqual(boingbag.processor_of("a1200", []), ("68020", False))
        self.assertEqual(boingbag.processor_of("a500", ["pistorm"]), ("68040", True))
        self.assertEqual(boingbag.processor_of("a1200", ["acc-68030"]), ("68030", True))
        self.assertEqual(boingbag.processor_of("a4000", ["acc-68060"]), ("68060", True))

    def test_the_35_packs_use_the_35_layout(self) -> None:
        tree = SystemTree()
        found = boingbag.packs_in(
            self.archives(("bb.lha", fixture.boingbag_35_1())), "3.5"
        )[0]

        boingbag.plan_pack(found, tree, "a1200", [])

        self.assertIn("C/SetPatch", tree)
        self.assertIn("Devs/Printers/EpsonQ", tree)
        self.assertIn("Devs/AmigaOS ROM Update", tree)


class SidecarTests(unittest.TestCase):
    """FS-UAE keeps protection bits for a directory drive beside each file."""

    def test_the_bits_are_written_the_way_list_prints_them(self) -> None:
        self.assertTrue(boingbag_update.sidecar_text(0).startswith("----rwed "))
        self.assertTrue(boingbag_update.sidecar_text(fixture.SCRIPT).startswith("-s--rw-d "))
        self.assertTrue(boingbag_update.sidecar_text(fixture.PURE).startswith("--p-rwed "))

    def test_they_come_back_as_they_went(self) -> None:
        for value in (0, fixture.PURE, fixture.SCRIPT, 0x0F, 0x10):
            with self.subTest(value=value):
                text = boingbag_update.sidecar_text(value, "a comment here")
                self.assertEqual(
                    boingbag_update.protection_from_sidecar(text), (value, "a comment here")
                )


class _Amiga:
    """What the emulated machine does, without the machine.

    Reads the configuration it was started with, checks that the startup it
    was given really asks for the Updater, and writes each file the payloads
    name into the drawer it was told to update.
    """

    runs: list[dict] = []
    finishes = True

    def __init__(self, arguments, **_options) -> None:
        self.returncode = None
        config = Path(arguments[-1])
        settings = dict(
            line.split(" = ", 1) for line in config.read_text().splitlines() if " = " in line
        )
        labels = {
            settings[f"{key}_label"]: Path(settings[key])
            for key in ("hard_drive_0", "hard_drive_1", "hard_drive_2")
        }
        boot = labels[boingbag_update.BOOT_LABEL]
        pack = labels[boingbag_update.PACK_LABEL]
        target = labels[boingbag_update.TARGET_LABEL]
        startup = (boot / "S" / "User-Startup").read_text(encoding="latin-1")
        record = {
            "settings": settings,
            "startup": startup,
            "boot": sorted(
                path.relative_to(boot).as_posix() for path in boot.rglob("*") if path.is_file()
            ),
            "pack": sorted(
                path.relative_to(pack).as_posix() for path in pack.rglob("*") if path.is_file()
            ),
            "disc": Path(settings["cdrom_drive_0"]).is_file(),
            "rom": Path(settings["kickstart_file"]).read_bytes(),
            "setpatch": (boot / "C" / "SetPatch").read_bytes(),
            "targetWasEmpty": not any(target.iterdir()),
        }
        type(self).runs.append(record)
        if not type(self).finishes:
            return
        updater = (pack / "C" / "Updater").read_bytes()
        for line in startup.splitlines():
            if not line.startswith("BB:C/Updater "):
                continue
            payload = pack / line.split()[1].removeprefix("BB:")
            with zipfile.ZipFile(io.BytesIO(payload.read_bytes())) as bundle:
                for name in bundle.namelist():
                    written = target / name
                    written.parent.mkdir(parents=True, exist_ok=True)
                    written.write_bytes(b"fixed by " + updater + b": " + name.encode())
                    Path(str(written) + ".uaem").write_text(
                        "--p-rwed 2002-03-20 12:00:00.00 \n"
                    )
        (boot / boingbag_update.MARKER).write_text("applied\n")

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    kill = terminate

    def wait(self, timeout=None):
        return self.returncode


class InstallFixture(unittest.TestCase):
    machine = "a1200"
    addons = ["pistorm32"]

    def setUp(self) -> None:
        fsync = patch("amiganut.filesystem.blocks.os.fsync")
        fsync.start()
        self.addCleanup(fsync.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.service = DiskService(self.folder / "work")
        self.rom = self.folder / "kick31.rom"
        self.rom.write_bytes(b"kickstart 3.1")
        _Amiga.runs = []
        _Amiga.finishes = True

        import subprocess

        real = subprocess.Popen

        def start(arguments, *more, **options):
            # Only the emulator is stood in for. The module is the one every
            # other part of the application starts its own programs through.
            if not str(arguments[-1]).endswith("updater.fs-uae"):
                return real(arguments, *more, **options)
            return _Amiga(arguments, **options)

        for target, value in (
            ("app.boingbag_update.subprocess.Popen", start),
            ("app.boingbag_update.POLL_SECONDS", 0.01),
            ("app.boingbag_update.is_confined", lambda _executable: False),
            ("app.emulator_config.ManagedEmulator.available", True),
            ("app.emulator_config.kickstart_for", lambda _machine: self.rom),
        ):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def drive(self, filesystem: str = "pfs3", size: str = "64MB"):
        drive = self.service.create_drive("Target", size, [
            {"name": "DH0", "label": "System", "filesystem": filesystem, "bootable": True},
        ])
        drive.hardware_profile = {"machine": self.machine, "addons": list(self.addons)}
        self.service.select_partition(drive, 0)
        return drive

    def disc(self, **options):
        return open_image_path(self.service, fixture.release_disc(self.folder, **options))

    def put(self, drive, path: str, data: bytes) -> None:
        from app import volume_copy

        volume_copy.write_file(self.service, drive, path, data)

    def names(self, drive, drawer: str = "") -> list[str]:
        return [
            entry["name"] for entry in self.service.list_directory(drive, drawer)["entries"]
        ]

    def protection(self, drive, path: str) -> int:
        drawer, _slash, leaf = path.rpartition("/")
        for entry in self.service.list_directory(drive, drawer)["entries"]:
            if entry["name"] == leaf:
                return int(entry["protection"] or 0)
        raise AssertionError(f"{path} is not on the drive")


class InstallTests(InstallFixture):
    def test_an_empty_drive_holds_the_release_afterwards(self) -> None:
        drive = self.drive()

        result = self.service.install_amigaos_cd(drive, self.disc())

        self.assertEqual(result["release"], "AmigaOS 3.9")
        self.assertEqual(result["over"], "empty")
        self.assertEqual(result["written"], 15)
        self.assertEqual(result["warnings"], [])
        self.assertEqual(
            self.service.read_file(drive, "C/SetPatch"), b"\x00\x00\x03\xf3 setpatch 3.9"
        )
        self.assertEqual(
            self.service.read_file(drive, "S/Startup-Sequence").decode("latin-1"),
            fixture.STARTUP,
        )
        self.assertEqual(
            self.service.read_file(drive, "Prefs/Presets/Backdrops/Boing.jpg"), b"picture"
        )
        # The drawer the disc carried empty, and the ones no disc provides.
        for drawer in ("Expansion", "T", "Trashcan"):
            self.assertIn(drawer, self.names(drive))
        self.assertIn("DOSDrivers", self.names(drive, "Devs"))
        self.assertEqual(_Amiga.runs, [], "no emulator is needed to install the release")

    def test_the_same_installation_onto_each_filing_system(self) -> None:
        for filesystem in ("ffs", "pfs3"):
            with self.subTest(filesystem=filesystem):
                drive = self.drive(filesystem)
                result = self.service.install_amigaos_cd(drive, self.disc())
                self.assertEqual(result["written"], 15)
                self.assertEqual(
                    self.service.read_file(drive, "Libs/icon.library"), b"icon 3.9" * 600
                )

    def test_a_pure_command_is_still_executable(self) -> None:
        """A protection long is a number, and is not read as hexadecimal text.

        Reading 32 as the digits "32" gives 0x32, which denies execute, and the
        machine stops at the first pure command in its Startup-Sequence.
        """
        drive = self.drive()

        self.service.install_amigaos_cd(drive, self.disc())

        self.assertEqual(self.protection(drive, "C/AddBuffers"), fixture.PURE)
        self.assertEqual(self.protection(drive, "S/Startup-Sequence"), fixture.SCRIPT)
        self.assertEqual(self.protection(drive, "C/SetPatch"), 0)

    def test_installing_over_a_system_is_an_upgrade_that_keeps_what_is_there(self) -> None:
        drive = self.drive()
        self.put(drive, "S/Startup-Sequence", b"C:LoadWB ; from 3.1\n")
        self.put(drive, "C/SetPatch", b"setpatch 3.1")
        self.put(drive, "Games/Turrican/Turrican.slave", b"slave")
        self.put(drive, "S/User-Startup", b"Assign Work: DH1:\n")

        result = self.service.install_amigaos_cd(drive, self.disc())

        self.assertEqual(result["over"], "system")
        self.assertEqual(
            self.service.read_file(drive, "C/SetPatch"), b"\x00\x00\x03\xf3 setpatch 3.9"
        )
        self.assertEqual(
            self.service.read_file(drive, "Games/Turrican/Turrican.slave"), b"slave"
        )
        self.assertEqual(self.service.read_file(drive, "S/User-Startup"), b"Assign Work: DH1:\n")

    def test_a_68000_machine_is_refused_and_nothing_is_written(self) -> None:
        self.machine, self.addons = "a500", []
        drive = self.drive()
        before = self.names(drive)

        with self.assertRaises(DiskError) as raised:
            self.service.install_amigaos_cd(drive, self.disc())

        self.assertIn("68020", str(raised.exception))
        self.assertEqual(self.names(drive), before)

    def test_an_incomplete_disc_is_refused_and_nothing_is_written(self) -> None:
        drive = self.drive()

        with self.assertRaises(DiskError) as raised:
            self.service.install_amigaos_cd(
                drive, self.disc(without=("OS-Version3.9/Workbench3.9",))
            )

        self.assertIn("Workbench 3.9", str(raised.exception))
        self.assertEqual(self.names(drive), [])

    def test_a_volume_with_no_room_is_refused_before_anything_is_written(self) -> None:
        drive = self.drive()
        self.put(drive, "Filler", b"\xAA" * (50 * 1024 * 1024))
        self.put(drive, "Filler2", b"\xAA" * (6 * 1024 * 1024))
        before = self.names(drive)
        with patch(
            "tests.amigaos_fixture.release_39_files",
            lambda: {
                "OS-Version3.9/Workbench3.5/Libs/big.library": (b"\x55" * (6 * 1024 * 1024), None),
                "OS-Version3.9/Workbench3.9/C/SetPatch": (b"setpatch", None),
            },
        ):
            path = fixture.release_disc(self.folder, name="large.iso")
        disc = open_image_path(self.service, path)

        with self.assertRaises(DiskError) as raised:
            self.service.install_amigaos_cd(drive, disc)

        self.assertIn("MB free", str(raised.exception))
        self.assertEqual(self.names(drive), before)

    def test_the_35_disc_installs_a_whole_system(self) -> None:
        drive = self.drive()

        result = self.service.install_amigaos_cd(drive, self.disc(release="3.5"))

        self.assertEqual(result["release"], "AmigaOS 3.5")
        self.assertEqual(
            self.service.read_file(drive, "C/SetPatch"), b"\x00\x00\x03\xf3 setpatch 3.5"
        )
        self.assertEqual(
            self.service.read_file(drive, "C/LoadWB"), b"\x00\x00\x03\xf3 loadwb 3.1"
        )
        self.assertIn("Calculator", self.names(drive, "Tools"))

    def test_the_preflight_says_what_will_be_installed(self) -> None:
        drive = self.drive()

        checked = self.service.amigaos_cd_preflight(drive, self.disc())

        self.assertTrue(checked["ready"], checked["blocking"])
        self.assertEqual(checked["over"], "empty")
        self.assertEqual(checked["plan"]["files"], 15)
        self.assertEqual(checked["plan"]["layers"][0]["label"], "Workbench 3.5 base")
        self.assertTrue(checked["updater"]["available"])


class PackInstallTests(InstallFixture):
    def packs(self) -> list[tuple[str, bytes]]:
        return [
            ("BB1-4.lha", fixture.lha_archive(fixture.boingbag_34())),
            ("BoingBag39-2.lha", fixture.boingbag_2()),
            ("BoingBag39-1.lha", fixture.boingbag_1()),
        ]

    def test_the_packs_go_over_the_release_oldest_first(self) -> None:
        drive = self.drive()

        result = self.service.install_amigaos_cd(drive, self.disc(), packs=self.packs())

        self.assertEqual(
            [pack["key"] for pack in result["packs"]], ["3.9-1", "3.9-2", "3.9-34"]
        )
        self.assertTrue(all(pack.get("applied", True) for pack in result["packs"]))
        self.assertEqual(result["warnings"], [])
        # BoingBags 3 and 4 carry the newest SetPatch, and are laid on last.
        self.assertEqual(
            self.service.read_file(drive, "C/SetPatch"), b"\x00\x00\x03\xf3 setpatch BB4"
        )
        # Only BoingBag 1 fixes IPrefs and only BoingBag 2 fixes HDToolBox.
        self.assertEqual(
            self.service.read_file(drive, "C/IPrefs"),
            b"fixed by \x00\x00\x03\xf3 updater 1: C/IPrefs",
        )
        self.assertEqual(
            self.service.read_file(drive, "Tools/HDToolBox"),
            b"fixed by \x00\x00\x03\xf3 updater 2: Tools/HDToolBox",
        )
        self.assertEqual(
            self.service.read_file(drive, "Libs/xadmaster.library"), b"xad 020"
        )
        self.assertEqual(self.service.read_file(drive, "Libs/dos.library"), b"dos 42.1")
        self.assertEqual(self.protection(drive, "C/IPrefs"), fixture.PURE)

    def test_the_updater_is_given_what_it_needs_to_run(self) -> None:
        drive = self.drive()

        self.service.install_amigaos_cd(drive, self.disc(), packs=self.packs())

        self.assertEqual(len(_Amiga.runs), 2)
        first, second = _Amiga.runs
        self.assertIn('BB:C/Updater BB:AmigaOS-Update "Updating:"', first["startup"])
        self.assertTrue(first["startup"].rstrip().endswith('"applied"'))
        self.assertIn("BB:C/Updater BB:XAD-Update", second["startup"])
        self.assertEqual(first["pack"], ["AmigaOS-Update", "C/Updater"])
        # XAD will not write over a file, so it is given an empty drawer.
        self.assertTrue(first["targetWasEmpty"])
        self.assertTrue(first["disc"], "the Updater looks for the disc it updates")
        self.assertEqual(first["rom"], b"kickstart 3.1")
        self.assertIn("S/Startup-Sequence", first["boot"])
        # The second pack runs on a system the first has already fixed.
        self.assertEqual(first["setpatch"], b"\x00\x00\x03\xf3 setpatch 3.9")
        self.assertEqual(
            second["setpatch"], b"fixed by \x00\x00\x03\xf3 updater 1: C/SetPatch"
        )

    def test_nothing_staged_for_the_run_is_left_on_the_host_or_the_drive(self) -> None:
        drive = self.drive()

        self.service.install_amigaos_cd(drive, self.disc(), packs=self.packs())

        staged = self.folder / "work" / "emulator-media"
        self.assertEqual([path for path in staged.rglob("*") if path.is_file()], [])
        self.assertNotIn(boingbag_update.MARKER, self.names(drive))
        self.assertNotIn("User-Startup", self.names(drive, "S"))
        self.assertFalse(any(name.endswith(".uaem") for name in self.names(drive, "C")))

    def test_with_no_emulator_the_plain_files_go_on_and_the_rest_are_named(self) -> None:
        drive = self.drive()
        with patch("app.emulator_config.ManagedEmulator.available", False):
            result = self.service.install_amigaos_cd(
                drive, self.disc(), packs=self.packs()
            )

        self.assertEqual(_Amiga.runs, [])
        first = result["packs"][0]
        self.assertFalse(first["applied"])
        self.assertEqual(first["leftOut"], sorted(fixture.LOCKED_1))
        self.assertIn("FS-UAE is not installed", first["reason"])
        self.assertTrue(any("BoingBag 1" in item and "3 system files" in item
                            for item in result["warnings"]))
        self.assertIn("Readme", self.names(drive, "Contribution"))
        self.assertNotIn("IPrefs", self.names(drive, "C"))

    def test_a_run_that_never_finishes_is_stopped_and_reported(self) -> None:
        drive = self.drive()
        _Amiga.finishes = False
        with patch("app.boingbag_update.IDLE_TIMEOUT", 0.05):
            original = boingbag_update.run_updater

            def hurried(*arguments, **options):
                return original(*arguments, idle_timeout=0.05, **options)

            with patch("app.boingbag_update.run_updater", hurried):
                result = self.service.install_amigaos_cd(
                    drive, self.disc(), packs=[("one.lha", fixture.boingbag_1())]
                )

        self.assertFalse(result["packs"][0]["applied"])
        self.assertIn("did not finish", result["packs"][0]["reason"])
        # The release itself is installed all the same.
        self.assertEqual(
            self.service.read_file(drive, "C/SetPatch"), b"\x00\x00\x03\xf3 setpatch 3.9"
        )

    def test_a_pack_for_the_other_release_is_passed_over_and_named(self) -> None:
        drive = self.drive()

        result = self.service.install_amigaos_cd(
            drive, self.disc(), packs=[("bb35.lha", fixture.boingbag_35_1())]
        )

        self.assertEqual(result["packs"], [])
        self.assertEqual(result["packsUnrecognised"], ["bb35.lha"])
        self.assertEqual(
            self.service.read_file(drive, "C/SetPatch"), b"\x00\x00\x03\xf3 setpatch 3.9"
        )


@unittest.skipIf(create_app is None, "Flask is available in the application container")
class RouteTests(InstallFixture):
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
        self.headers = {"X-Amiga-Desktop-Token": self.TOKEN}
        created = self.client.post("/api/images/create", json={
            "format": "ffs-hard", "title": "Empty", "capacity": "64MB",
            "drive": {"partitions": [
                {"name": "DH0", "label": "System", "filesystem": "pfs3", "bootable": True},
            ]},
        }, headers=self.headers)
        self.assertEqual(created.status_code, 200, created.get_json())
        self.image = created.get_json()["image"]["id"]
        profile = self.client.patch(
            f"/api/images/{self.image}/hardware-profile",
            json={"machine": "a1200", "addons": ["pistorm32"]},
            headers=self.headers,
        )
        self.assertEqual(profile.status_code, 200, profile.get_json())
        opened = self.client.post(
            "/api/desktop/open-path",
            json={"path": str(fixture.release_disc(self.folder))},
            headers=self.headers,
        )
        self.assertEqual(opened.status_code, 200, opened.get_json())
        self.disc_id = opened.get_json()["image"]["id"]

    def form(self, **fields):
        data = {"disc": self.disc_id, "partition": "0", **fields}
        return self.client.post(
            f"/api/images/{self.image}/install/amigaos-cd",
            data=data, headers=self.headers, content_type="multipart/form-data",
        )

    def test_the_packs_in_what_was_chosen_are_described(self) -> None:
        answered = self.client.post(
            "/api/install/amigaos-cd/packs",
            data={"packs": [
                (io.BytesIO(fixture.collection()), "BB1-4.lha"),
                (io.BytesIO(fixture.lha_archive({"Archives/faq.html": b"x"})), "faq.lha"),
            ]},
            headers=self.headers, content_type="multipart/form-data",
        )

        self.assertEqual(answered.status_code, 200, answered.get_json())
        body = answered.get_json()
        self.assertEqual([pack["key"] for pack in body["packs"]], ["3.9-1", "3.9-2", "3.9-34"])
        self.assertEqual(body["packs"][0]["lockedFiles"], 3)
        self.assertEqual(body["unrecognised"], ["faq.lha"])

    def test_the_route_installs_and_can_be_undone(self) -> None:
        answered = self.form(
            packs=[(io.BytesIO(fixture.collection()), "BB1-4.lha")],
            chosenPacks="3.9-1,3.9-34",
        )

        self.assertEqual(answered.status_code, 200, answered.get_json())
        report = answered.get_json()["amigaos"]
        self.assertEqual([pack["key"] for pack in report["packs"]], ["3.9-1", "3.9-34"])
        listing = self.client.get(
            f"/api/images/{self.image}/tree?path=C&partition=0", headers=self.headers
        ).get_json()
        self.assertIn("SetPatch", [entry["name"] for entry in listing["entries"]])
        listed = self.client.get(
            f"/api/images/{self.image}/checkpoints", headers=self.headers
        ).get_json()
        self.assertEqual(
            listed["checkpoints"][0]["reason"], "installing AmigaOS from a release CD"
        )

    def test_unticking_every_pack_installs_the_release_alone(self) -> None:
        answered = self.form(
            packs=[(io.BytesIO(fixture.collection()), "BB1-4.lha")], chosenPacks="",
        )

        self.assertEqual(answered.status_code, 200, answered.get_json())
        self.assertEqual(answered.get_json()["amigaos"]["packs"], [])
        self.assertEqual(_Amiga.runs, [])

    def test_something_that_is_not_an_archive_is_refused(self) -> None:
        answered = self.form(packs=[(io.BytesIO(b"<html>"), "BoingBag39-1.lha")])

        self.assertEqual(answered.status_code, 400)
        self.assertIn("not an LHA archive", answered.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
