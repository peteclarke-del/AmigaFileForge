"""Reading and writing Smart File System volumes.

Every volume here is formatted by the workbench itself, because SFS images
cannot be shipped in the repository. The same code was checked against real
SFS on an emulated A1200: SFScheck accepted volumes this code built and
changed, and this code read back every change real SFS then made.
"""

from __future__ import annotations

import os
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from amiganut.disc.mount import mount_image
from amiganut.errors import DataError
from amiganut.file import AmigaMeta
from amiganut.filesystem import SFSMount, identify
from amiganut.filesystem.blocks import BlockReader
from amiganut.filesystem.rdb import partition_reader, read_rigid_disk, write_rigid_disk
from amiganut.filesystem.sfs import SFSVolume
from amiganut.filesystem.sfs_blocks import (
    compress_operation,
    datetime_to_sfs,
    sfs_checksum,
    sfs_date,
    sfs_hash,
    uncompress_operation,
    validate_sfs_name,
)
from amiganut.filesystem.sfs_write import format_sfs_volume


class PowerCut(Exception):
    """Raised by a patched write to stand for the power going off."""


def skip_fsync(test: unittest.TestCase) -> None:
    """Commits flush to the medium four times; the tests have no medium to protect."""
    stub = patch("amiganut.filesystem.blocks.os.fsync")
    stub.start()
    test.addCleanup(stub.stop)


class SFSTestCase(unittest.TestCase):
    def setUp(self) -> None:
        skip_fsync(self)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.image = Path(self.folder.name) / "sfs.img"

    def make_volume(self, megabytes: int = 16, block_size: int = 512, label: str = "Work") -> None:
        with self.image.open("wb") as handle:
            handle.truncate(megabytes * 1024 * 1024)
        with BlockReader(self.image, writable=True) as reader:
            format_sfs_volume(reader, label=label, block_size=block_size)

    def open(self, *, writable: bool = True) -> SFSVolume:
        volume = SFSVolume(BlockReader(self.image, writable=writable))
        self.addCleanup(volume.close)
        return volume

    def reopen(self, volume: SFSVolume, *, writable: bool = True) -> SFSVolume:
        volume.close()
        return self.open(writable=writable)

    def assertValid(self, volume: SFSVolume) -> None:
        # A volume carrying an unfinished change says so, which is a notice
        # rather than damage: the change is replayed when it is read.
        problems = volume.validate()
        if volume.recovered_transaction:
            self.assertTrue(problems and problems[0].startswith("SFS did not finish"))
            problems = problems[1:]
        self.assertEqual(problems, [])


class FormatTests(SFSTestCase):
    def test_a_new_volume_is_empty_valid_and_carries_its_name(self) -> None:
        self.make_volume(label="Games")
        volume = self.open(writable=False)
        self.assertEqual(volume.title, "Games")
        self.assertEqual(list(volume.iter_entries("")), [])
        self.assertValid(volume)
        self.assertEqual(volume.free_block_count(), volume.root_info()["freeBlocks"])

    def test_the_recycled_directory_exists_but_is_hidden(self) -> None:
        self.make_volume()
        volume = self.open(writable=False)
        self.assertTrue(volume.exists(".recycled"))
        self.assertNotIn(".recycled", [entry.name for entry in volume.iter_entries("")])

    def test_a_larger_block_size_is_recorded_in_the_root(self) -> None:
        self.make_volume(block_size=1024)
        volume = self.open(writable=False)
        self.assertEqual(volume.block_size, 1024)
        self.assertValid(volume)

    def test_a_bare_sfs_image_is_identified_by_content(self) -> None:
        self.make_volume(label="Bare")
        found = identify(self.image)
        self.assertEqual(found[0].filesystem, "sfs")
        self.assertIn("Bare", found[0].detail)


