import unittest

from app.cheat_analysis import analyse_basic, analyse_disassembly, cheat_report, disassembly_diagnostics


class CheatAnalysisTests(unittest.TestCase):
    def test_basic_finds_semantic_state_and_direct_memory_writes(self):
        findings = analyse_basic("10 lives%=3\n20 lives%=lives%-1\n30 IF lives%=0 THEN GOTO 100\n40 ?&70=lives%")
        self.assertTrue(any(row["category"] == "lives" and row["confidence"] == "strong" for row in findings))
        self.assertTrue(any("&70" in row["evidence"] for row in findings))

    def test_basic_does_not_treat_memory_reads_as_pokes(self):
        findings = analyse_basic("10 A%=?&70\n20 PRINT ?&71")
        self.assertFalse(any(row["category"] == "memory-write" for row in findings))

    def test_basic_rejects_unexplained_memory_write(self):
        self.assertEqual(analyse_basic("10 ?&70=3"), [])

    def test_basic_rejects_opaque_loop_counter(self):
        findings = analyse_basic("10 L%=3\n20 L%=L%-1\n30 IF L%=0 THEN GOTO 100")
        self.assertEqual(findings, [])

    def test_basic_opaque_counter_requires_semantic_terminal_path(self):
        findings = analyse_basic('10 L%=3\n20 L%=L%-1\n30 IF L%=0 THEN GOTO 100\n100 PRINT "GAME OVER"')
        candidate = next(row for row in findings if "L%" in row["summary"])
        self.assertEqual(candidate["category"], "lives")
        self.assertIn("terminal path", candidate["evidence"])

    def test_basic_recognises_trainer_opcode_writes(self):
        findings = analyse_basic("10 ?&3456=&EA")
        self.assertTrue(any(row["category"] == "code-patch" and "NOP" in row["summary"] for row in findings))

    def test_disassembly_links_initialised_state_change_to_terminal_branch(self):
        findings = analyse_disassembly({"rows": [
            {"address": 0x2000, "mnemonic": "LDA", "operand": "#3"},
            {"address": 0x2002, "mnemonic": "STA", "operand": "&70"},
            {"address": 0x2004, "mnemonic": "DEC", "operand": "&70"},
            {"address": 0x2006, "mnemonic": "BEQ", "operand": "&2010", "target": 0x2010},
            {"address": 0x2010, "mnemonic": "RTS", "operand": ""},
        ]})
        self.assertEqual(findings[0]["confidence"], "likely")
        self.assertIn("&70", findings[0]["summary"])
        self.assertEqual(findings[0]["navigation"], {"kind": "disassembly", "address": 0x2004, "offset": 0})

    def test_disassembly_rejects_backward_decrement_loop(self):
        findings = analyse_disassembly({"rows": [
            {"address": 0x2000, "mnemonic": "DEC", "operand": "&70"},
            {"address": 0x2002, "mnemonic": "BNE", "operand": "&2000", "target": 0x2000},
        ]})
        self.assertEqual(findings, [])

    def test_disassembly_keeps_reachable_unlabelled_forward_state_as_possible(self):
        findings = analyse_disassembly({"rows": [
            {"address": 0x2000, "mnemonic": "DEC", "operand": "&70", "reachable": True},
            {"address": 0x2002, "mnemonic": "BEQ", "operand": "&2010", "target": 0x2010, "reachable": True},
            {"address": 0x2004, "mnemonic": "RTS", "operand": "", "reachable": True},
            {"address": 0x2010, "mnemonic": "RTS", "operand": "", "reachable": True},
        ]})
        self.assertEqual(findings[0]["confidence"], "possible")

    def test_disassembly_ignores_unreachable_decoded_data(self):
        findings = analyse_disassembly({"rows": [
            {"address": 0x2000, "mnemonic": "RTS", "operand": "", "reachable": True},
            {"address": 0x2001, "mnemonic": "DEC", "operand": "&70", "reachable": False},
            {"address": 0x2003, "mnemonic": "BEQ", "operand": "&2010", "target": 0x2010, "reachable": False},
        ]})
        self.assertEqual(findings, [])

    def test_disassembly_diagnoses_runtime_payload(self):
        rows = [{"address": 0x2000 + index, "mnemonic": "DC.B", "operand": "$00", "reachable": index == 0}
                for index in range(200)]
        diagnostics = disassembly_diagnostics({"rows": rows})
        self.assertTrue(any(item["kind"] == "packed" for item in diagnostics))

    def test_disassembly_accepts_semantically_labelled_multibyte_lives_counter(self):
        findings = analyse_disassembly({"rows": [
            {"address": 0x2100, "mnemonic": "DEC", "operand": "&80", "label": "lives_units"},
            {"address": 0x2102, "mnemonic": "BPL", "operand": "&2110", "target": 0x2110},
            {"address": 0x2104, "mnemonic": "LDA", "operand": "#9"},
            {"address": 0x2106, "mnemonic": "STA", "operand": "&80"},
            {"address": 0x2108, "mnemonic": "DEC", "operand": "&81", "label": "lives_tens"},
        ]})
        self.assertTrue(any(row["category"] == "lives" for row in findings))

    def test_disassembly_correlates_load_subtract_store_and_branch(self):
        findings = analyse_disassembly({"rows": [
            {"address": 0x2FF0, "mnemonic": "LDA", "operand": "#3"},
            {"address": 0x2FF2, "mnemonic": "STA", "operand": "&71"},
            {"address": 0x3000, "mnemonic": "LDA", "operand": "&71"},
            {"address": 0x3002, "mnemonic": "SEC", "operand": ""},
            {"address": 0x3003, "mnemonic": "SBC", "operand": "#&01"},
            {"address": 0x3005, "mnemonic": "STA", "operand": "&71"},
            {"address": 0x3007, "mnemonic": "BEQ", "operand": "&3020", "target": 0x3020},
        ]})
        self.assertTrue(any("Load, subtract one and store" in row["summary"] for row in findings))

    def test_disassembly_ignores_unlabelled_hardware_counter_write(self):
        findings = analyse_disassembly({"rows": [
            {"address": 0x4000, "mnemonic": "DEC", "operand": "&FE40"},
            {"address": 0x4003, "mnemonic": "BNE", "operand": "&4000"},
        ]})
        self.assertEqual(findings, [])

    def test_disassembly_understands_decimal_and_hex_immediates(self):
        decimal = analyse_disassembly({"rows": [
            {"address": 0x4100, "mnemonic": "LDA", "operand": "&72", "comment": "lives"},
            {"address": 0x4102, "mnemonic": "SBC", "operand": "#1"},
            {"address": 0x4104, "mnemonic": "STA", "operand": "&72"},
        ]})
        hexadecimal = analyse_disassembly({"rows": [
            {"address": 0x4200, "mnemonic": "LDA", "operand": "&73", "comment": "energy"},
            {"address": 0x4202, "mnemonic": "SBC", "operand": "#$01"},
            {"address": 0x4204, "mnemonic": "STA", "operand": "&73"},
        ]})
        self.assertTrue(any("Load, subtract one and store" in row["summary"] for row in decimal))
        self.assertTrue(any("Load, subtract one and store" in row["summary"] for row in hexadecimal))

    def test_immediate_value_is_not_mistaken_for_store_address(self):
        findings = analyse_disassembly({"rows": [
            {"address": 0x4300, "mnemonic": "LDA", "operand": "&74", "comment": "player_lives"},
            {"address": 0x4302, "mnemonic": "SBC", "operand": "#&01"},
            {"address": 0x4304, "mnemonic": "STA", "operand": "&74"},
        ]})
        candidate = next(row for row in findings if "Load, subtract one and store" in row["summary"])
        self.assertIn("&74", candidate["summary"])

    def test_semantic_matching_does_not_confuse_sometimes_with_time(self):
        findings = analyse_basic("10 sometimes%=3\n20 sometimes%=sometimes%-1\n30 IF sometimes%=0 THEN END")
        self.assertEqual(findings, [])

    def test_report_is_explicitly_read_only(self):
        report = cheat_report(path="$.GAME", kind="basic", findings=[], title="Frak", machine="Amiga 500")
        self.assertTrue(report["readOnly"])
        self.assertEqual(len(report["referenceSearches"]), 4)


if __name__ == "__main__":
    unittest.main()
