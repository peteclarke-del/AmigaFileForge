from __future__ import annotations

import re

from .image_session import ImageSession


def normalise_warnings(warnings: list[str]) -> list[str]:
    """Keep durable image history concise and discard superseded diagnostics."""
    result: list[str] = []
    directory_fields_repaired = False
    accelerator_warning = False
    loader_review = False
    for value in warnings:
        warning = str(value).strip()
        if not warning:
            continue
        if re.match(r"^Repaired \d+ directory block checksum", warning):
            directory_fields_repaired = True
            continue
        if "selected hardware profile fits a CPU accelerator" in warning:
            accelerator_warning = True
            continue
        # A point-in-time loader diagnosis from an older release is replaced by
        # one review notice, because the audit that produced it has changed.
        if (
            "contains an ambiguous" in warning and "reference" in warning
            or "contains ambiguous FFS command" in warning
        ):
            loader_review = True
            continue
        if warning not in result:
            result.append(warning)
    if directory_fields_repaired:
        result.append("Repaired directory block checksums and refreshed the volume bitmap.")
    if accelerator_warning:
        result.append(
            "The selected hardware profile fits a CPU accelerator. Some OCS and ECS software "
            "requires the accelerator and its Fast RAM to be disabled unless it explicitly "
            "supports them."
        )
    if loader_review:
        result.append(
            "Installed FFS loader diagnostics have changed since this image was edited. "
            "Run Tools > Check installed disk software for the current path-aware results."
        )
    return result


def session_metadata(session: ImageSession) -> dict:
    """Build the stable JSON representation of a recoverable image session."""
    return {
        "id": session.id,
        "name": session.name,
        "kind": session.kind,
        "descriptorName": session.descriptor_name,
        "descriptorFile": session.descriptor_path.name if session.descriptor_path else None,
        "partition": session.partition,
        "ffsSourceNames": session.ffs_source_names,
        "distributionName": session.distribution_name,
        "targetHardware": session.target_hardware,
        "hardwareProfile": session.hardware_profile,
        "ffsCapabilities": session.ffs_capabilities,
        "workingFile": session.path.name,
        "hfeOriginalFile": session.hfe_original_path.name if session.hfe_original_path else None,
        "hfeVersion": session.hfe_version,
        "hfeReadOnly": session.hfe_read_only,
        "hfeExportFile": session.hfe_export_path.name if session.hfe_export_path else None,
        "scpOriginalFile": session.scp_original_path.name if session.scp_original_path else None,
        "scpReadOnly": session.scp_read_only,
        "scpExportFile": session.scp_export_path.name if session.scp_export_path else None,
        "romBankSize": session.rom_bank_size,
        "romEraseByte": session.rom_erase_byte,
        "romPlatform": session.rom_platform,
        "romLayout": session.rom_layout,
        "romComponentNames": session.rom_component_names,
        "romProject": session.rom_project,
        "editorProjects": session.editor_projects,
        "compatibilityReports": session.compatibility_reports[-10:],
        "dirty": session.dirty,
        "finalisedMtimeNs": session.finalised_mtime_ns,
        "ownerId": session.owner_id,
        "warnings": session.warnings,
    }
