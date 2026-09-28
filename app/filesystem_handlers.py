"""The filing-system handlers a new drive is given.

Kickstart holds the FastFileSystem and nothing else. A partition formatted with
the Professional or the Smart File System mounts only when its handler travels
with the drive, in the Rigid Disk Block, where the machine loads it before it
mounts the first partition. A drive created without one looks finished here
and shows an unreadable partition on the Amiga, so the handler is part of
creating the drive rather than something to be added by hand afterwards.

Two places hold handlers. The application ships the Professional File System,
which its licence allows. Anything else is supplied by the person using the
application; a supplied handler takes the place of a shipped one for the same
filing system, which is how a newer release of PFS3 is used without waiting
for a new release of this application.

Where a supplied handler is kept follows how the application was installed.
A copy installed for one person, from a checkout in their home directory,
keeps it in that person's configuration directory. A copy installed for the
whole machine, from a package or as the Docker service, keeps it in one place
for everyone who uses that copy. Writing there takes an administrator's
permission, which the desktop application asks for the way its own update
does.

A handler can also be lifted out of a drive or a drive image that carries one.
A card prepared on the Amiga, or an image made by another tool, holds exactly
the handler its partitions were formatted with.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .checksum import sha256_bytes
from .errors import DiskError

#: Whether this process may ask for an administrator's password. Only the
#: desktop host may: it runs in the session of the person at the machine. The
#: person using the web host may be on another computer.
MAY_ASK_FOR_PASSWORD = False

APPLICATION_ROOT = Path(__file__).resolve().parent.parent

#: Where a copy installed for the whole machine keeps supplied handlers.
MACHINE_DIRECTORY = Path("/var/lib/amiga-file-forge/handlers")

# pkexec's exit statuses when the password prompt is dismissed or refused.
PKEXEC_DISMISSED = 126
PKEXEC_REFUSED = 127

BUNDLED_DIR = Path(__file__).resolve().parent / "handlers"

#: An Amiga load file begins with ``HUNK_HEADER``.
HUNK_HEADER = b"\x00\x00\x03\xf3"

#: Nothing that is a filing-system handler is as large as this.
LARGEST_HANDLER = 1024 * 1024

VERSION_STRING = re.compile(rb"\$VER:\s*([^\x00\r\n]{1,100})")
VERSION_NUMBER = re.compile(r"(\d+)\.(\d+)")


@dataclass(frozen=True)
class Family:
    """One filing system that needs a handler, and the DOS types it serves."""

    key: str
    label: str
    dos_types: tuple[bytes, ...]
    #: Words a handler of this family carries in its version string.
    signatures: tuple[str, ...]
    #: What the handler is usually called, for the person looking for it.
    usual_name: str


FAMILIES = {
    "pfs3": Family(
        "pfs3",
        "Professional File System 3",
        (b"PFS\x03", b"PDS\x03", b"PFS\x01", b"PFS\x02"),
        ("professional-file-system", "professional file system", "pfs3", "pfs"),
        "pfs3aio",
    ),
    "sfs": Family(
        "sfs",
        "Smart File System",
        (b"SFS\x00",),
        ("smartfilesystem", "smart file system", "smartfs", "sfs"),
        "SmartFilesystem",
    ),
    "ffs": Family(
        "ffs",
        "FastFileSystem 46 or later",
        (b"DOS\x06", b"DOS\x07"),
        ("fastfilesystem", "fast file system", "ffs", "fs "),
        "FastFileSystem",
    ),
}


def family_for(dos_type: bytes) -> Family | None:
    """Return the family whose handler mounts a partition of this DOS type."""
    for family in FAMILIES.values():
        if dos_type in family.dos_types:
            return family
    return None


def install_scope(root: Path | None = None) -> str:
    """Say whether this copy is installed for one person or for the machine.

    A copy inside somebody's home directory, or one that the person running
    it owns, is theirs. Anything else was put there for everyone: a package
    under ``/opt``, or the Docker service. ``AMIGA_FILE_FORGE_INSTALL_SCOPE``
    settles it for an installation this cannot tell apart.
    """
    declared = os.environ.get("AMIGA_FILE_FORGE_INSTALL_SCOPE", "").strip().lower()
    if declared in ("user", "machine"):
        return declared
    root = Path(root or APPLICATION_ROOT)
    try:
        root.resolve().relative_to(Path.home().resolve())
        return "user"
    except (ValueError, OSError, RuntimeError):
        pass
    try:
        user = os.getuid()
        if user != 0 and root.stat().st_uid == user:
            return "user"
    except (AttributeError, OSError):
        pass
    return "machine"


def user_directory() -> Path:
    """Where supplied handlers are kept, for one person or for the machine."""
    configured = os.environ.get("AMIGA_FILE_FORGE_HANDLER_DIR")
    if configured:
        return Path(configured)
    if install_scope() == "machine":
        return MACHINE_DIRECTORY
    return Path.home() / ".config" / "amiga-file-forge" / "handlers"


def _can_write(folder: Path) -> bool:
    """Whether this process can create files in a folder, or create the folder."""
    probe = folder
    while not probe.exists():
        if probe.parent == probe:
            return False
        probe = probe.parent
    return probe.is_dir() and os.access(probe, os.W_OK | os.X_OK)


def _as_administrator(
    command: list[str],
    refusal: str,
    by_hand: str,
    run: Callable[..., Any],
) -> None:
    """Run one command with an administrator's permission, or say what to run."""
    pkexec = shutil.which("pkexec")
    if not MAY_ASK_FOR_PASSWORD or not pkexec:
        raise DiskError(f"{refusal} An administrator can do it with: {by_hand}")
    try:
        result = run([pkexec, *command], capture_output=True, text=True, check=False)
    except OSError as exc:
        raise DiskError(f"{refusal} An administrator can do it with: {by_hand}") from exc
    if result.returncode == PKEXEC_DISMISSED:
        raise DiskError("The password prompt was dismissed, so nothing was changed.")
    if result.returncode != 0:
        raise DiskError(f"{refusal} An administrator can do it with: {by_hand}")


