"""Desktop-only adapters kept outside the shared image API."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
import tempfile

from flask import Blueprint, jsonify

from amiga_floppy import (
    AMIGA_GEOMETRIES,
    geometry as floppy_geometry,
    validated_device,
    FloppyDevice,
    FloppyError,
    available_devices,
)
from amiga_greaseweazle import (
    DRIVE_CHOICES,
    GreaseweazleClient,
    GreaseweazleError,
    image_format,
    stable_snapshot,
)

from ..disk_service import DiskError, DiskService
from ..desktop_state import DesktopClientState
from ..image_opening import open_image_path, open_rom_component_paths
from ..operations import OperationRegistry
from ..rom_components import MAX_ROM_COMPONENTS
from .common import payload
from .effects import request_effect


def _regular_file(value: object, label: str) -> Path:
    raw = str(value or "").strip()
    if not raw:
        raise DiskError(f"Choose {label}.")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise DiskError(f"The desktop {label} must use an absolute path.")
    try:
        path = path.resolve(strict=True)
    except OSError as exc:
        raise DiskError(f"The desktop {label} no longer exists: {path}") from exc
    if not path.is_file():
        raise DiskError(f"The desktop {label} is not a regular file: {path}")
    return path


def _matching_sibling(path: Path, suffix: str) -> Path | None:
    wanted = f"{path.stem}{suffix}".casefold()
    try:
        return next(
            item for item in path.parent.iterdir()
            if item.is_file() and item.name.casefold() == wanted
        )
    except (OSError, StopIteration):
        return None


def _image_pair(data: dict) -> tuple[Path, Path | None]:
    image = _regular_file(data.get("path"), "image")
    descriptor_value = data.get("descriptorPath")
    descriptor = (
        _regular_file(descriptor_value, "GEO descriptor")
        if descriptor_value else None
    )
    if image.suffix.casefold() == ".geo":
        descriptor = image
        image = _matching_sibling(image, ".hdf") or _matching_sibling(image, ".hda")
        if image is None:
            raise DiskError(f"Choose the hard-drive image matching {descriptor.name}.")
    elif image.suffix.casefold() in {".hdf", ".hda"} and descriptor is None:
        descriptor = _matching_sibling(image, ".geo")
    return image, descriptor


def _physical_media_details(service: DiskService, session) -> dict:
    if session.kind == "hdf":
        raise DiskError(
            "A hard drive cannot be written to a floppy drive. Open a floppy image instead."
        )
    name = session.name
    if session.kind in {"ffs", "ofs"} and service.summary(session).get("hardDisk"):
        raise DiskError(
            "A hard-disk image cannot be written to a floppy drive. Open a floppy image instead."
        )
    try:
        media_format = image_format(name)
    except GreaseweazleError as exc:
        raise DiskError(str(exc)) from exc
    return {
        "name": name,
        "format": media_format.label,
        "automaticVerification": media_format.automatic_verification,
    }


@contextmanager
def _physical_media(service: DiskService, session, details: dict, progress):
    """Expose finalised media without allowing later edits to change the write."""
    temporary: Path | None = None
    try:
        progress("Finalising the working image before physical media access", 0, None)
        with session.lock:
            source = service.prepare_download(
                session,
                lambda message, _current=None, _total=None: progress(message, None, None),
            )
        snapshot_context = stable_snapshot(source, service.work_dir)
        with session.lock:
            snapshot = snapshot_context.__enter__()
        try:
            yield snapshot
        finally:
            snapshot_context.__exit__(None, None, None)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


# Capture targets the workbench can open again, mapped to the suffix each one
# writes. A request names the format, so the suffix is taken from this table
# rather than from the request, keeping the caller's text out of the path.
#
# Only formats Greaseweazle itself writes appear here: sectors as an ADF or a
# PC-format IMG, and flux as HFE, SCP or IPF. An ADZ is an ADF compressed
# afterwards and a DMS is built by DiskMasher, so neither is a capture target.
PHYSICAL_READ_FORMATS: dict[str, str] = {
    "adf": ".adf",
    "img": ".img",
    "hfe": ".hfe",
    "scp": ".scp",
    "ipf": ".ipf",
}


# A capture is written under a fixed name inside a private scratch directory,
# so no part of a request reaches the filesystem. The name the caller asked for
# is applied to the session afterwards, where it is validated by the same rules
# as any other rename.
CAPTURE_STEM = "capture"


def _name_captured_session(service: DiskService, session, requested: object) -> None:
    """Apply the caller's chosen name to a freshly captured image."""
    wanted = str(requested or "").strip()
    if not wanted:
        return
    suffix = Path(str(getattr(session, "name", "") or "")).suffix
    try:
        service.rename_session(session, f"{Path(wanted).stem}{suffix}")
    except (DiskError, TypeError, ValueError):
        # A capture that cannot take the requested name is still a good
        # capture, so it keeps the default rather than being discarded.
        pass


