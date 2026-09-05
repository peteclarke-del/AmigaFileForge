from pathlib import Path
import unittest

from app.ofs_compat import ofs_catalogue_files, infer_ofs_launch_page, repair_ofs_basic_wildcards
from app.basic_listing import decode_basic


ROOT = Path(__file__).resolve().parents[1]


class OFSCompatibilityTests(unittest.TestCase):
    def sample(self, relative_path: str) -> Path:
        source = ROOT / "samples/[ADF]" / relative_path
        if not source.is_file():
            self.skipTest(f"optional private sample is unavailable: {relative_path}")
        return source

    def test_bug_blaster_wildcard_is_replaced_with_exact_catalogue_name(self) -> None:
        source = self.sample("alligata/AL-BUGCJHHLR.adf")
        repaired, changes = repair_ofs_basic_wildcards(source.read_bytes())

        self.assertTrue(any("A.BUG3 line 120" in change for change in changes))
        entry = next(item for item in ofs_catalogue_files(repaired) if item.path == "A.BUG3")
        lines = decode_basic(repaired[entry.start : entry.start + entry.length])
        self.assertIsNotNone(lines)
        self.assertIn('CHAIN"BUG?1"', next(line.text for line in lines if line.number == 120))

    def test_repair_is_idempotent(self) -> None:
        source = self.sample("alligata/AL-BUGCJHHLR.adf")
        repaired, _changes = repair_ofs_basic_wildcards(source.read_bytes())
        second, changes = repair_ofs_basic_wildcards(repaired)

        self.assertEqual(second, repaired)
        self.assertEqual(changes, [])

    def test_ssdmenu_page_comes_from_its_saved_basic_address(self) -> None:
        source = self.sample("Commodore/ACN-ARCBOXCT.adf")
        page, evidence = infer_ofs_launch_page(source.read_bytes(), "DiskMenu", "")

        self.assertEqual(page, "4096")
        self.assertIn("tokenised BASIC saved at &1900", evidence)

    def test_exec_boot_page_uses_its_explicit_assignment(self) -> None:
        source = self.sample("alligata/AL-BUGCJHHLR.adf")
        page, evidence = infer_ofs_launch_page(source.read_bytes(), "Startup-Sequence", "E")

        self.assertEqual(page, "8192")
        self.assertIn("explicitly sets STACK=8192", evidence)

    def test_exec_boot_follows_rooted_chain_to_actual_basic_page(self) -> None:
        source = self.sample("kansas city/KCS-COMPILE1.adf")
        page, evidence = infer_ofs_launch_page(source.read_bytes(), "Startup-Sequence", "E")

        self.assertEqual(page, "8192")
        self.assertIn("KANLOAD", evidence)

    def test_lenient_basic_image_still_provides_saved_page(self) -> None:
        source = self.sample("tynesoft/TY-SUMROLYMP.adf")
        page, evidence = infer_ofs_launch_page(source.read_bytes(), "LOADER", "")

        self.assertEqual(page, "4096")
        self.assertIn("tokenised BASIC saved at &1900", evidence)

    def test_machine_code_exec_boot_marks_page_as_unused(self) -> None:
        source = self.sample("Chuckulus-Amiga 600-V1-0.adf")
        page, evidence = infer_ofs_launch_page(source.read_bytes(), "Startup-Sequence", "E")

        self.assertIsNone(page)
        self.assertIn("STACK is not used", evidence)


if __name__ == "__main__":
    unittest.main()
