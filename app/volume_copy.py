"""Reading an Amiga volume, and copying its contents into another one.

Two things in this application copy a whole volume into a drawer on a second
volume: staging the discs of a title onto the drive it is destined for, and
installing AmigaOS from the Workbench floppies. They were written separately
and came out almost identical, which is the usual way a subtle difference
appears between two paths that were supposed to behave the same.

The steps are the same either way. Read what is already at the destination, so
a file that is there can be recognised without asking the volume about every
name in turn. Walk the source. Spill each file to a host temporary, because the
volume writer takes a batch of host paths and writing the batch in one mount is
what makes a thousand-file disk take a moment rather than minutes. Then create
the drawers that ended up with no files in them, because a drawer an installer
writes into has to exist even when the disc shipped it empty.

Only one thing genuinely differs, and it is what to do about a file that is
already there:

``replace``
    Write it anyway. This is a disc being staged again after correction: the
    newer copy is the point of the exercise.
``skip``
    Leave what is there. This is the second and later disks of an AmigaOS
    release, where the copy order was chosen precisely so that the earlier
    disk wins, and the Workbench disk's full ``C:`` is not overwritten by the
    cut-down copy Extras carries.
``divert``
    Leave what is there, and put a file whose bytes disagree somewhere else so
    that neither is lost. This is a multi-disc set merging into one tree, where
    which copy an installer wants is not knowable from here. A file whose bytes
    are identical is simply skipped; only a real disagreement is diverted.

A file that cannot be read, or whose name the destination will not accept, is
reported rather than raised. One bad file on a floppy should not throw away the
nine hundred that copied, and the caller has somewhere to put the report.
"""

from __future__ import annotations

import dataclasses
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from . import amiga_paths
from . import progress as progress_module
from .errors import DiskError
from .image_session import ImageSession

if TYPE_CHECKING:  # pragma: no cover - imported for type checkers only
    from .disk_service import DiskService


#: What to do with a file the destination already has.
EXISTING_POLICIES = ("replace", "skip", "divert")


@dataclasses.dataclass
class CopyReport:
    """What a copy did, in enough detail for a caller to describe it."""

    #: Paths written into the destination, relative to it.
    written: list[str] = dataclasses.field(default_factory=list)
    #: Total size of those files.
    bytes_written: int = 0
    #: Paths the destination already had, left as they were.
    skipped: list[str] = dataclasses.field(default_factory=list)
    #: Paths whose bytes disagreed with what was there, written elsewhere.
    diverted: list[str] = dataclasses.field(default_factory=list)
    #: Drawers created because the source carried them and no file landed in them.
    directories: list[str] = dataclasses.field(default_factory=list)
    #: Files that could not be read, or that the destination would not accept.
    warnings: list[str] = dataclasses.field(default_factory=list)

    @property
    def file_count(self) -> int:
        return len(self.written)


def volume_name(service: DiskService, session: ImageSession) -> str:
    """The name AmigaDOS shows for this volume, not the host file's name."""
    try:
        return str(
            service.list_directory(session, amiga_paths.ROOT).get("title") or session.name
        )
    except DiskError:
        return session.name


def walk_volume(
    service: DiskService, session: ImageSession, directory: str = amiga_paths.ROOT
) -> list[dict]:
    """List every entry on a volume, parents before the things inside them.

    ``list_directory`` is used rather than a mount so this works the same for
    an ADF, a DMS still in its archive, and a partition of a drive.
    """
    collected: list[dict] = []
    pending = [directory]
    while pending:
        current = pending.pop(0)
        listing = service.list_directory(session, current)
        for entry in listing.get("entries", []):
            path = str(entry.get("path") or amiga_paths.join(current, entry.get("name", "")))
            if entry.get("type") == "dir":
                collected.append({"path": path, "directory": True})
                pending.append(path)
                continue
            collected.append({
                "path": path,
                "directory": False,
                "length": int(entry.get("length") or 0),
                "protection": entry.get("protection"),
                "comment": str(entry.get("comment") or ""),
                "filetype": str(entry.get("filetype") or ""),
            })
    return collected


