from __future__ import annotations

import csv
import io
import re
from collections import defaultdict, deque
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from .checksum import sha256_path
from .basic_listing import decode_basic
from .disk_service import DiskError
from .filename_policy import target_name_policy
from .operations import OperationCancelled
from . import amiga_paths


MAX_INSPECT_BYTES = 1024 * 1024
COMMAND_RE = re.compile(
    r"(?:\*\s*)?(CHAIN|EXEC|RUN|LOAD|DIR|LIB)\s*[\"']?([^\"'\s:\r]+)",
    re.IGNORECASE,
)


def _join(parent: str, name: str) -> str:
    return amiga_paths.join(parent, name)


def _row_size(row: dict) -> int:
    try:
        return int(row.get("length", row.get("size", 0)) or 0)
    except (TypeError, ValueError):
        return 0


def _walk(
    service,
    session,
    side: int | None = None,
    progress=None,
):
    # Every AmigaDOS volume starts at its root, whatever the DOS type is.
    pending = deque([""])
    visited = set()
    count = 0
    while pending:
        parent = pending.popleft()
        if parent.casefold() in visited:
            continue
        visited.add(parent.casefold())
        if progress:
            progress(f"Reading directory {parent}", count, None)
        listing = service.list_directory(session, parent, side)
        for row in listing["entries"]:
            path = _join(parent, str(row.get("name") or "Untitled"))
            yield path, row
            count += 1
            if count > 100_000:
                raise DiskError("The filesystem walk exceeded the 100,000-object safety limit.")
            if row.get("type") in {"dir", "directory"}:
                pending.append(path)


def inspect_file(
    service,
    session,
    path: str,
    side: int | None,
    progress=None,
) -> dict:
    report = progress or (lambda _message, _current=None, _total=None: None)
    report(f"Reading launcher {path}", 0, None)
    exported = service.export_file(session, path, side)
    try:
        size = exported.stat().st_size
        with exported.open("rb") as source:
            preview = source.read(MAX_INSPECT_BYTES)
        truncated = size > len(preview)
        digest = sha256_path(
            exported,
            (
                lambda current, total: report(
                    f"Checksumming launcher {path}", current, total
                )
            ) if progress else None,
        )
    finally:
        exported.unlink(missing_ok=True)
    basic = decode_basic(preview)
    printable = sum(value in (9, 10, 13) or 32 <= value < 127 for value in preview)
    looks_text = bool(preview) and printable / len(preview) >= 0.82
    if basic:
        text = "\n".join(f"{line.number} {line.text}" for line in basic)
        view = "basic"
    elif looks_text:
        text = preview.decode("latin-1", "replace").replace("\r", "\n")
        view = "text"
    else:
        text = ""
        view = "hex"
    commands = [
        {"action": action.upper(), "target": target}
        for action, target in COMMAND_RE.findall(text)
    ]
    hex_lines = []
    for offset in range(0, min(len(preview), 4096), 16):
        chunk = preview[offset : offset + 16]
        hex_lines.append(
            f"{offset:06X}  {' '.join(f'{value:02X}' for value in chunk):<47}  "
            + "".join(chr(value) if 32 <= value < 127 else "." for value in chunk)
        )
    return {
        "path": path,
        "size": size,
        "sha256": digest,
        "view": view,
        "text": text,
        "hex": "\n".join(hex_lines),
        "truncated": truncated,
        "editable": looks_text and not truncated and size <= 64 * 1024,
        "tokenisedBasic": basic is not None,
        "commands": commands,
    }


