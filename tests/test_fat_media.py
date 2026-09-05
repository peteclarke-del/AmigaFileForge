from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path

from app.fat_media import build_hdf_card, read_hdf_card


class FatMediaTests(unittest.TestCase):
    def test_builds_a_deterministic_fat32_card_with_contiguous_amiga_hdf(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "games.hdf"
            payload = bytes(range(256)) * 1200
            source.write_bytes(payload)
            card = root / "card.img"
            layout = build_hdf_card(source, card)
            data = card.read_bytes()
            extracted = read_hdf_card(card, layout)
            card_size = card.stat().st_size

        self.assertEqual(data[510:512], b"\x55\xAA")
        self.assertEqual(data[82:90], b"FAT32   ")
        reserved = struct.unpack_from("<H", data, 14)[0]
        fats = data[16]
        fat_sectors = struct.unpack_from("<I", data, 36)[0]
        root_entries = struct.unpack_from("<H", data, 17)[0]
        root_offset = (reserved + fats * fat_sectors) * 512
        self.assertEqual(data[root_offset:root_offset + 11], b"AMIGA FORGE")
        self.assertEqual(data[root_offset + 32:root_offset + 43], b"AMIGA   HDF")
        self.assertEqual(struct.unpack_from("<I", data, root_offset + 32 + 28)[0], len(payload))
        self.assertEqual(extracted, payload)
        self.assertEqual(card_size, layout.image_size)
        data_sectors = card_size // 512 - reserved - fats * fat_sectors - root_entries * 32 // 512
        self.assertGreaterEqual(
            data_sectors // data[13], 65525,
            "Even a small source must remain an unambiguous FAT32 volume",
        )
        self.assertEqual(struct.unpack_from("<I", data, 44)[0], 2)
        self.assertEqual(data[512:516], b"RRaA")


if __name__ == "__main__":
    unittest.main()
