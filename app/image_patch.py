from __future__ import annotations

import json
import re
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path

from .analysis_service import build_manifest
from .checksum import sha256_copy, sha256_stream
from .disk_service import DiskError
from .image_diff import compare_manifests, manifest_fingerprint, record_key
from .ffs_items import delete_ffs_items
from . import amiga_paths


PATCH_FORMAT = "amiga-file-forge-image-patch"
PATCH_VERSION = 1
MAX_OPERATIONS = 100_000
MAX_PATCH_UNCOMPRESSED_BYTES = 9 * 1024 * 1024 * 1024
MAX_PATCH_DOCUMENT_BYTES = 64 * 1024 * 1024
PATCH_ACTIONS = frozenset({"added", "removed", "modified", "metadata"})
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def _session_label(session) -> str:
    """Return a stable human-readable label for real and lightweight sessions."""
    name = getattr(session, "name", None)
    if name:
        return str(name)
    path = getattr(session, "path", None)
    if path:
        return Path(path).name
    return f"{str(getattr(session, 'kind', 'image')).upper()} image"


def _layout_signature(manifest: dict) -> dict:
    """Return the physical traits that can change how logical paths are addressed."""
    image = manifest.get("image", {})
    signature = {"kind": image.get("kind")}
    if image.get("kind") == "ofs":
        signature["doubleSided"] = bool(image.get("doubleSided"))
    elif image.get("kind") == "rom":
        signature["bankSize"] = (image.get("rom") or {}).get("bankSize")
    return signature


def _patch_changes(kind: str, comparison: dict):
    for action in ("removed", "added", "modified", "metadata"):
        for change in comparison["changes"][action]:
            row = change.get("after") or change.get("before") or {}
            record_type = row.get("recordType")
            if kind == "rom" and record_type != "rom-bank":
                continue
            yield action, change, row
    for change in comparison["changes"].get("renamed", []):
        before, after = change["before"], change["after"]
        if kind == "rom":
            continue
        for action, row in (("removed", before), ("added", after)):
            operation_change = {
                "key": record_key(row),
                "before": before if action == "removed" else None,
                "after": after if action == "added" else None,
                "changedFields": ["path"],
            }
            yield action, operation_change, row


def _change_patchable(kind: str, change: dict) -> bool:
    row = change.get("after") or change.get("before") or {}
    if kind == "rom":
        return row.get("recordType") == "rom-bank"
    return kind != "dms"


def _parent_paths(path: str) -> list[str]:
    parts = amiga_paths.split(path)
    return [
        amiga_paths.SEPARATOR.join(parts[:index])
        for index in range(1, len(parts))
    ]


def _selected_candidate_manifest(
    kind: str,
    base: dict,
    candidate: dict,
    comparison: dict,
    selected_keys: list[str],
) -> tuple[dict, dict]:
    """Build a dependency-closed candidate for a reviewed logical subset."""
    changes = {
        change["key"]: (category, change)
        for category, rows in comparison["changes"].items()
        for change in rows
        if _change_patchable(kind, change)
    }
    requested = {str(key) for key in selected_keys if str(key)}
    unknown = sorted(requested - changes.keys())
    if unknown:
        raise DiskError(f"The selected patch item is unavailable or dependent-only: {unknown[0]}.")
    if not requested:
        raise DiskError("Select at least one independent change for a selective patch.")
    selected = set(requested)
    by_after_path = {
        str(change.get("after", {}).get("path") or "").casefold(): key
        for key, (category, change) in changes.items()
        if category in {"added", "renamed"} and change.get("after", {}).get("path")
    }
    expanded = True
    while expanded:
        expanded = False
        for key in list(selected):
            category, change = changes[key]
            after_path = str(change.get("after", {}).get("path") or "")
            if category in {"added", "renamed"}:
                for parent in _parent_paths(after_path):
                    dependency = by_after_path.get(parent.casefold())
                    if dependency and dependency not in selected:
                        selected.add(dependency)
                        expanded = True
            before = change.get("before") or {}
            if category == "removed" and before.get("recordType") == "directory":
                prefix = str(before.get("path") or "").casefold() + "."
                for dependency, (other_category, other) in changes.items():
                    other_path = str((other.get("before") or {}).get("path") or "").casefold()
                    if other_category == "removed" and other_path.startswith(prefix) and dependency not in selected:
                        selected.add(dependency)
                        expanded = True

    base_records = {record_key(row): dict(row) for row in base.get("records", [])}
    candidate_records = [dict(row) for row in candidate.get("records", [])]
    candidate_by_key = {record_key(row): row for row in candidate_records}
    for key in sorted(selected):
        category, change = changes[key]
        before, after = change.get("before"), change.get("after")
        if category in {"removed", "renamed"} and before:
            base_records.pop(record_key(before), None)
        if category in {"added", "renamed", "modified", "metadata"} and after:
            base_records[record_key(after)] = candidate_by_key.get(
                record_key(after), dict(after)
            )
    derived = {
        "image": candidate.get("image", {}),
        "records": sorted(base_records.values(), key=record_key),
    }
    return derived, {
        "requestedKeys": sorted(requested),
        "includedKeys": sorted(selected),
        "automaticallyIncludedKeys": sorted(selected - requested),
    }