def version_text(binary: bytes) -> str:
    """Return what a handler says it is, or an empty string if it does not say."""
    match = VERSION_STRING.search(binary)
    return match.group(1).decode("latin-1").strip() if match else ""


def version_number(binary: bytes) -> str:
    match = VERSION_NUMBER.search(version_text(binary))
    return f"{int(match.group(1))}.{int(match.group(2))}" if match else ""


def recognise(binary: bytes) -> Family | None:
    """Work out which filing system a handler is for from its version string."""
    text = version_text(binary).lower()
    if not text:
        return None
    for key in ("pfs3", "sfs", "ffs"):
        family = FAMILIES[key]
        if any(signature in text for signature in family.signatures[:-1]):
            return family
    for key in ("pfs3", "sfs", "ffs"):
        family = FAMILIES[key]
        if text.startswith(family.signatures[-1]):
            return family
    return None


def check_handler(binary: bytes, family: Family) -> None:
    """Refuse a file that cannot be the handler it is offered as."""
    if not binary:
        raise DiskError("That file is empty.")
    if len(binary) > LARGEST_HANDLER:
        raise DiskError("That file is too large to be a filing-system handler.")
    if binary[:4] != HUNK_HEADER:
        raise DiskError(
            "That file is not an Amiga program, so it cannot be a filing-system "
            "handler. A handler is the file the Amiga keeps in L:, such as "
            f"{family.usual_name}, not the archive it was distributed in."
        )
    recognised = recognise(binary)
    if recognised is not None and recognised.key != family.key:
        raise DiskError(
            f"That file says it is “{version_text(binary)}”, which is the "
            f"{recognised.label}, not the {family.label}."
        )


