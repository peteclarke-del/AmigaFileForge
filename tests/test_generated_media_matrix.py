from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.disk_service import DiskService
from tests.generated_media import add_test_file, generated_media_matrix

FIXTURE_PAYLOAD = b"Amiga File Forge generated fixture\n"


class GeneratedMediaMatrixTests(unittest.TestCase):
    def test_every_core_format_is_generated_and_reopened_without_private_samples(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = DiskService(root / "work")
            media = generated_media_matrix(service)

            self.assertEqual(
                {item.format for item in media},
                {
                    "adf", "adf-intl", "adf-dc",
                    "ffs", "ffs-intl", "ffs-dc",
                    "adf-hd", "ffs-hd", "ffs-hd-dc",
                    "hardfile", "ffs-hard", "rom", "kickfs", "dms",
                },
            )
            for item in media:
                self.assertTrue(item.session.path.is_file(), item.format)
                self.assertGreater(item.session.path.stat().st_size, 0, item.format)
                reopened = DiskService(root / "work").get(item.session.id)
                summary = service.summary(reopened)
                self.assertEqual(summary["id"], item.session.id)
                if reopened.kind == "hdf":
                    # A drive describes itself, so it must declare at least
                    # the partition it was created with.
                    self.assertGreaterEqual(
                        len(service.list_partitions(reopened)), 1, item.format
                    )
                else:
                    listing = service.browse_directory(reopened, "", None)
                    self.assertIn("entries", listing, item.format)

    def test_generated_writable_filesystems_accept_and_return_known_content(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = DiskService(root / "work")
            for item in generated_media_matrix(service):
                if item.session.kind not in {"ofs", "ffs"}:
                    continue
                add_test_file(service, item.session, root, path="Test")
                self.assertEqual(
                    service.read_file(item.session, "Test"),
                    FIXTURE_PAYLOAD,
                    item.format,
                )
                self.assertEqual(
                    service.validate(item.session),
                    "No structural errors found",
                    item.format,
                )

    def test_every_dos_type_reports_its_real_capabilities(self):
        expected = {
            "adf": ("OFS", "ofs", "hashed"),
            "adf-intl": ("OFS-INTL", "ofs", "hashed"),
            "adf-dc": ("OFS-DC", "ofs", "dircache"),
            "ffs": ("FFS", "ffs", "hashed"),
            "ffs-intl": ("FFS-INTL", "ffs", "hashed"),
            "ffs-dc": ("FFS-DC", "ffs", "dircache"),
        }
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(Path(folder) / "work")
            media = {item.format: item.session for item in generated_media_matrix(service)}
            for format_name, (label, family, directories) in expected.items():
                capabilities = service.summary(media[format_name])["filesystemCapabilities"]
                self.assertEqual(capabilities["format"], label, format_name)
                self.assertEqual(capabilities["map"], family, format_name)
                self.assertEqual(capabilities["directories"], directories, format_name)
                self.assertEqual(capabilities["nameLimit"], 30, format_name)
                # A hash-table directory has no fixed entry count.
                self.assertIsNone(capabilities["directoryEntryLimit"], format_name)

    def test_long_amiga_names_round_trip_and_validate(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = DiskService(root / "work")
            session = service.create_blank("ffs-intl", "LongNames")
            # Exactly the 30 characters AmigaDOS allows.
            long_name = "A descriptive Amiga filename30"
            self.assertEqual(len(long_name), 30)
            add_test_file(service, session, root, path=long_name)
            self.assertEqual(
                service.read_file(session, long_name), FIXTURE_PAYLOAD
            )
            self.assertEqual(service.validate(session), "No structural errors found")

    def test_a_name_with_full_stops_and_spaces_round_trips(self):
        """``Disk.info`` and ``My Drawer`` are ordinary Amiga names."""
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = DiskService(root / "work")
            session = service.create_blank("ffs-intl", "Names")
            service.make_directory(session, "My Drawer")
            add_test_file(service, session, root, path="My Drawer/Disk.info")
            self.assertEqual(
                service.read_file(session, "My Drawer/Disk.info"), FIXTURE_PAYLOAD
            )
            self.assertEqual(
                [row["name"] for row in service.list_directory(session, "My Drawer", None)["entries"]],
                ["Disk.info"],
            )

    def test_a_hardfile_keeps_its_geometry_sidecar_through_an_edit(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = DiskService(root / "work")
            session = service.create_blank("hardfile", "Drive", "20MB", "hardfile")
            declared = service._hardfile_descriptor_size(session.descriptor_path)
            add_test_file(service, session, root, path="Payload")

            self.assertEqual(service.read_file(session, "Payload"), FIXTURE_PAYLOAD)
            self.assertEqual(service.validate(session), "No structural errors found")
            self.assertEqual(session.path.stat().st_size, declared)

    def test_generated_rom_session_survives_service_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder) / "work"
            session = DiskService(work).create_blank("kickfs", "Recover")
            restored = DiskService(work).get(session.id)
            self.assertEqual(restored.kind, "kickfs")
            self.assertEqual(restored.name, session.name)


if __name__ == "__main__":
    unittest.main()
