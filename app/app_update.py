"""Check for, download and install a newer release of the application itself.

Nothing is checked until the user presses Check for Application Updates in the
About box. The check reads the release GitHub marks as the latest, which is
never a draft or a prerelease, and compares its tag, ``vX.Y.Z``, with the
running version from ``VERSION``.

A release carries one Debian package for each supported system, named as
``tools/build-linux-package.sh`` names it with the tilde of its revision
published as a dot, such as ``<package>_0.5.0-1.deb13_amd64.deb``, and a
``SHA256SUMS`` file for all of them. A package built by the release workflow
records the system it was built for in ``package-target`` beside ``VERSION``,
so the update takes the package made for the same system rather than guessing
from the running one. The package is downloaded to the cache folder, checked
against ``SHA256SUMS`` and installed with ``pkexec apt-get install``, which
asks for the user's password. The desktop window runs this server in its own
process, so the password prompt appears in the user's session.

A copy without ``package-target``, such as a source checkout, the Docker
service or a package built by hand, cannot update itself and is sent to the
release page instead. The web host never installs, whatever it finds, because
the person pressing the button may be on another computer.

The server keeps one ``AppUpdater``, so an update carries on when the About
box is closed, shows again when it is reopened, and cannot be started twice.
"""

from __future__ import annotations

import functools
import json
import re
import shlex
import shutil
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .branding import (
    APPLICATION_NAME,
    HOMEPAGE,
    PACKAGE_NAME,
    RELEASES_API,
    RELEASES_PAGE,
    REPOSITORY,
)
from .checksum import sha256_path
from .version import application_version

LATEST_URL = f"{RELEASES_API}/latest"
SUMS_NAME = "SHA256SUMS"
#: The file tools/build-linux-package.sh writes beside VERSION in a release package.
PACKAGE_TARGET = Path(__file__).resolve().parent.parent / "package-target"
USER_AGENT = f"{REPOSITORY.rpartition('/')[2]}/{application_version()} (+{HOMEPAGE})"
GITHUB_JSON = "application/vnd.github+json"
CHECK_TIMEOUT = 20.0
DOWNLOAD_TIMEOUT = 60.0
MAX_REPLY_BYTES = 4 * 1024 * 1024
MAX_SUMS_BYTES = 64 * 1024
MAX_PACKAGE_BYTES = 512 * 1024 * 1024
CHUNK_BYTES = 256 * 1024
BUSY_PHASES = frozenset({"checking", "downloading", "installing"})
BUSY_WITH_MEDIA = (
    f"{APPLICATION_NAME} can be updated once the floppy disk has been read or written."
)
RESTART_WITH_MEDIA = (
    f"{APPLICATION_NAME} can restart once the floppy disk has been read or written."
)
_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_TOKEN = re.compile(r"^[a-z0-9][a-z0-9.+-]*$")
_SUM_LINE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(\S.*)$")
# pkexec's exit statuses when the password prompt is dismissed or refused.
_PKEXEC_DISMISSED = 126
_PKEXEC_REFUSED = 127

Progress = Callable[[int, int | None], None]
Opener = Callable[..., Any]


class UpdateError(RuntimeError):
    """The update could not be checked or installed; the message is for the user."""


class UpdateCancelled(UpdateError):
    """The user cancelled the update."""


class _Missing(UpdateError):
    """The server answered that the file does not exist."""


@dataclass(frozen=True, slots=True)
class PackageTarget:
    """The system a release package was built for, such as ("deb13", "amd64", "Debian 13")."""

    distro: str
    arch: str
    label: str = ""

    @property
    def system(self) -> str:
        return f"{self.label or self.distro} {self.arch}"

    def package_revision(self, name: str, version: str) -> int | None:
        """The Debian revision of ``name`` when it is this system's package of ``version``."""
        match = re.fullmatch(
            rf"{re.escape(PACKAGE_NAME)}_{re.escape(version)}-(\d+)\."
            rf"{re.escape(self.distro)}_{re.escape(self.arch)}\.deb",
            name,
        )
        return int(match.group(1)) if match else None


