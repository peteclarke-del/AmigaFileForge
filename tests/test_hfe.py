from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.disk_service import DiskError, DiskService, ImageSession
from app.hfe import HFEError, parse_hfe_header


def header(signature: bytes = b"HXCPICFE", revision: int = 0) -> bytes:
    data = bytearray(512)
    data[:8] = signature
    data[8] = revision
    data[9] = 80
    data[10] = 2
    data[11] = 2
    data[12:14] = (250).to_bytes(2, "little")
    return bytes(data)


class HFETests(unittest.TestCase):
    def test_v1_header_geometry_is_parsed(self) -> None:
        parsed = parse_hfe_header(header())
        self.assertEqual((parsed.version, parsed.tracks, parsed.sides), ("v1", 80, 2))
        self.assertFalse(parsed.advanced)

    def test_v2_and_v3_are_advanced(self) -> None:
        self.assertTrue(parse_hfe_header(header(revision=1)).advanced)
        self.assertEqual(parse_hfe_header(header(b"HXCHFEV3")).version, "v3")

    def test_invalid_signature_is_rejected(self) -> None:
        with self.assertRaisesRegex(HFEError, "valid HFE signature"):
            parse_hfe_header(header(b"NOTANHFE"))

    def test_hfe_extension_uses_container_decoder(self) -> None:
        self.assertEqual(DiskService.detect_kind("disk.hfe"), "hfe")

    def test_decoded_non_amiga_hfe_reports_container_and_filesystem_distinction(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / "foreign.hfe"
            original.write_bytes(header())
            service = DiskService(Path(folder) / "work")

            def convert(arguments):
                output = next(
                    (item.removeprefix("-foutput:") for item in arguments if item.startswith("-foutput:")),
                    None,
                )
                if output:
                    Path(output).write_bytes(bytes(901_120))
                return "Number of bad sectors : 0"

            with (
                patch.object(service, "_run_hxcfe", side_effect=convert),
                patch.object(
                    service,
                    "identify_kind",
                    side_effect=DiskError("No supported Amiga filesystem was found."),
                ),
            ):
                with self.assertRaisesRegex(
                    DiskError,
                    "container is valid, but its contents cannot be browsed",
                ):
                    service._open_hfe(original)

    def test_advanced_hfe_working_copy_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "decoded.adf"
            image.write_bytes(b"")
            session = ImageSession(
                "a" * 32,
                "protected.hfe",
                "ofs",
                image,
                hfe_original_path=Path(folder) / "protected.hfe",
                hfe_version="v3",
                hfe_read_only=True,
            )
            with self.assertRaisesRegex(DiskError, "cannot be rewritten safely"):
                DiskService.require_writable_geometry(session)

    @unittest.skipIf(
        shutil.which("hxcfe") is None,
        "HxCFE is installed in the application container",
    )
    def test_every_creatable_hfe_filesystem_opens_and_browses(self) -> None:
        expected = {
            "hfe-adf": ("ofs", 901_120),
            "hfe-ffs": ("ffs", 901_120),
            "hfe-ffs-intl": ("ffs", 901_120),
            "hfe-adf-hd": ("ofs", 1_802_240),
            "hfe-ffs-hd": ("ffs", 1_802_240),
        }
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(Path(folder) / "work")
            for format_name, (kind, decoded_size) in expected.items():
                with self.subTest(format=format_name):
                    created = service.create_blank(format_name, "Wrapped")
                    source_hfe = created.hfe_original_path
                    if format_name == "hfe-adf":
                        payload = Path(folder) / "hfe-test-file"
                        payload.write_bytes(b"Browseable HFE content\n")
                        service.put(created, "Test", payload)
                        source_hfe = service.prepare_download(created)
                    with source_hfe.open("rb") as image:
                        reopened = service.create_from_stream(created.name, image)
                    self.assertEqual(reopened.kind, kind)
                    self.assertEqual(reopened.path.stat().st_size, decoded_size)
                    listing = service.browse_directory(reopened, "", None)
                    self.assertEqual(
                        [entry["name"] for entry in listing["entries"]],
                        ["Test"] if format_name == "hfe-adf" else [],
                    )


if __name__ == "__main__":
    unittest.main()