def dependency_report(
    service,
    session,
    path: str,
    side: int | None,
    progress=None,
) -> dict:
    inspected = inspect_file(service, session, path, side, progress)
    parent = amiga_paths.parent(path) if "." in path else "$"
    catalogue = [
        (candidate_path, row)
        for candidate_path, row in _walk(service, session, side, progress)
        if row.get("type") not in {"dir", "directory"}
    ]
    by_path = {candidate_path.casefold(): (candidate_path, row) for candidate_path, row in catalogue}
    by_leaf: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for candidate_path, row in catalogue:
        by_leaf[str(row.get("name") or amiga_paths.leaf(candidate_path)).casefold()].append((candidate_path, row))
    dependencies = []
    for command in inspected["commands"]:
        original_target = command["target"].strip()
        target = amiga_paths.leaf(original_target)
        rooted = original_target.startswith(("$", ":", "/")) or ":" in original_target
        relative_path = original_target if rooted else _join(parent, original_target)
        exact = by_path.get(relative_path.casefold())
        leaf_candidates = by_leaf.get(target.casefold(), [])
        found = exact or (leaf_candidates[0] if len(leaf_candidates) == 1 else None)
        dependencies.append({
            **command,
            "resolved": bool(found),
            "path": found[0] if found else None,
            "rootRelative": rooted,
            "ambiguous": not exact and len(leaf_candidates) > 1,
            "candidates": [candidate[0] for candidate in leaf_candidates[:20]],
        })
    unsafe = [item for item in dependencies if not item["resolved"] or item["rootRelative"] or item["ambiguous"]]
    return {
        "launcher": path,
        "dependencies": dependencies,
        "safeForSubdirectory": not unsafe,
        "warnings": [
            f"{item['action']} {item['target']} is "
            + ("root-relative" if item["rootRelative"] else "ambiguous" if item["ambiguous"] else "not present in the image")
            for item in unsafe
        ],
        "filesIndexed": len(catalogue),
    }


def build_manifest(service, session, progress=None) -> dict:
    if session.kind == "rom":
        records = []
        banks = service.list_rom_banks(session)
        for index, row in enumerate(banks):
            path = f"bank:{row['bank']}"
            if progress:
                progress(f"Checksumming ROM {path}", index, len(banks))
            exported = service.export_file(session, path)
            try:
                digest = sha256_path(
                    exported,
                    (lambda current, total: progress(
                        f"Checksumming ROM {path}", current, total
                    )) if progress else None,
                )
            finally:
                exported.unlink(missing_ok=True)
            records.append({
                "recordType": "rom-bank",
                "path": path,
                "bank": row["bank"],
                "title": row["name"],
                "size": row["length"],
                "romType": row["filetype"],
                "empty": row["empty"],
                "sha256": digest,
            })
        return {"image": service.summary(session), "records": records, "menus": []}

    records = []
    # A hard drive is described partition by partition, because a path is only
    # unique within the volume that holds it: two partitions may each have an
    # "S/Startup-Sequence" and they are different files.
    partitions = _manifest_partitions(service, session)
    for partition, device in partitions:
        if partition is not None:
            service.select_partition(session, partition)
            records.append({
                "recordType": "partition",
                "path": f"{device}:",
                "partition": partition,
                "device": device,
                "title": str(service.summary(session).get("title") or device),
            })
        sides = [0, 2] if service.is_two_volume_image(session) else [None]
        for side in sides:
            for path, row in _walk(service, session, side, progress):
                record = {
                    "recordType": "directory" if row.get("type") in {"dir", "directory"} else "file",
                    "path": path,
                    "side": side,
                    "partition": partition,
                    "size": _row_size(row),
                    "protection": row.get("protection"),
                    "comment": row.get("comment") or "",
                    "datestamp": row.get("datestamp") or "",
                    "attributes": row.get("attr", ""),
                }
                if record["recordType"] == "file":
                    try:
                        exported = service.export_file(session, path, side)
                        try:
                            record["sha256"] = sha256_path(
                                exported,
                                (lambda current, total: progress(
                                    f"Checksumming {path}", current, total
                                )) if progress else None,
                            )
                        finally:
                            exported.unlink(missing_ok=True)
                    except OperationCancelled:
                        raise
                    except DiskError as exc:
                        record["error"] = str(exc)
                records.append(record)
    return {"image": service.summary(session), "records": records, "menus": []}


def _manifest_partitions(service, session) -> list[tuple[int | None, str]]:
    """Every partition a manifest should walk, or one unpartitioned volume.

    The session's own partition selection is restored by the caller's normal
    refresh; nothing here leaves it pointing somewhere the user did not choose,
    because a manifest is a read-only report.
    """
    if session.kind != "hdf":
        return [(None, "")]
    try:
        partitions = service.list_partitions(session)
    except DiskError:
        return [(None, "")]
    if not partitions:
        return [(None, "")]
    return [
        (
            index,
            str(partition.get("device") or partition.get("name") or f"DH{index}"),
        )
        for index, partition in enumerate(partitions)
    ]


