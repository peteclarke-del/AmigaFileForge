from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from amiga_greaseweazle import (
    GreaseweazleClient,
    GreaseweazleError,
    ProbeResult,
    ReadResult,
    WriteResult,
    image_format,
    stable_snapshot,
)

try:
    from flask import Flask, jsonify
    from app.disk_service import DiskError
    from app.image_session import ImageSession
    from app.routes.desktop import create_desktop_blueprint
except ModuleNotFoundError:
    Flask = create_desktop_blueprint = None


class _Process:
    def __init__(self, output: str, return_code: int = 0) -> None:
        self.stdout = io.StringIO(output)
        self.return_code = return_code
        self.terminated = False

    def wait(self, timeout=None):
        return self.return_code

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True


class GreaseweazleTests(unittest.TestCase):
    def test_supported_formats_apply_correct_verification_policy(self) -> None:
        self.assertTrue(image_format("game.adf").automatic_verification)
        self.assertTrue(image_format("utilities.ADZ").automatic_verification)
        self.assertFalse(image_format("preserved.hfe").automatic_verification)
        self.assertFalse(image_format("greaseweazle-capture.scp").automatic_verification)
        with self.assertRaisesRegex(GreaseweazleError, "not a floppy image"):
            image_format("scsi0.hda")

    def test_snapshot_has_stable_bytes_and_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "game.adf"
            source.write_bytes(b"original")
            with stable_snapshot(source, temporary) as snapshot:
                source.write_bytes(b"changed")
                self.assertEqual(snapshot.read_bytes(), b"original")
                snapshot_path = snapshot
            self.assertFalse(snapshot_path.exists())

    @patch("amiga_greaseweazle.client.subprocess.Popen")
    @patch("amiga_greaseweazle.client.subprocess.run")
    def test_sector_image_requires_and_reports_verification(self, run, popen) -> None:
        run.return_value = subprocess.CompletedProcess(["gw", "info"], 0, "Device: Greaseweazle")
        popen.return_value = _Process(
            "Writing c=0-1:h=0-1\nT0.0: Written and verified\nT0.1: Written and verified\n"
            "T1.0: Written and verified\nT1.1: Written and verified\nAll tracks verified\n"
        )
        progress = Mock()
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "game.adf"
            image.write_bytes(b"disk")
            result = GreaseweazleClient("/usr/bin/gw").write(image, "A", progress)

        self.assertTrue(result.verified)
        self.assertTrue(result.verification_supported)
        self.assertEqual(result.tracks_written, 4)
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0][:3], ["/usr/bin/gw", "write", "--drive=A"])

    @patch("amiga_greaseweazle.client.subprocess.Popen")
    @patch("amiga_greaseweazle.client.subprocess.run")
    def test_hfe_can_complete_with_explicitly_unavailable_verification(self, run, popen) -> None:
        run.return_value = subprocess.CompletedProcess(["gw", "info"], 0, "Device ready")
        popen.return_value = _Process("Writing c=0-0:h=0-0\nT0.0: Written\n")
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "preserved.hfe"
            image.write_bytes(b"hfe")
            result = GreaseweazleClient("/usr/bin/gw").write(image, "0")

        self.assertFalse(result.verified)
        self.assertFalse(result.verification_supported)

    @patch("amiga_greaseweazle.client.subprocess.Popen")
    @patch("amiga_greaseweazle.client.subprocess.run")
    def test_missing_sector_verification_is_a_failure(self, run, popen) -> None:
        run.return_value = subprocess.CompletedProcess(["gw", "info"], 0, "Device ready")
        popen.return_value = _Process("Writing c=0-0:h=0-0\nT0.0: Written\n")
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "game.adf"
            image.write_bytes(b"disk")
            with self.assertRaisesRegex(GreaseweazleError, "without confirming"):
                GreaseweazleClient("/usr/bin/gw").write(image, "A")

    @patch("amiga_greaseweazle.client.subprocess.Popen")
    @patch("amiga_greaseweazle.client.subprocess.run")
    def test_cancellation_terminates_the_active_hardware_command(self, run, popen) -> None:
        run.return_value = subprocess.CompletedProcess(["gw", "info"], 0, "Device ready")
        process = _Process("Writing c=0-79:h=0-1\nT0.0: Written and verified\n")
        popen.return_value = process
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "game.adf"
            image.write_bytes(b"disk")

            reports = 0

            def cancel(_message, _current=None, _total=None):
                nonlocal reports
                reports += 1
                if reports > 1:
                    raise RuntimeError("cancel requested")

            with self.assertRaisesRegex(RuntimeError, "cancel requested"):
                GreaseweazleClient("/usr/bin/gw").write(image, "A", cancel)

        self.assertTrue(process.terminated)

    def test_drive_identifier_cannot_be_used_as_command_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "game.adf"
            image.write_bytes(b"disk")
            with self.assertRaisesRegex(GreaseweazleError, "Choose Greaseweazle drive"):
                GreaseweazleClient("/usr/bin/gw").write(image, "A; eject")

    def test_probe_explains_missing_command(self) -> None:
        # Isolate PATH discovery so this remains valid on a Greaseweazle machine.
        with patch("amiga_greaseweazle.client.shutil.which", return_value=None):
            result = GreaseweazleClient().probe()
        self.assertFalse(result.available)
        self.assertIn("not installed", result.detail)

    @unittest.skipIf(Flask is None, "Flask is available in the application environment")
    @patch("app.routes.desktop.GreaseweazleClient.probe")
    def test_desktop_status_exposes_drive_and_verification_policy(self, probe) -> None:
        probe.return_value = ProbeResult(True, "/usr/bin/gw", "Device ready")
        headers = {"X-Amiga-Desktop-Token": "d" * 32}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image_path = root / "physical.adf"
            image_path.write_bytes(b"disk")
            session = ImageSession("image-id", "physical.adf", "ofs", image_path)
            service = Mock(work_dir=root)
            service.get.return_value = session
            service.summary.return_value = {"hardDisk": False}
            app = Flask(__name__)
            app.register_blueprint(create_desktop_blueprint(service))
            app.register_error_handler(DiskError, lambda error: (jsonify(error=str(error)), 400))
            client = app.test_client()
            response = client.get(
                "/api/desktop/images/image-id/physical-floppy",
                headers=headers,
            )

        self.assertEqual(response.status_code, 200)
        status = response.get_json()
        self.assertTrue(status["available"])
        self.assertTrue(status["media"]["automaticVerification"])
        self.assertEqual([item["id"] for item in status["drives"]], ["A", "B", "0", "1", "2", "3"])

    @unittest.skipIf(Flask is None, "Flask is available in the application environment")
    @patch("app.routes.desktop.GreaseweazleClient.write")
    def test_desktop_write_uses_snapshot_and_reports_result(self, write) -> None:
        write.side_effect = lambda path, drive, progress: WriteResult(
            drive=drive,
            image=Path(path).name,
            verified=True,
            verification_supported=True,
            tracks_written=80,
            output_tail=("All tracks verified",),
        )
        headers = {"X-Amiga-Desktop-Token": "d" * 32}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            image_path = root / "physical.adf"
            image_path.write_bytes(b"disk")
            session = ImageSession("image-id", "physical.adf", "ofs", image_path)
            service = Mock(work_dir=root)
            service.get.return_value = session
            service.summary.return_value = {"hardDisk": False}
            service.prepare_download.return_value = image_path
            app = Flask(__name__)
            app.register_blueprint(create_desktop_blueprint(service))
            app.register_error_handler(DiskError, lambda error: (jsonify(error=str(error)), 400))
            client = app.test_client()
            response = client.post(
                "/api/desktop/images/image-id/physical-floppy",
                json={"drive": "B"},
                headers=headers,
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["result"]["drive"], "B")
        written_path = Path(write.call_args.args[0])
        self.assertTrue(written_path.name.startswith("amiga-floppy-"))
        self.assertFalse(written_path.exists())




