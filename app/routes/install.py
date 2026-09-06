"""Routes for turning a floppy into something a hard drive can run.

Three modes reach an image from here and each declares what it does to one.
Staging touches no image at all, so it is read-only as far as the undo history
is concerned; installing a staged title, installing WHDLoad and placing a
slave all change a volume and are declared as mutations, which is what gets
them an undo checkpoint before they run. Booting the emulator changes nothing
this application owns, so it is external.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

from .. import whdload
from ..disk_service import DiskError, DiskService
from ..install_service import DEFAULT_INSTALL_PARENT, DEFAULT_WHDLOAD_PARENT
from ..lha import is_lha_bytes
from ..operations import OperationRegistry
from .common import apply_partition, payload
from .effects import image_mutation, request_effect


#: A slave is a few kilobytes and its archive not much more. A ceiling this
#: far above either still refuses a whole disc image sent by mistake.
SLAVE_UPLOAD_LIMIT = 4 * 1024 * 1024

#: The same ceiling the downloader applies, so an archive supplied by hand and
#: one fetched from the author's site are held to one rule.
ARCHIVE_UPLOAD_LIMIT = whdload.DOWNLOAD_LIMIT


def create_install_blueprint(service: DiskService, operations: OperationRegistry) -> Blueprint:
    blueprint = Blueprint("install", __name__)

    def uploaded(field: str, limit: int) -> tuple[str, bytes]:
        upload = request.files.get(field)
        if upload is None or not upload.filename:
            raise DiskError("No file was supplied.")
        data = upload.read(limit + 1)
        if len(data) > limit:
            raise DiskError(f"{upload.filename} is larger than the {limit // (1024 * 1024)} MB limit.")
        return Path(upload.filename).name, data

    # ------------------------------------------------------------------
    # Staging
    # ------------------------------------------------------------------

    @blueprint.get("/api/install/staged")
    def list_staged():
        return jsonify(titles=service.staged_titles(), root=str(service.staging_root()))

    @blueprint.post("/api/install/stage")
    @request_effect("external", "extracting a disc into the staging area")
    def stage():
        data = payload()
        source = service.get(data["sourceImage"])
        apply_partition(service, source, data.get("sourcePartition"))
        title = str(data.get("title") or "").strip()
        with operations.tracked(
            data.get("operationId"),
            f"Staging {title or source.name}",
            "Disc staged",
        ) as progress:
            staged = service.stage_disk(
                source,
                title or source.name,
                disc_label=str(data.get("discLabel") or "").strip() or None,
                progress=progress,
            )
        return jsonify(staged=staged)

    @blueprint.delete("/api/install/staged/<slug>")
    @request_effect("lifecycle", "discarding a staged title")
    def discard_staged(slug):
        service.discard_staged_title(slug)
        return jsonify(titles=service.staged_titles())

    @blueprint.post("/api/images/<image_id>/install/staged")
    @image_mutation("installing a staged title")
    def install_staged(image_id):
        data = payload()
        session = service.get(image_id)
        apply_partition(service, session, data.get("partition"))
        with operations.tracked(
            data.get("operationId"),
            "Installing a staged title",
            "Staged title installed",
        ) as progress:
            result = service.install_staged_title(
                session,
                str(data["slug"]),
                parent=str(data.get("parent", DEFAULT_INSTALL_PARENT)),
                drawer=str(data.get("drawer") or "") or None,
                progress=progress,
            )
        if data.get("discard"):
            service.discard_staged_title(str(data["slug"]))
        return jsonify(image=service.summary(session), **result)

    # ------------------------------------------------------------------
    # WHDLoad
    # ------------------------------------------------------------------

    @blueprint.get("/api/images/<image_id>/install/whdload")
    def whdload_state(image_id):
        session = service.get(image_id)
        apply_partition(service, session, request.args.get("partition"))
        return jsonify(
            whdload=service.whdload_status(session),
            defaultParent=DEFAULT_WHDLOAD_PARENT,
        )

    @blueprint.post("/api/images/<image_id>/install/whdload")
    @image_mutation("installing WHDLoad")
    def install_whdload(image_id):
        """Install WHDLoad, from the author's site or from a supplied archive.

        The upload path is not a convenience. Somebody working offline, or
        behind a network that will not reach whdload.de, still needs the
        install to be possible, and the archive they already have is the same
        archive the download would have fetched.
        """
        session = service.get(image_id)
        if request.files.get("archive") is not None:
            name, data = uploaded("archive", ARCHIVE_UPLOAD_LIMIT)
            if not is_lha_bytes(data):
                raise DiskError(f"{name} is not an LHA archive. WHDLoad is published as WHDLoad_usr.lha.")
            apply_partition(service, session, request.form.get("partition"))
            source, url = f"the supplied {name}", ""
            keep = request.form.get("keepPreferences", "true") != "false"
            operation_id = request.form.get("operationId")
        else:
            data = None
            body = payload()
            apply_partition(service, session, body.get("partition"))
            source = url = ""
            keep = bool(body.get("keepPreferences", True))
            operation_id = body.get("operationId")

        with operations.tracked(operation_id, "Installing WHDLoad", "WHDLoad installed") as progress:
            if data is None:
                progress("Fetching WHDLoad", 0, None)
                release = whdload.download()
                data, source, url = release.archive_bytes, release.source, release.url
            result = service.install_whdload(
                session, data, source=source, url=url, keep_preferences=keep, progress=progress
            )
        return jsonify(image=service.summary(session), whdload=result)

    @blueprint.post("/api/images/<image_id>/install/whdload/slave")
    @image_mutation("adding a WHDLoad slave")
    def add_slave(image_id):
        """Place a slave the operator supplied.

        There is no download here on purpose: slaves are not published in any
        form this application can fetch, and offering a button that always
        failed would be worse than not offering one.
        """
        session = service.get(image_id)
        apply_partition(service, session, request.form.get("partition"))
        name, data = uploaded("slave", SLAVE_UPLOAD_LIMIT)
        result = service.install_whdload_slave(
            session, str(request.form.get("destination") or ""), data, name
        )
        return jsonify(image=service.summary(session), slave=result)

    return blueprint