@dataclass(frozen=True, slots=True)
class AppRelease:
    """A published application release newer than the running version."""

    version: str
    tag: str
    name: str
    page_url: str
    package_name: str = ""  # empty when the release has no package for this system
    package_url: str = ""
    package_size: int | None = None
    sums_url: str = ""

    @property
    def installable(self) -> bool:
        return bool(self.package_url and self.sums_url)

    def public(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "tag": self.tag,
            "name": self.name,
            "pageUrl": self.page_url,
            "installable": self.installable,
            "packageName": self.package_name,
            "packageSize": self.package_size,
        }


def installed_target(path: Path | None = None) -> PackageTarget | None:
    """The system this package was built for, or None for any other copy."""
    try:
        text = (path or PACKAGE_TARGET).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    values = {}
    for line in text.splitlines():
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    distro, arch = values.get("distro", ""), values.get("arch", "")
    if not (_TOKEN.match(distro) and _TOKEN.match(arch)):
        return None
    return PackageTarget(distro, arch, values.get("label", ""))


def parse_version(text: str) -> tuple[int, int, int] | None:
    """(0, 4, 0) for "v0.4.0" or "0.4.0"; None for anything else, such as "v0.5.0-rc.1"."""
    match = _TAG.match(text if text.startswith("v") else f"v{text}")
    return tuple(int(part) for part in match.groups()) if match else None  # type: ignore[return-value]


def is_newer(tag: str, current: str | None = None) -> bool:
    theirs = parse_version(tag)
    ours = parse_version(application_version() if current is None else current)
    return theirs is not None and (ours is None or theirs > ours)


def release_from(
    release: dict[str, Any], target: PackageTarget | None, current: str | None = None
) -> AppRelease | None:
    """The release as an AppRelease when it is newer than ``current``, else None."""
    tag = str(release.get("tag_name", ""))
    version = parse_version(tag)
    if version is None:
        raise UpdateError(
            f"The latest release on GitHub has the tag {tag or '(none)'}, "
            "which is not a version such as v1.2.3."
        )
    if not is_newer(tag, current):
        return None
    text = ".".join(str(part) for part in version)
    assets = {
        str(asset.get("name", "")): asset
        for asset in release.get("assets") or []
        if isinstance(asset, dict)
    }
    # A rebuilt package gets the next revision, so the highest one is taken.
    candidates = sorted(
        (revision, name)
        for name in assets
        if target is not None
        and (revision := target.package_revision(name, text)) is not None
    )
    wanted = candidates[-1][1] if candidates else ""
    package, sums = assets.get(wanted), assets.get(SUMS_NAME)
    size = package.get("size") if package else None
    return AppRelease(
        version=text,
        tag=tag,
        # Named the same way whatever the release's title says.
        name=f"{APPLICATION_NAME} {text}",
        page_url=str(release.get("html_url") or ""),
        package_name=wanted,
        package_url=str(package.get("browser_download_url", "")) if package else "",
        package_size=size if isinstance(size, int) else None,
        sums_url=str(sums.get("browser_download_url", "")) if sums else "",
    )


def _request(url: str, accept: str = "") -> urllib.request.Request:
    headers = {"User-Agent": USER_AGENT}
    if accept:
        headers["Accept"] = accept
    return urllib.request.Request(url, headers=headers)


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc or url


def _unreachable(url: str, error: BaseException) -> UpdateError:
    reason = getattr(error, "reason", None) or error
    if isinstance(reason, TimeoutError):
        return UpdateError(f"{_host(url)} did not answer in time.")
    return UpdateError(f"{_host(url)} could not be reached ({reason}).")


def _refused(url: str, error: urllib.error.HTTPError) -> UpdateError:
    message = None
    try:
        reply = json.loads(error.read(MAX_REPLY_BYTES).decode("utf-8"))
        message = reply.get("message") if isinstance(reply, dict) else None
    except (OSError, UnicodeDecodeError, ValueError):
        pass
    finally:
        error.close()
    kind = _Missing if error.code == 404 else UpdateError
    text = f"{_host(url)} refused the request (HTTP {error.code})"
    return kind(f"{text}: {str(message).rstrip('.')}." if message else f"{text}.")


