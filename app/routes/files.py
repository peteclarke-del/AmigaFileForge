from __future__ import annotations

import json
import io
import tempfile
import zipfile
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file
from .effects import image_mutation, request_effect

from ..amiga_metadata import format_inf
from ..checksum import sha256_bytes
from ..archive_utils import open_single_upload_image
from ..archive_browser import (
    ArchiveError,
    MAX_ARCHIVE_BYTES,
    archive_member_editable,
    is_archive_name,
    list_archive,
    preview_archive_member_replacement,
    read_archive_member_details,
    replace_archive_member,
)
from ..disk_service import (
    DiskError,
    DiskService,
)
from ..formats import FFS_EXTENSIONS, OFS_EXTENSIONS, HFE_EXTENSIONS, HDF_EXTENSIONS, SCP_EXTENSIONS, DMS_EXTENSIONS
from ..file_editor import (
    MAX_DISASSEMBLY_FILE,
    disassemble_file_data,
    encode_editor_replacement,
    inspect_file_data,
    replace_file_bytes,
)
from ..disk_identity import analyse_directory
from ..ffs_items import delete_ffs_items, move_ffs_items
from ..metadata_lookup import (
    best_distribution_filename,
    enrich_from_distribution_filename,
    enrich_if_ambiguous,
)
from ..operations import OperationRegistry
from .common import apply_partition, optional_int, payload, protection_field
from .. import amiga_paths


def _metadata_for_directory(
    service: DiskService,
    session,
    path: str,
    source_names: list[str] | None = None,
) -> dict:
    metadata = analyse_directory(service, session, path)
    if source_names:
        enrich_from_distribution_filename(
            metadata,
            best_distribution_filename(source_names),
        )
    return enrich_if_ambiguous(metadata) if metadata["ambiguous"] else metadata


