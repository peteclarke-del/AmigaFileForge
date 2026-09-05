import io
import tempfile
import unittest
from pathlib import Path

from app.disk_service import DiskError, DiskService


class KickstartRomTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.service = DiskService(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def _host_file(self, data=b"HELLO", name="host-file"):
        path = Path(self.temporary.name) / name
        path.write_bytes(data)
        return path

    def test_a_created_rom_has_a_valid_header_footer_and_checksum(self):
        session = self.service.create_blank(
            "kickfs", "Tools", target_hardware="a1200-ffs",
            options={"geometry": "256k", "version": 40, "copyright": "tools.library 1.0"},
        )
        self.assertEqual(session.kind, "kickfs")
        self.assertEqual(session.path.stat().st_size, 256 * 1024)
        self.assertEqual(session.target_hardware, "a1200-ffs")
        details = self.service.kickfs_details(session)
        self.assertEqual(details["title"], "Tools.library")
        self.assertEqual(details["version"], "40.0")
        self.assertEqual(details["copyright"], "tools.library 1.0")
        self.assertFalse(details["readOnly"])
        self.assertEqual(details["fileCount"], 1)
        self.assertIn("Valid Kickstart ROM", self.service.validate(session))

    def test_a_512k_rom_can_be_created(self):
        session = self.service.create_blank(
            "kickfs", "Big", options={"geometry": "512k"}
        )
        self.assertEqual(session.path.stat().st_size, 512 * 1024)

    def test_an_opened_rom_is_detected_as_a_module_list_before_raw_bytes(self):
        created = self.service.create_blank("kickfs", "Modules")
        reopened = self.service.create_from_stream(
            "modules.rom", io.BytesIO(created.path.read_bytes())
        )
        self.assertEqual(reopened.kind, "kickfs")
        self.assertIsNotNone(self.service.summary(reopened)["kickfs"])

    def test_modules_are_listed_with_their_identity(self):
        session = self.service.create_blank("kickfs", "Modules")
        rows = self.service.list_directory(session, "", None)["entries"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "Modules.library")
        self.assertGreater(rows[0]["length"], 0)
        self.assertEqual(
            self.service.read_file(session, "Modules.library")[:2], b"\x4a\xfc"
        )

    def test_the_identity_can_be_rewritten_and_the_checksum_repaired(self):
        session = self.service.create_blank("kickfs", "Old")
        self.service.set_kickfs_properties(
            session, title="New", version=45, copyright_text="new.library 2.0",
        )
        details = self.service.kickfs_details(session)
        self.assertEqual(details["version"], "45.0")
        self.assertEqual(details["copyright"], "new.library 2.0")
        self.assertIn("all block CRCs passed", self.service.validate(session))

    def test_a_failed_identity_write_restores_the_exact_rom(self):
        session = self.service.create_blank("kickfs", "Safe")
        original = session.path.read_bytes()
        with self.assertRaises(DiskError):
            self.service.set_kickfs_properties(
                session, title="Changed", version=2,
                copyright_text="far too long " * 30,
            )
        self.assertEqual(session.path.read_bytes(), original)
        self.assertEqual(self.service.kickfs_details(session)["title"], "Safe.library")

    def test_an_identity_string_that_would_not_fit_is_refused(self):
        """A silently shortened string is indistinguishable from corruption."""
        session = self.service.create_blank("kickfs", "Fit")
        original = session.path.read_bytes()
        with self.assertRaises(DiskError):
            self.service.set_kickfs_properties(
                session, title="Fit", version=1,
                copyright_text="x" * 119,
            )
        self.assertEqual(session.path.read_bytes(), original)

    def test_a_rom_version_outside_its_range_is_refused(self):
        session = self.service.create_blank("kickfs", "Range")
        with self.assertRaisesRegex(DiskError, "0 to 65535"):
            self.service.set_kickfs_properties(
                session, title="Range", version=70000, copyright_text="range.library",
            )

    def test_a_rom_module_list_is_read_only(self):
        """A module's position is fixed by the pointers the ROM scan follows."""
        session = self.service.create_blank("kickfs", "Fixed")
        with self.assertRaises(DiskError):
            self.service.put(session, "Extra", self._host_file(b"DATA"))

    def test_an_unsupported_rom_size_is_refused(self):
        with self.assertRaisesRegex(DiskError, "256 KiB, 512 KiB or 1 MiB"):
            self.service.create_blank("kickfs", "Odd", options={"geometry": "16k"})


if __name__ == "__main__":
    unittest.main()