def _payload_required(kind: str, operation: dict) -> bool:
    row = operation.get("after") or operation.get("before") or {}
    return (
        operation.get("action") in {"added", "modified"}
        and row.get("recordType") != "directory"
    )


def _member_sha256(archive: zipfile.ZipFile, name: str, progress=None) -> str:
    with archive.open(name) as source:
        return sha256_stream(source, progress)


def _extract_payload(
    archive: zipfile.ZipFile,
    name: str,
    work_dir: Path,
    progress=None,
) -> Path:
    """Stream one already-verified member to disk without retaining it in RAM."""
    with tempfile.NamedTemporaryFile(dir=work_dir, prefix="patch-file-", delete=False) as temporary:
        temporary_path = Path(temporary.name)
        try:
            with archive.open(name) as source:
                processed = 0
                total = archive.getinfo(name).file_size
                for chunk in iter(lambda: source.read(8 * 1024 * 1024), b""):
                    temporary.write(chunk)
                    processed += len(chunk)
                    if progress:
                        progress(processed, total)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
    return temporary_path


def _read_patch_document(archive: zipfile.ZipFile) -> dict:
    names = archive.namelist()
    if len(names) > MAX_OPERATIONS + 1 or "patch.json" not in names:
        raise DiskError("This is not a valid Amiga File Forge patch archive.")
    if len(names) != len(set(names)):
        raise DiskError("The patch archive contains duplicate member names.")
    if any(name.startswith("/") or ".." in Path(name).parts for name in names):
        raise DiskError("The patch archive contains an unsafe member path.")
    if sum(item.file_size for item in archive.infolist()) > MAX_PATCH_UNCOMPRESSED_BYTES:
        raise DiskError("The expanded patch archive exceeds the 9 GiB safety limit.")
    if archive.getinfo("patch.json").file_size > MAX_PATCH_DOCUMENT_BYTES:
        raise DiskError("The patch operation document exceeds the 64 MiB safety limit.")
    document = json.loads(archive.read("patch.json"))
    if document.get("format") != PATCH_FORMAT or document.get("version") != PATCH_VERSION:
        raise DiskError("This patch format or version is not supported.")
    operations = document.get("operations")
    if not isinstance(operations, list) or len(operations) > MAX_OPERATIONS:
        raise DiskError("The patch operation list is invalid or too large.")
    for field in ("baseFingerprint", "candidateFingerprint"):
        fingerprint = str(document.get(field) or "").lower()
        if not SHA256_PATTERN.fullmatch(fingerprint):
            raise DiskError(f"The patch has an invalid {field}.")
        document[field] = fingerprint
    expected_payloads = set()
    for index, operation in enumerate(operations, start=1):
        if not isinstance(operation, dict) or operation.get("action") not in PATCH_ACTIONS:
            raise DiskError(f"Patch operation {index} has an invalid action.")
        if not isinstance(operation.get("after") or operation.get("before"), dict):
            raise DiskError(f"Patch operation {index} has no logical record.")
        if not _payload_required(str(document.get("kind") or ""), operation):
            continue
        name = str(operation.get("payload") or "")
        checksum = str(operation.get("payloadSha256") or "").lower()
        if not name.startswith("payloads/") or name not in names:
            raise DiskError(f"Patch operation {index} is missing its payload.")
        if not SHA256_PATTERN.fullmatch(checksum):
            raise DiskError(f"Patch operation {index} has an invalid payload checksum.")
        operation["payloadSha256"] = checksum
        expected_payloads.add(name)
    unexpected = set(names) - {"patch.json"} - expected_payloads
    if unexpected:
        raise DiskError(f"The patch archive contains an unexpected member: {sorted(unexpected)[0]}.")
    return document


