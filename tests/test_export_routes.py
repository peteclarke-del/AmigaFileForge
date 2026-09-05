from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from flask import Flask

from app.errors import DiskError
from app.operations import OperationRegistry
from app.routes.images import create_images_blueprint


class ExportRouteTests(unittest.TestCase):
    """The export endpoints added with SCP support."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.service = Mock()
        self.session = SimpleNamespace(kind="ofs", name="disk.adf")
        self.service.get.return_value = self.session
        app = Flask(__name__)
        app.register_blueprint(
            create_images_blueprint(
                self.service,
                self.root,
                OperationRegistry(),
            )
        )

        @app.errorhandler(DiskError)
        def _disk_error(error):
            return {"error": str(error)}, 400

        self.client = app.test_client()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_available_formats_are_listed_for_the_open_image(self) -> None:
        self.service.export_formats.return_value = [
            {"format": "native", "extension": "adf", "label": "Native sector image (.adf)"},
            {"format": "scp", "extension": "scp", "label": "SuperCard Pro flux image (.scp)"},
        ]
        response = self.client.get(f"/api/images/{'a' * 32}/export/formats")
        self.assertEqual(response.status_code, 200)
        payload = json.loads(response.data)
        self.assertEqual(
            [entry["format"] for entry in payload["formats"]],
            ["native", "scp"],
        )
        self.service.export_formats.assert_called_once_with(self.session)

    def test_an_image_with_no_export_route_reports_an_empty_list(self) -> None:
        self.service.export_formats.return_value = []
        response = self.client.get(f"/api/images/{'a' * 32}/export/formats")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.data)["formats"], [])

    def test_export_defaults_to_the_native_sector_image(self) -> None:
        output = self.root / "disk-export.adf"
        output.write_bytes(b"SECTORS")
        self.service.export_image.return_value = (output, "disk-export.adf")
        response = self.client.get(f"/api/images/{'a' * 32}/export")
        self.assertEqual(response.status_code, 200)
        # send_file keeps the handle open until the response is released.
        response.close()
        self.service.export_image.assert_called_once_with(self.session, "native")

    def test_the_requested_format_is_passed_through_and_downloaded(self) -> None:
        output = self.root / "disk-export.scp"
        output.write_bytes(b"SCP-FLUX-BYTES")
        self.service.export_image.return_value = (output, "disk-export.scp")
        response = self.client.get(f"/api/images/{'a' * 32}/export?format=scp")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"SCP-FLUX-BYTES")
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn("disk-export.scp", response.headers["Content-Disposition"])
        response.close()
        self.service.export_image.assert_called_once_with(self.session, "scp")

    def test_an_unavailable_format_is_reported_rather_than_served(self) -> None:
        self.service.export_image.side_effect = DiskError(
            "“hfe” is not an available export format for this image."
        )
        response = self.client.get(f"/api/images/{'a' * 32}/export?format=hfe")
        self.assertEqual(response.status_code, 400)
        self.assertIn("not an available export format", json.loads(response.data)["error"])

    def test_a_failed_round_trip_is_reported_rather_than_served(self) -> None:
        self.service.export_image.side_effect = DiskError(
            "The exported SCP image did not decode back to identical sectors, "
            "so the export was discarded."
        )
        response = self.client.get(f"/api/images/{'a' * 32}/export?format=scp")
        self.assertEqual(response.status_code, 400)
        self.assertIn("did not decode back", json.loads(response.data)["error"])


if __name__ == "__main__":
    unittest.main()
