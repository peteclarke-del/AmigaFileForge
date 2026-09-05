from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from app.platform_contract import (
    HOST_EXCLUSIVE_ENDPOINTS,
    PLATFORM_CONTRACT_FORMAT,
    PLATFORM_CONTRACT_VERSION,
    PlatformRuntime,
)

try:
    from app.server import create_app
    from app.routes.desktop import _image_pair
    from desktop.__main__ import _paired_selection, _review_open_plans
    from desktop.runtime import DesktopServer, desktop_paths
except ModuleNotFoundError:  # Flask and Werkzeug are container dependencies.
    create_app = DesktopServer = desktop_paths = _image_pair = _paired_selection = _review_open_plans = None


class PlatformContractTests(unittest.TestCase):
    def test_desktop_runtime_requires_private_token(self) -> None:
        with self.assertRaisesRegex(ValueError, "private launch token"):
            PlatformRuntime("desktop", "short")
        self.assertEqual(PlatformRuntime().public_contract()["host"], "web")

    @unittest.skipIf(create_app is None, "Flask is available in the application container")
    def test_web_and_desktop_route_maps_differ_only_by_declared_adapters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            web = create_app(work_dir=root / "web")
            desktop = create_app(
                work_dir=root / "desktop",
                platform="desktop",
                desktop_token="d" * 32,
                desktop_owner="o" * 32,
            )

        web_routes = {rule.endpoint for rule in web.url_map.iter_rules()}
        desktop_routes = {rule.endpoint for rule in desktop.url_map.iter_rules()}
        self.assertEqual(web_routes - desktop_routes, HOST_EXCLUSIVE_ENDPOINTS["web"])
        self.assertEqual(
            desktop_routes - web_routes,
            HOST_EXCLUSIVE_ENDPOINTS["desktop"],
        )
        self.assertEqual(web.static_folder, desktop.static_folder)
        self.assertFalse(web.extensions["amiga_interactive_emulator"].native)
        self.assertTrue(desktop.extensions["amiga_interactive_emulator"].native)

    @unittest.skipIf(create_app is None, "Flask is available in the application container")
    def test_desktop_service_rejects_requests_without_launch_token(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            app = create_app(
                work_dir=Path(temporary),
                platform="desktop",
                desktop_token="d" * 32,
                desktop_owner="o" * 32,
            )
            client = app.test_client()
            denied = client.get("/api/health")
            allowed = client.get(
                "/api/health", headers={"X-Amiga-Desktop-Token": "d" * 32}
            )

        self.assertEqual(denied.status_code, 403)
        self.assertNotIn("X-Amiga-Session-Owner", denied.headers)
        self.assertNotIn("amiga_file_forge_owner", denied.headers.get("Set-Cookie", ""))
        self.assertEqual(allowed.status_code, 200)
        contract = allowed.get_json()["platform"]
        self.assertEqual(contract["format"], PLATFORM_CONTRACT_FORMAT)
        self.assertEqual(contract["version"], PLATFORM_CONTRACT_VERSION)
        self.assertEqual(contract["host"], "desktop")
        self.assertIn("native-file-drop", contract["hostCapabilities"])

    @unittest.skipIf(create_app is None, "Flask is available in the application container")
    def test_web_and_desktop_responses_share_security_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            web = create_app(work_dir=Path(temporary) / "web")
            response = web.test_client().get("/api/health")

        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "SAMEORIGIN")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertIn("camera=()", response.headers["Permissions-Policy"])
        self.assertIn("object-src 'none'", response.headers["Content-Security-Policy"])
        self.assertIn("frame-ancestors 'self'", response.headers["Content-Security-Policy"])
        self.assertIn("http://*:8668", response.headers["Content-Security-Policy"])

    @unittest.skipIf(DesktopServer is None, "Flask is available in the application container")
    def test_desktop_server_binds_random_loopback_port(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            with DesktopServer(Path(temporary)) as server:
                port = server.port
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(
                        f"http://127.0.0.1:{server.port}/api/health", timeout=5
                    )
                request = urllib.request.Request(
                    f"http://127.0.0.1:{server.port}/api/health",
                    headers={"X-Amiga-Desktop-Token": server.token},
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    health = json.load(response)

        self.assertEqual(denied.exception.code, 403)
        denied.exception.close()
        self.assertGreater(port, 0)
        self.assertEqual(health["platform"]["host"], "desktop")

    @unittest.skipIf(DesktopServer is None, "Flask is available in the application container")
    def test_desktop_owner_is_stable_while_launch_authentication_rotates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary) / "work"
            first = DesktopServer(work)
            second = DesktopServer(work)

        self.assertEqual(first.owner, second.owner)
        self.assertNotEqual(first.token, second.token)

    @unittest.skipIf(DesktopServer is None, "Flask is available in the application container")
    def test_desktop_owner_recovers_from_non_ascii_state_and_is_private(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary) / "work"
            config = Path(temporary) / "config"
            config.mkdir()
            owner_path = config / "owner-id"
            owner_path.write_bytes(b"\xffbroken")

            server = DesktopServer(work)

            self.assertRegex(server.owner, r"^[A-Za-z0-9_-]{32,64}$")
            self.assertEqual(owner_path.stat().st_mode & 0o777, 0o600)

    @unittest.skipIf(desktop_paths is None, "Desktop runtime dependencies unavailable")
    def test_desktop_paths_follow_xdg_locations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ,
            {
                "XDG_DATA_HOME": f"{temporary}/data",
                "XDG_CONFIG_HOME": f"{temporary}/config",
                "XDG_CACHE_HOME": f"{temporary}/cache",
            },
        ):
            paths = desktop_paths()

        self.assertEqual(paths.work, Path(temporary) / "data/amiga-file-forge/work")
        self.assertEqual(paths.config, Path(temporary) / "config/amiga-file-forge")
        self.assertEqual(paths.cache, Path(temporary) / "cache/amiga-file-forge")

    @unittest.skipIf(desktop_paths is None, "Desktop runtime dependencies unavailable")
    def test_desktop_paths_ignore_an_ide_snap_private_xdg_home(self) -> None:
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            os.environ,
            {
                "HOME": temporary,
                "XDG_DATA_HOME": f"{temporary}/snap/code/current/.local/share",
                "XDG_CONFIG_HOME": f"{temporary}/snap/code/current/.config",
                "XDG_CACHE_HOME": f"{temporary}/snap/code/current/.cache",
            },
        ):
            paths = desktop_paths()

        self.assertEqual(paths.data, Path(temporary) / ".local/share/amiga-file-forge")
        self.assertEqual(paths.config, Path(temporary) / ".config/amiga-file-forge")
        self.assertEqual(paths.cache, Path(temporary) / ".cache/amiga-file-forge")

    @unittest.skipIf(_image_pair is None, "Flask is available in the application container")
    def test_desktop_open_pairs_dat_and_dsc_from_either_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hda = root / "SCSI0.HDA"
            geo = root / "scsi0.geo"
            hda.write_bytes(b"data")
            geo.write_bytes(b"descriptor")

            from_dat = _image_pair({"path": str(hda)})
            from_dsc = _image_pair({"path": str(geo)})

        self.assertEqual(from_dat, (hda, geo))
        self.assertEqual(from_dsc, (hda, geo))

    @unittest.skipIf(_paired_selection is None, "Desktop dependencies unavailable")
    def test_native_multi_file_selection_collapses_matching_dat_dsc_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            hda = root / "SCSI0.HDA"
            geo = root / "scsi0.geo"
            adf = root / "game.adf"
            for path in (hda, geo, adf):
                path.touch()

            paired = _paired_selection([geo, adf, hda])
            descriptor_only = _paired_selection([geo])

        self.assertEqual(paired, [adf.resolve(), hda.resolve()])
        self.assertEqual(descriptor_only, [geo.resolve()])

    @unittest.skipIf(_review_open_plans is None, "Desktop dependencies unavailable")
    def test_native_open_batch_is_validated_before_it_is_queued(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            image = Path(temporary) / "one.adf"
            image.touch()
            message = json.dumps({
                "command": "open-plans",
                "plans": [
                    {"paths": [str(image)]},
                    {"paths": [str(Path(temporary) / "missing.adf")]},
                ],
            })

            with self.assertRaisesRegex(ValueError, "no longer available"):
                _review_open_plans(message)


if __name__ == "__main__":
    unittest.main()
