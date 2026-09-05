from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.errors import DiskError
from app.image_diff import compare_manifests, manifest_fingerprint
from app.image_patch import (
    PATCH_FORMAT,
    _protection_text,
    _selected_candidate_manifest,
    apply_patch_archive,
    inspect_patch_archive,
    write_patch_archive,
)


def manifest(identity: str, records: list[dict]) -> dict:
    return {
        "image": {"id": identity, "name": f"{identity}.adf", "kind": "ofs"},
        "records": records,
        "menus": [],
    }


class PatchService:
    def __init__(self, work_dir: Path):
        self.work_dir = work_dir

    def export_file(self, _session, path, _side=None):
        target = self.work_dir / f"export-{path.rsplit('/', 1)[-1]}"
        target.write_bytes({"NEW": b"new bytes", "CHANGED": b"changed bytes"}[path])
        return target


class ImagePatchTests(unittest.TestCase):
    def test_selective_candidate_automatically_includes_a_required_parent_directory(self) -> None:
        base = manifest("base", [])
        candidate = manifest("candidate", [
            {"recordType": "directory", "path": "Games", "size": 0},
            {"recordType": "file", "path": "Games/NEW", "size": 9, "sha256": "new"},
        ])
        comparison = compare_manifests(base, candidate)
        file_key = next(
            row["key"] for row in comparison["changes"]["added"]
            if row["after"]["recordType"] == "file"
        )

        derived, selection = _selected_candidate_manifest(
            "ffs", base, candidate, comparison, [file_key]
        )

        self.assertEqual(len(derived["records"]), 2)
        self.assertEqual(len(selection["automaticallyIncludedKeys"]), 1)

    def test_numeric_manifest_protection_is_rendered_as_hexadecimal(self) -> None:
        self.assertEqual(_protection_text(5), "5")
        self.assertEqual(_protection_text("00000005"), "00000005")

    def test_patch_archive_embeds_only_added_and_changed_payloads(self) -> None:
        base = manifest("base", [
            {"recordType": "file", "path": "CHANGED", "size": 3, "sha256": "old"},
            {"recordType": "file", "path": "$.REMOVED", "size": 4, "sha256": "gone"},
        ])
        candidate = manifest("candidate", [
            {"recordType": "file", "path": "CHANGED", "size": 13, "sha256": "changed"},
            {"recordType": "file", "path": "NEW", "size": 9, "sha256": "new"},
        ])
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "change.affpatch.zip"
            service = PatchService(Path(folder))
            sessions = [SimpleNamespace(kind="ofs"), SimpleNamespace(kind="ofs")]
            with patch("app.image_patch.build_manifest", side_effect=[base, candidate]):
                document = write_patch_archive(service, *sessions, destination)
            self.assertEqual(document["format"], PATCH_FORMAT)
            self.assertEqual(document["summary"]["total"], 3)
            with zipfile.ZipFile(destination) as archive:
                self.assertEqual(
                    sorted(name for name in archive.namelist() if name.startswith("payloads/")),
                    ["payloads/00000000.bin", "payloads/00000001.bin"],
                )
                stored = json.loads(archive.read("patch.json"))
                self.assertEqual(len(stored["operations"]), 3)
                self.assertEqual(stored["candidateRecords"], candidate["records"])
                self.assertEqual(stored["layout"], {"kind": "ofs", "doubleSided": False})

    def test_patch_creation_reports_catalogue_payload_and_completion_progress(self) -> None:
        base = manifest("base", [])
        candidate = manifest("candidate", [
            {"recordType": "file", "path": "NEW", "size": 9, "sha256": "candidate"},
        ])
        updates = []
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "progress.affpatch.zip"
            service = PatchService(Path(folder))
            sessions = [SimpleNamespace(kind="ofs"), SimpleNamespace(kind="ofs")]
            with patch("app.image_patch.build_manifest", side_effect=[base, candidate]):
                write_patch_archive(
                    service,
                    *sessions,
                    destination,
                    lambda *values: updates.append(values),
                )

        messages = [update[0] for update in updates]
        self.assertTrue(any(message.startswith("Cataloguing base image") for message in messages))
        self.assertIn("Compressing NEW", messages)
        self.assertEqual(updates[-1], ("Guarded patch archive is ready", 1, 1))

    def test_patch_expands_a_proven_rename_into_guarded_remove_and_add_operations(self) -> None:
        base = manifest("base", [
            {"recordType": "file", "path": "OLD", "size": 9, "sha256": "same"},
        ])
        candidate = manifest("candidate", [
            {"recordType": "file", "path": "NEW", "size": 9, "sha256": "same"},
        ])
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "rename.affpatch.zip"
            service = PatchService(Path(folder))
            sessions = [SimpleNamespace(kind="ofs"), SimpleNamespace(kind="ofs")]
            with patch("app.image_patch.build_manifest", side_effect=[base, candidate]):
                document = write_patch_archive(service, *sessions, destination)
            with patch("app.image_patch.build_manifest", return_value=base):
                inspected = inspect_patch_archive(service, sessions[0], destination)

        self.assertEqual(document["summary"]["renamed"], 1)
        self.assertEqual([item["action"] for item in document["operations"]], ["removed", "added"])
        self.assertEqual(inspected["operationCount"], 2)

    def test_selective_patch_derives_and_verifies_its_own_candidate_fingerprint(self) -> None:
        base = manifest("base", [
            {"recordType": "file", "path": "CHANGED", "size": 3, "sha256": "old"},
        ])
        candidate = manifest("candidate", [
            {"recordType": "file", "path": "CHANGED", "size": 13, "sha256": "changed"},
            {"recordType": "file", "path": "NEW", "size": 9, "sha256": "new"},
        ])
        changed_key = compare_manifests(base, candidate)["changes"]["modified"][0]["key"]
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "selected.affpatch.zip"
            service = PatchService(Path(folder))
            sessions = [SimpleNamespace(kind="ofs"), SimpleNamespace(kind="ofs")]
            with patch("app.image_patch.build_manifest", side_effect=[base, candidate]):
                document = write_patch_archive(
                    service, *sessions, destination, selected_keys=[changed_key]
                )
            with patch("app.image_patch.build_manifest", return_value=base):
                inspected = inspect_patch_archive(service, sessions[0], destination)

        self.assertEqual(document["summary"]["total"], 1)
        self.assertEqual(document["selection"]["requestedKeys"], [changed_key])
        self.assertEqual(len(document["candidateRecords"]), 1)
        self.assertEqual(inspected["operationCount"], 1)

    def test_wrong_base_fingerprint_is_rejected_before_mutation(self) -> None:
        current = manifest("current", [{"recordType": "file", "path": "$.A", "sha256": "a", "size": 1}])
        document = {
            "format": PATCH_FORMAT,
            "version": 1,
            "kind": "ofs",
            "baseFingerprint": "0" * 64,
            "candidateFingerprint": manifest_fingerprint(current),
            "operations": [],
        }
        with tempfile.TemporaryDirectory() as folder:
            archive_path = Path(folder) / "wrong.zip"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("patch.json", json.dumps(document))
            service = PatchService(Path(folder))
            with patch("app.image_patch.build_manifest", return_value=current):
                with self.assertRaisesRegex(DiskError, "exact base revision"):
                    apply_patch_archive(service, SimpleNamespace(kind="ofs"), archive_path)

    def test_patch_creation_rejects_different_ofs_side_layouts(self) -> None:
        base = manifest("base", [])
        candidate = manifest("candidate", [])
        base["image"]["doubleSided"] = False
        candidate["image"]["doubleSided"] = True
        with tempfile.TemporaryDirectory() as folder:
            with patch("app.image_patch.build_manifest", side_effect=[base, candidate]):
                with self.assertRaisesRegex(DiskError, "matching OFS side layouts"):
                    write_patch_archive(
                        PatchService(Path(folder)),
                        SimpleNamespace(kind="ofs"),
                        SimpleNamespace(kind="ofs"),
                        Path(folder) / "wrong-layout.zip",
                    )

    def test_patch_inspection_verifies_payloads_without_mutating(self) -> None:
        base = manifest("base", [])
        candidate = manifest("candidate", [
            {"recordType": "file", "path": "NEW", "size": 9, "sha256": "candidate"},
        ])
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "inspect.affpatch.zip"
            service = PatchService(Path(folder))
            sessions = [SimpleNamespace(kind="ofs"), SimpleNamespace(kind="ofs")]
            with patch("app.image_patch.build_manifest", side_effect=[base, candidate]):
                write_patch_archive(service, *sessions, destination)
            with patch("app.image_patch.build_manifest", return_value=base):
                report = inspect_patch_archive(service, sessions[0], destination)
            self.assertTrue(report["compatible"])
            self.assertEqual(report["operationCount"], 1)
            self.assertEqual(report["payloadCount"], 1)
            self.assertEqual(report["payloadBytes"], len(b"new bytes"))

    def test_patch_inspection_rejects_a_corrupt_payload(self) -> None:
        base = manifest("base", [])
        candidate = manifest("candidate", [
            {"recordType": "file", "path": "NEW", "size": 9, "sha256": "candidate"},
        ])
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / "original.affpatch.zip"
            corrupt = Path(folder) / "corrupt.affpatch.zip"
            service = PatchService(Path(folder))
            sessions = [SimpleNamespace(kind="ofs"), SimpleNamespace(kind="ofs")]
            with patch("app.image_patch.build_manifest", side_effect=[base, candidate]):
                write_patch_archive(service, *sessions, original)
            with zipfile.ZipFile(original) as source, zipfile.ZipFile(corrupt, "w") as target:
                for item in source.infolist():
                    content = b"damaged" if item.filename.startswith("payloads/") else source.read(item)
                    target.writestr(item.filename, content)
            with patch("app.image_patch.build_manifest", return_value=base):
                with self.assertRaisesRegex(DiskError, "failed its SHA-256 check"):
                    inspect_patch_archive(service, sessions[0], corrupt)

    def test_patch_inspection_rejects_an_operation_that_does_not_match_the_candidate(self) -> None:
        base = manifest("base", [])
        candidate = manifest("candidate", [
            {"recordType": "file", "path": "NEW", "size": 9, "sha256": "candidate"},
        ])
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / "original.affpatch.zip"
            altered = Path(folder) / "altered.affpatch.zip"
            service = PatchService(Path(folder))
            sessions = [SimpleNamespace(kind="ofs"), SimpleNamespace(kind="ofs")]
            with patch("app.image_patch.build_manifest", side_effect=[base, candidate]):
                write_patch_archive(service, *sessions, original)
            with zipfile.ZipFile(original) as source, zipfile.ZipFile(altered, "w") as target:
                document = json.loads(source.read("patch.json"))
                document["operations"][0]["after"]["path"] = "$.MISLEADING"
                for item in source.infolist():
                    content = json.dumps(document) if item.filename == "patch.json" else source.read(item)
                    target.writestr(item.filename, content)
            with patch("app.image_patch.build_manifest", return_value=base):
                with self.assertRaisesRegex(DiskError, "operation plan"):
                    inspect_patch_archive(service, sessions[0], altered)


if __name__ == "__main__":
    unittest.main()
