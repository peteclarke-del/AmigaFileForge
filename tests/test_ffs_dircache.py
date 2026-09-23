"""Directory-cache volumes, DOS\\4 and DOS\\5.

An Amiga's ``List`` and Workbench read a directory's cache blocks rather than
its hash chains, so a change that left the cache behind would show the wrong
contents on the machine even though every header was correct. These tests
check after every kind of change that each cache matches the headers exactly,
and cross-check the cache format against amitools where it is available. The
same code was also checked against the FFS in Kickstart 3.1 on an emulated
A1200, which listed a volume built here and whose own changes read back
cleanly.
"""

from __future__ import annotations

import dataclasses
import os
import random
import struct
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from amiganut.errors import DataError
from amiganut.file import AmigaMeta
from amiganut.filesystem.amigados import AmigaDOSVolume, format_volume
from amiganut.filesystem.blocks import (
    BlockReader,
    DirCacheRecord,
    T_DIRCACHE,
    is_dircache,
    is_international,
    is_long_names,
    long_at,
    pack_dircache_records,
    unpack_dircache_records,
)
from tests import amitools_reference

DD_BYTES = 1760 * 512


class DosTypeFlagTests(unittest.TestCase):
    def test_only_dos4_and_dos5_keep_a_directory_cache(self) -> None:
        flags = {value: is_dircache(b"DOS" + bytes([value])) for value in range(8)}
        self.assertEqual(flags, {0: False, 1: False, 2: False, 3: False, 4: True, 5: True, 6: False, 7: False})

    def test_dos2_to_dos7_are_international(self) -> None:
        flags = {value: is_international(b"DOS" + bytes([value])) for value in range(8)}
        self.assertEqual(flags, {0: False, 1: False, 2: True, 3: True, 4: True, 5: True, 6: True, 7: True})

    def test_only_dos6_and_dos7_have_long_names(self) -> None:
        flags = {value: is_long_names(b"DOS" + bytes([value])) for value in range(8)}
        self.assertEqual(flags, {0: False, 1: False, 2: False, 3: False, 4: False, 5: False, 6: True, 7: True})


class RecordPackingTests(unittest.TestCase):
    def record(self, name: bytes, comment: bytes = b"") -> DirCacheRecord:
        return DirCacheRecord(
            header=881, size=1234, protection=0x0F, uid=0, gid=0, days=17000,
            mins=600, ticks=25, secondary_type=-3, name=name, comment=comment,
        )

    def test_records_start_on_even_offsets(self) -> None:
        odd = self.record(b"ab")          # 25 + 2 = 27 bytes, padded to 28
        even = self.record(b"abc")        # 25 + 3 = 28 bytes, no padding
        self.assertEqual(odd.packed_size, 28)
        self.assertEqual(even.packed_size, 28)
        self.assertEqual(len(odd.pack()), 28)

    def test_field_layout_matches_the_documented_record(self) -> None:
        packed = self.record(b"Name", b"Note").pack()
        self.assertEqual(struct.unpack_from(">IIIHHHHH", packed), (881, 1234, 0x0F, 0, 0, 17000, 600, 25))
        self.assertEqual(packed[22], 0xFD)        # ST_FILE as a signed byte
        self.assertEqual(packed[23], 4)
        self.assertEqual(packed[24:28], b"Name")
        self.assertEqual(packed[28], 4)
        self.assertEqual(packed[29:33], b"Note")

    def test_round_trip_through_a_block(self) -> None:
        records = [self.record(b"x" * length, b"c" * (length % 5)) for length in range(1, 12)]
        block = bytes(24) + pack_dircache_records(records)
        self.assertEqual(unpack_dircache_records(block, len(records)), records)

    def test_a_count_that_overruns_the_block_is_refused(self) -> None:
        block = bytes(24) + pack_dircache_records([self.record(b"a")])
        with self.assertRaises(DataError):
            unpack_dircache_records(block, 40)


class DirCacheVolumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "dc.adf"
        self.path.write_bytes(bytes(DD_BYTES))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def fresh(self, dos_type: bytes = b"DOS\x05") -> AmigaDOSVolume:
        return format_volume(BlockReader(self.path, writable=True), label="Cache", dos_type=dos_type)

    def assertConsistent(self, volume: AmigaDOSVolume) -> None:
        self.assertEqual(volume.validate(), [])

    def cache_names(self, volume: AmigaDOSVolume, directory_block: int) -> set[str]:
        return {
            record.name.decode("latin-1")
            for _block, records, _raw in volume._load_cache(directory_block)
            for record in records
        }

    def test_format_gives_the_root_one_empty_cache_block(self) -> None:
        for dos_type in (b"DOS\x04", b"DOS\x05"):
            with self.subTest(dos_type=dos_type):
                volume = self.fresh(dos_type)
                root = volume.reader.read_block(volume.root_block)
                cache_block = long_at(root, 512 - 8)
                self.assertNotEqual(cache_block, 0)
                raw = volume.reader.read_block(cache_block)
                self.assertEqual(long_at(raw, 0), T_DIRCACHE)
                self.assertEqual(long_at(raw, 4), cache_block)
                self.assertEqual(long_at(raw, 8), volume.root_block)
                self.assertEqual(long_at(raw, 12), 0)
                self.assertFalse(volume._is_free(cache_block))
                self.assertConsistent(volume)
                volume.close()

    def test_long_name_volumes_get_no_cache(self) -> None:
        volume = self.fresh(b"DOS\x07")
        self.assertFalse(volume.dircache)
        root = volume.reader.read_block(volume.root_block)
        self.assertEqual(long_at(root, 512 - 8), 0)
        volume.mkdir("Plain")
        header = volume.reader.read_block(volume.stat("Plain").block)
        self.assertEqual(long_at(header, 512 - 8), 0)
        volume.close()

    def test_every_kind_of_change_keeps_the_cache_exact(self) -> None:
        volume = self.fresh()
        rng = random.Random(5)
        volume.mkdir("Tools")
        volume.mkdir("Tools/Deep")
        volume.mkdir("Tools/Deep/Deeper")
        self.assertConsistent(volume)
        for index in range(45):
            volume.write_bytes(
                f"Tools/File{index:02d}.dat",
                rng.randbytes(rng.randrange(0, 3000)),
                AmigaMeta(protection=(index % 8) << 4, comment="n" * (index % 30), datestamp=None),
            )
        volume.write_bytes("Tools/Deep/Deeper/leaf", b"leaf")
        self.assertConsistent(volume)
        tools = volume.stat("Tools").block
        self.assertGreater(len(volume._load_cache(tools)), 1, "the chain should span blocks")

        volume.rename("Tools/File03.dat", "Tools/Renamed.dat")
        volume.rename("Tools/File04.dat", "Tools/Deep/Moved.dat")
        volume.rename("Tools/Deep", "Tools/Relocated")
        volume.set_comment("Tools/File05.dat", "A comment that is quite a lot longer than before")
        volume.set_access("Tools/File06.dat", 0x05)
        volume.set_datestamp("Tools/File07.dat", datetime(1994, 3, 1, 12, 30, tzinfo=timezone.utc))
        volume.write_bytes("Tools/File08.dat", b"replaced")
        self.assertConsistent(volume)
        self.assertIn("Renamed.dat", self.cache_names(volume, tools))
        self.assertNotIn("File03.dat", self.cache_names(volume, tools))
        self.assertIn("Moved.dat", self.cache_names(volume, volume.stat("Tools/Relocated").block))

        for index in range(10, 40):
            volume.remove(f"Tools/File{index:02d}.dat")
        self.assertConsistent(volume)
        volume.remove("Tools/Relocated", recursive=True)
        self.assertConsistent(volume)
        self.assertNotIn("Relocated", self.cache_names(volume, tools))
        volume.close()

    def test_directory_dates_in_the_parent_cache_follow_changes_inside(self) -> None:
        volume = self.fresh()
        volume.mkdir("Outer")
        volume.mkdir("Outer/Inner")
        volume.set_datestamp("Outer/Inner", datetime(1990, 1, 1, tzinfo=timezone.utc))
        volume.write_bytes("Outer/Inner/new", b"x")
        self.assertConsistent(volume)
        volume.close()

    def test_deleting_everything_returns_every_cache_block(self) -> None:
        volume = self.fresh()
        baseline = volume.free_bytes()
        volume.mkdir("Box")
        for index in range(60):
            volume.write_bytes(f"Box/entry-number-{index:03d}", b"")
        volume.remove("Box", recursive=True)
        self.assertEqual(volume.free_bytes(), baseline)
        self.assertEqual(len(volume._load_cache(volume.root_block)), 1)
        self.assertConsistent(volume)
        volume.close()

    def test_changes_survive_a_remount(self) -> None:
        volume = self.fresh(b"DOS\x04")
        volume.mkdir("A")
        volume.write_bytes("A/one", b"1" * 1000, AmigaMeta(protection=0, comment="first", datestamp=None))
        volume.close()
        again = AmigaDOSVolume(BlockReader(self.path, writable=True))
        self.assertEqual(again.format, "OFS-DC")
        self.assertConsistent(again)
        again.remove("A/one")
        self.assertConsistent(again)
        again.close()

    def test_validate_notices_a_stale_cache_and_rebuild_repairs_it(self) -> None:
        volume = self.fresh()
        volume.mkdir("Dir")
        for index in range(30):
            volume.write_bytes(f"Dir/f{index}", b"data")
        directory = volume.stat("Dir").block
        # Emulate a tool that ignores the cache: drop two records and
        # corrupt a third behind the volume's back.
        chain = volume._load_cache(directory)
        records = chain[0][1]
        del records[0:2]
        first = records[0]
        records[0] = dataclasses.replace(first, protection=first.protection ^ 1)
        volume._commit_cache(directory, chain)
        problems = volume.validate()
        self.assertTrue(any("has no record" in problem for problem in problems))
        self.assertTrue(any("disagrees" in problem for problem in problems))
        self.assertGreater(volume.rebuild_dircache(), 0)
        self.assertConsistent(volume)
        volume.close()


