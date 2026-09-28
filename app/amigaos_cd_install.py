"""Installing AmigaOS 3.5 or 3.9 onto a drive from the release CD.

The installation is made here, in full, and written into the partition that is
open. Nothing is left for the operator to finish inside an emulator: when this
returns, the drive holds the system and starts the machine.

It is done the way the PiStorm imager does it. The layout of each release is
read out of the Installer script on its disc and kept in ``app.amigaos_cd``,
the layers are resolved by name so that the newest copy of every file is the
one that lands, and the volume is written once. The update packs published
after each release go over the top in the order they came out.

What the disc's own installer asks about and this does not is the choice of
what to leave out. Every language, keymap and printer driver the disc carries
is installed, which costs a few megabytes on a drive measured in gigabytes and
means nothing has to be fetched from the disc again later.

A file already on the drive is replaced when the release carries one of the
same name, which is what makes installing over Workbench 3.1 an upgrade.
Anything the release does not carry is left exactly as it was, so a drive that
already holds software keeps it.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from . import amiga_paths, amigaos_cd, boingbag, volume_copy
from . import progress as progress_module
from .errors import DiskError
from .image_session import ImageSession
from .system_tree import PlannedFile, SystemTree
from .workbench_install import STARTUP_SEQUENCE

#: How much is spilled to the host and written in one mount. Large enough that
#: a system is a handful of mounts, small enough that a cancelled installation
#: stops promptly and the temporary files stay modest.
BATCH_FILES = 250
BATCH_BYTES = 12 * 1024 * 1024

#: Files a person edits, which an installation over an existing system must
#: not put back to how the disc has them.
KEPT_IF_PRESENT = ("S/User-Startup",)


def _batches(files: list[PlannedFile]):
    batch: list[PlannedFile] = []
    size = 0
    for item in files:
        if batch and (len(batch) >= BATCH_FILES or size + item.length > BATCH_BYTES):
            yield batch
            batch, size = [], 0
        batch.append(item)
        size += item.length
    if batch:
        yield batch


def write_system_tree(
    service,
    target: ImageSession,
    tree: SystemTree,
    progress: progress_module.Progress | None = None,
    *,
    message: str = "Installing",
) -> dict:
    """Write a resolved system into the open volume, replacing what is there."""
    report = progress_module.reporter(progress)
    files = tree.files()
    warnings: list[str] = []
    accepted: list[PlannedFile] = []
    for item in files:
        try:
            for part in item.path.split("/"):
                service.validate_leaf_name(target, part)
        except DiskError as exc:
            warnings.append(f"{item.path} was left out ({exc}).")
            continue
        accepted.append(item)

    written = 0
    written_bytes = 0
    for batch in _batches(accepted):
        report(f"{message}: {batch[0].path}", written, len(accepted))
        temporary: list[Path] = []
        items: list[dict] = []
        try:
            for item in batch:
                try:
                    data = item.read()
                except Exception as exc:  # noqa: BLE001 - one bad file is reported
                    warnings.append(f"{item.path} could not be read from {item.origin} ({exc}).")
                    continue
                with tempfile.NamedTemporaryFile(
                    dir=service.work_dir, prefix="system-", delete=False
                ) as handle:
                    handle.write(data)
                temporary.append(Path(handle.name))
                items.append({
                    "targetPath": item.path,
                    "hostPath": Path(handle.name),
                    "metadata": {"protection": item.protection, "comment": item.comment},
                })
                written_bytes += len(data)
            if items:
                service.put_host_tree(
                    target, "", items, preserve_directories=True, replace=True,
                )
                written += len(items)
        finally:
            for path in temporary:
                path.unlink(missing_ok=True)

    drawers: list[str] = []
    for drawer in tree.empty_drawers():
        if volume_copy.drawer_exists(service, target, drawer):
            continue
        try:
            service.make_directory(target, drawer)
        except DiskError as exc:
            warnings.append(f"{drawer} could not be created: {exc}")
            continue
        drawers.append(drawer)
    report(message, len(accepted), len(accepted))
    return {
        "written": written,
        "bytes": written_bytes,
        "drawers": drawers,
        "warnings": warnings,
    }


class AmigaosCdInstallMixin:
    """Install AmigaOS from a release CD into the volume that is open."""

    def plan_amigaos_cd(self, disc: ImageSession, image) -> dict:
        """Resolve the disc's layers, refusing a disc that cannot install."""
        found = self.amigaos_release_on(disc)
        if not found.get("recognised"):
            raise DiskError(found.get("reason") or "That disc is not an AmigaOS release CD.")
        release = next(
            item for item in amigaos_cd.RELEASES if item.key == found["release"]
        )
        planned = amigaos_cd.plan_release(image, release)
        lacking = [item["label"] for item in planned["missing"] if item["required"]]
        if lacking:
            raise DiskError(
                f"{disc.name} is missing {', '.join(lacking)}, which {release.label} "
                "cannot be installed without. The image may be incomplete."
            )
        planned["release"] = release
        return planned

    def install_amigaos_cd(
        self,
        target: ImageSession,
        disc: ImageSession,
        *,
        packs: list[tuple[str, bytes]] | None = None,
        chosen_packs: list[str] | None = None,
        use_emulator: bool = True,
        progress: progress_module.Progress | None = None,
    ) -> dict:
        """Install the release on ``disc`` into the open volume of ``target``.

        ``packs`` are the update archives the operator supplied, as name and
        bytes. ``chosen_packs`` narrows them to the named packs, and when it is
        empty every pack found for the release that is on by default is
        applied.
        """
        from .boingbag_update import apply_locked_pack
        from .emulator_config import profile_machine
        from .hardware_profiles import profile_addons

        report = progress_module.reporter(progress)
        checked = self.amigaos_cd_preflight(target, disc)
        if not checked["ready"]:
            raise DiskError(checked["blocking"][0])
        if self.summary(target).get("readOnly"):
            raise DiskError(f"{target.name} is open read-only, so nothing can be installed onto it.")
        self.require_mounted_volume(target)
        self.require_writable_geometry(target)

        report("Reading the disc", 0, None)
        archives = boingbag.open_archives(packs or [])
        with self.iso_image(disc) as image:
            planned = self.plan_amigaos_cd(disc, image)
            release = planned["release"]
            tree: SystemTree = planned["tree"]

            # One pack at a time and oldest first, its encrypted payloads
            # straight after its plain files, so that a later pack replaces
            # what an earlier one fixed and never the other way about.
            machine = profile_machine(target)
            addons = set(profile_addons(target))
            applied: list[dict] = []
            for found in boingbag.packs_in(archives, release.key, chosen_packs or []):
                report(f"Adding {found.bag.label}", 0, None)
                entry = boingbag.plan_pack(found, tree, machine, addons)
                if found.locked:
                    entry.update(apply_locked_pack(
                        self, target, disc, tree, found,
                        use_emulator=use_emulator, progress=report,
                    ))
                applied.append(entry)

            kept: list[str] = []
            for path in KEPT_IF_PRESENT:
                if path in tree and volume_copy.entry_exists(self, target, path):
                    tree.remove(path)
                    kept.append(path)

            self._require_room(target, tree, release.label)
            had_system = volume_copy.entry_exists(self, target, STARTUP_SEQUENCE)
            result = write_system_tree(
                self, target, tree, report, message=f"Installing {release.label}"
            )
        warnings = [*tree.warnings, *result["warnings"]]
        drawers = [*result["drawers"], *self._create_system_drawers(target, warnings)]
        warnings.extend(self._workbench_readiness(target))
        for entry in applied:
            warnings.extend(entry.pop("warnings", []))
            if entry.get("locked") and not entry.get("applied"):
                warnings.append(
                    f"{entry['label']}: {entry['lockedFiles']} system files were not "
                    f"applied. {entry['reason']}"
                )
        self._persist_session(target)
        return {
            "release": release.label,
            "key": release.key,
            "disc": disc.name,
            "layers": planned["layers"],
            "missing": planned["missing"],
            "packs": applied,
            "packsUnrecognised": boingbag.unrecognised(archives, release.key),
            "written": result["written"],
            "bytes": result["bytes"],
            "drawers": drawers,
            "kept": kept,
            "over": "system" if had_system else "empty",
            "warnings": warnings,
        }

    def _require_room(self, target: ImageSession, tree: SystemTree, label: str) -> None:
        """Refuse an installation the volume has no room for, before writing."""
        try:
            capacity = self.list_directory(target, amiga_paths.ROOT).get("capacity")
        except DiskError:
            capacity = None
        free = int(capacity.get("free") or 0) if isinstance(capacity, dict) else 0
        # Every file takes a header block and rounds up to whole blocks, so the
        # total of the lengths understates what the volume will use.
        needed = tree.total_bytes + len(tree) * 1024
        if free and needed > free:
            raise DiskError(
                f"{label} needs about {needed // (1024 * 1024) + 1} MB and "
                f"{self.partition_label(target) or 'this volume'} has "
                f"{free // (1024 * 1024)} MB free."
            )


__all__ = [
    "AmigaosCdInstallMixin",
    "BATCH_BYTES",
    "BATCH_FILES",
    "KEPT_IF_PRESENT",
    "write_system_tree",
]
