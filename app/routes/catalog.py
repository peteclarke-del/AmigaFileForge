from __future__ import annotations

import io
import json
import re
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from flask import Blueprint, jsonify, request

from ..amiga_metadata import amiga_zip_metadata
from ..archive_utils import validated_zip_members
from ..catalog_service import CatalogueService, archive_members
from ..disk_service import DiskError, DiskService
from ..formats import OFS_EXTENSIONS, HFE_EXTENSIONS, SCP_EXTENSIONS, DMS_EXTENSIONS
from ..filename_policy import session_name_policy
from ..disk_identity import analyse_directory
from ..metadata_lookup import enrich_if_ambiguous
from .common import payload
from .effects import image_mutation, request_effect
from .. import amiga_paths


DISK_EXTENSIONS = OFS_EXTENSIONS | HFE_EXTENSIONS | SCP_EXTENSIONS | DMS_EXTENSIONS


def _catalogue_identities(value: object) -> set[str]:
    """Return conservative comparable names for a catalogue or installed item."""
    text = Path(str(value or "")).stem.strip()
    if not text:
        return set()
    without_attribution = re.sub(r"\s+\([^()]*(?:\)|$)", "", text).strip()
    return {
        identity
        for candidate in {text, without_attribution}
        if (identity := re.sub(r"[^a-z0-9]+", " ", candidate.casefold()).strip())
    }


def _available_ffs_directory_name(
    service: DiskService,
    target,
    parent: str,
    preferred: str,
) -> str:
    """Allocate a legal, unused FFS child name for an online import."""
    policy = session_name_policy(target)
    used = {
        str(entry.get("name") or "").casefold()
        for entry in service.list_directory(target, parent, None)["entries"]
    }
    return policy.allocate(preferred, used)


def _disk_members(filename: str, content: bytes) -> list[tuple[str, bytes]]:
    return [
        (name, data) for name, data in archive_members(filename, content)
        if Path(name).suffix.lower() in DISK_EXTENSIONS
    ]


def _preferred_disk_members(filename: str, content: bytes) -> list[tuple[str, bytes]]:
    """Keep every disk in the best available format, not duplicate dms variants."""
    members = _disk_members(filename, content)
    if not members:
        return []
    priority = {".adf": 0, ".adz": 1, ".hfe": 2, ".scp": 3, ".dms": 4}
    best = min(priority.get(Path(name).suffix.lower(), 99) for name, _data in members)
    return [
        (name, data)
        for name, data in members
        if priority.get(Path(name).suffix.lower(), 99) == best
    ]


def _copy_disk_files(service: DiskService, source, target, target_path, target_side):
    sides = [0, 2] if service.is_two_volume_image(source) else [None]
    copied = 0
    for source_side in sides:
        rows = service.list_ofs_catalogue_files(source, source_side)
        preserve_prefixes = any(row["prefix"] not in amiga_paths.ROOT_TOKENS for row in rows)
        for row in rows:
            name = str(row["name"])
            destination_prefix = row["prefix"] if preserve_prefixes else target_path
            destination = amiga_paths.join(destination_prefix, name)
            service.copy(source, row["path"], target, destination, False, source_side, target_side)
            copied += 1
    if not copied:
        raise DiskError("The downloaded disk image is empty, so nothing was installed.")
    # A disk installed without its boot block cannot start itself, which is
    # the difference between the files being present and the title running.
    service.carry_boot_option(source, target, target_path)
    return copied