def drawer_exists(service: DiskService, session: ImageSession, path: str) -> bool:
    """Whether a drawer is present, asked in the way every format answers."""
    try:
        service.list_directory(session, path)
    except DiskError:
        return False
    return True


def entry_exists(service: DiskService, session: ImageSession, path: str) -> bool:
    """Whether anything at all, file or drawer, is at this path.

    ``drawer_exists`` can only answer for drawers, because listing is what it
    asks. Asking it about a file says the file is absent, which once turned a
    finished Workbench install into one that warned its own
    ``S:Startup-Sequence`` was missing while the file sat there.
    """
    normalised = amiga_paths.normalise(path)
    if not normalised:
        return True
    leaf = amiga_paths.leaf(normalised).casefold()
    try:
        listing = service.list_directory(
            session, amiga_paths.parent(normalised) or amiga_paths.ROOT
        )
    except DiskError:
        return False
    return any(
        str(entry.get("name") or "").casefold() == leaf
        for entry in listing.get("entries", [])
    )


def relative_to(path: str, base: str) -> str:
    """The part of ``path`` below ``base``, in AmigaDOS spelling."""
    normalised = amiga_paths.normalise(path)
    prefix = amiga_paths.normalise(base)
    if not prefix:
        return normalised
    if normalised.casefold() == prefix.casefold():
        return ""
    marker = f"{prefix}{amiga_paths.SEPARATOR}".casefold()
    if not normalised.casefold().startswith(marker):
        return ""
    return normalised[len(marker):]


def files_present(
    service: DiskService, session: ImageSession, directory: str
) -> dict[str, str]:
    """Every file under ``directory``, keyed by its case-folded relative path.

    Read in one pass and compared against, rather than asking the volume about
    each name in turn. A Workbench disk holds well over a thousand entries, and
    the difference is an install that takes a moment against one that takes
    minutes.
    """
    if not drawer_exists(service, session, directory or amiga_paths.ROOT):
        return {}
    present: dict[str, str] = {}
    for entry in walk_volume(service, session, directory or amiga_paths.ROOT):
        if entry["directory"]:
            continue
        relative = relative_to(entry["path"], directory)
        if relative:
            present[relative.casefold()] = entry["path"]
    return present


def entry_metadata(entry: dict) -> dict:
    """The Amiga metadata that has to travel with a file, in writer form.

    A loader that arrives without its ``e`` bit will not start, and the failure
    looks nothing like a missing permission, so this is not decoration.
    """
    return {"protection": entry.get("protection"), "comment": entry.get("comment")}


def write_file(
    service: DiskService,
    target: ImageSession,
    path: str,
    data: bytes,
    metadata: dict | None = None,
) -> None:
    """Write one file into a volume, replacing whatever is there."""
    with tempfile.NamedTemporaryFile(
        dir=service.work_dir, prefix="volume-", delete=False
    ) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    try:
        service.put_host_tree(
            target,
            amiga_paths.parent(path) or amiga_paths.ROOT,
            [{
                "targetPath": amiga_paths.leaf(path),
                "hostPath": temporary,
                "metadata": metadata or {},
            }],
            preserve_directories=True,
            replace=True,
        )
    finally:
        temporary.unlink(missing_ok=True)


def delete_tree(service: DiskService, target: ImageSession, path: str) -> None:
    """Remove a drawer and everything below it."""
    from .ffs_items import delete_ffs_items

    delete_ffs_items(service, target, [path])


