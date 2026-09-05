from __future__ import annotations

import unittest

from app.emulator_evidence import EmulatorEvidenceError, private_display_arguments


class EmulatorEvidenceTests(unittest.TestCase):
    def test_private_display_replaces_nested_xvfb_wrapper_and_extends_bound(self):
        source = [
            "timeout", "--signal=TERM", "--kill-after=2", "8", "env",
            "ALSOFT_DRIVERS=null", "xvfb-run", "-a", "/opt/fs-uae/fs-uae",
            "-disc", "/work/menu.adf",
        ]
        command = private_display_arguments(source, ":147", 14)
        self.assertEqual(command[3], "14")
        self.assertIn("DISPLAY=:147", command)
        self.assertNotIn("xvfb-run", command)
        self.assertEqual(source[3], "8", "The shared emulator command must not be mutated")

    def test_private_display_refuses_an_unmanaged_command(self):
        with self.assertRaisesRegex(EmulatorEvidenceError, "headless display"):
            private_display_arguments(["/usr/bin/emulator"], ":147")


if __name__ == "__main__":
    unittest.main()
