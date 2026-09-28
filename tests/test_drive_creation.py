"""Creating large drives in the workbench, and changing their partitions.

The drives here are the size of real cards. Each is a sparse file, so a test
that creates a 128 GB drive, installs onto it and saves it uses a few
megabytes, and the assertions about how much room a drive takes are what keep
it that way.
"""

from __future__ import annotations

import io
import os
import struct
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app import drive_layout
from app.attached_drives import AttachedDrive
from app.disk_service import DiskService
from app.drive_write import image_extents, write_image, write_plan
from app.errors import DiskError

try:
    from app.server import create_app
except ModuleNotFoundError:  # Flask and Werkzeug are container dependencies.
    create_app = None

MIB = 1024 * 1024
GIB = 1024 * MIB


def load_file(version: str) -> bytes:
    return (
        struct.pack(">6I", 0x3F3, 0, 1, 0, 0, 64)
        + struct.pack(">2I", 0x3E9, 64)
        + f"$VER: {version}".encode("latin-1").ljust(256, b"\0")
        + struct.pack(">I", 0x3F2)
    )


SFS = load_file("SmartFilesystem 1.279 (12.10.2008)")

CARD = [
    {"name": "DH0", "label": "System", "filesystem": "pfs3", "sizeBytes": "2GB", "bootable": True},
    {"name": "DH1", "label": "Classic", "filesystem": "ffs-intl", "sizeBytes": "1GB"},
    {"name": "DH2", "label": "Work", "filesystem": "pfs3", "sizeBytes": "60GB"},
    {"name": "DH3", "label": "Games", "filesystem": "pds3"},
]


def allocated(path: Path) -> int:
    return path.stat().st_blocks * 512


def head(path: Path, length: int = 4) -> bytes:
    """The start of a file, without reading a drive of gigabytes to get it."""
    with path.open("rb") as handle:
        return handle.read(length)


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
        environment = patch.dict(
            os.environ, {"AMIGA_FILE_FORGE_HANDLER_DIR": str(self.folder / "handlers")}
        )
        environment.start()
        self.addCleanup(environment.stop)
        probe = self.folder / "probe"
        with probe.open("wb") as handle:
            handle.truncate(64 * MIB)
        if allocated(probe) > MIB:
            self.skipTest("the temporary directory cannot hold sparse files")
        probe.unlink()
        self.service = DiskService(self.folder / "work")

    def fill(self, session, index: int, name: str = "Stuff") -> bytes:
        """Put a drawer and a file in a partition and return the file's bytes."""
        self.service.select_partition(session, index)
        self.service.make_directory(session, name)
        source = self.folder / "payload"
        data = os.urandom(90_000)
        source.write_bytes(data)
        self.service.put(session, f"{name}/payload", source)
        self.assertEqual(self.service.read_file(session, f"{name}/payload"), data)
        self.assertEqual(self.service.validate(session), "No structural errors found")
        return data

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
            contents="",
        )
        return replace(listed, **changes)