def create_files_blueprint(
    service: DiskService,
    work_dir: Path,
    operations: OperationRegistry,
) -> Blueprint:
    blueprint = Blueprint("files", __name__)

    @blueprint.get("/api/operations")
    def operation_history():
        return jsonify(operations=operations.list())

    @blueprint.delete("/api/operations")
    @request_effect("external", "clearing completed operation records")
    def clear_operation_history():
        return jsonify(removed=operations.clear_terminal())

    @blueprint.get("/api/operations/<operation_id>")
    def operation_progress(operation_id):
        return jsonify(operation=operations.get(operation_id))

    @blueprint.post("/api/operations/<operation_id>/cancel")
    @request_effect("external", "requesting operation cancellation")
    def cancel_operation(operation_id):
        return jsonify(operation=operations.cancel(operation_id))

    @blueprint.get("/api/images/<image_id>/tree")
    def tree(image_id):
        session = service.get(image_id)
        apply_partition(service, session, request.args.get("partition"))
        result = service.browse_directory(
            session,
            request.args.get("path", ""),
            optional_int(request.args.get("side")),
        )
        for entry in result.get("entries", []):
            if entry.get("type") not in {"dir", "directory", "disk"} and is_archive_name(str(entry.get("name") or "")):
                entry["archive"] = True
                entry["filetype"] = "Archive"
        return jsonify(result)

    def archive_context(image_id):
        session = service.get(image_id)
        path = request.args.get("path", "")
        if not path:
            raise ArchiveError("Choose an archive to browse.")
        apply_partition(service, session, request.args.get("partition"))
        side = optional_int(request.args.get("side"))
        metadata = service.file_metadata(session, path, side)
        if int(metadata.get("length") or 0) > MAX_ARCHIVE_BYTES:
            raise ArchiveError("That archive is too large to browse safely in memory.")
        data = service.read_file(session, path, side)
        return data, str(request.args.get("name") or amiga_paths.leaf(path))

    def archive_member_context(image_id):
        session = service.get(image_id)
        data, filename = archive_context(image_id)
        member = str(request.args.get("member") or "")
        if not member:
            raise ArchiveError("Choose an archive member to inspect.")
        content, metadata = read_archive_member_details(data, filename, member)
        digest = sha256_bytes(content)
        return session, member, content, metadata, digest

    @blueprint.get("/api/images/<image_id>/archive/tree")
    def archive_tree(image_id):
        data, filename = archive_context(image_id)
        return jsonify(list_archive(data, filename, request.args.get("member", "")))

    @blueprint.get("/api/images/<image_id>/archive/file")
    def archive_file(image_id):
        data, filename = archive_context(image_id)
        member = request.args.get("member", "")
        content, metadata = read_archive_member_details(data, filename, member)
        leaf = member.rsplit("/", 1)[-1] or "archive-member"
        if request.args.get("bundle") == "metadata" and metadata.get("metadataAvailable"):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(leaf, content)
                archive.writestr(f"{leaf}.inf", format_inf(leaf, metadata))
            stream.seek(0)
            return send_file(
                stream, mimetype="application/zip", as_attachment=True,
                download_name=f"{leaf}-with-metadata.zip",
            )
        return send_file(
            io.BytesIO(content), mimetype="application/octet-stream", as_attachment=True,
            download_name=leaf,
        )

    @blueprint.get("/api/images/<image_id>/archive/inspect")
    def archive_inspect(image_id):
        session, member, content, metadata, digest = archive_member_context(image_id)
        archive_data, filename = archive_context(image_id)
        writable = (
            archive_member_editable(archive_data, filename, member)
            and not session.hfe_read_only
            and session.kind != "dms"
        )
        return jsonify(inspect_file_data(
            content[:MAX_DISASSEMBLY_FILE], metadata, member, read_only=not writable,
            size=len(content), digest=digest,
        ) | {"archiveSha256": sha256_bytes(archive_data), "archiveEditable": writable})

    @blueprint.post("/api/images/<image_id>/archive/rebuild-preview")
    @request_effect("read-only", "proving an archive member rebuild")
    def preview_archive_rebuild(image_id):
        body = payload()
        session = service.get(image_id)
        path = str(body.get("path") or "")
        member = str(body.get("member") or "")
        if not path or not member:
            raise ArchiveError("Choose an archive member to inspect before rebuilding it.")
        apply_partition(service, session, body.get("partition"))
        side = optional_int(body.get("side"))
        filename = str(body.get("name") or amiga_paths.leaf(path))
        archive_data = service.read_file(session, path, side)
        archive_digest = sha256_bytes(archive_data)
        if archive_digest != str(body.get("archiveSha256") or ""):
            raise ArchiveError("The archive changed after the member opened. Reopen it before reviewing the rebuild.")
        original, metadata = read_archive_member_details(archive_data, filename, member)
        if sha256_bytes(original) != str(body.get("sha256") or ""):
            raise ArchiveError("The archive member changed after it opened. Reopen it before reviewing the rebuild.")
        inspection = inspect_file_data(original, metadata, member, read_only=False)
        if not inspection["editable"]:
            raise ArchiveError("This archive member cannot be encoded safely by the source editor.")
        replacement = encode_editor_replacement(
            original, str(body.get("text") or ""), bool(inspection["tokenisedBasic"]),
        )
        return jsonify(preview_archive_member_replacement(
            archive_data, filename, member, replacement,
        ))

    @blueprint.put("/api/images/<image_id>/archive/inspect")
    @image_mutation("editing a file inside an archive")
    def save_archive_inspect(image_id):
        body = payload()
        session = service.get(image_id)
        path = str(body.get("path") or "")
        member = str(body.get("member") or "")
        if not path or not member:
            raise ArchiveError("Choose an archive member to update.")
        apply_partition(service, session, body.get("partition"))
        side = optional_int(body.get("side"))
        filename = str(body.get("name") or amiga_paths.leaf(path))
        archive_data = service.read_file(session, path, side)
        archive_digest = sha256_bytes(archive_data)
        if archive_digest != str(body.get("archiveSha256") or ""):
            raise ArchiveError("The archive changed after the member opened. Reopen it before saving.")
        if session.hfe_read_only or session.kind == "dms" or not archive_member_editable(archive_data, filename, member):
            raise ArchiveError("This container cannot be rebuilt safely in the current image.")
        original, metadata = read_archive_member_details(archive_data, filename, member)
        if sha256_bytes(original) != str(body.get("sha256") or ""):
            raise ArchiveError("The archive member changed after it opened. Reopen it before saving.")
        inspection = inspect_file_data(original, metadata, member, read_only=False)
        if not inspection["editable"]:
            raise ArchiveError("This archive member cannot be encoded safely by the source editor.")
        replacement = encode_editor_replacement(
            original, str(body.get("text") or ""), bool(inspection["tokenisedBasic"]),
        )
        rebuilt = replace_archive_member(archive_data, filename, member, replacement)
        image = replace_file_bytes(service, session, path, side, rebuilt, archive_digest)
        saved, saved_metadata = read_archive_member_details(rebuilt, filename, member)
        result = inspect_file_data(saved, saved_metadata, member, read_only=False)
        result.update(archiveSha256=sha256_bytes(rebuilt), archiveEditable=True)
        return jsonify(image=image, inspection=result)

    @blueprint.get("/api/images/<image_id>/archive/disassembly")
    def archive_disassembly(image_id):
        session, member, content, metadata, digest = archive_member_context(image_id)
        try:
            origin = int(str(request.args.get("origin")), 0) if request.args.get("origin") not in (None, "") else None
            start = int(str(request.args.get("start") or "0"), 0)
            length = int(str(request.args.get("length")), 0) if request.args.get("length") not in (None, "") else None
        except ValueError as exc:
            raise ArchiveError("Origin, offset and length must be valid decimal or 0x-prefixed numbers.") from exc
        return jsonify(disassemble_file_data(
            content[:MAX_DISASSEMBLY_FILE], metadata, session, member,
            str(request.args.get("architecture") or "auto"),
            origin, start, length,
            size=len(content), digest=digest,
        ))

    @blueprint.get("/api/images/<image_id>/preview")
    def preview_image(image_id):
        return jsonify(service.preview_image_contents(service.get(image_id)))

    @blueprint.get("/api/images/<image_id>/stat")
    def stat(image_id):
        session = service.get(image_id)
        apply_partition(service, session, request.args.get("partition"))
        return jsonify(service.stat(session))

    @blueprint.get("/api/images/<image_id>/capacity")
    def capacity(image_id):
        session = service.get(image_id)
        apply_partition(service, session, request.args.get("partition"))
        return jsonify(capacity=service.capacity(session))

    @blueprint.post("/api/images/<image_id>/validate")
    @request_effect("read-only", "validating an image without changing it")
    def validate(image_id):
        session = service.get(image_id)
        apply_partition(service, session, payload().get("partition"))
        return jsonify(message=service.validate(session))

    @blueprint.post("/api/images/<image_id>/rename")
    @image_mutation("renaming an item")
    def rename(image_id):
        data = payload()
        session = service.get(image_id)
        apply_partition(service, session, data.get("partition"))
        if session.kind == "rom":
            service.rename_rom_bank(session, int(data["bank"]), data.get("title", ""))
            result = {}
        elif session.kind in {"ffs", "ofs"}:
            result = move_ffs_items(
                service,
                session,
                [{
                    "source": data["source"],
                    "destination": data["destination"],
                }],
            )
        else:
            side = optional_int(data.get("side"))
            service.mutate(
                session,
                [
                    "mv",
                    "--force" if data.get("overwrite") else "",
                    "{image}:" + data["source"],
                    data["destination"],
                ],
                side,
            )
            service.move_editor_projects(
                session,
                [{"source": data["source"], "destination": data["destination"]}],
                side,
            )
            result = {}
        return jsonify(image=service.summary(session), **result)

    @blueprint.post("/api/images/<image_id>/move")
    @image_mutation("moving items")
    def move_items(image_id):
        session = service.get(image_id)
        result = move_ffs_items(
            service,
            session,
            payload().get("items", []),
        )
        return jsonify(image=service.summary(session), **result)

    @blueprint.post("/api/images/<image_id>/move-ofs")
    @image_mutation("moving files between drawers")
    def move_ofs_items(image_id):
        data = payload()
        session = service.get(image_id)
        moved = service.move_ofs_items(
            session,
            data.get("items", []),
            optional_int(data.get("side")),
        )
        return jsonify(image=service.summary(session), moved=moved)

    @blueprint.post("/api/images/<image_id>/delete")
    @image_mutation("deleting an item")
    def delete(image_id):
        data = payload()
        session = service.get(image_id)
        items = data.get("items")
        if items is None:
            items = [{
                "path": data["path"],
                "recursive": bool(data.get("recursive")),
            }]
        if not isinstance(items, list) or not items:
            raise DiskError("Choose at least one item to delete.")
        if session.kind == "rom":
            banks = [int(item.get("bank")) for item in items]
            service.clear_rom_banks(session, banks)
            result = {"deletedItems": [{"bank": bank} for bank in banks]}
        elif session.kind in {"ffs", "ofs"}:
            result = delete_ffs_items(
                service,
                session,
                [item["path"] for item in items],
            )
        else:
            apply_partition(service, session, data.get("partition"))
            side = optional_int(data.get("side"))
            args = ["rm", "--force"]
            if any(item.get("recursive") for item in items):
                args.append("--recursive")
            # Every path is compound. The engine opens the image once and
            # deletes them together, so a partial failure cannot leave half a
            # selection removed.
            args.extend(
                "{image}:" + amiga_paths.normalise(item["path"]) for item in items
            )
            service.mutate(
                session,
                args,
                side,
            )
            service.delete_editor_projects(
                session,
                [item["path"] for item in items],
                side,
            )
            result = {
                "deletedItems": [
                    {"path": item["path"], "isDirectory": bool(item.get("recursive"))}
                    for item in items
                ]
            }
        return jsonify(image=service.summary(session), **result)

    @blueprint.post("/api/images/<image_id>/mkdir")
    @image_mutation("creating a folder")
    def mkdir(image_id):
        data = payload()
        session = service.get(image_id)
        apply_partition(service, session, data.get("partition"))
        side = optional_int(data.get("side"))
        # Every AmigaDOS volume nests drawers, so the only kinds that cannot
        # are the ones with no directory structure at all.
        if session.kind in {"rom", "dms", "kickfs"} or not service.mountable(session):
            raise DiskError(
                "This view has no directories to create one in. Open a partition "
                "or a filing-system image first."
            )
        path = str(data.get("path") or "").strip()
        if not amiga_paths.normalise(path):
            raise DiskError("Choose a valid parent drawer and folder name.")
        service.validate_leaf_name(session, amiga_paths.leaf(path))
        service.make_directory(session, path, side)
        return jsonify(image=service.summary(session))

    @blueprint.post("/api/images/<image_id>/empty-file")
    @image_mutation("creating a file")
    def create_empty_file(image_id):
        data = payload()
        session = service.get(image_id)
        apply_partition(service, session, data.get("partition"))
        side = optional_int(data.get("side"))
        if session.kind in {"rom", "dms"} or not service.mountable(session):
            raise DiskError("This view cannot contain ordinary files.")
        destination_dir = str(data.get("destination") or "$").rstrip(".")
        if session.kind == "ofs":
            destination_dir = service.validate_ofs_prefix(destination_dir)
        name = service.validate_leaf_name(session, str(data.get("name") or ""))
        existing = service.list_directory(session, destination_dir, side)["entries"]
        if any(str(row.get("name") or "").casefold() == name.casefold() for row in existing):
            raise DiskError(f"'{name}' already exists in this directory.")
        destination = name if session.kind == "kickfs" else amiga_paths.join(destination_dir, name)
        with tempfile.NamedTemporaryFile(dir=work_dir, prefix="empty-file-", delete=False) as temp:
            temp_path = Path(temp.name)
        try:
            service.put(
                session, destination, temp_path,
                protection_field(data.get("protection")),
                str(data.get("comment") or "") or None,
                str(data.get("filetype") or "") or None,
                side,
            )
        finally:
            temp_path.unlink(missing_ok=True)
        return jsonify(image=service.summary(session), path=destination)

    @blueprint.post("/api/images/<image_id>/lock")
    @image_mutation("changing file protection")
    def lock(image_id):
        data = payload()
        session = service.get(image_id)
        paths = data.get("paths")
        if paths is None:
            paths = [data["path"]]
        if not isinstance(paths, list) or not paths:
            raise DiskError("Choose at least one file to update.")
        updated = service.set_access(
            session,
            paths,
            bool(data.get("unlock")),
            optional_int(data.get("side")),
        )
        return jsonify(image=service.summary(session), paths=updated)

    @blueprint.post("/api/images/<image_id>/metadata")
    @image_mutation("changing file metadata")
    def file_metadata_update(image_id):
        data = payload()
        session = service.get(image_id)
        apply_partition(service, session, data.get("partition"))
        path = str(data.get("path") or "").strip()
        if not path:
            raise DiskError("Choose a file whose metadata should be changed.")
        metadata = service.set_file_metadata(
            session,
            path,
            str(data.get("protection") or ""),
            str(data.get("comment") or ""),
            optional_int(data.get("side")),
        )
        return jsonify(image=service.summary(session), path=path, metadata=metadata)

    @blueprint.post("/api/images/<image_id>/files")
    @image_mutation("adding a file")
    def put_file(image_id):
        upload = request.files.get("file")
        if not upload or not upload.filename:
            raise DiskError("Choose a host file to import.")
        session = service.get(image_id)
        if session.kind == "rom":
            data = upload.read()
            requested = optional_int(request.form.get("bank"))
            inserted = service.put_rom_bank(session, data, requested)
            return jsonify(image=service.summary(session), bank=inserted)
        apply_partition(service, session, request.form.get("partition"))
        name = request.form.get("targetName") or DiskService.safe_filename(upload.filename)
        name = service.validate_leaf_name(session, name)
        destination_dir = request.form.get("destination", "$").rstrip(".")
        if session.kind == "ofs":
            destination_dir = service.validate_ofs_prefix(destination_dir)
        destination = name if session.kind == "kickfs" else amiga_paths.join(destination_dir, name)
        with tempfile.NamedTemporaryFile(dir=work_dir, prefix="import-", delete=False) as temp:
            upload.save(temp)
            temp_path = Path(temp.name)
        try:
            service.put(
                session,
                destination,
                temp_path,
                protection_field(request.form.get("protection")),
                request.form.get("comment") or None,
                request.form.get("filetype"),
                optional_int(request.form.get("side")),
            )
        finally:
            temp_path.unlink(missing_ok=True)
        return jsonify(image=service.summary(session))

    @blueprint.post("/api/images/<image_id>/rom-banks/blank")
    @image_mutation("appending a blank ROM bank")
    def append_blank_rom_bank(image_id):
        session = service.get(image_id)
        if session.kind != "rom":
            raise DiskError("This image is not a ROM.")
        bank = service.put_rom_bank(
            session,
            bytes((session.rom_erase_byte,)) * session.rom_bank_size,
            len(service.list_rom_banks(session)),
        )
        return jsonify(image=service.summary(session), bank=bank)

    @blueprint.get("/api/images/<image_id>/rom-banks/<int:bank>/inspect")
    def inspect_rom_bank(image_id, bank):
        session = service.get(image_id)
        return jsonify(bank=service.inspect_rom_bank(session, bank))

    @blueprint.post("/api/images/<image_id>/rom-banks/move")
    @image_mutation("moving ROM banks")
    def move_rom_banks(image_id):
        data = payload()
        session = service.get(image_id)
        targets = service.move_rom_banks(
            session,
            [int(bank) for bank in data.get("banks", [])],
            int(data.get("targetStart")),
        )
        return jsonify(image=service.summary(session), banks=targets)

    @blueprint.post("/api/images/<image_id>/folder-import")
    @image_mutation("importing a host folder")
    def put_folder(image_id):
        uploads = request.files.getlist("files")
        try:
            target_paths = json.loads(request.form.get("targetPaths", "[]"))
            metadata = json.loads(request.form.get("metadata", "[]"))
        except json.JSONDecodeError as exc:
            raise DiskError("The folder import plan is invalid.") from exc
        if not uploads or len(uploads) != len(target_paths):
            raise DiskError("The selected files no longer match the folder import plan.")
        if not isinstance(target_paths, list) or not all(isinstance(path, str) for path in target_paths):
            raise DiskError("The folder import paths are invalid.")
        if not metadata:
            metadata = [{} for _upload in uploads]
        if len(metadata) != len(uploads) or not all(isinstance(item, dict) for item in metadata):
            raise DiskError("The folder import metadata is invalid.")
        session = service.get(image_id)
        apply_partition(service, session, request.form.get("partition"))
        temp_paths: list[Path] = []
        try:
            items = []
            for upload, target_path, file_metadata in zip(uploads, target_paths, metadata, strict=True):
                with tempfile.NamedTemporaryFile(dir=work_dir, prefix="folder-import-", delete=False) as temp:
                    upload.save(temp)
                    temp_path = Path(temp.name)
                temp_paths.append(temp_path)
                items.append({
                    "targetPath": target_path,
                    "hostPath": temp_path,
                    "metadata": file_metadata,
                })
            result = service.put_host_tree(
                session,
                request.form.get("destination", "$"),
                items,
                preserve_directories=request.form.get("mode") == "preserve",
                replace=request.form.get("replace") == "true",
                side=optional_int(request.form.get("side")),
            )
        finally:
            for temp_path in temp_paths:
                temp_path.unlink(missing_ok=True)
        return jsonify(image=service.summary(session), **result)

    @blueprint.post("/api/transfer")
    @image_mutation("copying files", target="targetImage")
    def transfer():
        data = payload()
        source = service.get(data["sourceImage"])
        target = service.get(data["targetImage"])
        # Each side of a transfer names the partition it works in, because a
        # path is only unique inside the volume that holds it.
        apply_partition(service, source, data.get("sourcePartition"))
        apply_partition(service, target, data.get("targetPartition"))
        service.copy(
            source,
            data["sourcePath"],
            target,
            data["targetPath"],
            bool(data.get("recursive")),
            optional_int(data.get("sourceSide")),
            optional_int(data.get("targetSide")),
        )
        return jsonify(image=service.summary(target))

    @blueprint.post("/api/transfer-image-to-directory")
    @image_mutation("extracting an image to FFS", target="targetImage")
    def transfer_image_to_directory():
        data = payload()
        source = service.get(data["sourceImage"])
        target = service.get(data["targetImage"])
        create_directory = data.get("createDirectory", True) is not False
        operation_id = data.get("operationId")
        with operations.tracked(
            operation_id, "Preparing image extraction", "Extraction complete"
        ) as progress:
            destination = service.extract_image_to_ffs_directory(
                source,
                target,
                data.get("targetPath", "$"),
                data.get("directoryName"),
                progress,
                create_directory=create_directory,
            )
            service.set_ffs_source_name(
                target,
                destination,
                source.distribution_name or source.name,
            )
            metadata = (
                _metadata_for_directory(
                    service,
                    target,
                    destination,
                )
                if data.get("addMenu")
                else None
            )
        return jsonify(
            image=service.summary(target),
            path=destination,
            metadata=metadata,
        )

    @blueprint.post("/api/images/<image_id>/extract-to-directory")
    @image_mutation("extracting an image")
    def extract_to_directory(image_id):
        target = service.get(image_id)
        upload = request.files.get("image")
        if not upload or not upload.filename:
            raise DiskError("Choose a supported disk or DMS archive to extract.")
        operation_id = request.form.get("operationId")
        create_directory = request.form.get("createDirectory", "yes") != "no"
        extensions = (
            OFS_EXTENSIONS | HDF_EXTENSIONS | DMS_EXTENSIONS | FFS_EXTENSIONS | HFE_EXTENSIONS | SCP_EXTENSIONS
        )
        with open_single_upload_image(upload, extensions) as image:
            source = service.create_from_stream(image.filename, image.stream)
            try:
                with operations.tracked(
                    operation_id,
                    "Preparing uploaded image extraction",
                    "Extraction complete",
                ) as progress:
                    destination = service.extract_image_to_ffs_directory(
                        source,
                        target,
                        request.form.get("targetPath", "$"),
                        request.form.get("directoryName"),
                        progress,
                        create_directory=create_directory,
                    )
                    service.set_ffs_source_name(
                        target,
                        destination,
                        best_distribution_filename(image.metadata_names),
                    )
                    metadata = (
                        _metadata_for_directory(service, target, destination)
                        if request.form.get("addMenu") == "yes"
                        else None
                    )
            finally:
                service.discard_session(source)
        return jsonify(
            image=service.summary(target),
            path=destination,
            metadata=metadata,
        )

    @blueprint.get("/api/images/<image_id>/file")
    def get_file(image_id):
        session = service.get(image_id)
        inner = request.args["path"]
        path = service.export_file(
            session,
            inner,
            optional_int(request.args.get("side")),
        )
        name = amiga_paths.leaf(inner) or "file"
        bundle = request.args.get("bundle") == "metadata"
        download_path = path
        download_name = name
        mimetype = "application/octet-stream"
        cleanup = [path]
        if bundle:
            try:
                metadata = service.file_metadata(
                    session,
                    inner,
                    optional_int(request.args.get("side")),
                )
                with tempfile.NamedTemporaryFile(
                    dir=work_dir,
                    prefix="file-export-",
                    suffix=".zip",
                    delete=False,
                ) as archive_temp:
                    archive_path = Path(archive_temp.name)
                cleanup.append(archive_path)
                inf = format_inf(inner, metadata)
                with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.write(path, name)
                    archive.writestr(f"{name}.inf", inf)
                download_path = archive_path
                download_name = f"{name}-with-metadata.zip"
                mimetype = "application/zip"
            except Exception:
                for item in cleanup:
                    item.unlink(missing_ok=True)
                raise
        response = send_file(
            download_path,
            as_attachment=True,
            download_name=download_name,
            mimetype=mimetype,
            conditional=True,
        )
        def remove_exports() -> None:
            for item in cleanup:
                item.unlink(missing_ok=True)

        response.call_on_close(remove_exports)
        return response

    return blueprint
