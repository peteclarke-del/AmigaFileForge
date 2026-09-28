"""Routes for turning a floppy into something a hard drive can run.

Four modes reach an image from here and each declares what it does to one.
Staging, installing a staged title, installing Workbench, installing WHDLoad
and placing a slave all write into a volume and are declared as mutations,
which is what gets them an undo checkpoint before they run. Staging is a
mutation because it now writes onto the drive being built rather than into a
directory on this machine, which is what lets the install be finished in an
emulator or on the real hardware. Booting the emulator changes nothing this
application owns, so it is external.
"""

from __future__ import annotations

from pathlib import Path

from flask import Blueprint, jsonify, request

from .. import boingbag, whdload
from ..disk_service import DiskError, DiskService
from ..install_service import (
    DEFAULT_INSTALL_PARENT,
    DEFAULT_STAGING_PARENT,
    DEFAULT_WHDLOAD_PARENT,
)
from ..lha import is_lha_bytes
from ..amigaos_cd import REQUIRED_PROCESSOR, describe_releases
from ..workbench_install import describe_roles
from ..operations import OperationRegistry
from .common import apply_partition, payload
from .effects import image_mutation, request_effect


#: A slave is a few kilobytes and its archive not much more. A ceiling this
#: far above either still refuses a whole disc image sent by mistake.
SLAVE_UPLOAD_LIMIT = 4 * 1024 * 1024

#: The same ceiling the downloader applies, so an archive supplied by hand and
#: one fetched from the author's site are held to one rule.
ARCHIVE_UPLOAD_LIMIT = whdload.DOWNLOAD_LIMIT

