from __future__ import annotations

import threading
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from .rom import DEFAULT_BANK_SIZE
from .rom_workbench import normalise_project
from .dms import DMSContents


SESSION_OWNER: ContextVar[str | None] = ContextVar("amiga_session_owner", default=None)


@dataclass
class ImageSession:
    """Mutable state for one image open in the workbench.

    The model lives outside ``DiskService`` because checkpoints, operations,
    downloads and analysis services all consume the same session contract.
    """

    id: str
    name: str
    kind: str
    path: Path
    descriptor_name: str | None = None
    descriptor_path: Path | None = None
    dirty: bool = False
    dms: DMSContents | None = None
    #: Which RDB partition of a hard drive is open, by index into the drive's
    #: own partition list. A single-volume image leaves this None.
    partition: int | None = None
    ffs_source_names: dict[str, str] = field(default_factory=dict)
    distribution_name: str | None = None
    target_hardware: str = "auto"
    hardware_profile: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    ffs_capabilities: dict = field(default_factory=dict)
    finalised_mtime_ns: int | None = None
    hfe_original_path: Path | None = None
    hfe_version: str | None = None
    hfe_read_only: bool = False
    hfe_export_path: Path | None = None
    scp_original_path: Path | None = None
    scp_read_only: bool = False
    scp_export_path: Path | None = None
    rom_bank_size: int = DEFAULT_BANK_SIZE
    rom_erase_byte: int = 0xFF
    rom_platform: str = "kickstart"
    rom_layout: str = "linear"
    rom_component_names: list[str] = field(default_factory=list)
    rom_project: dict = field(default_factory=lambda: normalise_project({}))
    editor_projects: dict[str, dict] = field(default_factory=dict)
    compatibility_reports: list[dict] = field(default_factory=list)
    content_kind_cache: dict[tuple, str] = field(default_factory=dict)
    owner_id: str | None = field(default_factory=lambda: SESSION_OWNER.get())
    lock: threading.RLock = field(default_factory=threading.RLock)

    def invalidate_cached_views(self) -> None:
        """Drop everything derived from the image bytes.

        Anything the workbench inferred from the previous contents, extracted
        cached capability reports and prepared container exports, describes bytes
        that no longer exist once the image is rewritten wholesale. Callers that
        replace image data must invalidate together or a later read will mix
        old conclusions with new bytes.

        DMS state and the dirty flag are deliberately left to the caller: a
        restored checkpoint reparses its dms and stays clean, while a raw
        write clears the DMS and marks the image changed.
        """
        self.content_kind_cache.clear()
        self.hfe_export_path = None
        self.scp_export_path = None
        self.finalised_mtime_ns = None


__all__ = ["ImageSession", "SESSION_OWNER"]