class FormatDetailTests(unittest.TestCase):
    def test_the_checksum_makes_a_sealed_block_sum_to_zero(self) -> None:
        block = bytearray(512)
        block[:4] = b"OBJC"
        block[4:8] = ((-sfs_checksum(bytes(block))) & 0xFFFFFFFF).to_bytes(4, "big")
        self.assertEqual(sfs_checksum(bytes(block)), 0)

    def test_the_hash_is_seeded_with_the_length_and_folds_case(self) -> None:
        # The value SFS stores for its own recycled directory on real volumes.
        self.assertEqual(sfs_hash(".recycled"), sfs_hash(".RECYCLED"))
        self.assertNotEqual(sfs_hash("a"), sfs_hash("aa"))
        self.assertEqual(sfs_hash("é"), sfs_hash("É"))
        self.assertNotEqual(sfs_hash("é", case_sensitive=True), sfs_hash("É", case_sensitive=True))

    def test_dates_are_seconds_since_1978(self) -> None:
        moment = sfs_date(1446741634)
        self.assertEqual(datetime_to_sfs(moment), 1446741634)
        self.assertEqual(sfs_date(0).year, 1978)

    def test_a_logged_block_replays_to_itself_over_anything(self) -> None:
        block = bytearray(os.urandom(512))
        block[40:200] = bytes(160)
        payload = compress_operation(bytes(block))
        self.assertEqual(uncompress_operation(bytes(512), payload), bytes(block))
        self.assertEqual(uncompress_operation(os.urandom(512), payload), bytes(block))

    def test_names_that_sfs_refuses_are_refused(self) -> None:
        for name in ("", "a:b", "a/b", "x" * 101, "tab\there", "snowman ☃"):
            with self.subTest(name=name), self.assertRaises(DataError):
                validate_sfs_name(name)
        self.assertEqual(validate_sfs_name("Café"), "Café")


