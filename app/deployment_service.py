"""Build validated, non-mutating hardware deployment packages."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import threading
import zipfile
from contextlib import contextmanager
from copy import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Callable

from .analysis_service import preflight_report
from .checksum import sha256_bytes, sha256_path
from .errors import DiskError
from .version import application_version


DEPLOYMENT_FORMAT = "amiga-file-forge-hardware-deployment"
DEPLOYMENT_VERSION = 1
FAT32_FILE_LIMIT = 4 * 1024 * 1024 * 1024 - 1


TARGETS = (
    {
        "id": "gotek",
        "label": "Gotek / FlashFloppy USB",
        "description": "Floppy images in native or indexed FlashFloppy layout.",
    },
    {
        "id": "hdf-card",
        "label": "HDF on an SD card",
        "description": "An HDF installed as AMIGA.HDF in the FAT root.",
    },
    {
        "id": "hardfile",
        "label": "Hardfile SD card",
        "description": "A matched HDA and GEO pair below Hardfile0.",
    },
    {
        "id": "pistorm",
        "label": "PiStorm SD card",
        "description": "An HDF or a hardfile pair in the paths PiStorm uses.",
    },
    {
        "id": "amigaos",
        "label": "AmigaOS hard-drive host",
        "description": "An AmigaDOS image and companion metadata for deployment or emulation.",
    },
)


@dataclass(frozen=True)
class DeploymentEntry:
    path: str
    role: str
    source: Path | None = None
    data: bytes | None = None

    @property
    def size(self) -> int:
        return len(self.data) if self.data is not None else int(self.source.stat().st_size)

    def digest(self, progress: Callable[[int, int], None] | None = None) -> str:
        if self.data is not None:
            return sha256_bytes(self.data)
        return sha256_path(self.source, progress)


def _safe_leaf(value: str, fallback: str = "DISK") -> str:
    stem = re.sub(r"[^A-Za-z0-9._ -]+", "_", str(value or "")).strip(" ._")
    return stem or fallback


def _entry(path: str, role: str, *, source: Path | None = None, data: bytes | str | None = None) -> DeploymentEntry:
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts or not pure.parts:
        raise DiskError(f"The deployment path is unsafe: {path}")
    encoded = data.encode("utf-8") if isinstance(data, str) else data
    if (source is None) == (encoded is None):
        raise DiskError("A deployment entry must have exactly one source.")
    return DeploymentEntry(str(pure), role, source=source, data=encoded)


def is_hard_drive_image(service, session) -> bool:
    """Whether this image is a hard drive, with or without a partition table.

    A ``.hdf`` that carries a Rigid Disk Block opens as ``kind == "hdf"``. A
    bare hardfile -- the commonest kind of ``.hdf`` in the wild -- has no RDB
    at all, so it identifies as the single volume it holds and arrives here as
    an ordinary FFS or OFS session that happens to be hard-drive sized. Both
    are hard drives, and a target that copies the file as it stands works the
    same for either.
    """
    if session.kind == "hdf":
        return True
    return bool(service.summary(session).get("hardDisk"))


def available_deployment_targets(service, session) -> list[dict]:
    suffix = session.path.suffix.casefold()
    summary = service.summary(session)
    floppy = session.kind in {"ofs", "ffs"} and not bool(summary.get("hardDisk"))
    paired_dat = bool(session.descriptor_path and suffix in {".hdf", ".hda"})
    hard_drive = is_hard_drive_image(service, session)
    support = {
        "gotek": floppy or suffix == ".hfe",
        "hdf-card": hard_drive,
        "hardfile": paired_dat,
        "pistorm": hard_drive or paired_dat,
        "amigaos": session.kind in {"ffs", "ofs"} and not paired_dat,
    }
    reasons = {
        "gotek": "A Gotek holds floppy images. Open a floppy or an HFE.",
        "hdf-card": "SD-card deployment requires a hard-drive image.",
        "hardfile": "Hardfile deployment requires a matched HDA and GEO pair.",
        "pistorm": "PiStorm deployment requires a hard-drive image or a matched Hardfile pair.",
        "amigaos": "AmigaOS deployment requires an AmigaDOS FFS, HDF or RAW image.",
    }
    return [
        {**target, "available": support[target["id"]], "reason": "" if support[target["id"]] else reasons[target["id"]]}
        for target in TARGETS
    ]


def _copy_sparse(
    source: Path,
    destination: Path,
    progress: Callable[[int, int], None] | None = None,
) -> None:
    """Copy an image without materialising zero-filled HDA extents."""
    block_size = 4 * 1024 * 1024
    size = source.stat().st_size
    copied = 0
    with source.open("rb") as reader, destination.open("wb") as writer:
        while chunk := reader.read(block_size):
            if chunk.strip(b"\0"):
                writer.write(chunk)
            else:
                writer.seek(len(chunk), os.SEEK_CUR)
            copied += len(chunk)
            if progress:
                progress(copied, size)
        writer.truncate(size)
    shutil.copystat(source, destination)


@contextmanager
def prepared_snapshot(service, session, progress: Callable | None = None):
    """Yield a hardware-finalised session copy and leave the live session untouched."""
    with tempfile.TemporaryDirectory(dir=service.work_dir, prefix="deployment-snapshot-") as folder:
        root = Path(folder)
        configured = copy(session)
        configured.path = root / session.path.name
        _copy_sparse(
            session.path,
            configured.path,
            (lambda current, total: progress("Copying an isolated image snapshot", current, total))
            if progress else None,
        )
        if session.descriptor_path:
            configured.descriptor_path = root / session.descriptor_path.name
            shutil.copy2(session.descriptor_path, configured.descriptor_path)
        configured.lock = threading.RLock()
        configured.finalised_mtime_ns = None
        service.prepare_download(configured, progress)
        yield configured


def _gotek_entries(service, session, options: dict) -> list[DeploymentEntry]:
    mode = str(options.get("gotekMode") or "native").strip().lower()
    if mode not in {"native", "indexed"}:
        raise DiskError("Choose Native or Indexed Gotek navigation.")
    try:
        start = int(options.get("startIndex") or 0)
    except (TypeError, ValueError) as exc:
        raise DiskError("The first Gotek index must be a number from 0 to 9999.") from exc
    if start < 0 or start > 9999:
        raise DiskError("The first Gotek index must be between 0 and 9999.")
    # A Gotek holds floppy images. A hard drive's partitions are not floppies,
    # so offering them here would produce media the device cannot use.
    if session.kind == "hdf":
        raise DiskError(
            "A Gotek package holds floppy images. Build a whole-drive package "
            "for a hard drive, or deploy its floppies individually."
        )
    images: list[tuple[str, bytes | None, Path | None]] = []
    source = service.prepare_download(session)
    images.append((_safe_leaf(source.name), None, source))
    if start + len(images) > 10_000:
        raise DiskError("The selected Gotek index range exceeds DSKA9999.")
    entries = []
    for offset, (name, data, source) in enumerate(images):
        leaf = name
        if mode == "indexed":
            suffix = Path(name).suffix or ".adf"
            leaf = f"DSKA{start + offset:04d}_{_safe_leaf(Path(name).stem)}{suffix.lower()}"
        entries.append(_entry(f"GOTEK-USB/{leaf}", "floppy image", source=source, data=data))
    if mode == "indexed":
        entries.append(_entry(
            "GOTEK-USB/FF.CFG",
            "FlashFloppy configuration",
            data="nav-mode = indexed\nindexed-prefix = DSKA\n",
        ))
    return entries


def _media_entries(service, session, target: str) -> list[DeploymentEntry]:
    if target == "hdf-card":
        return [_entry("SD-CARD/AMIGA.HDF", "HDF disk collection", source=session.path)]
    if target in {"hardfile", "pistorm"} and session.descriptor_path:
        source = service.prepare_download(session)
        return [
            _entry("SD-CARD/Hardfile0/scsi0.hda", "Hardfile data image", source=source),
            _entry("SD-CARD/Hardfile0/scsi0.geo", "Hardfile geometry descriptor", source=session.descriptor_path),
        ]
    if target == "pistorm":
        return [_entry("SD-CARD/AMIGA.HDF", "PiStorm HDF disk collection", source=session.path)]
    if target == "amigaos":
        source = service.prepare_download(session)
        entries = [_entry(f"AMIGA-HOST/Images/{_safe_leaf(source.name)}", "AmigaDOS image", source=source)]
        if session.descriptor_path:
            entries.append(_entry(
                f"AMIGA-HOST/Images/{_safe_leaf(session.descriptor_path.name)}",
                "companion descriptor",
                source=session.descriptor_path,
            ))
        return entries
    raise DiskError("The open image is not compatible with that deployment target.")


def _profile_findings(session, target: str, *, has_partition_table: bool = True) -> list[dict]:
    profile = session.hardware_profile or {}
    addons = {str(value) for value in profile.get("addons") or []}
    findings = []
    def warn(message: str) -> None:
        findings.append({"severity": "warning", "message": message})
    if not profile:
        warn("No hardware profile is applied; machine-specific checks are limited.")
    if target == "hdf-card" and not (
        {item for item in addons if item.startswith(("ide-", "scsi-", "a2091", "cf-")) }
        or str(profile.get("handlerBuild") or "none") != "none"
    ):
        warn("The selected hardware profile declares no mass-storage interface for a hard drive.")
    if target == "hardfile" and "hardfile" not in addons and session.target_hardware != "hardfile":
        warn("The selected hardware profile does not declare Hardfile storage.")
    if target == "pistorm":
        if str(profile.get("emulator") or "") != "fs-uae-pistorm":
            warn("The profile does not explicitly select the PiStorm-aware FS-UAE integration.")
        if not {"pistorm", "pistorm32"} & addons:
            warn("The applied profile declares no PiStorm board.")
        elif profile.get("machine") == "a1200" and "pistorm32" not in addons:
            warn("An A1200 takes the PiStorm32 in its CPU slot, not the 68000-socket board.")
        elif profile.get("machine") != "a1200" and "pistorm32" in addons:
            warn("The PiStorm32 fits the A1200 CPU slot only.")
    if target == "amigaos" and profile.get("machine") not in {"a3000", "a4000", None, ""}:
        warn("The applied profile is not an Amiga 3000 or 4000 hard-drive machine.")
    if target in {"hdf-card", "pistorm"} and not has_partition_table:
        # A bare hardfile is a legitimate AMIGA.HDF, but nothing inside it says
        # how many heads and sectors the drive has. Something on the receiving
        # side has to supply that, and saying so here is more use than refusing
        # to build the package at all.
        warn(
            "This image carries no Rigid Disk Block, so it holds one bare volume "
            "and declares no geometry of its own. The receiving adapter or "
            "firmware must be told the drive's heads, sectors and cylinders, or "
            "be one that assumes them."
        )
    return findings


def _instructions(session, target: str, options: dict) -> list[str]:
    instructions = {
        "gotek": [
            "Format the USB device with a filesystem supported by the installed Gotek firmware.",
            "Copy the contents of GOTEK-USB to the root of the USB device.",
            "Keep FF.CFG with the indexed images when Indexed mode was selected.",
            "Insert the USB device, select a disk, catalogue it and verify a read before enabling writes.",
        ],
        "hdf-card": [
            "Back up the existing SD card before replacing its disk collection.",
            "Copy SD-CARD/AMIGA.HDF to the FAT root as AMIGA.HDF.",
            "Check that the Kickstart in the target machine can read the file system the HDF uses.",
            "Boot the machine, list two known partitions and test a read before writing to the collection.",
        ],
        "hardfile": [
            "Back up the existing SD card and preserve any other SCSI target directories.",
            "Copy SD-CARD/Hardfile0 to the SD-card root without renaming scsi0.hda or scsi0.geo.",
            "Start with the intended Kickstart and target hardware, then list the root and several drawers.",
            "After the first write, reboot and repeat the directory checks before relying on the image.",
        ],
        "pistorm": [
            "Start from a working PiStorm SD card and preserve its kernel, firmware and PiStorm.cfg.",
            "Merge the contents of SD-CARD into the existing FAT root; do not replace unrelated PiStorm files.",
            "For a whole-drive image keep AMIGA.HDF in the root; for a hardfile keep the pair below Hardfile0.",
            "Boot the configured machine with the accelerator disabled first, verify storage, then repeat with optional expansions.",
        ],
        "amigaos": [
            "Back up the destination emulator or storage media before installing the image.",
            "Copy the image from AMIGA-HOST/Images to the location the emulator or storage adapter expects.",
            "Attach it using the geometry and interface appropriate to the selected AmigaOS target.",
            "Run the filing-system free-space and directory checks before allowing applications to write.",
        ],
    }
    return instructions[target]


def _deployment_plan(service, session, payload: dict, progress: Callable | None = None) -> tuple[dict, list[DeploymentEntry]]:
    report = progress or (lambda _message, _current=None, _total=None: None)
    target = str(payload.get("target") or "").strip().lower()
    availability = {item["id"]: item for item in available_deployment_targets(service, session)}
    if target not in availability:
        raise DiskError("Choose a supported deployment target.")
    if not availability[target]["available"]:
        raise DiskError(availability[target]["reason"])
    report("Planning target paths and filenames", 0, 4)
    entries = _gotek_entries(service, session, payload) if target == "gotek" else _media_entries(service, session, target)
    paths = [entry.path.casefold() for entry in entries]
    if len(paths) != len(set(paths)):
        raise DiskError("The deployment would create two files with the same target path.")
    report("Checking capacity and target profile", 1, 4)
    issues = _profile_findings(
        session, target, has_partition_table=session.kind == "hdf"
    )
    for entry in entries:
        if entry.size > FAT32_FILE_LIMIT and target in {"gotek", "hdf-card", "hardfile", "pistorm"}:
            issues.append({
                "severity": "error",
                "message": f"{entry.path} exceeds the FAT32 single-file limit.",
            })
    report("Hashing deployment files", 2, 4)
    total_bytes = sum(entry.size for entry in entries)
    hashed = 0
    manifest_entries = []
    for entry in entries:
        digest = entry.digest(
            lambda current, _total, entry=entry, completed=hashed: report(
                f"Hashing {entry.path}", completed + current, total_bytes,
            )
        )
        manifest_entries.append({
            "path": entry.path,
            "role": entry.role,
            "size": entry.size,
            "sha256": digest,
        })
        hashed += entry.size
        report(f"Hashed {entry.path}", hashed, total_bytes)
    summary = service.summary(session)
    compatibility = preflight_report(service, session, {
        "operation": f"deploy-{target}",
        "sourceKind": session.kind,
        "targetKind": "host",
        "changes": [
            {
                "name": PurePosixPath(entry.path).name,
                "nameIsLeaf": True,
                "parent": str(PurePosixPath(entry.path).parent),
                "source": session.name,
                "type": entry.role,
            }
            for entry in entries
        ],
    })
    issues.extend(compatibility["issues"])
    plan = {
        "format": DEPLOYMENT_FORMAT,
        "version": DEPLOYMENT_VERSION,
        "applicationVersion": application_version(),
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "target": target,
        "targetLabel": availability[target]["label"],
        "source": {
            "image": session.name,
            "kind": session.kind,
            "revision": summary["revision"],
            "hardwareProfile": session.hardware_profile or {},
        },
        "entries": manifest_entries,
        "issues": issues,
        "canProceed": not any(item["severity"] == "error" for item in issues),
        "instructions": _instructions(session, target, payload),
        "compatibilityReport": compatibility,
    }
    report("Deployment plan validated", 4, 4)
    return plan, entries


def deployment_plan(service, session, payload: dict, progress: Callable | None = None) -> dict:
    """Build an exact plan from a disposable, hardware-finalised snapshot."""
    with prepared_snapshot(service, session, progress) as snapshot:
        plan, _entries = _deployment_plan(service, snapshot, payload, progress)
    # The revision protects the real session, not the disposable snapshot.
    plan["source"]["revision"] = service.summary(session)["revision"]
    return plan


def deployment_readme(plan: dict) -> str:
    lines = [
        f"# {plan['targetLabel']} deployment",
        "",
        f"Created by Amiga File Forge {plan['applicationVersion']}.",
        f"Source image: `{plan['source']['image']}`",
        f"Source revision: `{plan['source']['revision']}`",
        "",
        "## Installation",
        "",
    ]
    lines.extend(f"{index}. {step}" for index, step in enumerate(plan["instructions"], 1))
    lines.extend(["", "## Files", ""])
    lines.extend(
        f"- `{entry['path']}`: {entry['role']}, {entry['size']:,} bytes, SHA-256 `{entry['sha256']}`"
        for entry in plan["entries"]
    )
    lines.extend(["", "## Validation findings", ""])
    lines.extend(
        f"- {item['severity'].upper()}: {item['message']}" for item in plan["issues"]
    )
    if not plan["issues"]:
        lines.append("- No target-layout problems were detected.")
    lines.extend([
        "",
        "## Recovery",
        "",
        "Keep the previous working media unchanged until the new deployment has passed its read, write and reboot checks. Restore that backup if any check fails.",
        "",
    ])
    return "\n".join(lines)


def build_deployment_archive(service, session, payload: dict, output: Path, progress: Callable | None = None) -> dict:
    report = progress or (lambda _message, _current=None, _total=None: None)
    expected = str(payload.get("expectedRevision") or "")
    live_revision = service.summary(session)["revision"]
    if expected and expected != live_revision:
        raise DiskError("The image changed after deployment review. Build a new plan before downloading.")
    with prepared_snapshot(service, session, report) as snapshot:
        plan, entries = _deployment_plan(service, snapshot, payload, report)
        plan["source"]["revision"] = live_revision
        if not plan["canProceed"]:
            raise DiskError("The deployment plan contains blocking findings.")
        total = sum(entry.size for entry in entries)
        written = 0
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            for entry in entries:
                report(f"Adding {entry.path}", written, total)
                if entry.data is not None:
                    archive.writestr(entry.path, entry.data)
                    written += len(entry.data)
                else:
                    with entry.source.open("rb") as source, archive.open(entry.path, "w", force_zip64=True) as target:
                        while chunk := source.read(4 * 1024 * 1024):
                            target.write(chunk)
                            written += len(chunk)
                            report(f"Adding {entry.path}", written, total)
                report(f"Added {entry.path}", written, total)
            archive.writestr("README.md", deployment_readme(plan))
            archive.writestr("Deployment/manifest.json", json.dumps(plan, indent=2, ensure_ascii=False) + "\n")
            archive.writestr("Deployment/compatibility-report.md", plan["compatibilityReport"]["markdown"])
    report("Deployment package complete", total, total)
    return plan


__all__ = [
    "DEPLOYMENT_FORMAT",
    "DEPLOYMENT_VERSION",
    "available_deployment_targets",
    "build_deployment_archive",
    "deployment_plan",
    "deployment_readme",
]
