"""Long-name volumes, DOS\\6 and DOS\\7.

These variants come from the FFS of AmigaOS 3.1.4 and 3.2, which is not
available to the test suite. The format is therefore checked against amitools
in both directions where amitools is available, and not against a real FFS.
"""

from __future__ import annotations

import os
import random
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from amiganut.errors import DataError
from amiganut.file import AmigaMeta, datetime_to_datestamp
from amiganut.filesystem.amigados import AmigaDOSVolume, format_volume
from amiganut.filesystem.blocks import (
    BlockReader,
    T_COMMENT,
    hash_name,
    long_at,
)
from tests import amitools_reference

DD_BYTES = 1760 * 512


def long_name(length: int, stem: str = "Name") -> str:
    text = f"{stem}-{length}-"
    return (text + "abcdefghijklmnopqrstuvwxyz" * 5)[:length]


class LongNameVolumeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ln.adf"
        self.path.write_bytes(bytes(DD_BYTES))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def fresh(self, dos_type: bytes = b"DOS\x07") -> AmigaDOSVolume:
        return format_volume(BlockReader(self.path, writable=True), label="Long", dos_type=dos_type)

    def header(self, volume: AmigaDOSVolume, path: str) -> bytes:
        return volume.reader.read_block(volume.stat(path).block)

    def test_both_variants_are_writable(self) -> None:
        for dos_type, label in ((b"DOS\x06", "OFS-LNFS"), (b"DOS\x07", "FFS-LNFS")):
            with self.subTest(dos_type=dos_type):
                volume = self.fresh(dos_type)
                self.assertEqual(volume.format, label)
                self.assertFalse(volume.read_only)
                self.assertTrue(volume.international)
                self.assertFalse(volume.dircache)
                self.assertEqual(volume.name_limit, 107)
                self.assertEqual(volume.validate(), [])
                volume.close()

    def test_names_from_31_to_107_characters_round_trip(self) -> None:
        volume = self.fresh()
        volume.mkdir(long_name(64, "Directory"))
        written = {}
        for length in (1, 30, 31, 55, 80, 106, 107):
            name = long_name(length)
            volume.write_bytes(f"{long_name(64, 'Directory')}/{name}", name.encode())
            written[name] = name.encode()
        listed = {entry.name for entry in volume.iter_entries(long_name(64, "Directory"))}
        self.assertEqual(listed, set(written))
        for name, data in written.items():
            self.assertEqual(volume.read_bytes(f"{long_name(64, 'Directory')}/{name}"), data)
        self.assertEqual(volume.validate(), [])
        volume.close()

    def test_a_108_character_name_is_refused(self) -> None:
        volume = self.fresh()
        with self.assertRaises(DataError):
            volume.write_bytes(long_name(108), b"")
        with self.assertRaises(DataError):
            volume.set_title(long_name(31))
        volume.close()

    def test_standard_volumes_still_stop_at_30(self) -> None:
        volume = self.fresh(b"DOS\x03")
        with self.assertRaises(DataError):
            volume.write_bytes(long_name(31), b"")
        volume.close()

    def test_lookup_folds_case_and_latin1_letters(self) -> None:
        volume = self.fresh()
        name = "Überlänge Dateiname mit vielen Buchstaben àéîõü " + "x" * 40
        volume.write_bytes(name, b"data")
        self.assertEqual(volume.read_bytes(name.upper()), b"data")
        slot = hash_name(name, True, volume.hash_table_size)
        root = volume.reader.read_block(volume.root_block)
        self.assertEqual(long_at(root, 24 + slot * 4), volume.stat(name).block)
        volume.close()

    def test_header_layout(self) -> None:
        volume = self.fresh()
        name = long_name(40)
        volume.write_bytes(name, b"x", AmigaMeta(protection=0, comment="note", datestamp=None))
        header = self.header(volume, name)
        area = header[512 - 184 : 512 - 72]
        self.assertEqual(area[0], 40)
        self.assertEqual(area[1:41].decode(), name)
        self.assertEqual(area[41], 4)
        self.assertEqual(area[42:46], b"note")
        self.assertEqual(long_at(header, 512 - 72), 0)
        volume.close()

    def test_dates_live_60_bytes_from_the_end_and_leave_the_name_alone(self) -> None:
        volume = self.fresh()
        name = long_name(100)
        volume.write_bytes(name, b"x", AmigaMeta(protection=0, comment="c" * 8, datestamp=None))
        moment = datetime(1995, 6, 1, 10, 0, tzinfo=timezone.utc)
        volume.set_datestamp(name, moment)
        self.assertEqual(volume.datestamp(name), moment)
        self.assertEqual([entry.name for entry in volume.iter_entries("")], [name])
        self.assertEqual(volume.comment(name), "c" * 8)
        header = self.header(volume, name)
        days, mins, ticks = datetime_to_datestamp(moment)
        self.assertEqual(
            (long_at(header, 512 - 60), long_at(header, 512 - 56), long_at(header, 512 - 52)),
            (days, mins, ticks),
        )
        volume.close()

    def test_a_comment_that_does_not_fit_moves_to_its_own_block_and_back(self) -> None:
        volume = self.fresh()
        name = long_name(100)
        volume.write_bytes(name, b"payload")
        baseline = volume.free_bytes()

        volume.set_comment(name, "This comment cannot fit beside a name of one hundred characters")
        header = self.header(volume, name)
        pointer = long_at(header, 512 - 72)
        self.assertNotEqual(pointer, 0)
        self.assertEqual(header[512 - 184 + 1 + 100], 0)
        comment_block = volume.reader.read_block(pointer)
        self.assertEqual(long_at(comment_block, 0), T_COMMENT)
        self.assertEqual(long_at(comment_block, 4), pointer)
        self.assertEqual(long_at(comment_block, 8), volume.stat(name).block)
        self.assertEqual(volume.comment(name), "This comment cannot fit beside a name of one hundred characters")
        self.assertEqual(volume.free_bytes(), baseline - volume.data_capacity)
        self.assertEqual(volume.validate(), [])

        volume.set_comment(name, "short")
        self.assertEqual(long_at(self.header(volume, name), 512 - 72), 0)
        self.assertEqual(volume.comment(name), "short")
        self.assertEqual(volume.free_bytes(), baseline)
        self.assertEqual(volume.validate(), [])
        volume.close()

    def test_rename_moves_the_comment_to_suit_the_new_name(self) -> None:
        volume = self.fresh()
        comment = "A forty character comment for the rename"
        volume.mkdir("Dir")
        volume.write_bytes("Dir/short", b"x", AmigaMeta(protection=0, comment=comment, datestamp=None))
        self.assertEqual(long_at(self.header(volume, "Dir/short"), 512 - 72), 0)
        longer = long_name(90)
        volume.rename("Dir/short", longer)
        self.assertNotEqual(long_at(self.header(volume, longer), 512 - 72), 0)
        self.assertEqual(volume.comment(longer), comment)
        volume.rename(longer, "Dir/back")
        self.assertEqual(long_at(self.header(volume, "Dir/back"), 512 - 72), 0)
        self.assertEqual(volume.comment("Dir/back"), comment)
        self.assertEqual(volume.validate(), [])
        volume.close()

    def test_removing_an_entry_frees_its_comment_block(self) -> None:
        volume = self.fresh()
        baseline = volume.free_bytes()
        name = long_name(107)
        volume.write_bytes(name, b"z" * 2000, AmigaMeta(protection=0, comment="k" * 79, datestamp=None))
        volume.mkdir(long_name(105, "Folder"))
        volume.set_comment(long_name(105, "Folder"), "folder comment too long to sit beside the name")
        self.assertEqual(volume.validate(), [])
        volume.remove(name)
        volume.remove(long_name(105, "Folder"))
        self.assertEqual(volume.free_bytes(), baseline)
        self.assertEqual(volume.validate(), [])
        volume.close()

    def test_root_keeps_its_dos_type_and_a_count_of_used_blocks(self) -> None:
        volume = self.fresh()
        root = volume.reader.read_block(volume.root_block)
        self.assertEqual(root[512 - 16 : 512 - 12], b"DOS\x07")
        self.assertEqual(long_at(root, 512 - 44), 2)       # root and bitmap
        volume.write_bytes(long_name(70), b"q" * 1500)
        volume.flush()
        root = volume.reader.read_block(volume.root_block)
        bits = volume._load_bitmap()
        self.assertEqual(long_at(root, 512 - 44), len(bits) - sum(bits))
        self.assertEqual(volume.validate(), [])
        volume.close()

    def test_the_root_keeps_the_classic_layout(self) -> None:
        volume = self.fresh()
        volume.set_title("Renamed Volume")
        self.assertEqual(volume.title, "Renamed Volume")
        meta = volume.amiga_meta("")
        self.assertEqual(meta.comment, "")
        volume.set_amiga_meta("", AmigaMeta(protection=0xFF, comment="ignored", datestamp=None))
        self.assertEqual(volume.validate(), [])
        volume.close()