class CreateDriveTests(DriveFixture):
    def test_a_card_of_128_gigabytes_is_created_and_every_partition_is_usable(self) -> None:
        session = self.service.create_drive("Card", drive_layout.card_bytes(128), CARD)

        self.assertEqual(session.kind, "hdf")
        self.assertEqual(session.name, "Card.hdf")
        self.assertEqual(session.path.stat().st_size, drive_layout.card_bytes(128))
        self.assertLess(allocated(session.path), 64 * MIB)
        partitions = self.service.list_partitions(session)
        self.assertEqual(
            [(row["name"], row["format"]) for row in partitions],
            [("DH0", "PFS3"), ("DH1", "FFS-INTL"), ("DH2", "PFS3"), ("DH3", "PFS3")],
        )
        self.assertEqual(partitions[2]["sizeBytes"], 60 * GIB)
        for index in range(4):
            self.fill(session, index)
        layout = self.service.drive_layout(session)
        self.assertEqual(layout["freeRanges"], [])
        self.assertEqual(layout["missingHandlers"], [])
        self.assertEqual(len(layout["filesystems"]), 2)
        self.assertTrue(any("PDS\\3" in warning for warning in session.warnings))

    def test_the_same_request_through_the_create_route(self) -> None:
        session = self.service.create_blank(
            "ffs-hard", "Through", "128GB", options={"partitions": CARD}
        )
        self.assertEqual(session.path.stat().st_size, 128 * GIB)
        self.assertEqual(len(self.service.list_partitions(session)), 4)
        summary = self.service.summary(session)
        self.assertEqual(summary["size"], 128 * GIB)
        self.assertEqual(summary["targetHardware"], "amigaos")

    def test_a_drive_with_no_layout_is_one_ffs_partition_as_before(self) -> None:
        session = self.service.create_blank("ffs-hard", "Small", "512MB")
        partitions = self.service.list_partitions(session)
        self.assertEqual(len(partitions), 1)
        self.assertEqual(partitions[0]["format"], "FFS-INTL")
        self.assertLess(allocated(session.path), 4 * MIB)
        self.fill(session, 0)

    def test_a_large_ffs_drive_is_refused_and_the_way_forward_is_named(self) -> None:
        for format_name in ("ffs-hard", "hardfile", "ffs-physical"):
            with self.subTest(format=format_name):
                with self.assertRaises(DiskError) as raised:
                    self.service.create_blank(format_name, "TooBig", "128GB")
                self.assertIn("Professional File System", str(raised.exception))
        self.assertEqual(list((self.folder / "work").glob("*/*.hdf")), [])

    def test_a_hardfile_of_gigabytes_is_created(self) -> None:
        """It used to stop at about 50 MB, where the root block's bitmap list ends."""
        session = self.service.create_blank("hardfile", "Two", "2GB")
        self.assertEqual(session.path.stat().st_size, 2 * GIB)
        self.assertIsNotNone(session.descriptor_path)
        self.assertLess(allocated(session.path), 4 * MIB)
        self.service.make_directory(session, "Games")
        self.assertEqual(self.service.validate(session), "No structural errors found")

    def test_one_volume_with_no_partition_table_in_another_filing_system(self) -> None:
        session = self.service.create_blank(
            "ffs-physical", "Huge", "200GB", options={"filesystem": "pfs3"}
        )
        self.assertEqual(session.kind, "ffs")
        self.assertEqual(session.ffs_capabilities["map"], "pfs3")
        self.assertEqual(session.path.stat().st_size, 200 * GIB)
        self.assertLess(allocated(session.path), 96 * MIB)
        self.assertTrue(any("no partition table" in warning for warning in session.warnings))
        self.service.make_directory(session, "Work")
        self.assertEqual(self.service.validate(session), "No structural errors found")
        with self.assertRaises(DiskError):
            self.service.create_blank("hardfile", "No", "8GB", options={"filesystem": "pfs3"})
        with self.assertRaises(DiskError):
            self.service.create_blank("ffs-physical", "No", "200GB", options={"filesystem": "sfs"})

    def test_a_layout_that_cannot_be_made_creates_nothing(self) -> None:
        with self.assertRaises(DiskError):
            self.service.create_drive(
                "Bad", "32GB", [{"name": "DH0", "label": "All", "filesystem": "ffs-intl"}]
            )
        with self.assertRaises(DiskError):
            self.service.create_drive(
                "Bad", "32GB", [{"name": "DH0", "label": "All", "filesystem": "sfs"}]
            )
        self.assertEqual(self.service.sessions, {})
        self.assertEqual(list((self.folder / "work").iterdir()), [])


