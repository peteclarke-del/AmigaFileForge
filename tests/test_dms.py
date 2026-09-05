from __future__ import annotations

import struct
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.disk_service import DiskService
from app.dms_codec import DMSDecoder
from app.dms import (
    COMPRESSION_MODES,
    HEADER_SIZE,
    MAGIC,
    TRACK_SIZE,
    DMSError,
    basic_unopened_channel_io,
    crc16,
    dms_editability,
    dms_project,
    is_tokenized_basic,
    parse_dms,
    replace_dms_file,
    rewrite_basic_loader,
    to_adf,
    unpack_rle,
)

ROOT = Path(__file__).resolve().parents[1]


def sample_dms(fixture_name: str) -> bytes:
    fixture = ROOT / "samples" / fixture_name
    if not fixture.is_file():
        raise unittest.SkipTest(f"Optional DMS fixture is not bundled: {fixture_name}")
    if fixture.suffix.casefold() == ".dms":
        return fixture.read_bytes()
    with zipfile.ZipFile(fixture) as archive:
        member = next(name for name in archive.namelist() if name.lower().endswith(".dms"))
        return archive.read(member)


def build_track(number: int, data: bytes, *, mode: int = 0, flags: int = 0) -> bytes:
    header = bytearray(20)
    header[0:2] = b"TR"
    struct.pack_into(
        ">HHHHHBBHH", header, 2,
        number, 0, len(data), 0, len(data), flags, mode, crc16(data), crc16(data),
    )
    struct.pack_into(">H", header, 18, crc16(bytes(header[:18])))
    return bytes(header) + data


def build_archive(tracks: list[bytes], *, disk_type: int = 2, compression: int = 0) -> bytes:
    header = bytearray(HEADER_SIZE)
    header[0:4] = MAGIC
    struct.pack_into(
        ">IIIHHIIHHHHHHHIHHHHH", header, 4,
        0, 0, 0,
        0, max(1, len(tracks)) - 1,
        sum(len(track) for track in tracks),
        TRACK_SIZE * len(tracks),
        39, 106, 0, 0, 500, 0, 0, 0,
        0x0207, 0x0100, disk_type, compression, 0,
    )
    return bytes(header) + b"".join(tracks)


class DMSFormatTests(unittest.TestCase):
    def test_bytes_without_the_signature_are_refused(self):
        with self.assertRaisesRegex(DMSError, "DMS! signature"):
            parse_dms(b"NOT A DMS ARCHIVE" + bytes(HEADER_SIZE))

    def test_the_archive_header_is_decoded(self):
        archive = build_archive([build_track(0, b"A" * TRACK_SIZE)])
        contents = parse_dms(archive)
        self.assertEqual(contents.version, "2.07")
        self.assertEqual(contents.info["diskType"], "AmigaDOS FFS")
        self.assertEqual(contents.info["requiredVersion"], "1.00")

    def test_every_track_is_listed_with_its_mode_and_checksums(self):
        archive = build_archive(
            [build_track(0, b"A" * TRACK_SIZE), build_track(1, b"B" * TRACK_SIZE)]
        )
        contents = parse_dms(archive)
        self.assertEqual([track.name for track in contents.files], ["Track 000", "Track 001"])
        self.assertTrue(all(track.complete for track in contents.files))
        self.assertTrue(all(track.crc_ok for track in contents.files))
        self.assertEqual({track.mode for track in contents.files}, {"NOCOMP"})
        self.assertEqual(contents.chunk_counts, {0: 2})

    def test_a_bad_packed_checksum_is_reported_rather_than_decoded(self):
        track = bytearray(build_track(0, b"A" * TRACK_SIZE))
        track[25] ^= 0xFF
        contents = parse_dms(build_archive([bytes(track)]))
        self.assertFalse(contents.files[0].complete)
        self.assertTrue(
            any("packed-data checksum" in warning for warning in contents.warnings),
            contents.warnings,
        )

    def test_a_truncated_track_is_detected(self):
        archive = build_archive([build_track(0, b"A" * TRACK_SIZE)])[:-100]
        contents = parse_dms(archive)
        self.assertTrue(
            any("only" in warning for warning in contents.warnings), contents.warnings
        )

    def test_an_undecodable_compression_mode_is_named_not_guessed(self):
        archive = build_archive([build_track(0, b"A" * 100, mode=5)])
        contents = parse_dms(archive)
        self.assertEqual(contents.files[0].mode, "HEAVY1")
        self.assertFalse(contents.files[0].complete)
        self.assertTrue(
            any("HEAVY1" in warning for warning in contents.warnings), contents.warnings
        )

    def test_every_compression_mode_has_a_name(self):
        self.assertEqual(
            set(COMPRESSION_MODES.values()),
            {"NOCOMP", "SIMPLE", "QUICK", "MEDIUM", "DEEP", "HEAVY1", "HEAVY2"},
        )