@unittest.skipUnless(amitools_reference.AVAILABLE, "amitools is not available")
class AmitoolsCrossCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "dc.adf"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_amitools_reads_what_amiganut_wrote(self) -> None:
        self.path.write_bytes(bytes(DD_BYTES))
        volume = format_volume(BlockReader(self.path, writable=True), label="Cross", dos_type=b"DOS\x05")
        rng = random.Random(9)
        expected = {}
        volume.mkdir("Sub")
        volume.mkdir("Sub/Two")
        expected["Sub"] = (True, 0, "", None)
        expected["Sub/Two"] = (True, 0, "", None)
        for index in range(40):
            path = f"Sub/item{index:02d}" if index % 2 else f"Sub/Two/thing{index:02d}"
            data = rng.randbytes(rng.randrange(0, 2000))
            comment = f"comment {index}" if index % 3 else ""
            volume.write_bytes(path, data, AmigaMeta(protection=(index % 8) << 4, comment=comment, datestamp=None))
            expected[path] = (False, (index % 8) << 4, comment, data)
        volume.remove("Sub/item01")
        del expected["Sub/item01"]
        volume.rename("Sub/item03", "Sub/Two/item03")
        expected["Sub/Two/item03"] = expected.pop("Sub/item03")
        self.assertEqual(volume.validate(), [])
        volume.close()

        ami, blkdev = amitools_reference.open_volume(self.path)
        try:
            self.assertEqual(amitools_reference.tree(ami), expected)
            self.assertEqual(amitools_reference.structure_errors(blkdev), [])
            records = amitools_reference.cache_records(ami)
            for directory, contents in records.items():
                names = sorted(record.name.get_unicode() for record in contents)
                wanted = sorted(
                    path.rsplit("/", 1)[-1]
                    for path in expected
                    if (path.rsplit("/", 1)[0] if "/" in path else "") == directory
                )
                self.assertEqual(names, wanted, directory)
                for record in contents:
                    full = f"{directory}/{record.name.get_unicode()}" if directory else record.name.get_unicode()
                    is_dir, protection, comment, data = expected[full]
                    self.assertEqual(record.protect, protection)
                    self.assertEqual(record.comment.get_unicode(), comment)
                    self.assertEqual(record.size, 0 if is_dir else len(data))
                    self.assertEqual(record.type, 2 if is_dir else 0xFD)
        finally:
            blkdev.close()

    def test_amiganut_reads_and_repairs_what_amitools_wrote(self) -> None:
        from amitools.fs.DosType import DOS5

        ami, blkdev = amitools_reference.create_volume(self.path, DOS5)
        fs = amitools_reference.fs_string
        ami.create_dir(fs("Stuff"))
        payloads = {}
        for index in range(25):
            data = os.urandom(index * 97)
            ami.write_file(data, fs(f"Stuff/f{index}"))
            payloads[f"Stuff/f{index}"] = data
        ami.close()
        blkdev.close()

        volume = AmigaDOSVolume(BlockReader(self.path, writable=True))
        self.assertEqual(volume.format, "FFS-DC")
        for path, data in payloads.items():
            self.assertEqual(volume.read_bytes(path), data)
        # amitools leaves the type byte of each record at zero; that is the
        # only way its cache differs from the headers.
        problems = volume.validate()
        self.assertTrue(problems)
        self.assertTrue(all("secondary_type" in problem for problem in problems), problems)
        volume.rebuild_dircache()
        self.assertEqual(volume.validate(), [])
        volume.write_bytes("Stuff/added", b"new")
        volume.remove("Stuff/f3")
        self.assertEqual(volume.validate(), [])
        volume.close()

        ami, blkdev = amitools_reference.open_volume(self.path)
        try:
            names = set(amitools_reference.tree(ami))
            self.assertIn("Stuff/added", names)
            self.assertNotIn("Stuff/f3", names)
            self.assertEqual(amitools_reference.structure_errors(blkdev), [])
        finally:
            blkdev.close()


if __name__ == "__main__":
    unittest.main()
