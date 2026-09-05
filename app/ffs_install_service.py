from __future__ import annotations

from typing import Callable

from .errors import DiskError
from .image_session import ImageSession
from .amiganut_internals import file_copy_item, write_copy_item
from . import amiga_paths


class FFSInstallMixin:
    """Audit and repair software trees installed into FFS hard disks."""

    def carry_boot_option(
        self, source: ImageSession, target: ImageSession, destination: str
    ) -> int | None:
        """Give the destination volume the source disk's bootability.

        Installing a disk's files without its boot block leaves an image the
        machine will not start from: Kickstart reads block 0, finds no boot
        code, and asks for another disk. The boot block is what makes the
        difference, so it is carried across with the files.

        Only the root is eligible. A boot block runs ``S:Startup-Sequence``
        relative to the volume root, so setting it after extracting into a
        child drawer would point the machine at a file that is not there and
        turn a working image into one that fails on boot. Software installed
        into its own drawer is reached through the launcher instead, which
        makes that drawer current first.

        Returns the option carried across, or None when there was nothing to
        carry. A failure to set it is reported as a warning rather than raised:
        the files are already installed and are still usable by hand.
        """
        if target.kind not in {"ffs", "ofs"}:
            return None
        if amiga_paths.normalise(destination):
            return None
        try:
            reported = str(self._run(["opt", str(source.path)])).strip()
        except DiskError:
            return None
        digits = reported.split(" ", 1)[0]
        if not digits.isdigit():
            return None
        option = int(digits)
        if option == 0:
            return None
        try:
            self._run(["opt", str(target.path), str(option)])
        except DiskError as exc:
            self._append_warning(
                target,
                "The files were installed, but the source disc's boot option "
                f"could not be set on the destination: {exc}",
            )
            return None
        self._mark_mutated(target)
        return option

    @staticmethod
    def _ffs_directory_items(mount, directory: str, file_item) -> list[dict]:
        pending = [directory]
        items: list[dict] = []
        while pending:
            parent = pending.pop()
            for entry in mount.iter_entries(parent):
                path = str(entry.path)
                if entry.is_dir:
                    pending.append(path)
                    continue
                item = file_item(mount, path, path)
                item["sourceName"] = (
                    path[len(directory) + 1 :]
                    if path.startswith(f"{directory}{amiga_paths.SEPARATOR}")
                    else amiga_paths.leaf(path)
                )
                items.append(item)
        return items

    def _repair_copied_ffs_loaders(
        self, target: ImageSession, directory: str
    ) -> tuple[list[str], list[str]]:
        with self.ffs_mount(target) as mount:
            items = self._ffs_directory_items(mount, directory, file_copy_item)
            repairs, warnings = self._repair_ffs_loader_items(items)
            for item in items:
                if item.get("loaderRepairs"):
                    write_copy_item(mount, str(item["dst"]), item, True)
        if repairs:
            target.dirty = True
        return repairs, warnings

    @staticmethod
    def _ffs_installation_roots(
        directory_files: dict[str, list[str]], source_names: dict[str, str]
    ) -> list[str]:
        loader_names = {"Startup-Sequence", "BOOT", "GO", "MENU", "LOADER", "START"}
        menu_markers = {"GAMDATA", "GAMINDX", "PUBDATA", "PUBINDX", "WBMENU"}
        candidates = {path for path in source_names if path in directory_files}
        for path, names in directory_files.items():
            upper = {name.upper() for name in names}
            if upper & loader_names and not menu_markers.issubset(upper):
                candidates.add(path)
        roots: list[str] = []
        for candidate in sorted(candidates, key=lambda item: (item.count("."), item.casefold())):
            if any(candidate.casefold().startswith(f"{root.casefold()}.") for root in roots):
                continue
            roots.append(candidate)
        return roots

    def audit_ffs_installations(
        self,
        session: ImageSession,
        root: str = "$",
        progress: Callable[[str, int | None, int | None], None] | None = None,
    ) -> dict:
        if session.kind not in {"ffs", "ofs"} or not self.summary(session)["hardDisk"]:
            raise DiskError("Installed disk auditing is available only for FFS HDD images.")
        report = progress or (lambda _message, _current=None, _total=None: None)
        with self.ffs_mount(session) as mount:
            if not mount.exists(root):
                raise DiskError(f"Path not found: {root}")
            directory_files: dict[str, list[str]] = {}
            pending = [root]
            while pending:
                directory = pending.pop()
                entries = list(mount.iter_entries(directory))
                directory_files[directory] = [str(entry.name) for entry in entries if not entry.is_dir]
                pending.extend(str(entry.path) for entry in entries if entry.is_dir)
            source_names = {
                path: name for path, name in session.ffs_source_names.items()
                if path == root or path.startswith(f"{root}{amiga_paths.SEPARATOR}")
            }
            roots = self._ffs_installation_roots(directory_files, source_names)
            findings = []
            for offset, directory in enumerate(roots):
                report(f"Checking installed software in {directory}", offset, len(roots))
                files = self._ffs_directory_items(mount, directory, file_copy_item)
                proposed = [dict(item) for item in files]
                repairs, warnings = self._repair_ffs_loader_items(proposed)
                findings.append({
                    "path": directory,
                    "source": source_names.get(directory, ""),
                    "fileCount": len(files),
                    "repairs": repairs,
                    "warnings": warnings,
                    "status": "repairable" if repairs else "warning" if warnings else "clean",
                })
            report("Installed software audit complete", len(roots), len(roots))
        return {
            "root": root,
            "directories": findings,
            "checked": len(findings),
            "repairable": sum(bool(item["repairs"]) for item in findings),
            "warnings": sum(bool(item["warnings"]) for item in findings),
        }

    def repair_ffs_installations(
        self,
        session: ImageSession,
        directories: list[str],
        progress: Callable[[str, int | None, int | None], None] | None = None,
    ) -> dict:
        if session.kind not in {"ffs", "ofs"} or not self.summary(session)["hardDisk"]:
            raise DiskError("Installed disk repair is available only for FFS HDD images.")
        unique = list(dict.fromkeys(str(path) for path in directories if str(path)))
        if not unique:
            raise DiskError("Choose at least one repairable installed disk directory.")
        current = self.audit_ffs_installations(session)
        available = {item["path"]: item for item in current["directories"] if item["repairs"]}
        unknown = [path for path in unique if path not in available]
        if unknown:
            raise DiskError(
                "The audit result is stale or no deterministic repair remains for: " + ", ".join(unknown)
            )
        report = progress or (lambda _message, _current=None, _total=None: None)
        repaired = []
        with self.ffs_mount(session) as mount:
            for offset, directory in enumerate(unique):
                report(f"Repairing installed software in {directory}", offset, len(unique))
                items = self._ffs_directory_items(mount, directory, file_copy_item)
                repairs, warnings = self._repair_ffs_loader_items(items)
                for item in items:
                    if item.get("loaderRepairs"):
                        write_copy_item(mount, str(item["dst"]), item, True)
                for repair in repairs:
                    self._append_warning(session, f"{directory}: FFS compatibility change made: {repair}.")
                for warning in warnings:
                    self._append_warning(session, f"{directory}: {warning}")
                repaired.append({"path": directory, "repairs": repairs, "warnings": warnings})
        session.dirty = True
        self._persist_session(session)
        report("Installed software repair complete", len(unique), len(unique))
        return {"repaired": repaired, "count": len(repaired)}
