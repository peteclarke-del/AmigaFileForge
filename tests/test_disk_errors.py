from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.disk_service import (
    HARDFILE_MAX_SIZE,
    SESSION_OWNER,
    DiskError,
    DiskService,
    ImageSession,
)
try:
    from app.server import create_app
except ModuleNotFoundError:  # Flask is intentionally absent from the light host test env.
    create_app = None


class DiskErrorTests(unittest.TestCase):
    @unittest.skipIf(create_app is None, "Flask is available in the application container")
    def test_browser_storage_owner_restores_missing_cookie(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch(
            "app.server.WORK_DIR", Path(folder)
        ):
            application = create_app()
            first_browser = application.test_client()
            first_response = first_browser.get("/api/health")
            owner = first_response.headers["X-Amiga-Session-Owner"]
            self.assertRegex(owner, r"^[A-Za-z0-9_-]{32,64}$")

            replacement_cookie_jar = application.test_client()
            restored_response = replacement_cookie_jar.get(
                "/api/health",
                headers={"X-Amiga-Session-Owner": owner},
            )

            self.assertEqual(restored_response.headers["X-Amiga-Session-Owner"], owner)
            self.assertIn(
                f"amiga_file_forge_owner={owner}",
                restored_response.headers["Set-Cookie"],
            )

    def test_hardfile_is_a_distinct_target_profile(self) -> None:
        self.assertEqual(DiskService._target_hardware("hardfile"), "hardfile")

    def test_capacity_sums_filesystem_partitions(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "disk.hda"
            image.write_bytes(b"")
            service = DiskService(folder)
            # A raw drive image with no readable volume still reports capacity
            # from its partition table.
            session = ImageSession("c" * 32, "disk.img", "raw", image)
            report = {"reports": {
                "partition_1": {"rows": [{"size": 1_000_000, "free": 250_000}]},
                "partition_2": {"rows": [{"size": 500_000, "free": 100_000}]},
            }}
            with patch.object(service, "stat", return_value=report):
                capacity = service.capacity(session)
            self.assertEqual(capacity, {
                "available": True,
                "unit": "bytes",
                "total": 1_500_000,
                "used": 1_150_000,
                "free": 350_000,
            })

    def test_a_volume_root_lists_its_files_and_drawers(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(folder)
            session = service.create_blank("adf", "Volume")
            host = Path(folder) / "payload"
            host.write_bytes(b"test")
            service.make_directory(session, "Games")
            service.put(session, "Games/MyFile", host)

            root = service.list_directory(session, "", None)
            drawer = service.list_directory(session, "Games", None)

            self.assertEqual([row["name"] for row in root["entries"]], ["Games"])
            self.assertEqual(root["entries"][0]["type"], "dir")
            self.assertEqual([row["name"] for row in drawer["entries"]], ["MyFile"])

    def test_browsing_a_volume_reports_content_kinds(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(folder)
            session = service.create_blank("adf", "Catalogue")
            host = Path(folder) / "payload"
            host.write_bytes(b"; startup\nEcho \"Booting\"\nExecute Games/Zool\n")
            service.put(session, "Startup-Sequence", host)
            service.put(session, "Boot", host)

            listing = service.browse_directory(session, "", None)

            self.assertEqual(
                sorted(row["name"] for row in listing["entries"]),
                ["Boot", "Startup-Sequence"],
            )
            self.assertTrue(
                all(row.get("contentKind") for row in listing["entries"]),
                listing["entries"],
            )

    def test_protection_edits_survive_reopening_a_volume(self) -> None:
        """An Amiga catalogue carries one attribute word, not two addresses."""
        for image_format in ("adf", "ffs-intl"):
            with self.subTest(image_format=image_format), tempfile.TemporaryDirectory() as folder:
                service = DiskService(folder)
                session = service.create_blank(image_format, "Attributes")
                host = Path(folder) / "payload"
                host.write_bytes(b"machine code")
                service.put(session, "Program", host)

                service.set_file_metadata(session, "Program", "&00000005", "Locked file")
                reopened = service.create_from_stream(
                    session.name, io.BytesIO(session.path.read_bytes()),
                )
                row = service.browse_directory(reopened, "", None)["entries"][0]

                self.assertEqual(int(row["protection"]), 0x05)
                self.assertEqual(row["attr"], "----r-e-")
                self.assertEqual(row["comment"], "Locked file")
                self.assertEqual(
                    service.read_file(reopened, "Program"), b"machine code"
                )

    def test_writing_into_a_missing_drawer_is_refused(self) -> None:
        """A full stop is a legal name character, so this is a missing drawer."""
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(folder)
            session = service.create_blank("adf", "Volume")
            host = Path(folder) / "payload"
            host.write_bytes(b"test")

            with self.assertRaises(DiskError):
                service.put(session, "Games/MyFile", host)
            service.put(session, "Games.MyFile", host)
            self.assertEqual(
                [row["name"] for row in service.list_directory(session, "", None)["entries"]],
                ["Games.MyFile"],
            )

    def test_files_move_between_drawers(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(folder)
            session = service.create_blank("adf", "Volume")
            host = Path(folder) / "payload"
            host.write_bytes(b"test")
            service.make_directory(session, "Games")
            service.put(session, "Hello", host)

            moved = service.move_ofs_items(
                session, [{"source": "Hello", "destination": "Games/Hello"}],
            )

            self.assertEqual(moved[0]["destination"], "Games/Hello")
            self.assertEqual(
                [row["name"] for row in service.list_directory(session, "", None)["entries"]],
                ["Games"],
            )
            self.assertEqual(
                [row["name"] for row in service.list_directory(session, "Games", None)["entries"]],
                ["Hello"],
            )

    def test_a_drawer_tree_keeps_its_contents_separate(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(folder)
            session = service.create_blank("ffs-intl", "Volume")
            host = Path(folder) / "payload"
            host.write_bytes(b"contents")
            service.make_directory(session, "One")
            service.make_directory(session, "Two")
            service.put(session, "Two/File", host)

            self.assertEqual(
                [row["name"] for row in service.list_directory(session, "One", None)["entries"]],
                [],
            )
            self.assertEqual(
                [row["name"] for row in service.list_directory(session, "Two", None)["entries"]],
                ["File"],
            )

    def test_hardfile_download_reports_preparation_phases(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "scsi0.hda"
            descriptor = Path(folder) / "scsi0.geo"
            image.write_bytes(b"image")
            descriptor.write_bytes(b"descriptor")
            service = DiskService(folder)
            session = ImageSession(
                "e" * 32,
                "scsi0.hda",
                "ffs",
                image,
                descriptor_name="scsi0.geo",
                descriptor_path=descriptor,
            )
            progress = []
            with (
                patch.object(service, "_apply_target_hardware"),
                patch.object(service, "_normalise_hardfile_dat_size"),
                patch.object(service, "_finalise_hardfile_directories", return_value=0),
                patch.object(service, "_advance_hardfile_disc_id", return_value=False),
                patch.object(service, "_validate_created_hardfile_pair"),
            ):
                result = service.prepare_download(
                    session,
                    lambda message, current=None, total=None: progress.append(
                        (message, current, total)
                    ),
                )

            self.assertEqual(result, image)
            self.assertEqual([item[1] for item in progress], [0, 1, 2, 3, 4, 5])
            self.assertTrue(all(item[2] == 5 for item in progress))

    def test_mark_saved_clears_and_persists_dirty_state(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            session_folder = Path(folder) / ("f" * 32)
            session_folder.mkdir()
            image = session_folder / "saved.adf"
            image.write_bytes(b"image")
            service = DiskService(folder)
            session = ImageSession(
                "f" * 32,
                image.name,
                "raw",
                image,
                dirty=True,
            )

            service.mark_saved(session)

            self.assertFalse(session.dirty)
            restored = service._restore_session(session.id)
            self.assertFalse(restored.dirty)

    def test_clean_edited_hfe_uses_prepared_export(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            session_folder = Path(folder) / ("1" * 32)
            session_folder.mkdir()
            raw = session_folder / "working.img"
            original = session_folder / "original.hfe"
            exported = session_folder / "saved.hfe"
            raw.write_bytes(b"raw")
            original.write_bytes(b"original")
            exported.write_bytes(b"edited")
            service = DiskService(folder)
            session = ImageSession(
                "1" * 32,
                "disk.hfe",
                "ofs",
                raw,
                dirty=False,
                hfe_original_path=original,
                hfe_export_path=exported,
            )

            self.assertEqual(service._prepare_hfe_download(session), exported)

    def test_image_rename_preserves_format_and_renames_descriptor_download(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            session_folder = Path(folder) / ("a" * 32)
            session_folder.mkdir()
            image_path = session_folder / "scsi0.hda"
            descriptor_path = session_folder / "scsi0.geo"
            image_path.write_bytes(b"image")
            descriptor_path.write_bytes(b"descriptor")
            service = DiskService(folder)
            session = ImageSession(
                "a" * 32,
                "scsi0.hda",
                "ffs",
                image_path,
                descriptor_name="scsi0.geo",
                descriptor_path=descriptor_path,
            )

            service.rename_session(session, "Games")

            self.assertEqual(session.name, "Games.hda")
            self.assertEqual(session.descriptor_name, "Games.geo")
            self.assertEqual(session.path, image_path)
            self.assertTrue((session_folder / "session.json").is_file())

    def test_image_rename_cannot_change_its_format(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image_path = Path(folder) / "disk.adf"
            image_path.write_bytes(b"image")
            session = ImageSession("b" * 32, "disk.adf", "ofs", image_path)

            with self.assertRaisesRegex(DiskError, r"\.adf"):
                DiskService(folder).rename_session(session, "disk.hdf")

    def test_blank_image_targets_follow_the_selected_format(self) -> None:
        self.assertEqual(
            DiskService._blank_target_hardware("hardfile", "a1200-ffs"),
            "hardfile",
        )
        self.assertEqual(
            DiskService._blank_target_hardware("ffs-hard", "a500-ofs"),
            "amigaos",
        )
        self.assertEqual(
            DiskService._blank_target_hardware("ffs-physical", "hardfile"),
            "amigaos",
        )
        self.assertEqual(
            DiskService._blank_target_hardware("ffs-hard", "a1200-ffs"),
            "amigaos",
        )
        self.assertEqual(
            DiskService._blank_target_hardware("ffs-physical", "a1200-ffs"),
            "amigaos",
        )
        self.assertEqual(
            DiskService._blank_target_hardware("ffs-intl", "a1200-ffs"),
            "a1200-ffs",
        )
        self.assertEqual(
            DiskService._blank_target_hardware("hfe-ffs-hd", "a500-ofs"),
            "a500-ofs",
        )
        # A hard-drive profile is not a choice a floppy can make.
        self.assertEqual(
            DiskService._blank_target_hardware("adf", "amigaos"),
            "auto",
        )
        self.assertEqual(
            DiskService._blank_target_hardware("hdf", "hardfile"),
            "auto",
        )
        self.assertEqual(
            DiskService._blank_target_hardware("adf", "amigaos"),
            "auto",
        )

    def test_failed_blank_creation_removes_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = DiskService(root)

            with patch.object(service, "_run", side_effect=DiskError("failed")):
                with self.assertRaisesRegex(DiskError, "failed"):
                    service.create_blank("adf", "Blank")

            self.assertEqual(list(root.iterdir()), [])

    def test_image_can_extract_directly_into_current_ffs_directory(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = DiskService(root)
            source_path = root / "source.adf"
            target_path = root / "target.hda"
            source_path.write_bytes(b"source")
            target_path.write_bytes(b"target")
            source = ImageSession("1" * 32, source_path.name, "ofs", source_path)
            target = ImageSession("2" * 32, target_path.name, "ffs", target_path)

            def listing(session, path, *_args, **_kwargs):
                if session is not source:
                    return {"entries": []}
                if path == "":
                    return {"entries": [{"name": "$", "type": "dir", "virtual": True}]}
                return {"entries": [{"name": "GAME", "type": "file"}]}

            with (
                patch.object(service, "require_writable_geometry"),
                patch.object(service, "list_directory", side_effect=listing),
                patch.object(service, "_copy_image_listing_to_ffs") as copy_listing,
                patch.object(service, "_repair_copied_ffs_loaders", return_value=([], [])),
                patch.object(service, "_run") as run,
            ):
                destination = service.extract_image_to_ffs_directory(
                    source,
                    target,
                    "$.Games",
                    None,
                    create_directory=False,
                )

            self.assertEqual(destination, "$.Games")
            copy_listing.assert_called_once()
            run.assert_not_called()
            self.assertTrue(target.dirty)
            self.assertEqual(list(root.glob(".import-rollback-*")), [])

    def _extraction_fixture(self, root: Path):
        service = DiskService(root)
        source_path = root / "source.adf"
        target_path = root / "target.hda"
        source_path.write_bytes(b"source")
        target_path.write_bytes(b"target")
        source = ImageSession("4" * 32, source_path.name, "ofs", source_path)
        target = ImageSession("5" * 32, target_path.name, "ffs", target_path)

        def listing(session, path, *_args, **_kwargs):
            if session is not source:
                return {"entries": []}
            if path == "":
                return {"entries": [{"name": "$", "type": "dir", "virtual": True}]}
            return {"entries": [{"name": "GAME", "type": "file"}]}

        return service, source, target, listing

    def test_root_extraction_carries_the_source_boot_option(self) -> None:
        # A disc installed into the root without its bootblock setting has all its
        # files but cannot start itself, which is the difference between the
        # contents being present and the title running.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service, source, target, listing = self._extraction_fixture(root)

            with (
                patch.object(service, "require_writable_geometry"),
                patch.object(service, "list_directory", side_effect=listing),
                patch.object(service, "_copy_image_listing_to_ffs"),
                patch.object(service, "_repair_copied_ffs_loaders", return_value=([], [])),
                patch.object(service, "_mark_mutated"),
                patch.object(service, "_run", return_value="3 (EXEC)") as run,
            ):
                destination = service.extract_image_to_ffs_directory(
                    source, target, "$", None, create_directory=False,
                )

            self.assertEqual(destination, "$")
            self.assertIn(
                ["opt", str(target.path), "3"],
                [list(call.args[0]) for call in run.call_args_list],
            )
            self.assertEqual(target.warnings, [])

    def test_a_directory_destination_leaves_the_boot_option_alone(self) -> None:
        # A boot option names $.Startup-Sequence. Setting it after installing into a child
        # directory would point the machine at a file that is not there and
        # break an image which previously started. Software installed into its
        # own directory is reached through the menu, which selects it first.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service, source, target, _ = self._extraction_fixture(root)

            with patch.object(service, "_run", return_value="3 (EXEC)") as run:
                for destination in ("$.GAME", "$.Games.Chuck", "GAME"):
                    with self.subTest(destination=destination):
                        self.assertIsNone(
                            service.carry_boot_option(source, target, destination)
                        )
            run.assert_not_called()

    def test_a_destination_that_is_not_a_volume_is_declined(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service, source, target, _ = self._extraction_fixture(root)
            target.kind = "hdf"

            with patch.object(service, "_run", return_value="1") as run:
                self.assertIsNone(service.carry_boot_option(source, target, ""))
            run.assert_not_called()

    def test_a_source_disc_with_no_boot_option_sets_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service, source, target, listing = self._extraction_fixture(root)

            with (
                patch.object(service, "require_writable_geometry"),
                patch.object(service, "list_directory", side_effect=listing),
                patch.object(service, "_copy_image_listing_to_ffs"),
                patch.object(service, "_repair_copied_ffs_loaders", return_value=([], [])),
                patch.object(service, "_mark_mutated"),
                patch.object(service, "_run", return_value="0 (OFF)") as run,
            ):
                service.extract_image_to_ffs_directory(
                    source, target, "$", None, create_directory=False,
                )

            self.assertEqual(
                [call for call in run.call_args_list
                 if len(call.args[0]) > 2 and call.args[0][0] == "opt"],
                [],
            )

    def test_a_failed_boot_option_warns_and_keeps_the_installed_files(self) -> None:
        # The files are already in place, so refusing the whole extraction over
        # the boot option would lose more than it protects.
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service, source, target, listing = self._extraction_fixture(root)

            def run(args, *_a, **_k):
                if args[0] == "opt" and len(args) > 2:
                    raise DiskError("engine refused")
                return "3 (EXEC)"

            with (
                patch.object(service, "require_writable_geometry"),
                patch.object(service, "list_directory", side_effect=listing),
                patch.object(service, "_copy_image_listing_to_ffs"),
                patch.object(service, "_repair_copied_ffs_loaders", return_value=([], [])),
                patch.object(service, "_mark_mutated"),
                patch.object(service, "_run", side_effect=run),
            ):
                destination = service.extract_image_to_ffs_directory(
                    source, target, "$", None, create_directory=False,
                )

            self.assertEqual(destination, "$")
            self.assertTrue(any("boot option" in warning for warning in target.warnings))

    def test_failed_current_directory_extraction_restores_target_image(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = DiskService(root)
            source_path = root / "source.adf"
            target_path = root / "target.hda"
            source_path.write_bytes(b"source")
            target_path.write_bytes(b"original target")
            source = ImageSession("3" * 32, source_path.name, "ofs", source_path)
            target = ImageSession("4" * 32, target_path.name, "ffs", target_path)

            def listing(session, path, *_args, **_kwargs):
                if session is not source:
                    return {"entries": []}
                if path == "":
                    return {"entries": [{"name": "$", "type": "dir", "virtual": True}]}
                return {"entries": [{"name": "GAME", "type": "file"}]}

            def fail_copy(*_args, **_kwargs):
                target_path.write_bytes(b"partly modified")
                target.warnings.append("partial warning")
                raise DiskError("copy failed")

            with (
                patch.object(service, "require_writable_geometry"),
                patch.object(service, "list_directory", side_effect=listing),
                patch.object(service, "_copy_image_listing_to_ffs", side_effect=fail_copy),
            ):
                with self.assertRaisesRegex(DiskError, "copy failed"):
                    service.extract_image_to_ffs_directory(
                        source,
                        target,
                        "$",
                        None,
                        create_directory=False,
                    )

            self.assertEqual(target_path.read_bytes(), b"original target")
            self.assertFalse(target.dirty)
            self.assertEqual(target.warnings, [])
            self.assertEqual(list(root.glob(".import-rollback-*")), [])

    def test_import_preview_traverses_source_drawers(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = DiskService(root)
            image = root / "source.adf"
            image.write_bytes(b"image")
            session = ImageSession("5" * 32, image.name, "ffs", image)

            def listing(_session, path, *_args, **_kwargs):
                if not path:
                    return {"entries": [
                        {"name": "Startup-Sequence", "type": "file", "size": 24},
                        {"name": "Games", "type": "dir", "size": 0},
                    ]}
                return {"entries": [{"name": "Chuck", "type": "file", "size": 1088}]}

            with patch.object(service, "list_directory", side_effect=listing):
                preview = service.preview_image_contents(session)

            self.assertEqual(preview["total"], 3)
            self.assertFalse(preview["truncated"])
            self.assertEqual(
                [(entry["path"], entry["name"]) for entry in preview["entries"]],
                [("", "Startup-Sequence"), ("", "Games"), ("Games", "Chuck")],
            )

    def test_recoverable_sessions_lists_persisted_working_image(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = DiskService(root)
            image_id = "a" * 32
            session_folder = root / image_id
            session_folder.mkdir()
            image = session_folder / "scsi0.hda"
            descriptor = session_folder / "scsi0.geo"
            image.write_bytes(bytes(512))
            descriptor.write_bytes(bytes(22))
            session = ImageSession(
                image_id,
                image.name,
                "ffs",
                image,
                descriptor_name=descriptor.name,
                descriptor_path=descriptor,
                target_hardware="hardfile",
            )
            service._persist_session(session)

            recovered = service.recoverable_sessions()

            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0]["id"], image_id)
            self.assertEqual(recovered[0]["name"], "scsi0.hda")
            self.assertEqual(recovered[0]["size"], 512)
            self.assertTrue(recovered[0]["hasDescriptor"])

    def test_recovery_is_scoped_to_current_browser_owner(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = DiskService(root)
            owner_token = SESSION_OWNER.set("owner-one")
            try:
                image_id = "b" * 32
                session_folder = root / image_id
                session_folder.mkdir()
                image = session_folder / "private.adf"
                image.write_bytes(bytes(204800))
                session = ImageSession(image_id, image.name, "ofs", image)
                service.sessions[image_id] = session
                service._persist_session(session)
                self.assertEqual(len(service.recoverable_sessions()), 1)
            finally:
                SESSION_OWNER.reset(owner_token)

            other_token = SESSION_OWNER.set("owner-two")
            try:
                self.assertEqual(service.recoverable_sessions(), [])
                with self.assertRaisesRegex(DiskError, "no longer exists"):
                    service.get(image_id)
                self.assertEqual(service.clear_recoverable_sessions(), 0)
                self.assertTrue(image.is_file())
            finally:
                SESSION_OWNER.reset(other_token)

    def test_restore_drops_legacy_ambiguous_command_false_positives(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = DiskService(root)
            image_id = "6" * 32
            session_folder = root / image_id
            session_folder.mkdir()
            image = session_folder / "working.adf"
            image.write_bytes(bytes(901120))
            session = ImageSession(image_id, image.name, "raw", image)
            session.warnings = [
                "Games/Review: contains an ambiguous DF0: reference that no single file resolves",
                "A useful current warning",
            ]
            service._persist_session(session)

            restored = service._restore_session(image_id)

            self.assertEqual(restored.warnings[0], "A useful current warning")
            self.assertEqual(len(restored.warnings), 2)
            self.assertIn("current path-aware results", restored.warnings[1])

    def test_descriptor_is_rejected_for_non_dat_image(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(Path(folder))

            with self.assertRaisesRegex(DiskError, "only accompany"):
                service.create_from_stream(
                    "disk.adf",
                    io.BytesIO(bytes(204800)),
                    ("disk.geo", io.BytesIO(b"geometry")),
                )

    def test_a_zero_tail_is_removed_to_match_the_geometry_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            hda = root / "drive.hda"
            geo = root / "drive.hda.geo"
            geo.write_text("surfaces=2\nblockspertrack=32\ncylinders=4\nblocksize=512\n")
            declared = 2 * 32 * 4 * 512
            hda.write_bytes(bytes(declared) + bytes(1024))
            session = ImageSession(
                "a" * 32, hda.name, "ffs", hda,
                descriptor_name=geo.name, descriptor_path=geo,
            )

            DiskService(root)._normalise_hardfile_dat_size(session)

            self.assertEqual(hda.stat().st_size, declared)
            self.assertTrue(session.dirty)
            self.assertIn("all-zero 1,024-byte tail", session.warnings[0])

    def test_non_zero_data_beyond_the_declared_capacity_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            hda = root / "drive.hda"
            geo = root / "drive.hda.geo"
            geo.write_text("surfaces=2\nblockspertrack=32\ncylinders=4\nblocksize=512\n")
            declared = 2 * 32 * 4 * 512
            hda.write_bytes(bytes(declared) + bytes(1023) + b"\x01")
            session = ImageSession(
                "a" * 32, hda.name, "ffs", hda,
                descriptor_name=geo.name, descriptor_path=geo,
            )

            DiskService(root)._normalise_hardfile_dat_size(session)

            self.assertEqual(hda.stat().st_size, declared + 1024)
            self.assertFalse(session.dirty)
            self.assertIn("non-zero data beyond", session.warnings[0])

    def test_amiganut_traceback_is_reduced_to_final_error(self) -> None:
        message = """Traceback (most recent call last):
  File "/usr/local/bin/disc", line 8, in <module>
ValueError: A concise engine failure"""

        self.assertEqual(
            DiskService._friendly_engine_error(message),
            "A concise engine failure",
        )

    def test_created_hardfile_pair_matches_its_geometry_sidecar(self) -> None:
        """An emulator multiplies the sidecar out and refuses a mismatch."""
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(folder)
            session = service.create_blank("hardfile", "Drive", capacity="4MB")

            DiskService._validate_created_hardfile_pair(session)

            declared = service._hardfile_descriptor_size(session.descriptor_path)
            self.assertEqual(declared, session.path.stat().st_size)

            session.descriptor_path.write_text("surfaces=1\nblockspertrack=1\ncylinders=1\n")
            with self.assertRaisesRegex(DiskError, "declares"):
                DiskService._validate_created_hardfile_pair(session)

    def test_a_hardfile_sidecar_without_a_geometry_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            hda = root / "drive.hda"
            geo = root / "drive.hda.geo"
            geo.write_text("# no fields here\n")
            hda.write_bytes(bytes(1024))
            session = ImageSession(
                "a" * 32, hda.name, "ffs", hda,
                descriptor_name=geo.name, descriptor_path=geo,
            )
            with self.assertRaisesRegex(DiskError, "does not declare"):
                DiskService._validate_created_hardfile_pair(session)
            self.assertEqual(HARDFILE_MAX_SIZE, 0x7FFFFF * 512)

    def test_a_created_hardfile_root_carries_the_requested_volume_name(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(folder)
            session = service.create_blank("hardfile", "WorkDisk", capacity="4MB")

            DiskService._canonicalise_created_hardfile_root(session, "WorkDisk")

            with self.assertRaisesRegex(DiskError, "named"):
                DiskService._canonicalise_created_hardfile_root(session, "Something Else")

    def test_a_download_repairs_a_damaged_block_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(folder)
            session = service.create_blank("hardfile", "Repair", capacity="4MB")
            size = session.path.stat().st_size
            root_block = (size // 512) // 2

            with session.path.open("r+b") as image:
                image.seek(root_block * 512 + 20)
                image.write(b"\x00\x00\x00\x00")

            repaired = DiskService._finalise_hardfile_directories(session)

            self.assertGreater(repaired, 0)
            self.assertTrue(session.dirty)
            with session.path.open("rb") as image:
                image.seek(root_block * 512)
                block = image.read(512)
            total = sum(
                int.from_bytes(block[offset : offset + 4], "big")
                for offset in range(0, 512, 4)
            ) & 0xFFFFFFFF
            self.assertEqual(total, 0)

    def test_a_repair_revalidates_an_invalidated_bitmap(self) -> None:
        """A machine reads a zero bitmap flag as an unclean shutdown."""
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(folder)
            session = service.create_blank("hardfile", "Bitmap", capacity="4MB")
            size = session.path.stat().st_size
            root_block = (size // 512) // 2

            with session.path.open("r+b") as image:
                image.seek(root_block * 512 + 512 - 200)
                image.write(b"\x00\x00\x00\x00")

            self.assertGreater(DiskService._finalise_hardfile_directories(session), 0)

            with session.path.open("rb") as image:
                image.seek(root_block * 512 + 512 - 200)
                self.assertEqual(image.read(4), b"\xff\xff\xff\xff")

    def test_a_repair_leaves_a_healthy_volume_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(folder)
            session = service.create_blank("hardfile", "Healthy", capacity="4MB")
            service.make_directory(session, "Games")
            before = session.path.read_bytes()

            self.assertEqual(DiskService._finalise_hardfile_directories(session), 0)
            self.assertEqual(session.path.read_bytes(), before)

    def test_a_download_restamps_the_volume_once(self) -> None:
        """A machine caches a volume by name and datestamp, so it must change."""
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(folder)
            session = service.create_blank("hardfile", "Restamp", capacity="4MB")
            before = session.path.read_bytes()

            self.assertTrue(DiskService._advance_hardfile_disc_id(session))
            after = session.path.read_bytes()
            self.assertNotEqual(before, after)
            self.assertEqual(len(before), len(after))

            # A second call with no intervening change is a no-op.
            self.assertFalse(DiskService._advance_hardfile_disc_id(session))
            self.assertEqual(session.path.read_bytes(), after)

    def test_a_block_checksum_makes_its_longs_sum_to_zero(self) -> None:
        """Every AmigaDOS block ends with the value that zeroes its own sum."""
        block = bytearray(512)
        block[0:4] = (2).to_bytes(4, "big")
        block[508:512] = (1).to_bytes(4, "big")
        checksum = DiskService._block_checksum(bytes(block))
        block[20:24] = checksum.to_bytes(4, "big")
        total = sum(
            int.from_bytes(block[offset : offset + 4], "big")
            for offset in range(0, 512, 4)
        ) & 0xFFFFFFFF
        self.assertEqual(total, 0)

    def test_a_block_bounds_error_explains_the_missing_geometry_sidecar(self) -> None:
        message = "DataError: Block 3200 is outside this volume."

        self.assertIn("matching GEO", DiskService._friendly_engine_error(message))

    def test_descriptorless_dat_is_not_writable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scsi0.hda"
            path.touch()
            session = ImageSession("test", path.name, "ffs", path)

            with self.assertRaisesRegex(DiskError, "matching GEO"):
                DiskService.require_writable_geometry(session)

if __name__ == "__main__":
    unittest.main()