class DMSUnpackerTests(unittest.TestCase):
    def test_the_run_length_pass_repeats_the_byte_the_stream_names(self):
        """``90 <count> <value>`` repeats that value, not whatever preceded it."""
        self.assertEqual(unpack_rle(bytes([65, 0x90, 5, 66]), 6), b"ABBBBB")
        self.assertEqual(unpack_rle(bytes([0x90, 0x00]), 1), b"\x90")

    def test_a_long_run_uses_its_sixteen_bit_count(self):
        packed = bytes([65, 0x90, 0xFF, 66]) + (300).to_bytes(2, "big")
        self.assertEqual(unpack_rle(packed, 301), b"A" + b"B" * 300)

    def test_a_truncated_run_length_escape_is_refused(self):
        with self.assertRaisesRegex(DMSError, "truncated|past the end|middle of a run"):
            unpack_rle(bytes([65, 0x90]), 10)

    def test_quick_mode_round_trips_a_literal_run(self):
        """Every leading 1 bit is a literal byte, so a literal stream decodes."""
        bits = "".join("1" + format(value, "08b") for value in b"AMIGA")
        packed = bytes(
            int(bits[offset : offset + 8].ljust(8, "0"), 2)
            for offset in range(0, len(bits), 8)
        )
        decoder = DMSDecoder()
        self.assertEqual(decoder.unpack_track(packed, 2, 5, 5, 1), b"AMIGA")


class DMSRebuildTests(unittest.TestCase):
    def test_a_complete_archive_rebuilds_as_an_adf(self):
        archive = build_archive(
            [build_track(0, b"A" * TRACK_SIZE), build_track(1, b"B" * TRACK_SIZE)]
        )
        image = to_adf(archive)
        self.assertEqual(len(image), TRACK_SIZE * 2)
        self.assertEqual(image[:TRACK_SIZE], b"A" * TRACK_SIZE)
        self.assertEqual(image[TRACK_SIZE:], b"B" * TRACK_SIZE)

    def test_omitted_tracks_are_zeroed_at_their_declared_positions(self):
        """DiskMasher omits empty tracks, so the gaps must be filled, not closed."""
        archive = build_archive(
            [build_track(0, b"A" * TRACK_SIZE), build_track(3, b"D" * TRACK_SIZE)]
        )
        image = to_adf(archive)
        self.assertEqual(len(image), TRACK_SIZE * 4)
        self.assertEqual(image[TRACK_SIZE : TRACK_SIZE * 3], bytes(TRACK_SIZE * 2))
        self.assertEqual(image[TRACK_SIZE * 3 :], b"D" * TRACK_SIZE)

    def test_an_archive_with_an_undecodable_track_refuses_to_rebuild(self):
        archive = build_archive(
            [build_track(0, b"A" * TRACK_SIZE), build_track(1, b"x" * 100, mode=6)]
        )
        with self.assertRaisesRegex(DMSError, "could not be unpacked"):
            to_adf(archive)