class GreaseweazleReadTests(unittest.TestCase):
    """Capturing a physical disk, the mirror of the existing write support."""

    @staticmethod
    def _capture(output: str, produced: bytes | None, return_code: int = 0):
        """Fake a gw run that writes ``produced`` to the requested destination."""
        def popen(command, **_kwargs):
            if produced is not None:
                Path(command[-1]).write_bytes(produced)
            return _Process(output, return_code)
        return popen

    def _client(self):
        return GreaseweazleClient(command="gw")

    @patch("amiga_greaseweazle.client.subprocess.Popen")
    @patch("amiga_greaseweazle.client.subprocess.run")
    def test_a_capture_reports_its_drive_geometry_and_size(self, run, popen) -> None:
        run.return_value = subprocess.CompletedProcess(["gw", "info"], 0, "Device: Greaseweazle")
        popen.side_effect = self._capture(
            "Reading c=0-1:h=0-1\nT0.0: Read\nT0.1: Read\nT1.0: Read\nT1.1: Read\n",
            bytes(204_800),
        )
        progress = Mock()
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "capture.adf"
            result = self._client().read(target, "A", progress)
        self.assertIsInstance(result, ReadResult)
        self.assertEqual(result.drive, "A")
        self.assertEqual(result.image, "capture.adf")
        self.assertEqual(result.tracks_read, 4)
        self.assertEqual(result.size, 204_800)
        self.assertTrue(progress.called)

    @patch("amiga_greaseweazle.client.subprocess.Popen")
    @patch("amiga_greaseweazle.client.subprocess.run")
    def test_a_flux_destination_is_accepted(self, run, popen) -> None:
        run.return_value = subprocess.CompletedProcess(["gw", "info"], 0, "Device: Greaseweazle")
        popen.side_effect = self._capture("Reading c=0-0:h=0-0\nT0.0: Read\n", b"SCP" + bytes(64))
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "capture.scp"
            result = self._client().read(target, "0")
        self.assertEqual(result.image, "capture.scp")
        self.assertEqual(result.size, 67)

    @patch("amiga_greaseweazle.client.subprocess.Popen")
    @patch("amiga_greaseweazle.client.subprocess.run")
    def test_the_requested_revolutions_reach_the_command(self, run, popen) -> None:
        run.return_value = subprocess.CompletedProcess(["gw", "info"], 0, "Device: Greaseweazle")
        seen = []

        def record(command, **_kwargs):
            seen.append(command)
            Path(command[-1]).write_bytes(bytes(1024))
            return _Process("T0.0: Read\n")

        popen.side_effect = record
        with tempfile.TemporaryDirectory() as folder:
            self._client().read(Path(folder) / "capture.scp", "A", revolutions=3)
        self.assertIn("--revs=3", seen[0])
        self.assertIn("--drive=A", seen[0])
        self.assertEqual(seen[0][1], "read")

    @patch("amiga_greaseweazle.client.subprocess.run")
    def test_an_implausible_revolution_count_is_refused(self, run) -> None:
        run.return_value = subprocess.CompletedProcess(["gw", "info"], 0, "Device: Greaseweazle")
        with tempfile.TemporaryDirectory() as folder:
            for revolutions in (0, 11, -1):
                with self.subTest(revolutions=revolutions):
                    with self.assertRaisesRegex(GreaseweazleError, "between 1 and 10"):
                        self._client().read(
                            Path(folder) / "capture.scp", "A", revolutions=revolutions,
                        )

    @patch("amiga_greaseweazle.client.subprocess.Popen")
    @patch("amiga_greaseweazle.client.subprocess.run")
    def test_a_failed_capture_leaves_no_partial_image_behind(self, run, popen) -> None:
        """A half-written capture must never be mistaken for a real disk."""
        run.return_value = subprocess.CompletedProcess(["gw", "info"], 0, "Device: Greaseweazle")
        popen.side_effect = self._capture(
            "Reading c=0-0:h=0-0\nT0.0: Read\nERROR: no disk\n", bytes(512), return_code=1,
        )
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "capture.adf"
            with self.assertRaisesRegex(GreaseweazleError, "could not read the physical disk"):
                self._client().read(target, "A")
            self.assertFalse(target.exists())

    @patch("amiga_greaseweazle.client.subprocess.Popen")
    @patch("amiga_greaseweazle.client.subprocess.run")
    def test_a_capture_that_produced_nothing_is_reported(self, run, popen) -> None:
        run.return_value = subprocess.CompletedProcess(["gw", "info"], 0, "Device: Greaseweazle")
        popen.side_effect = self._capture("No disk detected\n", None)
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(GreaseweazleError, "without producing an image"):
                self._client().read(Path(folder) / "capture.adf", "A")

    @patch("amiga_greaseweazle.client.subprocess.Popen")
    @patch("amiga_greaseweazle.client.subprocess.run")
    def test_an_empty_capture_file_is_rejected(self, run, popen) -> None:
        run.return_value = subprocess.CompletedProcess(["gw", "info"], 0, "Device: Greaseweazle")
        popen.side_effect = self._capture("T0.0: Read\n", b"")
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(GreaseweazleError, "without producing an image"):
                self._client().read(Path(folder) / "capture.adf", "A")

    def test_an_unsupported_destination_suffix_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(GreaseweazleError, "not a floppy image"):
                self._client().read(Path(folder) / "capture.txt", "A")

    @patch("amiga_greaseweazle.client.subprocess.run")
    def test_an_invalid_drive_is_refused_before_any_device_access(self, run) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(GreaseweazleError, "drive A, B, 0, 1, 2 or 3"):
                self._client().read(Path(folder) / "capture.adf", "Z")
        run.assert_not_called()

    @patch("amiga_greaseweazle.client.shutil.which", return_value=None)
    def test_a_missing_gw_installation_is_reported(self, _which) -> None:
        """An absent command is explained, not attempted."""
        client = GreaseweazleClient()
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(GreaseweazleError, "gw command is not installed"):
                client.read(Path(folder) / "capture.adf", "A")