class PartitionTableTests(DriveFixture):
    def card(self):
        return self.service.create_drive("Card", "32GB", [
            {"name": "DH0", "label": "System", "filesystem": "ffs-intl", "sizeBytes": "1GB", "bootable": True},
            {"name": "DH1", "label": "Work", "filesystem": "pfs3", "sizeBytes": "10GB"},
            {"name": "DH2", "label": "Games", "filesystem": "pfs3"},
        ])

    def test_a_partition_is_removed_and_another_put_in_its_place(self) -> None:
        session = self.card()
        kept = self.fill(session, 1)
        self.service.remove_partition(session, 2)

        self.assertIsNone(session.partition, "the pane goes back to the partition table")
        layout = self.service.drive_layout(session)
        self.assertEqual(len(layout["freeRanges"]), 1)
        self.assertGreater(layout["freeRanges"][0]["sizeBytes"], 20 * GIB)

        added = self.service.add_partition(
            session, {"name": "DH2", "label": "Music", "filesystem": "pds3", "sizeBytes": "5GB"}
        )
        self.assertEqual(added["sizeBytes"], 5 * GIB)
        self.assertEqual(added["label"], "Music")
        self.fill(session, 2)
        layout = self.service.drive_layout(session)
        self.assertEqual(
            sorted(row["dosType"] for row in layout["filesystems"]), ["PDS\x03", "PFS\x03"]
        )
        self.assertEqual(layout["missingHandlers"], [])
        # What was on the partition beside it is still there.
        self.service.select_partition(session, 1)
        self.assertEqual(self.service.read_file(session, "Stuff/payload"), kept)

    def test_an_added_partition_obeys_the_same_limits_as_a_planned_one(self) -> None:
        session = self.card()
        self.service.remove_partition(session, 2)
        for row, words in (
            ({"name": "DH2", "label": "Big", "filesystem": "ffs-intl"}, "Professional File System"),
            ({"name": "DH2", "label": "Smart", "filesystem": "sfs", "sizeBytes": "1GB"}, "Smart File System handler"),
            ({"name": "DH1", "label": "Twice", "filesystem": "pfs3", "sizeBytes": "1GB"}, "already has a partition"),
            ({"name": "DH2", "label": "Huge", "filesystem": "pfs3", "sizeBytes": "30GB"}, "less than the partition asks for"),
        ):
            with self.subTest(row=row):
                with self.assertRaises(DiskError) as raised:
                    self.service.add_partition(session, row)
                self.assertIn(words, str(raised.exception))
        self.assertEqual(len(self.service.list_partitions(session)), 2)

    def test_a_partition_is_emptied_in_another_filing_system(self) -> None:
        session = self.card()
        self.fill(session, 0)
        formatted = self.service.format_partition(session, 0, "Fresh", "pfs3")

        self.assertEqual(formatted["format"], "PFS3")
        self.assertEqual(formatted["label"], "Fresh")
        self.service.select_partition(session, 0)
        self.assertEqual(self.service.list_directory(session, "")["entries"], [])
        self.fill(session, 0)
        with self.assertRaises(DiskError):
            self.service.format_partition(session, 1, "TooBig", "ffs-intl")
        with self.assertRaises(DiskError):
            self.service.format_partition(session, 9, "Nothing")

    def test_a_partition_is_renamed_and_made_bootable(self) -> None:
        session = self.card()
        data = self.fill(session, 1)
        changed = self.service.change_partition(
            session, 1, {"name": "Work", "bootable": True, "bootPriority": 5}
        )
        self.assertEqual((changed["name"], changed["bootable"], changed["bootPriority"]), ("Work", True, 5))
        self.service.select_partition(session, 1)
        self.assertEqual(self.service.read_file(session, "Stuff/payload"), data)
        for changes in ({"name": "DH0"}, {"name": "No Spaces"}, {"bootPriority": 500}, {}):
            with self.subTest(changes=changes):
                with self.assertRaises(DiskError):
                    self.service.change_partition(session, 1, changes)

    def test_a_drive_image_is_made_larger_to_take_another_partition(self) -> None:
        session = self.card()
        with self.assertRaises(DiskError):
            self.service.resize_drive_image(session, "16GB")
        self.service.resize_drive_image(session, "128GB")

        self.assertEqual(session.path.stat().st_size, 128 * GIB)
        layout = self.service.drive_layout(session)
        self.assertEqual(layout["describedBytes"], 128 * GIB)
        self.assertEqual(layout["freeRanges"][0]["sizeBytes"], 96 * GIB)
        self.service.add_partition(session, {"name": "DH3", "label": "More", "filesystem": "pfs3"})
        self.fill(session, 3)
        self.assertLess(allocated(session.path), 96 * MIB)

    def test_a_drive_lacking_a_handler_is_given_it(self) -> None:
        from amiganut.filesystem.blocks import BlockReader
        from amiganut.filesystem.rdb import remove_handler

        session = self.card()
        with BlockReader(session.path, writable=True) as reader:
            remove_handler(reader, b"PFS\x03")
        layout = self.service.drive_layout(session)
        self.assertEqual(
            [(row["dosType"], row["available"]) for row in layout["missingHandlers"]],
            [("PFS\x03", True)],
        )
        self.assertEqual(self.service.embed_handlers(session), ["Professional File System 3"])
        self.assertEqual(self.service.drive_layout(session)["missingHandlers"], [])
        with self.assertRaises(DiskError):
            self.service.embed_handlers(session)

    def test_only_a_partitioned_drive_has_partitions_to_change(self) -> None:
        floppy = self.service.create_blank("ffs-intl", "Floppy")
        for call in (
            lambda: self.service.drive_layout(floppy),
            lambda: self.service.remove_partition(floppy, 0),
            lambda: self.service.add_partition(floppy, {"name": "DH0", "label": "x", "filesystem": "pfs3"}),
            lambda: self.service.extend_drive(floppy),
        ):
            with self.assertRaises(DiskError):
                call()