def _validate_operation_plan(kind: str, document: dict, current: dict) -> None:
    """Prove that the advertised operations are the canonical candidate diff."""
    candidate_records = document.get("candidateRecords")
    if not isinstance(candidate_records, list) or not all(
        isinstance(record, dict) for record in candidate_records
    ):
        raise DiskError("The patch has no verifiable candidate manifest.")
    candidate_keys = [record_key(record) for record in candidate_records]
    if len(candidate_keys) != len(set(candidate_keys)):
        raise DiskError("The patch candidate manifest contains duplicate logical records.")
    candidate = {"image": document.get("candidate", {}), "records": candidate_records}
    if manifest_fingerprint(candidate) != document["candidateFingerprint"]:
        raise DiskError("The patch candidate manifest does not match its fingerprint.")
    comparison = compare_manifests(current, candidate)
    expected = [
        {
            "action": action,
            "key": change["key"],
            "before": change.get("before"),
            "after": change.get("after"),
            "changedFields": change.get("changedFields", []),
        }
        for action, change, _row in _patch_changes(kind, comparison)
    ]
    actual = [
        {
            "action": operation.get("action"),
            "key": operation.get("key"),
            "before": operation.get("before"),
            "after": operation.get("after"),
            "changedFields": operation.get("changedFields", []),
        }
        for operation in document["operations"]
    ]
    if actual != expected:
        raise DiskError("The patch operation plan does not match its base and candidate manifests.")
    if document.get("summary") != comparison["summary"]:
        raise DiskError("The patch change summary does not match its operation plan.")


def _preflight_patch(service, session, archive: zipfile.ZipFile, progress=None) -> tuple[dict, dict]:
    report = progress or (lambda _message, _current=None, _total=None: None)
    report("Reading and validating the patch plan", 0, None)
    document = _read_patch_document(archive)
    if document.get("kind") != session.kind:
        raise DiskError(f"This patch targets {document.get('kind')}, not the open {session.kind} image.")
    report(f"Cataloguing the open {session.kind.upper()} image", 0, None)
    current = build_manifest(service, session, report)
    if document.get("layout") and _layout_signature(current) != document.get("layout"):
        raise DiskError("This patch targets a different OFS side layout or ROM bank size.")
    if manifest_fingerprint(current) != document.get("baseFingerprint"):
        raise DiskError("The open image does not match this patch's exact base revision.")
    report("Checking the canonical operation plan", 0, None)
    _validate_operation_plan(session.kind, document, current)
    payloads = [
        operation for operation in document["operations"]
        if _payload_required(session.kind, operation)
    ]
    total_bytes = sum(archive.getinfo(operation["payload"]).file_size for operation in payloads)
    verified_bytes = 0
    for index, operation in enumerate(payloads, start=1):
        name = operation["payload"]
        size = archive.getinfo(name).file_size
        message = f"Verifying patch payload {index} of {len(payloads)}"
        report(message, verified_bytes, total_bytes)
        checksum = _member_sha256(
            archive,
            name,
            lambda current: report(message, verified_bytes + current, total_bytes),
        )
        if checksum != operation["payloadSha256"]:
            raise DiskError(f"Patch payload for operation {index} failed its SHA-256 check.")
        verified_bytes += size
    report("Patch preflight complete", total_bytes, total_bytes)
    return document, current


