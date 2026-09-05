from __future__ import annotations

import errno
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from amiga_floppy import (
    AMIGA_GEOMETRIES,
    FloppyDevice,
    FloppyError,
    available_devices,
    geometry,
    geometry_for_size,
)


def as_block_device(device: Path):
    """Treat one ordinary file as a block device for the duration of a test.

    No development machine has a floppy controller, so the adapter is exercised
    against a file standing in for the drive.
    """
    return patch(
        "amiga_floppy.device.is_block_device",
        side_effect=lambda path: str(path) == str(device),
    )


class FloppyGeometryTests(unittest.TestCase):
    def test_every_geometry_reports_its_canonical_image_size(self) -> None:
        expected = {
            "dd": 901_120, "hd": 1_802_240, "dd-40": 450_560,
            "dd-81": 912_384, "dd-82": 923_648,
            "pc-720": 737_280, "pc-1440": 1_474_560,
        }
        for identifier, size in expected.items():
            with self.subTest(geometry=identifier):
                self.assertEqual(AMIGA_GEOMETRIES[identifier].size, size)

    def test_an_unknown_geometry_names_the_accepted_values(self) -> None:
        with self.assertRaisesRegex(FloppyError, "Choose a floppy geometry"):
            geometry("betamax")

    def test_geometry_names_are_matched_without_case_or_padding(self) -> None:
        self.assertEqual(geometry("  DD-40 ").identifier, "dd-40")

    def test_a_size_identifies_its_geometry_when_unambiguous(self) -> None:
        self.assertEqual(geometry_for_size(901_120).identifier, "dd")
        self.assertEqual(geometry_for_size(1_802_240).identifier, "hd")

    def test_an_unknown_size_identifies_nothing(self) -> None:
        # An 800 KiB file is not any Amiga or CrossDOS geometry, so nothing is
        # guessed from it: a capture is only ever named by a shape it matches.
        self.assertIsNone(geometry_for_size(819_200))
        self.assertIsNone(geometry_for_size(12_345))


