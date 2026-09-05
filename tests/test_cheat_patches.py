from __future__ import annotations

import unittest

from app.cheat_patches import CheatPatchError, apply_guarded_cheat_patch, build_guarded_cheat_patch
from app.checksum import sha256_bytes


class CheatPatchTests(unittest.TestCase):
    def document(self, source: bytes) -> dict:
        return {
            "sourceSha256": sha256_bytes(source),
            "offset": 2,
            "originalHex": "20 30",
            "replacementHex": "EA EA",
            "watchAddress": "&70",
            "title": "Test unlimited lives",
            "path": "$.GAME",
            "author": "Test engineer",
            "rationale": "The watched lives byte decreases only on two recorded player-death events.",
            "observations": [
                {"event": "First player death", "before": "3", "after": "2", "emulator": "FS-UAE"},
                {"event": "Second player death", "before": "2", "after": "1", "emulator": "FS-UAE"},
            ],
            "hardwareProfile": {"machine": "a600", "accelerated": False},
        }

    def test_patch_is_bound_to_hash_bytes_profile_and_two_events(self):
        source = bytes.fromhex("10 11 20 30 40")
        patch = build_guarded_cheat_patch(source, self.document(source))
        self.assertEqual(patch["sourceSha256"], sha256_bytes(source))
        self.assertEqual(patch["originalHex"], "2030")
        self.assertEqual(patch["replacementHex"], "EAEA")
        self.assertEqual(len(patch["observations"]), 2)
        self.assertEqual(apply_guarded_cheat_patch(source, patch), bytes.fromhex("10 11 EA EA 40"))

    def test_patch_refuses_one_event_or_a_changed_source(self):
        source = bytes.fromhex("10 11 20 30 40")
        document = self.document(source)
        document["observations"] = document["observations"][:1]
        with self.assertRaisesRegex(CheatPatchError, "two distinct"):
            build_guarded_cheat_patch(source, document)
        patch = build_guarded_cheat_patch(source, self.document(source))
        with self.assertRaisesRegex(CheatPatchError, "different exact file revision"):
            apply_guarded_cheat_patch(source + b"x", patch)


if __name__ == "__main__":
    unittest.main()
