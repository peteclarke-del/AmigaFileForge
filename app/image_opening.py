"""Shared image-opening workflow for browser uploads and desktop paths."""

from __future__ import annotations

from contextlib import ExitStack
import tempfile
from pathlib import Path

from werkzeug.datastructures import FileStorage

from .archive_utils import open_disk_image_upload
from .formats import (
    FFS_EXTENSIONS,
    OFS_EXTENSIONS,
    HFE_EXTENSIONS,
    HDF_EXTENSIONS,
    ROM_EXTENSIONS,
    SCP_EXTENSIONS,
    DMS_EXTENSIONS,
)
from .metadata_lookup import best_distribution_filename
from .rom_components import write_combined_rom


IMAGE_EXTENSIONS = (
    OFS_EXTENSIONS
    | HDF_EXTENSIONS
    | DMS_EXTENSIONS
    | FFS_EXTENSIONS
    | HFE_EXTENSIONS
    | SCP_EXTENSIONS
    | ROM_EXTENSIONS
)


def open_image_upload(
    service,
    image,
    descriptor=None,
    *,
    target_hardware: str = "auto",
    rom_options: dict | None = None,
    force_kind: str | None = None,
):
    """Open one upload-like stream through the canonical archive-aware path."""
    with open_disk_image_upload(image, IMAGE_EXTENSIONS) as (
        image_item,
        archived_descriptor,
    ):
        companion = descriptor or archived_descriptor
        descriptor_stream = None
        if companion is not None and companion.filename:
            descriptor_stream = (companion.filename, companion.stream)
        session = service.create_from_stream(
            image_item.filename,
            image_item.stream,
            descriptor_stream,
            target_hardware,
            rom_options,
            force_kind,
        )
        service.set_distribution_name(
            session,
            best_distribution_filename(image_item.metadata_names),
        )
        return session


def open_image_path(
    service,
    image_path: Path,
    descriptor_path: Path | None = None,
    *,
    target_hardware: str = "auto",
    rom_options: dict | None = None,
    force_kind: str | None = None,
):
    """Open a trusted desktop path without routing raw media through HTTP.

    Archives still use the canonical archive-aware stream path. Ordinary
    images use the service's reflink/sparse local copy while preserving the
    same validation and private-session boundary as browser uploads.
    """
    image_path = Path(image_path)
    descriptor_path = Path(descriptor_path) if descriptor_path else None
    if image_path.suffix.casefold() != ".zip":
        session = service.create_from_path(
            image_path,
            descriptor_path,
            target_hardware,
            rom_options,
            force_kind,
        )
        service.set_distribution_name(
            session,
            best_distribution_filename([image_path.name]),
        )
        return session
    with ExitStack() as stack:
        image_stream = stack.enter_context(image_path.open("rb"))
        image = FileStorage(stream=image_stream, filename=image_path.name)
        descriptor = None
        if descriptor_path is not None:
            descriptor_stream = stack.enter_context(descriptor_path.open("rb"))
            descriptor = FileStorage(
                stream=descriptor_stream,
                filename=descriptor_path.name,
            )
        return open_image_upload(
            service,
            image,
            descriptor,
            target_hardware=target_hardware,
            rom_options=rom_options,
            force_kind=force_kind,
        )


def open_rom_component_paths(
    service,
    component_paths: list[Path],
    *,
    layout: str = "linear",
    platform: str = "kickstart",
):
    """Build one logical ROM from trusted native component paths."""
    components = [Path(path) for path in component_paths]
    if not components:
        raise ValueError("Choose at least one ROM component.")
    stem = components[0].stem or "rom"
    with tempfile.TemporaryDirectory(dir=service.work_dir, prefix="native-rom-set-") as folder:
        combined = Path(folder) / f"{stem}-set.rom"
        write_combined_rom(components, combined, layout)
        return open_image_path(
            service,
            combined,
            target_hardware="auto",
            rom_options={
                "layout": layout,
                "platform": platform,
                "componentNames": [path.name for path in components],
            },
            force_kind="rom",
        )


__all__ = [
    "IMAGE_EXTENSIONS",
    "open_image_path",
    "open_image_upload",
    "open_rom_component_paths",
]
