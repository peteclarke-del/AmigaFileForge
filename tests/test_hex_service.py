from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from app.disk_service import DiskError, DiskService, ImageSession
from app.hex_service import compare_data, compare_paths, raw_image_range, search_raw_image, write_raw_image


class HexServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.service = DiskService(self.root / "work")
        self.path = self.root / "image.adf"
        self.path.write_bytes(bytes(range(256)) + b"Disc catalogue" + bytes(range(256)))
        self.session = ImageSession("a" * 32, self.path.name, "ofs", self.path)

    def tearDown(self):
        self.temporary.cleanup()

    def test_reads_a_bounded_range_without_loading_the_complete_image(self):
        result = raw_image_range(self.session, 250, 16)

        self.assertEqual(result["offset"], 250)
        self.assertEqual(result["length"], 16)
        self.assertEqual(bytes.fromhex(result["data"]), self.path.read_bytes()[250:266])
        self.assertEqual(result["size"], self.path.stat().st_size)

    def test_searches_hex_and_text_in_both_directions(self):
        text = search_raw_image(self.session, "Disc", "text", 0, "forward", False)
        backward = search_raw_image(
            self.session, "44 69 73 63", "hex", self.path.stat().st_size - 1, "backward", False
        )

        self.assertEqual(text["offset"], 256)
        self.assertEqual(backward["offset"], 256)

        wrapped = search_raw_image(self.session, "Disc", "text", -1, "backward", True)
        self.assertEqual(wrapped["offset"], 256)
        self.assertTrue(wrapped["wrapped"])
        wrapped_forward = search_raw_image(self.session, "Disc catalogue", "text", 300, "forward", True)
        self.assertEqual(wrapped_forward["offset"], 256)
        self.assertTrue(wrapped_forward["wrapped"])

    def test_raw_write_requires_confirmation_and_current_version(self):
        version = raw_image_range(self.session, 0, 16)["version"]

        with self.assertRaisesRegex(DiskError, "confirmation"):
            write_raw_image(
                self.service, self.session, version, [{"offset": 4, "data": "AABB"}], False
            )
        with self.assertRaisesRegex(DiskError, "changed after"):
            write_raw_image(
                self.service, self.session, "stale", [{"offset": 4, "data": "AABB"}], True
            )

    def test_raw_write_is_fixed_size_and_invalidates_derived_state(self):
        version = raw_image_range(self.session, 0, 16)["version"]
        self.session.content_kind_cache[("-", "Game", 4, 0, 0, "")] = "basic"
        self.session.partition = 1

        result = write_raw_image(
            self.service,
            self.session,
            version,
            [{"offset": 4, "data": "AABB"}, {"offset": 10, "data": "CC"}],
            True,
        )

        self.assertEqual(result["written"], 3)
        self.assertEqual(self.path.read_bytes()[4:6], b"\xAA\xBB")
        self.assertEqual(self.path.stat().st_size, 256 + len(b"Disc catalogue") + 256)
        self.assertEqual(self.session.content_kind_cache, {})
        self.assertTrue(self.session.dirty)

    def test_raw_write_rejects_overlapping_or_extending_changes(self):
        version = raw_image_range(self.session, 0, 16)["version"]
        with self.assertRaisesRegex(DiskError, "overlap"):
            write_raw_image(
                self.service,
                self.session,
                version,
                [{"offset": 4, "data": "AABB"}, {"offset": 5, "data": "CC"}],
                True,
            )
        with self.assertRaisesRegex(DiskError, "boundary"):
            write_raw_image(
                self.service,
                self.session,
                version,
                [{"offset": self.path.stat().st_size, "data": "00"}],
                True,
            )

    def test_binary_comparison_reports_ranges_offsets_and_size(self):
        source = b"abcdefghi"
        candidate = b"abXXefgYY-extra"

        report = compare_data(source, BytesIO(candidate), len(candidate))

        self.assertEqual(report["count"], 10)
        self.assertEqual(report["differences"], [2, 3, 7, 8])
        self.assertEqual(report["ranges"], [[2, 3], [7, 8], [9, 14]])
        self.assertEqual(report["sourceSize"], len(source))
        self.assertEqual(report["candidateSize"], len(candidate))

    def test_path_comparison_streams_progress_and_skips_equal_chunks(self):
        candidate = self.root / "candidate.adf"
        source = b"A" * (1024 * 1024) + b"B"
        candidate.write_bytes(source[:-1] + b"C")
        self.path.write_bytes(source)
        updates = []

        report = compare_paths(
            self.path, candidate, lambda current, total: updates.append((current, total))
        )

        self.assertEqual(report["count"], 1)
        self.assertEqual(report["ranges"], [[len(source) - 1, len(source) - 1]])
        self.assertEqual(updates[-1], (len(source), len(source)))


if __name__ == "__main__":
    unittest.main()
