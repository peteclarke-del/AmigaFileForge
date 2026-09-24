"""Reading and writing Professional File System (PFS3) volumes.

Most volumes here are formatted by the workbench itself, laid out as
pfs3aio's own format lays them out. The same code was checked against real
pfs3aio on an emulated A1200: DiskValid accepted volumes this code built and
changed, AmigaOS listed and copied every file intact, and this code read back,
checked and changed again volumes that pfs3aio had formatted and written.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import struct
import random
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from amiganut.disc.mount import mount_image
from amiganut.errors import DataError
from amiganut.file import AmigaMeta
from amiganut.filesystem import AmigaDOSFilesystem, PFS3Mount, identify
from amiganut.filesystem.blocks import ST_LINKDIR, ST_LINKFILE, ST_SOFTLINK, BlockReader
from amiganut.filesystem.pfs3 import PFS3Volume
from amiganut.filesystem.pfs3_blocks import (
    EXT_TOBEDONE,
    MODE_DELDIR,
    MODE_SPLITTED_ANODES,
    MODE_SUPERINDEX,
    ROOT_BLOCKSFREE,
    ROOT_OPTIONS,
    ROOT_ROVING_PTR,
    ST_ROLLOVERFILE,
    ExtraFields,
    encode_entry,
    fold,
    parse_entry,
    put_u32,
    u16,
    u32,
)
from amiganut.filesystem.pfs3_write import format_pfs3_volume
from amiganut.filesystem.rdb import partition_reader, read_rigid_disk, write_rigid_disk


class PowerCut(Exception):
    """Raised by a patched write to stand for the power going off."""


def skip_fsync(test: unittest.TestCase) -> None:
    """Commits flush to the medium three times; the tests have no medium to protect."""
    stub = patch("amiganut.filesystem.blocks.os.fsync")
    stub.start()
    test.addCleanup(stub.stop)


class PFS3TestCase(unittest.TestCase):
    def setUp(self) -> None:
        skip_fsync(self)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.image = Path(self.folder.name) / "pfs3.hdf"

    def make_volume(self, megabytes: float = 8, **options) -> None:
        with self.image.open("wb") as handle:
            handle.truncate(int(megabytes * 1024 * 1024))
        with BlockReader(self.image, writable=True) as reader:
            format_pfs3_volume(reader, label=options.pop("label", "Work"), **options)

    def open(self, *, writable: bool = True) -> PFS3Volume:
        volume = PFS3Volume(BlockReader(self.image, writable=writable))
        self.addCleanup(volume.close)
        return volume

    def reopen(self, volume: PFS3Volume, *, writable: bool = True) -> PFS3Volume:
        volume.close()
        return self.open(writable=writable)

    def assertValid(self, volume: PFS3Volume) -> None:
        self.assertEqual(volume.validate(), [])

    @staticmethod
    def rewind(volume: PFS3Volume) -> None:
        """Send the data roving pointer back to the start, so the next file fills holes."""
        with volume._change():
            put_u32(volume._root, ROOT_ROVING_PTR, 0)
            volume._rovingbit = 0


class FormatTests(PFS3TestCase):
    def test_a_new_volume_is_empty_valid_and_named(self) -> None:
        self.make_volume(label="Empty Disk")
        volume = self.open(writable=False)
        self.assertEqual(volume.title, "Empty Disk")
        self.assertEqual(volume.disktype, b"PFS\x01")
        self.assertEqual(list(volume.iter_entries()), [])
        self.assertEqual(volume.options & 0x7FF, 0x77F)
        self.assertEqual(volume.reserved_blksize, 1024)
        self.assertEqual(volume.max_name_length, 31)
        self.assertValid(volume)
        self.assertEqual(volume.free_block_count(), u32(volume._root, ROOT_BLOCKSFREE))

    def test_reserved_block_numbers_count_sectors_not_reserved_blocks(self) -> None:
        self.make_volume()
        volume = self.open(writable=False)
        self.assertEqual(volume.rescluster, 2)
        self.assertEqual(volume.extension_block, volume.firstreserved + u16(volume._root, 66))
        self.assertEqual((volume.extension_block - volume.firstreserved) % volume.rescluster, 0)

    def test_a_volume_beyond_five_gigabytes_uses_the_super_index(self) -> None:
        self.make_volume(megabytes=5.5 * 1024)
        volume = self.open()
        self.assertTrue(volume.options & MODE_SUPERINDEX)
        self.assertEqual(volume.disktype, b"PFS\x01")
        volume.mkdir("Dir")
        for index in range(120):
            volume.write_bytes(f"Dir/file{index}", os.urandom(700))
        volume = self.reopen(volume)
        self.assertEqual(len(list(volume.iter_entries("Dir"))), 120)
        self.assertValid(volume)

    def test_larger_logical_blocks_make_a_pfs2_volume(self) -> None:
        self.make_volume(megabytes=16, block_size=1024)
        volume = self.open()
        self.assertEqual(volume.disktype, b"PFS\x02")
        self.assertEqual(volume.block_size, 1024)
        payload = os.urandom(10_000)
        volume.write_bytes("file", payload)
        volume = self.reopen(volume)
        self.assertEqual(volume.read_bytes("file"), payload)
        self.assertValid(volume)

    def test_a_bare_pfs3_image_is_identified_by_content(self) -> None:
        self.make_volume()
        found = identify(self.image)
        self.assertEqual(found[0].filesystem, "pfs3")
        with BlockReader(self.image) as reader:
            self.assertIsNone(AmigaDOSFilesystem().identify(reader))
        mount, name = mount_image(self.image, writable=True)
        try:
            self.assertIsInstance(mount, PFS3Mount)
            self.assertEqual(name, "pfs3")
            self.assertEqual(mount.format, "PFS3")
        finally:
            mount.close()

    def test_the_name_length_is_the_volumes_to_choose(self) -> None:
        self.make_volume()
        volume = self.open()
        with self.assertRaises(DataError):
            volume.write_bytes("x" * 32, b"")
        volume.write_bytes("x" * 31, b"")
        self.make_volume(name_length=106)
        volume = self.open()
        long_name = "A name far longer than thirty-one characters, as LONGFN allows.txt"
        volume.write_bytes(long_name, b"long")
        volume = self.reopen(volume)
        self.assertEqual(volume.read_bytes(long_name.upper()), b"long")
        self.assertValid(volume)


class BlockFormatTests(unittest.TestCase):
    def test_extra_fields_pack_only_what_is_set_in_reverse_order(self) -> None:
        extra = ExtraFields(link=0x00050003, prot=0x12345600)
        packed = extra.packed()
        self.assertEqual(packed[-2:], (0b110011).to_bytes(2, "big"))
        entry = encode_entry(raw_name=b"name", entry_type=-3, anode=6, size=5, days=1, mins=2,
                             ticks=3, protection=0x12345640, comment=b"hi", extra=extra,
                             dir_extension=True, largefile=False)
        decoded = parse_entry(bytearray(20) + entry + b"\0", 20, dir_extension=True, largefile=False)
        self.assertEqual(decoded.extra.link, 0x00050003)
        self.assertEqual(decoded.full_protection, 0x12345640)
        self.assertEqual(decoded.comment, "hi")
        self.assertEqual(len(entry) % 2, 0)

    def test_names_fold_the_way_the_handler_compares_them(self) -> None:
        self.assertEqual(fold("café ÿ÷".encode("latin-1")), "CAFÉ ÿ÷".encode("latin-1"))


class ChangeTests(PFS3TestCase):
    def setUp(self) -> None:
        super().setUp()
        self.make_volume(megabytes=16)

    def test_files_directories_and_metadata_survive_a_reopen(self) -> None:
        volume = self.open()
        moment = datetime(1994, 3, 5, 12, 30, tzinfo=timezone.utc)
        volume.mkdir("Dir")
        volume.mkdir("Dir/Sub")
        volume.write_bytes("Dir/Sub/file", b"contents", AmigaMeta(protection=0x00031240, comment="a note", datestamp=moment))
        volume.write_bytes("Empty", b"")
        volume = self.reopen(volume)
        self.assertEqual([entry.name for entry in volume.iter_entries("Dir")], ["Sub"])
        self.assertTrue(volume.stat("Dir/Sub").is_dir)
        self.assertEqual(volume.read_bytes("dir/sub/FILE"), b"contents")
        self.assertEqual(volume.read_bytes("Empty"), b"")
        meta = volume.amiga_meta("Dir/Sub/file")
        self.assertEqual(meta.protection, 0x00031240)
        self.assertEqual(meta.comment, "a note")
        self.assertEqual(meta.datestamp, moment)
        self.assertEqual(volume.stat("Dir/Sub/file").length, 8)
        self.assertValid(volume)

    def test_replacing_a_file_returns_its_old_blocks(self) -> None:
        volume = self.open()
        before = volume.free_bytes()
        volume.write_bytes("file", os.urandom(40_000))
        volume.write_bytes("file", os.urandom(1_000))
        volume.write_bytes("file", b"")
        self.assertEqual(volume.free_bytes(), before)
        volume.remove("file")
        self.assertEqual(volume.free_bytes(), before)
        self.assertValid(volume)

    def test_rename_moves_between_directories_and_keeps_the_contents(self) -> None:
        volume = self.open()
        volume.mkdir("A")
        volume.mkdir("B")
        volume.mkdir("A/Tree")
        volume.write_bytes("A/Tree/leaf", b"leaf")
        volume.write_bytes("A/file", b"moved")
        volume.rename("A/file", "B/renamed")
        volume.rename("A/Tree", "B/Tree")
        volume.rename("B/renamed", "B/RENAMED")
        volume = self.reopen(volume)
        self.assertEqual(volume.read_bytes("B/RENAMED"), b"moved")
        self.assertEqual([entry.name for entry in volume.iter_entries("B") if entry.name.startswith("R")], ["RENAMED"])
        self.assertEqual(volume.read_bytes("B/Tree/leaf"), b"leaf")
        self.assertFalse(volume.exists("A/file"))
        self.assertValid(volume)

    def test_a_directory_cannot_be_moved_inside_itself(self) -> None:
        volume = self.open()
        volume.mkdir("Top")
        volume.mkdir("Top/Inner")
        with self.assertRaises(DataError):
            volume.rename("Top", "Top/Inner/Top")
        self.assertValid(volume)

    def test_names_clash_regardless_of_case_and_accents(self) -> None:
        volume = self.open()
        volume.write_bytes("Café", b"1")
        with self.assertRaises(DataError):
            volume.mkdir("CAFÉ")
        with self.assertRaises(DataError):
            volume.write_bytes("Dir:name", b"")
        volume.write_bytes("CAFÉ", b"2")
        self.assertEqual([entry.name for entry in volume.iter_entries()], ["CAFÉ"])

    def test_deleting_honours_protection_and_emptiness(self) -> None:
        volume = self.open()
        volume.mkdir("Dir")
        volume.write_bytes("Dir/locked", b"x", AmigaMeta(protection=0x01))
        with self.assertRaises(DataError):
            volume.remove("Dir/locked")
        with self.assertRaises(DataError):
            volume.remove("Dir")
        volume.set_access("Dir/locked", 0)
        volume.remove("Dir", recursive=True)
        self.assertEqual(list(volume.iter_entries()), [])
        self.assertValid(volume)

    def test_comments_that_outgrow_their_block_move_the_entry(self) -> None:
        volume = self.open()
        for index in range(60):
            volume.write_bytes(f"file with a longish name {index:02}", b"x")
        for index in range(60):
            volume.set_comment(f"file with a longish name {index:02}", f"comment {index} " + "x" * 60)
        volume = self.reopen(volume)
        for index in range(60):
            self.assertTrue(volume.comment(f"file with a longish name {index:02}").startswith(f"comment {index} "))
        self.assertGreater(len(volume.anode_chain(5)), 3)
        self.assertValid(volume)

    def test_many_entries_and_fragmented_files_keep_every_structure_valid(self) -> None:
        volume = self.open()
        rng = random.Random(3)
        contents: dict[str, bytes] = {}
        volume.mkdir("Many")
        for index in range(400):
            data = os.urandom(rng.randint(0, 4000))
            volume.write_bytes(f"Many/file{index:03}", data)
            contents[f"Many/file{index:03}"] = data
        for index in range(0, 400, 2):
            volume.remove(f"Many/file{index:03}")
            del contents[f"Many/file{index:03}"]
        self.rewind(volume)
        big = os.urandom(600_000)
        volume.write_bytes("big", big)
        contents["big"] = big
        self.assertGreater(len(volume.extents(volume.resolve("big")[0].anode)), 20)
        volume = self.reopen(volume)
        for path, data in contents.items():
            self.assertEqual(volume.read_bytes(path), data, path)
        self.assertValid(volume)
        volume.remove("Many", recursive=True)
        self.assertValid(volume)

    def test_the_volume_can_be_renamed_but_has_no_boot_options(self) -> None:
        volume = self.open()
        volume.set_title("Renamed")
        self.assertEqual(self.reopen(volume).title, "Renamed")
        volume = self.open()
        self.assertEqual(volume.boot_option(), 0)
        with self.assertRaises(DataError):
            volume.set_boot_option(1)
        with self.assertRaises(DataError):
            volume.defragment()

    def test_a_read_only_volume_refuses_changes(self) -> None:
        volume = self.open(writable=False)
        self.assertTrue(volume.read_only)
        with self.assertRaises(DataError):
            volume.mkdir("Nope")

    def test_running_out_of_space_changes_nothing_and_keeps_the_reserve(self) -> None:
        volume = self.open()
        volume.write_bytes("keep", b"kept")
        free = volume.free_bytes()
        with self.assertRaises(DataError):
            volume.write_bytes("huge", bytes(free + volume.block_size))
        self.assertFalse(volume.exists("huge"))
        self.assertEqual(volume.free_bytes(), free)
        volume.write_bytes("fits", bytes(free))
        self.assertEqual(volume.free_bytes(), 0)
        self.assertGreater(u32(volume._root, ROOT_BLOCKSFREE), 0)
        self.assertValid(volume)

    def test_the_free_map_follows_both_bitmaps(self) -> None:
        volume = self.open()
        volume.write_bytes("file", os.urandom(5000))
        flags = volume.free_map()
        self.assertEqual(len(flags), volume.total_blocks)
        self.assertFalse(flags[0])
        first = volume.extents(volume.resolve("file")[0].anode)[0][0]
        self.assertFalse(flags[first])
        data_free = sum(flags[volume.bitmap_start:])
        self.assertEqual(data_free, u32(volume._root, ROOT_BLOCKSFREE))

    def test_a_postponed_operation_is_read_but_not_written_over(self) -> None:
        volume = self.open()
        volume.write_bytes("file", b"data")
        ext = bytearray(volume._reserved(volume.extension_block))
        put_u32(ext, EXT_TOBEDONE, 1)
        volume.blocks.write_range(volume.extension_block * volume.block_size, bytes(ext))
        volume = self.reopen(volume)
        self.assertTrue(volume.read_only)
        self.assertEqual(volume.read_bytes("file"), b"data")
        with self.assertRaises(DataError):
            volume.write_bytes("other", b"")
        self.assertTrue(any("postponed" in problem for problem in volume.validate()))

    def test_volumes_without_split_anode_numbers_are_written_their_way(self) -> None:
        volume = self.open()
        options = u32(volume._root, ROOT_OPTIONS) & ~MODE_SPLITTED_ANODES
        with volume._change():
            put_u32(volume._root, ROOT_OPTIONS, options)
        volume = self.reopen(volume)
        self.assertFalse(volume.split_anodes)
        for index in range(150):
            volume.write_bytes(f"file{index}", bytes([index]) * 600)
        volume = self.reopen(volume)
        self.assertEqual(volume.read_bytes("file149"), bytes([149]) * 600)
        self.assertLess(max(entry.block for entry in volume.iter_entries()), 0x10000)
        self.assertValid(volume)


class LinkTests(PFS3TestCase):
    """Links are made here as pfs3aio makes them, to check reading and changing around them."""

    def setUp(self) -> None:
        super().setUp()
        self.make_volume(megabytes=8)

    def hard_link(self, volume: PFS3Volume, link: str, target: str) -> None:
        with volume._change():
            parent, raw = volume._split_parent(link)
            obj = volume.resolve(target)[0]
            node = volume._alloc_anode()
            volume._add_entry(parent.listing_anode, volume._encode(
                raw_name=raw, entry_type=ST_LINKFILE, anode=node, size=obj.size,
                days=obj.days, mins=obj.mins, ticks=obj.ticks, protection=0, comment=b"",
                extra=ExtraFields(link=obj.anode),
            ))
            volume._put_anode(node, obj.directory, parent.listing_anode, obj.extra.link)
            obj = volume.resolve(target)[0]
            volume._replace_entry(obj, volume._reencode(obj, extra=replace(obj.extra, link=node)))

    def test_hard_links_read_through_and_follow_moves_and_deletes(self) -> None:
        volume = self.open()
        volume.mkdir("A")
        volume.mkdir("B")
        volume.write_bytes("A/object", b"linked data")
        self.hard_link(volume, "B/first", "A/object")
        self.hard_link(volume, "second", "A/object")
        volume = self.reopen(volume)
        self.assertEqual(volume.read_bytes("B/first"), b"linked data")
        self.assertTrue(next(iter(volume.iter_entries("B"))).is_link)
        self.assertValid(volume)
        volume.rename("A/object", "object")
        volume.rename("B/first", "first")
        self.assertEqual(volume.read_bytes("second"), b"linked data")
        self.assertValid(volume)
        volume.write_bytes("object", b"new contents, longer")
        self.assertEqual(volume.stat("first").length, 20)
        volume.remove("first")
        self.assertValid(volume)
        volume.remove("object")
        # The remaining link has become the object and still holds the data.
        self.assertEqual(volume.read_bytes("second"), b"new contents, longer")
        self.assertFalse(volume.resolve("second")[0].is_link)
        self.assertValid(volume)
        volume.remove("second")
        self.assertValid(volume)

    def test_a_linked_directory_passes_to_its_link_when_deleted(self) -> None:
        volume = self.open()
        volume.mkdir("A")
        volume.mkdir("A/Tree")
        volume.write_bytes("A/Tree/leaf", b"leaf")
        volume.mkdir("B")
        with volume._change():
            parent, raw = volume._split_parent("B/link")
            obj = volume.resolve("A/Tree")[0]
            node = volume._alloc_anode()
            volume._add_entry(parent.listing_anode, volume._encode(
                raw_name=raw, entry_type=ST_LINKDIR, anode=node, size=0, days=0, mins=0,
                ticks=0, protection=0, comment=b"", extra=ExtraFields(link=obj.anode),
            ))
            volume._put_anode(node, obj.directory, parent.listing_anode, 0)
            volume._replace_entry(obj, volume._reencode(obj, extra=replace(obj.extra, link=node)))
        self.assertEqual(volume.read_bytes("B/link/leaf"), b"leaf")
        self.assertValid(volume)
        volume.remove("A", recursive=True)
        self.assertEqual(volume.read_bytes("B/link/leaf"), b"leaf")
        self.assertTrue(volume.stat("B/link").is_dir)
        self.assertValid(volume)

    def test_soft_links_and_rollover_files_are_read(self) -> None:
        volume = self.open()
        with volume._change():
            parent, raw = volume._split_parent("soft")
            node = volume._alloc_anode()
            runs = volume._allocate_data(1)
            volume._write_data(runs, b"Work:target\0")
            volume._write_chain(node, runs)
            volume._add_entry(parent.listing_anode, volume._encode(
                raw_name=raw, entry_type=ST_SOFTLINK, anode=node, size=11, days=0, mins=0,
                ticks=0, protection=0, comment=b"", extra=ExtraFields(),
            ))
            parent, raw = volume._split_parent("ring")
            node = volume._alloc_anode()
            runs = volume._allocate_data(1)
            volume._write_data(runs, b"3456789012")
            volume._write_chain(node, runs)
            volume._add_entry(parent.listing_anode, volume._encode(
                raw_name=raw, entry_type=ST_ROLLOVERFILE, anode=node, size=10, days=0, mins=0,
                ticks=0, protection=0, comment=b"", extra=ExtraFields(virtualsize=9, rollpointer=8),
            ))
        volume = self.reopen(volume)
        self.assertEqual(volume.read_bytes("soft"), b"Work:target")
        self.assertTrue(next(entry for entry in volume.iter_entries() if entry.name == "soft").is_link)
        self.assertEqual(volume.read_bytes("ring"), b"123456789")
        self.assertValid(volume)
        volume.remove("soft")
        volume.remove("ring")
        self.assertValid(volume)


class AtomicityTests(PFS3TestCase):
    """Power lost at any write leaves the volume as it was or as it became."""

    def setUp(self) -> None:
        super().setUp()
        self.make_volume(megabytes=4)
        volume = self.open()
        volume.mkdir("Dir")
        for index in range(12):
            volume.write_bytes(f"file{index}", os.urandom(1500))
            volume.write_bytes(f"Dir/inner{index}", os.urandom(700))
        volume.close()
        self.base = self.image.read_bytes()

    def snapshot(self, volume: PFS3Volume) -> dict:
        state = {}

        def walk(path: str) -> None:
            for entry in volume.iter_entries(path):
                if entry.is_dir:
                    state[entry.path] = "dir"
                    walk(entry.path)
                else:
                    state[entry.path] = hashlib.md5(volume.read_bytes(entry.path)).hexdigest()

        walk("")
        return state

    def attempt(self, change, cut: int | None) -> bool:
        self.image.write_bytes(self.base)
        volume = PFS3Volume(BlockReader(self.image, writable=True))
        original = volume.blocks.write_range
        writes = [0]

        def failing(offset, data):
            if cut is not None and writes[0] >= cut:
                raise PowerCut()
            writes[0] += 1
            original(offset, data)

        try:
            with patch.object(volume.blocks, "write_range", failing):
                change(volume)
            return True
        except PowerCut:
            return False
        finally:
            volume.blocks._handle.close()
            volume.sector_reader._handle.close()

    def test_every_write_of_every_kind_of_change_is_atomic(self) -> None:
        payload = os.urandom(30_000)
        leak = "reserved blocks are marked in use but nothing refers to them"
        changes = {
            "write": lambda volume: volume.write_bytes("Dir/new", payload),
            "replace": lambda volume: volume.write_bytes("file3", payload),
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
                    # A cut between the two root writes leaves the old places of
                    # moved blocks marked in use, which is all it may leave.
                    self.assertEqual([problem for problem in recovered.validate() if leak not in problem], [])
                recovered.close()
                if completed:
                    break
                cut += 1
            self.assertGreater(cut, 3, label)


class ValidationTests(PFS3TestCase):
    """The check is only worth running if it notices damage."""

    def setUp(self) -> None:
        super().setUp()
        self.make_volume(megabytes=8)
        volume = self.open()
        volume.mkdir("Dir")
        volume.write_bytes("Dir/file", os.urandom(5000))
        volume.close()

    def test_file_data_the_bitmap_calls_free_is_reported(self) -> None:
        volume = self.open()
        first = volume.extents(volume.resolve("Dir/file")[0].anode)[0][0]
        number = volume._bitmap_block_number(0)
        data = bytearray(volume._reserved(number))
        bit = first - volume.bitmap_start
        data[12 + bit // 8] |= 0x80 >> (bit % 8)
        volume.blocks.write_range(number * volume.block_size, bytes(data))
        problems = self.reopen(volume, writable=False).validate()
        self.assertTrue(any("marks free" in problem for problem in problems), problems)

    def test_a_wrong_free_count_is_reported(self) -> None:
        volume = self.open()
        put_u32(volume._root, ROOT_BLOCKSFREE, u32(volume._root, ROOT_BLOCKSFREE) + 1)
        volume.blocks.write_range(2 * volume.block_size, bytes(volume._root))
        problems = self.reopen(volume, writable=False).validate()
        self.assertTrue(any("free blocks" in problem for problem in problems), problems)

    def test_a_directory_block_naming_the_wrong_parent_is_reported(self) -> None:
        volume = self.open()
        block = volume.anode(volume.resolve("Dir")[0].anode).blocknr
        data = bytearray(volume._reserved(block))
        put_u32(data, 16, 77)
        volume.blocks.write_range(block * volume.block_size, bytes(data))
        problems = self.reopen(volume, writable=False).validate()
        self.assertTrue(any("wrong parent" in problem for problem in problems), problems)

    def test_an_unreferenced_anode_is_reported(self) -> None:
        volume = self.open()
        with volume._change():
            volume._alloc_anode()
        problems = self.reopen(volume, writable=False).validate()
        self.assertTrue(any("nothing refers" in problem for problem in problems), problems)


class RealPFS3Tests(unittest.TestCase):
    """A volume formatted and filled by pfs3aio 19.2 itself on an emulated A1200.

    PFSFormat had the handler format an 8 MB partition, then AmigaOS copied
    files in, set a comment and protection bits, deleted a file (which pfs3aio
    keeps in its deleted-files directory) and made a hard link, a hard link to
    a directory and a soft link. The fixture keeps only the blocks that are
    not zero, as (sector number, sector) pairs after the partition length.
    """

    FIXTURE = Path(__file__).with_name("pfs3aio_volume.bin.gz")

    def setUp(self) -> None:
        skip_fsync(self)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.image = Path(folder.name) / "pfs3aio.hdf"
        raw = gzip.decompress(self.FIXTURE.read_bytes())
        with self.image.open("wb") as handle:
            handle.truncate(struct.unpack(">I", raw[:4])[0])
            for offset in range(4, len(raw), 516):
                handle.seek(struct.unpack(">I", raw[offset : offset + 4])[0] * 512)
                handle.write(raw[offset + 4 : offset + 516])

    def open(self, *, writable: bool = False) -> PFS3Volume:
        volume = PFS3Volume(BlockReader(self.image, writable=writable))
        self.addCleanup(volume.close)
        return volume

    def test_everything_pfs3aio_wrote_reads_back(self) -> None:
        volume = self.open()
        self.assertEqual(volume.title, "Fixture")
        self.assertEqual(sorted(entry.name for entry in volume.iter_entries()),
                         ["Drawer", "Startup-Sequence", "dirlink", "hardlink", "softlink"])
        self.assertEqual(volume.read_bytes("Drawer/note.txt"), b"written by pfs3aio\n")
        self.assertEqual(volume.comment("Drawer/note.txt"), "a comment from real PFS3")
        self.assertEqual(volume.amiga_meta("Drawer/Info").protection, 0x20)
        self.assertEqual(hashlib.md5(volume.read_bytes("Drawer/Info")).hexdigest(),
                         "f5523f7eea9b69d5dd8dc48f13eea7e9")
        self.assertEqual(volume.read_bytes("hardlink"), b"written by pfs3aio\n")
        self.assertEqual(volume.stat("hardlink").secondary_type, ST_LINKFILE)
        self.assertEqual(sorted(entry.name for entry in volume.iter_entries("dirlink")), ["Info", "note.txt"])
        self.assertEqual(volume.read_bytes("softlink"), b"Fixture:Drawer/note.txt")
        self.assertEqual(volume.validate(), [])

    def test_a_pfs3aio_volume_can_be_changed_here(self) -> None:
        volume = self.open(writable=True)
        volume.write_bytes("Drawer/new", os.urandom(20_000), AmigaMeta(comment="added here"))
        volume.rename("Drawer/note.txt", "moved.txt")
        volume.remove("hardlink")
        volume.remove("softlink")
        volume.remove("Startup-Sequence")
        self.assertEqual(volume.validate(), [])
        volume.close()
        volume = self.open()
        self.assertEqual(volume.read_bytes("moved.txt"), b"written by pfs3aio\n")
        self.assertEqual(volume.comment("Drawer/new"), "added here")
        self.assertEqual(volume.validate(), [])


class PartitionTests(unittest.TestCase):
    def setUp(self) -> None:
        skip_fsync(self)

    def test_pfs3_partitions_in_a_rigid_disk_block_mount_as_pfs3(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "drive.hdf"
            with image.open("wb") as handle:
                handle.truncate(40 * 1024 * 1024)
            dos_types = [b"PFS\x03", b"PDS\x03", b"PFS\x01"]
            with BlockReader(image, writable=True) as reader:
                write_rigid_disk(reader, [
                    {"name": f"DH{index}", "dosType": dos_type, "sizeBytes": 12 * 1024 * 1024}
                    for index, dos_type in enumerate(dos_types)
                ])
                disk = read_rigid_disk(reader)
                for partition in disk.partitions:
                    window = partition_reader(reader, partition)
                    format_pfs3_volume(window, label=f"Part{partition.index}")
                    window.close()
            for index in range(len(dos_types)):
                mount, _name = mount_image(image, writable=True, partition=index)
                try:
                    self.assertIsInstance(mount, PFS3Mount)
                    self.assertEqual(mount.title, f"Part{index}")
                    mount.write_bytes("hello", b"from an RDB partition")
                    self.assertEqual(mount.read_bytes("hello"), b"from an RDB partition")
                    self.assertEqual(mount.validate(), [])
                finally:
                    mount.close()

    def test_the_deleted_files_directory_is_kept_when_files_are_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "volume.hdf"
            with image.open("wb") as handle:
                handle.truncate(8 * 1024 * 1024)
            with BlockReader(image, writable=True) as reader:
                format_pfs3_volume(reader, label="Deldir")
            volume = PFS3Volume(BlockReader(image, writable=True))
            try:
                self.assertTrue(volume.options & MODE_DELDIR)
                volume.write_bytes("gone", b"x" * 3000)
                volume.remove("gone")
                self.assertEqual(volume.validate(), [])
            finally:
                volume.close()


if __name__ == "__main__":
    unittest.main()