def _read(url: str, limit: int, opener: Opener, timeout: float, accept: str = "") -> bytes:
    try:
        with opener(_request(url, accept), timeout=timeout) as reply:
            data = reply.read(limit + 1)
    except urllib.error.HTTPError as error:
        raise _refused(url, error) from error
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise _unreachable(url, error) from error
    if len(data) > limit:
        raise UpdateError(f"The reply from {_host(url)} is larger than expected.")
    return data


def latest_release(url: str = LATEST_URL, opener: Opener = urllib.request.urlopen) -> dict | None:
    """The release GitHub marks as the latest, or None when none is published.

    The repository is public, so this needs no account. GitHub never marks a
    draft or a prerelease as the latest release.
    """
    try:
        data = _read(url, MAX_REPLY_BYTES, opener, CHECK_TIMEOUT, GITHUB_JSON)
    except _Missing:
        return None
    try:
        release = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise UpdateError(f"The reply from {_host(url)} could not be read.") from error
    if not isinstance(release, dict) or "tag_name" not in release:
        message = release.get("message") if isinstance(release, dict) else None
        reason = f"{_host(url)} did not send a release"
        raise UpdateError(f"{reason}: {str(message).rstrip('.')}." if message else f"{reason}.")
    return release


def check(
    target: PackageTarget | None,
    *,
    url: str = LATEST_URL,
    current: str | None = None,
    opener: Opener = urllib.request.urlopen,
) -> AppRelease | None:
    """A newer release than ``current``, or None when this is the newest.

    Raises UpdateError, with the reason, when the check cannot be made.
    """
    release = latest_release(url, opener)
    if release is None:
        raise UpdateError("No release has been published on GitHub yet.")
    return release_from(release, target, current)


def published_sum(sums: str, name: str) -> str:
    """The SHA-256 ``SHA256SUMS`` gives for ``name``, or ""."""
    for line in sums.splitlines():
        match = _SUM_LINE.match(line.strip())
        if match and match.group(2).strip() == name:
            return match.group(1).lower()
    return ""


def _fetch(
    url: str,
    destination: Path,
    progress: Progress | None,
    cancel: threading.Event | None,
    opener: Opener,
) -> Path:
    """Download ``url`` to ``destination`` through a partial file, reporting progress."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f"{destination.name}.part")
    try:
        with partial.open("wb") as handle:
            try:
                reply = opener(_request(url), timeout=DOWNLOAD_TIMEOUT)
            except urllib.error.HTTPError as error:
                raise _refused(url, error) from error
            except (urllib.error.URLError, OSError, ValueError) as error:
                raise _unreachable(url, error) from error
            with reply:
                length = str(reply.headers.get("Content-Length") or "")
                total = int(length) if length.isdigit() else None
                done = 0
                if progress:
                    progress(done, total)
                while True:
                    if cancel is not None and cancel.is_set():
                        raise UpdateCancelled("The update was cancelled.")
                    try:
                        block = reply.read(CHUNK_BYTES)
                    except OSError as error:
                        raise _unreachable(url, error) from error
                    if not block:
                        break
                    done += len(block)
                    if done > MAX_PACKAGE_BYTES:
                        raise UpdateError("The package is larger than any release package.")
                    handle.write(block)
                    if progress:
                        progress(done, total)
        partial.replace(destination)
    except OSError as error:
        raise UpdateError(
            f"The package could not be saved in {destination.parent}: {error.strerror or error}."
        ) from error
    finally:
        partial.unlink(missing_ok=True)
    return destination


def download(
    release: AppRelease,
    progress: Progress | None = None,
    cancel: threading.Event | None = None,
    *,
    folder: Path,
    opener: Opener = urllib.request.urlopen,
) -> Path:
    """Download the release's package for this system and check it against SHA256SUMS."""
    if not release.installable:
        raise UpdateError(f"{release.name} has no package for this system.")
    sums = _read(release.sums_url, MAX_SUMS_BYTES, opener, DOWNLOAD_TIMEOUT)
    expected = published_sum(sums.decode("utf-8", "replace"), release.package_name)
    if not expected:
        raise UpdateError(f"{SUMS_NAME} in {release.name} has no line for the package.")
    package = _fetch(release.package_url, folder / release.package_name, progress, cancel, opener)
    if sha256_path(package) != expected:
        package.unlink(missing_ok=True)
        raise UpdateError(
            "The downloaded package does not match its published checksum, so it was not "
            "installed. Try again."
        )
    return package


