from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .amiga_metadata import format_protection
from .checksum import sha256_path
from .ofs_compat import ofs_catalogue_files
from . import amiga_paths

if TYPE_CHECKING:
    from .disk_service import DiskService, ImageSession


def timestamped_archive_name(image_name: str, generated: datetime | None = None) -> str:
    moment = generated or datetime.now().astimezone()
    stem = Path(image_name).stem or "amiga-image"
    return f"{stem}-{moment:%Y%m%d-%H%M%S}.zip"


def _safe_cell(value: object) -> str:
    return str(value if value not in (None, "") else "-").replace("|", "\\|").replace("\n", " ")


def _ofs_rows(data: bytes) -> list[str]:
    return [
        f"| `{_safe_cell(item.path)}` | {item.length:,} | `{item.load:06X}` | `{item.execute:06X}` |"
        for item in ofs_catalogue_files(data)
    ]


def _partition_catalogue(service, session) -> list[str]:
    """Describe every partition a hard drive declares.

    The table is read through the service rather than from the raw blocks, so
    the README reports exactly what the workbench shows: the device name each
    partition mounts as, its filing system, its size and whether the machine
    will boot from it.
    """
    lines = ["## Partition table", ""]
    try:
        partitions = service.list_partitions(session)
    except Exception as error:
        lines.extend((f"The partition table could not be read: {error}", ""))
        return lines
    if not partitions:
        lines.extend(("This drive declares no partitions.", ""))
        return lines
    lines.extend((
        "| Device | Filing system | Size | Cylinders | Boot |",
        "| --- | --- | ---: | --- | --- |",
    ))
    for partition in partitions:
        device = str(partition.get("device") or partition.get("name") or "")
        dos_type = str(partition.get("format") or "").replace("\x00", "\\0")
        size = int(partition.get("sizeBytes") or 0)
        low = partition.get("lowCylinder")
        high = partition.get("highCylinder")
        boot = (
            f"priority {partition.get('bootPriority')}"
            if partition.get("bootable")
            else "no"
        )
        lines.append(
            f"| `{device}` | {dos_type} | {size:,} bytes | {low}-{high} | {boot} |"
        )
    lines.append("")
    return lines


def _row_line(path: str, row: dict) -> str:
    kind = "directory" if row.get("type") in {"dir", "directory"} else "file"
    size = row.get("length", row.get("size", "-"))
    protection = row.get("protectionText") or format_protection(row.get("protection") or 0)
    comment = " ".join(str(row.get("comment") or "").split())
    datestamp = str(row.get("datestamp") or "")
    return (
        f"| `{_safe_cell(path)}` | {kind} | {_safe_cell(size)} | "
        f"`{_safe_cell(protection)}` | {_safe_cell(datestamp) or '-'} | "
        f"{_safe_cell(comment) or '-'} |"
    )


def _filesystem_catalogue(service: DiskService, session: ImageSession) -> list[str]:
    if session.kind == "rom":
        lines = [
            "## ROM bank catalogue", "",
            "| Bank | Title | Bytes | Kind | Header | SHA-256 | CRC-32 |",
            "|---:|---|---:|---|---|---|---|",
        ]
        for row in service.list_rom_banks(session):
            header = row.get("header") or {}
            extension = row.get("extensionHeader") or {}
            if header:
                detail = (
                    f"type &{header.get('typeHex')} · {header.get('processor')} · "
                    f"version {header.get('version') or header.get('versionByte')}"
                )
            elif extension:
                detail = (
                    "ExtnROM0 · checksum "
                    + ("valid" if extension.get("checksumValid") else "INVALID")
                )
            else:
                detail = "not recognised"
            lines.append("| " + " | ".join((
                f"{row['bank']:03d}",
                _safe_cell(row["name"]),
                f"{row['length']:,}",
                _safe_cell(row["filetype"]),
                _safe_cell(detail),
                f"`{row['diagnostics']['sha256']}`",
                f"`{row['diagnostics']['crc32']}`",
            )) + " |")
        return [*lines, ""]
    lines = [
        "## Filesystem catalogue",
        "",
        "| Path | Type | Bytes | Protection | Modified | Comment |",
        "|---|---|---:|---|---|---|",
    ]
    if session.kind == "dms":
        listing = service.list_directory(session, "")
        lines.extend(_row_line(str(row.get("name", "Untitled")), row) for row in listing["entries"])
        return [*lines, ""]

    if session.kind == "ofs":
        sides = [0, 2] if service.is_two_volume_image(session) else [None]
        object_count = 0
        for side in sides:
            for row in service.list_ofs_catalogue_files(session, side):
                display_path = row["path"]
                if side is not None:
                    display_path = f"Side {side}: {display_path}"
                lines.append(_row_line(display_path, row))
                object_count += 1
        if object_count == 0:
            lines.append("| _(empty)_ | - | - | - | - | - |")
        return [*lines, ""]

    pending: list[tuple[str, int | None]] = [("$", None)]
    visited: set[tuple[str, int | None]] = set()
    object_count = 0
    while pending:
        directory, side = pending.pop(0)
        identity = (directory.casefold(), side)
        if identity in visited:
            continue
        visited.add(identity)
        listing = service.list_directory(session, directory, side)
        for row in listing["entries"]:
            name = str(row.get("name") or "Untitled")
            inner_path = amiga_paths.join(directory, name)
            display_path = f"Side {side}: {inner_path}" if side is not None else inner_path
            lines.append(_row_line(display_path, row))
            object_count += 1
            if session.kind in {"ffs", "ofs"} and row.get("type") in {"dir", "directory"}:
                pending.append((inner_path, None))
            if object_count >= 100_000:
                lines.extend(("", "Catalogue stopped at the 100,000-object safety limit."))
                pending.clear()
                break
    if object_count == 0:
        lines.append("| _(empty)_ | - | - | - | - | - |")
    return [*lines, ""]