def manifest_csv(manifest: dict) -> str:
    keys = sorted({key for row in manifest["records"] for key in row})
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=keys, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(manifest["records"])
    return output.getvalue()


def _project_offset(value) -> int | None:
    try:
        text = str(value).strip()
        return int(text[1:], 16) if text.startswith("&") else int(text, 0)
    except (TypeError, ValueError):
        return None


def workspace_metadata_records(service, session) -> list[dict]:
    """Return bounded partition and saved-project records for workspace search."""
    records: list[dict] = []
    if session.kind == "hdf":
        try:
            partitions = service.list_partitions(session)
        except DiskError:
            partitions = []
        for index, partition in enumerate(partitions):
            device = str(partition.get("device") or partition.get("name") or "")
            records.append({
                "virtual": True,
                "resultType": "partition",
                "kind": "partition",
                "name": device,
                "path": device,
                "openable": True,
                "partition": index,
                "searchFields": {
                    "device": device,
                    "filing system": partition.get("format"),
                    "size": partition.get("sizeBytes"),
                    "bootable": partition.get("bootable"),
                },
            })
    if session.kind == "rom":
        project = session.rom_project or {}
        identity = project.get("identity") or {}
        records.append({
            "virtual": True, "resultType": "rom-project", "kind": "rom-project",
            "name": str(identity.get("title") or session.name), "path": "ROM project",
            "openable": False, "romProject": True,
            "searchFields": {
                "title": identity.get("title"), "version": identity.get("version"),
                "publisher": identity.get("publisher"), "platform": identity.get("platform"),
                "identity notes": identity.get("notes"), "project notes": project.get("notes"),
                "hardware": project.get("hardware"),
            },
        })
        for address, label in dict(project.get("symbols") or {}).items():
            records.append({
                "virtual": True, "resultType": "rom-symbol", "kind": "rom-project",
                "name": str(label), "path": "ROM project symbols", "openable": False,
                "romProject": True, "romTab": "code", "address": address,
                "searchFields": {"symbol": label, "address": address},
            })
        for region in project.get("regions") or []:
            records.append({
                "virtual": True, "resultType": "rom-region", "kind": "rom-project",
                "name": str(region.get("name") or "ROM region"), "path": "ROM project regions",
                "openable": False, "romProject": True, "romTab": "code",
                "address": region.get("start"),
                "searchFields": {
                    "region": region.get("name"), "start": region.get("start"),
                    "end": region.get("end"),
                },
            })
    for key, project in list((session.editor_projects or {}).items())[:4096]:
        parts = str(key).split("|", 1)
        if len(parts) != 2:
            continue
        side_text, path = parts
        context = {
            "path": path,
            **({"side": int(side_text)} if side_text != "-" else {}),
        }
        common = {
            "virtual": True, "kind": "project", "path": path,
            "fileName": amiga_paths.leaf(path), "openable": True, **context,
        }
        if project.get("notes"):
            records.append({
                **common, "resultType": "project-notes", "name": amiga_paths.leaf(path),
                "searchFields": {"project notes": project.get("notes")},
            })
        for offset, label in dict(project.get("symbols") or {}).items():
            parsed_offset = _project_offset(offset)
            if parsed_offset is None:
                continue
            records.append({
                **common, "resultType": "project-symbol", "name": str(label),
                "offset": parsed_offset, "searchFields": {"symbol": label, "offset": offset},
            })
        for offset, comment in dict(project.get("comments") or {}).items():
            parsed_offset = _project_offset(offset)
            if parsed_offset is None:
                continue
            records.append({
                **common, "resultType": "project-comment", "name": amiga_paths.leaf(path),
                "offset": parsed_offset, "searchFields": {"comment": comment, "offset": offset},
            })
    return records[:20_000]


def duplicate_report(service, session, progress=None) -> dict:
    manifest = build_manifest(service, session, progress)
    exact: dict[str, list[dict]] = defaultdict(list)
    variants: dict[str, list[dict]] = defaultdict(list)
    for row in manifest["records"]:
        if row.get("sha256"):
            exact[str(row["sha256"])].append(row)
        if row.get("recordType") == "file" and row.get("diskTitle"):
            continue
        title = str(row.get("diskTitle") or row.get("path") or "")
        key = re.sub(r"[^a-z0-9]", "", re.sub(r"(?:disc|disk|side|v|rev)[-_ ]?[0-9a-z]+$", "", title.casefold()))
        if key:
            variants[key].append(row)
    result = {
        "exact": [items for items in exact.values() if len(items) > 1],
        "variants": [items for items in variants.values() if len(items) > 1],
    }
    return result


