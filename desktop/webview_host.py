"""A portable desktop shell for Windows and macOS.

The Linux edition uses GTK 4 with WebKitGTK, which is not available in any
supportable form on Windows or macOS. Everything behind the window is already
portable: the Flask application, the filesystem services and the whole
frontend. Only the shell needed replacing.

This host puts the same private, token-authenticated server behind a system
webview instead:

* Windows uses WebView2, the Edge runtime that ships with current Windows.
* macOS uses WKWebView, part of the operating system.
* Linux can use this host too, through WebKitGTK, but the GTK host remains the
  preferred Linux shell because it also provides native menus, file chooser,
  drag and drop and file associations.

The window is the only difference. Sessions, working images, undo history and
the platform contract behave the same, so a change to shared behaviour cannot
apply to one desktop and not another.

Native file dialogs come from the webview toolkit rather than GTK, so opening
local media still avoids uploading bytes through the browser stack.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .runtime import DesktopServer


WINDOW_TITLE = "Amiga File Forge"
MINIMUM_SIZE = (960, 640)
DEFAULT_SIZE = (1440, 900)


class WebviewUnavailable(RuntimeError):
    """The pywebview package or its platform backend is not installed."""


def _load_webview():
    try:
        import webview
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise WebviewUnavailable(
            "The portable desktop shell needs pywebview. Install it with "
            "'pip install pywebview'. On Windows it also needs the Microsoft "
            "Edge WebView2 runtime, which current Windows installs already "
            "provide."
        ) from exc
    return webview


class WebviewHost:
    """Own one private server and the system webview showing it."""

    def __init__(self, work_dir: Path | None = None) -> None:
        self.server = DesktopServer(work_dir)
        self._webview = None

    def _url(self) -> str:
        return f"http://127.0.0.1:{self.server.port}/"

    def run(self, images: list[Path] | None = None) -> int:
        """Start the server, show the window, and stop cleanly on close."""
        webview = _load_webview()
        self._webview = webview
        self.server.start()
        try:
            window = webview.create_window(
                WINDOW_TITLE,
                self._url(),
                width=DEFAULT_SIZE[0],
                height=DEFAULT_SIZE[1],
                min_size=MINIMUM_SIZE,
                text_select=True,
            )
            self._authenticate(window)
            if images:
                self._queue_images(window, images)
            webview.start()
        finally:
            self.server.stop()
        return 0

    def _authenticate(self, window) -> None:
        """Give the window the launch token the private server requires.

        The server rejects any request without it, so the token is installed as
        a cookie before the first navigation completes rather than being put in
        the URL, where it would end up in history and referrer headers.
        """
        token = self.server.token

        def install() -> None:
            window.evaluate_js(
                "document.cookie = 'amiga_file_forge_desktop="
                + token
                + "; path=/; SameSite=Strict';"
                + "location.reload();"
            )

        window.events.loaded += install

    @staticmethod
    def _queue_images(window, images: list[Path]) -> None:
        """Ask the frontend to open files named on the command line."""
        paths = [str(Path(image).resolve()) for image in images]

        def open_them() -> None:
            window.evaluate_js(
                "window.amigaPendingOpen = " + repr(paths).replace("'", '"') + ";"
            )

        window.events.loaded += open_them


def available() -> bool:
    """Whether this host can run here."""
    try:
        _load_webview()
    except WebviewUnavailable:
        return False
    return True


def preferred_for_platform() -> bool:
    """Whether this host is the right shell for the current platform.

    Linux keeps the GTK host, which offers native menus, the file chooser and
    desktop integration that this shell does not.
    """
    return sys.platform in {"win32", "darwin"}


__all__ = [
    "WebviewHost",
    "WebviewUnavailable",
    "available",
    "preferred_for_platform",
]