#: The largest update pack accepted. The collection that carries BoingBags 1
#: to 4 together is fourteen megabytes, so this leaves a wide margin.
PACK_UPLOAD_LIMIT = 64 * 1024 * 1024


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

    @blueprint.get("/api/images/<image_id>/install/staged")
    def list_staged(image_id):
        """What is waiting on this drive.

        Staging writes onto the target volume, so the list is a property of an
        image and is read back off it. A drive built elsewhere still reports
        what is sitting in its staging drawer.
        """
        session = service.get(image_id)
        apply_partition(service, session, request.args.get("partition"))
        parent = str(request.args.get("parent") or "")
        return jsonify(
            titles=service.staged_titles(session, parent=parent),
            root=service.staging_parent(parent),
            defaultParent=DEFAULT_STAGING_PARENT,
        )

    @blueprint.post("/api/images/<image_id>/install/stage")
    @image_mutation("staging a disc onto a drive")
    def stage(image_id):
        """Extract one disc into a drawer on the drive it is destined for."""
        data = payload()
        session = service.get(image_id)
        apply_partition(service, session, data.get("partition"))
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
                session,
                title or source.name,
                parent=str(data.get("stagingParent") or ""),
                disc_label=str(data.get("discLabel") or "").strip() or None,
                progress=progress,
            )
        return jsonify(image=service.summary(session), staged=staged)

    @blueprint.post("/api/images/<image_id>/install/staged/discard")
    @image_mutation("discarding a staged title")
    def discard_staged(image_id):
        data = payload()
        session = service.get(image_id)
        apply_partition(service, session, data.get("partition"))
        parent = str(data.get("stagingParent") or "")
        service.discard_staged_title(session, str(data["name"]), parent=parent)
        return jsonify(
            image=service.summary(session),
            titles=service.staged_titles(session, parent=parent),
        )

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
                str(data["name"]),
                parent=str(data.get("parent", DEFAULT_INSTALL_PARENT)),
                staging=str(data.get("stagingParent") or ""),
                drawer=str(data.get("drawer") or "") or None,
                progress=progress,
            )
        return jsonify(image=service.summary(session), **result)

    # ------------------------------------------------------------------
    # Workbench
    # ------------------------------------------------------------------

    @blueprint.get("/api/install/workbench/disks")
    def workbench_disks():
        """The disk set an install wants, so the interface can ask for it."""
        return jsonify(roles=describe_roles())

    @blueprint.post("/api/images/<image_id>/install/workbench/survey")
    @request_effect("read-only", "identifying Workbench install discs")
    def survey_workbench(image_id):
        """Identify a pile of opened discs and propose a set to install from.

        This changes nothing: it reads volume names out of images that are
        already open and says what it found, so the operator can correct the
        choice before a drive is written to.
        """
        data = payload()
        session = service.get(image_id)
        apply_partition(service, session, data.get("partition"))
        discs = [service.get(str(identifier)) for identifier in data.get("discs") or []]
        if not discs:
            raise DiskError("Choose the Workbench floppy images to install from.")
        return jsonify(
            survey=service.survey_workbench_discs(
                discs, version=str(data.get("version") or "")
            ),
        )

    @blueprint.post("/api/images/<image_id>/install/workbench")
    @image_mutation("installing Workbench")
    def install_workbench(image_id):
        """Copy the chosen Workbench disks into this volume."""
        data = payload()
        session = service.get(image_id)
        apply_partition(service, session, data.get("partition"))
        chosen = data.get("discs") or {}
        if not isinstance(chosen, dict) or not chosen:
            raise DiskError("Choose which disc plays each part before installing.")
        discs = {str(role): service.get(str(identifier)) for role, identifier in chosen.items()}
        with operations.tracked(
            data.get("operationId"),
            "Installing Workbench",
            "Workbench installed",
        ) as progress:
            result = service.install_workbench(
                session,
                discs,
                version=str(data.get("version") or ""),
                create_drawers=data.get("createDrawers", True) is not False,
                progress=progress,
            )
        return jsonify(image=service.summary(session), workbench=result)

    # ------------------------------------------------------------------
    # AmigaOS 3.5 and 3.9, published on CD
    # ------------------------------------------------------------------

    @blueprint.get("/api/install/amigaos-cd/releases")
    def amigaos_cd_releases():
        """The CD releases this recognises, and what each one needs."""
        return jsonify(
            releases=describe_releases(),
            processor=REQUIRED_PROCESSOR,
            packs=boingbag.describe_bags(),
        )

    def uploaded_packs() -> list[tuple[str, bytes]]:
        """The update archives sent with a request, each held to the limit."""
        packs: list[tuple[str, bytes]] = []
        for upload in request.files.getlist("packs"):
            if not upload.filename:
                continue
            data = upload.read(PACK_UPLOAD_LIMIT + 1)
            name = Path(upload.filename).name
            if len(data) > PACK_UPLOAD_LIMIT:
                raise DiskError(
                    f"{name} is larger than the "
                    f"{PACK_UPLOAD_LIMIT // (1024 * 1024)} MB limit for an update pack."
                )
            if not is_lha_bytes(data):
                raise DiskError(
                    f"{name} is not an LHA archive. The BoingBags are published as "
                    "LHA archives, such as BoingBag39-1.lha."
                )
            packs.append((name, data))
        return packs

    @blueprint.post("/api/images/<image_id>/install/amigaos-cd/preflight")
    @request_effect("read-only", "checking whether a drive can take an AmigaOS CD")
    def amigaos_cd_preflight(image_id):
        """Say whether this drive, this hardware and this disc can work.

        Checked before anything is written, because every one of these is
        knowable from the outset. Nothing here writes to anything.
        """
        data = payload()
        session = service.get(image_id)
        apply_partition(service, session, data.get("partition"))
        disc = service.get(str(data["disc"]))
        return jsonify(preflight=service.amigaos_cd_preflight(session, disc))

    @blueprint.post("/api/install/amigaos-cd/packs")
    @request_effect("read-only", "looking inside update packs")
    def amigaos_cd_packs():
        """Say which update packs the supplied archives hold.

        One archive can hold several packs and a pack can be nested at any
        depth, so what was found is reported by pack rather than by file, and
        an archive that held nothing recognisable is named.
        """
        packs = uploaded_packs()
        archives = boingbag.open_archives(packs)
        found = boingbag.survey(archives)
        holding = {row["archive"] for row in found}
        return jsonify(
            packs=found,
            unrecognised=[name for name, _data in packs if name not in holding],
        )

    @blueprint.post("/api/images/<image_id>/install/amigaos-cd")
    @image_mutation("installing AmigaOS from a release CD")
    def install_amigaos_cd(image_id):
        """Install the release on a CD into this volume, with its update packs.

        The installation is made here and written into the drive. When this
        returns the drive holds the system, and nothing is left to be finished
        inside an emulator.
        """
        session = service.get(image_id)
        form = request.form
        apply_partition(service, session, form.get("partition"))
        disc = service.get(str(form.get("disc") or ""))
        chosen = [key for key in str(form.get("chosenPacks") or "").split(",") if key]
        packs = uploaded_packs() if form.get("withPacks", "true") != "false" else []
        if packs and not chosen and form.get("chosenPacks") is not None:
            # Every pack was unticked, which is a choice and not an omission.
            packs = []
        with operations.tracked(
            form.get("operationId"),
            "Installing AmigaOS",
            "AmigaOS installed",
        ) as progress:
            result = service.install_amigaos_cd(
                session,
                disc,
                packs=packs,
                chosen_packs=chosen,
                use_emulator=form.get("useEmulator", "true") != "false",
                progress=progress,
            )
        return jsonify(image=service.summary(session), amigaos=result)

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