def storage() -> dict:
    """Describe where supplied handlers go, for the dialog that takes them."""
    folder = user_directory()
    scope = "machine" if folder == MACHINE_DIRECTORY or install_scope() == "machine" else "user"
    writable = _can_write(folder)
    return {
        "scope": scope,
        "folder": str(folder),
        "writable": writable,
        "asksForPassword": not writable and MAY_ASK_FOR_PASSWORD and bool(shutil.which("pkexec")),
    }


def _describe(path: Path, family: Family, source: str, name: str = "") -> dict:
    binary = path.read_bytes()
    return {
        "family": family.key,
        "label": family.label,
        "source": source,
        "name": name or path.name,
        "description": version_text(binary),
        "version": version_number(binary),
        "sizeBytes": len(binary),
        "sha256": sha256_bytes(binary),
        "dosTypes": [dos_type.decode("latin-1") for dos_type in family.dos_types],
    }


def _bundled_path(family: Family) -> Path | None:
    candidate = BUNDLED_DIR / family.usual_name
    return candidate if candidate.is_file() else None


def _supplied_path(family: Family) -> Path:
    return user_directory() / f"{family.key}.handler"


def _supplied_name(family: Family) -> str:
    try:
        details = json.loads(
            _supplied_path(family).with_suffix(".json").read_text("utf-8")
        )
        return str(details.get("name") or "")
    except (OSError, ValueError):
        return ""


def available() -> list[dict]:
    """Describe every family: the handler in use for it, or that there is none."""
    rows = []
    for family in FAMILIES.values():
        supplied = _supplied_path(family)
        bundled = _bundled_path(family)
        try:
            if supplied.is_file():
                row = _describe(supplied, family, "supplied", _supplied_name(family))
                row["replacesBundled"] = bundled is not None
            elif bundled is not None:
                row = _describe(bundled, family, "bundled")
            else:
                row = None
        except OSError:
            row = None
        rows.append(row or {
            "family": family.key,
            "label": family.label,
            "source": "missing",
            "name": family.usual_name,
            "description": "",
            "version": "",
            "sizeBytes": 0,
            "dosTypes": [dos_type.decode("latin-1") for dos_type in family.dos_types],
        })
    return rows


def load(family_key: str) -> bytes | None:
    """Return the handler in use for a family, or None when there is none."""
    family = FAMILIES.get(str(family_key or ""))
    if family is None:
        return None
    for candidate in (_supplied_path(family), _bundled_path(family)):
        try:
            if candidate is not None and candidate.is_file():
                return candidate.read_bytes()
        except OSError:
            continue
    return None


def store(
    family_key: str,
    binary: bytes,
    name: str = "",
    *,
    run: Callable[..., Any] = subprocess.run,
) -> dict:
    """Keep a handler that was supplied, for every drive created from now on.

    In a copy installed for the whole machine the handler is kept for
    everyone who uses it, which takes an administrator's permission.
    """
    family = FAMILIES.get(str(family_key or ""))
    if family is None:
        raise DiskError("Choose which filing system the handler is for.")
    check_handler(binary, family)
    folder = user_directory()
    target = folder / f"{family.key}.handler"
    details = json.dumps({"name": Path(str(name or family.usual_name)).name})
    if _can_write(folder):
        try:
            folder.mkdir(parents=True, exist_ok=True)
            temporary = target.with_suffix(".tmp")
            temporary.write_bytes(binary)
            temporary.replace(target)
            target.with_suffix(".json").write_text(details, encoding="utf-8")
        except OSError as exc:
            raise DiskError(
                f"The handler could not be kept in {folder}: {exc.strerror or exc}."
            ) from exc
    else:
        with tempfile.TemporaryDirectory(prefix="amiga-file-forge-handler-") as staging:
            staged = [Path(staging) / target.name, Path(staging) / f"{family.key}.json"]
            staged[0].write_bytes(binary)
            staged[1].write_text(details, encoding="utf-8")
            installer = shutil.which("install") or "/usr/bin/install"
            _as_administrator(
                [installer, "-D", "-m", "0644", "-t", str(folder), *map(str, staged)],
                f"This copy of Amiga File Forge is installed for the whole machine, "
                f"so its handlers are kept in {folder}, which this account cannot "
                "write to.",
                f"sudo install -D -m 0644 {shlex.quote(family.usual_name)} "
                f"{shlex.quote(str(target))}",
                run,
            )
    return _describe(target, family, "supplied", _supplied_name(family))


