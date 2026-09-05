from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.rom_components import write_combined_rom


class ImageOpeningTests(unittest.TestCase):
    def test_empty_native_rom_component_set_is_rejected(self):
        with TemporaryDirectory() as temporary:
            output = Path(temporary) / "combined.rom"

            with self.assertRaisesRegex(ValueError, "at least one"):
                write_combined_rom([], output)

    def test_unknown_native_rom_layout_is_rejected_before_writing(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            component = root / "component.rom"
            component.write_bytes(b"data")
            output = root / "combined.rom"

            with self.assertRaisesRegex(ValueError, "linear, two-chip or four-chip"):
                write_combined_rom([component], output, "byte-interleaved-many")

            self.assertFalse(output.exists())

    def test_native_rom_components_use_the_reviewed_interleaving_plan(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = root / "rom0.bin"
            second = root / "rom1.bin"
            first.write_bytes(b"ac")
            second.write_bytes(b"bd")
            output = root / "combined.rom"
            write_combined_rom([first, second], output, "byte-interleaved-2")

            self.assertEqual(output.read_bytes(), b"abcd")

    def test_native_rom_layout_component_count_must_match(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            components = []
            for index in range(4):
                component = root / f"rom{index}.bin"
                component.write_bytes(bytes([index]))
                components.append(component)

            with self.assertRaisesRegex(ValueError, "requires exactly 2"):
                write_combined_rom(
                    components,
                    root / "combined.rom",
                    "byte-interleaved-2",
                )

            self.assertFalse((root / "combined.rom").exists())


if __name__ == "__main__":
    unittest.main()


class CompressedImageTests(unittest.TestCase):
    """An ADZ is a gzipped ADF, so the sectors inside are what is opened."""

    def _blank_adf(self, folder: Path) -> bytes:
        from amiganut.filesystem import format_volume
        from amiganut.filesystem.blocks import BlockReader

        image = folder / "source.adf"
        image.write_bytes(bytes(901_120))
        reader = BlockReader(image, writable=True)
        try:
            volume = format_volume(reader, label="Compressed", dos_type=b"DOS\x01")
            volume.write_bytes("Read.Me", b"Hello from a compressed disk.\n")
            volume.flush()
        finally:
            reader.close()
        return image.read_bytes()

    def test_a_gzipped_adf_opens_as_the_disk_inside_it(self):
        import gzip
        import io

        from app.disk_service import DiskService

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            sectors = self._blank_adf(root)
            service = DiskService(root / "work")
            session = service.create_from_stream(
                "Compressed.adz", io.BytesIO(gzip.compress(sectors, mtime=0))
            )
            self.assertIn(session.kind, {"ofs", "ffs"})
            self.assertEqual(session.path.stat().st_size, 901_120)
            self.assertEqual(session.path.read_bytes(), sectors)

    def test_a_truncated_gzip_image_is_refused_with_an_explanation(self):
        import gzip
        import io

        from app.disk_service import DiskService
        from app.errors import DiskError

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            sectors = self._blank_adf(root)
            truncated = gzip.compress(sectors, mtime=0)[: 1024]
            service = DiskService(root / "work")
            with self.assertRaisesRegex(DiskError, "truncated or damaged"):
                service.create_from_stream("Broken.adz", io.BytesIO(truncated))

    def test_an_uncompressed_adf_named_adz_is_left_exactly_as_it_is(self):
        import io

        from app.disk_service import DiskService

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            sectors = self._blank_adf(root)
            service = DiskService(root / "work")
            session = service.create_from_stream("Plain.adz", io.BytesIO(sectors))
            self.assertEqual(session.path.read_bytes(), sectors)

    def test_an_adz_export_expands_back_to_the_same_sectors(self):
        import gzip
        import io

        from app.disk_service import DiskService

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            sectors = self._blank_adf(root)
            service = DiskService(root / "work")
            session = service.create_from_stream("Disk.adf", io.BytesIO(sectors))
            output, name = service.export_image(session, "adz")
            self.assertTrue(name.endswith(".adz"))
            self.assertEqual(gzip.decompress(output.read_bytes()), sectors)
