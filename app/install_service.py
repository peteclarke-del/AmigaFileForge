"""Installing a floppy into a hard disk, rather than only copying it there.

Copying a game disk into an HDF gives you the files. It does not give you
something that runs: the title still expects to be booted from DF0:, and the
hard drive still has no idea the title exists. Closing that gap is what this
component is for, and there are only three honest ways to do it.

**Staging** extracts the discs of a title into one host directory, merging a
multi-disc set into a single tree the way an installer would see it in a
drawer. Nothing is emulated and nothing is guessed at, so it always works and
it is always fast. What comes out is what an operator finishes by hand, either
into an image later or on the real machine. This is the default because it is
the only mode that cannot half-succeed.

**WHDLoad** is the right answer for most games and demos, and the program half
of it can be installed here directly. The per-title slave cannot be fetched
from anywhere (see ``app.whdload``), so this mode installs WHDLoad, stages the
disc content under the title's drawer, and places a slave only when one was
supplied. An install without a slave is reported as incomplete rather than
presented as finished.

**The title's own installer** cannot be second-guessed at all. Productivity
software asks questions this application has no answer to, so that mode boots
the emulator with the drive and the disc attached and gets out of the way.

Every mode here changes a drive an operator may have spent a long time
building, so each one is reached through a route that declares itself an image
mutation and therefore gets an undo checkpoint before it runs. The checkpoint
is not taken in this module: putting it in one place, at the boundary, is what
stops a new entry point from quietly arriving without one.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import amiga_paths, whdload
from .amiga_metadata import format_inf, parse_inf
from .errors import DiskError
from .image_session import ImageSession
from .lha import LHAArchive, LHAError, is_lha_bytes


#: Where staged titles are kept. It defaults inside the working directory so a
#: container with nothing mounted still works, and it is overridable because
#: the whole point of staging is to end up somewhere an Amiga can reach, which
#: on a real setup means a share or a mounted card.
STAGING_DIRECTORY_VARIABLE = "AMIGA_INSTALL_STAGING_DIR"

#: The staged payload lives under this name so the manifest can sit beside it
#: without ever being copied to the Amiga along with the title.
PAYLOAD_DIRECTORY = "files"
MANIFEST_NAME = "manifest.json"

#: Where an install puts a title inside the destination volume, unless the
#: operator picks somewhere else. Both are the conventional Amiga drawers.
DEFAULT_WHDLOAD_PARENT = "Games"
DEFAULT_INSTALL_PARENT = ""

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(title: str) -> str:
    """Reduce a title to a directory name that survives every filesystem.

    Staged titles end up on a FAT card or an Amiga volume as often as on the
    host, so the name is held to what all three accept. The readable title is
    kept in the manifest, which is what the interface shows.
    """
    folded = unicodedata.normalize("NFKD", str(title or "")).encode("ascii", "ignore").decode("ascii")
    slug = _SLUG_STRIP.sub("-", folded.casefold()).strip("-")
    return slug[:48] or "untitled"


class InstallMixin:
    """Stage, install and hand off disks that are meant to be run, not stored."""

    # ------------------------------------------------------------------
    # Staging
    # ------------------------------------------------------------------

    def staging_root(self) -> Path:
        root = os.environ.get(STAGING_DIRECTORY_VARIABLE, "").strip()
        path = Path(root) if root else self.work_dir / "staging"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _title_directory(self, slug: str) -> Path:
        clean = slugify(slug)
        directory = self.staging_root() / clean
        # slugify already removes every separator, so this cannot escape the
        # staging root. Checking anyway costs nothing and means a future
        # change to slugify cannot quietly turn into a path traversal.
        if directory.parent != self.staging_root():
            raise DiskError(f"{slug} is not a valid staged title name.")
        return directory

    @staticmethod
    def _read_manifest(directory: Path) -> dict:
        try:
            return json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    @staticmethod
    def _write_manifest(directory: Path, manifest: dict) -> None:
        """Replace the manifest atomically.

        Staging a multi-disc set writes this once per disc. A half-written
        manifest would strand the discs already staged, with the files present
        and nothing recording what they are.
        """
        temporary = directory / f"{MANIFEST_NAME}.tmp"
        temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(directory / MANIFEST_NAME)

    def _volume_name(self, session: ImageSession) -> str:
        """The name AmigaDOS shows for this volume, not the host file's name."""
        try:
            return str(self.list_directory(session, amiga_paths.ROOT).get("title") or session.name)
        except DiskError:
            return session.name

    def _walk_image(self, session: ImageSession, directory: str = amiga_paths.ROOT) -> list[dict]:
        """List every file on a volume, deepest paths last.

        ``list_directory`` is used rather than a mount so this works the same
        for an ADF, a DMS still in its archive, and a partition of a drive.
        """
        collected: list[dict] = []
        pending = [directory]
        while pending:
            current = pending.pop(0)
            listing = self.list_directory(session, current)
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

    def stage_disk(
        self,
        source: ImageSession,
        title: str,
        *,
        disc_label: str | None = None,
        progress: Callable[[str, int | None, int | None], None] | None = None,
    ) -> dict:
        """Extract one disc into a title's staging directory.

        Discs of the same title merge into one tree, which is what an
        installer expects to be pointed at and what a person expects to copy
        to a real machine. Where two discs carry the same path with different
        contents the first is kept and the later one is filed under the disc
        it came from, so a set is never silently reduced to its last disc.

        Staging under a label that is already present is the one exception:
        that is a correction, not a second disc, so its files overwrite what
        the earlier attempt left behind and any conflict recorded against that
        label is dropped. Treating it as a conflict would file the corrected
        file away as an alternate and leave the broken one in place, which is
        the opposite of what was asked for.
        """
        report = progress or (lambda _message, _current=None, _total=None: None)
        readable = str(title or source.name or "Untitled").strip() or "Untitled"
        slug = slugify(readable)
        directory = self._title_directory(slug)
        payload = directory / PAYLOAD_DIRECTORY
        payload.mkdir(parents=True, exist_ok=True)

        manifest = self._read_manifest(directory)
        discs = list(manifest.get("discs") or [])
        label = str(disc_label or "").strip()
        if not label:
            used = {disc["label"] for disc in discs}
            position = 1
            while f"Disc {position}" in used:
                position += 1
            label = f"Disc {position}"
        alternates = directory / "alternates" / slugify(label)
        replacing = any(disc["label"] == label for disc in discs)
        if replacing:
            shutil.rmtree(alternates, ignore_errors=True)

        report(f"Reading {source.name}", 0, None)
        entries = self._walk_image(source)
        files = [entry for entry in entries if not entry["directory"]]
        written = 0
        total_bytes = 0
        conflicts: list[dict] = []
        replaced_paths: set[str] = set()

        for index, entry in enumerate(entries):
            relative = amiga_paths.normalise(entry["path"])
            if not relative:
                continue
            report(f"Staging {relative}", index, len(entries))
            destination = payload / Path(*amiga_paths.split(relative))
            if entry["directory"]:
                destination.mkdir(parents=True, exist_ok=True)
                continue
            data = self.read_file(source, entry["path"])
            if destination.exists() and not replacing:
                if destination.read_bytes() == data:
                    continue
                # Same name, different bytes: both discs are kept, because
                # which one an installer wants is not knowable from here.
                spare = alternates / Path(*amiga_paths.split(relative))
                spare.parent.mkdir(parents=True, exist_ok=True)
                spare.write_bytes(data)
                conflicts.append({
                    "path": relative,
                    "keptFrom": next(
                        (disc["label"] for disc in discs if relative in disc.get("paths", [])),
                        discs[0]["label"] if discs else label,
                    ),
                    "alsoIn": label,
                    "storedAs": str(spare.relative_to(directory)),
                })
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
            self._write_sidecar(destination, relative, entry)
            written += 1
            total_bytes += len(data)
            replaced_paths.add(relative)

        summary = self.summary(source)
        record = {
            "label": label,
            "source": source.name,
            # The volume name is what an installer and an operator both
            # recognise a disc by, and it is a property of the filing system
            # rather than of the file the image happens to be stored in.
            "volume": self._volume_name(source),
            "format": str(summary.get("kind") or source.kind),
            "bootable": bool(summary.get("bootable")),
            "files": written,
            "bytes": total_bytes,
            "added": _now(),
            "paths": [amiga_paths.normalise(entry["path"]) for entry in files],
        }
        # Staging a disc under a label that is already present replaces it.
        # Somebody re-staging Disk 1 after fixing it has one Disk 1, not two,
        # and a set that silently grew every time it was corrected would be
        # impossible to reason about by the time it was installed.
        existing = next(
            (position for position, disc in enumerate(discs) if disc["label"] == label), None
        )
        if existing is None:
            discs.append(record)
        else:
            discs[existing] = record
        # A conflict is only still true if the file it named is still the one
        # on disk. Restaging a disc rewrites its files, so anything recorded
        # against this label, or against a path this disc has just replaced,
        # is stale and would otherwise be reported for ever.
        carried = [
            conflict
            for conflict in (manifest.get("conflicts") or [])
            if conflict.get("alsoIn") != label and conflict.get("path") not in replaced_paths
        ]
        manifest.update({
            "title": readable,
            "slug": slug,
            "created": manifest.get("created") or _now(),
            "updated": _now(),
            "discs": discs,
            "conflicts": carried + conflicts,
        })
        self._write_manifest(directory, manifest)
        report("Staged", len(entries), len(entries))
        return self._staged_summary(directory, manifest)

    @staticmethod
    def _write_sidecar(destination: Path, relative: str, entry: dict) -> None:
        """Record protection bits and the comment beside the staged file.

        A host filesystem has nowhere to put either, and both matter: a title
        whose loader is missing its ``e`` bit will not start. The sidecar is
        the same one this application already writes on export and reads on
        import, so a staged tree can be brought back in without loss.
        """
        if entry.get("protection") is None and not entry.get("comment"):
            return
        sidecar = destination.with_name(destination.name + ".inf")
        sidecar.write_text(
            format_inf(relative, {
                "protection": entry.get("protection"),
                "length": entry.get("length"),
                "comment": entry.get("comment"),
            }),
            encoding="latin-1",
        )

    def _staged_summary(self, directory: Path, manifest: dict | None = None) -> dict:
        manifest = manifest if manifest is not None else self._read_manifest(directory)
        discs = list(manifest.get("discs") or [])
        payload = directory / PAYLOAD_DIRECTORY
        return {
            "slug": str(manifest.get("slug") or directory.name),
            "title": str(manifest.get("title") or directory.name),
            "path": str(payload),
            "discs": [
                {key: value for key, value in disc.items() if key != "paths"}
                for disc in discs
            ],
            "discCount": len(discs),
            "fileCount": sum(int(disc.get("files") or 0) for disc in discs),
            "bytes": sum(int(disc.get("bytes") or 0) for disc in discs),
            "conflicts": list(manifest.get("conflicts") or []),
            "created": str(manifest.get("created") or ""),
            "updated": str(manifest.get("updated") or ""),
        }

    def staged_titles(self) -> list[dict]:
        """Every title waiting to be installed, most recently touched first."""
        root = self.staging_root()
        titles = [
            self._staged_summary(child)
            for child in sorted(root.iterdir())
            if child.is_dir() and (child / MANIFEST_NAME).is_file()
        ]
        return sorted(titles, key=lambda item: item["updated"], reverse=True)

    def discard_staged_title(self, slug: str) -> None:
        directory = self._title_directory(slug)
        if not directory.is_dir():
            raise DiskError(f"There is no staged title called {slug}.")
        shutil.rmtree(directory)

    def install_staged_title(
        self,
        target: ImageSession,
        slug: str,
        *,
        parent: str = DEFAULT_INSTALL_PARENT,
        drawer: str | None = None,
        progress: Callable[[str, int | None, int | None], None] | None = None,
    ) -> dict:
        """Write a staged title into a volume, under its own drawer.

        The staged tree carries its sidecars, so this restores the protection
        bits and comments the floppy had. Loaders are repaired afterwards by
        the same pass that repairs a plain copy, because a title moved off
        DF0: has the same problem however it got there.
        """
        self.require_mounted_volume(target)
        directory = self._title_directory(slug)
        manifest = self._read_manifest(directory)
        if not manifest:
            raise DiskError(f"There is no staged title called {slug}.")
        payload = directory / PAYLOAD_DIRECTORY
        readable = str(manifest.get("title") or slug)
        leaf = self.validate_leaf_name(target, str(drawer or "").strip() or readable)
        destination = amiga_paths.join(parent, leaf)
        report = progress or (lambda _message, _current=None, _total=None: None)

        items = self._staged_items(payload)
        if not items:
            raise DiskError(f"{readable} has no staged files to install.")
        report(f"Installing {readable}", 0, len(items))
        self.put_host_tree(target, destination, items, preserve_directories=True)
        repairs, warnings = self._repair_copied_ffs_loaders(target, destination)
        self._persist_session(target)
        report("Installed", len(items), len(items))
        return {
            "path": destination,
            "title": readable,
            "fileCount": len(items),
            "repairs": repairs,
            "warnings": warnings,
        }

    @staticmethod
    def _staged_items(payload: Path) -> list[dict]:
        """Turn a staged tree into the import batch the volume writer takes.

        The sidecars written during staging are read back here rather than
        being copied across as files of their own: their whole purpose is to
        put the protection bits and comment back on the entry they describe.
        """
        items: list[dict] = []
        for path in sorted(payload.rglob("*")):
            if not path.is_file() or path.name.endswith(".inf"):
                continue
            relative = path.relative_to(payload)
            metadata: dict = {}
            sidecar = path.with_name(path.name + ".inf")
            if sidecar.is_file():
                parsed = parse_inf(sidecar.read_bytes())
                if parsed:
                    metadata = {
                        "protection": parsed["protection"],
                        "comment": parsed["comment"],
                    }
            items.append({
                "targetPath": amiga_paths.SEPARATOR.join(relative.parts),
                "hostPath": path,
                "metadata": metadata,
            })
        return items

    # ------------------------------------------------------------------
    # WHDLoad
    # ------------------------------------------------------------------

    def whdload_status(self, target: ImageSession) -> dict:
        """Whether this volume already has WHDLoad, and where it came from.

        Checked before every WHDLoad install, so an image that already has a
        newer build than the one online is not quietly downgraded.
        """
        self.require_mounted_volume(target)
        if not self.mountable(target):
            raise DiskError("WHDLoad can only be installed into an AmigaDOS volume.")
        with self.ffs_mount(target) as mount:
            found = whdload.detect(mount)
        return {
            **found,
            "sources": [{"name": name, "url": url} for name, url in whdload.WHDLOAD_SOURCES],
        }

    def install_whdload(
        self,
        target: ImageSession,
        data: bytes,
        *,
        source: str,
        url: str,
        keep_preferences: bool = True,
        progress: Callable[[str, int | None, int | None], None] | None = None,
    ) -> dict:
        """Put the WHDLoad program into a volume's ``C:`` and ``S:``.

        The archive is opened and every file is decompressed before the first
        byte is written, so an archive that turns out to be truncated leaves
        the image exactly as it was rather than half-updated.
        """
        self.require_mounted_volume(target)
        if not self.mountable(target):
            raise DiskError("WHDLoad can only be installed into an AmigaDOS volume.")
        release = whdload.read_release(data, source, url)
        report = progress or (lambda _message, _current=None, _total=None: None)

        with self.ffs_mount(target) as mount:
            existing = whdload.detect(mount)
        keep = keep_preferences and existing["installed"]
        plan = whdload.installation_plan(release.archive, keep_preferences=keep)

        report(f"Reading {release.label}", 0, len(plan))
        contents: list[tuple[str, bytes]] = []
        for index, (member_path, destination) in enumerate(plan):
            member = release.archive.find(member_path)
            report(f"Reading {destination}", index, len(plan))
            try:
                contents.append((destination, release.archive.read(member)))
            except LHAError as exc:
                raise DiskError(f"The WHDLoad archive could not be read: {exc}") from exc

        written: list[str] = []
        with self.ffs_mount(target) as mount:
            for index, (destination, payload) in enumerate(contents):
                report(f"Writing {destination}", index, len(contents))
                parent = amiga_paths.parent(destination)
                if parent and not mount.exists(parent):
                    mount.make_directory(parent, parents=True, exist_ok=True)
                mount.write_bytes(destination, payload)
                written.append(destination)
        self._mark_mutated(target)
        self._persist_session(target)
        report("WHDLoad installed", len(contents), len(contents))
        return {
            "version": release.version,
            "label": release.label,
            "source": release.source,
            "url": release.url,
            "files": written,
            "replaced": existing["installed"],
            "previousVersion": existing["version"],
            # Whether this moved the drive forwards. Reinstalling the same
            # build and putting an older one over a newer one are both
            # legitimate and both worth saying out loud, because the operator
            # asked for an install and would otherwise assume an upgrade.
            "upgraded": whdload.newer(release.version, existing["version"]),
            "keptPreferences": keep,
        }

    def install_whdload_slave(
        self,
        target: ImageSession,
        destination: str,
        data: bytes,
        name: str,
    ) -> dict:
        """Place one slave, taken from a file or from an archive of one.

        A slave arrives either bare or inside the small LHA it was published
        in. Both are accepted, because insisting the operator unpack it first
        would mean insisting they have an LHA tool, which is the dependency
        this build went out of its way not to need.
        """
        self.require_mounted_volume(target)
        payload = data
        leaf = name
        if is_lha_bytes(data):
            try:
                archive = LHAArchive(data)
            except LHAError as exc:
                raise DiskError(f"{name} could not be opened: {exc}") from exc
            members = [member for member in archive.members if whdload.is_slave_name(member.path)]
            if not members:
                raise DiskError(f"{name} does not contain a WHDLoad slave.")
            if len(members) > 1:
                raise DiskError(
                    f"{name} contains {len(members)} slaves. Extract the one you want and add it directly."
                )
            payload = archive.read(members[0])
            leaf = members[0].name
        if not whdload.is_slave_name(leaf):
            raise DiskError(f"{leaf} is not a WHDLoad slave; a slave's name ends in .slave.")
        path = amiga_paths.join(destination, self.validate_leaf_name(target, leaf))
        with self.ffs_mount(target) as mount:
            if destination and not mount.exists(destination):
                mount.make_directory(destination, parents=True, exist_ok=True)
            mount.write_bytes(path, payload)
        self._mark_mutated(target)
        self._persist_session(target)
        return {"path": path, "name": leaf, "bytes": len(payload)}


__all__ = [
    "DEFAULT_INSTALL_PARENT",
    "DEFAULT_WHDLOAD_PARENT",
    "MANIFEST_NAME",
    "PAYLOAD_DIRECTORY",
    "STAGING_DIRECTORY_VARIABLE",
    "InstallMixin",
    "slugify",
]
