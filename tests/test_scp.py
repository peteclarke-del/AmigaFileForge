from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.disk_service import DiskError, DiskService, ImageSession


def _write_output(arguments: list[str], content: bytes) -> None:
    output = next(
        (item.removeprefix("-foutput:") for item in arguments if item.startswith("-foutput:")),
        None,
    )
    if output:
        Path(output).write_bytes(content)


class ScpTests(unittest.TestCase):
    def test_scp_extension_uses_container_decoder(self) -> None:
        self.assertEqual(DiskService.detect_kind("capture.scp"), "scp")

    def test_open_scp_decodes_clean_round_tripping_capture(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / "capture.scp"
            original.write_bytes(b"SCP" + bytes(100))
            service = DiskService(Path(folder) / "work")

            def convert(arguments):
                if any(argument.startswith("-conv:SCP_FLUX_STREAM") for argument in arguments):
                    _write_output(arguments, b"SCP" + bytes(100))
                else:
                    _write_output(arguments, bytes(901_120))
                return ""

            with (
                patch.object(service, "_run_hxcfe", side_effect=convert),
                patch.object(service, "_run", return_value=""),
                patch.object(service, "identify_kind", return_value="ofs"),
            ):
                working, kind, original_path, read_only, warnings = service._open_scp(original)
            self.assertEqual(kind, "ofs")
            self.assertEqual(original_path, original)
            self.assertFalse(read_only)
            self.assertTrue(working.is_file())
            self.assertTrue(any("Opened SCP flux capture" in warning for warning in warnings))

    def test_open_scp_marks_non_round_tripping_capture_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / "capture.scp"
            original.write_bytes(b"SCP" + bytes(100))
            service = DiskService(Path(folder) / "work")

            def convert(arguments):
                if any(argument.startswith("-conv:SCP_FLUX_STREAM") for argument in arguments):
                    _write_output(arguments, b"SCP" + bytes(100))
                elif any(argument.startswith("-finput") and "open-check" in argument for argument in arguments):
                    _write_output(arguments, bytes(1))
                else:
                    _write_output(arguments, bytes(901_120))
                return ""

            with (
                patch.object(service, "_run_hxcfe", side_effect=convert),
                patch.object(service, "_run", return_value=""),
                patch.object(service, "identify_kind", return_value="ofs"),
            ):
                _working, _kind, _original, read_only, warnings = service._open_scp(original)
            self.assertTrue(read_only)
            self.assertTrue(any("read-only" in warning for warning in warnings))

    def test_open_scp_rejects_non_amiga_filesystem(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / "capture.scp"
            original.write_bytes(b"SCP" + bytes(100))
            service = DiskService(Path(folder) / "work")

            def convert(arguments):
                _write_output(arguments, bytes(901_120))
                return ""

            with (
                patch.object(service, "_run_hxcfe", side_effect=convert),
                patch.object(
                    service,
                    "identify_kind",
                    side_effect=DiskError("No supported Amiga filesystem was found."),
                ),
            ):
                with self.assertRaisesRegex(DiskError, "cannot be browsed as an Amiga disk image"):
                    service._open_scp(original)

    def test_read_only_scp_session_cannot_be_edited(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "decoded.adf"
            image.write_bytes(b"")
            session = ImageSession(
                "a" * 32,
                "protected.scp",
                "ofs",
                image,
                scp_original_path=Path(folder) / "protected.scp",
                scp_read_only=True,
            )
            with self.assertRaisesRegex(DiskError, "cannot be rewritten safely"):
                DiskService.require_writable_geometry(session)

    def test_scp_decode_restores_one_omitted_double_density_tail_sector(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / "capture.scp"
            original.write_bytes(b"SCP" + bytes(100))
            service = DiskService(Path(folder) / "work")

            def convert(arguments):
                if any(argument.startswith("-conv:SCP_FLUX_STREAM") for argument in arguments):
                    _write_output(arguments, b"SCP" + bytes(100))
                else:
                    _write_output(arguments, bytes(901_120 - 512))
                return "Invalid rpm or tracklen"

            with (
                patch.object(service, "_run_hxcfe", side_effect=convert),
                patch.object(service, "_run", return_value=""),
                patch.object(service, "identify_kind", return_value="ffs"),
            ):
                working, kind, _original, _read_only, warnings = service._open_scp(original)
            self.assertEqual(kind, "ffs")
            self.assertEqual(working.suffix, ".adf")
            self.assertEqual(working.stat().st_size, 901_120)
            self.assertTrue(any("restored the declared floppy geometry" in row for row in warnings))
            self.assertTrue(any("non-standard index timing" in row for row in warnings))

    def test_scp_decode_rejects_a_broken_directory_tree(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / "capture.scp"
            original.write_bytes(b"SCP" + bytes(100))
            service = DiskService(Path(folder) / "work")

            def convert(arguments):
                _write_output(arguments, bytes(901_120))
                return ""

            with (
                patch.object(service, "_run_hxcfe", side_effect=convert),
                patch.object(service, "_run", side_effect=DiskError("Broken directory")),
                patch.object(service, "identify_kind", return_value="ffs"),
            ):
                with self.assertRaisesRegex(DiskError, "complete directory tree is not safe"):
                    service._open_scp(original)

    def test_scp_requires_a_real_container_signature(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            original = Path(folder) / "not-really.scp"
            original.write_bytes(b"ZIP" + bytes(100))
            service = DiskService(Path(folder) / "work")
            with self.assertRaisesRegex(DiskError, "valid SuperCard Pro SCP signature"):
                service._open_scp(original)

    def test_export_formats_offer_flux_containers_for_ofs_and_ffs(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(Path(folder) / "work")
            ofs_image = Path(folder) / "disk.adf"
            ofs_image.write_bytes(bytes(901_120))
            ofs_session = ImageSession("a" * 32, "disk.adf", "ofs", ofs_image)
            formats = {entry["format"] for entry in service.export_formats(ofs_session)}
            self.assertEqual(formats, {"native", "adz", "hfe", "scp"})

            ffs_image = Path(folder) / "ffs.adf"
            ffs_image.write_bytes(bytes(901_120))
            ffs_session = ImageSession("b" * 32, "ffs.adf", "ffs", ffs_image)
            self.assertEqual(
                {entry["format"] for entry in service.export_formats(ffs_session)},
                {"native", "adz", "hfe", "scp"},
            )

            ffs_d = Path(folder) / "disk.adf"
            ffs_d.write_bytes(bytes(800 * 1024))
            ffs_d_session = ImageSession("c" * 32, "disk.adf", "ffs", ffs_d)
            self.assertEqual(
                {entry["format"] for entry in service.export_formats(ffs_d_session)},
                {"native"},
            )

    def test_export_formats_empty_for_media_that_is_not_a_filing_system(self) -> None:
        """A ROM or a DMS archive has no decoded sectors to write elsewhere."""
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(Path(folder) / "work")
            for kind, name in (("rom", "kick.rom"), ("dms", "game.dms")):
                image = Path(folder) / name
                image.write_bytes(bytes(1024))
                session = ImageSession("a" * 32, name, kind, image)
                self.assertEqual(service.export_formats(session), [], kind)

    def test_a_hard_drive_is_offered_the_conversion_it_does_not_already_have(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(Path(folder) / "work")
            image = Path(folder) / "drive.hdf"
            image.write_bytes(bytes(8 * 1024 * 1024))
            partitioned = ImageSession("a" * 32, "drive.hdf", "hdf", image)
            self.assertEqual(
                {row["format"] for row in service.export_formats(partitioned)},
                {"native", "hardfile"},
            )
            bare = ImageSession("b" * 32, "drive.hdf", "ffs", image)
            self.assertEqual(
                {row["format"] for row in service.export_formats(bare)},
                {"native", "rdb"},
            )

    def test_export_native_copies_current_sectors_with_canonical_extension(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(Path(folder) / "work")
            image = Path(folder) / "disk.adf"
            image.write_bytes(b"payload" + bytes(901_120 - 7))
            session = ImageSession("a" * 32, "disk.adf", "ofs", image)
            output, name = service.export_image(session, "native")
            self.assertEqual(output.read_bytes(), image.read_bytes())
            self.assertTrue(name.endswith(".adf"))

    def test_export_scp_verifies_round_trip_before_returning(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(Path(folder) / "work")
            image = Path(folder) / "disk.adf"
            image.write_bytes(bytes(901_120))
            session = ImageSession("a" * 32, "disk.adf", "ofs", image)

            def convert(arguments):
                if any(argument.startswith("-conv:SCP_FLUX_STREAM") for argument in arguments):
                    _write_output(arguments, b"SCP-EXPORT")
                else:
                    _write_output(arguments, bytes(901_120))
                return ""

            with patch.object(service, "_run_hxcfe", side_effect=convert):
                output, name = service.export_image(session, "scp")
            self.assertTrue(output.is_file())
            self.assertTrue(name.endswith(".scp"))

    def test_export_rejects_encoding_that_fails_to_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(Path(folder) / "work")
            image = Path(folder) / "disk.adf"
            image.write_bytes(bytes(901_120))
            session = ImageSession("a" * 32, "disk.adf", "ofs", image)

            def convert(arguments):
                if any(argument.startswith("-conv:SCP_FLUX_STREAM") for argument in arguments):
                    _write_output(arguments, b"SCP-EXPORT")
                else:
                    _write_output(arguments, bytes(1))
                return ""

            with patch.object(service, "_run_hxcfe", side_effect=convert):
                with self.assertRaisesRegex(DiskError, "did not decode back to identical"):
                    service.export_image(session, "scp")

    def test_export_rejects_unavailable_format(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(Path(folder) / "work")
            image = Path(folder) / "disk.adf"
            image.write_bytes(bytes(800 * 1024))
            session = ImageSession("a" * 32, "disk.adf", "ffs", image)
            with self.assertRaisesRegex(DiskError, "not an available export format"):
                service.export_image(session, "hfe")


class ScpSaveTests(unittest.TestCase):
    """Cover the save path, which re-encodes edited sectors back to SCP."""

    def _session(self, folder: str, *, suffix: str, size: int, kind: str, dirty: bool):
        image = Path(folder) / f"working{suffix}"
        image.write_bytes(bytes(size))
        original = Path(folder) / "capture.scp"
        original.write_bytes(b"SCP" + bytes(100))
        return ImageSession(
            "a" * 32,
            "capture.scp",
            kind,
            image,
            dirty=dirty,
            scp_original_path=original,
        )

    def test_unedited_session_downloads_the_untouched_original_capture(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(Path(folder) / "work")
            session = self._session(
                folder, suffix=".adf", size=901_120, kind="ofs", dirty=False
            )
            with patch.object(service, "_run_hxcfe") as engine:
                output = service._prepare_scp_download(session)
            self.assertEqual(output, session.scp_original_path)
            engine.assert_not_called()

    def test_edited_session_is_re_encoded_and_verified(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(Path(folder) / "work")
            session = self._session(
                folder, suffix=".adf", size=901_120, kind="ofs", dirty=True
            )

            def convert(arguments):
                if any(item.startswith("-conv:SCP_FLUX_STREAM") for item in arguments):
                    _write_output(arguments, b"REBUILT-SCP")
                else:
                    _write_output(arguments, bytes(901_120))
                return ""

            with patch.object(service, "_run_hxcfe", side_effect=convert):
                output = service._prepare_scp_download(session)
            self.assertTrue(output.name.endswith("-edited.scp"))
            self.assertEqual(output.read_bytes(), b"REBUILT-SCP")
            self.assertEqual(session.scp_export_path, output)

    def test_edited_ffs_save_survives_an_omitted_tail_sector(self) -> None:
        """Regression: the SCP save path once lacked the tail-sector repair.

        HxCFE can drop the blank final sector when decoding its own
        output. Without the repair the verification compared 900,608 bytes
        against 901,120 and refused a save that was in fact byte-exact.
        """
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(Path(folder) / "work")
            session = self._session(
                folder, suffix=".adf", size=901_120, kind="ffs", dirty=True
            )

            def convert(arguments):
                if any(item.startswith("-conv:SCP_FLUX_STREAM") for item in arguments):
                    _write_output(arguments, b"REBUILT-SCP")
                else:
                    _write_output(arguments, bytes(901_120 - 512))
                return ""

            with patch.object(service, "_run_hxcfe", side_effect=convert):
                output = service._prepare_scp_download(session)
            self.assertEqual(output.read_bytes(), b"REBUILT-SCP")

    def test_save_is_refused_when_the_sectors_do_not_survive_encoding(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(Path(folder) / "work")
            session = self._session(
                folder, suffix=".adf", size=901_120, kind="ofs", dirty=True
            )

            def convert(arguments):
                if any(item.startswith("-conv:SCP_FLUX_STREAM") for item in arguments):
                    _write_output(arguments, b"REBUILT-SCP")
                else:
                    _write_output(arguments, b"\xFF" * 901_120)
                return ""

            with patch.object(service, "_run_hxcfe", side_effect=convert):
                with self.assertRaisesRegex(DiskError, "did not survive SCP encoding"):
                    service._prepare_scp_download(session)
            self.assertIsNone(session.scp_export_path)

    def test_a_read_only_capture_is_never_re_encoded(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(Path(folder) / "work")
            session = self._session(
                folder, suffix=".adf", size=901_120, kind="ofs", dirty=True
            )
            session.scp_read_only = True
            with patch.object(service, "_run_hxcfe") as engine:
                with self.assertRaisesRegex(DiskError, "cannot be rewritten safely"):
                    service._prepare_scp_download(session)
            engine.assert_not_called()


class ScpSessionPersistenceTests(unittest.TestCase):
    """An open capture must survive a service restart with its rules intact."""

    def _persisted_session(self, work: Path, *, read_only: bool) -> str:
        service = DiskService(work)
        image_id = "b" * 32
        folder = work / image_id
        folder.mkdir(parents=True, exist_ok=True)
        working = folder / "capture.adf"
        working.write_bytes(bytes(901_120))
        original = folder / "capture.scp"
        original.write_bytes(b"SCP" + bytes(100))
        session = ImageSession(
            image_id,
            "capture.scp",
            "ofs",
            working,
            scp_original_path=original,
            scp_read_only=read_only,
        )
        service.sessions[image_id] = session
        service._persist_session(session)
        return image_id

    def test_capture_identity_and_read_only_rule_survive_a_restart(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder) / "work"
            image_id = self._persisted_session(work, read_only=True)
            restored = DiskService(work).get(image_id)
            self.assertEqual(restored.scp_original_path.name, "capture.scp")
            self.assertTrue(restored.scp_read_only)
            with self.assertRaisesRegex(DiskError, "cannot be rewritten safely"):
                DiskService.require_writable_geometry(restored)

    def test_a_writable_capture_stays_writable_after_a_restart(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder) / "work"
            image_id = self._persisted_session(work, read_only=False)
            restored = DiskService(work).get(image_id)
            self.assertFalse(restored.scp_read_only)
            DiskService.require_writable_geometry(restored)

    def test_the_summary_reports_scp_as_the_container_format(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder) / "work"
            image_id = self._persisted_session(work, read_only=True)
            service = DiskService(work)
            summary = service.summary(service.get(image_id))
            self.assertEqual(summary["containerFormat"], "scp")
            self.assertTrue(summary["readOnly"])

    def test_a_session_whose_original_capture_vanished_is_not_recovered(self) -> None:
        """Recovery must not resurrect a capture whose source bytes are gone."""
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder) / "work"
            image_id = self._persisted_session(work, read_only=False)
            (work / image_id / "capture.scp").unlink()
            with self.assertRaises(DiskError):
                DiskService(work).get(image_id)

    def test_a_stale_export_is_dropped_rather_than_served(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder) / "work"
            image_id = self._persisted_session(work, read_only=False)
            service = DiskService(work)
            session = service.get(image_id)
            session.scp_export_path = work / image_id / "gone-edited.scp"
            service._persist_session(session)
            restored = DiskService(work).get(image_id)
            self.assertIsNone(restored.scp_export_path)


class FluxSavePolicyTests(unittest.TestCase):
    """HFE and SCP must stay on identical save rules now they share a path."""

    def _prepared(self, folder: str, container: str, kind: str, size: int, suffix: str):
        service = DiskService(Path(folder) / "work")
        image = Path(folder) / f"working{suffix}"
        image.write_bytes(bytes(size))
        original = Path(folder) / f"source.{container}"
        original.write_bytes(b"ORIGINAL")
        session = ImageSession(
            "a" * 32,
            f"source.{container}",
            kind,
            image,
            dirty=True,
            **{f"{container}_original_path": original},
        )
        plugin = "HXC_HFE" if container == "hfe" else "SCP_FLUX_STREAM"

        def convert(arguments):
            if any(item.startswith(f"-conv:{plugin}") for item in arguments):
                _write_output(arguments, b"REBUILT")
            else:
                _write_output(arguments, bytes(size - 512))
            return ""

        return service, session, convert

    def test_both_containers_repair_an_omitted_tail_sector_when_saving(self) -> None:
        for container in ("hfe", "scp"):
            with self.subTest(container=container):
                with tempfile.TemporaryDirectory() as folder:
                    service, session, convert = self._prepared(
                        folder, container, "ffs", 901_120, ".adf"
                    )
                    with patch.object(service, "_run_hxcfe", side_effect=convert):
                        output = getattr(service, f"_prepare_{container}_download")(session)
                    self.assertEqual(output.read_bytes(), b"REBUILT")
                    self.assertTrue(output.name.endswith(f"-edited.{container}"))

    def test_both_containers_pass_the_original_as_the_timing_reference(self) -> None:
        for container in ("hfe", "scp"):
            with self.subTest(container=container):
                with tempfile.TemporaryDirectory() as folder:
                    service, session, convert = self._prepared(
                        folder, container, "ofs", 901_120, ".adf"
                    )
                    seen: list[list[str]] = []

                    def record(arguments):
                        seen.append(arguments)
                        return convert(arguments)

                    with patch.object(service, "_run_hxcfe", side_effect=record):
                        getattr(service, f"_prepare_{container}_download")(session)
                    reference = next(
                        item
                        for arguments in seen
                        for item in arguments
                        if item.startswith("-reffile:")
                    )
                    original = getattr(session, f"{container}_original_path")
                    self.assertEqual(reference, f"-reffile:{original}")


if __name__ == "__main__":
    unittest.main()
