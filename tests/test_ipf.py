"""The IPF path: how it behaves with and without the decoder library.

The library itself is not ours to ship, so these tests cover the two halves
the workbench owns: finding the library (or saying plainly that it is not
here), and turning the MFM bit cells it returns into AmigaDOS sectors. The
second half is checked by encoding a track the way an Amiga writes one and
requiring the decoder to hand back exactly what went in.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import ipf


def encode_mfm(data: bytes) -> bytes:
    """Split a value into the odd and even bit planes Amiga MFM stores.

    Clock bits are left at zero. A real drive needs them, but every decoder
    masks them away, so a track built this way decodes exactly as a written
    one does.
    """
    odd = bytes((value >> 1) & 0x55 for value in data)
    even = bytes(value & 0x55 for value in data)
    return odd + even


def checksum(encoded: bytes) -> int:
    total = 0
    for offset in range(0, len(encoded), 4):
        total ^= int.from_bytes(encoded[offset : offset + 4], "big")
    return total & 0x55555555


def build_sector(track: int, sector: int, payload: bytes, remaining: int) -> bytes:
    """Assemble one sector exactly as AmigaDOS lays it out on the disk."""
    info = bytes([0xFF, track, sector, remaining])
    label = bytes(16)
    header = encode_mfm(info) + encode_mfm(label)
    data = encode_mfm(payload)
    header_checksum = encode_mfm(checksum(header).to_bytes(4, "big"))
    data_checksum = encode_mfm(checksum(data).to_bytes(4, "big"))
    return b"\x44\x89\x44\x89" + header + header_checksum + data_checksum + data


def build_track(track: int, sectors: int = 11) -> tuple[bytes, list[bytes]]:
    payloads = [
        bytes(((sector * 7 + offset) & 0xFF) for offset in range(512))
        for sector in range(sectors)
    ]
    raw = b""
    for sector in range(sectors):
        raw += build_sector(track, sector, payloads[sector], sectors - sector)
        raw += bytes(32)  # the gap a real track leaves between sectors
    return raw, payloads


class IPFAvailabilityTests(unittest.TestCase):
    def test_a_missing_library_is_explained_rather_than_guessed_at(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.object(
            ipf, "_search_directories", return_value=[Path(folder)]
        ), patch.dict("os.environ", {ipf.ENVIRONMENT_VARIABLE: ""}, clear=False):
            self.assertIsNone(ipf.library_path())
            self.assertFalse(ipf.available())
            message = ipf.unavailable_message()
            self.assertIn("SPS decoder library", message)
            self.assertIn(ipf.ENVIRONMENT_VARIABLE, message)

    def test_the_environment_variable_names_one_exact_file(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            library = Path(folder) / "libcapsimage.so.5.1"
            library.write_bytes(b"not really a library")
            with patch.dict("os.environ", {ipf.ENVIRONMENT_VARIABLE: str(library)}):
                self.assertEqual(ipf.library_path(), library)
            with patch.dict("os.environ", {ipf.ENVIRONMENT_VARIABLE: str(library) + "-gone"}):
                self.assertIsNone(ipf.library_path())

    def test_reading_without_the_library_raises_the_explanation(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch.object(
            ipf, "_search_directories", return_value=[Path(folder)]
        ), patch.dict("os.environ", {ipf.ENVIRONMENT_VARIABLE: ""}, clear=False):
            with self.assertRaisesRegex(ipf.IPFError, "SPS decoder library"):
                ipf.read_ipf(Path(folder) / "capture.ipf")


class AmigaMfmDecodeTests(unittest.TestCase):
    def test_a_written_track_decodes_back_to_its_sectors(self) -> None:
        raw, payloads = build_track(track=3)
        sectors, warnings = ipf._decode_track(raw, 11)
        self.assertEqual(warnings, [])
        self.assertEqual(sorted(sectors), list(range(11)))
        for number, payload in enumerate(payloads):
            self.assertEqual(sectors[number], payload, f"sector {number}")

    def test_a_sector_with_a_damaged_header_is_reported_not_used(self) -> None:
        raw, _payloads = build_track(track=0)
        damaged = bytearray(raw)
        damaged[8] ^= 0x54  # a data bit inside the first sector's label
        sectors, warnings = ipf._decode_track(bytes(damaged), 11)
        self.assertNotIn(0, sectors)
        self.assertTrue(any("checksum" in warning for warning in warnings), warnings)

    def test_a_sector_with_damaged_data_is_reported_not_used(self) -> None:
        raw, _payloads = build_track(track=0)
        damaged = bytearray(raw)
        damaged[4 + 56 + 10] ^= 0x54  # a data bit inside the first sector's payload
        sectors, warnings = ipf._decode_track(bytes(damaged), 11)
        self.assertNotIn(0, sectors)
        self.assertTrue(any("data failed" in warning for warning in warnings), warnings)

    def test_a_sector_that_straddles_the_index_is_still_found(self) -> None:
        """A track is a ring, so the last sector may wrap past its start."""
        raw, payloads = build_track(track=1)
        rotated = raw[600:] + raw[:600]
        sectors, _warnings = ipf._decode_track(rotated, 11)
        self.assertEqual(sorted(sectors), list(range(11)))
        self.assertEqual(sectors[0], payloads[0])


if __name__ == "__main__":
    unittest.main()
