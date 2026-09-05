from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from app.deployment_service import (
    DEPLOYMENT_FORMAT,
    _profile_findings,
    available_deployment_targets,
    build_deployment_archive,
    deployment_plan,
)
from app.disk_service import DiskError, DiskService
from app.image_session import ImageSession


class DeploymentDiskService(DiskService):
    """Exercise deployment packaging without requiring the Amiganut CLI."""

    def summary(self, session):
        return {
            "revision": f"{session.path.stat().st_size}:{session.path.stat().st_mtime_ns}",
            "hardDisk": False,
        }

    def prepare_download(self, session, progress=None):
        return session.path


class HardDriveDiskService(DeploymentDiskService):
    """A bare hardfile: one hard-drive-sized volume with no partition table."""

    def summary(self, session):
        return {**super().summary(session), "hardDisk": True}


def floppy_session(service: DiskService, name: str) -> ImageSession:
    path = service.work_dir / f"{name}.adf"
    path.write_bytes(bytes(200 * 1024))
    return ImageSession("a" * 32, path.name, "ofs", path)


def hardfile_session(service: DiskService, name: str) -> ImageSession:
    path = service.work_dir / f"{name}.hdf"
    path.write_bytes(bytes(4 * 1024 * 1024))
    return ImageSession("b" * 32, path.name, "ffs", path)


class DeploymentServiceTests(unittest.TestCase):
    def test_gotek_plan_uses_a_finalised_snapshot_without_mutating_source(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DeploymentDiskService(Path(folder) / "work")
            session = floppy_session(service, "ARCADIANS")
            original = session.path.read_bytes()
            revision = service.summary(session)["revision"]

            plan = deployment_plan(service, session, {
                "target": "gotek",
                "gotekMode": "native",
            })

            self.assertEqual(plan["format"], DEPLOYMENT_FORMAT)
            self.assertEqual(plan["source"]["revision"], revision)
            self.assertEqual(plan["entries"][0]["path"], "GOTEK-USB/ARCADIANS.adf")
            self.assertTrue(plan["canProceed"])
            self.assertEqual(session.path.read_bytes(), original)
            self.assertEqual(service.summary(session)["revision"], revision)

    def test_indexed_gotek_package_contains_config_manifest_and_readme(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DeploymentDiskService(Path(folder) / "work")
            session = floppy_session(service, "GAME")
            output = Path(folder) / "deployment.zip"
            plan = deployment_plan(service, session, {
                "target": "gotek", "gotekMode": "indexed", "startIndex": 12,
            })

            built = build_deployment_archive(service, session, {
                "target": "gotek",
                "gotekMode": "indexed",
                "startIndex": 12,
                "expectedRevision": plan["source"]["revision"],
            }, output)

            with zipfile.ZipFile(output) as archive:
                names = set(archive.namelist())
                manifest = json.loads(archive.read("Deployment/manifest.json"))
                config = archive.read("GOTEK-USB/FF.CFG").decode("ascii")
                readme = archive.read("README.md").decode("utf-8")
            self.assertIn("GOTEK-USB/DSKA0012_GAME.adf", names)
            self.assertIn("Deployment/compatibility-report.md", names)
            self.assertIn("nav-mode = indexed", config)
            self.assertEqual(manifest["target"], "gotek")
            self.assertEqual(built["source"]["revision"], plan["source"]["revision"])
            self.assertIn("## Recovery", readme)

    def test_package_rejects_a_revision_changed_after_review(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DeploymentDiskService(Path(folder) / "work")
            session = floppy_session(service, "GAME")
            output = Path(folder) / "deployment.zip"

            with self.assertRaisesRegex(DiskError, "changed after deployment review"):
                build_deployment_archive(service, session, {
                    "target": "gotek",
                    "gotekMode": "native",
                    "expectedRevision": "stale",
                }, output)

    def test_targets_explain_why_an_image_is_not_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DeploymentDiskService(Path(folder) / "work")
            session = floppy_session(service, "GAME")
            targets = {row["id"]: row for row in available_deployment_targets(service, session)}

            self.assertTrue(targets["gotek"]["available"])
            self.assertFalse(targets["hdf-card"]["available"])
            self.assertIn("hard-drive image", targets["hdf-card"]["reason"])

    def test_a_bare_hardfile_is_still_an_sd_card_target(self) -> None:
        """A .hdf with no Rigid Disk Block is the commonest AMIGA.HDF there is.

        It identifies as the single volume it holds rather than as a partition
        table, and the SD-card package copies the file as it stands, so nothing
        in the build needs an RDB. What is missing is the drive geometry, and
        that is reported as a warning rather than used to refuse the package.
        """
        with tempfile.TemporaryDirectory() as folder:
            service = HardDriveDiskService(Path(folder) / "work")
            session = hardfile_session(service, "SYSTEM")
            targets = {row["id"]: row for row in available_deployment_targets(service, session)}

            self.assertTrue(targets["hdf-card"]["available"], targets["hdf-card"]["reason"])
            self.assertTrue(targets["pistorm"]["available"], targets["pistorm"]["reason"])
            self.assertFalse(targets["gotek"]["available"])

            findings = _profile_findings(session, "hdf-card", has_partition_table=False)
            self.assertTrue(
                any("Rigid Disk Block" in item["message"] for item in findings), findings
            )


if __name__ == "__main__":
    unittest.main()
