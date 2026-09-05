from __future__ import annotations

import json
import io
import shutil
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.headless import load_recipe
from app import cli
from app.disk_service import DiskService
from app.workflow_recipe import (
    WORKFLOW_PATCH_NAME,
    WORKFLOW_RECIPE_NAME,
    _workflow_readme,
    build_workflow_recipe_bundle,
)


class WorkflowRecipeTests(unittest.TestCase):
    def test_rebuild_guide_shell_quotes_image_descriptor_and_output_names(self):
        session = SimpleNamespace(name="My Games.hda")
        recipe = {
            "sources": {"image": {
                "name": "My Base.hda",
                "size": 10,
                "sha256": "a" * 64,
                "descriptor": {
                    "name": "My Base.geo", "size": 2, "sha256": "b" * 64,
                },
            }},
            "output": {"files": [
                {"name": "My Games.hda", "size": 10, "sha256": "c" * 64},
            ]},
        }

        readme = _workflow_readme(session, recipe, {"operations": []})

        self.assertIn("--source 'image=/path/to/My Base.hda'", readme)
        self.assertIn("--descriptor 'image=/path/to/My Base.geo'", readme)
        self.assertIn("--output 'My Games.hda'", readme)

    @unittest.skipUnless(shutil.which("disc"), "Amiganut CLI is required")
    def test_real_ofs_workflow_replays_to_the_recorded_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            service = DiskService(root / "work")
            session = service.create_blank("adf", "RECIPE")
            checkpoint = service.create_checkpoint(session, "Before import")
            payload = root / "PROGRAM"
            payload.write_bytes(b"PRINT \"REBUILT\"\r")
            service.put(session, "PROGRAM", payload)
            bundle = root / "workflow.affrecipe.zip"

            build_workflow_recipe_bundle(service, session, bundle)

            extracted = root / "recipe"
            extracted.mkdir()
            with zipfile.ZipFile(bundle) as archive:
                archive.extractall(extracted)
            checkpoint_image = (
                session.path.parent / "checkpoints" / checkpoint["id"] / "image.bin"
            )
            base = root / "base.adf"
            base.write_bytes(checkpoint_image.read_bytes())
            output = root / "rebuilt.adf"
            stdout = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                code = cli.main([
                    "recipe-run",
                    str(extracted / WORKFLOW_RECIPE_NAME),
                    "--source", f"image={base}",
                    "--source", f"changes={extracted / WORKFLOW_PATCH_NAME}",
                    "--output", str(output),
                ])

            result = json.loads(stdout.getvalue())
            self.assertEqual(code, cli.EXIT_OK, result)
            self.assertTrue(result["result"]["outputVerified"])
            recipe = load_recipe(extracted / WORKFLOW_RECIPE_NAME)
            self.assertEqual(output.stat().st_size, recipe["output"]["files"][0]["size"])

    def test_bundle_contains_guarded_patch_recipe_hashes_and_rebuild_guide(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            base_path = root / "base.adf"
            base_path.write_bytes(b"base")
            session = SimpleNamespace(
                name="games.adf",
                kind="ofs",
                path=root / "current.adf",
                descriptor_path=None,
                descriptor_name=None,
                target_hardware="a1200-ffs",
                hardware_profile={"machine": "a1200", "accelerated": False},
                compatibility_reports=[{"format": "amiga-file-forge-compatibility-report"}],
                hfe_original_path=None,
            )
            session.path.write_bytes(b"current")
            base_session = SimpleNamespace(
                kind="ofs", name="games.adf", path=base_path, descriptor_path=None,
            )
            service = SimpleNamespace(work_dir=root, discard_session=Mock())
            output = root / "workflow.zip"

            def write_patch(_service, _base, _candidate, destination, _progress):
                with zipfile.ZipFile(destination, "w") as archive:
                    archive.writestr("patch.json", "{}")
                return {"operations": [{"action": "modified"}]}

            identities = [
                {"name": "games.adf", "size": 4, "sha256": "a" * 64},
                {"name": WORKFLOW_PATCH_NAME, "size": 12, "sha256": "b" * 64},
            ]
            with (
                patch("app.workflow_recipe._checkpoint_source", return_value=(base_path, None, {"name": "games.adf", "targetHardware": "a1200-ffs"})),
                patch("app.workflow_recipe._open_snapshot", return_value=base_session),
                patch("app.workflow_recipe.write_patch_archive", side_effect=write_patch),
                patch("app.workflow_recipe.apply_patch_archive"),
                patch("app.workflow_recipe._save_replay_outputs", return_value=[{"name": "games.adf", "size": 7, "sha256": "c" * 64}]),
                patch("app.workflow_recipe.source_identity", side_effect=identities),
            ):
                report = build_workflow_recipe_bundle(service, session, output)

            self.assertEqual(report["baseCheckpoint"]["sha256"], "a" * 64)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {WORKFLOW_RECIPE_NAME, WORKFLOW_PATCH_NAME, "README.md"},
                )
                recipe = json.loads(archive.read(WORKFLOW_RECIPE_NAME))
                self.assertEqual(recipe["actions"], [{
                    "action": "apply-patch",
                    "source": "changes",
                    "targetHardware": "a1200-ffs",
                }])
                self.assertEqual(recipe["output"]["files"][0]["sha256"], "c" * 64)
                self.assertEqual(recipe["decisions"]["hardwareProfile"]["machine"], "a1200")
                self.assertIn("recipe-run", archive.read("README.md").decode("utf-8"))
                extracted = root / WORKFLOW_RECIPE_NAME
                extracted.write_bytes(archive.read(WORKFLOW_RECIPE_NAME))
            self.assertEqual(load_recipe(extracted)["actions"][0]["action"], "apply-patch")

    def test_dirty_session_without_checkpoint_is_rejected(self):
        from app.workflow_recipe import _checkpoint_source

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "disk.adf"
            path.write_bytes(b"image")
            session = SimpleNamespace(path=path, dirty=True)
            service = SimpleNamespace(oldest_checkpoint_snapshot=lambda _session: None)
            with self.assertRaisesRegex(Exception, "no retained pre-change checkpoint"):
                _checkpoint_source(service, session)


if __name__ == "__main__":
    unittest.main()