class DMSProjectTests(unittest.TestCase):
    def test_the_project_view_inventories_the_header_and_every_track(self):
        archive = build_archive(
            [build_track(0, b"A" * TRACK_SIZE), build_track(1, b"B" * TRACK_SIZE)]
        )
        project = dms_project(archive)
        self.assertEqual(project["diskType"], "AmigaDOS FFS")
        self.assertEqual(project["modes"], {"NOCOMP": 2})
        self.assertEqual(len(project["checksum"]), 64)
        self.assertEqual([track["number"] for track in project["tracks"]], [0, 1])
        self.assertTrue(all(track["checksumValid"] for track in project["tracks"]))

    def test_an_uncompressed_track_can_be_replaced_at_the_same_length(self):
        archive = build_archive([build_track(0, b"A" * TRACK_SIZE)])
        self.assertTrue(dms_editability(archive, 0)["editable"])
        rebuilt, report = replace_dms_file(archive, 0, b"B" * TRACK_SIZE)
        self.assertEqual(report["length"], TRACK_SIZE)
        self.assertEqual(parse_dms(rebuilt).files[0].data, b"B" * TRACK_SIZE)
        self.assertTrue(parse_dms(rebuilt).files[0].crc_ok)

    def test_a_different_length_replacement_is_refused(self):
        archive = build_archive([build_track(0, b"A" * TRACK_SIZE)])
        with self.assertRaisesRegex(DMSError, "exactly the same length"):
            replace_dms_file(archive, 0, b"B" * (TRACK_SIZE - 1))

    def test_a_compressed_track_cannot_be_replaced(self):
        archive = build_archive([build_track(0, b"A" * 100, mode=2)])
        editability = dms_editability(archive, 0)
        self.assertFalse(editability["editable"])
        self.assertTrue(editability["reasons"])


class BasicLoaderTests(unittest.TestCase):
    def _program(self, source: str) -> bytes:
        from amiganut.basic import tokenise

        return tokenise(source)

    def test_a_tokenised_program_is_recognised(self):
        self.assertTrue(is_tokenized_basic(self._program('10 PRINT "HI"')))
        self.assertFalse(is_tokenized_basic(b"plain text"))

    def test_file_input_without_an_open_is_detected(self):
        """A converted loader that assumes an open channel fails on the disk."""
        program = self._program('10 INPUT#1,A$\n20 PRINT A$')
        self.assertTrue(basic_unopened_channel_io(program))

    def test_an_explicit_open_is_not_misreported(self):
        program = self._program('10 OPEN "Data" FOR INPUT AS 1\n20 INPUT#1,A$')
        self.assertFalse(basic_unopened_channel_io(program))

    def test_a_floppy_device_prefix_is_removed_for_a_hard_drive_install(self):
        program = self._program('10 CHAIN "DF0:Game"')
        rewritten, changes = rewrite_basic_loader(program, "Game", {})
        self.assertNotEqual(rewritten, program)
        self.assertTrue(any("DF0:" in change for change in changes), changes)
        from amiganut.basic import detokenise

        self.assertNotIn("DF0:", detokenise(rewritten))

    def test_a_renamed_file_reference_is_updated_and_reported(self):
        program = self._program('10 CHAIN "OldName"')
        rewritten, changes = rewrite_basic_loader(program, "NewName", {"OldName": "NewName"})
        from amiganut.basic import detokenise

        self.assertIn("NewName", detokenise(rewritten))
        self.assertTrue(any("OldName" in change for change in changes), changes)

    def test_a_loader_that_needs_no_change_is_returned_unchanged(self):
        program = self._program('10 CHAIN "Game"')
        rewritten, _changes = rewrite_basic_loader(program, "Game", {})
        self.assertEqual(rewritten, program)


class DMSSessionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.service = DiskService(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_an_opened_archive_lists_its_tracks_through_the_pane(self):
        archive = build_archive(
            [build_track(0, b"A" * TRACK_SIZE), build_track(1, b"B" * TRACK_SIZE)]
        )
        path = Path(self.temporary.name) / "game.dms"
        path.write_bytes(archive)
        session = self.service.create_from_path(path)
        self.assertEqual(session.kind, "dms")
        rows = self.service.list_directory(session, "", None)["entries"]
        self.assertEqual([row["name"] for row in rows], ["Track 000", "Track 001"])
        self.assertIn("Valid DMS", self.service.validate(session))


if __name__ == "__main__":
    unittest.main()