def copy_volume_tree(
    service: DiskService,
    source: ImageSession,
    target: ImageSession,
    destination: str,
    *,
    existing: str = "skip",
    divert_to: str = "",
    progress: progress_module.Progress | None = None,
    message: str = "Copying",
) -> CopyReport:
    """Copy everything on ``source`` into ``destination`` on ``target``.

    ``existing`` says what to do about a file the destination already has, and
    is one of ``EXISTING_POLICIES``. ``divert_to`` names the drawer that a
    disagreeing file is written into, and is required by the ``divert`` policy.
    """
    if existing not in EXISTING_POLICIES:
        raise DiskError(f"{existing} is not a way of handling files that are already there.")
    if existing == "divert" and not divert_to:
        raise DiskError("Diverting a disagreeing file needs somewhere to put it.")

    report = progress_module.reporter(progress)
    result = CopyReport()
    present = files_present(service, target, destination) if existing != "replace" else {}

    entries = walk_volume(service, source)
    carried_directories: list[str] = []
    temporary: list[Path] = []
    destination_items: list[dict] = []
    diverted_items: list[dict] = []

    def spill(data: bytes) -> Path:
        with tempfile.NamedTemporaryFile(
            dir=service.work_dir, prefix="volume-", delete=False
        ) as handle:
            handle.write(data)
        path = Path(handle.name)
        temporary.append(path)
        return path

    try:
        for index, entry in enumerate(entries):
            relative = amiga_paths.normalise(entry["path"])
            if not relative:
                continue
            report(f"{message} {relative}", index, len(entries))
            try:
                for part in amiga_paths.split(relative):
                    service.validate_leaf_name(target, part)
            except DiskError as exc:
                result.warnings.append(f"{source.name}: {relative} was left out ({exc}).")
                continue
            if entry["directory"]:
                carried_directories.append(relative)
                continue

            already = present.get(relative.casefold())
            if already is not None and existing == "skip":
                result.skipped.append(relative)
                continue
            try:
                data = service.read_file(source, entry["path"])
            except DiskError as exc:
                result.warnings.append(f"{source.name}: {relative} could not be read ({exc}).")
                continue
            if already is not None and existing == "divert":
                try:
                    same = service.read_file(target, already) == data
                except DiskError:
                    same = False
                if same:
                    result.skipped.append(relative)
                    continue
                diverted_items.append({
                    "targetPath": relative,
                    "hostPath": spill(data),
                    "metadata": entry_metadata(entry),
                })
                result.diverted.append(relative)
                continue

            destination_items.append({
                "targetPath": relative,
                "hostPath": spill(data),
                "metadata": entry_metadata(entry),
            })
            result.written.append(relative)
            result.bytes_written += len(data)

        for batch, into in ((destination_items, destination), (diverted_items, divert_to)):
            if not batch:
                continue
            report(f"Writing {len(batch)} file(s) into {into or amiga_paths.ROOT_DISPLAY}",
                   len(entries), len(entries))
            service.put_host_tree(
                target, into or amiga_paths.ROOT, batch,
                preserve_directories=True, replace=True,
            )
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)

    result.directories = _create_carried_drawers(
        service, target, destination, carried_directories, destination_items, result.warnings
    )
    return result


def _create_carried_drawers(
    service: DiskService,
    target: ImageSession,
    destination: str,
    carried: list[str],
    written_items: list[dict],
    warnings: list[str],
) -> list[str]:
    """Make the drawers the source had that no file landed in.

    A drawer the disc carried but put no files in is still part of the disc: an
    installer that writes into it fails if it is absent, and a Workbench
    without ``Prefs/Presets`` cannot save a preference. Drawers that a written
    file already created are left alone, which is most of them.
    """
    written_parents = {
        parent for parent in (amiga_paths.parent(item["targetPath"]) for item in written_items)
        if parent
    }
    created: list[str] = []
    for relative in carried:
        marker = f"{relative}{amiga_paths.SEPARATOR}".casefold()
        if any(
            parent == relative or parent.casefold().startswith(marker)
            for parent in written_parents
        ):
            continue
        path = amiga_paths.join(destination, relative) if destination else relative
        if drawer_exists(service, target, path):
            continue
        try:
            service.make_directory(target, path)
        except DiskError as exc:
            warnings.append(f"{path} could not be created: {exc}")
            continue
        created.append(relative)
    return created


__all__ = [
    "EXISTING_POLICIES",
    "CopyReport",
    "copy_volume_tree",
    "delete_tree",
    "drawer_exists",
    "entry_exists",
    "entry_metadata",
    "files_present",
    "relative_to",
    "volume_name",
    "walk_volume",
    "write_file",
]