@unittest.skipUnless(amitools_reference.AVAILABLE, "amitools is not available")
class AmitoolsCrossCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ln.adf"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_amitools_reads_what_amiganut_wrote(self) -> None:
        for dos_type in (b"DOS\x06", b"DOS\x07"):
            with self.subTest(dos_type=dos_type):
                self.path.write_bytes(bytes(DD_BYTES))
                volume = format_volume(BlockReader(self.path, writable=True), label="Cross", dos_type=dos_type)
                rng = random.Random(3)
                expected = {}
                folder = long_name(60, "Folder")
                volume.mkdir(folder)
                expected[folder] = (True, 0, "", None)
                for length in range(31, 108, 7):
                    name = long_name(length)
                    path = f"{folder}/{name}" if length % 2 else name
                    comment = "m" * rng.randrange(0, 80)
                    data = rng.randbytes(rng.randrange(0, 5000))
                    volume.write_bytes(path, data, AmigaMeta(protection=0, comment=comment, datestamp=None))
                    expected[path] = (False, 0, comment, data)
                renamed = long_name(107, "Renamed")
                source = next(path for path in expected if path.startswith(folder + "/"))
                volume.rename(source, renamed)
                expected[renamed] = expected.pop(source)
                self.assertEqual(volume.validate(), [])
                volume.close()

                ami, blkdev = amitools_reference.open_volume(self.path)
                try:
                    self.assertEqual(amitools_reference.tree(ami), expected)
                    self.assertEqual(amitools_reference.structure_errors(blkdev), [])
                    self.assertEqual(ami.root.fstype, int.from_bytes(dos_type, "big"))
                    self.assertEqual(ami.root.blocks_used, ami.bitmap.get_num_used())
                finally:
                    blkdev.close()

    def test_amiganut_reads_what_amitools_wrote(self) -> None:
        from amitools.fs.DosType import DOS7

        fs = amitools_reference.fs_string
        with amitools_reference.long_name_writer_fixed():
            ami, blkdev = amitools_reference.create_volume(self.path, DOS7)
            folder = long_name(70, "Folder")
            ami.create_dir(fs(folder))
            payloads = {}
            for length in (31, 50, 77, 107):
                name = f"{folder}/{long_name(length)}"
                data = os.urandom(length * 41)
                ami.write_file(data, fs(name))
                payloads[name] = data
            comments = {
                f"{folder}/{long_name(31)}": "fits inline",
                f"{folder}/{long_name(107)}": "far too long to sit beside a one hundred and seven character name",
            }
            for path, comment in comments.items():
                ami.get_path_name(fs(path)).change_comment(fs(comment))
            ami.close()
            blkdev.close()

        volume = AmigaDOSVolume(BlockReader(self.path, writable=True))
        self.assertEqual(volume.format, "FFS-LNFS")
        for path, data in payloads.items():
            self.assertEqual(volume.read_bytes(path), data)
        for path, comment in comments.items():
            self.assertEqual(volume.comment(path), comment)
        self.assertEqual(volume.validate(), [])

        # Now change it here and hand it back.
        volume.remove(f"{folder}/{long_name(50)}")
        volume.rename(f"{folder}/{long_name(107)}", long_name(99, "Moved"))
        volume.write_bytes(long_name(100, "Added"), b"added", AmigaMeta(protection=0, comment="x" * 60, datestamp=None))
        self.assertEqual(volume.validate(), [])
        volume.close()

        ami, blkdev = amitools_reference.open_volume(self.path)
        try:
            found = amitools_reference.tree(ami)
            self.assertNotIn(f"{folder}/{long_name(50)}", found)
            self.assertEqual(found[long_name(99, "Moved")][2], comments[f"{folder}/{long_name(107)}"])
            self.assertEqual(found[long_name(100, "Added")][2:], ("x" * 60, b"added"))
            self.assertEqual(amitools_reference.structure_errors(blkdev), [])
        finally:
            blkdev.close()


if __name__ == "__main__":
    unittest.main()