class ReadWriteTests(SFSTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.make_volume(megabytes=24)

    def test_files_directories_and_metadata_survive_a_reopen(self) -> None:
        volume = self.open()
        volume.mkdir("Docs")
        volume.mkdir("Docs/Deep")
        big = os.urandom(3 * 1024 * 1024 + 17)
        volume.write_bytes("Docs/Deep/big.bin", big)
        volume.write_bytes("ReadMe", b"hello", AmigaMeta(protection=0x40, comment="a note"))
        volume.write_bytes("Empty", b"")
        volume = self.reopen(volume, writable=False)
        self.assertEqual(volume.read_bytes("Docs/Deep/big.bin"), big)
        self.assertEqual(volume.read_bytes("readme"), b"hello")
        self.assertEqual(volume.read_bytes("Empty"), b"")
        meta = volume.amiga_meta("ReadMe")
        self.assertEqual(meta.comment, "a note")
        self.assertEqual(meta.protection, 0x40)
        self.assertEqual(sorted(entry.name for entry in volume.iter_entries("")), ["Docs", "Empty", "ReadMe"])
        self.assertValid(volume)

    def test_protection_is_stored_inverted_as_sfs_keeps_it(self) -> None:
        volume = self.open()
        volume.write_bytes("locked", b"x", AmigaMeta(protection=0x05))
        found, _parts = volume.resolve("locked")
        self.assertEqual(found.protection, 0x05 ^ 0x0F)

    def test_replacing_a_file_returns_its_old_blocks(self) -> None:
        volume = self.open()
        free = volume.root_info()["freeBlocks"]
        volume.write_bytes("file", os.urandom(200_000))
        volume.write_bytes("file", os.urandom(1000))
        volume.remove("file")
        self.assertEqual(volume.root_info()["freeBlocks"], free)
        self.assertValid(volume)

    def test_rename_moves_between_directories_and_keeps_the_contents(self) -> None:
        volume = self.open()
        volume.mkdir("A")
        volume.mkdir("B")
        volume.write_bytes("A/file", b"payload", AmigaMeta(comment="kept"))
        volume.rename("A/file", "B/a much longer name than before")
        self.assertFalse(volume.exists("A/file"))
        self.assertEqual(volume.read_bytes("B/a much longer name than before"), b"payload")
        self.assertEqual(volume.amiga_meta("B/a much longer name than before").comment, "kept")
        self.assertValid(volume)

    def test_a_directory_cannot_be_moved_inside_itself(self) -> None:
        volume = self.open()
        volume.mkdir("A")
        volume.mkdir("A/B")
        with self.assertRaises(DataError):
            volume.rename("A", "A/B/A")
        self.assertValid(volume)

    def test_names_clash_regardless_of_case(self) -> None:
        volume = self.open()
        volume.mkdir("Docs")
        with self.assertRaises(DataError):
            volume.mkdir("DOCS")
        with self.assertRaises(DataError):
            volume.write_bytes("docs", b"x")

    def test_deleting_honours_protection_and_emptiness(self) -> None:
        volume = self.open()
        volume.mkdir("Full")
        volume.write_bytes("Full/one", b"1")
        volume.write_bytes("keep", b"x", AmigaMeta(protection=0x01))
        with self.assertRaises(DataError):
            volume.remove("keep")
        with self.assertRaises(DataError):
            volume.remove("Full")
        volume.remove("Full", recursive=True)
        self.assertFalse(volume.exists("Full"))
        with self.assertRaises(DataError):
            volume.remove(".recycled", recursive=True)
        self.assertValid(volume)

    def test_comments_that_outgrow_their_container_move_the_entry(self) -> None:
        volume = self.open()
        for index in range(12):
            volume.write_bytes(f"f{index}", b"x")
        volume.set_comment("f3", "c" * 79)
        self.assertEqual(volume.amiga_meta("f3").comment, "c" * 79)
        with self.assertRaises(DataError):
            volume.set_comment("f3", "c" * 80)
        self.assertValid(volume)

    def test_the_volume_can_be_renamed(self) -> None:
        volume = self.open()
        volume.set_title("Renamed")
        volume = self.reopen(volume, writable=False)
        self.assertEqual(volume.title, "Renamed")
        self.assertValid(volume)

    def test_a_read_only_volume_refuses_changes(self) -> None:
        volume = self.open(writable=False)
        with self.assertRaises(DataError):
            volume.write_bytes("x", b"x")

    def test_running_out_of_space_changes_nothing(self) -> None:
        volume = self.open()
        free = volume.root_info()["freeBlocks"]
        with self.assertRaises(DataError):
            volume.write_bytes("huge", bytes(64 * 1024 * 1024))
        self.assertEqual(volume.root_info()["freeBlocks"], free)
        self.assertFalse(volume.exists("huge"))
        self.assertValid(volume)


class GrowthTests(SFSTestCase):
    """Enough entries to grow every tree past its root."""

    def test_many_entries_and_fragmented_files_keep_every_structure_valid(self) -> None:
        self.make_volume(megabytes=12)
        rng = random.Random(3)
        volume = self.open()
        expected: dict[str, bytes] = {}
        # Six hundred entries give the node tree an index level.
        for index in range(40):
            volume.mkdir(f"D{index}")
        for index in range(560):
            path = f"D{index % 40}/file{index}"
            expected[path] = os.urandom(rng.randint(0, 1500))
            volume.write_bytes(path, expected[path])
        # Fill the rest of the volume, then free every other file, so the only
        # room left is a row of holes and a large file has to be scattered
        # across them, splitting the extent tree.
        filler = []
        while True:
            path = f"fill{len(filler)}"
            try:
                volume.write_bytes(path, bytes(16 * 1024))
            except DataError:
                break
            filler.append(path)
        for path in filler[::2]:
            volume.remove(path)
        free = volume.root_info()["freeBlocks"] * volume.block_size
        expected["scattered"] = os.urandom(free // 2)
        volume.write_bytes("scattered", expected["scattered"])
        self.assertGreater(len(volume.extents(volume.resolve("scattered")[0].first)), 40)
        self.assertValid(volume)
        volume = self.reopen(volume)
        for path, data in expected.items():
            self.assertEqual(volume.read_bytes(path), data, path)
        # Deleting everything merges the extent containers back into the root.
        volume.remove("scattered")
        for path in filler[1::2]:
            volume.remove(path)
        for index in range(40):
            volume.remove(f"D{index}", recursive=True)
        self.assertValid(volume)
        self.assertEqual(list(volume.iter_entries("")), [])


class CrashSafetyTests(SFSTestCase):
    """The power going off during a change leaves the old volume or the new one."""

    def setUp(self) -> None:
        super().setUp()
        self.make_volume(megabytes=12)
        volume = self.open()
        for index in range(30):
            volume.write_bytes(f"file{index}", os.urandom(600 * (index % 4)))
        volume.mkdir("Dir")
        volume.write_bytes("Dir/inner", b"inside")
        volume.close()
        self.base = self.image.read_bytes()

    def snapshot(self, volume: SFSVolume) -> dict:
        tree = {}

        def walk(path: str) -> None:
            for entry in volume.iter_entries(path):
                if entry.is_dir:
                    tree[entry.path + "/"] = None
                    walk(entry.path)
                else:
                    tree[entry.path] = volume.read_bytes(entry.path)

        walk("")
        return tree

    def attempt(self, change, cut: int | None) -> bool:
        self.image.write_bytes(self.base)
        volume = SFSVolume(BlockReader(self.image, writable=True))
        original = volume.blocks.write_block
        writes = {"count": 0}

        def failing(number, data):
            if cut is not None and writes["count"] == cut:
                raise PowerCut()
            writes["count"] += 1
            original(number, data)

        try:
            with patch.object(volume.blocks, "write_block", failing):
                change(volume)
            return True
        except PowerCut:
            return False
        finally:
            volume.blocks._handle.close()
            volume.sector_reader._handle.close()

    def test_every_write_of_every_kind_of_change_is_atomic(self) -> None:
        payload = os.urandom(30_000)
        changes = {
            "write": lambda volume: volume.write_bytes("Dir/new", payload),
            "delete": lambda volume: volume.remove("file7"),
            "rmtree": lambda volume: volume.remove("Dir", recursive=True),
            "rename": lambda volume: volume.rename("file5", "Dir/moved with a longer name"),
        }
        for label, change in changes.items():
            self.attempt(change, None)
            after = self.snapshot(self.open(writable=False))
            self.image.write_bytes(self.base)
            before = self.snapshot(self.open(writable=False))
            cut = 0
            while True:
                completed = self.attempt(change, cut)
                recovered = self.open(writable=False)
                with self.subTest(change=label, cut=cut):
                    self.assertIn(self.snapshot(recovered), (before, after))
                    self.assertValid(recovered)
                    if recovered.recovered_transaction:
                        self.assertEqual(self.snapshot(recovered), after)
                recovered.close()
                if completed:
                    break
                cut += 1
            self.assertGreater(cut, 3, label)

    def test_the_next_change_completes_an_interrupted_one(self) -> None:
        change = lambda volume: volume.write_bytes("Dir/new", b"new")  # noqa: E731
        cut = 0
        while True:
            self.assertFalse(self.attempt(change, cut))
            volume = self.open(writable=False)
            recovered = volume.recovered_transaction
            volume.close()
            if recovered:
                break
            cut += 1
        volume = self.open()
        self.assertTrue(volume.recovered_transaction)
        volume.write_bytes("afterwards", b"ok")
        volume = self.reopen(volume, writable=False)
        self.assertFalse(volume.recovered_transaction)
        self.assertEqual(volume.read_bytes("Dir/new"), b"new")
        self.assertEqual(volume.read_bytes("afterwards"), b"ok")
        self.assertValid(volume)


class ValidationTests(SFSTestCase):
    """The check is only worth running if it notices damage."""

    def setUp(self) -> None:
        super().setUp()
        self.make_volume(megabytes=8)
        volume = self.open()
        volume.mkdir("Dir")
        volume.write_bytes("Dir/file", os.urandom(5000))
        volume.close()

    def corrupt(self, block: int, offset: int, value: bytes, *, reseal: bool = True) -> list[str]:
        volume = self.open()
        data = bytearray(volume.read(block))
        data[offset : offset + len(value)] = value
        if reseal:
            data[4:8] = bytes(4)
            data[4:8] = ((-sfs_checksum(bytes(data))) & 0xFFFFFFFF).to_bytes(4, "big")
        volume.blocks.write_block(block, bytes(data))
        volume.close()
        return self.open(writable=False).validate()

    def test_a_bad_checksum_is_reported(self) -> None:
        volume = self.open(writable=False)
        container = volume.resolve("Dir/file")[0].container
        volume.close()
        self.assertTrue(self.corrupt(container, 100, b"\xff", reseal=False))

    def test_file_data_the_bitmap_calls_free_is_reported(self) -> None:
        volume = self.open(writable=False)
        first = volume.resolve("Dir/file")[0].first
        page = volume.bitmap_base
        bit = first
        volume.close()
        problems = self.corrupt(page, 12 + bit // 8, bytes([0xFF]))
        self.assertTrue(any("free" in problem for problem in problems), problems)

    def test_a_wrong_free_count_is_reported(self) -> None:
        volume = self.open(writable=False)
        root, offset = volume.root_container, volume.block_size - 36 + 8
        volume.close()
        problems = self.corrupt(root, offset, (1).to_bytes(4, "big"))
        self.assertTrue(any("free blocks" in problem for problem in problems), problems)


class PartitionTests(unittest.TestCase):
    def setUp(self) -> None:
        skip_fsync(self)

    def test_an_sfs_partition_in_a_rigid_disk_block_mounts_as_sfs(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "drive.hdf"
            with image.open("wb") as handle:
                handle.truncate(24 * 1024 * 1024)
            with BlockReader(image, writable=True) as reader:
                write_rigid_disk(reader, [
                    {"name": "DH0", "dosType": b"DOS\x03", "sizeBytes": 6 * 1024 * 1024},
                    {"name": "DH1", "dosType": b"SFS\x00", "sizeBytes": 12 * 1024 * 1024},
                ])
                disk = read_rigid_disk(reader)
                window = partition_reader(reader, disk.partitions[1])
                format_sfs_volume(window, label="Data", block_size=1024)
                window.close()
            mount, _name = mount_image(image, writable=True, partition=1)
            try:
                self.assertIsInstance(mount, SFSMount)
                self.assertEqual(mount.format, "SFS")
                mount.write_bytes("hello", b"from an RDB partition")
                self.assertEqual(mount.read_bytes("hello"), b"from an RDB partition")
                self.assertEqual(mount.validate(), [])
            finally:
                mount.close()


if __name__ == "__main__":
    unittest.main()
