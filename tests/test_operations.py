from __future__ import annotations

import unittest
import uuid
import tempfile
import time
from pathlib import Path

from app.disk_service import DiskError, SESSION_OWNER
from app.operations import OperationCancelled, OperationRegistry


class OperationRegistryTests(unittest.TestCase):
    def test_progress_lifecycle(self):
        registry = OperationRegistry()
        operation_id = str(uuid.uuid4())

        registry.start(operation_id, "Preparing")
        registry.update(operation_id, "Copying", 2, 5)
        copying = registry.get(operation_id)
        registry.finish(operation_id)

        self.assertEqual(copying["message"], "Copying")
        self.assertEqual(copying["current"], 2)
        self.assertEqual(copying["total"], 5)
        self.assertEqual(registry.get(operation_id)["state"], "complete")

    def test_cancel_is_raised_at_the_next_progress_boundary(self):
        registry = OperationRegistry()
        operation_id = str(uuid.uuid4())
        registry.start(operation_id, "Copying")

        cancelling = registry.cancel(operation_id)

        self.assertEqual(cancelling["state"], "cancelling")
        with self.assertRaises(OperationCancelled):
            registry.update(operation_id, "Starting another file")
        registry.cancelled(operation_id)
        self.assertEqual(registry.get(operation_id)["state"], "cancelled")

    def test_progress_reports_elapsed_throughput_and_eta(self):
        registry = OperationRegistry()
        operation_id = str(uuid.uuid4())
        registry.start(operation_id, "Copying")
        registry._items[operation_id]["startedAt"] = time.time() - 2
        registry.update(operation_id, "Copying", 4, 10)

        progress = registry.get(operation_id)

        self.assertGreaterEqual(progress["elapsedSeconds"], 2)
        self.assertGreater(progress["ratePerSecond"], 1.5)
        self.assertGreater(progress["etaSeconds"], 2)

        registry.finish(operation_id)
        finished = registry.get(operation_id)["elapsedSeconds"]
        time.sleep(0.01)
        self.assertAlmostEqual(registry.get(operation_id)["elapsedSeconds"], finished, places=3)

    def test_cancel_can_arrive_before_the_worker_starts(self):
        registry = OperationRegistry()
        operation_id = str(uuid.uuid4())

        registry.cancel(operation_id)
        registry.start(operation_id, "Preparing")

        with self.assertRaises(OperationCancelled):
            registry.update(operation_id, "Starting first file")

    def test_tracked_operation_finishes_and_exposes_progress_callback(self):
        registry = OperationRegistry()
        operation_id = str(uuid.uuid4())

        with registry.tracked(operation_id, "Preparing", "Ready") as progress:
            progress("Working", 2, 3)

        item = registry.get(operation_id)
        self.assertEqual(item["state"], "complete")
        self.assertEqual(item["message"], "Ready")
        self.assertEqual(item["current"], 2)

    def test_tracked_operation_records_failures(self):
        registry = OperationRegistry()
        operation_id = str(uuid.uuid4())

        with self.assertRaisesRegex(RuntimeError, "broken"):
            with registry.tracked(operation_id, "Preparing"):
                raise RuntimeError("broken")

        item = registry.get(operation_id)
        self.assertEqual(item["state"], "failed")
        self.assertEqual(item["message"], "broken")

    def test_job_history_survives_restart_and_marks_running_job_interrupted(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "operations.json"
            operation_id = str(uuid.uuid4())
            registry = OperationRegistry(path)
            registry.start(operation_id, "Bulk copy")
            registry.details(operation_id, resumable=True, completed=[{"sourceSlot": 4}])

            restored = OperationRegistry(path).get(operation_id)

            self.assertEqual(restored["state"], "interrupted")
            self.assertTrue(restored["details"]["resumable"])
            self.assertEqual(restored["details"]["completed"][0]["sourceSlot"], 4)

    def test_job_records_are_private_to_the_browser_owner(self):
        registry = OperationRegistry()
        operation_id = str(uuid.uuid4())
        first = SESSION_OWNER.set("a" * 32)
        try:
            registry.start(operation_id, "Private copy")
        finally:
            SESSION_OWNER.reset(first)
        second = SESSION_OWNER.set("b" * 32)
        try:
            self.assertEqual(registry.list(), [])
            with self.assertRaises(DiskError):
                registry.get(operation_id)
            with self.assertRaises(DiskError):
                registry.cancel(operation_id)
        finally:
            SESSION_OWNER.reset(second)


if __name__ == "__main__":
    unittest.main()
