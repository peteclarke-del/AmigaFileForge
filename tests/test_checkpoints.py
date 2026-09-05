from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.disk_service import DiskError, DiskService, ImageSession


class CheckpointTests(unittest.TestCase):
    def make_session(self, root: Path, *, paired: bool = False) -> tuple[DiskService, ImageSession]:
        folder = root / ("a" * 32)
        folder.mkdir()
        image = folder / ("scsi0.hda" if paired else "games.adf")
        image.write_bytes(b"original image")
        descriptor = folder / "scsi0.geo" if paired else None
        if descriptor:
            descriptor.write_bytes(b"original descriptor")
        service = DiskService(root)
        session = ImageSession(
            "a" * 32,
            image.name,
            "ffs" if paired else "raw",
            image,
            descriptor_name=descriptor.name if descriptor else None,
            descriptor_path=descriptor,
        )
        return service, session

    def test_named_checkpoint_restores_image_descriptor_and_session_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, session = self.make_session(Path(directory), paired=True)
            checkpoint = service.create_checkpoint(session, "Known good")

            session.path.write_bytes(b"changed image")
            session.descriptor_path.write_bytes(b"changed descriptor")
            session.name = "changed.hda"
            session.descriptor_name = "changed.geo"
            session.dirty = True
            session.warnings = ["changed"]
            restored = service.restore_checkpoint(session, checkpoint["id"])

            self.assertEqual(restored["name"], "Known good")
            self.assertEqual(session.path.read_bytes(), b"original image")
            self.assertEqual(session.descriptor_path.read_bytes(), b"original descriptor")
            self.assertEqual(session.name, "scsi0.hda")
            self.assertEqual(session.descriptor_name, "scsi0.geo")
            self.assertFalse(session.dirty)
            self.assertEqual(session.warnings, [])

    def test_oldest_snapshot_exposes_validated_primary_descriptor_and_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, session = self.make_session(Path(directory), paired=True)
            oldest = service.create_checkpoint(session, "Workflow base")
            session.path.write_bytes(b"later")
            service.create_checkpoint(session, "Later point")

            image, descriptor, metadata = service.oldest_checkpoint_snapshot(session)

            self.assertEqual(metadata["id"], oldest["id"])
            self.assertEqual(image.read_bytes(), b"original image")
            self.assertEqual(descriptor.read_bytes(), b"original descriptor")
            self.assertEqual(metadata["reason"], "Workflow base")

    def test_undo_restores_and_consumes_latest_automatic_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, session = self.make_session(Path(directory))
            first = service.begin_automatic_checkpoint(session, "adding a file")
            session.path.write_bytes(b"first edit")
            session.dirty = True
            service.finish_automatic_checkpoint(session, first)
            second = service.begin_automatic_checkpoint(session, "deleting a file")
            session.path.write_bytes(b"second edit")
            service.finish_automatic_checkpoint(session, second)

            undone = service.undo_last_change(session)

            self.assertEqual(undone["reason"], "deleting a file")
            self.assertEqual(session.path.read_bytes(), b"first edit")
            self.assertTrue(service.summary(session)["checkpoints"]["canUndo"])
            service.undo_last_change(session)
            self.assertEqual(session.path.read_bytes(), b"original image")
            self.assertFalse(service.summary(session)["checkpoints"]["canUndo"])

    def test_unchanged_operation_drops_speculative_undo_point(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, session = self.make_session(Path(directory))
            token = service.begin_automatic_checkpoint(session, "checking something")

            service.finish_automatic_checkpoint(session, token)

            self.assertEqual(service.list_checkpoints(session), [])

    def test_mutation_finaliser_persists_dirty_recovery_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, session = self.make_session(Path(directory))
            token = service.begin_automatic_checkpoint(session, "editing a file")
            session.dirty = True

            service.finish_automatic_checkpoint(session, token)

            restored = service._restore_session(session.id)
            self.assertTrue(restored.dirty)

    def test_no_op_does_not_prune_existing_undo_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, session = self.make_session(Path(directory))
            service.checkpoints.automatic_limit = 2
            for number in range(2):
                token = service.begin_automatic_checkpoint(session, f"edit {number}")
                session.path.write_bytes(f"edit {number}".encode())
                service.finish_automatic_checkpoint(session, token)
            no_op = service.begin_automatic_checkpoint(session, "no-op")

            service.finish_automatic_checkpoint(session, no_op)

            self.assertEqual(
                [item["reason"] for item in service.list_checkpoints(session)],
                ["edit 1", "edit 0"],
            )

    def test_named_checkpoint_is_not_consumed_by_undo(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, session = self.make_session(Path(directory))
            named = service.create_checkpoint(session, "Before menu work")
            session.path.write_bytes(b"changed")

            with self.assertRaisesRegex(DiskError, "no automatic checkpoint"):
                service.undo_last_change(session)

            self.assertEqual(service.list_checkpoints(session)[0]["id"], named["id"])

    def test_checkpoint_names_are_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service, session = self.make_session(Path(directory))
            with self.assertRaisesRegex(DiskError, "Enter a name"):
                service.create_checkpoint(session, "   ")
            with self.assertRaisesRegex(DiskError, "at most 60"):
                service.create_checkpoint(session, "x" * 61)


if __name__ == "__main__":
    unittest.main()
