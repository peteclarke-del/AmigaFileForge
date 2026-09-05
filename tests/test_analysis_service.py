from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.analysis_service import (
    accept_compatibility_report,
    build_manifest,
    dependency_report,
    duplicate_report,
    health_report,
    inspect_file,
    workspace_metadata_records,
    preflight_report,
)
from app.disk_service import DiskError, DiskService, ImageSession
from app.operations import OperationCancelled


class AnalysisServiceTests(unittest.TestCase):
    def test_workspace_metadata_exposes_rom_symbols_regions_and_editor_comments(self) -> None:
        session = Mock(
            kind="rom",
            name="TOOLS.rom",
            rom_project={
                "identity": {"title": "Development Tools"},
                "symbols": {"32768": "service_entry"},
                "regions": [{"start": "40960", "end": "&80FF", "name": "Dispatch table"}],
            },
            editor_projects={
                "-|bank:0": {
                    "notes": "Reverse engineering notes",
                    "symbols": {"&8010": "command_dispatch"},
                    "comments": {"32": "Command parser"},
                },
            },
        )

        records = workspace_metadata_records(Mock(), session)

        self.assertTrue(any(row.get("resultType") == "rom-symbol" for row in records))
        self.assertTrue(any(row.get("resultType") == "rom-region" for row in records))
        comment = next(row for row in records if row.get("resultType") == "project-comment")
        self.assertEqual(comment["offset"], 32)
        self.assertEqual(comment["fileName"], "bank:0")
        saved_symbol = next(row for row in records if row.get("resultType") == "project-symbol")
        self.assertEqual(saved_symbol["offset"], 0x8010)

    def test_preflight_reports_target_truncation_and_collision(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "disk.adf"
            path.write_bytes(b"image")
            session = ImageSession("a" * 32, path.name, "ffs", path)

            # An Amiga name holds 30 characters, so these two collide only
            # after the limit trims them.
            # Both names are 31 characters and share their first 30, so the
            # limit trims them to the same target.
            long_name = "A" * 30
            report = preflight_report(
                DiskService(folder),
                session,
                {
                    "operation": "copy",
                    "changes": [{"name": long_name + "B"}, {"name": long_name + "C"}],
                },
            )

            self.assertFalse(report["canProceed"])
            self.assertEqual(report["format"], "amiga-file-forge-compatibility-report")
            self.assertEqual(report["version"], 1)
            self.assertEqual(report["items"][0]["sourceName"], long_name + "B")
            self.assertEqual(report["items"][0]["targetName"], long_name)
            self.assertTrue(any("clashes" in item["message"] for item in report["issues"]))
            self.assertIn("# Amiga File Forge compatibility report", report["markdown"])

    def test_a_drawer_copied_between_dos_types_loses_nothing(self) -> None:
        """Every AmigaDOS DOS type nests drawers, so this is an ordinary copy."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "disk.adf"
            path.write_bytes(b"image")
            session = ImageSession("b" * 32, path.name, "ofs", path)

            report = preflight_report(
                DiskService(folder),
                session,
                {
                    "operation": "copy",
                    "sourceKind": "ffs",
                    "targetKind": "ofs",
                    "changes": [{"name": "Games", "type": "directory"}],
                },
            )

            self.assertTrue(report["canProceed"])
            self.assertEqual(report["items"][0]["losses"], [])

    def test_a_workbench_icon_type_is_reported_lost_where_it_cannot_live(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "archive.dms"
            path.write_bytes(b"image")
            session = ImageSession("c" * 32, path.name, "dms", path)

            report = preflight_report(
                DiskService(folder),
                session,
                {
                    "operation": "copy",
                    "sourceKind": "ffs",
                    "targetKind": "dms",
                    "changes": [{"name": "Game", "type": "file", "filetype": "Tool"}],
                },
            )

            self.assertEqual(len(report["items"][0]["losses"]), 1)
            self.assertIn("Workbench icon type", report["items"][0]["losses"][0])

    def test_preflight_can_describe_distinct_slots_or_shared_destinations(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "collection.hdf"
            path.write_bytes(b"image")
            session = ImageSession("f" * 32, path.name, "hdf", path)
            report = preflight_report(
                DiskService(folder), session,
                {
                    "operation": "online-library-install",
                    "changes": [
                        {"name": "SAME", "sourceName": "Game One", "allowDuplicateName": True},
                        {"name": "SAME", "sourceName": "Game Two", "allowDuplicateName": True},
                    ],
                },
            )
            self.assertTrue(report["canProceed"])
            self.assertEqual(report["items"][1]["sourceName"], "Game Two")

    def test_preflight_does_not_revalidate_an_existing_destination_path(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "drive.hda"
            path.write_bytes(b"image")
            session = ImageSession("1" * 32, path.name, "ffs", path)
            report = preflight_report(
                DiskService(folder), session,
                {
                    "operation": "online-library-install",
                    "targetKind": "ffs",
                    "changes": [
                        {
                            "name": "$.Games",
                            "sourceName": "Arcadians",
                            "nameIsLeaf": True,
                            "existingDestination": True,
                            "allowDuplicateName": True,
                            "type": "contents into directory",
                        },
                    ],
                },
            )
            self.assertTrue(report["canProceed"])
            self.assertEqual(report["items"][0]["targetName"], "$.Games")
            self.assertEqual(report["issues"], [])

    def test_accepted_preflight_is_retained_with_canonical_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "disk.adf"
            path.write_bytes(b"image")
            session = ImageSession("c" * 32, path.name, "ofs", path)
            report = preflight_report(
                DiskService(folder), session,
                {"operation": "copy", "changes": [{"name": "GAME"}]},
            )
            accepted = accept_compatibility_report(DiskService(folder), session, report)
            self.assertEqual(session.compatibility_reports, [accepted])
            self.assertIn("acceptedAt", accepted)
            self.assertEqual(accepted["acceptedImage"]["name"], "disk.adf")
            self.assertIn("# Amiga File Forge compatibility report", accepted["markdown"])

    def test_blocking_preflight_cannot_be_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "disk.adf"
            path.write_bytes(b"image")
            session = ImageSession("d" * 32, path.name, "ofs", path)
            report = preflight_report(
                DiskService(folder), session,
                {
                    "operation": "copy",
                    "changes": [
                        {"name": "Games", "type": "directory"},
                        {"name": "games", "type": "directory"},
                    ],
                },
            )
            self.assertFalse(report["canProceed"])
            with self.assertRaisesRegex(DiskError, "blocking findings"):
                accept_compatibility_report(DiskService(folder), session, report)

            report["canProceed"] = True
            report["issues"] = []
            report["items"] = []
            with self.assertRaisesRegex(DiskError, "blocking findings"):
                accept_compatibility_report(DiskService(folder), session, report)

    def test_inspector_decodes_plain_text_loader_commands(self) -> None:
        service = Mock()
        temporary = tempfile.NamedTemporaryFile(delete=False)
        temporary.write(b'*DIR Games\rCHAIN "MENU"\r')
        temporary.close()
        service.export_file.return_value = Path(temporary.name)
        session = Mock()

        try:
            report = inspect_file(service, session, "$.Startup-Sequence", None)
        finally:
            Path(temporary.name).unlink(missing_ok=True)

        self.assertEqual(report["view"], "text")
        self.assertTrue(report["editable"])
        self.assertEqual([item["action"] for item in report["commands"]], ["DIR", "CHAIN"])

    @patch("app.analysis_service.build_manifest")
    def test_duplicate_finder_forwards_progress_to_manifest_build(self, manifest) -> None:
        manifest.return_value = {"records": [], "menus": []}
        progress = Mock()

        duplicate_report(Mock(), Mock(kind="ofs"), progress)

        self.assertIs(manifest.call_args.args[2], progress)

    def test_health_check_reports_progress_and_honours_abort(self) -> None:
        service = Mock()
        session = Mock(kind="ofs", descriptor_path=None, dms=None, warnings=[])

        def abort(_message, _current, _total):
            raise OperationCancelled("Stopped safely")

        with self.assertRaises(OperationCancelled):
            health_report(service, session, abort)

    @patch("app.analysis_service.sha256_path")
    def test_manifest_checksum_does_not_swallow_cancellation(self, checksum) -> None:
        service = Mock()
        service.list_directory.return_value = {
            "entries": [{"name": "GAME", "type": "file", "length": 1}],
        }
        temporary = tempfile.NamedTemporaryFile(delete=False)
        temporary.write(b"x")
        temporary.close()
        service.export_file.return_value = Path(temporary.name)
        session = Mock(kind="ffs")

        def cancel_during_checksum(_path, progress):
            progress(1, 1)

        def progress(message, _current, _total):
            if message.startswith("Checksumming"):
                raise OperationCancelled("Stopped safely")

        checksum.side_effect = cancel_during_checksum
        try:
            with self.assertRaises(OperationCancelled):
                build_manifest(service, session, progress)
        finally:
            Path(temporary.name).unlink(missing_ok=True)

    def test_dependency_scan_honours_cancellation_during_catalogue_walk(self) -> None:
        service = Mock()
        temporary = tempfile.NamedTemporaryFile(delete=False)
        temporary.write(b'CHAIN "GAME"\r')
        temporary.close()
        service.export_file.return_value = Path(temporary.name)
        service.list_directory.return_value = {"entries": []}
        session = Mock(kind="ffs")

        def abort(message, _current, _total):
            if message.startswith("Reading directory"):
                raise OperationCancelled("Stopped safely")

        try:
            with self.assertRaises(OperationCancelled):
                dependency_report(service, session, "S/Startup-Sequence", None, abort)
        finally:
            Path(temporary.name).unlink(missing_ok=True)

if __name__ == "__main__":
    unittest.main()
