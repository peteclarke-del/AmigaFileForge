import io
import struct
import tempfile
import unittest
import zipfile

from app.disk_service import DiskError, DiskService
from app.download_archive import build_download_archive
from app.rom import (
    EXTENDED_ROM_SIGNATURE,
    inspect_bank,
    make_expansion_rom,
    parse_extended_rom_header,
    parse_rom_header,
    resident_module_candidates,
    rom_base,
    rom_checksum,
    star_command_inventory,
)

KICKSTART_256K = 256 * 1024
KICKSTART_512K = 512 * 1024


def build_rom(size: int = KICKSTART_256K, *, modules=(), version=(40, 68)) -> bytearray:
    """Assemble a ROM with a valid header, footer, checksum and resident tags.

    ``modules`` entries are ``(offset, name, id_string, node_type, priority)``.
    Everything a real machine's ROM scan needs is present, so a test that
    passes here would also pass on hardware.
    """
    base = rom_base(size)
    rom = bytearray(size)
    struct.pack_into(">H", rom, 0, 0x1111 if size == KICKSTART_256K else 0x1114)
    struct.pack_into(">H", rom, 2, 0x4EF9)
    struct.pack_into(">I", rom, 4, base + 0x400)
    struct.pack_into(">HH", rom, 12, *version)
    for index, (offset, name, identity, node_type, priority) in enumerate(modules):
        name_offset = offset + 0x40
        id_offset = offset + 0x80
        end = offset + 0x800
        struct.pack_into(">H", rom, offset, 0x4AFC)
        struct.pack_into(">I", rom, offset + 2, base + offset)
        struct.pack_into(">I", rom, offset + 6, base + end)
        rom[offset + 10] = 0x80
        rom[offset + 11] = version[0]
        rom[offset + 12] = node_type
        struct.pack_into(">b", rom, offset + 13, priority)
        struct.pack_into(">I", rom, offset + 14, base + name_offset)
        struct.pack_into(">I", rom, offset + 18, base + id_offset)
        struct.pack_into(">I", rom, offset + 22, base + offset + 0x100)
        rom[name_offset : name_offset + len(name) + 1] = name.encode() + b"\0"
        rom[id_offset : id_offset + len(identity) + 1] = identity.encode() + b"\0"
        del index
    struct.pack_into(">I", rom, size - 20, size)
    struct.pack_into(">I", rom, size - 24, 0)
    struct.pack_into(">I", rom, size - 24, rom_checksum(bytes(rom), skip_offset=size - 24))
    return rom


EXEC_MODULE = (0x200, "exec.library", "exec 40.10 (1993)", 9, 126)
DOS_MODULE = (0x2000, "dos.library", "dos 40.3 (1993)", 9, 100)


class RomHeaderTests(unittest.TestCase):
    def test_a_kickstart_header_is_parsed(self):
        header = parse_rom_header(bytes(build_rom(modules=[EXEC_MODULE])))
        self.assertIsNotNone(header)
        self.assertEqual(header.title, "exec.library")
        self.assertEqual(header.version, "40.68")
        self.assertEqual(header.roles, "Kickstart")
        self.assertEqual(header.language_entry, rom_base(KICKSTART_256K) + 0x400)
        self.assertTrue(header.checksum_valid)
        self.assertEqual(header.module_count, 1)

    def test_a_512k_kickstart_uses_its_own_header_word_and_base(self):
        header = parse_rom_header(
            bytes(build_rom(KICKSTART_512K, modules=[EXEC_MODULE]))
        )
        self.assertIsNotNone(header)
        self.assertEqual(header.base, 0xF80000)
        self.assertEqual(header.declared_size, KICKSTART_512K)

    def test_bytes_without_a_header_or_a_tag_are_not_guessed_at(self):
        self.assertIsNone(parse_rom_header(bytes(4096)))
        self.assertIsNone(parse_rom_header(b"\xff" * 4096))

    def test_a_tag_whose_self_pointer_is_wrong_is_rejected(self):
        """The self-reference is what separates a tag from the same two bytes."""
        rom = build_rom(modules=[EXEC_MODULE])
        struct.pack_into(">I", rom, EXEC_MODULE[0] + 2, 0xDEADBEEF)
        self.assertEqual(resident_module_candidates(bytes(rom)), [])

    def test_a_broken_checksum_is_reported_rather_than_ignored(self):
        rom = build_rom(modules=[EXEC_MODULE])
        rom[0x1000] ^= 0xFF
        header = parse_rom_header(bytes(rom))
        self.assertFalse(header.checksum_valid)
        row = inspect_bank(bytes(rom), 0)
        self.assertTrue(
            any("reset checksum" in warning for warning in row["warnings"]),
            row["warnings"],
        )

    def test_a_declared_size_mismatch_is_reported(self):
        rom = build_rom(modules=[EXEC_MODULE])
        struct.pack_into(">I", rom, len(rom) - 20, KICKSTART_512K)
        struct.pack_into(">I", rom, len(rom) - 24, 0)
        struct.pack_into(
            ">I", rom, len(rom) - 24, rom_checksum(bytes(rom), skip_offset=len(rom) - 24)
        )
        row = inspect_bank(bytes(rom), 0)
        self.assertTrue(
            any("split set" in warning for warning in row["warnings"]), row["warnings"]
        )