def inspect_patch_archive(service, session, archive_path: Path, progress=None) -> dict:
    """Verify a patch completely without changing the open image."""
    try:
        with zipfile.ZipFile(archive_path) as archive:
            document, _current = _preflight_patch(service, session, archive, progress)
            payload_names = {
                operation["payload"]
                for operation in document["operations"]
                if _payload_required(session.kind, operation)
            }
            payload_bytes = sum(archive.getinfo(name).file_size for name in payload_names)
    except zipfile.BadZipFile as exc:
        raise DiskError("The selected patch is not a readable ZIP archive.") from exc
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DiskError(f"The patch document is incomplete or invalid: {exc}") from exc
    return {
        "compatible": True,
        "base": document.get("base", {}),
        "candidate": document.get("candidate", {}),
        "summary": document.get("summary", {}),
        "operationCount": len(document["operations"]),
        "payloadCount": len(payload_names),
        "payloadBytes": payload_bytes,
        "operations": document["operations"][:200],
        "truncated": len(document["operations"]) > 200,
        "selection": document.get("selection"),
    }


@contextmanager
def _candidate_payload_path(service, session, row: dict):
    """Expose one candidate payload as a path and clean generated exports."""
    exported = service.export_file(
        session,
        str(row["path"]),
        int(row["side"]) if row.get("side") is not None else None,
    )
    try:
        yield exported
    finally:
        exported.unlink(missing_ok=True)


def write_patch_archive(
    service,
    base_session,
    candidate_session,
    destination: Path,
    progress=None,
    selected_keys: list[str] | None = None,
) -> dict:
    report = progress or (lambda _message, _current=None, _total=None: None)
    report(f"Cataloguing base image {_session_label(base_session)}", 0, None)
    base = build_manifest(service, base_session, report)
    report(f"Cataloguing candidate image {_session_label(candidate_session)}", 0, None)
    candidate = build_manifest(service, candidate_session, report)
    report("Comparing logical contents and metadata", 0, None)
    comparison = compare_manifests(base, candidate)
    if not comparison["sameFormat"]:
        raise DiskError("Patch sets require two images from the same filesystem family.")
    if _layout_signature(base) != _layout_signature(candidate):
        raise DiskError("Patch sets require matching OFS side layouts or ROM bank sizes.")
    if base_session.kind == "dms":
        raise DiskError("DMS DMS archives are read-only and cannot receive patch sets.")
    selection = None
    if selected_keys is not None:
        candidate, selection = _selected_candidate_manifest(
            base_session.kind, base, candidate, comparison, selected_keys
        )
        comparison = compare_manifests(base, candidate)

    operation_count = sum(1 for _item in _patch_changes(base_session.kind, comparison))
    if operation_count > MAX_OPERATIONS:
        raise DiskError(f"Patch sets are limited to {MAX_OPERATIONS:,} logical operations.")
    operations = []
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
        payload_count = 0
        for operation_index, (action, change, row) in enumerate(
            _patch_changes(base_session.kind, comparison),
            start=1,
        ):
            label = row.get("path") or row.get("diskTitle") or change["key"]
            report(
                f"Writing patch operation {operation_index} of {operation_count} · {label}",
                operation_index - 1,
                operation_count,
            )
            operation = {
                "action": action,
                "key": change["key"],
                "before": change.get("before"),
                "after": change.get("after"),
                "changedFields": change.get("changedFields", []),
            }
            if _payload_required(base_session.kind, operation):
                payload_name = f"payloads/{payload_count:08d}.bin"
                with _candidate_payload_path(service, candidate_session, row) as source:
                    size = source.stat().st_size
                    with source.open("rb") as payload_source, archive.open(
                        payload_name,
                        "w",
                        force_zip64=True,
                    ) as payload_target:
                        checksum = sha256_copy(
                            payload_source,
                            payload_target,
                            lambda current: report(
                                f"Compressing {label}",
                                current,
                                size,
                            ),
                        )
                    expected = str(row.get("sha256") or "").lower()
                    if SHA256_PATTERN.fullmatch(expected) and checksum != expected:
                        raise DiskError(
                            f"{row.get('path') or row.get('diskTitle') or operation['key']} "
                            "changed while the patch was being built. Compare the images again."
                        )
                operation["payload"] = payload_name
                operation["payloadSha256"] = checksum
                payload_count += 1
            operations.append(operation)
        document = {
            "format": PATCH_FORMAT,
            "version": PATCH_VERSION,
            "kind": base_session.kind,
            "base": comparison["base"],
            "candidate": comparison["candidate"],
            "baseFingerprint": comparison["baseFingerprint"],
            "candidateFingerprint": comparison["candidateFingerprint"],
            "layout": _layout_signature(base),
            "candidateRecords": candidate.get("records", []),
            "summary": comparison["summary"],
            "operations": operations,
            **({"selection": selection} if selection else {}),
        }
        archive.writestr("patch.json", json.dumps(document, indent=2, ensure_ascii=False))
    report("Guarded patch archive is ready", operation_count, operation_count)
    return document