def remove(family_key: str, *, run: Callable[..., Any] = subprocess.run) -> None:
    """Forget a handler that was supplied. A shipped one is then used again."""
    family = FAMILIES.get(str(family_key or ""))
    if family is None:
        raise DiskError("There is no such filing system.")
    target = _supplied_path(family)
    if not target.is_file():
        raise DiskError(f"No {family.label} handler has been supplied.")
    if os.access(target.parent, os.W_OK | os.X_OK):
        try:
            target.unlink()
            target.with_suffix(".json").unlink(missing_ok=True)
        except OSError as exc:
            raise DiskError(f"The handler could not be removed: {exc.strerror or exc}.") from exc
        return
    remover = shutil.which("rm") or "/usr/bin/rm"
    files = [str(target), str(target.with_suffix(".json"))]
    _as_administrator(
        [remover, "-f", "--", *files],
        f"The handler is kept for the whole machine in {target.parent}, which "
        "this account cannot write to.",
        "sudo rm -f -- " + " ".join(shlex.quote(name) for name in files),
        run,
    )


def store_from_drive(path: Path | str) -> list[dict]:
    """Keep every handler a drive or a drive image carries in its table."""
    try:
        from amiganut.filesystem.drive import handlers_in

        carried = handlers_in(path)
    except Exception as exc:
        raise DiskError(
            "That file holds no Rigid Disk Block to take a handler from."
        ) from exc
    kept: dict[str, dict] = {}
    for handler in carried:
        family = family_for(handler.dos_type)
        if family is None or family.key in kept or not handler.seglist:
            continue
        # The table stores a handler in whole blocks, so what comes back is
        # the load file followed by the zeros that filled its last block.
        binary = handler.seglist.rstrip(b"\0")
        binary += b"\0" * (-len(binary) % 4)
        try:
            kept[family.key] = store(family.key, binary, f"{family.usual_name} from {Path(path).name}")
        except DiskError:
            continue
    if not kept:
        raise DiskError(
            "That drive carries no filing-system handler in its partition table."
        )
    return list(kept.values())


def engine_handlers(dos_types: list[bytes]) -> tuple[list, list[dict]]:
    """Return the handlers to embed for these DOS types, and what is missing.

    Each missing entry names the family, so the caller can say exactly which
    file the person needs to supply.
    """
    from amiganut.filesystem.drive import make_handler, needs_handler

    chosen = []
    missing: list[dict] = []
    seen: set[bytes] = set()
    for dos_type in dos_types:
        if dos_type in seen or not needs_handler(dos_type):
            continue
        seen.add(dos_type)
        family = family_for(dos_type)
        binary = load(family.key) if family is not None else None
        if binary is None:
            if family is not None and not any(row["family"] == family.key for row in missing):
                missing.append({
                    "family": family.key,
                    "label": family.label,
                    "name": family.usual_name,
                })
            continue
        chosen.append(make_handler(dos_type, binary))
    return chosen, missing


__all__ = [
    "BUNDLED_DIR",
    "FAMILIES",
    "Family",
    "available",
    "check_handler",
    "engine_handlers",
    "family_for",
    "install_scope",
    "load",
    "recognise",
    "remove",
    "storage",
    "store",
    "store_from_drive",
    "user_directory",
    "version_number",
    "version_text",
]
