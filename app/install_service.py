"""Installing a floppy into a hard disk, rather than only copying it there.

Copying a game disk into an HDF gives you the files. It does not give you
something that runs: the title still expects to be booted from DF0:, and the
hard drive still has no idea the title exists. Closing that gap is what this
component is for, and there are only four honest ways to do it.

**Staging** extracts the discs of a title into a drawer *on the drive being
built*, merging a multi-disc set into a single tree the way an installer would
see it in a drawer. Nothing is emulated and nothing is guessed at, so it always
works and it is always fast. Putting it on the target image rather than in a
directory on this machine is the whole point: the operator boots the drive in
an emulator, or puts it in a real Amiga, and finishes the job there with the
discs already in front of them. This is the default because it is the only
mode that cannot half-succeed.

**Workbench** is the one install this application can perform in full, because
AmigaOS is installed by copying disks into known places rather than by running
code (see ``app.workbench_install``). Given the operator's own Workbench
floppies it prepares a drive that boots.

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
import re
import unicodedata
from datetime import datetime, timezone

from . import amiga_paths, volume_copy, whdload
from . import progress as progress_module
from .errors import DiskError
from .image_session import ImageSession
from .lha import LHAArchive, LHAError, is_lha_bytes


#: The drawer on the target volume that holds staged discs, unless the operator
#: names another.
#:
#: ``Storage`` is where Workbench keeps what is not in use yet, which is
#: exactly what a staged set is, and ``Storage/Install`` is where the PiStorm
#: imager puts the same thing. The obvious alternative, a plain ``Install``
#: drawer at the volume root, is already taken: the AmigaOS Install disk is
#: copied there by a Workbench install, so staging into it would list that
#: disk's own ``c`` and ``Libs`` drawers as though they were staged titles.
DEFAULT_STAGING_PARENT = "Storage/Install"

#: Staging keeps its own records out of the payload drawer, because that drawer
#: has to be exactly what gets installed. Everything belonging to this
#: application - the manifest, and the files a later disc disagreed about -
#: lives here instead, and the whole drawer can be deleted once a set is in.
HOUSEKEEPING_DIRECTORY = "Forge-Staging"
MANIFEST_NAME = "Manifest"

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

    def staging_parent(self, requested: str = "") -> str:
        """The drawer on the target volume that holds staged discs."""
        value = str(requested or "").strip().strip(amiga_paths.SEPARATOR)
        return amiga_paths.normalise(value) or DEFAULT_STAGING_PARENT

    def _housekeeping_drawer(self, staging: str, leaf: str = "") -> str:
        """Where the record of a staging run lives, away from the payload.

        The payload drawer has to be exactly what gets installed, so nothing
        belonging to this application may sit inside it. Everything that is
        bookkeeping, the manifest and the files a later disc disagreed about,
        goes here instead, in one drawer the operator can delete once the
        title is in place.
        """
        base = amiga_paths.join(staging, HOUSEKEEPING_DIRECTORY)
        return amiga_paths.join(base, leaf) if leaf else base

    def _staging_paths(
        self, target: ImageSession, title: str, parent: str
    ) -> tuple[str, str, str]:
        """The three places a staged title occupies, worked out in one place.

        Every entry point needs the same trio and they have to agree: the
        payload drawer that gets installed, the housekeeping drawer beside it,
        and the legal Amiga leaf name both are built from.
        """
        staging = self.staging_parent(parent)
        leaf = self.validate_leaf_name(target, str(title or "").strip())
        return staging, leaf, amiga_paths.join(staging, leaf)

    def _read_staged_manifest(self, target: ImageSession, staging: str, leaf: str) -> dict:
        drawer = self._housekeeping_drawer(staging, leaf)
        if not volume_copy.drawer_exists(self, target, drawer):
            return {}
        try:
            return json.loads(
                self.read_file(target, amiga_paths.join(drawer, MANIFEST_NAME)).decode("utf-8")
            )
        except Exception:
            # A record that cannot be read is a record that is not there. The
            # payload on the drive is the authority, and a staged set has to
            # stay usable when its notes are damaged or were never written.
            return {}

    def _write_staged_manifest(
        self, target: ImageSession, staging: str, leaf: str, manifest: dict
    ) -> None:
        """Record what was staged, on the drive itself.

        Keeping this on the image rather than on the host is what lets a drive
        be carried to another machine, or to a real Amiga, and still describe
        the set that is waiting on it. It is written into the housekeeping
        drawer, so installing the title never carries it along.
        """
        body = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
        volume_copy.write_file(
            self, target,
            amiga_paths.join(self._housekeeping_drawer(staging, leaf), MANIFEST_NAME),
            body,
        )

    def _next_disc_label(self, discs: list[dict], requested: str | None) -> str:
        """The label this disc is filed under, named or numbered in turn."""
        label = str(requested or "").strip()
        if label:
            return label
        used = {disc["label"] for disc in discs}
        position = 1
        while f"Disc {position}" in used:
            position += 1
        return f"Disc {position}"

    def stage_disk(
        self,
        source: ImageSession,
        target: ImageSession,
        title: str,
        *,
        parent: str = "",
        disc_label: str | None = None,
        progress: progress_module.Progress | None = None,
    ) -> dict:
        """Extract one disc into a drawer on the drive it is destined for.

        Staging exists so that the install can be finished where the title will
        actually run, which means the discs have to be somewhere an Amiga can
        reach: a drawer on the target volume, not a directory on the machine
        running this application. An operator can then boot the drive in an
        emulator or put it in a real machine, open the drawer and run the
        title's own installer against it.

        Discs of the same title merge into one tree, which is what an installer
        expects to be pointed at. Where two discs carry the same path with
        different contents the first is kept and the later one is filed in the
        housekeeping drawer under the disc it came from, so a set is never
        silently reduced to its last disc.

        Staging under a label that is already present is the one exception:
        that is a correction, not a second disc, so its files overwrite what
        the earlier attempt left behind and any conflict recorded against that
        label is dropped.
        """
        report = progress_module.reporter(progress)
        self.require_mounted_volume(target)
        if self.summary(target).get("readOnly"):
            raise DiskError(f"{target.name} is open read-only, so nothing can be staged onto it.")
        self.require_writable_geometry(target)

        readable = str(title or source.name or "Untitled").strip() or "Untitled"
        staging, leaf, drawer = self._staging_paths(target, readable, parent)

        manifest = self._read_staged_manifest(target, staging, leaf)
        discs = list(manifest.get("discs") or [])
        label = self._next_disc_label(discs, disc_label)
        alternates = amiga_paths.join(
            self._housekeeping_drawer(staging, leaf), self.validate_leaf_name(target, label)
        )
        # Re-staging a disc under a label that is already there is a
        # correction, so its files replace what the earlier attempt wrote and
        # anything filed aside for that disc stops being true.
        replacing = any(disc["label"] == label for disc in discs)
        if replacing and volume_copy.drawer_exists(self, target, alternates):
            volume_copy.delete_tree(self, target, alternates)

        report(f"Reading {source.name}", 0, None)
        copied = volume_copy.copy_volume_tree(
            self, source, target, drawer,
            existing="replace" if replacing else "divert",
            divert_to=alternates,
            progress=report,
            message="Staging",
        )

        summary = self.summary(source)
        record = {
            "label": label,
            "source": source.name,
            # The volume name is what an installer and an operator both
            # recognise a disc by, and it is a property of the filing system
            # rather than of the file the image happens to be stored in.
            "volume": volume_copy.volume_name(self, source),
            "format": str(summary.get("kind") or source.kind),
            "bootable": bool(summary.get("bootable")),
            "files": copied.file_count,
            "bytes": copied.bytes_written,
            "added": _now(),
            "paths": sorted(set(copied.written) | set(copied.skipped) | set(copied.diverted)),
        }
        # Somebody re-staging Disk 1 after fixing it has one Disk 1, not two.
        # A set that silently grew every time it was corrected would be
        # impossible to reason about by the time it was installed.
        position = next(
            (offset for offset, disc in enumerate(discs) if disc["label"] == label), None
        )
        if position is None:
            discs.append(record)
        else:
            discs[position] = record

        earlier = {disc["label"] for disc in discs if disc["label"] != label}
        conflicts = [
            {
                "path": path,
                "keptFrom": next(
                    (disc["label"] for disc in discs if path in disc.get("paths", [])
                     and disc["label"] in earlier),
                    sorted(earlier)[0] if earlier else label,
                ),
                "alsoIn": label,
                "storedAs": amiga_paths.join(alternates, path),
            }
            for path in copied.diverted
        ]
        # A conflict is only still true if the file it named is still the one
        # on disk. Restaging a disc rewrites its files, so anything recorded
        # against this label, or against a path this disc has just replaced,
        # is stale and would otherwise be reported for ever.
        rewritten = set(copied.written)
        carried = [
            conflict
            for conflict in (manifest.get("conflicts") or [])
            if conflict.get("alsoIn") != label and conflict.get("path") not in rewritten
        ]
        manifest.update({
            "title": readable,
            "name": leaf,
            "slug": slugify(readable),
            "parent": staging,
            "created": manifest.get("created") or _now(),
            "updated": _now(),
            "discs": discs,
            "conflicts": carried + conflicts,
        })
        self._write_staged_manifest(target, staging, leaf, manifest)
        self._persist_session(target)
        report("Staged", 1, 1)
        staged = self._staged_summary(target, staging, leaf, manifest)
        staged["warnings"] = copied.warnings
        return staged

    def _staged_summary(
        self, target: ImageSession, staging: str, leaf: str, manifest: dict | None = None
    ) -> dict:
        manifest = (
            manifest if manifest is not None
            else self._read_staged_manifest(target, staging, leaf)
        )
        drawer = amiga_paths.join(staging, leaf)
        discs = list(manifest.get("discs") or [])
        if discs:
            file_count = sum(int(disc.get("files") or 0) for disc in discs)
            total_bytes = sum(int(disc.get("bytes") or 0) for disc in discs)
        else:
            # A drawer somebody made by hand, or one whose record was deleted,
            # is still a staged title. Measuring it is better than hiding it.
            staged_files = [
                entry for entry in volume_copy.walk_volume(self, target, drawer)
                if not entry["directory"]
            ]
            file_count = len(staged_files)
            total_bytes = sum(int(entry.get("length") or 0) for entry in staged_files)
        return {
            "name": leaf,
            "slug": str(manifest.get("slug") or slugify(leaf)),
            "title": str(manifest.get("title") or leaf),
            "path": drawer,
            "parent": staging,
            "discs": [
                {key: value for key, value in disc.items() if key != "paths"}
                for disc in discs
            ],
            "discCount": len(discs),
            "fileCount": file_count,
            "bytes": total_bytes,
            "conflicts": list(manifest.get("conflicts") or []),
            "warnings": [],
            "created": str(manifest.get("created") or ""),
            "updated": str(manifest.get("updated") or ""),
        }

    def staged_titles(self, target: ImageSession, *, parent: str = "") -> list[dict]:
        """Every title waiting on this drive, most recently touched first.

        The list is read off the volume rather than out of a record kept here,
        so a drive built somewhere else, or one whose manifest was deleted,
        still reports what is sitting in its staging drawer.
        """
        self.require_mounted_volume(target)
        staging = self.staging_parent(parent)
        if not volume_copy.drawer_exists(self, target, staging):
            return []
        listing = self.list_directory(target, staging)
        titles = [
            self._staged_summary(target, staging, str(entry.get("name") or ""))
            for entry in listing.get("entries", [])
            if entry.get("type") == "dir"
            and str(entry.get("name") or "").casefold() != HOUSEKEEPING_DIRECTORY.casefold()
        ]
        return sorted(titles, key=lambda item: (item["updated"], item["name"]), reverse=True)

    def discard_staged_title(self, target: ImageSession, name: str, *, parent: str = "") -> None:
        """Remove a staged title from the drive, payload and record together."""
        self.require_mounted_volume(target)
        staging, leaf, drawer = self._staging_paths(target, name, parent)
        if not volume_copy.drawer_exists(self, target, drawer):
            raise DiskError(f"There is no staged title called {leaf} in {staging}.")
        volume_copy.delete_tree(self, target, drawer)
        housekeeping = self._housekeeping_drawer(staging, leaf)
        if volume_copy.drawer_exists(self, target, housekeeping):
            volume_copy.delete_tree(self, target, housekeeping)
        self._persist_session(target)

    def install_staged_title(
        self,
        target: ImageSession,
        name: str,
        *,
        parent: str = DEFAULT_INSTALL_PARENT,
        staging: str = "",
        drawer: str | None = None,
        progress: progress_module.Progress | None = None,
    ) -> dict:
        """Move a staged title out of the staging drawer into its own home.

        Both ends are on the same volume, so this is a move rather than a copy:
        the protection bits, comments and datestamps the discs carried are the
        ones already written, and nothing is read or written twice. Loaders are
        repaired afterwards by the same pass that repairs a plain copy, because
        a title moved off DF0: has the same problem however it got there.
        """
        from .ffs_items import move_ffs_items

        self.require_mounted_volume(target)
        self.require_writable_geometry(target)
        staging_parent, leaf, source = self._staging_paths(target, name, staging)
        if not volume_copy.drawer_exists(self, target, source):
            raise DiskError(f"There is no staged title called {leaf} in {staging_parent}.")

        manifest = self._read_staged_manifest(target, staging_parent, leaf)
        readable = str(manifest.get("title") or leaf)
        target_leaf = self.validate_leaf_name(target, str(drawer or "").strip() or leaf)
        destination = amiga_paths.join(parent, target_leaf)
        if destination.casefold() == source.casefold():
            raise DiskError(f"{readable} is already installed at {destination}.")
        if volume_copy.drawer_exists(self, target, destination):
            raise DiskError(f"{destination} already exists. Choose another drawer name.")

        report = progress_module.reporter(progress)
        report(f"Installing {readable}", 0, None)
        staged_files = [
            entry for entry in volume_copy.walk_volume(self, target, source)
            if not entry["directory"]
        ]
        if not staged_files:
            raise DiskError(f"{readable} has no staged files to install.")
        if parent:
            for part in amiga_paths.split(parent):
                self.validate_leaf_name(target, part)
            if not volume_copy.drawer_exists(self, target, parent):
                self.make_directory(target, parent)
        move_ffs_items(self, target, [{"source": source, "destination": destination}])
        repairs, warnings = self._repair_copied_ffs_loaders(target, destination)
        housekeeping = self._housekeeping_drawer(staging_parent, leaf)
        if volume_copy.drawer_exists(self, target, housekeeping):
            volume_copy.delete_tree(self, target, housekeeping)
        self._persist_session(target)
        report("Installed", len(staged_files), len(staged_files))
        return {
            "path": destination,
            "title": readable,
            "name": target_leaf,
            "fileCount": len(staged_files),
            "repairs": repairs,
            "warnings": warnings,
        }

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
        progress: progress_module.Progress | None = None,
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
        report = progress_module.reporter(progress)

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
    "DEFAULT_STAGING_PARENT",
    "DEFAULT_WHDLOAD_PARENT",
    "HOUSEKEEPING_DIRECTORY",
    "MANIFEST_NAME",
    "InstallMixin",
    "slugify",
]