def health_report(service, session, progress=None) -> dict:
    checks = []
    repairable = []
    def check(name, function):
        try:
            detail = function()
            checks.append({"name": name, "status": "pass", "detail": str(detail)})
        except OperationCancelled:
            raise
        except Exception as exc:
            checks.append({"name": name, "status": "fail", "detail": str(exc)})

    if session.kind == "hdf":
        if progress:
            progress("Reading the partition table", 0, None)
        partitions = service.list_partitions(session)
        check(
            "Rigid Disk Block",
            lambda: f"{len(partitions)} partition(s) declared",
        )
        unmountable = [
            partition for partition in partitions if not partition.get("automount")
        ]
        checks.append({
            "name": "Automounting partitions",
            "status": "warn" if unmountable else "pass",
            "detail": (
                f"{len(unmountable)} partition(s) are marked no-mount"
                if unmountable
                else "every partition mounts automatically"
            ),
        })
        sandbox_project = service.editor_project(session, "drive", None)
        sandbox_project = sandbox_project if isinstance(sandbox_project, dict) else {}
        sandbox_runs = [
            row
            for row in sandbox_project.get("tests", [])
            if row.get("kind") == "drive-sandbox"
        ]
        source_path = getattr(session, "path", None)
        current_hash = (
            sha256_path(source_path)
            if isinstance(source_path, Path) and source_path.is_file()
            else ""
        )
        current_runs = [
            row for row in sandbox_runs if row.get("sourceSha256") == current_hash
        ]
        latest_run = current_runs[-1] if current_runs else None
        checks.append({
            "name": "Whole-drive emulator evidence",
            "status": (
                "pass"
                if latest_run
                and latest_run.get("inputChangedDisplay")
                and latest_run.get("repeatable")
                else "warn"
            ),
            "detail": (
                latest_run.get("summary", "Current image captured in the sandbox")
                if latest_run
                else (
                    "No isolated emulator capture matches the current drive "
                    "revision. Run the whole drive to record one."
                )
            ),
            "findings": (
                [{
                    "time": latest_run.get("time"),
                    "machine": latest_run.get("machine"),
                    "frameHashes": latest_run.get("frameHashes", []),
                    "changedPixels": latest_run.get("changedPixels"),
                    "repeatable": bool(latest_run.get("repeatable")),
                }]
                if latest_run
                else []
            ),
        })
    elif session.kind == "rom":
        if progress:
            progress("Inspecting ROM banks and headers", 0, None)
        rows = service.list_rom_banks(session)
        check("ROM byte structure", lambda: service.validate(session))
        checks.append({
            "name": "Recognised Amiga ROM headers",
            "status": "pass" if any(row["header"] or row.get("extensionHeader") for row in rows) else "warn",
            "detail": (
                f"{sum(bool(row['header']) for row in rows)} of {len(rows)} bank(s) "
                "carry a recognised Amiga-family header; "
                f"{sum(bool(row.get('extensionHeader')) for row in rows)} AmigaOS extension trailer(s) found"
            ),
        })
        bad_extension_checksums = [
            row for row in rows
            if row.get("extensionHeader") and not row["extensionHeader"]["checksumValid"]
        ]
        if bad_extension_checksums:
            checks.append({
                "name": "AmigaOS extension ROM checksum",
                "status": "fail",
                "detail": "The ExtnROM0 trailer checksum does not match the image bytes.",
            })
        duplicate_groups = [
            [row["bank"], *row.get("matchingBanks", [])]
            for row in rows
            if row.get("matchingBanks") and row["bank"] < min(row["matchingBanks"])
        ]
        checks.append({
            "name": "Bank fingerprints",
            "status": "warn" if duplicate_groups else "pass",
            "detail": (
                "; ".join("Identical banks " + ", ".join(map(str, group)) for group in duplicate_groups)
                if duplicate_groups else "Every bank has a distinct SHA-256 fingerprint"
            ),
        })
        header_warnings = [
            f"Bank {row['bank']}: {warning}"
            for row in rows for warning in row.get("warnings", [])
        ]
        checks.append({
            "name": "Header flag consistency",
            "status": "warn" if header_warnings else "pass",
            "detail": (
                f"{len(header_warnings)} header/vector disagreement(s)"
                if header_warnings else "Recognised header flags agree with their entry vectors"
            ),
            "findings": header_warnings,
        })
        partial = session.path.stat().st_size % session.rom_bank_size
        checks.append({
            "name": "Bank boundaries",
            "status": "warn" if partial else "pass",
            "detail": (
                f"Final bank contains {partial:,} bytes"
                if partial else f"All banks are {session.rom_bank_size:,} bytes"
            ),
        })
    else:
        if progress:
            progress("Validating the filesystem structure", 0, None)
        check("Filesystem structure", lambda: service.validate(session))
        def catalogue_count():
            sides = [0, 2] if service.is_two_volume_image(session) else [None]
            count = sum(
                1
                for side in sides
                for _path, _row in _walk(service, session, side, progress)
            )
            return f"{count} objects"
        check("Filesystem catalogue", catalogue_count)
    if session.descriptor_path:
        check("Hardfile HDA/GEO geometry", lambda: service.stat(session).get("description", "valid"))
    warnings = [*session.warnings, *(list(session.dms.warnings) if session.dms else [])]
    profile = session.hardware_profile or {}
    if profile:
        additions = ", ".join(profile.get("addons") or []) or "stock machine"
        checks.append({
            "name": "Hardware profile",
            "status": "pass",
            "detail": f"{profile.get('name', 'Custom')} · {profile.get('machine', 'Amiga')} · {profile.get('filingSystem', 'automatic')} · {additions}",
        })
        if profile.get("accelerated") and session.kind == "hdf":
            checks.append({
                "name": "Accelerator compatibility",
                "status": "warn",
                "detail": "Many OCS and ECS titles depend on 68000 timing or on running from Chip RAM, "
                          "and need the accelerator and its Fast RAM disabled before launch.",
            })
    checks.extend({"name": "Compatibility warning", "status": "warn", "detail": warning} for warning in warnings)
    score = "healthy" if all(item["status"] == "pass" for item in checks) else (
        "attention" if not any(item["status"] == "fail" for item in checks) else "failed"
    )
    if progress:
        progress("Health check complete", 1, 1)
    return {"status": score, "checks": checks, "repairable": repairable}


