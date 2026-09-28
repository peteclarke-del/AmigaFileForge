"""Planning hard drives, keeping handlers and changing partition tables."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from .. import drive_layout, filesystem_handlers
from ..disk_service import DiskError, DiskService
from ..operations import OperationRegistry
from .common import payload
from .effects import image_mutation, request_effect


def create_drives_blueprint(
    service: DiskService,
    operations: OperationRegistry | None = None,
) -> Blueprint:
    operations = operations or OperationRegistry()
    blueprint = Blueprint("drives", __name__)

    @blueprint.get("/api/drive-layout/options")
    def layout_options():
        """Say what the partition editor can offer."""
        return jsonify(drive_layout.options())

    @blueprint.post("/api/drive-layout/preset")
    @request_effect("read-only", "working out a starting layout")
    def layout_preset():
        data = payload()
        size = drive_layout.parse_size(data.get("size"), what="drive size")
        rows = drive_layout.preset_rows(
            str(data.get("preset") or ""), str(data.get("filesystem") or ""), size
        )
        return jsonify(partitions=rows)

    @blueprint.post("/api/drive-layout/plan")
    @request_effect("read-only", "checking a drive layout")
    def layout_plan():
        """Work out exactly what a layout would write, and what is wrong with it."""
        data = payload()
        return jsonify(plan=drive_layout.plan(data.get("size"), data.get("partitions")))

    @blueprint.get("/api/filesystem-handlers")
    def list_handlers():
        return jsonify(
            handlers=filesystem_handlers.available(),
            folder=str(filesystem_handlers.user_directory()),
            storage=filesystem_handlers.storage(),
        )

    @blueprint.post("/api/filesystem-handlers")
    @request_effect("lifecycle", "keeping a filing-system handler for new drives")
    def keep_handler():
        """Keep a handler from an uploaded file or from an open drive."""
        if request.files.get("handler") is not None:
            upload = request.files["handler"]
            binary = upload.read(filesystem_handlers.LARGEST_HANDLER + 1)
            family = str(request.form.get("family") or "")
            if not family:
                recognised = filesystem_handlers.recognise(binary)
                if recognised is None:
                    raise DiskError(
                        "That file does not say which filing system it is for. "
                        "Choose the filing system and add it again."
                    )
                family = recognised.key
            kept = [filesystem_handlers.store(family, binary, upload.filename or "")]
        else:
            data = payload()
            session = service.get(str(data.get("imageId") or ""))
            if session.kind != "hdf":
                raise DiskError("Only a partitioned drive carries handlers to take.")
            with session.lock:
                kept = filesystem_handlers.store_from_drive(session.path)
        return jsonify(kept=kept, handlers=filesystem_handlers.available())

    @blueprint.delete("/api/filesystem-handlers/<family>")
    @request_effect("lifecycle", "forgetting a supplied filing-system handler")
    def forget_handler(family):
        filesystem_handlers.remove(family)
        return jsonify(handlers=filesystem_handlers.available())

    @blueprint.get("/api/images/<image_id>/drive-layout")
    def image_drive_layout(image_id):
        """Describe a drive's table, its unused space and the handlers it lacks."""
        session = service.get(image_id)
        return jsonify(
            image=service.summary(session),
            layout=service.drive_layout(session),
            options=drive_layout.options(),
        )

    @blueprint.post("/api/images/<image_id>/partitions")
    @image_mutation("adding a partition")
    def add_partition(image_id):
        session = service.get(image_id)
        partition = service.add_partition(session, payload())
        return jsonify(image=service.summary(session), partition=partition)

    @blueprint.patch("/api/images/<image_id>/partitions/<int:index>")
    @image_mutation("changing a partition")
    def change_partition(image_id, index):
        session = service.get(image_id)
        partition = service.change_partition(session, index, payload())
        return jsonify(image=service.summary(session), partition=partition)

    @blueprint.delete("/api/images/<image_id>/partitions/<int:index>")
    @image_mutation("removing a partition")
    def remove_partition(image_id, index):
        session = service.get(image_id)
        service.remove_partition(session, index)
        return jsonify(image=service.summary(session))

    @blueprint.post("/api/images/<image_id>/partitions/<int:index>/format")
    @image_mutation("formatting a partition")
    def format_partition(image_id, index):
        data = payload()
        session = service.get(image_id)
        partition = service.format_partition(
            session,
            index,
            str(data.get("label") or ""),
            str(data.get("filesystem") or "") or None,
        )
        return jsonify(image=service.summary(session), partition=partition)

    @blueprint.post("/api/images/<image_id>/drive-layout/extend")
    @image_mutation("making the partition table cover the whole drive")
    def extend_drive(image_id):
        """Claim the rest of a card, or make a drive image larger."""
        data = payload()
        session = service.get(image_id)
        if data.get("size") not in (None, ""):
            layout = service.resize_drive_image(session, data.get("size"))
        else:
            layout = service.extend_drive(session)
        return jsonify(image=service.summary(session), layout=layout)

    @blueprint.post("/api/images/<image_id>/drive-layout/handlers")
    @image_mutation("adding filing-system handlers to the partition table")
    def embed_handlers(image_id):
        session = service.get(image_id)
        added = service.embed_handlers(session)
        return jsonify(image=service.summary(session), added=added)

    return blueprint


__all__ = ["create_drives_blueprint"]