def build_download_readme(
    service: DiskService,
    session: ImageSession,
    image_path: Path,
    generated: datetime | None = None,
    *,
    image_checksum: str | None = None,
    descriptor_checksum: str | None = None,
) -> str:
    moment = generated or datetime.now().astimezone()
    container = "HFE" if session.hfe_original_path else session.kind.upper()
    lines = [
        f"# {session.name}",
        "",
        "This archive was prepared by Amiga File Forge, the open-source Amiga image workshop.",
        "Project: https://github.com/peteclarke-del/AmigaFileForge",
        "",
        "## Image details",
        "",
        f"- Generated: {moment.isoformat(timespec='seconds')}",
        f"- Container / filesystem: {container} / {session.kind.upper()}",
        f"- Target hardware profile: {session.target_hardware}",
        f"- Image filename: `{session.name}`",
        f"- Image size: {image_path.stat().st_size:,} bytes",
        f"- Image SHA-256: `{image_checksum or sha256_path(image_path)}`",
    ]
    profile = session.hardware_profile or {}
    if profile:
        lines.extend((
            f"- Workbench profile: {profile.get('name') or 'Custom'}",
            f"- Base machine: {profile.get('machine') or 'not specified'}",
            "- Hardware additions: " + (", ".join(profile.get("addons") or []) or "stock machine"),
            f"- Managed emulator: {profile.get('emulator') or 'automatic'}",
        ))
    capabilities = session.ffs_capabilities
    if capabilities:
        entry_limit = capabilities.get("directoryEntryLimit")
        lines.extend((
            f"- AmigaDOS format: FFS {capabilities.get('format', 'unknown')}",
            f"- Allocation map / directories: {capabilities.get('map', 'unknown')} / {capabilities.get('directories', 'unknown')}",
            f"- Filename limit: {capabilities.get('nameLimit', 10)} characters",
            "- Directory entry limit: " + (
                str(entry_limit) if entry_limit is not None else "capacity-dependent Big directory"
            ),
        ))
    if session.descriptor_path:
        lines.extend(
            (
                f"- Descriptor filename: `{session.descriptor_name}`",
                f"- Descriptor size: {session.descriptor_path.stat().st_size:,} bytes",
                f"- Descriptor SHA-256: `{descriptor_checksum or sha256_path(session.descriptor_path)}`",
            )
        )
    if session.compatibility_reports:
        accepted = session.compatibility_reports[-1]
        lines.extend((
            f"- Accepted compatibility report: {accepted.get('acceptedAt') or 'retained with this package'}",
            f"- Accepted operation: {accepted.get('operation') or 'review'}",
            "- Compatibility evidence: `Compatibility/accepted-report.json` and `Compatibility/accepted-report.md`",
        ))
    if session.hfe_original_path:
        lines.extend(
            (
                f"- HFE version: {session.hfe_version or 'unknown'}",
                f"- HFE write support: {'read-only' if session.hfe_read_only else 'editable and sector-verified'}",
            )
        )
    if session.kind == "rom":
        lines.extend((
            f"- ROM target family: {session.rom_platform}",
            f"- Logical bank size: {session.rom_bank_size:,} bytes",
            f"- Erased byte: `&{session.rom_erase_byte:02X}`",
            f"- Byte layout: {session.rom_layout}",
            "- Original component order: "
            + (", ".join(session.rom_component_names) or "single image or unspecified"),
            f"- Project hardware notes: {session.rom_project.get('hardware') or 'not recorded'}",
            f"- Saved project symbols: {len(session.rom_project.get('symbols', {}))}",
            f"- Saved emulator test results: {len(session.rom_project.get('tests', []))}",
        ))
    if session.kind == "kickfs":
        details = service.kickfs_details(session)
        lines.extend((
            f"- Kickstart ROM title: {details['title']}",
            f"- Paged-ROM header title: {details['headerTitle']}",
            f"- ROM version byte: {details['version']}",
            f"- Copyright: {details['copyright']}",
            f"- Catalogue files: {details['fileCount']}",
            f"- Filesystem state: {'plain and editable' if not details['readOnly'] else 'composite or incomplete, read-only'}",
        ))
    lines.extend(
        (
            "",
            "## Using this archive",
            "",
            "Keep this README beside the image so its catalogue, target and checksums stay with it.",
            "Verify the SHA-256 value after copying or writing it to media. Work from a backup, then test the edited image in an emulator or on disposable media before replacing a known-good card or disk.",
            "Before using important media on hardware, reopen a copy in Amiga File Forge and run Analyse > Image health dashboard, then review every itemised failure.",
        )
    )
    if session.descriptor_path:
        lines.extend(
            (
                "",
                "The HDA and GEO are a matched Hardfile pair. Keep both files together in the `Hardfile0` directory and do not substitute a descriptor from another image.",
            )
        )
    if session.kind == "rom":
        lines.extend((
            "",
            "## ROM interpretation and maintenance",
            "",
            "ROM images contain raw bytes rather than a filing system. The bank catalogue is a view over the saved byte image in ascending order.",
            "A Kickstart is 256 KiB, 512 KiB or 1 MiB; an expansion ROM is usually 8 KiB to 32 KiB. Test edited ROMs in an emulator or a spare programmable device before using valuable hardware.",
            "A bank title, role, processor and entry vectors are decoded from proven header structures. Printable strings and plausible AmigaOS modules remain evidence rather than invented files or a guarantee of compatibility.",
            "The programmed-byte count means bytes that differ from the configured erased value. It is not filesystem free space. File offsets refer to the complete image; mapped addresses refer to the configured target CPU window.",
            "`ROM-project.json` contains notes, symbols, analysed regions and test results. It is workbench metadata and is not programmed into the ROM device.",
            "Use Tools > ROM Workbench to inspect the bank map and audit, disassemble 68000-family code, follow reachable instructions and cross-references, compare revisions, build guarded patches and prepare physical chip files.",
            "Workbench comparison patches verify the complete source SHA-256 before applying ranges and the complete target SHA-256 afterwards. A mismatch aborts the operation.",
            "Programmer export does not rewrite the logical ROM. It applies padding or mirroring, optional adjacent-byte and 16-bit word swaps, address-line swaps, then one, two or four physical byte lanes to the programmer download.",
            "An expansion-ROM scaffold carries a real resident tag whose initialisation routine returns immediately, until a developer supplies code. The file archive is a documented layout for companion data and is not mounted by Kickstart.",
            "Exact ROM identities are keyed by complete SHA-256. Different padding, a one-byte edit or a concatenated bank set is a different identity even when the visible title matches.",
        ))
    if session.kind == "kickfs":
        lines.extend((
            "",
            "## Amiga Kickstart ROM notes",
            "",
            "This is a file archive stored inside a valid Amiga ROM image. Kickstart finds the ROM through its resident tag; reading the archive needs a companion module written for this layout.",
            "The archive is flat. Filenames are case-sensitive and up to 31 Latin-1 characters. Each file retains its protection bits and its execute-only flag.",
            "Every catalogue block and data block has a CRC. Amiga File Forge rebuilds those CRCs after a file or property change and validates them when the image is reopened.",
            "The execute-only flag marks a file that a companion module may start but will not hand back as ordinary data. It is not the same as an AmigaDOS protection bit.",
            "A plain, complete ROM archive is editable. An image with executable bytes after the archive, or an incomplete fragment of a multi-ROM set, is opened read-only so absolute code addresses are not moved.",
        ))
    if session.kind == "hdf" and session.partition is None:
        lines.extend(
            (
                "",
                "This is a partitioned hard drive. Each partition is an ordinary "
                "AmigaDOS volume, mounted under the device name its Rigid Disk "
                "Block declares, and is browsed by opening it in the workbench.",
                "",
                *_partition_catalogue(service, session),
            )
        )
    else:
        lines.extend(("", *_filesystem_catalogue(service, session)))
    warnings = [*session.warnings, *(list(session.dms.warnings) if session.dms else [])]
    lines.extend(("## Warnings and compatibility notes", ""))
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- No compatibility warnings were recorded for this working copy.")
    lines.extend(
        (
            "",
            "## Technical notes",
            "",
            "Amiga filenames, protection bits and comments are significant. Renaming a loader or moving software between OFS and FFS can break relative file references even when every file copied successfully.",
            "Complete disk images keep file metadata inside their own catalogues, so they do not need an image-level .inf sidecar. Loose files exported from Amiga File Forge are packaged with a matching .inf file instead.",
            "OFS and HDF cannot preserve flux timing, weak sectors or every copy-protection feature. HFE can contain track-level information that is not representable after filesystem editing.",
            "FFS directory and free-space metadata must match the selected hardware profile. Hardfile HDA images also require their matching GEO geometry.",
            "For current documentation, releases and issue reporting, visit https://github.com/peteclarke-del/AmigaFileForge.",
            "",
        )
    )
    return "\n".join(lines)


def write_download_readme(
    service: DiskService,
    session: ImageSession,
    image_path: Path,
    generated: datetime | None = None,
    *,
    image_checksum: str | None = None,
    descriptor_checksum: str | None = None,
) -> Path:
    target = session.path.parent / "download-README.md"
    target.write_text(
        build_download_readme(
            service,
            session,
            image_path,
            generated,
            image_checksum=image_checksum,
            descriptor_checksum=descriptor_checksum,
        ),
        encoding="utf-8",
    )
    return target