COMPATIBILITY_REPORT_FORMAT = "amiga-file-forge-compatibility-report"
COMPATIBILITY_REPORT_VERSION = 1


def accept_compatibility_report(service, session, document: dict) -> dict:
    """Regenerate and retain one reviewed report for the next saved package."""
    if not isinstance(document, dict):
        raise DiskError("The compatibility report is not a JSON object.")
    if (
        document.get("format") != COMPATIBILITY_REPORT_FORMAT
        or document.get("version") != COMPATIBILITY_REPORT_VERSION
    ):
        raise DiskError(
            f"Only {COMPATIBILITY_REPORT_FORMAT} version "
            f"{COMPATIBILITY_REPORT_VERSION} reports can be retained."
        )
    if not document.get("dryRun") or not isinstance(document.get("changes"), list):
        raise DiskError("Only a complete dry-run compatibility report can be retained.")
    if not isinstance(document.get("source"), dict) or not isinstance(document.get("target"), dict):
        raise DiskError("The compatibility report source or target is incomplete.")
    target = document.get("target") or {}
    if target.get("image") != session.name or target.get("kind") != session.kind:
        raise DiskError("The compatibility report belongs to a different image or filesystem.")
    report = preflight_report(
        service,
        session,
        {
            "operation": document.get("operation"),
            "changes": deepcopy(document["changes"]),
            "sourceKind": document["source"].get("kind"),
            "targetKind": target.get("kind"),
        },
    )
    if not report["canProceed"]:
        raise DiskError("Resolve the report's blocking findings before accepting it.")
    report["acceptedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    report["acceptedImage"] = {
        "name": session.name,
        "kind": session.kind,
        "size": session.path.stat().st_size,
        "modifiedNs": session.path.stat().st_mtime_ns,
    }
    session.compatibility_reports = [*session.compatibility_reports[-9:], report]
    return report


