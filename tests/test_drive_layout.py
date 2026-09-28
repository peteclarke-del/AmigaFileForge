"""Planning a hard drive, and the handlers a drive is given.

The plan is what the partition editor shows and what is then written, so the
limits it applies are the ones a person meets: an FFS partition is kept to a
size FFS copes with, a filing system with no handler to hand is refused, and
a partition that a Kickstart 3.1 machine could not reach is said to be so.
"""

from __future__ import annotations

import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import drive_layout, filesystem_handlers
from app.errors import DiskError

MIB = drive_layout.MIB
GIB = drive_layout.GIB


def load_file(version: str) -> bytes:
    """A load file in miniature, carrying the version string of a real one."""
    return (
        struct.pack(">6I", 0x3F3, 0, 1, 0, 0, 64)
        + struct.pack(">2I", 0x3E9, 64)
        + f"$VER: {version}".encode("latin-1").ljust(256, b"\0")
        + struct.pack(">I", 0x3F2)
    )


PFS3 = load_file("Professional-File-System-III 19.2 PFS3AIO-VERSION (2.10.2018)")
SFS = load_file("SmartFilesystem 1.279 (12.10.2008)")
FFS = load_file("FastFileSystem 47.4 (1.1.2021)")


class HandlerStoreFixture(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        environment = patch.dict(
            os.environ, {"AMIGA_FILE_FORGE_HANDLER_DIR": str(self.folder / "handlers")}
        )
        environment.start()
        self.addCleanup(environment.stop)


class SizeTests(unittest.TestCase):
    def test_sizes_are_counted_in_powers_of_1024_however_they_are_spelt(self) -> None:
        for text, expected in (
            ("128GB", 128 * GIB),
            ("128 GiB", 128 * GIB),
            ("128g", 128 * GIB),
            ("1.5GB", 3 * GIB // 2),
            ("1,5 gb", 3 * GIB // 2),
            ("512MB", 512 * MIB),
            ("2TB", 2048 * GIB),
            ("880K", 880 * 1024),
            ("4096", 4096),
            (4096, 4096),
        ):
            with self.subTest(text=text):
                self.assertEqual(drive_layout.parse_size(text), expected)

    def test_what_is_not_a_size_is_refused_in_words(self) -> None:
        for text in ("", "big", "12XB", "-4GB", None, True):
            with self.subTest(text=text):
                with self.assertRaises(DiskError):
                    drive_layout.parse_size(text)

    def test_an_image_for_a_card_is_smaller_than_the_card(self) -> None:
        """A 128 GB card holds 128 thousand million bytes, less what its maker keeps."""
        size = drive_layout.card_bytes(128)
        self.assertLess(size, 128 * 1000 ** 3)
        self.assertGreater(size, 120 * 1000 ** 3)
        self.assertEqual(size % MIB, 0)
        labels = [card["label"] for card in drive_layout.card_sizes()]
        self.assertIn("128 GB card", labels)
        self.assertIn("1 TB card", labels)


class PlanTests(HandlerStoreFixture):
    def rows(self, *rows):
        return [
            {"name": name, "label": label, "filesystem": filesystem, "sizeBytes": size, "bootable": index == 0}
            for index, (name, label, filesystem, size) in enumerate(rows)
        ]

    def test_a_card_of_128_gigabytes_is_planned_to_the_cylinder(self) -> None:
        plan = drive_layout.plan("128GB", self.rows(
            ("DH0", "System", "pfs3", "2GB"),
            ("DH1", "Work", "pfs3", "60GB"),
            ("DH2", "Games", "pds3", None),
        ))

        self.assertTrue(plan["ok"], plan["errors"])
        self.assertEqual(plan["cylinderBytes"], MIB)
        self.assertEqual(plan["cylinders"], 128 * 1024)
        system, work, games = plan["partitions"]
        self.assertEqual(system["sizeBytes"], 2 * GIB)
        self.assertEqual(system["lowCylinder"], 1)
        self.assertEqual(work["lowCylinder"], system["highCylinder"] + 1)
        self.assertTrue(games["takesRest"])
        self.assertEqual(games["highCylinder"], plan["cylinders"] - 1)
        self.assertEqual(plan["unusedBytes"], 0)
        self.assertEqual(
            sorted(handler["dosType"] for handler in plan["handlers"]), ["PDS\x03", "PFS\x03"]
        )

    def test_the_professional_file_system_comes_with_the_application(self) -> None:
        rows = {row["family"]: row for row in filesystem_handlers.available()}
        self.assertEqual(rows["pfs3"]["source"], "bundled")
        self.assertEqual(rows["pfs3"]["version"], "19.2")
        self.assertEqual(rows["sfs"]["source"], "missing")
        self.assertTrue((filesystem_handlers.BUNDLED_DIR / "pfs3aio.LICENSE").is_file())

    def test_a_large_ffs_partition_is_refused_and_pfs3_is_named(self) -> None:
        plan = drive_layout.plan("32GB", self.rows(
            ("DH0", "System", "ffs-intl", "1GB"),
            ("DH1", "Work", "ffs-intl", None),
        ))
        self.assertFalse(plan["ok"])
        self.assertEqual(len(plan["errors"]), 1)
        self.assertIn("DH1", plan["errors"][0])
        self.assertIn("4 GiB", plan["errors"][0])
        self.assertIn("Professional File System", plan["errors"][0])
        with self.assertRaises(DiskError):
            drive_layout.require_plan("32GB", self.rows(("DH0", "All", "ffs-intl", None)))

    def test_an_ffs_partition_of_a_reasonable_size_is_allowed(self) -> None:
        plan = drive_layout.plan("4GB", self.rows(
            ("DH0", "System", "ffs-intl", "1GB"),
            ("DH1", "Work", "ffs-intl", None),
        ))
        self.assertTrue(plan["ok"], plan["errors"])
        self.assertTrue(any("large partition for FFS" in text for text in plan["warnings"]))
        self.assertEqual(plan["handlers"], [])

    def test_what_kickstart_31_cannot_reach_is_said(self) -> None:
        plan = drive_layout.plan("16GB", self.rows(
            ("DH0", "System", "pfs3", "8GB"),
            ("DH1", "Classic", "ffs-intl", "1GB"),
            ("DH2", "Work", "pds3", None),
        ))
        self.assertTrue(plan["ok"], plan["errors"])
        self.assertTrue(any("DH1 lies past the first 4 GB" in text for text in plan["warnings"]))
        self.assertTrue(any("PDS\\3" in text for text in plan["warnings"]))

    def test_a_filing_system_with_no_handler_cannot_be_used_until_one_is_supplied(self) -> None:
        rows = self.rows(("DH0", "System", "pfs3", "2GB"), ("DH1", "Work", "sfs", None))
        plan = drive_layout.plan("16GB", rows)
        self.assertFalse(plan["ok"])
        self.assertIn("Smart File System", plan["errors"][0])
        self.assertEqual([row["family"] for row in plan["missingHandlers"]], ["sfs"])

        filesystem_handlers.store("sfs", SFS, "SmartFilesystem")
        plan = drive_layout.plan("16GB", rows)
        self.assertTrue(plan["ok"], plan["errors"])
        self.assertIn("SFS\x00", [handler["dosType"] for handler in plan["handlers"]])

    def test_long_filename_ffs_is_allowed_without_a_handler_and_says_why_it_matters(self) -> None:
        plan = drive_layout.plan("2GB", self.rows(("DH0", "System", "ffs-lnfs", None)))
        self.assertTrue(plan["ok"], plan["errors"])
        self.assertTrue(any("3.1.4" in text for text in plan["warnings"]))

    def test_the_smart_file_system_stops_at_127_gigabytes(self) -> None:
        filesystem_handlers.store("sfs", SFS)
        plan = drive_layout.plan("256GB", self.rows(("DH0", "All", "sfs", None)))
        self.assertFalse(plan["ok"])
        self.assertIn("127 GiB", plan["errors"][0])

    def test_a_drive_nothing_boots_from_is_pointed_out(self) -> None:
        rows = self.rows(("DH0", "Work", "pfs3", None))
        rows[0]["bootable"] = False
        plan = drive_layout.plan("8GB", rows)
        self.assertTrue(plan["ok"])
        self.assertTrue(any("bootable" in text for text in plan["warnings"]))

    def test_names_sizes_and_priorities_are_checked(self) -> None:
        good = {"name": "DH0", "label": "System", "filesystem": "pfs3"}
        for change in (
            {"name": "Work Disk"},
            {"name": "DH0:"},
            {"name": "x" * 31},
            {"label": "Sys:tem"},
            {"label": "a/b"},
            {"label": "x" * 31},
            {"label": "Ω"},
            {"filesystem": "ntfs"},
            {"filesystem": ""},
            {"sizeBytes": "lots"},
            {"bootPriority": 200},
            {"bootPriority": "high"},
        ):
            with self.subTest(change=change):
                with self.assertRaises(DiskError):
                    drive_layout.plan("8GB", [{**good, **change}])
        with self.assertRaises(DiskError):
            drive_layout.plan("8GB", [good, {**good, "name": "dh0"}])
        with self.assertRaises(DiskError):
            drive_layout.plan("8GB", [])
        with self.assertRaises(DiskError):
            drive_layout.plan("1MB", [good])
        with self.assertRaises(DiskError):
            drive_layout.plan("3TB", [good])
        with self.assertRaises(DiskError):
            drive_layout.plan("8GB", [{**good, "sizeBytes": "6GB"}, {**good, "name": "DH1", "sizeBytes": "6GB"}])

    def test_no_starting_layout_is_chosen_for_the_user(self) -> None:
        """Each one is offered; which to use, and in which filing system, is asked."""
        options = drive_layout.options()
        self.assertGreaterEqual(len(options["presets"]), 3)
        self.assertEqual(set(options["largeFilesystems"]), {"pfs3", "pds3", "sfs"})
        with self.assertRaises(DiskError):
            drive_layout.preset_rows("system-work", "", 128 * GIB)
        with self.assertRaises(DiskError):
            drive_layout.preset_rows("", "pfs3", 128 * GIB)

    def test_every_starting_layout_makes_a_drive_that_can_be_created(self) -> None:
        for preset in drive_layout.PRESETS:
            for filesystem in ("pfs3", "pds3"):
                for size in (64 * MIB, 4 * GIB, 128 * GIB, 1024 * GIB):
                    with self.subTest(preset=preset["id"], filesystem=filesystem, size=size):
                        rows = drive_layout.preset_rows(preset["id"], filesystem, size)
                        plan = drive_layout.plan(size, rows)
                        self.assertTrue(plan["ok"], plan["errors"])
                        self.assertEqual(plan["unusedBytes"], 0)
                        self.assertTrue(plan["partitions"][0]["bootable"])


class HandlerStoreTests(HandlerStoreFixture):
    def test_a_handler_is_recognised_by_what_it_says_it_is(self) -> None:
        self.assertEqual(filesystem_handlers.recognise(PFS3).key, "pfs3")
        self.assertEqual(filesystem_handlers.recognise(SFS).key, "sfs")
        self.assertEqual(filesystem_handlers.recognise(FFS).key, "ffs")
        self.assertIsNone(filesystem_handlers.recognise(load_file("diskimage.device 52.1")))

    def test_a_supplied_handler_takes_the_place_of_the_shipped_one(self) -> None:
        newer = load_file("Professional-File-System-III 19.9 PFS3AIO (1.1.2026)")
        kept = filesystem_handlers.store("pfs3", newer, "/home/someone/Downloads/pfs3aio")

        self.assertEqual(kept["source"], "supplied")
        self.assertEqual(kept["version"], "19.9")
        self.assertEqual(kept["name"], "pfs3aio")
        self.assertEqual(filesystem_handlers.load("pfs3"), newer)
        rows = {row["family"]: row for row in filesystem_handlers.available()}
        self.assertTrue(rows["pfs3"]["replacesBundled"])

        filesystem_handlers.remove("pfs3")
        self.assertEqual(
            filesystem_handlers.load("pfs3"),
            (filesystem_handlers.BUNDLED_DIR / "pfs3aio").read_bytes(),
        )
        with self.assertRaises(DiskError):
            filesystem_handlers.remove("pfs3")

    def test_what_is_not_the_handler_it_is_offered_as_is_refused(self) -> None:
        with self.assertRaises(DiskError) as raised:
            filesystem_handlers.store("sfs", b"PK\x03\x04 an archive, not a program")
        self.assertIn("not an Amiga program", str(raised.exception))
        with self.assertRaises(DiskError) as raised:
            filesystem_handlers.store("sfs", PFS3)
        self.assertIn("Professional File System", str(raised.exception))
        with self.assertRaises(DiskError):
            filesystem_handlers.store("sfs", b"")
        with self.assertRaises(DiskError):
            filesystem_handlers.store("sfs", SFS + bytes(2 * MIB))
        with self.assertRaises(DiskError):
            filesystem_handlers.store("ext4", SFS)
        self.assertIsNone(filesystem_handlers.load("sfs"))

    def test_in_the_web_host_each_owner_keeps_handlers_of_their_own(self) -> None:
        """A handler is a program the Amiga runs, so nobody chooses one for anybody else."""
        from app.image_session import SESSION_OWNER

        flag = patch.object(filesystem_handlers, "PER_OWNER", True)
        flag.start()
        self.addCleanup(flag.stop)
        first = SESSION_OWNER.set("a" * 32)
        try:
            filesystem_handlers.store("sfs", SFS)
            self.assertEqual(filesystem_handlers.load("sfs"), SFS)
        finally:
            SESSION_OWNER.reset(first)
        second = SESSION_OWNER.set("b" * 32)
        try:
            self.assertIsNone(filesystem_handlers.load("sfs"))
            rows = {row["family"]: row for row in filesystem_handlers.available()}
            self.assertEqual(rows["sfs"]["source"], "missing")
            self.assertEqual(rows["pfs3"]["source"], "bundled")
            with self.assertRaises(DiskError):
                filesystem_handlers.remove("sfs")
            # What the host's operator provides is there for everyone.
            shared = filesystem_handlers.user_directory()
            (shared / "sfs.handler").write_bytes(SFS)
            self.assertEqual(filesystem_handlers.load("sfs"), SFS)
        finally:
            SESSION_OWNER.reset(second)

    def test_handlers_are_taken_from_a_drive_that_carries_them(self) -> None:
        from amiganut.filesystem.drive import create_drive, make_handler

        donor = self.folder / "donor.hdf"
        create_drive(
            donor,
            64 * MIB,
            [{"name": "DH0", "filesystem": "sfs"}],
            handlers=[make_handler("sfs", SFS)],
        )
        kept = filesystem_handlers.store_from_drive(donor)

        self.assertEqual([row["family"] for row in kept], ["sfs"])
        self.assertEqual(filesystem_handlers.load("sfs"), SFS)

        bare = self.folder / "bare.hdf"
        create_drive(bare, 64 * MIB, [{"name": "DH0", "filesystem": "ffs-intl"}])
        with self.assertRaises(DiskError):
            filesystem_handlers.store_from_drive(bare)
        with self.assertRaises(DiskError):
            filesystem_handlers.store_from_drive(self.folder / "missing.hdf")


if __name__ == "__main__":
    unittest.main()
