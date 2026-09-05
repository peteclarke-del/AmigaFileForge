import json
import tempfile
import unittest
from pathlib import Path

from app.rom_workbench import (
    RomWorkbenchError, apply_patch, audit_rom, bank_map, build_data_archive,
    build_expansion_rom, compare_roms, disassemble_68000, hardware_export,
    identify_rom, make_patch, normalise_project,
    make_selective_patch, disassemble_capstone, Cs,
)


class RomWorkbenchTests(unittest.TestCase):
    @unittest.skipIf(Cs is None, "Capstone is installed in the production image")
    def test_disassembly_names_library_vector_calls_through_a6(self):
        """``JSR -$228(A6)`` is how every Amiga program reaches the system."""
        # MOVEA.L $4.W,A6 ; JSR -$228(A6) ; RTS
        data = bytes.fromhex("2c7800044eaefdd84e75")
        rows = disassemble_68000(data, origin=0xF80000)["rows"]
        self.assertIn("ExecBase", rows[0]["comment"])
        self.assertIn("OpenLibrary", rows[1]["comment"])
        self.assertEqual(rows[2]["mnemonic"], "RTS")
        self.assertEqual(rows[2]["comment"], "Return from subroutine")

    @unittest.skipIf(Cs is None, "Capstone is installed in the production image")
    def test_the_library_a_routine_opened_is_tracked_into_later_calls(self):
        # MOVEA.L $4.W,A6 ; LEA (dos.library,PC),A1 ; JSR -$228(A6)
        # MOVEA.L D0,A6   ; JSR -$3C(A6)            ; RTS
        data = bytes.fromhex("2c7800044 3fa000e4eaefdd82c404eaeffc44e75".replace(" ", ""))
        data += b"dos.library\x00"
        rows = disassemble_68000(data, origin=0xF80000)["rows"]
        self.assertIn("exec.library OpenLibrary", rows[2]["comment"])
        self.assertIn("dos.library Output", rows[4]["comment"])

    @unittest.skipIf(Cs is None, "Capstone is installed in the production image")
    def test_a_custom_chip_register_access_is_identified(self):
        # MOVE.W #$0FFF,$DFF180
        data = bytes.fromhex("33fc0fff00dff180") + b"\x4e\x75"
        rows = disassemble_68000(data, origin=0xF80000)["rows"]
        self.assertIn("COLOR00", rows[0]["comment"])

    @unittest.skipIf(Cs is None, "Capstone is installed in the production image")
    def test_branches_are_labelled_and_explained(self):
        # BRA.B * ; RTS
        data = bytes.fromhex("60fe4e75")
        rows = disassemble_68000(data, origin=0xF80000)["rows"]
        self.assertEqual(rows[0]["target"], 0xF80000)
        self.assertIn("loop_F80000", rows[0]["operand"])
        self.assertIn("Branch always", rows[0]["comment"])

    @unittest.skipIf(Cs is None, "Capstone is installed in the production image")
    def test_bytes_that_are_not_instructions_stay_as_data(self):
        rows = disassemble_capstone(b"\xff\xff", architecture="68000", length=2)["rows"]
        self.assertIn("DC.", rows[0]["mnemonic"], rows[0])

    @unittest.skipIf(Cs is None, "Capstone is installed in the production image")
    def test_every_processor_in_the_family_decodes_big_endian(self):
        for architecture in ("68000", "68010", "68020", "68030", "68040", "68060"):
            with self.subTest(architecture=architecture):
                report = disassemble_capstone(
                    bytes.fromhex("4e714e75"), architecture=architecture, length=4,
                    symbols={"0x0": "start_here"},
                )
                self.assertEqual(
                    [row["mnemonic"] for row in report["rows"]], ["NOP", "RTS"]
                )
                self.assertEqual(report["rows"][0]["label"], "start_here")

    @unittest.skipIf(Cs is None, "Capstone is installed in the production image")
    def test_an_unknown_processor_is_refused_rather_than_guessed(self):
        with self.assertRaisesRegex(RomWorkbenchError, "68000, 68010"):
            disassemble_capstone(b"\x4e\x71", architecture="z80", length=2)

    def test_comparison_and_patch_are_checksum_guarded(self):
        left, right = b"hello ROM", b"hello rom!"
        report = compare_roms(left, right)
        self.assertGreater(report["changedBytes"], 0)
        patch = make_patch(left, right)
        self.assertEqual(apply_patch(left, patch), right)
        with self.assertRaises(RomWorkbenchError):
            apply_patch(b"wrong", patch)

    def test_selective_patch_contains_only_chosen_ranges(self):
        left, right = b"ABC-DEF-GHI", b"AbC-DEF-GhI"
        report = compare_roms(left, right)
        self.assertEqual(len(report["ranges"]), 2)
        patch = make_selective_patch(left, right, [1])
        self.assertEqual(apply_patch(left, patch), b"ABC-DEF-GhI")

    def test_bank_map_finds_duplicates(self):
        data = b"A" * 256 + b"B" * 256 + b"A" * 256
        report = bank_map(data, 256)
        self.assertEqual(report["banks"][0]["duplicates"], [2])
        self.assertEqual(report["banks"][2]["duplicates"], [0])

    def test_builder_creates_a_safe_rom_header(self):
        data = build_expansion_rom("Workshop", [{"name": "MENU", "syntax": "<file>"}])
        report = audit_rom(data, 256 * 1024)
        self.assertTrue(report["healthy"], report)

    def test_data_archive_refuses_overflow(self):
        with self.assertRaises(RomWorkbenchError):
            build_data_archive("Full", [("BIG", b"X" * 20_000_000)])

    def test_hardware_export_can_mirror_swap_and_split(self):
        result = hardware_export(b"\x01\x02", device_size=8, mirror=True, lanes=2, byte_swap=True)
        self.assertEqual(result["components"], [b"\x02" * 4, b"\x01" * 4])

    def test_hardware_export_can_swap_words_and_address_lines(self):
        words = hardware_export(bytes(range(8)), device_size=8, word_swap=True)
        self.assertEqual(words["components"][0], bytes((2, 3, 0, 1, 6, 7, 4, 5)))
        addresses = hardware_export(bytes(range(8)), device_size=8, address_swaps=[(0, 1)])
        self.assertEqual(addresses["components"][0], bytes((0, 2, 1, 3, 4, 6, 5, 7)))

    def test_identification_catalogue_and_mirror_hint(self):
        data = b"AB" * 16
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "catalogue.json"
            digest = __import__("hashlib").sha256(data).hexdigest()
            path.write_text(json.dumps({"roms": [{"sha256": digest, "title": "Known"}]}))
            result = identify_rom(data, path)
        self.assertTrue(result["matched"])
        self.assertTrue(any("mirrored" in row for row in result["transformations"]))

    def test_project_metadata_is_bounded(self):
        project = normalise_project({"notes": "x" * 30000, "symbols": {"32768": "start"}})
        self.assertEqual(len(project["notes"]), 20000)
        self.assertEqual(project["symbols"]["32768"], "start")


if __name__ == "__main__":
    unittest.main()