def compatibility_report_markdown(report: dict) -> str:
    lines = [
        "# Amiga File Forge compatibility report",
        "",
        f"Operation: {report['operation']}",
        f"Source: {report['source']['kind']}",
        f"Target: {report['target']['kind']}",
        f"Can proceed: {'yes' if report['canProceed'] else 'no'}",
        "",
        "## Items",
        "",
    ]
    for item in report["items"]:
        lines.append(f"### {item['sourceName'] or 'Unnamed item'}")
        lines.append("")
        lines.append(f"- Target name: `{item['targetName']}`")
        lines.append(f"- Type: {item['type']}")
        lines.append(f"- Protection: {item['metadata']['protection'] or 'not supplied'}")
        lines.append(f"- Comment: {item['metadata']['comment'] or 'none'}")
        for conversion in item["conversions"]:
            lines.append(f"- Conversion: {conversion}")
        for loss in item["losses"]:
            lines.append(f"- Metadata loss: {loss}")
        lines.append("")
    lines.extend(["## Findings", ""])
    lines.extend(
        f"- {finding['severity'].upper()}: {finding['message']}"
        for finding in report["issues"]
    )
    if not report["issues"]:
        lines.append("- No compatibility findings.")
    return "\n".join(lines) + "\n"


def preflight_report(service, session, payload: dict) -> dict:
    operation = str(payload.get("operation") or "review")
    changes = list(payload.get("changes") or [])
    issues = []
    items = []
    seen = set()
    target_kind = str(payload.get("targetKind") or session.kind).strip().lower()
    source_kind = str(payload.get("sourceKind") or session.kind).strip().lower()
    for offset, change in enumerate(changes):
        name = str(change.get("name") or change.get("destination") or "")
        leaf = name if change.get("nameIsLeaf") else amiga_paths.leaf(name)
        item_type = str(change.get("type") or "file").strip().lower()
        capabilities = getattr(session, "ffs_capabilities", {}) or {}
        policy = target_name_policy(
            target_kind,
            item_type=item_type,
            name_limit=capabilities.get("nameLimit"),
        )
        validate_name = not change.get("existingDestination")
        normal = policy.normalise(leaf) if validate_name else leaf
        conversions = []
        losses = []
        if validate_name and normal != leaf:
            issues.append({"severity": "warning", "item": offset, "message": f"{leaf} becomes {normal or 'FILE'}"})
            conversions.append(f"Filename {leaf} becomes {normal or 'FILE'}")
        if change.get("nameIsLeaf"):
            parent = str(change.get("parent") or change.get("targetParent") or "")
        else:
            parent = amiga_paths.parent(name) if "." in name else ""
        key = (parent.casefold(), normal.casefold())
        if validate_name and key in seen and not change.get("allowDuplicateName"):
            issues.append({"severity": "error", "item": offset, "message": f"{normal} clashes after target-name conversion"})
        seen.add(key)
        # Every AmigaDOS DOS type nests drawers, so a directory is never lost
        # in a copy between them. What can be lost is the Workbench icon: a
        # destination with no room for the companion .info file keeps the
        # drawer but not its appearance.
        if change.get("filetype") and target_kind in {"dms", "rom", "kickfs"}:
            losses.append(
                "A Workbench icon type has nowhere to live in the target, so it is dropped."
            )
        items.append({
            "index": offset,
            "sourceName": str(change.get("sourceName") or leaf),
            "targetName": normal or "FILE",
            "source": str(change.get("source") or ""),
            "type": item_type,
            "metadata": {
                "protection": str(change.get("protection") or ""),
                "comment": str(change.get("comment") or ""),
                "access": str(change.get("access") or change.get("attr") or ""),
                "filetype": str(change.get("filetype") or ""),
            },
            "conversions": conversions,
            "losses": losses,
        })
    report = {
        "format": COMPATIBILITY_REPORT_FORMAT,
        "version": COMPATIBILITY_REPORT_VERSION,
        "operation": operation,
        "dryRun": True,
        "source": {"kind": source_kind},
        "target": {
            "kind": target_kind,
            "image": session.name,
            "hardwareProfile": str((session.hardware_profile or {}).get("name") or ""),
        },
        "changes": changes,
        "items": items,
        "issues": issues,
        "canProceed": not any(item["severity"] == "error" for item in issues),
        "summary": f"{len(changes)} proposed changes, {len(issues)} findings",
    }
    report["markdown"] = compatibility_report_markdown(report)
    return report
