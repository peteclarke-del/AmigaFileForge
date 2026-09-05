from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from desktop import webview_host
from desktop.runtime import desktop_paths


class DesktopPathTests(unittest.TestCase):
    """Each desktop keeps its per-user state where that platform expects it."""

    def _paths_for(self, platform: str, environment: dict[str, str]):
        with patch("desktop.runtime.sys.platform", platform):
            with patch.dict("os.environ", environment, clear=True):
                with patch("desktop.runtime.Path.home", return_value=Path("/home/user")):
                    return desktop_paths()

    def test_linux_uses_xdg_directories(self) -> None:
        paths = self._paths_for("linux", {})
        self.assertEqual(paths.data, Path("/home/user/.local/share/amiga-file-forge"))
        self.assertEqual(paths.config, Path("/home/user/.config/amiga-file-forge"))
        self.assertEqual(paths.cache, Path("/home/user/.cache/amiga-file-forge"))
        self.assertEqual(paths.work, paths.data / "work")

    def test_linux_honours_an_xdg_override(self) -> None:
        paths = self._paths_for("linux", {"XDG_DATA_HOME": "/custom/data"})
        self.assertEqual(paths.data, Path("/custom/data/amiga-file-forge"))

    def test_macos_uses_application_support_and_caches(self) -> None:
        paths = self._paths_for("darwin", {})
        self.assertEqual(
            paths.data, Path("/home/user/Library/Application Support/AmigaFileForge")
        )
        self.assertEqual(paths.cache, Path("/home/user/Library/Caches/AmigaFileForge"))
        self.assertEqual(paths.work, paths.data / "work")

    def test_windows_uses_roaming_for_state_and_local_for_rebuildable_data(self) -> None:
        paths = self._paths_for(
            "win32",
            {"APPDATA": "C:/Users/user/AppData/Roaming", "LOCALAPPDATA": "C:/Users/user/AppData/Local"},
        )
        self.assertEqual(paths.data, Path("C:/Users/user/AppData/Roaming/AmigaFileForge"))
        self.assertEqual(paths.config, paths.data)
        # Working images are rebuildable, so they must not roam between machines.
        self.assertTrue(str(paths.work).startswith("C:/Users/user/AppData/Local"))
        self.assertTrue(str(paths.cache).startswith("C:/Users/user/AppData/Local"))

    def test_windows_falls_back_when_the_environment_is_bare(self) -> None:
        paths = self._paths_for("win32", {})
        self.assertIn("AppData", str(paths.data))
        self.assertIn("AmigaFileForge", str(paths.data))

    def test_every_platform_keeps_work_inside_a_known_directory(self) -> None:
        for platform in ("linux", "darwin", "win32"):
            with self.subTest(platform=platform):
                paths = self._paths_for(platform, {})
                self.assertIn("AmigaFileForge", str(paths.work)) if platform != "linux" else None
                self.assertTrue(str(paths.work).endswith("work"))


class ShellSelectionTests(unittest.TestCase):
    """The right window for the platform, without forking shared behaviour."""

    def test_windows_and_macos_prefer_the_portable_shell(self) -> None:
        for platform in ("win32", "darwin"):
            with self.subTest(platform=platform):
                with patch("desktop.webview_host.sys.platform", platform):
                    self.assertTrue(webview_host.preferred_for_platform())

    def test_linux_keeps_the_gtk_shell(self) -> None:
        with patch("desktop.webview_host.sys.platform", "linux"):
            self.assertFalse(webview_host.preferred_for_platform())

    def test_a_missing_pywebview_is_explained_rather_than_traced(self) -> None:
        with patch("desktop.webview_host._load_webview", side_effect=webview_host.WebviewUnavailable("no pywebview")):
            self.assertFalse(webview_host.available())

    def test_the_host_serves_the_private_loopback_server(self) -> None:
        host = webview_host.WebviewHost.__new__(webview_host.WebviewHost)

        class _Server:
            port = 45123

        host.server = _Server()
        self.assertEqual(host._url(), "http://127.0.0.1:45123/")

    def test_the_launch_token_is_not_placed_in_the_url(self) -> None:
        """A token in the URL would leak into history and referrer headers."""
        host = webview_host.WebviewHost.__new__(webview_host.WebviewHost)

        class _Server:
            port = 45123
            token = "s3cr3t-launch-token"

        host.server = _Server()
        self.assertNotIn("s3cr3t", host._url())


if __name__ == "__main__":
    unittest.main()
