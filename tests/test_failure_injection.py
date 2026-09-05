from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from app.disk_service import DiskError, DiskService, ImageSession


class FailingStream(io.BytesIO):
    def __init__(self, payload: bytes, fail_after: int = 32):
        super().__init__(payload)
        self.fail_after = fail_after

    def read(self, size: int = -1) -> bytes:
        if self.tell() >= self.fail_after:
            raise OSError("injected upload failure")
        return super().read(min(size, self.fail_after - self.tell()))


class FailureInjectionTests(unittest.TestCase):
    def test_interrupted_upload_removes_the_incomplete_private_session(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder) / "work"
            service = DiskService(work)

            with self.assertRaisesRegex(OSError, "injected upload failure"):
                service.create_from_stream("broken.adf", FailingStream(bytes(4096)))

            self.assertEqual(list(work.iterdir()), [])
            self.assertEqual(service.sessions, {})

    def test_checkpoint_rollback_restores_exact_bytes_after_partial_write(self):
        with tempfile.TemporaryDirectory() as folder:
            service = DiskService(Path(folder) / "work")
            session = service.create_blank("ffs-hard", "ROLLBACK", capacity="4MB")
            original = session.path.read_bytes()
            token = service.begin_automatic_checkpoint(session, "injected partial write")
            with session.path.open("r+b") as image:
                image.seek(16)
                image.write(b"BROKEN WRITE")

            service.rollback_automatic_checkpoint(session, token)

            self.assertEqual(session.path.read_bytes(), original)

    def test_full_ofs_image_refuses_the_new_file_without_losing_existing_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = DiskService(root / "work")
            session = service.create_blank("adf", "Full")
            large = root / "large.bin"
            extra = root / "extra.bin"
            # An 880 KiB OFS volume stores 488 bytes in each 512-byte block,
            # so 800 KiB of payload leaves too little room for the second file.
            large.write_bytes(b"A" * (800 * 1024))
            extra.write_bytes(b"B" * (64 * 1024))
            service.put(session, "Large", large)

            with self.assertRaises(DiskError):
                service.put(session, "Extra", extra)

            self.assertEqual(service.read_file(session, "Large"), large.read_bytes())
            self.assertNotIn(
                "Extra",
                {row["name"] for row in service.list_directory(session, "", None)["entries"]},
            )

    def test_corrupt_catalogue_fails_validation_instead_of_returning_partial_data(self):
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "corrupt.adf"
            image.write_bytes(b"not an OFS catalogue")
            service = DiskService(Path(folder) / "work")
            session = ImageSession("f" * 32, image.name, "ofs", image)

            with self.assertRaises(DiskError):
                service.validate(session)


if __name__ == "__main__":
    unittest.main()
