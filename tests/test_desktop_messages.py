from __future__ import annotations

import unittest

import tempfile
from pathlib import Path

from desktop.__main__ import _desktop_message_text, _folder_selection


class _ScriptValue:
    def __init__(self, value) -> None:
        self.value = value

    def to_string(self):
        return self.value


class _LegacyJavascriptResult:
    def __init__(self, value) -> None:
        self.value = value

    def get_js_value(self):
        return self.value


class DesktopMessageTests(unittest.TestCase):
    def test_current_webkit_value_is_read_directly(self) -> None:
        self.assertEqual(
            "open-images:2",
            _desktop_message_text(_ScriptValue("open-images:2")),
        )

    def test_legacy_webkit_result_wrapper_remains_supported(self) -> None:
        self.assertEqual(
            '{"command":"open-plans","plans":[]}',
            _desktop_message_text(
                _LegacyJavascriptResult(
                    _ScriptValue('{"command":"open-plans","plans":[]}')
                )
            ),
        )

    def test_unsupported_message_object_is_rejected(self) -> None:
        with self.assertRaisesRegex(TypeError, "unsupported script message"):
            _desktop_message_text(object())

    def test_non_text_message_is_rejected(self) -> None:
        with self.assertRaisesRegex(TypeError, "non-text script message"):
            _desktop_message_text(_ScriptValue(42))


if __name__ == "__main__":
    unittest.main()


class FolderSelectionTests(unittest.TestCase):
    """What a chosen folder hands back to the page.

    WebKitGTK's file chooser has no concept of a directory, so a directory
    input in the Linux desktop host opens an ordinary file chooser that an
    operator cannot pick a folder in. Pointing the Workbench install at a
    collection folder therefore came back with whichever single file happened
    to be selected. The host answers that request itself now, and this is the
    part that decides what the page receives.
    """

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def test_every_file_below_the_folder_is_offered(self) -> None:
        """A TOSEC set is one file per disk, often in subdirectories."""
        (self.root / "Workbench").mkdir()
        (self.root / "Workbench" / "disk1.zip").write_bytes(b"one")
        (self.root / "Workbench" / "disk2.zip").write_bytes(b"two")
        (self.root / "loose.adf").write_bytes(b"three")

        selected = _folder_selection(self.root)

        # Sorted by full path, so a subdirectory's contents group together
        # rather than being interleaved with the files beside it.
        self.assertEqual(
            [str(Path(path).relative_to(self.root)) for path in selected],
            ["Workbench/disk1.zip", "Workbench/disk2.zip", "loose.adf"],
        )

    def test_directories_are_not_offered_as_files(self) -> None:
        (self.root / "Extras").mkdir()
        self.assertEqual(_folder_selection(self.root), [])

    def test_a_link_that_points_at_its_own_parent_does_not_run_away(self) -> None:
        """A collection folder linking to itself would be walked for ever."""
        (self.root / "real.adf").write_bytes(b"disk")
        (self.root / "loop").symlink_to(self.root, target_is_directory=True)

        selected = _folder_selection(self.root)

        self.assertEqual([Path(path).name for path in selected], ["real.adf"])

    def test_the_selection_is_bounded(self) -> None:
        """A whole collection drive must not be handed over in one go."""
        for index in range(12):
            (self.root / f"disk{index:02d}.adf").write_bytes(b"x")

        self.assertEqual(len(_folder_selection(self.root, limit=5)), 5)
