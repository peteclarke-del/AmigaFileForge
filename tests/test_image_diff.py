from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.image_diff import compare_images, compare_manifests, manifest_fingerprint, record_key


def manifest(name: str, kind: str, records: list[dict]) -> dict:
    return {"image": {"id": name, "name": name, "kind": kind}, "records": records, "menus": []}


class ImageDiffTests(unittest.TestCase):
    def test_record_keys_include_side_and_bank_context(self) -> None:
        self.assertEqual(record_key({"recordType": "file", "path": "Game"}), "side::file:game")
        self.assertEqual(
            record_key({"recordType": "file", "side": 2, "path": "Game"}),
            "side:2:file:game",
        )
        self.assertEqual(
            record_key({"recordType": "rom-bank", "bank": 3, "path": "bank:3"}),
            "bank:3:rom-bank:bank:3",
        )

    def test_fingerprint_ignores_record_order_and_session_identity(self) -> None:
        records = [
            {"recordType": "file", "path": "$.A", "sha256": "a", "size": 1},
            {"recordType": "file", "path": "$.B", "sha256": "b", "size": 2},
        ]
        first = manifest("first", "ffs", records)
        second = manifest("second", "ffs", list(reversed(records)))
        self.assertEqual(manifest_fingerprint(first), manifest_fingerprint(second))

    def test_compare_classifies_content_and_metadata_changes(self) -> None:
        base = manifest("old", "ofs", [
            {"recordType": "file", "path": "$.SAME", "sha256": "1", "size": 10, "load": "4096"},
            {"recordType": "file", "path": "$.CONTENT", "sha256": "2", "size": 20},
            {"recordType": "file", "path": "$.META", "sha256": "3", "size": 30, "execute": "4096"},
            {"recordType": "file", "path": "$.REMOVED", "sha256": "4", "size": 40},
        ])
        candidate = manifest("new", "ofs", [
            {"recordType": "file", "path": "$.SAME", "sha256": "1", "size": 10, "load": "4096"},
            {"recordType": "file", "path": "$.CONTENT", "sha256": "changed", "size": 20},
            {"recordType": "file", "path": "$.META", "sha256": "3", "size": 30, "execute": "8192"},
            {"recordType": "file", "path": "$.ADDED", "sha256": "5", "size": 50},
        ])

        report = compare_manifests(base, candidate)

        self.assertEqual(report["summary"], {
            "added": 1, "removed": 1, "renamed": 0,
            "modified": 1, "metadata": 1, "total": 4,
        })
        self.assertEqual(report["changes"]["modified"][0]["changedFields"], ["sha256"])
        self.assertEqual(report["changes"]["metadata"][0]["changedFields"], ["execute"])
        self.assertTrue(report["sameFormat"])

    def test_compare_classifies_a_unique_same_content_file_as_renamed(self) -> None:
        base = manifest("old", "ffs", [
            {"recordType": "file", "path": "$.OLD", "sha256": "same", "size": 10, "load": "4096"},
        ])
        candidate = manifest("new", "ffs", [
            {"recordType": "file", "path": "$.NEW", "sha256": "same", "size": 10, "load": "4096"},
        ])

        report = compare_manifests(base, candidate)

        self.assertEqual(report["summary"]["renamed"], 1)
        self.assertEqual(report["summary"]["added"], 0)
        self.assertEqual(report["summary"]["removed"], 0)
        self.assertEqual(report["changes"]["renamed"][0]["changedFields"], ["path"])

    def test_compare_does_not_guess_between_duplicate_rename_candidates(self) -> None:
        base = manifest("old", "ffs", [
            {"recordType": "file", "path": "$.ONE", "sha256": "same", "size": 10},
            {"recordType": "file", "path": "$.TWO", "sha256": "same", "size": 10},
        ])
        candidate = manifest("new", "ffs", [
            {"recordType": "file", "path": "$.THREE", "sha256": "same", "size": 10},
            {"recordType": "file", "path": "$.FOUR", "sha256": "same", "size": 10},
        ])

        report = compare_manifests(base, candidate)

        self.assertEqual(report["summary"]["renamed"], 0)
        self.assertEqual(report["summary"]["added"], 2)
        self.assertEqual(report["summary"]["removed"], 2)

    def test_directory_allocation_changes_are_derived_not_logical_changes(self) -> None:
        base = manifest("old", "ffs", [
            {"recordType": "directory", "path": "$.Games", "size": 2048, "fileCount": 1, "attributes": "WR/"},
        ])
        candidate = manifest("new", "ffs", [
            {"recordType": "directory", "path": "$.Games", "size": 4096, "fileCount": 12, "attributes": "WR/"},
        ])
        self.assertEqual(compare_manifests(base, candidate)["summary"]["total"], 0)
        self.assertEqual(manifest_fingerprint(base), manifest_fingerprint(candidate))

    def test_image_comparison_reports_each_catalogue_phase(self) -> None:
        base = manifest("old", "ofs", [])
        candidate = manifest("new", "ofs", [])
        updates = []
        sessions = [
            SimpleNamespace(kind="ofs", name="old.adf"),
            SimpleNamespace(kind="ofs", name="new.adf"),
        ]

        with patch("app.image_diff.build_manifest", side_effect=[base, candidate]) as builder:
            report = compare_images(None, *sessions, lambda *values: updates.append(values))

        self.assertEqual(report["summary"]["total"], 0)
        self.assertEqual(builder.call_count, 2)
        self.assertTrue(all(call.args[2] is not None for call in builder.call_args_list))
        self.assertEqual(report["raw"]["components"], [])
        self.assertEqual(updates[-1], ("Image comparison complete", 3, 3))

    def test_image_comparison_joins_raw_component_ranges_to_logical_changes(self) -> None:
        base = manifest("old", "ofs", [])
        candidate = manifest("new", "ofs", [])
        with tempfile.TemporaryDirectory() as folder:
            left = Path(folder) / "left.adf"
            right = Path(folder) / "right.adf"
            left.write_bytes(b"CATALOGUE-A")
            right.write_bytes(b"CATALOGUE-B")
            sessions = [
                SimpleNamespace(kind="ofs", name=left.name, path=left, descriptor_path=None),
                SimpleNamespace(kind="ofs", name=right.name, path=right, descriptor_path=None),
            ]
            with patch("app.image_diff.build_manifest", side_effect=[base, candidate]):
                report = compare_images(None, *sessions)

        self.assertEqual(report["raw"]["changedBytes"], 1)
        self.assertEqual(report["raw"]["components"][0]["component"], "image")
        self.assertEqual(report["raw"]["components"][0]["ranges"], [[10, 10]])


if __name__ == "__main__":
    unittest.main()
