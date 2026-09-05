from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, jsonify, request, send_file, send_from_directory

from ..download_archive import build_download_archive, prepared_download
from ..disk_service import DiskError, DiskService
from ..image_opening import open_image_upload
from ..hardware_profiles import hardware_catalogue, normalise_hardware_profile
from ..operations import OperationRegistry
from ..platform_contract import PlatformRuntime
from ..version import application_version
from .common import apply_partition, payload
from .effects import image_mutation, request_effect


def create_images_blueprint(
    service: DiskService,
    static_dir: Path,
    operations: OperationRegistry,
    runtime: PlatformRuntime | None = None,
) -> Blueprint:
    runtime = runtime or PlatformRuntime()
    blueprint = Blueprint("images", __name__)

    @blueprint.get("/")
    def index():
        return send_from_directory(static_dir, "index.html")

    @blueprint.get("/api/health")
    def health():
        return jsonify(
            status="ok",
            engine="amiganut",
            version=application_version(),
            platform=runtime.public_contract(),
        )

    @blueprint.get("/api/hardware-profiles")
    def list_hardware_profiles():
        return jsonify(hardware_catalogue())

    @blueprint.post("/api/images")
    @request_effect("lifecycle", "opening an image session")
    def open_image():
        image = request.files.get("image")
        if not image or not image.filename:
            raise DiskError("Choose a media image to open.")
        descriptor_file = request.files.get("descriptor")
        try:
            rom_component_names = json.loads(
                request.form.get("romComponentNames", "[]")
            )
        except json.JSONDecodeError as exc:
            raise DiskError("The ROM component list is invalid.") from exc
        if not isinstance(rom_component_names, list):
            raise DiskError("The ROM component list is invalid.")
        session = open_image_upload(
            service,
            image,
            descriptor_file if descriptor_file and descriptor_file.filename else None,
            target_hardware=request.form.get("targetHardware", "auto"),
            rom_options={
                "layout": request.form.get("romLayout", "linear"),
                "platform": request.form.get("romPlatform", "kickstart"),
                "componentNames": rom_component_names,
            },
            force_kind=request.form.get("forceKind") or None,
        )
        return jsonify(image=service.summary(session))

    @blueprint.post("/api/images/create")
    @request_effect("lifecycle", "creating an image session")
    def create_image():
        data = payload()
        session = service.create_blank(
            data.get("format", "adf"),
            data.get("title", "BLANK"),
            data.get("capacity"),
            data.get("targetHardware", "auto"),
            options=data.get("rom") if isinstance(data.get("rom"), dict) else None,
        )
        return jsonify(image=service.summary(session))

    @blueprint.get("/api/images/<image_id>")
    def image_summary(image_id):
        return jsonify(image=service.summary(service.get(image_id)))

    @blueprint.get("/api/images/<image_id>/partitions")
    def image_partitions(image_id):
        """List the partitions a hard drive's Rigid Disk Block declares.

        Each row is presented as a drawer so the pane opens into it exactly as
        it opens a directory, and carries the device name the partition mounts
        as, its filing system, its size and whether the machine boots from it.
        """
        session = service.get(image_id)
        partitions = [
            {
                "partition": index,
                "name": str(item.get("device") or item.get("name") or f"Partition {index}"),
                "type": "partition",
                "format": item.get("format"),
                "length": int(item.get("sizeBytes") or 0),
                "bootable": bool(item.get("bootable")),
                "bootPriority": item.get("bootPriority"),
                "automount": bool(item.get("automount")),
                "lowCylinder": item.get("lowCylinder"),
                "highCylinder": item.get("highCylinder"),
            }
            for index, item in enumerate(service.list_partitions(session))
        ]
        return jsonify(image=service.summary(session), partitions=partitions)

    @blueprint.get("/api/images/<image_id>/rigid-disk")
    def image_rigid_disk(image_id):
        """Return the complete decoded Rigid Disk Block for inspection."""
        return jsonify(rigidDisk=service.rigid_disk(service.get(image_id)))

    @blueprint.patch("/api/images/<image_id>")
    @image_mutation("renaming the image")
    def rename_image(image_id):
        data = payload()
        session = service.get(image_id)
        service.rename_session(session, data.get("name", ""))
        return jsonify(image=service.summary(session))

    @blueprint.patch("/api/images/<image_id>/kickfs")
    @image_mutation("changing the Kickstart ROM configuration")
    def configure_kickfs(image_id):
        data = payload()
        session = service.get(image_id)
        service.set_kickfs_properties(
            session,
            title=data.get("title", ""),
            version=data.get("version", 1),
            copyright_text=data.get("copyright", ""),
        )
        return jsonify(image=service.summary(session))

    @blueprint.patch("/api/images/<image_id>/rom-layout")
    @image_mutation("changing the ROM layout")
    def configure_rom_layout(image_id):
        data = payload()
        session = service.get(image_id)
        service.configure_rom(
            session,
            bank_size=int(data.get("bankSize", session.rom_bank_size)),
            erase_byte=int(str(data.get("eraseByte", session.rom_erase_byte)), 0),
            platform=str(data.get("platform") or session.rom_platform),
            layout=str(data.get("layout") or session.rom_layout),
        )
        return jsonify(image=service.summary(session))

    @blueprint.patch("/api/images/<image_id>/hardware-profile")
    @image_mutation("changing the hardware profile")
    def set_hardware_profile(image_id):
        data = payload()
        session = service.get(image_id)
        allowed = {
            "name", "machine", "filingSystem", "handlerBuild", "accelerated",
            "page", "menuType", "notes", "targetHardware", "catalogMachine",
            "emulator", "debugger", "emulatorRam", "emulatorBoot", "addons",
        }
        profile = {
            key: value
            for key, value in data.items()
            if key in allowed and isinstance(value, (str, bool, int, float, list))
        }
        try:
            profile = normalise_hardware_profile(profile)
        except ValueError as exc:
            raise DiskError(str(exc)) from exc
        for key in ("emulator", "debugger", "emulatorRam", "emulatorBoot"):
            if key in profile:
                profile[key] = str(profile[key]).strip()[:2048]
        session.hardware_profile = profile
        if session.kind in {"ffs", "ofs"} and data.get("targetHardware"):
            session.target_hardware = service._target_hardware(str(data["targetHardware"]))
        service._persist_session(session)
        return jsonify(image=service.summary(session))

    @blueprint.get("/api/images/<image_id>/checkpoints")
    def image_checkpoints(image_id):
        session = service.get(image_id)
        return jsonify(
            image=service.summary(session),
            checkpoints=service.list_checkpoints(session),
        )

    @blueprint.post("/api/images/<image_id>/checkpoints")
    @request_effect("lifecycle", "creating a named checkpoint")
    def create_image_checkpoint(image_id):
        data = payload()
        session = service.get(image_id)
        checkpoint = service.create_checkpoint(session, data.get("name", ""))
        return jsonify(
            image=service.summary(session),
            checkpoint=checkpoint,
            checkpoints=service.list_checkpoints(session),
        )

    @blueprint.post("/api/images/<image_id>/checkpoints/<checkpoint_id>/restore")
    @request_effect("lifecycle", "restoring an explicitly managed checkpoint")
    def restore_image_checkpoint(image_id, checkpoint_id):
        session = service.get(image_id)
        service.begin_automatic_checkpoint(session, "restoring a named checkpoint")
        checkpoint = service.restore_checkpoint(session, checkpoint_id)
        return jsonify(
            image=service.summary(session),
            checkpoint=checkpoint,
            checkpoints=service.list_checkpoints(session),
        )

    @blueprint.delete("/api/images/<image_id>/checkpoints/<checkpoint_id>")
    @request_effect("lifecycle", "deleting checkpoint metadata")
    def delete_image_checkpoint(image_id, checkpoint_id):
        session = service.get(image_id)
        service.delete_checkpoint(session, checkpoint_id)
        return jsonify(
            image=service.summary(session),
            checkpoints=service.list_checkpoints(session),
        )

    @blueprint.post("/api/images/<image_id>/undo")
    @request_effect("lifecycle", "restoring the automatic undo checkpoint")
    def undo_image_change(image_id):
        session = service.get(image_id)
        checkpoint = service.undo_last_change(session)
        return jsonify(
            image=service.summary(session),
            checkpoint=checkpoint,
            checkpoints=service.list_checkpoints(session),
        )

    @blueprint.get("/api/images/recoverable")
    def recoverable_images():
        return jsonify(images=service.recoverable_sessions())

    @blueprint.delete("/api/images/recoverable")
    @request_effect("lifecycle", "clearing owned recovery sessions")
    def clear_recoverable_images():
        data = request.get_json(silent=True) or {}
        image_ids = data.get("imageIds")
        if image_ids is not None and not isinstance(image_ids, list):
            raise DiskError("Choose the sessions to clear.")
        removed = service.clear_recoverable_sessions(image_ids)
        return jsonify(removed=removed)

    @blueprint.delete("/api/images/<image_id>")
    @request_effect("lifecycle", "discarding an image session")
    def discard_image(image_id):
        service.discard_session(service.get(image_id))
        return ("", 204)

    @blueprint.get("/api/images/<image_id>/download")
    def download_image(image_id):
        session = service.get(image_id)
        archive_path, archive_name = prepared_download(session)
        return send_file(
            archive_path,
            mimetype="application/zip",
            as_attachment=True,
            download_name=archive_name,
            conditional=True,
        )

    @blueprint.post("/api/images/<image_id>/download/prepare")
    @image_mutation("finalising the image for download")
    def prepare_image_download(image_id):
        data = payload()
        operation_id = data.get("operationId")
        session = service.get(image_id)
        with operations.tracked(
            operation_id,
            "Preparing image download",
            "The complete ZIP is ready to download",
        ) as progress:
            with session.lock:
                build_download_archive(service, session, progress)
                service.mark_saved(session)
            return jsonify(image=service.summary(session), ready=True)

    @blueprint.post("/api/images/<image_id>/convert")
    @request_effect("lifecycle", "creating a converted image session")
    def convert_image(image_id):
        data = payload()
        converted, files = service.convert_dms(
            service.get(image_id),
            data.get("format", "adf"),
        )
        return jsonify(image=service.summary(converted), files=files)

    @blueprint.get("/api/images/<image_id>/export/formats")
    def image_export_formats(image_id):
        return jsonify(formats=service.export_formats(service.get(image_id)))

    @blueprint.get("/api/images/<image_id>/export")
    def export_image(image_id):
        session = service.get(image_id)
        target_format = request.args.get("format", "native")
        output, download_name = service.export_image(session, target_format)
        return send_file(
            output,
            mimetype="application/octet-stream",
            as_attachment=True,
            download_name=download_name,
            conditional=False,
        )

    @blueprint.post("/api/images/<image_id>/compact")
    @image_mutation("compacting the filesystem")
    def compact(image_id):
        data = payload()
        session = service.get(image_id)
        apply_partition(service, session, data.get("partition"))
        service.compact(session, data.get("order"))
        return jsonify(
            image=service.summary(session),
            message="Free space compacted successfully",
        )

    return blueprint
