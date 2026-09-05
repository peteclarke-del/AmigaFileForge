from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.desktop_state import DesktopClientState
from app.errors import DiskError


class DesktopClientStateTests(unittest.TestCase):
    def test_preferences_and_collection_survive_new_store_instances(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "client-state.json"
            DesktopClientState(path).update(
                local_storage={"theme": "dark"},
                collection={"images": [{"id": "one"}], "settings": {"wanted": []}},
            )
            restored = DesktopClientState(path).read()

        self.assertEqual(restored["localStorage"], {"theme": "dark"})
        self.assertEqual(restored["collection"]["images"], [{"id": "one"}])

    def test_oversized_preferences_are_rejected_without_truncation(self):
        with TemporaryDirectory() as temporary:
            state = DesktopClientState(Path(temporary) / "client-state.json")

            with self.assertRaisesRegex(DiskError, "safe size limit"):
                state.update(local_storage={"draft": "x" * (2 * 1024 * 1024 + 1)})

            self.assertEqual(state.read()["localStorage"], {})

    def test_collection_shape_is_validated(self):
        with TemporaryDirectory() as temporary:
            state = DesktopClientState(Path(temporary) / "client-state.json")

            with self.assertRaisesRegex(DiskError, "incomplete"):
                state.update(collection={"images": []})

    def test_non_utf8_store_reports_a_recoverable_error(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "client-state.json"
            path.write_bytes(b"\xff")

            with self.assertRaisesRegex(DiskError, "could not be read"):
                DesktopClientState(path).read()

    def test_unknown_store_version_is_not_silently_overwritten(self):
        with TemporaryDirectory() as temporary:
            path = Path(temporary) / "client-state.json"
            path.write_text('{"version":99}', encoding="utf-8")

            with self.assertRaisesRegex(DiskError, "version 99"):
                DesktopClientState(path).update(local_storage={"theme": "dark"})

            self.assertEqual(path.read_text(encoding="utf-8"), '{"version":99}')


if __name__ == "__main__":
    unittest.main()
