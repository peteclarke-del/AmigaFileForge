"""What this build knows about WHDLoad, and how it puts it on a hard disk.

WHDLoad is how most Amiga games and demos are made to run from a hard drive.
It comes in two halves and only one of them can be fetched automatically, so
the split is worth stating plainly because it shapes everything downstream.

The **program** is a single archive published by its author at whdload.de.
That address answers a plain request, carries the current release, and is
ahead of the Aminet copy, so it is the first source tried and Aminet is the
fallback.

A **slave** is the small per-title patch that teaches WHDLoad one game. Those
are not fetchable: whdload.de refuses ``/games/`` to anything that is not a
browser session, and Aminet does not carry them. Nothing here pretends
otherwise. A slave reaches an image because it is already in the image, or
because the operator points at a folder of them, or because they drop one in.
Guessing a download address for a title would produce a confident failure at
the worst moment, which is halfway through writing a disk.

Installing the program is deliberately done by copying files rather than by
running the archive's own ``Install`` script under emulation. The script's job
is to ask where things go and then copy them; the destinations are fixed and
known, so booting an emulator to rediscover them would cost a minute per image
and add a way to fail that copying does not have.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from .errors import DiskError
from .lha import LHAArchive, LHAError
from .outbound import checked_url


#: The author's own site. Ahead of every mirror, and it serves this file to a
#: plain request even though it refuses the slave index to one.
WHDLOAD_HOME = "https://whdload.de/whdload/"
WHDLOAD_ARCHIVE_URL = "https://whdload.de/whdload/WHDLoad_usr.lha"

#: Tried in order. Aminet is a genuine mirror of the same archive rather than
#: a different edition, so a fallback does not change what gets installed.
WHDLOAD_SOURCES = (
    ("whdload.de", WHDLOAD_ARCHIVE_URL),
    ("Aminet", "https://aminet.net/util/boot/WHDLoad_usr.lha"),
)

#: Where the program's own files live inside the archive, and where AmigaDOS
#: expects to find them. The archive nests everything under one drawer, which
#: is stripped here so the destinations read as the volume paths they are.
_ARCHIVE_ROOT = "WHDLoad"

#: Copied on every install. ``WHDLoad`` is the loader itself; ``WHDLoadCD32``
#: is the CD32 variant a fair number of slaves ask for by name; the rest are
#: the tools slaves invoke while unpacking a title's own data.
PROGRAM_FILES = (
    "C/WHDLoad",
    "C/WHDLoadCD32",
    "C/WHDLoad.VFS",
    "C/WArc",
    "C/DIC",
    "C/RawDIC",
    "C/Patcher",
)

#: The startup and cleanup scripts, and the preferences file that decides
#: where WHDLoad writes its debug output. Preferences are never overwritten:
#: an operator who has tuned them has tuned them for their own machine.
SCRIPT_FILES = ("S/WHDLoad-Startup", "S/WHDLoad-Cleanup")
PREFERENCES_FILE = "S/WHDLoad.prefs"

#: Reading this one file is enough to say whether a volume already has
#: WHDLoad, and its version string says which one.
INSTALLED_MARKER = "C/WHDLoad"

#: Slaves are named by convention rather than by any header, so this is what
#: identifies one on disk.
SLAVE_SUFFIX = ".slave"

_VERSION = re.compile(rb"\$VER:\s*(?P<name>[A-Za-z0-9_.]+)\s+(?P<version>\d+\.\d+)")


@dataclass(frozen=True)
class WHDLoadRelease:
    """One WHDLoad program archive, already fetched and readable."""

    source: str
    url: str
    version: str
    archive: LHAArchive
    #: The bytes the archive was opened from. A caller that has to hand the
    #: release on to a service which opens it again should pass these rather
    #: than fetch a second time, which would risk installing a different
    #: release from the one it just reported to the operator.
    archive_bytes: bytes = b""

    @property
    def label(self) -> str:
        return f"WHDLoad {self.version}" if self.version else "WHDLoad"


def parse_version(data: bytes) -> str:
    """Read the version out of a WHDLoad binary's own ``$VER:`` string.

    Every AmigaDOS program carries one, so this reports what is actually on
    the disk rather than what an installer once recorded alongside it.
    """
    found = _VERSION.search(data)
    return found.group("version").decode("ascii") if found else ""


def archive_path(volume_path: str) -> str:
    """The member inside the archive that supplies one volume path."""
    return f"{_ARCHIVE_ROOT}/{volume_path}"


def installation_plan(archive: LHAArchive, *, keep_preferences: bool) -> list[tuple[str, str]]:
    """Pair each file to install with where it goes on the volume.

    Raises rather than installing a partial WHDLoad. Half an installation
    looks like a working one from the outside and fails only when a game is
    started, which is a long way from the point where it could be fixed.
    """
    plan: list[tuple[str, str]] = []
    missing: list[str] = []
    wanted = list(PROGRAM_FILES) + list(SCRIPT_FILES)
    if not keep_preferences:
        wanted.append(PREFERENCES_FILE)
    for destination in wanted:
        member = archive.find(archive_path(destination))
        if member is None:
            missing.append(destination)
            continue
        plan.append((archive_path(destination), destination))
    if missing:
        raise DiskError(
            "This WHDLoad archive is missing " + ", ".join(missing) + ". "
            "It is not the usual WHDLoad_usr release; install from the original archive."
        )
    return plan


def read_release(data: bytes, source: str, url: str) -> WHDLoadRelease:
    """Open a downloaded archive and confirm it really is WHDLoad.

    A mirror that answers a missing file with an HTML page, or a release whose
    layout has moved, both have to be caught before anything is written into
    somebody's hard-disk image.
    """
    try:
        archive = LHAArchive(data)
    except LHAError as exc:
        raise DiskError(f"{source} did not return a readable WHDLoad archive: {exc}") from exc
    member = archive.find(archive_path(INSTALLED_MARKER))
    if member is None:
        raise DiskError(f"{source} returned an LHA archive that does not contain WHDLoad.")
    try:
        version = parse_version(archive.read(member))
    except LHAError as exc:
        raise DiskError(f"The WHDLoad program in the {source} archive could not be read: {exc}") from exc
    return WHDLoadRelease(
        source=source, url=url, version=version, archive=archive, archive_bytes=bytes(data)
    )


def detect(mount) -> dict:
    """Report whether a mounted volume already has WHDLoad, and which one.

    Called before every WHDLoad install so an image that is already set up is
    left alone. A volume that cannot be read here is reported as not having
    WHDLoad rather than failing the whole operation: the install can still go
    ahead, and it will say what it overwrote.
    """
    try:
        if not mount.exists(INSTALLED_MARKER):
            return {"installed": False, "version": "", "path": INSTALLED_MARKER}
        data = mount.read_bytes(INSTALLED_MARKER)
    except Exception:
        return {"installed": False, "version": "", "path": INSTALLED_MARKER}
    return {"installed": True, "version": parse_version(data), "path": INSTALLED_MARKER}


def is_slave_name(name: str) -> bool:
    return name.casefold().endswith(SLAVE_SUFFIX)


def newer(available: str, installed: str) -> bool:
    """Whether one WHDLoad version supersedes another.

    Versions are ``major.minor`` and are compared as numbers, because WHDLoad
    reached 10 and a string comparison would then rank 9 above it.
    """
    def parts(value: str) -> tuple[int, int]:
        found = re.match(r"(\d+)\.(\d+)", value or "")
        return (int(found.group(1)), int(found.group(2))) if found else (-1, -1)

    return parts(available) > parts(installed)


#: The program archive has been a megabyte and a half for years. The ceiling is
#: generous enough that a future release will not hit it, and low enough that a
#: mirror serving something else entirely is refused before it is parsed.
DOWNLOAD_LIMIT = 16 * 1024 * 1024

#: A short timeout, because the fallback is another source rather than failure.
DOWNLOAD_TIMEOUT = 30


def _fetch(url: str) -> bytes:
    """Read one URL under the application's outbound policy.

    Kept behind the same check every other outbound request goes through, so
    a WHDLoad source cannot become a way to reach an address on the host's own
    network that the rest of the application refuses.
    """
    request = urllib.request.Request(
        checked_url(url, f"{url} is not a usable WHDLoad source."),
        headers={"User-Agent": "AmigaFileForge/1.0 (+local archival tool)", "Accept-Encoding": "identity"},
    )
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
        declared = int(response.headers.get("Content-Length") or 0)
        if declared > DOWNLOAD_LIMIT:
            raise DiskError(f"{url} offered {declared:,} bytes, over the safety limit.")
        body = response.read(DOWNLOAD_LIMIT + 1)
    if len(body) > DOWNLOAD_LIMIT:
        raise DiskError(f"{url} returned more than the {DOWNLOAD_LIMIT // (1024 * 1024)} MB safety limit.")
    return body


def download(fetch: Callable[[str], bytes] | None = None) -> WHDLoadRelease:
    """Fetch the current WHDLoad program from the first source that answers.

    Every source is tried before giving up, and the failures are reported
    together. One address being unreachable is a common and boring event; what
    an operator needs to see is whether *all* of them were, and why.
    """
    reader = fetch or _fetch
    failures: list[str] = []
    for name, url in WHDLOAD_SOURCES:
        try:
            return read_release(reader(url), name, url)
        except (DiskError, urllib.error.URLError, OSError, ValueError) as exc:
            failures.append(f"{name}: {exc}")
    raise DiskError(
        "WHDLoad could not be downloaded from any known source. " + " ".join(failures) + " "
        "Download WHDLoad_usr.lha yourself and add it from this dialog instead."
    )


__all__ = [
    "PREFERENCES_FILE",
    "PROGRAM_FILES",
    "SCRIPT_FILES",
    "SLAVE_SUFFIX",
    "WHDLOAD_ARCHIVE_URL",
    "WHDLOAD_HOME",
    "WHDLOAD_SOURCES",
    "WHDLoadRelease",
    "DOWNLOAD_LIMIT",
    "archive_path",
    "detect",
    "download",
    "installation_plan",
    "is_slave_name",
    "newer",
    "parse_version",
    "read_release",
]
