from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import cli
from app.disk_service import DiskError
from app.headless import (
    RECIPE_FORMAT,
    RECIPE_VERSION,
    create_recipe,
    load_recipe,
    save_image,
    source_identity,
    verify_identity,
)


class CopyService:
    def prepare_download(self, session, progress=None):
        if progress:
            progress("Prepared", 1, 1)
        return session.path

    @staticmethod
    def _copy_local_file(source, destination):
        destination.write_bytes(source.read_bytes())


class FailingPairCopyService(CopyService):
    def __init__(self):
        self.copies = 0

    def _copy_local_file(self, source, destination):
        self.copies += 1
        if self.copies == 2:
            raise OSError("descriptor copy failed")
        super()._copy_local_file(source, destination)


class HeadlessCliTests(unittest.TestCase):
    def test_create_dry_run_has_stable_json_status(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with patch("app.cli.DiskService") as service_type:
            service = service_type.return_value
            service.create_blank.return_value = SimpleNamespace()
            service.summary.return_value = {"kind": "ofs", "name": "blank.adf"}
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = cli.main([
                    "create", "--format", "adf", "--title", "TEST",
                    "--output", "test.adf", "--dry-run",
                ])
        result = json.loads(stdout.getvalue())
        self.assertEqual(code, cli.EXIT_OK)
        self.assertEqual(result["format"], "amiga-file-forge-cli-result")
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["status"], "planned")
        self.assertTrue(result["dryRun"])
        self.assertEqual(result["result"]["format"], "adf")
        service.create_blank.assert_called_once()

    def test_output_cannot_replace_source_even_with_force(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "disk.adf"
            source.write_bytes(b"image")
            stdout = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                code = cli.main([
                    "save", str(source), "--output", str(source), "--force",
                ])
            result = json.loads(stdout.getvalue())
            self.assertEqual(code, cli.EXIT_VALIDATION)
            self.assertEqual(result["status"], "validation-failed")
            self.assertEqual(source.read_bytes(), b"image")

    def test_report_output_cannot_replace_image_even_with_force(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "disk.adf"
            source.write_bytes(b"image")
            stdout = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                code = cli.main([
                    "manifest", str(source), "--output", str(source), "--force",
                ])
            result = json.loads(stdout.getvalue())
            self.assertEqual(code, cli.EXIT_VALIDATION)
            self.assertIn("different from every source", result["result"]["error"])
            self.assertEqual(source.read_bytes(), b"image")

    def test_preflight_command_returns_shared_compatibility_schema(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image = root / "disk.adf"
            image.write_bytes(b"image")
            changes = root / "changes.json"
            changes.write_text(json.dumps([{"name": "LONG-FILENAME"}]), encoding="utf-8")
            session = SimpleNamespace(
                kind="ofs", name=image.name, hardware_profile={}, path=image,
            )
            opened = Mock()
            opened.__enter__ = Mock(return_value=(Mock(), session))
            opened.__exit__ = Mock(return_value=False)
            stdout = io.StringIO()
            with patch("app.cli.open_image", return_value=opened):
                with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                    code = cli.main([
                        "preflight", str(image), "--changes", str(changes),
                        "--source-kind", "ffs", "--target-kind", "ofs",
                    ])
            result = json.loads(stdout.getvalue())
            report = result["result"]
            self.assertEqual(code, cli.EXIT_OK)
            self.assertEqual(report["format"], "amiga-file-forge-compatibility-report")
            self.assertEqual(report["version"], 1)
            self.assertEqual(report["items"][0]["targetName"], "LONG-FILENAME")

    def test_usage_error_is_json_and_uses_documented_exit_code(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                cli.main([])
        result = json.loads(stdout.getvalue())
        self.assertEqual(raised.exception.code, cli.EXIT_USAGE)
        self.assertEqual(result["status"], "usage-error")
        self.assertEqual(result["exitCode"], cli.EXIT_USAGE)

    def test_recipe_round_trip_records_exact_source_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.bin"
            source.write_bytes(b"Amiga")
            identity = source_identity(source)
            recipe_path = root / "workflow.affrecipe.json"
            document = create_recipe(
                "Import one file",
                {"payload": identity},
                [{"action": "import-file", "source": "payload", "destination": "$.FILE"}],
                {"path": "result.adf"},
            )
            recipe_path.write_text(json.dumps(document), encoding="utf-8")

            loaded = load_recipe(recipe_path)

            self.assertEqual(loaded["format"], RECIPE_FORMAT)
            self.assertEqual(loaded["version"], RECIPE_VERSION)
            self.assertEqual(verify_identity(source, loaded["sources"]["payload"]), identity)

    def test_recipe_identity_rejects_changed_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source.bin"
            source.write_bytes(b"before")
            identity = source_identity(source)
            source.write_bytes(b"after")
            with self.assertRaisesRegex(DiskError, "expected size|expected sha256"):
                verify_identity(source, identity)

    def test_recipe_rejects_action_that_bypasses_source_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            recipe_path = Path(temporary) / "unsafe.json"
            recipe_path.write_text(json.dumps({
                "format": RECIPE_FORMAT,
                "version": RECIPE_VERSION,
                "name": "Unsafe",
                "sources": {},
                "actions": [{
                    "action": "import-file",
                    "source": "unchecked",
                    "destination": "$.FILE",
                }],
                "output": {"path": "result.adf", "files": []},
            }), encoding="utf-8")
            with self.assertRaisesRegex(DiskError, "unverified source alias"):
                load_recipe(recipe_path)

    def test_recipe_rejects_patch_action_that_bypasses_source_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            recipe_path = Path(temporary) / "unsafe-patch.json"
            recipe_path.write_text(json.dumps({
                "format": RECIPE_FORMAT,
                "version": RECIPE_VERSION,
                "name": "Unsafe patch",
                "sources": {
                    "image": {"size": 1, "sha256": "a" * 64},
                },
                "actions": [{"action": "apply-patch", "source": "unchecked"}],
                "output": {"path": "result.adf", "files": []},
            }), encoding="utf-8")
            with self.assertRaisesRegex(DiskError, "unverified source alias"):
                load_recipe(recipe_path)

    def test_recipe_output_identity_must_match_rebuild(self):
        document = {"output": {"files": [{"size": 10, "sha256": "a" * 64}]}}
        with self.assertRaisesRegex(cli.IdentityError, "does not match"):
            cli._verify_recipe_outputs(
                document,
                [{"size": 11, "sha256": "b" * 64}],
            )

    def test_save_image_writes_primary_and_matching_descriptor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            primary = root / "working.hda"
            descriptor = root / "working.geo"
            primary.write_bytes(b"HDA")
            descriptor.write_bytes(b"GEO")
            session = SimpleNamespace(path=primary, descriptor_path=descriptor)
            output = root / "out" / "scsi0.hda"

            files = save_image(CopyService(), session, output)

            self.assertEqual(output.read_bytes(), b"HDA")
            self.assertEqual(output.with_suffix(".geo").read_bytes(), b"GEO")
            self.assertEqual([Path(row["path"]).suffix for row in files], [".hda", ".geo"])

    def test_failed_pair_staging_leaves_no_partial_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            primary = root / "working.hda"
            descriptor = root / "working.geo"
            primary.write_bytes(b"HDA")
            descriptor.write_bytes(b"GEO")
            session = SimpleNamespace(path=primary, descriptor_path=descriptor)
            output = root / "out" / "scsi0.hda"
            with self.assertRaisesRegex(OSError, "descriptor copy failed"):
                save_image(FailingPairCopyService(), session, output)
            self.assertFalse(output.exists())
            self.assertFalse(output.with_suffix(".geo").exists())

    def test_failed_recipe_verification_does_not_publish_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            primary = root / "working.adf"
            primary.write_bytes(b"image")
            session = SimpleNamespace(path=primary, descriptor_path=None)
            output = root / "out" / "result.adf"
            with self.assertRaisesRegex(cli.IdentityError, "does not match"):
                save_image(
                    CopyService(), session, output,
                    verify=lambda files: cli._verify_recipe_outputs(
                        {"output": {"files": [{"size": 1, "sha256": "0" * 64}]}},
                        files,
                    ),
                )
            self.assertFalse(output.exists())

    def test_image_and_recipe_outputs_must_be_different(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            code = cli.main([
                "create", "--format", "adf", "--title", "TEST",
                "--output", "same.file", "--recipe-out", "same.file",
            ])
        result = json.loads(stdout.getvalue())
        self.assertEqual(code, cli.EXIT_VALIDATION)
        self.assertIn("must be different", result["result"]["error"])

    def test_recipe_records_image_interpretation_context(self):
        args = SimpleNamespace(target_hardware="hardfile", force_kind="rom")
        self.assertEqual(cli._recorded_open_context(args), {
            "targetHardware": "hardfile",
            "forceKind": "rom",
        })


if __name__ == "__main__":
    unittest.main()
