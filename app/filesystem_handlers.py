"""The filing-system handlers a new drive is given.

Kickstart holds the FastFileSystem and nothing else. A partition formatted with
the Professional or the Smart File System mounts only when its handler travels
with the drive, in the Rigid Disk Block, where the machine loads it before it
mounts the first partition. A drive created without one looks finished here
and shows an unreadable partition on the Amiga, so the handler is part of
creating the drive rather than something to be added by hand afterwards.

Two places hold handlers. The application ships the Professional File System,
which its licence allows. Anything else is supplied by the person using the
application and kept in their own configuration directory; a handler there
takes the place of a shipped one for the same filing system, which is how a
newer release of PFS3 is used without waiting for a new release of this
application.

A handler can also be lifted out of a drive or a drive image that carries one.
A card prepared on the Amiga, or an image made by another tool, holds exactly
the handler its partitions were formatted with.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from .checksum import sha256_bytes
from .errors import DiskError
from .image_session import SESSION_OWNER

#: Whether each owner keeps handlers of their own. The web host serves several
#: people, and a handler is a program the Amiga will run: a drive made by one
#: person must not carry a program that another person chose. The desktop
#: host has one owner and sets nothing.
PER_OWNER = False

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


def user_directory() -> Path:
    """Where handlers the user supplied are kept."""
    return Path(
        os.environ.get(
            "AMIGA_FILE_FORGE_HANDLER_DIR",
            Path.home() / ".config" / "amiga-file-forge" / "handlers",
        )
    )


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


def owner_directory() -> Path:
    """Where this request's owner keeps the handlers they supplied."""
    base = user_directory()
    owner = SESSION_OWNER.get() if PER_OWNER else None
    if owner and re.fullmatch(r"[A-Za-z0-9_-]{8,64}", owner):
        return base / "owners" / owner
    return base


def _supplied_path(family: Family) -> Path:
    """The handler supplied for a family: the owner's own, else the host's.

    The directory the host's operator keeps is read by everyone and written
    by nobody through the application, so an operator can provide a handler
    for every owner by putting it there.
    """
    own = owner_directory() / f"{family.key}.handler"
    shared = user_directory() / f"{family.key}.handler"
    return own if own.is_file() or not shared.is_file() else shared


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


def store(family_key: str, binary: bytes, name: str = "") -> dict:
    """Keep a handler the user supplied, for every drive created from now on."""
    family = FAMILIES.get(str(family_key or ""))
    if family is None:
        raise DiskError("Choose which filing system the handler is for.")
    check_handler(binary, family)
    folder = owner_directory()
    try:
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{family.key}.handler"
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(binary)
        temporary.replace(target)
        (folder / f"{family.key}.json").write_text(
            json.dumps({"name": Path(str(name or family.usual_name)).name}),
            encoding="utf-8",
        )
    except OSError as exc:
        raise DiskError(
            f"The handler could not be kept in {folder}: {exc.strerror or exc}."
        ) from exc
    return _describe(target, family, "supplied", _supplied_name(family))


def remove(family_key: str) -> None:
    """Forget a handler the user supplied. A shipped one is then used again."""
    family = FAMILIES.get(str(family_key or ""))
    if family is None:
        raise DiskError("There is no such filing system.")
    target = owner_directory() / f"{family.key}.handler"
    if not target.is_file():
        raise DiskError(f"No {family.label} handler has been supplied.")
    try:
        target.unlink()
        target.with_suffix(".json").unlink(missing_ok=True)
    except OSError as exc:
        raise DiskError(f"The handler could not be removed: {exc.strerror or exc}.") from exc


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
    "load",
    "owner_directory",
    "recognise",
    "remove",
    "store",
    "store_from_drive",
    "user_directory",
    "version_number",
    "version_text",
]
