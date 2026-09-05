from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from amiganut.basic import detokenise, tokenise

from app.checksum import sha256_bytes
from app.content_kind import analyse_content, metadata_kind
from app.disk_service import DiskService
from app.file_editor import (
    _format_basic_listing,
    _printable_strings,
    _renumber_tokenised,
    disassemble_file,
    disassemble_file_data,
    inspect_editable_file,
    normalise_basic_source,
    pack_basic_lines,
    prepare_basic_source,
    save_editor_text,
    save_editor_text_as,
    search_image_files,
    update_file_properties,
    verify_basic_source,
    write_file_range,
)
from app.operations import OperationCancelled
from tests.dms_fixture import minimal_dms


class FileEditorTests(unittest.TestCase):
    def service_with_file(self, content: bytes, metadata: dict | None = None):
        folder = tempfile.TemporaryDirectory()
        source = Path(folder.name) / "exported"
        source.write_bytes(content)
        service = Mock()

        def export(*_args):
            copy = Path(folder.name) / f"copy-{service.export_file.call_count}"
            copy.write_bytes(source.read_bytes())
            return copy

        service.export_file.side_effect = export
        service.file_metadata.return_value = metadata or {"protection": 0, "comment": "", "length": len(content)}
        service.editor_project.return_value = {}
        return folder, service

    def test_detects_and_decodes_tokenised_basic(self):
        program = tokenise('10 PRINT "HELLO"\n20 GOTO 10')
        folder, service = self.service_with_file(program)
        try:
            report = inspect_editable_file(service, SimpleNamespace(target_hardware="a1200-ffs", hfe_read_only=False, kind="ofs"), "GAME", None)
        finally:
            folder.cleanup()
        self.assertEqual(report["view"], "basic")
        self.assertTrue(report["editable"])
        self.assertIn('10 PRINT "HELLO"', report["text"])

    def test_listing_classifier_recognises_content_before_the_editor_opens_it(self):
        self.assertEqual(analyse_content(tokenise('10 PRINT "HELLO"'), "$.PROGRAM")[0], "basic")
        self.assertEqual(analyse_content(b"*DIR GAMES\rCHAIN \"MENU\"\r", "$.COMMANDS")[0], "script")
        self.assertEqual(analyse_content(b"A readable document\r", "$.NOTES")[0], "text")
        self.assertEqual(analyse_content(bytes.fromhex("A90020F4FF60"), "$.CODE")[0], "binary")
        self.assertEqual(analyse_content(minimal_dms(), "$.DMS")[0], "container")

    def test_listing_classifier_uses_amiga_metadata_without_reading_content(self):
        # A Workbench Tool icon proves the file is an executable, and a
        # Kickstart icon proves it is a ROM image.
        self.assertEqual(metadata_kind("Program", 3), "binary")
        self.assertEqual(metadata_kind("Kickstart", 7), "binary")
        # A Project icon says only that some tool opens the file, so the
        # content still has to be read.
        self.assertIsNone(metadata_kind("Data", 4))
        self.assertEqual(metadata_kind("Game.bas", None), "basic")
        self.assertEqual(metadata_kind("Startup-Sequence", None), "script")
        self.assertEqual(metadata_kind("ReadMe", None), "text")
        self.assertEqual(metadata_kind("Manual.guide", None), "text")

    def test_image_search_traverses_ffs_directories_and_reports_source_lines(self):
        service = Mock()
        service.list_directory.side_effect = lambda _session, path, *_rest: {
            "entries": (
                [{"name": "Games", "path": "Games", "type": "dir", "length": 0}]
                if path == "" else
                [{"name": "Startup-Sequence", "path": "Games/Startup-Sequence", "type": "file", "length": 31}]
            )
        }
        service.read_file.return_value = b'Run Intro\nCHAIN "ARCADIANS"\n'
        report = search_image_files(
            service, SimpleNamespace(kind="ffs"), "arcadians", None, "",
        )
        self.assertEqual(report["filesConsidered"], 1)
        self.assertEqual(report["results"][0]["path"], "Games/Startup-Sequence")
        self.assertEqual(report["results"][0]["matches"][0]["line"], 2)
    def test_image_search_matches_protection_and_comment_metadata(self):
        service = Mock()
        service.list_ofs_catalogue_files.return_value = [{
            "name": "GAME", "path": "GAME", "length": 4,
            "protection": 0x05, "comment": "Reviewed copy", "attr": "L",
        }]
        service.read_file.return_value = b"\x00\x01\x02\x03"

        report = search_image_files(
            service, SimpleNamespace(kind="ofs"), "----r-e-", None,
        )

        self.assertEqual(report["results"][0]["metadataMatches"], ["protection"])

    def test_image_search_accepts_a_sha256_prefix_and_returns_the_full_digest(self):
        service = Mock()
        content = b"A uniquely hashed Amiga file"
        service.list_ofs_catalogue_files.return_value = [{
            "name": "HASHED", "path": "$.HASHED", "length": len(content),
        }]
        service.read_file.return_value = content
        digest = sha256_bytes(content)

        report = search_image_files(
            service, SimpleNamespace(kind="ofs"), digest[:12], None, None,
        )

        self.assertTrue(report["results"][0]["hashMatch"])
        self.assertEqual(report["results"][0]["sha256"], digest)

    def test_image_search_reports_a_useful_binary_string_offset(self):
        service = Mock()
        content = bytes(range(32)) + b"LOAD GAME DATA" + bytes(range(32))
        service.list_ofs_catalogue_files.return_value = [{
            "name": "CODE", "path": "$.CODE", "length": len(content),
        }]
        service.read_file.return_value = content

        report = search_image_files(
            service, SimpleNamespace(kind="ofs"), "game data", None, None,
        )

        self.assertEqual(report["results"][0]["matches"][0]["offset"], 32)

    def test_image_search_honours_cancellation_between_files(self):
        service = Mock()
        service.list_ofs_catalogue_files.return_value = [
            {"name": "ONE", "path": "$.ONE", "length": 1},
            {"name": "TWO", "path": "$.TWO", "length": 1},
        ]
        service.read_file.return_value = b"X"

        def cancel(message, current, _total):
            if message.startswith("Searching") and current == 1:
                raise OperationCancelled("Stopped safely")

        with self.assertRaises(OperationCancelled):
            search_image_files(
                service, SimpleNamespace(kind="ofs"), "missing", None, None,
                progress=cancel,
            )

    def test_image_search_includes_installed_menu_and_project_metadata(self):
        service = Mock()
        service.list_ofs_catalogue_files.return_value = []
        supplemental = [{
            "virtual": True, "resultType": "menu", "kind": "menu",
            "name": "Arcadians", "fileName": "WBMENU", "path": "WBMENU",
            "slot": 20, "openable": True,
            "searchFields": {"publisher": "Commodore", "action": "EXECUTE"},
        }]

        report = search_image_files(
            service, SimpleNamespace(kind="ofs"), "commodore", None, None,
            supplemental=supplemental,
        )

        self.assertEqual(report["results"][0]["metadataMatches"], ["publisher"])
        self.assertEqual(report["results"][0]["fileName"], "WBMENU")

    def test_image_search_reads_raw_rom_banks(self):
        service = Mock()
        service.list_rom_banks.return_value = [{
            "bank": 0, "name": "Network ROM", "length": 64,
        }]
        service.read_file.return_value = bytes(16) + b"NETWORK COMMAND" + bytes(33)

        report = search_image_files(
            service, SimpleNamespace(kind="rom"), "network command", None, None,
        )

        self.assertEqual(report["results"][0]["path"], "bank:0")
        self.assertEqual(report["results"][0]["matches"][0]["offset"], 16)

    def test_basic_listing_always_has_a_space_after_the_line_number(self):
        self.assertEqual(
            _format_basic_listing('10PRINT "HELLO"\n20 GOTO 10\n30\tEND'),
            '10 PRINT "HELLO"\n20 GOTO 10\n30 END',
        )

    def test_extended_basic_tokens_open_read_only(self):
        """A program using the 1.2-only bank cannot be retokenised as 1.0."""
        from amiganut.basic import AMIGABASIC_12, tokenise

        program = tokenise("10 UCASE$(A$)", dialect=AMIGABASIC_12)
        folder, service = self.service_with_file(program)
        try:
            report = inspect_editable_file(
                service,
                SimpleNamespace(target_hardware="amigaos", hfe_read_only=False, kind="ffs"),
                "Program", None,
            )
        finally:
            folder.cleanup()
        self.assertEqual(report["view"], "basic")
        self.assertEqual(report["basic"]["dialect"], "AmigaBASIC 1.2")
        self.assertFalse(report["editable"])
        self.assertIn("UCASE$", report["text"])

    def test_boot_and_other_command_files_open_as_unnumbered_scripts(self):
        script = b"FailAt 21\nStack 8192\nCD Games\nExecute Menu\n"
        folder, service = self.service_with_file(script)
        try:
            report = inspect_editable_file(service, SimpleNamespace(hfe_read_only=False, kind="ofs"), "Startup-Sequence", None)
        finally:
            folder.cleanup()
        self.assertEqual(report["view"], "script")
        self.assertTrue(report["editable"])
        self.assertFalse(report["tokenisedBasic"])
        self.assertEqual(
            [item["action"] for item in report["script"]["commands"]],
            ["FAILAT", "STACK", "CD", "EXECUTE"],
        )
        self.assertEqual(report["text"].splitlines()[0], "FailAt 21")

        folder, service = self.service_with_file(b"Assign MENU: SYS:\nRun Game\n")
        try:
            other = inspect_editable_file(service, SimpleNamespace(hfe_read_only=False, kind="ofs"), "$.COMMANDS", None)
        finally:
            folder.cleanup()
        self.assertEqual(other["view"], "script")

    def test_extensionless_dms_opens_as_a_browsable_container(self):
        folder, service = self.service_with_file(minimal_dms())
        try:
            report = inspect_editable_file(
                service,
                SimpleNamespace(target_hardware="a600", hfe_read_only=False, kind="ffs"),
                "$.DMS.THRUST", None,
            )
        finally:
            folder.cleanup()
        self.assertEqual(report["view"], "container")
        self.assertEqual(report["containerKind"], "dms")
        self.assertTrue(report["readOnly"])
        self.assertFalse(report["editable"])

    def test_renumber_updates_encoded_targets_not_string_contents(self):
        program = tokenise('10 GOTO 30\n20 PRINT "30"\n30 END')
        listing = detokenise(_renumber_tokenised(program, 100, 20))
        self.assertIn("100 GOTO 140", listing)
        self.assertIn('120 PRINT "30"', listing)
        self.assertIn("140 END", listing)

    def test_prepare_basic_renumbers_newly_edited_listing(self):
        result = prepare_basic_source('10 PRINT "A"\n15 GOSUB 10\n20 END', 1000, 10)
        self.assertEqual(result["lineCount"], 3)
        self.assertIn("1010 GOSUB 1000", result["text"])

    def test_normalise_basic_source_validates_and_formats_pasted_lines(self):
        result = normalise_basic_source('100PRINT "PASTED"\n110GOTO 100')
        self.assertEqual(result["lineCount"], 2)
        self.assertEqual(result["text"], '100 PRINT "PASTED"\n110 GOTO 100')

    def test_basic_verification_proves_token_round_trip_and_maps_lines(self):
        result = verify_basic_source('10 PRINT "HELLO"\n20 GOTO 10', '10 PRINT "OLD"')
        self.assertTrue(result["roundTripExact"])
        self.assertEqual(result["lineCount"], 2)
        self.assertEqual(result["destinations"], [10])
        self.assertEqual([row["line"] for row in result["lineRanges"]], [10, 20])
        self.assertTrue(result["diff"])

    def test_project_regions_override_code_and_apply_bookmarks(self):
        # MOVEQ #65,D0 ; JSR $00FC00EE ; RTS, then a message the project marks
        # as text so it is not disassembled.
        data = bytes.fromhex("70414EB900FC00EE4E75") + b"HELLO"
        report = __import__("app.file_editor", fromlist=["disassemble_file_data"]).disassemble_file_data(
            data, {"protection": 0}, SimpleNamespace(target_hardware="a1200-ffs"),
            "CODE", project={
                "symbols": {"0": "start_here"},
                "regions": [{"start": 10, "end": 15, "kind": "text", "name": "message", "width": 8}],
                "bookmarks": [{"offset": 0, "name": "entry", "note": "Reviewed entry"}],
                "comments": {"0": "User annotation"},
            },
        )
        self.assertEqual(report["rows"][0]["label"], "start_here")
        self.assertIn("Reviewed entry", report["rows"][0]["comment"])
        self.assertIn("User annotation", report["rows"][0]["comment"])
        message = next(row for row in report["rows"] if row.get("regionKind") == "text")
        self.assertEqual(message["label"], "message")
        self.assertEqual(message["mnemonic"], "DC.B")

    def test_68000_project_words_use_the_processor_byte_order(self):
        report = disassemble_file_data(
            bytes.fromhex("12344E75"), {"protection": 0},
            SimpleNamespace(target_hardware="amigaos"), "CODE", architecture="m68k",
            project={"regions": [{"start": 0, "end": 2, "kind": "words", "width": 8}]},
        )
        word = next(row for row in report["rows"] if row.get("regionKind") == "words")
        self.assertEqual(word["operand"], "$1234")

    def test_pack_basic_lines_uses_tokenised_line_capacity(self):
        statements = ['PRINT "A"'] * 80
        result = pack_basic_lines([statements, ["A=1", "B=2", "PRINT A+B"]])
        self.assertGreater(len(result["groups"][0]), 1)
        self.assertEqual(sum(result["groups"][0]), 80)
        self.assertEqual(result["groups"][1], [3])

    def test_a_binary_file_gets_annotated_68000_disassembly(self):
        # MOVEA.L $4.W,A6 ; JSR -$228(A6) ; RTS
        data = bytes.fromhex("2c7800044eaefdd84e75")
        folder, service = self.service_with_file(
            data, {"load": 0, "execute": 0, "length": len(data)}
        )
        try:
            report = disassemble_file(
                service, SimpleNamespace(target_hardware="a1200-ffs"), "Code", None, None
            )
        finally:
            folder.cleanup()
        self.assertEqual(report["architecture"], "68000")
        self.assertIn("ExecBase", report["rows"][0]["comment"])
        self.assertIn("OpenLibrary", report["rows"][1]["comment"])
        self.assertGreater(report["reachableInstructions"], 0)

    def test_printable_strings_require_human_looking_words(self):
        data = b"!!!!1234___\0Hello world!\0AB\0LOAD GAME\0hJJJJ)\0A1$%\0"
        strings = _printable_strings(data, 0x8000)
        self.assertEqual([item["text"] for item in strings], ["Hello world!", "LOAD GAME"])
        self.assertEqual(strings[0]["address"], 0x8000 + data.index(b"Hello"))

    def test_file_disassembly_reports_the_readable_text_it_finds(self):
        # MOVEQ #0,D0 ; RTS, then a NUL-terminated string.
        data = bytes.fromhex("70004e75") + b"Hello world\0"
        folder, service = self.service_with_file(
            data, {"protection": 0, "comment": "", "length": len(data)}
        )
        try:
            report = disassemble_file(
                service, SimpleNamespace(target_hardware="a1200-ffs"), "Code", None, None
            )
        finally:
            folder.cleanup()
        self.assertTrue(
            any("Hello world" in str(row.get("comment") or "") for row in report["rows"]),
            report["rows"][:6],
        )

    def test_disassembly_assigns_semantic_routine_and_flow_labels(self):
        """A called routine gets a purpose label; a backwards branch is a loop."""
        # BSR.B *+6 ; RTS ; NOP ; MOVEA.L $4.W,A6 ; BRA.B *-2 ; RTS
        data = bytes.fromhex("61044e754e712c78000460fe4e75")
        folder, service = self.service_with_file(
            data, {"protection": 0, "comment": "", "length": len(data)}
        )
        try:
            report = disassemble_file(
                service, SimpleNamespace(target_hardware="a1200-ffs"), "Code", None, None
            )
        finally:
            folder.cleanup()
        labels = [str(row.get("label") or "") for row in report["rows"]]
        self.assertTrue(any(label for label in labels), report["rows"])
        self.assertTrue(
            any(label.startswith(("loop", "sub_", "subroutine", "call_", "access_")) for label in labels),
            labels,
        )

    def test_basic_save_retokenises_and_preserves_amiga_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = DiskService(root / "work")
            session = service.create_blank("adf", "Editor")
            source = root / "Program"
            source.write_bytes(tokenise('10 PRINT "OLD"'))
            service.put(session, "Program", source)
            service.set_access(session, ["Program"], writable=False)
            before = inspect_editable_file(service, session, "Program", None)

            save_editor_text(
                service, session, "Program", None,
                '10 PRINT "NEW"\n20 GOTO 10', True, before["sha256"],
            )

            self.assertIn(
                '10 PRINT "NEW"',
                detokenise(service.read_file(session, "Program")),
            )
            metadata = service.file_metadata(session, "Program")
            # The protection bits survive the retokenised write: a locked file
            # stays write and delete protected.
            self.assertTrue(metadata["access"] & 0x04, metadata)
            self.assertTrue(metadata["access"] & 0x01, metadata)

    def test_basic_save_preserves_a_trailing_binary_payload(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = DiskService(root / "work")
            session = service.create_blank("adf", "EDITOR")
            payload = bytes.fromhex("A90020EEFF60") + b"PAYLOAD\x00"
            source = root / "PROGRAM"
            source.write_bytes(tokenise('10 PRINT "OLD"') + payload)
            service.put(session, "PROGRAM", source, "----r-e-")
            before = inspect_editable_file(service, session, "$.PROGRAM", None)

            self.assertTrue(before["editable"])
            self.assertTrue(before["basic"]["compound"])
            self.assertEqual(before["basic"]["trailingBytes"], len(payload))
            save_editor_text(
                service, session, "$.PROGRAM", None,
                '10 PRINT "NEW"\n20 END', True, before["sha256"],
            )

            stored = service.read_file(session, "$.PROGRAM")
            self.assertTrue(stored.endswith(payload))
            self.assertIn('10 PRINT "NEW"', detokenise(stored[:-len(payload)]))

    def test_save_as_creates_a_sibling_with_content_and_metadata(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = DiskService(root / "work")
            session = service.create_blank("adf", "Editor")
            source = root / "Program"
            source.write_bytes(tokenise('10 PRINT "OLD"'))
            service.put(session, "Program", source)
            service.set_access(session, ["Program"], writable=False)
            before = inspect_editable_file(service, session, "Program", None)

            _image, saved_path = save_editor_text_as(
                service, session, "Program", None, "Copy",
                '10 PRINT "NEW"', True, before["sha256"],
            )

            self.assertEqual(saved_path, "Copy")
            self.assertIn('10 PRINT "NEW"', detokenise(service.read_file(session, "Copy")))
            metadata = service.file_metadata(session, "Copy")
            self.assertEqual(metadata["protection"], metadata["access"], metadata)
            # The original was locked, so the sibling is write and delete
            # protected too.
            self.assertTrue(metadata["access"] & 0x04, metadata)
            self.assertTrue(metadata["access"] & 0x01, metadata)

    def test_file_hex_write_is_fixed_size_and_stale_guarded(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = DiskService(root / "work")
            session = service.create_blank("adf", "EDITOR")
            source = root / "CODE"
            source.write_bytes(b"ABCDEF")
            service.put(session, "CODE", source)
            before = inspect_editable_file(service, session, "$.CODE", None)

            result = write_file_range(
                service, session, "$.CODE", None, before["sha256"],
                [{"offset": 1, "data": "7879"}], True,
            )

            self.assertEqual(result["written"], 2)
            self.assertEqual(service.read_file(session, "$.CODE"), b"AxyDEF")

    def test_file_properties_change_metadata_without_changing_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = DiskService(root / "work")
            session = service.create_blank("adf", "Editor")
            source = root / "Code"
            source.write_bytes(b"UNCHANGED")
            service.put(session, "Code", source)
            before = inspect_editable_file(service, session, "Code", None)

            update_file_properties(
                service, session, "Code", None, before["sha256"],
                protection="----r-e-", comment="Reviewed", writable=False,
            )

            self.assertEqual(service.read_file(session, "Code"), b"UNCHANGED")
            metadata = service.file_metadata(session, "Code")
            self.assertTrue(metadata["access"] & 0x04)
            self.assertEqual(metadata["comment"], "Reviewed")


if __name__ == "__main__":
    unittest.main()
