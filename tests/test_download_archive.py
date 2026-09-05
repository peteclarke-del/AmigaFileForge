from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from app.disk_service import DiskError, DiskService, ImageSession
from app.download_archive import build_download_archive, prepared_download


class DownloadArchiveTests(unittest.TestCase):
    @staticmethod
    def _hdf_session(root: Path) -> tuple[DiskService, ImageSession]:
        """A real partitioned drive, which is what a download packages."""
        service = DiskService(root / "work")
        session = service.create_blank("ffs-hard", "Games", capacity="4MB")
        session.name = "games.hdf"
        session.dirty = True
        return service, session

    def test_prepare_builds_complete_archive_before_reporting_ready(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, session = self._hdf_session(Path(directory))
            progress = []

            archive_path, archive_name = build_download_archive(
                service,
                session,
                lambda message, current=None, total=None: progress.append(
                    (message, current, total)
                ),
            )

            self.assertTrue(archive_path.is_file())
            self.assertTrue(archive_name.startswith("games-"))
            self.assertEqual(progress[-1], (
                "The complete ZIP is ready to download", 100, 100,
            ))
            self.assertTrue(any(current and current >= 40 for _message, current, _total in progress))
            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(archive.namelist(), ["README.md", "games.hdf"])
                self.assertEqual(archive.read("games.hdf"), session.path.read_bytes())

            self.assertEqual(prepared_download(session), (archive_path, archive_name))

    def test_prepared_archive_is_rejected_after_the_image_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, session = self._hdf_session(Path(directory))
            build_download_archive(service, session)

            with session.path.open("r+b") as image:
                image.seek(0)
                image.write(b"changed")

            with self.assertRaisesRegex(DiskError, "changed afterward"):
                prepared_download(session)

    def test_accepted_compatibility_report_is_packaged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, session = self._hdf_session(Path(directory))
            session.compatibility_reports = [{
                "format": "amiga-file-forge-compatibility-report",
                "version": 1,
                "operation": "copy",
                "markdown": "# Accepted report\n",
                "acceptedAt": "2026-08-17T12:00:00+00:00",
            }]
            archive_path, _archive_name = build_download_archive(service, session)
            with zipfile.ZipFile(archive_path) as archive:
                self.assertIn("Compatibility/accepted-report.json", archive.namelist())
                self.assertIn("Compatibility/accepted-report.md", archive.namelist())
                self.assertEqual(archive.read("Compatibility/accepted-report.md"), b"# Accepted report\n")
                document = archive.read("Compatibility/accepted-report.json").decode("utf-8")
                self.assertNotIn('"markdown"', document)

    def test_accepting_report_invalidates_previously_prepared_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, session = self._hdf_session(Path(directory))
            build_download_archive(service, session)
            session.compatibility_reports = [{"acceptedAt": "2026-08-17T12:00:00+00:00"}]
            with self.assertRaisesRegex(DiskError, "Save it again"):
                prepared_download(session)

    def test_sparse_hardfile_archive_is_compressed_and_byte_exact(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "scsi0.hda"
            descriptor = root / "scsi0.geo"
            with image.open("wb") as output:
                output.write(b"FFS")
                output.seek(32 * 1024 * 1024 - 1)
                output.write(b"\0")
            descriptor.write_bytes(b"geometry")
            service = DiskService(root / "work")
            service._optimise_sparse_file(image)
            service.prepare_download = lambda session, progress=None: image
            session = ImageSession(
                "b" * 32,
                image.name,
                "ffs",
                image,
                descriptor_name=descriptor.name,
                descriptor_path=descriptor,
            )

            def write_readme(_service, _session, _path, _generated, **_checksums):
                readme = root / "download-README.md"
                readme.write_text("test", encoding="utf-8")
                return readme

            with patch("app.download_archive.write_download_readme", write_readme):
                archive_path, _archive_name = build_download_archive(service, session)

            self.assertLess(archive_path.stat().st_size, 200_000)
            with zipfile.ZipFile(archive_path) as archive:
                self.assertEqual(
                    archive.read("Hardfile0/scsi0.hda"),
                    image.read_bytes(),
                )


if __name__ == "__main__":
    unittest.main()