class ResidentModuleTests(unittest.TestCase):
    def test_every_resident_module_is_decoded_with_its_identity(self):
        rom = bytes(build_rom(modules=[EXEC_MODULE, DOS_MODULE]))
        modules = resident_module_candidates(rom)
        self.assertEqual(
            [module["title"] for module in modules], ["exec.library", "dos.library"]
        )
        self.assertEqual(modules[0]["help"], "exec 40.10 (1993)")
        self.assertEqual(modules[0]["nodeType"], "library")
        self.assertEqual(modules[0]["priority"], 126)
        self.assertTrue(modules[0]["autoinit"])

    def test_modules_are_listed_in_the_order_the_machine_initialises_them(self):
        """Priority decides boot order, so that is the order presented."""
        rom = bytes(build_rom(modules=[DOS_MODULE, EXEC_MODULE]))
        modules = resident_module_candidates(rom)
        self.assertEqual(
            [module["title"] for module in modules], ["exec.library", "dos.library"]
        )

    def test_the_module_inventory_reports_what_a_rom_provides(self):
        rom = bytes(build_rom(modules=[EXEC_MODULE, DOS_MODULE]))
        inventory = star_command_inventory(rom, resident_module_candidates(rom))
        names = {row["name"] for row in inventory}
        self.assertIn("exec.library", names)
        self.assertIn("dos.library", names)
        declared = next(row for row in inventory if row["name"] == "dos.library")
        self.assertEqual(declared["confidence"], "declared")
        self.assertEqual(declared["helpText"], "dos 40.3 (1993)")

    def test_a_decoded_bank_exposes_regions_entry_points_and_strings(self):
        row = inspect_bank(
            bytes(build_rom(modules=[EXEC_MODULE])), 0, include_contents=True
        )
        kinds = [item["kind"] for item in row["structures"]]
        self.assertEqual(kinds[0], "header")
        self.assertIn("module", kinds)
        self.assertIn("footer", kinds)
        self.assertIn("exec.library", [item["text"] for item in row["strings"]])


class ExtendedRomTests(unittest.TestCase):
    def test_an_extended_rom_trailer_and_checksum_are_recognised(self):
        image = bytearray(KICKSTART_512K)
        struct.pack_into(">I", image, len(image) - 16, len(image))
        image[-8:] = EXTENDED_ROM_SIGNATURE
        struct.pack_into(">I", image, len(image) - 12, rom_checksum(bytes(image[:-12])))
        header = parse_extended_rom_header(bytes(image))
        self.assertIsNotNone(header)
        self.assertTrue(header.checksum_valid)
        self.assertEqual(header.declared_size, KICKSTART_512K)

    def test_a_trailer_that_disagrees_with_the_file_is_refused(self):
        image = bytearray(KICKSTART_512K)
        struct.pack_into(">I", image, len(image) - 16, KICKSTART_256K)
        image[-8:] = EXTENDED_ROM_SIGNATURE
        self.assertIsNone(parse_extended_rom_header(bytes(image)))


class RomTemplateTests(unittest.TestCase):
    def test_a_standard_sized_template_is_a_valid_rom(self):
        rom = make_expansion_rom(KICKSTART_256K, "Forge")
        header = parse_rom_header(rom)
        self.assertIsNotNone(header)
        self.assertTrue(header.checksum_valid)
        self.assertEqual(header.title, "Forge.library")

    def test_a_non_standard_size_still_carries_one_resident_tag(self):
        """An expansion ROM on an odd device has no machine header, only a tag."""
        rom = make_expansion_rom(8 * 1024, "Diag")
        header = parse_rom_header(rom)
        self.assertIsNotNone(header)
        self.assertEqual(header.title, "Diag.library")
        self.assertEqual(header.module_count, 1)


class RomServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.service = DiskService(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_a_512k_image_is_listed_as_two_256k_banks(self):
        session = self.service.create_blank(
            "rom",
            "Banked",
            options={
                "totalSize": KICKSTART_512K,
                "bankSize": KICKSTART_256K,
                "template": "kickstart",
            },
        )
        rows = self.service.list_rom_banks(session)
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[1]["empty"])
        decoded = self.service.inspect_rom_bank(session, 0)
        self.assertEqual(decoded["fileOffset"], 0)
        self.assertGreater(decoded["programmedBytes"], 0)
        self.assertEqual(len(decoded["diagnostics"]["sha256"]), 64)

    def test_an_opened_rom_layout_survives_recovery(self):
        session = self.service.create_from_stream(
            "chips.rom",
            io.BytesIO(bytes(range(256)) * 128),
            rom_options={
                "platform": "cartridge",
                "layout": "byte-interleaved-4",
                "componentNames": ["u34.rom", "u35.rom", "u36.rom", "u37.rom"],
            },
        )
        restored = DiskService(self.temporary.name)._restore_session(session.id)
        self.assertEqual(restored.rom_layout, "byte-interleaved-4")
        self.assertEqual(restored.rom_component_names[0], "u34.rom")

    def test_an_overlapping_bank_move_reads_sources_before_writing(self):
        session = self.service.create_blank(
            "rom", "Move", options={"totalSize": 4 * 1024, "bankSize": 1024}
        )
        for bank, value in enumerate((1, 2, 3, 4)):
            self.service.put_rom_bank(session, bytes((value,)) * 1024, bank)
        self.service.move_rom_banks(session, [0, 1, 2], 1)
        self.assertEqual(self.service.rom_bank_bytes(session, "bank:1")[:1], b"\x01")
        self.assertEqual(self.service.rom_bank_bytes(session, "bank:2")[:1], b"\x02")
        self.assertEqual(self.service.rom_bank_bytes(session, "bank:3")[:1], b"\x03")
        self.assertTrue(self.service.list_rom_banks(session)[0]["empty"])

    def test_a_bank_import_rejects_implicit_truncation(self):
        session = self.service.create_blank(
            "rom", "Small", options={"totalSize": 4096, "bankSize": 4096}
        )
        with self.assertRaisesRegex(DiskError, "does not fit"):
            self.service.put_rom_bank(session, b"x" * 4097)

    def test_interleaved_component_export_restores_chip_order(self):
        """A 512 KiB Kickstart on two 27C400s is even and odd bytes, in order."""
        logical = bytes((0, 10, 20, 30, 1, 11, 21, 31))
        session = self.service.create_from_stream(
            "set.rom",
            io.BytesIO(logical),
            rom_options={
                "layout": "byte-interleaved-4",
                "componentNames": ["a.rom", "b.rom", "c.rom", "d.rom"],
            },
        )
        exports = self.service.rom_component_exports(session)
        self.assertEqual(
            [path.read_bytes() for path, _name in exports],
            [b"\x00\x01", b"\x0a\x0b", b"\x14\x15", b"\x1e\x1f"],
        )

    def test_a_saved_interleaved_rom_contains_its_readme_and_physical_chips(self):
        session = self.service.create_from_stream(
            "set.rom",
            io.BytesIO(bytes((0, 10, 20, 30, 1, 11, 21, 31))),
            rom_options={
                "platform": "cartridge",
                "layout": "byte-interleaved-4",
                "componentNames": ["u34.rom", "u35.rom", "u36.rom", "u37.rom"],
            },
        )
        archive_path, _name = build_download_archive(self.service, session)
        with zipfile.ZipFile(archive_path) as archive:
            self.assertIn("set.rom", archive.namelist())
            self.assertIn("README.md", archive.namelist())
            self.assertEqual(archive.read("ROM-components/u34.rom"), b"\x00\x01")
            readme = archive.read("README.md").decode()
            self.assertIn("byte-interleaved-4", readme)
            self.assertIn("u34.rom", readme)


if __name__ == "__main__":
    unittest.main()