def install_command(package: Path) -> list[str] | None:
    """The command that installs ``package`` with the user's password, or None without pkexec."""
    pkexec, apt_get = shutil.which("pkexec"), shutil.which("apt-get")
    if not (pkexec and apt_get):
        return None
    return [pkexec, apt_get, "install", "--yes", str(package)]


def manual_command(package: Path) -> str:
    """The command a user can run in a terminal instead."""
    return f"sudo apt install {shlex.quote(str(package))}"


def install(package: Path, *, run: Callable[..., Any] = subprocess.run) -> None:
    """Install the downloaded package, then remove the download.

    Raises UpdateCancelled when the password prompt is dismissed, and
    UpdateError with the reason and a command to run by hand otherwise.

    There is no time limit. pkexec waits for as long as the password prompt
    is open, and once it is answered apt runs as root, where this process
    cannot stop it: giving up would report a failure while apt went on to
    install the package.
    """
    command = install_command(package)
    by_hand = f"Install it in a terminal with: {manual_command(package)}"
    if command is None:
        raise UpdateError(
            f"pkexec is not installed, so the package cannot be installed here. {by_hand}"
        )
    try:
        result = run(command, capture_output=True, text=True, check=False)
    except OSError as error:
        raise UpdateError(f"The package could not be installed: {error}. {by_hand}") from error
    if result.returncode == _PKEXEC_DISMISSED:
        raise UpdateCancelled("The password prompt was dismissed, so nothing was installed.")
    if result.returncode == _PKEXEC_REFUSED:
        raise UpdateError(f"The system did not allow the installation. {by_hand}")
    if result.returncode != 0:
        lines = [line for line in (result.stderr or result.stdout or "").splitlines() if line]
        reason = lines[-1] if lines else f"apt-get stopped with status {result.returncode}"
        raise UpdateError(f"The package could not be installed: {reason}. {by_hand}")
    package.unlink(missing_ok=True)


class Activity:
    """Counts the operations, reading or writing a floppy disk, an update must not interrupt."""

    def __init__(self) -> None:
        self._count = 0
        self._lock = threading.Lock()

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._count > 0

    @contextmanager
    def running(self) -> Iterator[None]:
        with self._lock:
            self._count += 1
        try:
            yield
        finally:
            with self._lock:
                self._count -= 1

    def guard(self, view: Callable[..., Any]) -> Callable[..., Any]:
        """Count a route as running for as long as its request lasts."""

        @functools.wraps(view)
        def guarded(*args: Any, **kwargs: Any) -> Any:
            with self.running():
                return view(*args, **kwargs)

        return guarded


@dataclass(frozen=True, slots=True)
class AppUpdateState:
    # "idle", "checking", "current", "available", "downloading", "installing",
    # "installed" or "failed"
    phase: str
    message: str = ""
    release: AppRelease | None = None
    done: int = 0
    total: int | None = None

    @property
    def busy(self) -> bool:
        return self.phase in BUSY_PHASES


def _start_thread(work: Callable[[], None], name: str) -> None:
    threading.Thread(target=work, name=name, daemon=True).start()