def create_desktop_blueprint(
    service: DiskService,
    operations: OperationRegistry | None = None,
    client_state: DesktopClientState | None = None,
) -> Blueprint:
    operations = operations or OperationRegistry()
    blueprint = Blueprint("desktop", __name__)

    @blueprint.get("/api/desktop/client-state")
    def get_client_state():
        return jsonify((client_state or DesktopClientState(service.work_dir / "client-state.json")).read())

    @blueprint.put("/api/desktop/client-state")
    @request_effect("external", "saving durable Linux desktop preferences")
    def put_client_state():
        data = payload()
        state = client_state or DesktopClientState(service.work_dir / "client-state.json")
        document = state.update(
            local_storage=data.get("localStorage") if "localStorage" in data else None,
            collection=data.get("collection") if "collection" in data else None,
        )
        return jsonify(version=document["version"])

    @blueprint.post("/api/desktop/open-path")
    @request_effect("lifecycle", "opening a local desktop image session")
    def open_local_path():
        data = payload()
        component_values = data.get("componentPaths")
        if isinstance(component_values, list) and len(component_values) > 1:
            if data.get("forceKind") != "rom":
                raise DiskError("Multiple native paths require an explicit ROM component-set plan.")
            if len(component_values) > MAX_ROM_COMPONENTS:
                raise DiskError(
                    f"A ROM set cannot contain more than {MAX_ROM_COMPONENTS} components."
                )
            components = [
                _regular_file(value, "ROM component") for value in component_values
            ]
            rom = data.get("rom") if isinstance(data.get("rom"), dict) else {}
            try:
                session = open_rom_component_paths(
                    service,
                    components,
                    layout=str(rom.get("layout") or "linear"),
                    platform=str(rom.get("platform") or "kickstart"),
                )
            except (OSError, ValueError) as exc:
                raise DiskError(str(exc)) from exc
            return jsonify(image=service.summary(session))
        image_path, descriptor_path = _image_pair(data)
        session = open_image_path(
            service,
            image_path,
            descriptor_path,
            target_hardware=str(data.get("targetHardware") or "auto"),
            rom_options=(
                data.get("rom") if isinstance(data.get("rom"), dict) else None
            ),
            force_kind=str(data.get("forceKind") or "") or None,
        )
        return jsonify(image=service.summary(session))

    @blueprint.get("/api/desktop/images/<image_id>/physical-floppy")
    @request_effect("external", "probing Greaseweazle physical-floppy access")
    def physical_floppy_status(image_id):
        session = service.get(image_id)
        details = _physical_media_details(service, session)
        probe = GreaseweazleClient().probe()
        return jsonify(
            available=probe.available,
            command=probe.command,
            detail=probe.detail,
            drives=[{"id": drive, "label": f"Drive {drive}"} for drive in DRIVE_CHOICES],
            media=details,
        )

    @blueprint.post("/api/desktop/images/<image_id>/physical-floppy")
    @request_effect("external", "writing a physical floppy through Greaseweazle")
    def write_physical_floppy(image_id):
        data = payload()
        session = service.get(image_id)
        details = _physical_media_details(service, session)
        operation_id = str(data.get("operationId") or "") or None
        try:
            with operations.tracked(
                operation_id,
                f"Preparing {details['name']} for physical drive {data.get('drive') or ''}",
                "Physical floppy write complete",
            ) as progress:
                with _physical_media(service, session, details, progress) as image:
                    result = GreaseweazleClient().write(
                        image,
                        str(data.get("drive") or ""),
                        progress,
                    )
        except GreaseweazleError as exc:
            raise DiskError(str(exc)) from exc
        return jsonify(result=asdict(result), media=details)

    @blueprint.post("/api/desktop/physical-floppy/read")
    @request_effect("external", "reading a physical floppy through Greaseweazle")
    def read_physical_floppy():
        """Capture a disk in a connected drive and open it as a new image.

        The capture lands in a temporary file first. Only a Greaseweazle run
        that completes and leaves a usable image is opened as a session, so a
        failed or empty read never becomes a pane the user might trust.
        """
        data = payload()
        drive = str(data.get("drive") or "")
        suffix = PHYSICAL_READ_FORMATS.get(str(data.get("format") or "adf").strip().lower())
        if suffix is None:
            raise DiskError(
                "Choose a capture format: "
                + ", ".join(sorted(PHYSICAL_READ_FORMATS))
                + "."
            )
        revolutions = data.get("revolutions")
        operation_id = str(data.get("operationId") or "") or None
        with tempfile.TemporaryDirectory(dir=service.work_dir, prefix="gw-read-") as folder:
            destination = Path(folder) / f"{CAPTURE_STEM}{suffix}"
            try:
                with operations.tracked(
                    operation_id,
                    f"Reading physical drive {drive}",
                    "Physical floppy captured",
                ) as progress:
                    result = GreaseweazleClient().read(
                        destination,
                        drive,
                        progress,
                        revolutions=int(revolutions) if revolutions is not None else None,
                    )
                    progress("Opening the captured image", None, None)
                    session = service.create_from_path(destination)
                    _name_captured_session(service, session, data.get("name"))
            except GreaseweazleError as exc:
                raise DiskError(str(exc)) from exc
        return jsonify(image=service.summary(session), result=asdict(result))

    @blueprint.get("/api/desktop/floppy-drive")
    @request_effect("external", "probing a floppy controller")
    def floppy_drive_status():
        """Report the floppy controller this host exposes, if any."""
        devices = available_devices()
        probe = FloppyDevice(devices[0]).probe() if devices else FloppyDevice().probe()
        return jsonify(
            available=probe.available,
            device=probe.device,
            detail=probe.detail,
            size=probe.size,
            devices=devices,
            geometries=[
                {
                    "id": item.identifier,
                    "label": item.label,
                    "extension": item.extension,
                    "size": item.size,
                }
                for item in sorted(AMIGA_GEOMETRIES.values(), key=lambda row: row.size)
            ],
        )

    @blueprint.post("/api/desktop/floppy-drive/read")
    @request_effect("external", "reading a disk from a floppy controller")
    def read_floppy_drive():
        """Capture a disk from a real drive and open it as a new image."""
        data = payload()
        geometry_id = str(data.get("geometry") or "")
        operation_id = str(data.get("operationId") or "") or None
        try:
            device = validated_device(data.get("device") or "/dev/fd0")
            layout = floppy_geometry(geometry_id)
        except FloppyError as exc:
            raise DiskError(str(exc)) from exc
        with tempfile.TemporaryDirectory(dir=service.work_dir, prefix="fd-read-") as folder:
            destination = Path(folder) / f"{CAPTURE_STEM}{layout.extension}"
            try:
                with operations.tracked(
                    operation_id,
                    f"Reading {layout.label} from {device}",
                    "Disk captured from the floppy drive",
                ) as progress:
                    result = FloppyDevice(device).read(destination, geometry_id, progress)
                    progress("Opening the captured image", None, None)
                    session = service.create_from_path(destination)
                    _name_captured_session(service, session, data.get("name"))
            except FloppyError as exc:
                raise DiskError(str(exc)) from exc
        return jsonify(image=service.summary(session), result=asdict(result))

    @blueprint.post("/api/desktop/floppy-drive/write")
    @request_effect("external", "writing a disk through a floppy controller")
    def write_floppy_drive():
        """Write an open image to a real drive, erasing the disk in it."""
        data = payload()
        session = service.get(str(data.get("image") or ""))
        try:
            device = validated_device(data.get("device") or "/dev/fd0")
        except FloppyError as exc:
            raise DiskError(str(exc)) from exc
        details = _physical_media_details(service, session)
        operation_id = str(data.get("operationId") or "") or None
        try:
            with operations.tracked(
                operation_id,
                f"Writing {details['name']} to {device}",
                "Floppy drive write complete",
            ) as progress:
                with _physical_media(service, session, details, progress) as image:
                    result = FloppyDevice(device).write(
                        image, progress, confirm=bool(data.get("confirm")),
                    )
        except FloppyError as exc:
            raise DiskError(str(exc)) from exc
        return jsonify(result=asdict(result), media=details)

    return blueprint


__all__ = ["create_desktop_blueprint"]
