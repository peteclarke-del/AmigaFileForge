"""Authenticated loopback runtime used by the Linux desktop shell."""

from __future__ import annotations

import os
import re
import secrets
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from werkzeug.serving import BaseWSGIServer, make_server

from app.server import create_app


@dataclass(frozen=True)
class DesktopPaths:
    data: Path
    config: Path
    cache: Path
    work: Path


def _xdg_path(variable: str, fallback: Path) -> Path:
    value = os.environ.get(variable, "").strip()
    if not value:
        return fallback
    path = Path(value).expanduser()
    snap_root = Path.home() / "snap"
    try:
        path.relative_to(snap_root)
    except ValueError:
        return path
    return fallback


APPLICATION_DIRECTORY = "amiga-file-forge"


def _windows_paths(home: Path) -> DesktopPaths:
    """Roaming for state worth keeping, local for data that can be rebuilt."""
    roaming = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    local = Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")
    data = roaming / "AmigaFileForge"
    return DesktopPaths(
        data=data,
        config=data,
        cache=local / "AmigaFileForge" / "Cache",
        work=local / "AmigaFileForge" / "work",
    )


def _macos_paths(home: Path) -> DesktopPaths:
    support = home / "Library" / "Application Support" / "AmigaFileForge"
    return DesktopPaths(
        data=support,
        config=support,
        cache=home / "Library" / "Caches" / "AmigaFileForge",
        work=support / "work",
    )


def _linux_paths(home: Path) -> DesktopPaths:
    data = _xdg_path("XDG_DATA_HOME", home / ".local" / "share") / APPLICATION_DIRECTORY
    config = _xdg_path("XDG_CONFIG_HOME", home / ".config") / APPLICATION_DIRECTORY
    cache = _xdg_path("XDG_CACHE_HOME", home / ".cache") / APPLICATION_DIRECTORY
    return DesktopPaths(data=data, config=config, cache=cache, work=data / "work")


def desktop_paths() -> DesktopPaths:
    """Return the per-user locations this platform expects an application to use.

    Each desktop has its own convention and users reasonably expect it to be
    followed: XDG directories on Linux, Application Support and Caches on
    macOS, and Roaming with Local on Windows. Working images can be rebuilt
    from their sources, so on Windows they live under Local rather than being
    synchronised with a roaming profile.
    """
    home = Path.home()
    if sys.platform == "win32":
        return _windows_paths(home)
    if sys.platform == "darwin":
        return _macos_paths(home)
    return _linux_paths(home)


def _stable_owner(config_dir: Path) -> str:
    """Load or create the durable owner used to recover desktop sessions."""
    config_dir.mkdir(parents=True, exist_ok=True)
    config_dir.chmod(0o700)
    path = config_dir / "owner-id"
    try:
        value = path.read_text(encoding="ascii").strip()
    except (OSError, UnicodeError):
        value = ""
    if re.fullmatch(r"[A-Za-z0-9_-]{32,64}", value):
        path.chmod(0o600)
        return value
    value = secrets.token_urlsafe(32)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="ascii",
            dir=config_dir,
            prefix="owner-id-",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            os.fchmod(temporary.fileno(), 0o600)
            temporary.write(value)
            temporary.flush()
            os.fsync(temporary.fileno())
        temporary_path.replace(path)
        directory = os.open(config_dir, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return value


class DesktopServer:
    """Own one private Flask server and its shared image service lifecycle."""

    def __init__(self, work_dir: Path | None = None) -> None:
        paths = desktop_paths()
        self.work_dir = Path(work_dir or paths.work)
        self.config_dir = paths.config if work_dir is None else self.work_dir.parent / "config"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.token = secrets.token_urlsafe(32)
        self.owner = _stable_owner(self.config_dir)
        self.application = create_app(
            work_dir=self.work_dir,
            platform="desktop",
            desktop_token=self.token,
            desktop_owner=self.owner,
            desktop_state_path=self.config_dir / "client-state.json",
        )
        self._server: BaseWSGIServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("The desktop service has not started.")
        return int(self._server.server_port)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def start(self) -> None:
        if self._server is not None:
            return
        self._server = make_server(
            "127.0.0.1",
            0,
            self.application,
            threaded=True,
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="amiga-file-forge-desktop-api",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        emulator = self.application.extensions.get("amiga_interactive_emulator")
        if emulator is not None:
            emulator.stop()
        server, thread = self._server, self._thread
        self._server = self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)

    def __enter__(self) -> "DesktopServer":
        self.start()
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.stop()


__all__ = ["DesktopPaths", "DesktopServer", "desktop_paths"]