def _remove_filesystem_record(service, session, row: dict) -> None:
    path = str(row["path"])
    if session.kind in {"ffs", "ofs"}:
        delete_ffs_items(service, session, [path])
        return
    arguments = ["rm", "--force"]
    if row.get("recordType") == "directory":
        arguments.append("--recursive")
    arguments.append("{image}:" + path)
    service.mutate(
        session,
        arguments,
        int(row["side"]) if row.get("side") is not None else None,
    )


def _protection_text(value) -> str:
    """Render a manifest protection value as the text a metadata edit takes."""
    if isinstance(value, int):
        return f"{value:X}"
    return str(value or "0")


def _apply_access(service, session, row: dict) -> None:
    path = str(row["path"])
    side = int(row["side"]) if row.get("side") is not None else None
    attributes = str(row.get("attributes") or "")
    if attributes:
        normalised = attributes.upper()
        writable = "RUN" not in normalised if session.kind == "kickfs" else "L" not in normalised
        service.set_access(session, [path], writable, side)


def _apply_metadata(service, session, row: dict) -> None:
    if row.get("recordType") == "directory":
        _apply_access(service, session, row)
        return
    service.set_file_metadata(
        session,
        str(row["path"]),
        _protection_text(row.get("protection")),
        str(row.get("comment") or ""),
        int(row["side"]) if row.get("side") is not None else None,
        # A patch reproduces the image it was taken from, so the entry keeps
        # the datestamp the candidate recorded rather than the moment the
        # patch happened to be applied.
        str(row.get("datestamp") or "") or None,
    )
    _apply_access(service, session, row)


def _apply_normal_patch(
    service,
    session,
    operations: list[dict],
    archive: zipfile.ZipFile,
    progress=None,
) -> None:
    report = progress or (lambda _message, _current=None, _total=None: None)
    removal_actions = {"removed", "modified"} if session.kind == "ofs" else {"removed"}
    removals = [item for item in operations if item["action"] in removal_actions]
    removals.sort(key=lambda item: (item["before"].get("recordType") == "directory", -str(item["before"].get("path") or "").count(".")))
    metadata = [item for item in operations if item["action"] == "metadata"]
    additions = [
        item for item in operations if item["action"] in {"added", "modified"}
    ]
    total_steps = len(removals) + len(additions) + len(metadata)
    completed = 0
    for operation in removals:
        report(f"Removing {operation['before'].get('path')}", completed, total_steps)
        _remove_filesystem_record(service, session, operation["before"])
        completed += 1

    additions.sort(key=lambda item: (item["after"].get("recordType") != "directory", str(item["after"].get("path") or "").count(".")))
    for operation in additions:
        row = operation["after"]
        report(f"Writing {row.get('path')}", completed, total_steps)
        if row.get("recordType") == "directory":
            if session.kind not in {"ffs", "ofs"}:
                raise DiskError("This patch contains a directory for a flat filesystem.")
            service.make_directory(session, str(row["path"]))
            _apply_access(service, session, row)
            completed += 1
            continue
        temporary_path = _extract_payload(
            archive,
            operation["payload"],
            service.work_dir,
            lambda current, total: report(
                f"Extracting {row.get('path')}", current, total
            ),
        )
        try:
            service.put(
                session,
                str(row["path"]),
                temporary_path,
                _protection_text(row.get("protection")) if row.get("protection") else None,
                str(row.get("comment") or "") or None,
                None,
                int(row["side"]) if row.get("side") is not None else None,
            )
        finally:
            temporary_path.unlink(missing_ok=True)
        # put() writes the protection bits and comment, but the entry gets a
        # fresh datestamp because it has genuinely just been written. A patch
        # is supposed to reproduce the image it was taken from, datestamps
        # included, so the recorded one is restored here. Without this the
        # candidate fingerprint can never match and every patch fails its own
        # verification on a metadata difference it created itself.
        _apply_metadata(service, session, row)
        completed += 1

    for operation in metadata:
        report(f"Updating metadata for {operation['after'].get('path')}", completed, total_steps)
        _apply_metadata(service, session, operation["after"])
        completed += 1
    report("Patch operations written", total_steps, total_steps)