class GreaseweazleSharedStreamTests(unittest.TestCase):
    """Both directions share one process, progress and cancellation path."""

    def test_read_and_write_use_the_same_streaming_implementation(self) -> None:
        import inspect

        from amiga_greaseweazle.client import GreaseweazleClient as Client

        for direction in ("read", "write"):
            with self.subTest(direction=direction):
                source = inspect.getsource(getattr(Client, direction))
                self.assertIn("self._stream(", source)
                self.assertNotIn("subprocess.Popen", source)


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(Flask is None, "Flask is installed in the production image")
class PhysicalReadRouteTests(unittest.TestCase):
    """The desktop endpoint that turns a physical disk into a working image."""

    def setUp(self) -> None:
        from app.operations import OperationRegistry
        from app.routes.desktop import create_desktop_blueprint

        self.temporary = tempfile.TemporaryDirectory()
        self.service = Mock()
        self.service.work_dir = Path(self.temporary.name)
        self.service.safe_filename = staticmethod(lambda value: value)
        self.session = Mock()
        self.service.create_from_path.return_value = self.session
        self.service.summary.return_value = {"id": "a" * 32, "kind": "ofs"}
        app = Flask(__name__)
        app.register_blueprint(
            create_desktop_blueprint(self.service, OperationRegistry(), Mock())
        )

        @app.errorhandler(DiskError)
        def _disk_error(error):
            return jsonify(error=str(error)), 400

        self.client = app.test_client()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _read_result(self, name="capture.adf"):
        return ReadResult(drive="A", image=name, tracks_read=80, size=204_800, output_tail=())

    def test_a_capture_is_opened_as_a_new_image(self) -> None:
        with patch("app.routes.desktop.GreaseweazleClient") as client:
            client.return_value.read.return_value = self._read_result()
            response = self.client.post(
                "/api/desktop/physical-floppy/read",
                json={"drive": "A", "format": "adf", "name": "capture"},
            )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertEqual(body["result"]["tracksRead" if "tracksRead" in body["result"] else "tracks_read"], 80)
        self.service.create_from_path.assert_called_once()
        self.service.summary.assert_called_once_with(self.session)

    def test_the_requested_capture_format_selects_the_destination_suffix(self) -> None:
        for requested, suffix in (("scp", ".scp"), ("img", ".img"), ("ipf", ".ipf"), ("hfe", ".hfe")):
            with self.subTest(format=requested):
                self.service.create_from_path.reset_mock()
                with patch("app.routes.desktop.GreaseweazleClient") as client:
                    client.return_value.read.return_value = self._read_result(f"capture{suffix}")
                    response = self.client.post(
                        "/api/desktop/physical-floppy/read",
                        json={"drive": "A", "format": requested},
                    )
                self.assertEqual(response.status_code, 200)
                destination = client.return_value.read.call_args[0][0]
                self.assertEqual(Path(destination).suffix, suffix)

    def test_an_unsupported_capture_format_is_refused(self) -> None:
        response = self.client.post(
            "/api/desktop/physical-floppy/read",
            json={"drive": "A", "format": "exe"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Choose a capture format", response.get_json()["error"])
        self.service.create_from_path.assert_not_called()

    def test_a_hardware_failure_is_reported_and_opens_nothing(self) -> None:
        with patch("app.routes.desktop.GreaseweazleClient") as client:
            client.return_value.read.side_effect = GreaseweazleError("No disk in drive A.")
            response = self.client.post(
                "/api/desktop/physical-floppy/read",
                json={"drive": "A", "format": "adf"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("No disk in drive A.", response.get_json()["error"])
        self.service.create_from_path.assert_not_called()

    def test_the_capture_scratch_directory_does_not_outlive_the_request(self) -> None:
        with patch("app.routes.desktop.GreaseweazleClient") as client:
            client.return_value.read.return_value = self._read_result()
            self.client.post(
                "/api/desktop/physical-floppy/read",
                json={"drive": "A", "format": "adf"},
            )
        leftovers = list(Path(self.temporary.name).glob("gw-read-*"))
        self.assertEqual(leftovers, [], "the capture scratch directory must be removed")