class RealDriveTests(DriveFixture):
    """A file stands in for the card, full of what a used card would hold."""

    def card(self, size: int = 8 * GIB) -> Path:
        path = self.folder / "card.img"
        with path.open("wb") as handle:
            handle.truncate(size)
            handle.write(b"\xeb\x58\x90MSDOS5.0")
            handle.seek(510)
            handle.write(b"\x55\xaa")
        return path

    def test_a_card_is_partitioned_and_formatted_where_it_is(self) -> None:
        card = self.card()
        result = self.service.initialise_attached_drive(str(card), [
            {"name": "DH0", "label": "System", "filesystem": "pfs3", "sizeBytes": "2GB", "bootable": True},
            {"name": "DH1", "label": "Work", "filesystem": "pfs3"},
        ])

        self.assertEqual(result["kind"], "drive")
        self.assertEqual([row["label"] for row in result["partitions"]], ["System", "Work"])
        self.assertEqual(head(card), b"RDSK")
        self.assertLess(allocated(card), 16 * MIB, "only what describes the drive is written")

        session = self.service.open_attached_drive(str(card), "CARD")
        self.assertEqual(session.kind, "hdf")
        with self.assertRaises(DiskError):
            self.service.add_partition(session, {"name": "DH2", "label": "x", "filesystem": "pfs3"})
        self.service.allow_drive_writes(session, True)
        self.fill(session, 0)
        self.fill(session, 1)

    def test_a_card_is_made_one_volume(self) -> None:
        card = self.card(16 * GIB)
        result = self.service.initialise_attached_drive(
            str(card), volume={"filesystem": "pfs3", "label": "Whole"}
        )
        self.assertEqual(result["kind"], "volume")
        session = self.service.open_attached_drive(str(card), "CARD")
        self.assertEqual(session.kind, "ffs")
        self.assertEqual(session.ffs_capabilities["map"], "pfs3")
        with self.assertRaises(DiskError) as raised:
            self.service.initialise_attached_drive(
                str(card), volume={"filesystem": "ffs-intl", "label": "Whole"}
            )
        self.assertIn("partitions", str(raised.exception))

    def test_a_layout_that_does_not_fit_the_card_leaves_it_alone(self) -> None:
        card = self.card()
        before = head(card, 1024)
        with self.assertRaises(DiskError):
            self.service.initialise_attached_drive(str(card), [
                {"name": "DH0", "label": "System", "filesystem": "pfs3", "sizeBytes": "20GB"},
            ])
        self.assertEqual(head(card, 1024), before)

    def test_an_image_written_to_a_larger_card_can_claim_the_rest(self) -> None:
        image = self.service.create_drive("Small", "4GB", [
            {"name": "DH0", "label": "System", "filesystem": "pfs3", "bootable": True},
        ])
        data = self.fill(image, 0)
        card = self.card(16 * GIB)
        with card.open("r+b") as handle:
            handle.seek(16 * GIB - 512)
            handle.write(b"EFI PART")

        plan = write_plan(image.path)
        self.assertLess(plan["writtenBytes"], 64 * MIB)
        result = write_image(image.path, self.drive(card), set(), ignore)

        self.assertEqual(result["unusedBytes"], 12 * GIB)
        self.assertLess(allocated(card), 64 * MIB)
        with card.open("rb") as handle:
            handle.seek(16 * GIB - 512)
            self.assertEqual(handle.read(8), bytes(8), "the old table's second copy is gone")

        session = self.service.open_attached_drive(str(card), "CARD")
        self.service.allow_drive_writes(session, True)
        self.service.select_partition(session, 0)
        self.assertEqual(self.service.read_file(session, "Stuff/payload"), data)
        layout = self.service.drive_layout(session)
        self.assertEqual(layout["beyondTableBytes"], 12 * GIB)
        self.service.extend_drive(session)
        self.service.add_partition(session, {"name": "DH1", "label": "Work", "filesystem": "pfs3"})
        self.fill(session, 1)
        self.service.select_partition(session, 0)
        self.assertEqual(self.service.read_file(session, "Stuff/payload"), data)

    def test_writing_every_byte_leaves_nothing_of_what_the_card_held(self) -> None:
        image = self.service.create_drive("Small", "96MB", [
            {"name": "DH0", "label": "System", "filesystem": "ffs-intl", "bootable": True},
        ])
        card = self.folder / "card.img"
        card.write_bytes(b"\xa5" * (100 * MIB))
        held = write_image(image.path, self.drive(card), set(), ignore)
        # Whole assertions rather than assertIn: a failure would otherwise
        # print the card.
        self.assertLess(held["writtenBytes"], 16 * MIB)
        self.assertTrue(b"\xa5" * 4096 in card.read_bytes()[: 96 * MIB])
        self.assertTrue(head(card) == b"RDSK")

        every = write_image(image.path, self.drive(card), set(), ignore, every_byte=True)
        self.assertEqual(every["writtenBytes"], image.path.stat().st_size)
        self.assertTrue(card.read_bytes()[: every["imageBytes"]] == image.path.read_bytes())

    def test_a_card_that_cannot_take_the_image_is_not_written_to(self) -> None:
        image = self.service.create_drive("Card", "4GB", [
            {"name": "DH0", "label": "System", "filesystem": "pfs3"},
        ])
        small = self.card(2 * GIB)
        before = head(small, 1024)
        for drive, words in (
            (self.drive(small), "too small"),
            (self.drive(self.card(8 * GIB), mounted=["/media/card"]), "mounted"),
            (self.drive(self.card(8 * GIB), read_only_switch=True), "write-protect"),
        ):
            with self.subTest(words=words):
                with self.assertRaises(DiskError) as raised:
                    write_image(image.path, drive, set(), ignore)
                self.assertIn(words, str(raised.exception))
        self.assertEqual(head(small, 1024), before)

    def test_a_card_that_does_not_keep_what_is_written_is_found_out(self) -> None:
        image = self.service.create_drive("Tiny", "24MB", [
            {"name": "DH0", "label": "System", "filesystem": "ffs-intl"},
        ])
        card = self.folder / "card.img"
        card.write_bytes(bytes(32 * MIB))
        real = os.pwrite

        def faulty(descriptor, data, offset):
            written = real(descriptor, data, offset)
            if offset <= 2 * MIB < offset + written:
                real(descriptor, b"\xff", 2 * MIB)
            return written

        with patch("app.drive_write.os.pwrite", faulty):
            with self.assertRaises(DiskError) as raised:
                write_image(image.path, self.drive(card), set(), ignore, every_byte=True)
        self.assertIn("does not hold what was written", str(raised.exception))

    def test_only_the_parts_of_an_image_that_hold_data_are_found(self) -> None:
        path = self.folder / "sparse.img"
        with path.open("wb") as handle:
            handle.truncate(GIB)
            handle.seek(300 * MIB)
            handle.write(b"x" * 8192)
        extents = image_extents(path, GIB)
        self.assertEqual(len(extents), 1)
        self.assertLessEqual(extents[0][0], 300 * MIB)
        self.assertGreaterEqual(extents[0][0] + extents[0][1], 300 * MIB + 8192)
        self.assertLess(extents[0][1], MIB)

    def test_a_drive_image_is_saved_without_its_empty_space(self) -> None:
        session = self.service.create_drive("Card", "128GB", CARD)
        data = self.fill(session, 2)
        target = self.folder / "saved" / "Card.hdf"
        target.parent.mkdir()
        result = self.service.save_drive_image(session, target)

        self.assertEqual(result["bytes"], 128 * GIB)
        self.assertEqual(target.stat().st_size, 128 * GIB)
        self.assertLess(allocated(target), 96 * MIB)
        self.assertFalse(session.dirty)
        self.assertEqual(list(target.parent.glob("*.part")), [])
        reopened = self.service.create_from_path(target)
        self.service.select_partition(reopened, 2)
        self.assertEqual(self.service.read_file(reopened, "Stuff/payload"), data)

    def test_one_partition_of_a_large_drive_is_saved_as_a_hardfile(self) -> None:
        from app.hardfile_geometry import parse_geometry

        session = self.service.create_drive("Card", "128GB", CARD)
        data = self.fill(session, 1)
        session.dirty = True
        scopes = [entry["scope"] for entry in self.service.save_scopes(session)]
        self.assertEqual(scopes, ["image", "partition:0", "partition:1", "partition:2", "partition:3"])

        target = self.folder / "Card-DH1.hdf"
        result = self.service.save_drive_image(session, target, scope="partition:1")

        self.assertEqual(target.stat().st_size, GIB)
        self.assertLess(allocated(target), 8 * MIB)
        self.assertTrue(session.dirty, "saving one partition does not save the drive")
        geometry = parse_geometry(Path(result["geometry"]).read_text())
        self.assertEqual(
            geometry["surfaces"] * geometry["blocks_per_track"] * geometry["cylinders"] * 512, GIB
        )
        reopened = self.service.create_from_path(target, Path(result["geometry"]))
        self.assertEqual(self.service.read_file(reopened, "Stuff/payload"), data)
        with self.assertRaises(DiskError):
            self.service.save_drive_image(session, target, scope="partition:9")

    def test_a_large_volume_is_saved_as_a_drive_that_describes_itself(self) -> None:
        from amiganut.filesystem.blocks import BlockReader
        from amiganut.filesystem.rdb import read_rigid_disk

        volume = self.service.create_blank(
            "ffs-physical", "Huge", "100GB", options={"filesystem": "pfs3"}
        )
        self.service.make_directory(volume, "Work")
        self.assertEqual(
            [entry["scope"] for entry in self.service.save_scopes(volume)], ["image", "drive"]
        )
        self.assertEqual(self.service.export_formats(volume)[1:], [], "too large to download")

        target = self.folder / "Huge-drive.hdf"
        self.service.save_drive_image(volume, target, scope="drive")

        self.assertLess(allocated(target), 96 * MIB)
        with BlockReader(target) as reader:
            disk = read_rigid_disk(reader)
        self.assertEqual(disk.partitions[0].size_bytes, 100 * GIB, "exactly the volume's size")
        self.assertEqual(disk.partitions[0].dos_type, b"PFS\x03")
        self.assertEqual([handler.dos_type for handler in disk.handlers], [b"PFS\x03"])
        reopened = self.service.create_from_path(target)
        self.service.select_partition(reopened, 0)
        self.assertEqual(
            [row["name"] for row in self.service.list_directory(reopened, "")["entries"]], ["Work"]
        )
        self.assertEqual(self.service.validate(reopened), "No structural errors found")

    def test_a_wrapped_ffs_volume_keeps_its_root_block_where_the_amiga_looks(self) -> None:
        """A partition rounded up to a cylinder moves the middle of the volume."""
        from amiganut.filesystem.blocks import BlockReader
        from amiganut.filesystem.rdb import read_rigid_disk

        bare = self.service.create_blank("ffs-physical", "Odd", "21MB")
        self.service.make_directory(bare, "Games")
        output, _name = self.service.export_image(bare, "rdb")

        with BlockReader(output) as reader:
            partition = read_rigid_disk(reader).partitions[0]
            self.assertEqual(partition.size_bytes, bare.path.stat().st_size)
            root = reader.read_block(partition.start_block + partition.total_blocks // 2)
        self.assertEqual(struct.unpack_from(">I", root, 0)[0], 2)
        self.assertEqual(struct.unpack_from(">i", root, 508)[0], 1)


@unittest.skipIf(create_app is None, "Flask is available in the application container")
class DriveRouteTests(DriveFixture):
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
        self.card = self.folder / "card.img"
        with self.card.open("wb") as handle:
            handle.truncate(16 * GIB)
        self.drives = [self.drive(self.card)]
        lister = patch("app.routes.desktop.list_attached_drives", side_effect=lambda: self.drives)
        lister.start()
        self.addCleanup(lister.stop)
        finder = patch(
            "app.routes.desktop.find_attached_drive",
            side_effect=lambda wanted: next(drive for drive in self.drives if drive.id == wanted),
        )
        finder.start()
        self.addCleanup(finder.stop)

    def request(self, method: str, url: str, body: dict | None = None, **options):
        return self.client.open(
            url, method=method, json=body, headers={"X-Amiga-Desktop-Token": self.TOKEN}, **options
        )

    def create(self, size: str = "32GB") -> str:
        preset = self.request(
            "POST", "/api/drive-layout/preset",
            {"preset": "system-work", "filesystem": "pfs3", "size": size},
        )
        self.assertEqual(preset.status_code, 200, preset.get_json())
        created = self.request("POST", "/api/images/create", {
            "format": "ffs-hard",
            "title": "Card",
            "capacity": size,
            "drive": {"partitions": preset.get_json()["partitions"]},
        })
        self.assertEqual(created.status_code, 200, created.get_json())
        return created.get_json()["image"]["id"]

    def test_the_editor_is_told_what_it_can_offer(self) -> None:
        options = self.request("GET", "/api/drive-layout/options").get_json()
        self.assertEqual(options["filesystems"][0]["id"], "pfs3")
        self.assertEqual(options["limits"]["largestFfsPartition"], 4 * GIB)
        self.assertTrue(options["presets"])
        plan = self.request("POST", "/api/drive-layout/plan", {
            "size": "128GB",
            "partitions": [{"name": "DH0", "label": "All", "filesystem": "ffs-intl"}],
        })
        self.assertEqual(plan.status_code, 200)
        self.assertFalse(plan.get_json()["plan"]["ok"])
        refused = self.request("POST", "/api/drive-layout/plan", {"size": "lots", "partitions": []})
        self.assertEqual(refused.status_code, 400)

    def test_a_partition_change_is_one_undo_point(self) -> None:
        image = self.create()
        removed = self.request("DELETE", f"/api/images/{image}/partitions/1")
        self.assertEqual(removed.status_code, 200, removed.get_json())
        listed = self.request("GET", f"/api/images/{image}/partitions").get_json()["partitions"]
        self.assertEqual([row["name"] for row in listed], ["DH0"])

        undone = self.request("POST", f"/api/images/{image}/undo")
        self.assertEqual(undone.status_code, 200, undone.get_json())
        listed = self.request("GET", f"/api/images/{image}/partitions").get_json()["partitions"]
        self.assertEqual([row["name"] for row in listed], ["DH0", "DH1"])

    def test_a_refused_change_leaves_no_undo_point_and_no_change(self) -> None:
        image = self.create()
        refused = self.request("POST", f"/api/images/{image}/partitions/1/format", {
            "label": "Big", "filesystem": "ffs-intl",
        })
        self.assertEqual(refused.status_code, 400)
        self.assertIn("Professional File System", refused.get_json()["error"])
        layout = self.request("GET", f"/api/images/{image}/drive-layout").get_json()
        self.assertEqual(layout["layout"]["partitions"][1]["format"], "PFS3")
        self.assertFalse(layout["image"]["checkpoints"]["canUndo"])

    def test_a_handler_is_supplied_used_and_forgotten(self) -> None:
        kept = self.client.post(
            "/api/filesystem-handlers",
            data={"family": "sfs", "handler": (io.BytesIO(SFS), "SmartFilesystem")},
            content_type="multipart/form-data",
            headers={"X-Amiga-Desktop-Token": self.TOKEN},
        )
        self.assertEqual(kept.status_code, 200, kept.get_json())
        self.assertEqual(kept.get_json()["kept"][0]["version"], "1.279")

        image = self.create()
        changed = self.request("POST", f"/api/images/{image}/partitions/1/format", {
            "label": "Smart", "filesystem": "sfs",
        })
        self.assertEqual(changed.status_code, 200, changed.get_json())
        layout = self.request("GET", f"/api/images/{image}/drive-layout").get_json()["layout"]
        self.assertIn("SFS\x00", [row["dosType"] for row in layout["filesystems"]])

        forgotten = self.request("DELETE", "/api/filesystem-handlers/sfs")
        self.assertEqual(forgotten.status_code, 200)
        taken = self.request("POST", "/api/filesystem-handlers", {"imageId": image})
        self.assertEqual(taken.status_code, 200, taken.get_json())
        self.assertIn("sfs", [row["family"] for row in taken.get_json()["kept"]])

        wrong = self.client.post(
            "/api/filesystem-handlers",
            data={"family": "sfs", "handler": (io.BytesIO(b"not a program"), "readme.txt")},
            content_type="multipart/form-data",
            headers={"X-Amiga-Desktop-Token": self.TOKEN},
        )
        self.assertEqual(wrong.status_code, 400)

    def test_a_card_has_to_be_named_twice_before_it_is_initialised(self) -> None:
        body = {
            "id": "usb-card",
            "partitions": [
                {"name": "DH0", "label": "System", "filesystem": "pfs3", "sizeBytes": "2GB", "bootable": True},
                {"name": "DH1", "label": "Work", "filesystem": "pfs3"},
            ],
            "allowWrites": True,
        }
        refused = self.request("POST", "/api/desktop/attached-drives/initialise", body)
        self.assertEqual(refused.status_code, 400)
        self.assertEqual(head(self.card), bytes(4))

        made = self.request(
            "POST", "/api/desktop/attached-drives/initialise", {**body, "confirm": "usb-card"}
        )
        self.assertEqual(made.status_code, 200, made.get_json())
        image = made.get_json()["image"]
        self.assertEqual(image["kind"], "hdf")
        self.assertTrue(image["attachedDrive"]["writesAllowed"])
        self.assertEqual(head(self.card), b"RDSK")
        folder = self.request("POST", f"/api/images/{image['id']}/mkdir", {"path": "Games", "partition": 1})
        self.assertEqual(folder.status_code, 200, folder.get_json())

        again = self.request(
            "POST", "/api/desktop/attached-drives/initialise", {**body, "confirm": "usb-card"}
        )
        self.assertEqual(again.status_code, 400)
        self.assertIn("open in a pane", again.get_json()["error"])

    def test_a_mounted_or_protected_card_is_not_initialised(self) -> None:
        body = {
            "id": "usb-card", "confirm": "usb-card",
            "volume": {"filesystem": "pfs3", "label": "Whole"},
        }
        for change, words in (
            ({"mounted": ["/media/card"]}, "mounted"),
            ({"read_only_switch": True}, "write-protect"),
        ):
            with self.subTest(words=words):
                self.drives = [self.drive(self.card, **change)]
                refused = self.request("POST", "/api/desktop/attached-drives/initialise", body)
                self.assertEqual(refused.status_code, 400)
                self.assertIn(words, refused.get_json()["error"])
        self.assertEqual(head(self.card), bytes(4))

    def test_an_image_is_written_to_a_card_and_saved_to_a_file(self) -> None:
        image = self.create("8GB")
        options = self.request("GET", f"/api/desktop/images/{image}/drive-write").get_json()
        self.assertEqual(options["imageBytes"], 8 * GIB)
        self.assertLess(options["heldBytes"], 64 * MIB)
        self.assertEqual(options["targets"][0]["problem"], "")
        self.assertEqual(options["targets"][0]["unusedBytes"], 8 * GIB)

        refused = self.request("POST", f"/api/desktop/images/{image}/drive-write", {"target": "usb-card"})
        self.assertEqual(refused.status_code, 400)
        written = self.request(
            "POST", f"/api/desktop/images/{image}/drive-write",
            {"target": "usb-card", "confirm": "usb-card"},
        )
        self.assertEqual(written.status_code, 200, written.get_json())
        self.assertEqual(head(self.card), b"RDSK")

        suggestion = self.request("GET", f"/api/desktop/images/{image}/save-image").get_json()
        self.assertEqual(suggestion["name"], "Card.hdf")
        self.assertEqual(
            [entry["scope"] for entry in suggestion["scopes"]],
            ["image", "partition:0", "partition:1"],
        )
        target = self.folder / "Saved.hdf"
        saved = self.request(
            "POST", f"/api/desktop/images/{image}/save-image", {"destination": str(target)}
        )
        self.assertEqual(saved.status_code, 200, saved.get_json())
        self.assertEqual(target.stat().st_size, 8 * GIB)
        self.assertLess(allocated(target), 64 * MIB)
        self.assertFalse(saved.get_json()["image"]["dirty"])
        nowhere = self.request(
            "POST", f"/api/desktop/images/{image}/save-image", {"destination": "relative.hdf"}
        )
        self.assertEqual(nowhere.status_code, 400)


if __name__ == "__main__":
    unittest.main()
