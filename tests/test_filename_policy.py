from __future__ import annotations

from types import SimpleNamespace
import unittest

from app.analysis_service import preflight_report
from app.disk_service import DiskService
from app.filename_policy import target_name_policy


class FilenamePolicyTests(unittest.TestCase):
    def test_kickstart_preflight_and_mutation_share_one_module_name_policy(self):
        session = SimpleNamespace(
            kind="kickfs", name="kick31.rom", hardware_profile={}, ffs_capabilities={}
        )
        long_name = "a" * 61
        report = preflight_report(None, session, {
            "targetKind": "kickfs",
            "changes": [{"name": long_name, "nameIsLeaf": True}],
        })

        self.assertEqual(report["items"][0]["targetName"], "a" * 60)
        with self.assertRaisesRegex(Exception, "at most 60"):
            DiskService.validate_leaf_name(session, long_name)

    def test_a_period_is_not_rewritten_by_preflight(self):
        """``exec.library`` and ``Disk.info`` are ordinary Amiga names."""
        session = SimpleNamespace(
            kind="kickfs", name="kick31.rom", hardware_profile={}, ffs_capabilities={}
        )
        report = preflight_report(None, session, {
            "targetKind": "kickfs",
            "changes": [{"name": "exec.library", "nameIsLeaf": True}],
        })

        self.assertEqual(report["items"][0]["targetName"], "exec.library")
        self.assertEqual(
            DiskService.validate_leaf_name(session, "exec.library"), "exec.library"
        )

    def test_ffs_allocator_preserves_legal_spaces_and_resolves_collisions(self):
        policy = target_name_policy("ffs", name_limit=30)

        self.assertEqual(policy.allocate("Elite II", []), "Elite II")
        self.assertEqual(policy.allocate("Elite II", ["elite ii"]), "Elite II1")
        # A full stop is legal, so a name carrying one keeps it.
        self.assertEqual(policy.allocate("Disk.info", []), "Disk.info")

    def test_amiga_names_reject_unrepresentable_and_edge_whitespace(self):
        policy = target_name_policy("ffs", name_limit=30)

        with self.assertRaisesRegex(Exception, "Latin-1"):
            policy.validate("Elite🙂")
        with self.assertRaisesRegex(Exception, "start or end"):
            policy.validate(" Elite")
        self.assertEqual(policy.normalise("Café"), "Café")

    def test_short_name_allocator_never_exceeds_its_limit(self):
        policy = target_name_policy("ffs", name_limit=1)
        used = ["A", *"123456789"]

        allocated = policy.allocate("A", used)

        self.assertEqual(len(allocated), 1)
        self.assertNotIn(allocated.casefold(), {name.casefold() for name in used})

    def test_preflight_only_reports_collisions_within_the_same_parent(self):
        session = SimpleNamespace(
            kind="ffs", name="files.adf", hardware_profile={},
            ffs_capabilities={"nameLimit": 30},
        )
        changes = [
            {"name": "ReadMe", "nameIsLeaf": True, "parent": "One"},
            {"name": "ReadMe", "nameIsLeaf": True, "parent": "Two"},
        ]

        report = preflight_report(None, session, {"changes": changes})

        self.assertTrue(report["canProceed"])

    def test_hdf_slot_preflight_uses_the_rdb_device_name_limit(self):
        session = SimpleNamespace(
            kind="hdf", name="games.hdf", hardware_profile={}, ffs_capabilities={}
        )

        report = preflight_report(None, session, {
            "targetKind": "hdf",
            "changes": [
                {"name": "A" * 32, "nameIsLeaf": True, "type": "disk image"}
            ],
        })

        # An RDB device name is a 31-character BSTR.
        self.assertEqual(report["items"][0]["targetName"], "A" * 31)


if __name__ == "__main__":
    unittest.main()