def _install_amigaos_package(service: DiskService, target, target_path: str, content: bytes) -> int:
    if target.kind not in {"ffs", "ofs"}:
        raise DiskError("AmigaOS packages can only be installed into an FFS or AmigaOS image.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise DiskError("The downloaded AmigaOS package is not a valid ZIP file.") from exc
    installed = 0
    made = set()
    try:
        for info in validated_zip_members(archive):
            path = PurePosixPath(info.filename)
            if not path.parts or path.parts[0].casefold() == "riscpkg" or ".." in path.parts:
                continue
            parts = [part for part in path.parts if part not in {"", "."}]
            if info.is_dir() or not parts:
                continue
            parent = target_path
            for part in parts[:-1]:
                parent = amiga_paths.join(parent, part)
                if parent.casefold() not in made:
                    service.mutate(target, ["mkdir", "-p", "{image}:" + parent])
                    made.add(parent.casefold())
            destination = amiga_paths.join(parent, parts[-1])
            # AmigaDOS stores no load or execution address, so the only
            # metadata a ZIP can carry across is the protection long, which an
            # Amiga-written archive keeps in its external attributes.
            metadata = amiga_zip_metadata(info) or {}
            protection = hex(metadata["protection"]) if "protection" in metadata else None
            with tempfile.NamedTemporaryFile(dir=service.work_dir, delete=False) as temporary:
                temporary.write(archive.read(info)); host_path = Path(temporary.name)
            try:
                service.put(target, destination, host_path, protection)
            finally:
                host_path.unlink(missing_ok=True)
            installed += 1
    finally:
        archive.close()
    if not installed:
        raise DiskError("The package did not contain any installable AmigaOS files.")
    return installed


def create_catalog_blueprint(service: DiskService, work_dir: Path) -> Blueprint:
    catalogue = CatalogueService(work_dir)
    blueprint = Blueprint("catalog", __name__)

    @blueprint.get("/api/catalog/sources")
    def sources():
        return jsonify(sources=catalogue.sources())

    @blueprint.put("/api/catalog/sources")
    @request_effect("external", "saving Online Library source configuration")
    def save_sources():
        return jsonify(sources=catalogue.save_sources(payload().get("sources", [])))

    @blueprint.get("/api/images/<image_id>/catalog/search")
    def search(image_id):
        session = service.get(image_id)
        machine = str(request.args.get("machine") or "all")
        selected_sources = {item for item in request.args.get("sources", "").split(",") if item} or None
        cursor_value = request.args.get("cursor")
        cursors = None
        if cursor_value:
            try:
                decoded = json.loads(cursor_value)
                cursors = {
                    str(source_id): max(0, int(offset))
                    for source_id, offset in decoded.items()
                } if isinstance(decoded, dict) else None
            except (TypeError, ValueError, json.JSONDecodeError):
                raise DiskError("The Online Library continuation cursor is invalid.")
        rows, failures, continuation = catalogue.search_page(
            str(request.args.get("q") or ""), machine, selected_sources, cursors
        )
        installed = set()
        try:
            listing = service.list_directory(
                session, str(request.args.get("path") or "")
            )
            for entry in listing["entries"]:
                installed.update(_catalogue_identities(entry["name"]))
        except DiskError:
            pass
        for name in session.ffs_source_names.values():
            installed.update(_catalogue_identities(name))
        for row in rows:
            candidates = _catalogue_identities(row["title"])
            candidates.update(_catalogue_identities(Path(str(row.get("pageUrl") or "")).stem))
            row["installed"] = bool(candidates & installed)
        available = len(rows)
        if request.args.get("scope") == "missing":
            rows = [row for row in rows if not row["installed"]]
        return jsonify(
            items=rows,
            failures=failures,
            continuation=continuation,
            available=available,
            hiddenInstalled=available - len(rows),
        )

    @blueprint.post("/api/images/<image_id>/catalog/install")
    @image_mutation("installing software from the Online Library")
    def install(image_id):
        data = payload(); target = service.get(image_id)
        item_ids = [str(item) for item in data.get("itemIds", [])]
        if not item_ids or len(item_ids) > 100:
            raise DiskError("Choose between 1 and 100 online catalogue items.")
        target_path = str(data.get("path") or "")
        target_side = data.get("side"); target_side = int(target_side) if target_side is not None else None
        identify = bool(data.get("identify", True))
        results = []
        for offset, item_id in enumerate(item_ids):
            source = None
            try:
                filename, content, item = catalogue.download(
                    item_id,
                    "ffs" if target.kind in {"ffs", "ofs"} else "ofs",
                )
                if item["artifactType"] == "amigaos-package":
                    count = _install_amigaos_package(service, target, target_path, content)
                    results.append({"id": item_id, "title": item["title"], "installed": count, "metadata": None})
                    continue
                members = _preferred_disk_members(filename, content)
                if not members:
                    raise DiskError("No supported ADF, ADZ, HFE or DMS image was found in the download.")
                for member_name, member_data in members:
                    source = service.create_from_stream(Path(member_name).name, io.BytesIO(member_data))
                    if target.kind in {"ffs", "ofs", "hdf"}:
                        create_dir = bool(data.get("createDirectory", False))
                        directory = str(data.get("directoryName") or "").strip()
                        if not directory:
                            directory = session_name_policy(target).normalise(
                                item["title"], "ONLINE"
                            )
                        if create_dir:
                            directory = _available_ffs_directory_name(
                                service, target, target_path, directory
                            )
                        destination = service.extract_image_to_ffs_directory(source, target, target_path, directory, create_directory=create_dir)
                        metadata = analyse_directory(service, target, destination) if identify else None
                        if metadata:
                            metadata["title"] = str(item.get("title") or metadata["title"])
                            metadata["publisher"] = str(item.get("publisher") or metadata["publisher"])
                            metadata.setdefault("sources", []).append(item.get("sourceName", "Online Library"))
                            metadata.setdefault("evidence", []).append(
                                "Title and publisher loaded from the selected online catalogue record."
                            )
                        if metadata and metadata.get("ambiguous"):
                            metadata = enrich_if_ambiguous(metadata)
                        results.append({"id": item_id, "title": item["title"], "installed": 1, "path": destination, "metadata": metadata})
                    else:
                        if service.replace_blank_ofs_image(
                            target,
                            source,
                            member_name,
                            target_path=target_path,
                        ):
                            count = len(service.list_ofs_catalogue_files(target, target_side))
                        else:
                            count = _copy_disk_files(service, source, target, target_path, target_side)
                        results.append({"id": item_id, "title": item["title"], "installed": count, "metadata": None})
                    service.discard_session(source); source = None
            except DiskError as exc:
                results.append({"id": item_id, "title": catalogue.item(item_id).get("title", item_id), "error": str(exc)})
            finally:
                if source:
                    service.discard_session(source)
        if not any(not result.get("error") for result in results):
            raise DiskError(results[0]["error"])
        return jsonify(image=service.summary(target), items=results)

    return blueprint