class FloppyProbeTests(unittest.TestCase):
    def test_a_missing_device_explains_that_there_is_no_controller(self) -> None:
        probe = FloppyDevice("/dev/definitely-not-a-floppy").probe()
        self.assertFalse(probe.available)
        self.assertIn("does not exist", probe.detail)

    def test_a_regular_file_is_not_accepted_as_a_drive(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            plain = Path(folder) / "notadevice"
            plain.write_bytes(b"")
            probe = FloppyDevice(plain).probe()
        self.assertFalse(probe.available)
        self.assertIn("not a block device", probe.detail)

    def test_a_ready_drive_reports_its_size(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            device = Path(folder) / "fd0"
            device.write_bytes(bytes(901_120))
            with as_block_device(device):
                probe = FloppyDevice(device).probe()
            self.assertTrue(probe.available)
            self.assertEqual(probe.size, 901_120)

    def test_an_empty_drive_is_reported_as_having_no_disk(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            device = Path(folder) / "fd0"
            device.write_bytes(bytes(1024))
            with as_block_device(device):
                with patch.object(Path, "open", side_effect=OSError(errno.ENOMEDIUM, "no medium")):
                    probe = FloppyDevice(device).probe()
        self.assertFalse(probe.available)
        self.assertIn("No readable disk", probe.detail)

    def test_a_permission_failure_names_the_group_to_join(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            device = Path(folder) / "fd0"
            device.write_bytes(bytes(1024))
            with as_block_device(device):
                with patch.object(Path, "open", side_effect=PermissionError("denied")):
                    probe = FloppyDevice(device).probe()
        self.assertFalse(probe.available)
        self.assertIn("floppy", probe.detail)


class FloppyReadTests(unittest.TestCase):
    def _drive(self, folder: str, payload: bytes):
        device = Path(folder) / "fd0"
        device.write_bytes(payload)
        return device

    def test_a_complete_disk_is_captured_at_its_declared_geometry(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            device = self._drive(folder, bytes(range(256)) * 3520)
            target = Path(folder) / "capture.adf"
            progress = Mock()
            with as_block_device(device):
                result = FloppyDevice(device).read(target, "dd", progress)
            self.assertEqual(result.size, 901_120)
            self.assertEqual(result.geometry, "dd")
            self.assertEqual(target.stat().st_size, 901_120)
            self.assertTrue(progress.called)

    def test_a_short_read_is_refused_and_leaves_no_partial_image(self) -> None:
        """A disk the controller cannot fully decode must not look complete."""
        with tempfile.TemporaryDirectory() as folder:
            device = self._drive(folder, bytes(100_000))
            target = Path(folder) / "capture.adf"
            with as_block_device(device):
                with self.assertRaisesRegex(FloppyError, "does not match the chosen format"):
                    FloppyDevice(device).read(target, "dd")
            self.assertFalse(target.exists())

    def test_a_read_error_names_the_track_and_suggests_flux(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            device = self._drive(folder, bytes(901_120))
            target = Path(folder) / "capture.adf"

            class _Failing:
                """A drive that reads a couple of tracks and then fails."""

                def __init__(self): self.calls = 0
                def read(self, _size=-1):
                    self.calls += 1
                    if self.calls > 3:
                        raise OSError(errno.EIO, "media error")
                    return bytes(64 * 1024)
                def seek(self, _offset, _whence=0): return 901_120
                def __enter__(self): return self
                def __exit__(self, *_a): return False

            real_open = Path.open

            def fake_open(self_path, mode="r", *args, **kwargs):
                if str(self_path) == str(device) and "b" in mode and "r" in mode:
                    return _Failing()
                return real_open(self_path, mode, *args, **kwargs)

            with as_block_device(device), patch.object(Path, "open", fake_open):
                with self.assertRaisesRegex(FloppyError, "could not read track"):
                    FloppyDevice(device).read(target, "dd")
            self.assertFalse(target.exists())

    def test_reading_from_an_absent_drive_is_refused_before_any_file_is_made(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "capture.adf"
            with self.assertRaisesRegex(FloppyError, "does not exist"):
                FloppyDevice("/dev/definitely-not-a-floppy").read(target, "pc-720")
            self.assertFalse(target.exists())

    def test_an_unknown_geometry_is_refused_before_the_drive_is_touched(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(FloppyError, "Choose a floppy geometry"):
                FloppyDevice("/dev/definitely-not-a-floppy").read(
                    Path(folder) / "capture.adf", "vhs",
                )


class FloppyWriteTests(unittest.TestCase):
    def test_a_write_is_refused_until_it_is_confirmed(self) -> None:
        """The destructive step is never reached by default."""
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "disk.adf"
            image.write_bytes(bytes(901_120))
            with self.assertRaisesRegex(FloppyError, "cannot be undone"):
                FloppyDevice("/dev/definitely-not-a-floppy").write(image)

    def test_an_image_of_an_unsupported_size_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "disk.img"
            image.write_bytes(bytes(12_345))
            with self.assertRaisesRegex(FloppyError, "not one of the Amiga floppy"):
                FloppyDevice("/dev/definitely-not-a-floppy").write(image, confirm=True)

    def test_a_size_mismatch_against_the_drive_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            device = Path(folder) / "fd0"
            device.write_bytes(bytes(737_280))
            image = Path(folder) / "disk.adf"
            image.write_bytes(bytes(901_120))
            with as_block_device(device):
                with self.assertRaisesRegex(FloppyError, "Set the kernel geometry"):
                    FloppyDevice(device).write(image, confirm=True)

    def test_a_confirmed_write_of_a_matching_image_reaches_the_drive(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            device = Path(folder) / "fd0"
            device.write_bytes(bytes(901_120))
            image = Path(folder) / "disk.adf"
            image.write_bytes(b"\xA5" * 901_120)
            progress = Mock()
            with as_block_device(device):
                result = FloppyDevice(device).write(image, progress, confirm=True)
            self.assertEqual(result.size, 901_120)
            self.assertEqual(device.read_bytes(), b"\xA5" * 901_120)
            self.assertTrue(progress.called)


class DeviceDiscoveryTests(unittest.TestCase):
    def test_a_host_without_a_controller_lists_no_drives(self) -> None:
        # This machine has no floppy controller, which is the common case.
        self.assertEqual(available_devices(), [])


if __name__ == "__main__":
    unittest.main()


try:
    from flask import Flask, jsonify
    from app.disk_service import DiskError
    from app.operations import OperationRegistry
    from app.routes.desktop import create_desktop_blueprint
except ModuleNotFoundError:  # Flask is installed in the production image.
    Flask = None


@unittest.skipIf(Flask is None, "Flask is installed in the production image")
class FloppyDriveRouteTests(unittest.TestCase):
    """The desktop endpoints for a host with a real floppy controller."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.service = Mock()
        self.service.work_dir = Path(self.temporary.name)
        self.service.safe_filename = staticmethod(lambda value: value)
        self.session = Mock()
        self.service.create_from_path.return_value = self.session
        self.service.summary.return_value = {"id": "a" * 32, "kind": "ffs"}
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

    def test_the_status_endpoint_lists_the_supported_geometries(self) -> None:
        response = self.client.get("/api/desktop/floppy-drive")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        identifiers = {row["id"] for row in body["geometries"]}
        self.assertEqual(identifiers, set(AMIGA_GEOMETRIES))
        # This host has no controller, which the endpoint reports rather than hides.
        self.assertFalse(body["available"])

    def test_a_capture_is_opened_as_a_new_image(self) -> None:
        from amiga_floppy import FloppyReadResult

        with patch("app.routes.desktop.FloppyDevice") as device:
            device.return_value.read.return_value = FloppyReadResult(
                device="/dev/fd0", image="capture.adf", geometry="dd", size=901_120,
            )
            response = self.client.post(
                "/api/desktop/floppy-drive/read",
                json={"device": "/dev/fd0", "geometry": "dd", "name": "capture"},
            )
        self.assertEqual(response.status_code, 200)
        self.service.create_from_path.assert_called_once()
        destination = device.return_value.read.call_args[0][0]
        self.assertEqual(Path(destination).suffix, ".adf")

    def test_an_unknown_geometry_is_refused_before_the_drive_is_touched(self) -> None:
        with patch("app.routes.desktop.FloppyDevice") as device:
            response = self.client.post(
                "/api/desktop/floppy-drive/read",
                json={"device": "/dev/fd0", "geometry": "laserdisc"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Choose a floppy geometry", response.get_json()["error"])
        device.return_value.read.assert_not_called()

    def test_a_drive_failure_is_reported_and_opens_nothing(self) -> None:
        with patch("app.routes.desktop.FloppyDevice") as device:
            device.return_value.read.side_effect = FloppyError("The drive reported no disk.")
            response = self.client.post(
                "/api/desktop/floppy-drive/read",
                json={"device": "/dev/fd0", "geometry": "pc-720"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("no disk", response.get_json()["error"])
        self.service.create_from_path.assert_not_called()

    def test_the_capture_scratch_directory_does_not_outlive_the_request(self) -> None:
        from amiga_floppy import FloppyReadResult

        with patch("app.routes.desktop.FloppyDevice") as device:
            device.return_value.read.return_value = FloppyReadResult(
                device="/dev/fd0", image="capture.img", geometry="pc-720", size=737_280,
            )
            self.client.post(
                "/api/desktop/floppy-drive/read",
                json={"device": "/dev/fd0", "geometry": "pc-720"},
            )
        self.assertEqual(list(Path(self.temporary.name).glob("fd-read-*")), [])


class DeviceValidationTests(unittest.TestCase):
    """A request may name the drive, so the value must be constrained."""

    def test_real_floppy_nodes_are_accepted(self) -> None:
        from amiga_floppy import validated_device

        for name in ("/dev/fd0", "/dev/fd1", "/dev/fd0u800", "  /dev/fd0  "):
            with self.subTest(name=name):
                self.assertEqual(validated_device(name), name.strip())

    def test_the_accepted_value_is_a_known_constant_not_the_input(self) -> None:
        """Returning the caller's own string would carry its taint onward."""
        from amiga_floppy import KNOWN_DEVICES, validated_device

        result = validated_device("  /dev/fd0  ")
        self.assertIn(result, KNOWN_DEVICES)
        self.assertTrue(any(result is candidate for candidate in KNOWN_DEVICES))

    def test_other_block_devices_are_refused(self) -> None:
        """A system disk is a block device too, and must never be readable here."""
        from amiga_floppy import validated_device

        for name in ("/dev/sda", "/dev/sda1", "/dev/nvme0n1", "/dev/mapper/root"):
            with self.subTest(name=name):
                with self.assertRaisesRegex(FloppyError, "not a floppy device"):
                    validated_device(name)

    def test_traversal_and_arbitrary_paths_are_refused(self) -> None:
        from amiga_floppy import validated_device

        for name in ("/etc/passwd", "/dev/fd0/../sda", "../fd0", "", None, "fd0", "/dev/fd9"):
            with self.subTest(name=name):
                with self.assertRaises(FloppyError):
                    validated_device(name)


@unittest.skipIf(Flask is None, "Flask is installed in the production image")
class DeviceRouteHardeningTests(unittest.TestCase):
    """The endpoints must refuse a non-floppy device before touching it."""

    def setUp(self) -> None:
        from app.operations import OperationRegistry
        from app.routes.desktop import create_desktop_blueprint

        self.temporary = tempfile.TemporaryDirectory()
        self.service = Mock()
        self.service.work_dir = Path(self.temporary.name)
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

    def test_reading_a_system_disk_is_refused(self) -> None:
        with patch("app.routes.desktop.FloppyDevice") as device:
            response = self.client.post(
                "/api/desktop/floppy-drive/read",
                json={"device": "/dev/sda", "geometry": "pc-720"},
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("not a floppy device", response.get_json()["error"])
        device.return_value.read.assert_not_called()

    def test_the_capture_filename_never_comes_from_the_request(self) -> None:
        """A requested name is applied to the session, never to the path."""
        from amiga_floppy import FloppyReadResult

        with patch("app.routes.desktop.FloppyDevice") as device:
            device.return_value.read.return_value = FloppyReadResult(
                device="/dev/fd0", image="capture.adf", geometry="dd", size=901_120,
            )
            self.client.post(
                "/api/desktop/floppy-drive/read",
                json={"device": "/dev/fd0", "geometry": "dd", "name": "../../escape"},
            )
            destination = Path(device.return_value.read.call_args[0][0])
            self.assertEqual(destination.name, "capture.adf")
            self.assertTrue(
                str(destination.resolve()).startswith(str(Path(self.temporary.name).resolve())),
                f"capture escaped to {destination}",
            )
