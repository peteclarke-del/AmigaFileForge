"""The DMS decoders are pinned to the reference implementation's output.

``dms_reference_vectors.json`` was produced by running the public-domain
xDMS 1.3 decoders over fixed inputs and recording exactly what they returned.
Each vector decodes a run of tracks in one session, so the shared dictionary
that carries from one track to the next is covered as well as the individual
modes. If a change to the port alters a single byte, these tests fail.
"""

from __future__ import annotations

import base64
import json
import unittest
from pathlib import Path

from app.dms_codec import DMSCodecError, DMSDecoder, unpack_rle

VECTORS = json.loads(
    (Path(__file__).with_name("dms_reference_vectors.json")).read_text(encoding="utf-8")
)

MODE_NAMES = {2: "QUICK", 3: "MEDIUM", 4: "DEEP", 5: "HEAVY"}


class DMSCodecReferenceTests(unittest.TestCase):
    def test_every_mode_matches_the_reference_decoder(self) -> None:
        self.assertTrue(VECTORS, "the reference vectors are missing")
        seen = set()
        for index, vector in enumerate(VECTORS):
            mode = int(vector["mode"])
            flags = int(vector["flags"])
            size = int(vector["size"])
            seen.add(mode)
            with self.subTest(vector=index, mode=MODE_NAMES.get(mode, mode)):
                decoder = DMSDecoder()
                produced = b""
                for packed in vector["tracks"]:
                    data = base64.b64decode(packed)
                    if mode == 2:
                        produced += decoder._quick(data, size)
                    elif mode == 3:
                        produced += decoder._medium(data, size)
                    elif mode == 4:
                        produced += decoder._deep(data, size)
                    else:
                        produced += decoder._heavy(data, flags, size)
                    if not flags & 1:
                        decoder.reset()
                self.assertEqual(produced, base64.b64decode(vector["expected"]))
        self.assertEqual(seen, {2, 3, 4, 5}, "every LZ mode must be covered")

    def test_the_dictionary_carries_from_one_track_to_the_next(self) -> None:
        """State survives a track, and only a cleared flag bit resets it."""
        vector = next(item for item in VECTORS if item["mode"] == 3)
        tracks = [base64.b64decode(item) for item in vector["tracks"]]
        size = int(vector["size"])

        decoder = DMSDecoder()
        start = decoder.medium_text_loc
        decoder._medium(tracks[0], size)
        after_first = decoder.medium_text_loc
        self.assertNotEqual(after_first, start)
        self.assertNotEqual(bytes(decoder.text), bytes(DMSDecoder().text))

        decoder._medium(tracks[1], size)
        self.assertNotEqual(decoder.medium_text_loc, after_first)

        # A track whose flags clear bit 0 puts every decoder back to its
        # starting state, which is what a new disk in the archive needs.
        decoder.unpack_track(b"\x00" * 8, 0, 8, 8, 0)
        self.assertEqual(decoder.medium_text_loc, start)
        self.assertEqual(bytes(decoder.text), bytes(DMSDecoder().text))


class DMSRunLengthTests(unittest.TestCase):
    def test_a_run_repeats_the_byte_the_stream_supplies(self) -> None:
        self.assertEqual(unpack_rle(bytes([0x41, 0x90, 0x03, 0x42]), 4), b"ABBB")

    def test_an_escaped_ninety_is_a_literal(self) -> None:
        self.assertEqual(unpack_rle(bytes([0x90, 0x00, 0x41]), 2), b"\x90A")

    def test_a_long_run_reads_a_sixteen_bit_count(self) -> None:
        packed = bytes([0x90, 0xFF, 0x5A]) + (400).to_bytes(2, "big")
        self.assertEqual(unpack_rle(packed, 400), b"\x5A" * 400)

    def test_a_run_that_would_overflow_the_track_is_refused(self) -> None:
        packed = bytes([0x90, 0x10, 0x41])
        with self.assertRaisesRegex(DMSCodecError, "overflows"):
            unpack_rle(packed, 4)


class DMSHeavyGuardTests(unittest.TestCase):
    def test_an_impossible_tree_size_is_refused_rather_than_read_past(self) -> None:
        # Nine set bits declare 511 code lengths; the format defines 510.
        with self.assertRaisesRegex(DMSCodecError, "more than the 510"):
            DMSDecoder()._heavy(b"\xff\xff" + bytes(64), 2, 16)


if __name__ == "__main__":
    unittest.main()
