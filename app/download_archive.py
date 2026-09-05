"""Build complete downloadable archives before the browser handoff."""

from __future__ import annotations

import json
import zipfile
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from .checksum import sha256_path
from .disk_service import DiskError, DiskService, ImageSession
from .readme_service import timestamped_archive_name, write_download_readme
from .rom_workbench import project_json


Progress = Callable[[str, int | None, int | None], None]
PROGRESS_TOTAL = 100


def _mapped_progress(
    report: Progress,
    message: str,
    start: int,
    finish: int,
) -> Callable[[int, int], None]:
    def update(current: int, total: int) -> None:
        fraction = current / total if total else 1
        report(message, start + round((finish - start) * fraction), PROGRESS_TOTAL)

    return update


def _archive_member(
    archive: zipfile.ZipFile,
    path: Path,
    archive_name: str,
    advanced: Callable[[int], None],
) -> None:
    info = zipfile.ZipInfo.from_file(path, archive_name)
    info.compress_type = archive.compression
    with path.open("rb") as source, archive.open(info, "w", force_zip64=True) as target:
        while chunk := source.read(8 * 1024 * 1024):
            target.write(chunk)
            advanced(len(chunk))


def build_download_archive(
    service: DiskService,
    session: ImageSession,
    report: Progress | None = None,
) -> tuple[Path, str]:
    """Finalise, document and completely build one ready-to-send ZIP."""
    notify = report or (lambda _message, _current=None, _total=None: None)
    notify("Validating the image for its selected hardware", 0, PROGRESS_TOTAL)
    image_path = service.prepare_download(
        session,
        lambda message, current=None, total=None: notify(
            message,
            round(10 * (current or 0) / total) if total else 0,
            PROGRESS_TOTAL,
        ),
    )
    generated = datetime.now().astimezone()

    notify("Calculating the image checksum", 10, PROGRESS_TOTAL)
    image_checksum = sha256_path(
        image_path,
        _mapped_progress(notify, "Calculating the image checksum", 10, 34),
    )
    descriptor_checksum = None
    if session.descriptor_path:
        descriptor_checksum = sha256_path(
            session.descriptor_path,
            _mapped_progress(notify, "Calculating the GEO checksum", 34, 35),
        )

    notify("Building the technical README and filesystem catalogue", 35, PROGRESS_TOTAL)
    readme_path = write_download_readme(
        service,
        session,
        image_path,
        generated,
        image_checksum=image_checksum,
        descriptor_checksum=descriptor_checksum,
    )

    is_hardfile = bool(
        session.descriptor_path and session.path.suffix.lower() in {".hdf", ".hda"}
    )
    image_stat = image_path.stat()
    allocated_size = int(getattr(image_stat, "st_blocks", 0)) * 512
    compress_sparse_dat = bool(
        is_hardfile
        and allocated_size
        and allocated_size < image_stat.st_size // 2
    )
    compression = zipfile.ZIP_DEFLATED if compress_sparse_dat else zipfile.ZIP_STORED
    archive_root = "Hardfile0/" if is_hardfile else ""
    files = [(readme_path, "README.md"), (image_path, f"{archive_root}{session.name}")]
    if session.descriptor_path:
        files.append(
            (session.descriptor_path, f"{archive_root}{session.descriptor_name}")
        )
    if session.kind == "rom":
        project_path = session.path.parent / "rom-project.json"
        project_path.write_bytes(project_json(session.rom_project))
        files.append((project_path, "ROM-project.json"))
        files.extend(
            (path, f"ROM-components/{name}")
            for path, name in service.rom_component_exports(session)
        )
    if session.compatibility_reports:
        accepted = session.compatibility_reports[-1]
        report_json = session.path.parent / "accepted-compatibility-report.json"
        report_markdown = session.path.parent / "accepted-compatibility-report.md"
        json_document = dict(accepted)
        markdown = str(json_document.pop("markdown", ""))
        report_json.write_text(
            json.dumps(json_document, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        report_markdown.write_text(markdown, encoding="utf-8")
        files.extend((
            (report_json, "Compatibility/accepted-report.json"),
            (report_markdown, "Compatibility/accepted-report.md"),
        ))
    byte_total = sum(path.stat().st_size for path, _name in files)
    byte_current = 0

    def advanced(length: int) -> None:
        nonlocal byte_current
        byte_current += length
        fraction = byte_current / byte_total if byte_total else 1
        notify(
            "Compressing the complete download ZIP"
            if compress_sparse_dat
            else "Building the complete download ZIP",
            40 + round(59 * fraction),
            PROGRESS_TOTAL,
        )

    archive_path = session.path.parent / "download-ready.zip"
    temporary = session.path.parent / "download-ready.zip.tmp"
    metadata_path = session.path.parent / "download-ready.json"
    metadata_temporary = session.path.parent / "download-ready.json.tmp"
    temporary.unlink(missing_ok=True)
    metadata_temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=compression,
            compresslevel=1 if compress_sparse_dat else None,
            allowZip64=True,
        ) as archive:
            for path, archive_name in files:
                _archive_member(archive, path, archive_name, advanced)
        temporary.replace(archive_path)
        archive_name = timestamped_archive_name(session.name, generated)
        metadata_temporary.write_text(
            json.dumps(
                {
                    "archiveName": archive_name,
                    "imageName": session.name,
                    "imagePath": image_path.name,
                    "imageSize": image_path.stat().st_size,
                    "imageMtimeNs": image_path.stat().st_mtime_ns,
                    "descriptorName": session.descriptor_name,
                    "descriptorMtimeNs": (
                        session.descriptor_path.stat().st_mtime_ns
                        if session.descriptor_path
                        else None
                    ),
                    "compatibilityReportAcceptedAt": (
                        session.compatibility_reports[-1].get("acceptedAt")
                        if session.compatibility_reports
                        else None
                    ),
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        metadata_temporary.replace(metadata_path)
    finally:
        temporary.unlink(missing_ok=True)
        metadata_temporary.unlink(missing_ok=True)
    notify("The complete ZIP is ready to download", PROGRESS_TOTAL, PROGRESS_TOTAL)
    return archive_path, archive_name


def prepared_download(session: ImageSession) -> tuple[Path, str]:
    """Return a complete archive only while it still matches this session."""
    archive_path = session.path.parent / "download-ready.zip"
    metadata_path = session.path.parent / "download-ready.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        image_path = session.path.parent / str(metadata["imagePath"])
        image_stat = image_path.stat()
        descriptor_mtime = (
            session.descriptor_path.stat().st_mtime_ns
            if session.descriptor_path
            else None
        )
        valid = (
            archive_path.is_file()
            and image_path.is_file()
            and metadata.get("imageName") == session.name
            and metadata.get("descriptorName") == session.descriptor_name
            and int(metadata.get("imageSize", -1)) == image_stat.st_size
            and int(metadata.get("imageMtimeNs", -1)) == image_stat.st_mtime_ns
            and metadata.get("descriptorMtimeNs") == descriptor_mtime
            and metadata.get("compatibilityReportAcceptedAt") == (
                session.compatibility_reports[-1].get("acceptedAt")
                if session.compatibility_reports
                else None
            )
        )
        if not valid:
            raise ValueError
        return archive_path, str(metadata["archiveName"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DiskError(
            "This download is not prepared yet, or the image changed afterward. Save it again."
        ) from exc