def _apply_rom_patch(service, session, operations: list[dict], archive: zipfile.ZipFile, progress=None) -> None:
    report = progress or (lambda _message, _current=None, _total=None: None)
    removed_banks = []
    for index, operation in enumerate(operations):
        row = operation.get("after") or operation.get("before") or {}
        bank = int(row["bank"])
        report(f"Updating ROM bank {bank}", index, len(operations))
        if operation["action"] == "removed":
            removed_banks.append(bank)
        elif operation["action"] in {"added", "modified"}:
            content = archive.read(operation["payload"])
            service.put_rom_bank(session, content, bank)
    if removed_banks:
        current_count = session.path.stat().st_size // session.rom_bank_size
        expected = list(range(current_count - len(removed_banks), current_count))
        if sorted(removed_banks) != expected:
            raise DiskError("A ROM patch can remove only contiguous banks from the end of an image.")
        with session.lock, session.path.open("r+b") as image:
            image.truncate((current_count - len(removed_banks)) * session.rom_bank_size)
        session.dirty = True
        service._persist_session(session)
    report("ROM patch operations written", len(operations), len(operations))


def apply_patch_archive(service, session, archive_path: Path, progress=None) -> dict:
    report = progress or (lambda _message, _current=None, _total=None: None)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            document, _current = _preflight_patch(service, session, archive, report)
            operations = document["operations"]
            if session.kind == "rom":
                _apply_rom_patch(service, session, operations, archive, report)
            elif session.kind == "dms":
                raise DiskError("A DMS archive is read-only and cannot receive a patch set.")
            else:
                _apply_normal_patch(service, session, operations, archive, report)
    except zipfile.BadZipFile as exc:
        raise DiskError("The selected patch is not a readable ZIP archive.") from exc
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DiskError(f"The patch document is incomplete or invalid: {exc}") from exc

    report("Verifying the completed candidate image", 0, None)
    result = build_manifest(service, session, report)
    actual = manifest_fingerprint(result)
    if actual != document.get("candidateFingerprint"):
        expected_records = document.get("candidateRecords")
        detail = ""
        if isinstance(expected_records, list):
            verification = compare_manifests(
                {"image": document.get("candidate", {}), "records": expected_records},
                result,
            )
            for category in ("added", "removed", "renamed", "modified", "metadata"):
                if not verification["changes"][category]:
                    continue
                mismatch = verification["changes"][category][0]
                row = mismatch.get("after") or mismatch.get("before") or {}
                fields = mismatch.get("changedFields") or []
                label = row.get("path") or row.get("diskTitle") or mismatch.get("key")
                detail = f" First mismatch: {label} ({category}{': ' + ', '.join(fields) if fields else ''})."
                break
        raise DiskError(
            "The patch operations completed, but the resulting logical fingerprint did not match the candidate image."
            + detail
        )
    report("Guarded patch applied and verified", len(operations), len(operations))
    return {"operations": len(operations), "fingerprint": actual, "summary": document.get("summary", {})}
