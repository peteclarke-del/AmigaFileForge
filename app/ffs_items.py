"""Moving and deleting objects inside one mounted AmigaDOS volume.

Both operations are done through a single mount rather than one engine call per
item. That is what makes a multi-item drag or delete atomic from the user's
point of view: every path is checked before anything is written, so a selection
that contains one impossible move fails without having half-moved the rest.

The checks are the ones AmigaDOS itself enforces, plus the two a filer has to
add because it is acting on a selection: no item may collide with another item's
destination, and a drawer may not be dropped inside itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .amiganut_internals import walk_post_order
from .errors import DiskError
from . import amiga_paths

if TYPE_CHECKING:  # pragma: no cover - imported for type checkers only
    from .disk_service import DiskService
    from .image_session import ImageSession


def move_ffs_items(
    service: DiskService,
    session: ImageSession,
    items: list[dict],
) -> dict:
    """Move objects within one volume, checking the whole selection first."""
    if not service.mountable(session):
        raise DiskError("Same-image moves are available inside a mounted volume.")
    if not items:
        raise DiskError("Choose at least one file or drawer to move.")
    service.require_writable_geometry(session)

    def normalise(value: object) -> str:
        path = str(value or "").strip().rstrip(".")
        if not amiga_paths.normalise(path):
            raise DiskError("A move needs a path inside the volume, not the root itself.")
        if path in amiga_paths.ROOT_TOKENS:
            raise DiskError("The volume root cannot be moved.")
        return path

    with service.ffs_mount(session) as mount:
        moves: list[dict] = []
        destinations: set[str] = set()
        for raw in items:
            source = normalise(raw.get("source"))
            destination = normalise(raw.get("destination"))
            service.validate_leaf_name(session, amiga_paths.leaf(destination))
            if source.casefold() == destination.casefold():
                continue
            if not mount.exists(source):
                raise DiskError(f"Source path “{source}” no longer exists.")
            entry = mount.stat(source)
            if entry.is_dir and destination.casefold().startswith(
                f"{source}{amiga_paths.SEPARATOR}".casefold()
            ):
                raise DiskError("A drawer cannot be moved inside itself.")
            destination_key = destination.casefold()
            if destination_key in destinations:
                raise DiskError(f"More than one item would become “{destination}”.")
            destinations.add(destination_key)
            if mount.exists(destination):
                raise DiskError(
                    f"“{destination}” already exists. Choose another destination."
                )
            parent = amiga_paths.parent(destination)
            if not mount.exists(parent) or not mount.stat(parent).is_dir:
                raise DiskError(f"Destination drawer “{parent}” does not exist.")
            moves.append(
                {
                    "source": source,
                    "destination": destination,
                    "isDirectory": bool(entry.is_dir),
                }
            )
        if not moves:
            return {"moved": []}

        for move in moves:
            mount.rename(move["source"], move["destination"])

        session.ffs_source_names = {
            _rewrite_path(path, moves): source_name
            for path, source_name in session.ffs_source_names.items()
        }

    session.dirty = True
    service.move_editor_projects(session, moves, None)
    service._persist_session(session)
    return {"moved": moves}


def delete_ffs_items(
    service: DiskService,
    session: ImageSession,
    paths: list[str],
) -> dict:
    """Delete objects from one volume, resolving the selection first."""
    if not service.mountable(session):
        raise DiskError("This deletion helper requires a mounted AmigaDOS volume.")
    service.require_writable_geometry(session)
    sources = list(dict.fromkeys(str(path or "").strip().rstrip(".") for path in paths))
    if not sources:
        raise DiskError("Choose at least one file or drawer to delete.")
    if any(not amiga_paths.normalise(source) for source in sources):
        raise DiskError("Choose files or drawers inside the volume.")

    with service.ffs_mount(session) as mount:
        deleted_items = []
        for source in sources:
            if not mount.exists(source):
                raise DiskError(f"“{source}” no longer exists.")
            deleted_items.append(
                {"path": source, "isDirectory": bool(mount.stat(source).is_dir)}
            )

        # A selected drawer already includes anything selected below it. Removing
        # descendants from the work list avoids a misleading second "not found".
        directories = [item["path"] for item in deleted_items if item["isDirectory"]]
        deleted_items = [
            item
            for item in deleted_items
            if not any(
                item["path"].casefold().startswith(
                    f"{directory}{amiga_paths.SEPARATOR}".casefold()
                )
                for directory in directories
                if item["path"].casefold() != directory.casefold()
            )
        ]

        for item in deleted_items:
            if item["isDirectory"]:
                for target in walk_post_order(mount, item["path"]):
                    mount.remove(target, force=True)
            else:
                mount.remove(item["path"], force=True)

        def path_was_deleted(path: str) -> bool:
            return any(
                path.casefold() == item["path"].casefold()
                or (
                    item["isDirectory"]
                    and path.casefold().startswith(
                        f"{item['path']}{amiga_paths.SEPARATOR}".casefold()
                    )
                )
                for item in deleted_items
            )

        session.ffs_source_names = {
            path: source_name
            for path, source_name in session.ffs_source_names.items()
            if not path_was_deleted(path)
        }

    session.dirty = True
    service.delete_editor_projects(
        session,
        [item["path"] for item in deleted_items],
        None,
    )
    service._persist_session(session)
    result = {"deletedItems": deleted_items}
    if len(deleted_items) == 1:
        result.update(
            deletedPath=deleted_items[0]["path"],
            deletedDirectory=deleted_items[0]["isDirectory"],
        )
    return result


def _rewrite_path(path: str, moves: list[dict]) -> str:
    """Follow a path through the moves that were just applied."""
    for move in moves:
        source = move["source"]
        if path.casefold() == source.casefold():
            return str(move["destination"])
        prefix = f"{source}{amiga_paths.SEPARATOR}"
        if path.casefold().startswith(prefix.casefold()):
            return f"{move['destination']}{amiga_paths.SEPARATOR}{path[len(prefix):]}"
    return path


__all__ = ["delete_ffs_items", "move_ffs_items"]