class AppUpdater:
    """The one application update this server runs, shown by every About box that asks."""

    def __init__(
        self,
        target: PackageTarget | None,
        *,
        folder: Path,
        activity: Activity,
        checker: Callable[[PackageTarget | None], AppRelease | None] | None = None,
        downloader: Callable[..., Path] | None = None,
        installer: Callable[[Path], None] | None = None,
        start: Callable[[Callable[[], None], str], None] = _start_thread,
    ) -> None:
        self.target = target
        self.activity = activity
        self._folder = folder
        self._check = checker or check
        self._download = downloader or download
        self._install = installer or install
        self._start = start
        self._lock = threading.RLock()
        self._state = AppUpdateState("idle")
        self._cancel: threading.Event | None = None

    @property
    def state(self) -> AppUpdateState:
        with self._lock:
            return self._state

    def _set(self, state: AppUpdateState) -> None:
        with self._lock:
            self._state = state

    def snapshot(self) -> dict[str, Any]:
        """The state as the About box reads it."""
        state = self.state
        return {
            "phase": state.phase,
            "message": state.message,
            "busy": state.busy,
            "done": state.done,
            "total": state.total,
            "release": state.release.public() if state.release else None,
            "application": APPLICATION_NAME,
            "currentVersion": application_version(),
            "system": self.target.system if self.target else "",
            "releasesPage": RELEASES_PAGE,
        }

    def available_text(self, release: AppRelease) -> str:
        text = f"{release.name} is available. You have version {application_version()}."
        if release.installable:
            return text
        if self.target is None:
            return (
                f"{text} This copy cannot update itself because it was not installed from a "
                "release package. Update it the way it was installed, or install a package "
                "from the release page."
            )
        if release.package_url:
            return (
                f"{text} The release has no {SUMS_NAME} file to check its package against, "
                "so it is not installed from here; the release page has it."
            )
        return (
            f"{text} The release has no package for {self.target.system}; "
            "the release page lists the packages it has."
        )

    def check(self) -> dict[str, Any]:
        """Ask GitHub for the latest release, unless an update is already under way."""
        with self._lock:
            if self._state.busy:
                return self.snapshot()
            self._state = AppUpdateState("checking", "Asking GitHub for the newest version")

        def work() -> None:
            try:
                release = self._check(self.target)
            except UpdateError as error:
                self._set(AppUpdateState("failed", f"Could not check for a newer version: {error}"))
                return
            except Exception as error:
                # Deliberately broad: a reply shaped in a way nobody foresaw
                # must end the check with a reason, never leave it "checking".
                self._set(AppUpdateState("failed", f"Could not check for a newer version: {error}"))
                return
            if release is None:
                self._set(
                    AppUpdateState(
                        "current", f"{APPLICATION_NAME} {application_version()} is the newest version"
                    )
                )
            else:
                self._set(AppUpdateState("available", self.available_text(release), release))

        self._start(work, "app-update-check")
        return self.snapshot()

    def install(self) -> dict[str, Any]:
        """Download and install the release the last check found."""
        with self._lock:
            state = self._state
            if state.busy:
                return self.snapshot()
            release = state.release
            if state.phase != "available" or release is None or not release.installable:
                raise UpdateError(
                    "There is no update to install here. Check for Application Updates first."
                )
            if self.activity.busy:
                self._state = AppUpdateState("available", BUSY_WITH_MEDIA, release)
                return self.snapshot()
            cancel = self._cancel = threading.Event()
            self._state = AppUpdateState(
                "downloading", "Downloading the package", release, 0, release.package_size
            )

        def progress(done: int, total: int | None) -> None:
            with self._lock:
                if self._state.phase == "downloading":
                    self._state = replace(self._state, done=done, total=total)

        def work() -> None:
            try:
                package = self._download(release, progress, cancel, folder=self._folder)
                with self._lock:
                    # A disk may have been started while the package downloaded.
                    if self.activity.busy:
                        self._state = AppUpdateState("available", BUSY_WITH_MEDIA, release)
                        return
                    self._state = AppUpdateState(
                        "installing",
                        f"Installing {release.name}. The system asks for your password.",
                        release,
                    )
                self._install(package)
            except UpdateCancelled as error:
                self._set(AppUpdateState("available", str(error), release))
            except Exception as error:
                # Deliberately broad, as for the check: the update ends with a reason.
                self._set(AppUpdateState("failed", f"The update failed: {error}", release))
            else:
                self._set(
                    AppUpdateState(
                        "installed",
                        f"{release.name} is installed. Restart {APPLICATION_NAME} to use it.",
                        release,
                    )
                )
            finally:
                with self._lock:
                    self._cancel = None

        self._start(work, "app-update-install")
        return self.snapshot()

    def cancel(self) -> dict[str, Any]:
        """Stop the download. Once apt is running, the install cannot be stopped."""
        with self._lock:
            if self._cancel is not None:
                self._cancel.set()
        return self.snapshot()

    def restart_refusal(self) -> str:
        """Why the application cannot restart now, or "" when it can."""
        return RESTART_WITH_MEDIA if self.activity.busy else ""
