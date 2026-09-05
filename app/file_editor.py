from __future__ import annotations

import json
import re
import tempfile
from difflib import unified_diff
from bisect import bisect_right
from pathlib import Path

from .amiga_metadata import format_protection
from .checksum import sha256_bytes, sha256_path
from .content_kind import (
    analyse_content,
    format_basic_listing as _format_basic_listing,
    is_dms_container,
)
from .disk_service import DiskError, DiskService, ImageSession
from .hex_service import MAX_HEX_READ, _decode_changes, _search_pattern
from .operations import OperationCancelled
from .rom_workbench import RomWorkbenchError, disassemble
from . import amiga_paths


MAX_EDITABLE_TEXT = 64 * 1024
MAX_DISASSEMBLY_FILE = 1024 * 1024
MAX_IMAGE_SEARCH_FILES = 5000
MAX_IMAGE_SEARCH_RESULTS = 500
MAX_IMAGE_SEARCH_BYTES = 256 * 1024


def _catalogue_search_terms(row: dict) -> dict[str, list[str]]:
    """Return labelled, human-searchable catalogue metadata without inventing values."""
    terms: dict[str, list[str]] = {}
    fields = {
        "disk title": ("diskTitle",),
        "file type": ("contentKind", "filetype", "type"),
        "access": ("attr", "access", "attributes"),
        "protection": ("protectionText", "protection"),
        "comment": ("comment",),
    }
    for label, keys in fields.items():
        value = next((row.get(key) for key in keys if row.get(key) not in (None, "")), None)
        if value is None:
            continue
        values = {str(value)}
        if label == "protection":
            try:
                numeric = int(str(value), 0) if not isinstance(value, int) else value
            except (TypeError, ValueError):
                numeric = None
            if numeric is not None:
                values.update({format_protection(numeric), f"&{numeric:X}", f"0x{numeric:X}"})
        terms[label] = sorted(values)
    return terms


def _search_every_partition(
    service, session, query, side, root, progress, supplemental,
) -> dict:
    """Run one search across every partition and merge the reports."""
    selected = session.partition
    partitions = service.list_partitions(session)
    merged = {
        "query": str(query or "").strip(),
        "root": root,
        "results": [],
        "filesConsidered": 0,
        "filesScanned": 0,
        "skippedLarge": 0,
        "failedReads": 0,
        "unreadableFiles": 0,
        "truncated": False,
    }
    try:
        for index, partition in enumerate(partitions):
            device = str(partition.get("device") or partition.get("name") or f"DH{index}")
            service.select_partition(session, index)
            try:
                report = search_image_files(
                    service, session, query, side, root, False, progress, supplemental,
                )
            except DiskError:
                # A partition holding a filing system this build cannot mount
                # is reported as unreadable rather than failing the search.
                merged["failedReads"] += 1
                continue
            for row in report["results"]:
                merged["results"].append({**row, "partition": index, "device": device})
            merged["filesConsidered"] += report["filesConsidered"]
            merged["filesScanned"] += report["filesScanned"]
            merged["failedReads"] += report["failedReads"]
            merged["skippedLarge"] += report["skippedLarge"]
            merged["unreadableFiles"] += report["unreadableFiles"]
            merged["truncated"] = merged["truncated"] or bool(report["truncated"])
    finally:
        service.select_partition(session, selected)
    merged["results"] = merged["results"][:MAX_IMAGE_SEARCH_RESULTS]
    return merged


def _hash_query(query: str) -> str | None:
    candidate = query.casefold().removeprefix("sha256:").strip()
    return candidate if re.fullmatch(r"[0-9a-f]{8,64}", candidate) else None


def _context(
    service: DiskService,
    session: ImageSession,
    path: str,
    side: int | None,
    limit: int | None = None,
):
    exported = service.export_file(session, path, side)
    try:
        size = exported.stat().st_size
        digest = sha256_path(exported)
        with exported.open("rb") as source:
            data = source.read(limit) if limit is not None else source.read()
    finally:
        exported.unlink(missing_ok=True)
    metadata = service.file_metadata(session, path, side)
    return data, metadata, size, digest


def inspect_editable_file(
    service: DiskService,
    session: ImageSession,
    path: str,
    side: int | None,
) -> dict:
    data, metadata, size, digest = _context(service, session, path, side, MAX_DISASSEMBLY_FILE)
    dms_proof = None
    if session.kind == "dms":
        dms_proof = service.dms_member_editability(session, path)
    report = inspect_file_data(
        data, metadata, path,
        read_only=bool(session.hfe_read_only or (dms_proof and not dms_proof["editable"])),
        size=size, digest=digest,
    )
    if dms_proof is not None:
        report["dmsProject"] = dms_proof
    return report


def inspect_file_data(
    data: bytes,
    metadata: dict,
    path: str,
    *,
    read_only: bool,
    size: int | None = None,
    digest: str | None = None,
) -> dict:
    """Inspect already-extracted bytes with the normal content-aware editor rules."""
    size = len(data) if size is None else int(size)
    digest = digest or sha256_bytes(data)
    truncated = size > len(data)
    if is_dms_container(data):
        return {
            "path": path,
            "size": size,
            "sha256": digest,
            "view": "container",
            "containerKind": "dms",
            "text": "",
            "editable": False,
            "tokenisedBasic": False,
            "basic": None,
            "script": None,
            "metadata": metadata,
            "readOnly": True,
            "truncated": truncated,
        }
    content_kind, basic, script, _printable_ratio = (
        ("binary", None, None, 0.0) if truncated else analyse_content(data, path)
    )
    looks_text = content_kind == "text"
    view = "basic" if basic else "script" if script else "text" if looks_text else "disassembly" if data else "hex"
    text = basic["source"] if basic else (
        data.decode("latin-1", "replace").replace("\r\n", "\n").replace("\r", "\n") if script or looks_text else ""
    )
    return {
        "path": path,
        "size": size,
        "sha256": digest,
        "view": view,
        "text": text,
        "editable": bool(not truncated and (basic and basic["editable"] or (script or looks_text) and len(data) <= MAX_EDITABLE_TEXT)),
        "tokenisedBasic": basic is not None,
        "basic": basic,
        "script": script,
        "metadata": metadata,
        "readOnly": bool(read_only),
        "truncated": truncated,
    }


def search_image_files(
    service: DiskService,
    session: ImageSession,
    query: str,
    side: int | None,
    root: str = "",
    all_partitions: bool = False,
    progress=None,
    supplemental: list[dict] | None = None,
) -> dict:
    """Search names and readable source across one mounted filesystem context.

    ``all_partitions`` widens a hard-drive search to every partition the Rigid
    Disk Block chains to, which is what a person means by "search this drive".
    Each result then carries the device name of the partition it came from, so
    two identically named files in different volumes stay distinguishable.
    """
    needle = str(query or "").strip()
    if not needle:
        raise DiskError("Enter text to search for in this image.")
    if len(needle) > 200:
        raise DiskError("Search text is limited to 200 characters.")
    if session.kind == "hdf" and session.partition is None and not all_partitions:
        raise DiskError("Open a partition on this drive before searching its files.")

    if session.kind == "hdf" and all_partitions:
        return _search_every_partition(
            service, session, query, side, root, progress, supplemental
        )

    files: list[dict] = []
    failed_reads = 0
    if session.kind == "ofs":
        files = service.list_ofs_catalogue_files(session, side)
    elif session.kind in {"kickfs", "dms"}:
        files = [
            {**row, "path": str(row.get("path") or row.get("name") or "")}
            for row in service.list_directory(session, "", side)["entries"]
            if str(row.get("type") or "file").lower() not in {"dir", "directory"}
        ]
    elif session.kind == "rom":
        files = [
            {
                **row,
                "name": str(row.get("name") or f"Bank {row.get('bank', 0)}"),
                "path": f"bank:{int(row.get('bank') or 0)}",
                "length": int(row.get("length") or 0),
                "contentKind": "rom-bank",
            }
            for row in service.list_rom_banks(session)
        ]
    elif session.kind in {"ffs", "ofs", "hdf"}:
        pending = [str(root or "")]
        visited = set()
        while pending and len(files) < MAX_IMAGE_SEARCH_FILES:
            directory = pending.pop()
            if directory.casefold() in visited:
                continue
            visited.add(directory.casefold())
            listing = service.list_directory(session, directory, side)
            for row in listing["entries"]:
                name = str(row.get("name") or "")
                if not name or name == "..":
                    continue
                path = str(row.get("path") or amiga_paths.join(directory, name))
                if str(row.get("type") or "file").lower() in {"dir", "directory"}:
                    pending.append(path)
                else:
                    files.append({**row, "path": path})
                    if len(files) >= MAX_IMAGE_SEARCH_FILES:
                        break
    else:
        raise DiskError("Image-wide file search is not available for this media view.")

    folded = needle.casefold()
    digest_query = _hash_query(needle)
    results = []
    scanned = 0
    skipped_large = 0
    unreadable = 0
    searchable_files = files[:MAX_IMAGE_SEARCH_FILES]
    for file_index, row in enumerate(searchable_files):
        row_side = int(row["side"]) if row.get("side") is not None else side
        path = str(row.get("path") or row.get("name") or "")
        if progress:
            progress(f"Searching {path}", file_index, len(searchable_files))
        name = str(row.get("name") or amiga_paths.leaf(path))
        name_match = folded in path.casefold()
        catalogue_terms = _catalogue_search_terms(row)
        metadata_matches = [
            label for label, values in catalogue_terms.items()
            if any(folded in value.casefold() for value in values)
        ]
        length = int(row.get("length") or 0)
        matches = []
        kind = str(row.get("contentKind") or "")
        digest = ""
        if length <= MAX_IMAGE_SEARCH_BYTES:
            try:
                data = service.read_file(session, path, row_side)
                scanned += 1
                if digest_query:
                    digest = sha256_bytes(data)
                content_kind, basic, script, printable_ratio = analyse_content(data, path)
                kind = kind or content_kind
                if basic:
                    text = str(basic["source"])
                elif script or printable_ratio >= 0.70:
                    text = data.decode("latin-1", "replace").replace("\r", "\n")
                else:
                    text = ""
                for line_number, line in enumerate(text.splitlines(), 1):
                    if folded in line.casefold():
                        matches.append({"line": line_number, "text": line[:240]})
                        if len(matches) >= 20:
                            break
                if not text:
                    for item in _printable_strings(data, 0):
                        if folded in item["text"].casefold():
                            matches.append({
                                "offset": item["offset"],
                                "address": item["address"],
                                "text": item["text"][:240],
                            })
                            if len(matches) >= 20:
                                break
            except OperationCancelled:
                raise
            except Exception:
                # One unreadable or damaged file must not abandon a whole-image
                # search, but it is counted so the result can report that its
                # coverage was incomplete rather than implying a clean sweep.
                unreadable += 1
        else:
            skipped_large += 1
            if digest_query:
                try:
                    exported = service.export_file(session, path, row_side)
                    try:
                        digest = sha256_path(
                            exported,
                            (
                                lambda current, total: progress(
                                    f"Checksumming {path}", current, total
                                )
                            ) if progress else None,
                        )
                    finally:
                        exported.unlink(missing_ok=True)
                    scanned += 1
                except OperationCancelled:
                    raise
                except Exception:
                    unreadable += 1
        hash_match = bool(digest_query and digest.startswith(digest_query))
        if name_match or metadata_matches or hash_match or matches:
            results.append({
                "path": path, "name": name, "kind": kind or "file",
                "size": length, "nameMatch": name_match,
                "metadataMatches": metadata_matches,
                "hashMatch": hash_match,
                **({"sha256": digest} if hash_match else {}),
                "matches": matches,
                **({"side": row_side} if row_side is not None else {}),
            })
            if len(results) >= MAX_IMAGE_SEARCH_RESULTS:
                break
    for item in (supplemental or [])[:20_000]:
        fields = {
            str(label): str(value)
            for label, value in dict(item.get("searchFields") or {}).items()
            if value not in (None, "")
        }
        field_matches = [label for label, value in fields.items() if folded in value.casefold()]
        if not field_matches or len(results) >= MAX_IMAGE_SEARCH_RESULTS:
            continue
        offset = item.get("offset")
        results.append({
            **{key: value for key, value in item.items() if key != "searchFields"},
            "size": int(item.get("size") or 0),
            "nameMatch": False,
            "metadataMatches": field_matches,
            "hashMatch": False,
            "matches": ([{"offset": int(offset), "text": fields[field_matches[0]][:240]}]
                        if offset is not None else []),
        })
    if progress:
        progress("Workspace search complete", len(searchable_files), len(searchable_files))
    return {
        "query": needle,
        "root": root,
        "filesConsidered": len(files),
        "filesScanned": scanned,
        "skippedLarge": skipped_large,
        "failedReads": failed_reads,
        "unreadableFiles": unreadable,
        "truncated": len(files) >= MAX_IMAGE_SEARCH_FILES or len(results) >= MAX_IMAGE_SEARCH_RESULTS,
        "results": results,
    }


#: Every processor this build disassembles. They are all 68000 family, because
#: every Amiga is.
M68K_ARCHITECTURES = ("68000", "68010", "68020", "68030", "68040", "68060", "m68k")

#: The processor each hardware profile implies, and why.
PROFILE_PROCESSORS = {
    "a500-ofs": ("68000", "The active hardware profile targets an Amiga 500 or 2000"),
    # An Amiga 600 is a 68000 and an Amiga 1200 a 68EC020, so code that runs
    # on both is 68000 code and that is what the profile decodes as.
    "a1200-ffs": ("68000", "The active hardware profile targets an Amiga 600 or 1200"),
    "amigaos": ("68030", "The active hardware profile targets an AmigaOS hard drive"),
    "hardfile": ("68000", "The active hardware profile targets a UAE hardfile"),
}


def _architecture(session: ImageSession, requested: str) -> tuple[str, str]:
    if requested in M68K_ARCHITECTURES:
        return requested, "Selected in the disassembly viewer"
    return PROFILE_PROCESSORS.get(
        getattr(session, "target_hardware", ""),
        ("68000", "No hardware profile is set, so the baseline 68000 is assumed"),
    )


def _printable_strings(data: bytes, origin: int) -> list[dict]:
    """Return useful human-readable strings, not every accidental printable byte run."""
    strings = []
    run_start = None
    for offset, value in enumerate(data + b"\0"):
        if 32 <= value < 127:
            run_start = offset if run_start is None else run_start
            continue
        if run_start is None:
            continue
        raw = data[run_start:offset]
        leading = len(raw) - len(raw.lstrip())
        text = raw.decode("ascii").strip()
        string_offset = run_start + leading
        letters = sum(character.isalpha() for character in text)
        words = re.findall(r"[A-Za-z]{3,}", text)
        human_words = [word for word in words if re.search(r"[AEIOUYaeiouy]", word) or word.isupper()]
        if (
            len(text) >= 4
            and human_words
            and not re.search(r"([A-Za-z])\1{2,}", text, re.IGNORECASE)
            and letters >= max(3, int(len(text) * 0.40))
            and sum(character.isalnum() or character.isspace() or character in "!$%&'()*+,-./:;=?@[]_" for character in text) / len(text) >= 0.90
        ):
            strings.append({"offset": string_offset, "address": origin + string_offset, "text": text})
        run_start = None
        if len(strings) >= 200:
            break
    return strings


def _materialise_readable_strings(report: dict, strings: list[dict], data: bytes, origin: int) -> None:
    """Replace decoded opcode guesses across known text with labelled DC.B data rows."""
    rows = list(report.get("rows") or [])
    if not rows or not strings:
        return
    report_start = int(report.get("start") or 0)
    report_end = int(report.get("end") or len(data))
    referenced_addresses = {
        int(row["target"])
        for row in rows
        if isinstance(row.get("target"), int)
    }
    references = {
        int(item["target"]): [int(source) for source in item.get("sources") or []]
        for item in report.get("crossReferences") or []
    }

    def extent(row: dict) -> tuple[int, int]:
        start = int(row["offset"])
        return start, start + max(1, len(str(row.get("bytes") or "").split()))

    def reachable_between(start: int, end: int, affected: list[dict]) -> bool:
        return any(bool(row.get("reachable")) and extent(row)[0] < end and extent(row)[1] > start for row in affected)

    def data_byte(offset: int, affected: list[dict]) -> dict:
        value = data[offset]
        return {
            "offset": offset, "address": origin + offset, "bytes": f"{value:02X}",
            "mnemonic": "DC.B", "operand": f"${value:02X}", "target": None,
            "label": "", "comment": "Data adjacent to readable text",
            "reachable": reachable_between(offset, offset + 1, affected),
            "references": references.get(origin + offset, []),
        }

    for item in sorted(strings, key=lambda value: int(value["offset"])):
        string_start = int(item["offset"])
        string_end = string_start + len(str(item["text"]).encode("ascii"))
        if string_start < report_start or string_end > report_end:
            continue
        affected_indexes = [
            index for index, row in enumerate(rows)
            if extent(row)[0] < string_end and extent(row)[1] > string_start
        ]
        if not affected_indexes:
            continue
        first, last = min(affected_indexes), max(affected_indexes)
        affected = rows[first:last + 1]
        covered_start = min(extent(row)[0] for row in affected)
        covered_end = max(extent(row)[1] for row in affected)
        replacement = [data_byte(offset, affected) for offset in range(covered_start, string_start)]
        split_offsets = [string_start] + sorted(
            address - origin
            for address in referenced_addresses
            if origin + string_start < address < origin + string_end
        ) + [string_end]
        for segment_start, segment_end in zip(split_offsets, split_offsets[1:]):
            raw = data[segment_start:segment_end]
            replacement.append({
                "offset": segment_start,
                "address": origin + segment_start,
                "bytes": " ".join(f"{value:02X}" for value in raw),
                "mnemonic": "DC.B",
                "operand": json.dumps(raw.decode("ascii")),
                "target": None,
                "label": "",
                "comment": "Readable text data",
                "reachable": reachable_between(segment_start, segment_end, affected),
                "references": references.get(origin + segment_start, []),
            })
        replacement.extend(data_byte(offset, affected) for offset in range(string_end, covered_end))
        rows[first:last + 1] = replacement
    report["rows"] = rows
    report["reachableInstructions"] = sum(
        bool(row.get("reachable")) and not str(row.get("mnemonic") or "").upper().startswith("DC.")
        for row in rows
    )


def _annotate_file_context(
    report: dict, strings: list[dict], entry: int | None = None
) -> None:
    """Add readable-text notes, and mark an entry point when one is known.

    An AmigaDOS load file is relocatable and its catalogue entry records no
    address, so ``entry`` is normally ``None``. It is passed only where the
    caller has proved one, such as a hunk whose first code block starts at a
    known offset.
    """
    rows = report.get("rows", [])
    if not rows:
        return

    def append(row: dict, note: str) -> None:
        existing = str(row.get("comment") or "")
        if note and note not in existing:
            row["comment"] = f"{existing}; {note}" if existing else note

    # The names the disassembler generates itself, which may be replaced by a
    # better one. A label a person wrote is never overwritten.
    generated_label = re.compile(
        r"^(?:program_entry|subroutine|sub|loc|text|interrupt_handler|"
        r"raise_exception|loop_routine|access_hardware|call_[a-z0-9_]+|"
        r"equal|not_equal|carry_clear|carry_set|negative|positive|"
        r"overflow_clear|overflow_set|greater_equal|less_than|greater_than|"
        r"less_equal|higher|lower_same|always|dispatch|continue)_[0-9A-F]+$",
        re.IGNORECASE,
    )

    by_address = {int(row["address"]): row for row in rows}
    entry_row = by_address.get(entry) if entry is not None else None
    if entry_row is not None:
        if not entry_row.get("label") or generated_label.match(str(entry_row["label"])):
            entry_row["label"] = f"program_entry_{entry:X}"
        append(entry_row, "Program entry point")

    ordered = sorted(rows, key=lambda row: int(row["offset"]))
    row_offsets = [int(row["offset"]) for row in ordered]
    for item in strings:
        text = str(item["text"])
        preview = text if len(text) <= 48 else f"{text[:45]}…"
        offset = int(item["offset"])
        row_index = bisect_right(row_offsets, offset) - 1
        row = ordered[row_index] if row_index >= 0 else None
        if row is not None:
            words = re.findall(r"[A-Za-z0-9]+", text.lower())[:4]
            readable_name = "_".join(words)[:36].strip("_") or "text"
            if not row.get("label") or generated_label.match(str(row["label"])):
                row["label"] = f"text_{readable_name}_{int(item['address']):X}"
            append(row, f"Readable text begins here: {preview!r}")
    string_starts = [int(item["address"]) for item in strings]
    for source in rows:
        target = source.get("target")
        if not isinstance(target, int) or not string_starts:
            continue
        string_index = bisect_right(string_starts, target) - 1
        if string_index < 0:
            continue
        item = strings[string_index]
        text = str(item["text"])
        if target < int(item["address"]) + len(text):
            preview = text if len(text) <= 48 else f"{text[:45]}…"
            target_row = by_address.get(target)
            fragment = text[target - int(item["address"]):]
            fragment_words = re.findall(r"[A-Za-z0-9]+", fragment.lower())[:4]
            fragment_name = "_".join(fragment_words)[:36].strip("_")
            if target_row is not None and fragment_name and (
                not target_row.get("label") or generated_label.match(str(target_row["label"]))
            ):
                target_row["label"] = f"text_{fragment_name}_{target:X}"
            append(source, f"References text {preview!r} at &{target:X}")

    # Labels are assigned after decoding because file metadata and strings add
    # information unavailable to the opcode pass. Refresh control-flow and
    # proven text operands so the listing consistently uses those names.
    controls = {"JSR", "JMP", "BEQ", "BNE", "BCC", "BCS", "BMI", "BPL", "BVC", "BVS", "B", "BL", "BLX", "BRA", "BSR"}
    for source in rows:
        target = source.get("target")
        target_row = by_address.get(target) if isinstance(target, int) else None
        label = str(target_row.get("label") or "") if target_row else ""
        if not label:
            continue
        mnemonic = str(source.get("mnemonic") or "").upper()
        if mnemonic in controls:
            source["operand"] = label
        elif label.startswith("text_"):
            operand = str(source.get("operand") or "")
            suffix = ",X" if operand.endswith(",X") else ",Y" if operand.endswith(",Y") else ""
            source["operand"] = f"{label}{suffix}"


def disassemble_file(
    service: DiskService,
    session: ImageSession,
    path: str,
    side: int | None,
    architecture: str = "auto",
    origin: int | None = None,
    start: int = 0,
    length: int | None = None,
) -> dict:
    data, metadata, size, digest = _context(service, session, path, side, MAX_DISASSEMBLY_FILE)
    project = service.editor_project(session, path, side)
    return disassemble_file_data(
        data, metadata, session, path, architecture, origin, start, length,
        size=size, digest=digest, project=project,
    )


def disassemble_file_data(
    data: bytes,
    metadata: dict,
    session: ImageSession,
    path: str,
    architecture: str = "auto",
    origin: int | None = None,
    start: int = 0,
    length: int | None = None,
    *,
    size: int | None = None,
    digest: str | None = None,
    project: dict | None = None,
) -> dict:
    """Disassemble already-extracted bytes using the pane's hardware profile."""
    size = len(data) if size is None else int(size)
    digest = digest or sha256_bytes(data)
    if not data:
        raise DiskError("An empty file has no machine code to disassemble.")
    architecture, reason = _architecture(session, architecture)
    # An AmigaDOS binary is relocatable: the hunk loader places it wherever
    # there is free RAM, so there is no base address to assume and nothing in
    # the catalogue records one. Origin defaults to zero, which is what a
    # relative listing wants, and the caller can move it deliberately.
    selected_origin = int(origin) if origin is not None else 0
    available = max(1, len(data) - start)
    requested_length = min(length or available, available, MAX_DISASSEMBLY_FILE)
    entries: list[int] = []
    try:
        report = disassemble(
            data,
            architecture=architecture,
            origin=selected_origin,
            start=start,
            length=requested_length,
            entry_points=entries,
            symbols=dict((project or {}).get("symbols") or {}),
        )
    except RomWorkbenchError as exc:
        raise DiskError(str(exc)) from exc
    strings = _printable_strings(data, selected_origin)
    _materialise_readable_strings(report, strings, data, selected_origin)
    _apply_editor_project(report, project or {}, data, selected_origin, strings)
    _annotate_file_context(report, strings)
    return {
        **report,
        "path": path,
        "size": size,
        "sha256": digest,
        "metadata": metadata,
        "architectureReason": reason,
        "strings": strings,
        "project": project or {},
        "limited": size > start + requested_length,
    }


def _project_data_rows(
    data: bytes, origin: int, start: int, end: int, kind: str, width: int, byteorder: str,
) -> list[dict]:
    """Render an explicitly classified byte range as assembler data rows."""
    rows = []
    unit = 2 if kind in {"words", "addresses"} else 1
    chunk = max(unit, min(32, width - (width % unit)))
    for offset in range(start, end, chunk):
        block = data[offset:min(end, offset + chunk)]
        address = origin + offset
        if kind in {"words", "addresses"}:
            values = [
                int.from_bytes(block[index:index + 2], byteorder)
                for index in range(0, len(block) - 1, 2)
            ]
            mnemonic = "DC.W"
            operand = ", ".join(f"${value:04X}" for value in values)
            if len(block) % 2:
                operand = f"{operand}, ${block[-1]:02X}" if operand else f"${block[-1]:02X}"
        else:
            mnemonic = "DC.B"
            operand = ", ".join(f"${value:02X}" for value in block)
        rows.append({
            "offset": offset, "address": address, "bytes": block.hex(" ").upper(),
            "mnemonic": mnemonic, "operand": operand, "target": None, "label": "",
            "comment": {"bitmap": "Bitmap data", "addresses": "Address table",
                        "words": "16-bit word data", "bytes": "Byte data"}.get(kind, "Data"),
            "reachable": False, "references": [], "regionKind": kind,
        })
    return rows


def _row_byte_length(row: dict) -> int:
    """Return a decoded row's byte length without trusting optional fields."""
    try:
        return max(1, len(bytes.fromhex(str(row.get("bytes") or ""))))
    except ValueError:
        return 1


def _apply_editor_project(report: dict, project: dict, data: bytes, origin: int, strings: list[dict]) -> None:
    """Apply user symbols, range classifications, bookmarks and notes to a report."""
    rows = list(report.get("rows") or [])
    # Every 68000-family processor is big-endian.
    byteorder = "big"
    report_start, report_end = int(report.get("start") or 0), int(report.get("end") or len(data))
    for region in project.get("regions") or []:
        start = max(report_start, int(region.get("start") or 0))
        end = min(report_end, int(region.get("end") or 0), len(data))
        kind = str(region.get("kind") or "bytes")
        if end <= start:
            continue
        if kind == "code":
            for row in rows:
                row_start = int(row.get("offset") or 0)
                row_end = row_start + _row_byte_length(row)
                if row_start < end and row_end > start:
                    row["regionKind"] = "code"
            continue
        rows = [
            row for row in rows
            if not (int(row.get("offset") or 0) < end and
                    int(row.get("offset") or 0) + _row_byte_length(row) > start)
        ]
        if kind == "text":
            for offset in range(start, end, 64):
                block = data[offset:min(end, offset + 64)]
                escaped = "".join(chr(value) if 32 <= value <= 126 else f"\\x{value:02X}" for value in block)
                rows.append({
                    "offset": offset, "address": origin + offset, "bytes": block.hex(" ").upper(),
                    "mnemonic": "DC.B", "operand": json.dumps(escaped), "target": None,
                    "label": "", "comment": "User-defined text region", "reachable": False,
                    "references": [], "regionKind": "text",
                })
            whole = data[start:end]
            preview = "".join(chr(value) if 32 <= value <= 126 else "." for value in whole[:256])
            if not any(int(item.get("offset", -1)) == start for item in strings):
                strings.append({"offset": start, "address": origin + start, "text": preview,
                                "length": len(whole), "source": "project"})
        else:
            rows.extend(_project_data_rows(
                data, origin, start, end, kind, int(region.get("width") or 8), byteorder,
            ))
        first = next((row for row in rows if int(row.get("offset") or -1) == start), None)
        if first is not None and region.get("name"):
            first["label"] = re.sub(r"[^A-Za-z0-9_.]", "_", str(region["name"]))

    rows.sort(key=lambda row: int(row.get("offset") or 0))
    for bookmark in project.get("bookmarks") or []:
        offset = int(bookmark.get("offset") or 0)
        row = next((item for item in rows if int(item.get("offset") or 0) <= offset <
                    int(item.get("offset") or 0) + _row_byte_length(item)), None)
        if row is None:
            continue
        name = str(bookmark.get("name") or f"Offset {offset}")
        if not row.get("label"):
            row["label"] = re.sub(r"[^A-Za-z0-9_.]", "_", name)
        note = str(bookmark.get("note") or "").strip()
        row["comment"] = "; ".join(part for part in [str(row.get("comment") or ""), note] if part)
        row["bookmarked"] = True
    for offset_text, comment in dict(project.get("comments") or {}).items():
        offset = int(offset_text)
        row = next((item for item in rows if int(item.get("offset") or 0) <= offset <
                    int(item.get("offset") or 0) + _row_byte_length(item)), None)
        if row is not None:
            row["comment"] = "; ".join(part for part in [str(row.get("comment") or ""), str(comment).strip()] if part)
            row["userComment"] = str(comment).strip()
    report["rows"] = rows


def _renumber_tokenised(program: bytes, start: int, step: int) -> bytes:
    try:
        from amiganut.basic import TokenKind, scan_program
        from amiganut.basic.linenumber import encode_line_number
    except ImportError as exc:
        raise DiskError("The AmigaBASIC editing library is unavailable.") from exc
    lines = list(scan_program(program))
    if not lines:
        raise DiskError("The BASIC program contains no numbered lines.")
    if start < 0 or step < 1 or start + step * (len(lines) - 1) > 32767:
        raise DiskError("Choose line numbers from 0 to 32767 with a positive step.")
    mapping = {line.line_number: start + index * step for index, line in enumerate(lines)}
    result = bytearray(program)
    for line in lines:
        replacement = mapping[line.line_number]
        result[line.start + 1] = replacement >> 8
        result[line.start + 2] = replacement & 0xFF
        for token in line.tokens:
            if token.kind is TokenKind.LINENUM:
                referenced = int(token.value)
                if referenced in mapping:
                    result[token.start + 1:token.start + 4] = encode_line_number(mapping[referenced])
    return bytes(result)


def prepare_basic_source(source: str, start: int, step: int) -> dict:
    try:
        from amiganut.basic import detokenise, scan_program, tokenise
        program = tokenise(source.replace("\r\n", "\n").replace("\r", "\n"))
        renumbered = _renumber_tokenised(program, start, step)
        return {
            "text": _format_basic_listing(detokenise(renumbered)),
            "lineCount": len(list(scan_program(renumbered))),
        }
    except DiskError:
        raise
    except Exception as exc:
        raise DiskError(f"The BASIC listing could not be tokenised: {exc}") from exc


def normalise_basic_source(source: str) -> dict:
    try:
        from amiganut.basic import detokenise, scan_program, tokenise
        program = tokenise(source.replace("\r\n", "\n").replace("\r", "\n"))
        return {
            "text": _format_basic_listing(detokenise(program)),
            "lineCount": len(list(scan_program(program))),
        }
    except Exception as exc:
        raise DiskError(f"The pasted text is not a valid numbered AmigaBASIC listing: {exc}") from exc


def verify_basic_source(source: str, baseline: str = "") -> dict:
    """Tokenise, detokenise and retokenise source, reporting its exact round trip."""
    try:
        from amiganut.basic import detokenise, scan_program, tokenise
        cleaned = source.replace("\r\n", "\n").replace("\r", "\n")
        program = tokenise(cleaned)
        listing = _format_basic_listing(detokenise(program))
        repeated = tokenise(listing)
        scanned = list(scan_program(program))
        ranges = []
        for index, line in enumerate(scanned):
            end = scanned[index + 1].start if index + 1 < len(scanned) else len(program)
            ranges.append({"line": int(line.line_number), "start": int(line.start), "end": int(end)})
        destinations = sorted({
            int(value)
            for value in re.findall(r"\b(?:GOTO|GOSUB|RESTORE|THEN|RUN)\s*(\d+)\b", listing, re.IGNORECASE)
        })
        baseline_lines = baseline.replace("\r\n", "\n").replace("\r", "\n").splitlines() if baseline else []
        diff = list(unified_diff(baseline_lines, listing.splitlines(), fromfile="original", tofile="proposed", lineterm=""))
        warnings = []
        if program != repeated:
            warnings.append("Tokenising the round-trip listing did not reproduce identical bytes.")
        if len(program) > 65535:
            warnings.append("The tokenised program exceeds 64 KiB and may not fit the target machine.")
        return {
            "valid": not warnings,
            "roundTripExact": program == repeated,
            "text": listing,
            "lineCount": len(scanned),
            "byteLength": len(program),
            "lineRanges": ranges,
            "destinations": destinations,
            "diff": diff[:4000],
            "warnings": warnings,
        }
    except Exception as exc:
        raise DiskError(f"The BASIC listing could not complete a tokenisation round trip: {exc}") from exc


def pack_basic_lines(runs: list[list[str]]) -> dict:
    """Pack ordered BASIC statements using the tokeniser's real line limit.

    The browser decides which physical-line boundaries are semantically safe to
    remove.  This helper has the narrower job of fitting those safe runs into as
    few tokenised AmigaBASIC 1.0 lines as possible.  Measuring the actual token stream
    matters because keywords and line destinations occupy fewer bytes than their
    readable source spelling.
    """
    try:
        from amiganut.basic import tokenise
    except ImportError as exc:
        raise DiskError("The AmigaBASIC editing library is unavailable.") from exc

    packed: list[list[int]] = []
    for run_number, raw_run in enumerate(runs, start=1):
        if not isinstance(raw_run, list):
            raise DiskError(f"BASIC packing run {run_number} is invalid.")
        statements = [str(statement) for statement in raw_run]
        groups: list[int] = []
        current: list[str] = []
        for statement in statements:
            candidate = [*current, statement]
            try:
                tokenise(f"10 {':'.join(candidate)}")
                current = candidate
            except Exception as exc:
                if not current:
                    raise DiskError(
                        f"BASIC statement {len(groups) + 1} in packing run {run_number} "
                        f"cannot fit on a physical line: {exc}"
                    ) from exc
                groups.append(len(current))
                current = [statement]
                try:
                    tokenise(f"10 {statement}")
                except Exception as single_exc:
                    raise DiskError(
                        f"A BASIC statement in packing run {run_number} cannot fit on a "
                        f"physical line: {single_exc}"
                    ) from single_exc
        if current:
            groups.append(len(current))
        packed.append(groups)
    return {"groups": packed}


def _find_row(service, session, path, side):
    parent, leaf = amiga_paths.parent(path), amiga_paths.leaf(path)
    row = next((item for item in service.list_directory(session, parent, side)["entries"]
                if str(item.get("name", "")).casefold() == leaf.casefold()), None)
    if row is None:
        raise DiskError("The file changed while the editor was open. Refresh and try again.")
    return row


def replace_file_bytes(service, session, path, side, content: bytes, expected_sha256: str) -> dict:
    current = service.read_file(session, path, side)
    if sha256_bytes(current) != expected_sha256:
        raise DiskError("The file changed after the editor opened it. Reopen the file before saving.")
    if session.kind == "dms":
        service.replace_dms_member(session, path, content)
        return service.summary(session)
    row = _find_row(service, session, path, side)
    with tempfile.NamedTemporaryFile(dir=service.work_dir, prefix="file-edit-", delete=False) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    filetype = row.get("filetype") or None
    protection = str(row.get("protectionText") or row.get("protection") or "") or None
    comment = str(row.get("comment") or "") or None
    # A locked entry shows neither the write nor the delete flag.
    attributes = str(row.get("attr") or "")
    was_locked = bool(attributes) and ("w" not in attributes or "d" not in attributes)
    try:
        if was_locked:
            service.set_access(session, [path], writable=True, side=side)
        service.mutate(session, ["rm", "--force", "{image}:" + path], side)
        service.put(session, path, temporary_path, protection, comment, filetype, side)
        if was_locked:
            service.set_access(session, [path], writable=False, side=side)
    finally:
        temporary_path.unlink(missing_ok=True)
    return service.summary(session)


def update_file_properties(
    service,
    session,
    path,
    side,
    expected_sha256: str,
    *,
    protection: str = "",
    comment: str = "",
    filetype: str = "",
    writable: bool = True,
) -> dict:
    """Rewrite catalogue metadata without changing the file's bytes."""
    content = service.read_file(session, path, side)
    if sha256_bytes(content) != expected_sha256:
        raise DiskError("The file changed after the editor opened it. Reopen the file before changing its properties.")
    _find_row(service, session, path, side)
    with tempfile.NamedTemporaryFile(dir=service.work_dir, prefix="file-properties-", delete=False) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    try:
        service.set_access(session, [path], writable=True, side=side)
        service.mutate(session, ["rm", "--force", "{image}:" + path], side)
        service.put(
            session, path, temporary_path,
            str(protection or "") or None,
            str(comment or "") or None,
            str(filetype or "") or None,
            side,
        )
        if not writable:
            service.set_access(session, [path], writable=False, side=side)
    finally:
        temporary_path.unlink(missing_ok=True)
    return service.summary(session)


def _encode_editor_text(text: str, basic: bool) -> bytes:
    """Encode edited text the way AmigaDOS stores it: one newline per line."""
    try:
        normalised = text.replace("\r\n", "\n").replace("\r", "\n")
        if basic:
            from amiganut.basic import tokenise
            return tokenise(normalised)
        return normalised.encode("latin-1", "strict")
    except Exception as exc:
        raise DiskError(f"The edited file could not be encoded: {exc}") from exc


def _preserve_basic_payload(original: bytes, tokenised: bytes) -> bytes:
    """Replace only an AmigaBASIC 1.0 program prefix and retain a proven trailing payload."""
    try:
        from amiganut.basic import Verdict, detect
    except ImportError as exc:
        raise DiskError("The AmigaBASIC editing library is unavailable.") from exc
    detection = detect(original)
    if detection.verdict not in {Verdict.BASIC, Verdict.BASIC_TRAILING}:
        raise DiskError("The file is no longer a recognised tokenised BASIC program.")
    program_length = int(detection.program_length or len(original))
    if program_length < 2 or program_length > len(original):
        raise DiskError("The original BASIC program boundary is invalid.")
    return tokenised + original[program_length:]


def encode_editor_replacement(original: bytes, text: str, basic: bool) -> bytes:
    """Encode editor text and preserve any recognised compound BASIC payload."""
    encoded = _encode_editor_text(text, basic)
    return _preserve_basic_payload(original, encoded) if basic else encoded


def save_editor_text(service, session, path, side, text: str, basic: bool, expected_sha256: str) -> dict:
    original = service.read_file(session, path, side)
    content = encode_editor_replacement(original, text, basic)
    return replace_file_bytes(service, session, path, side, content, expected_sha256)


def save_editor_text_as(
    service,
    session,
    path,
    side,
    new_name: str,
    text: str,
    basic: bool,
    expected_sha256: str,
) -> tuple[dict, str]:
    """Create an edited sibling while retaining the original file's Amiga metadata."""
    current = service.read_file(session, path, side)
    if sha256_bytes(current) != expected_sha256:
        raise DiskError("The file changed after the editor opened it. Reopen the file before saving.")
    leaf = service.validate_leaf_name(session, new_name)
    parent = amiga_paths.parent(path)
    destination = amiga_paths.join(parent, leaf)
    siblings = service.list_directory(session, parent, side)["entries"]
    if any(str(item.get("name") or "").casefold() == leaf.casefold() for item in siblings):
        raise DiskError(f"“{leaf}” already exists in this directory.")

    row = _find_row(service, session, path, side)
    content = _encode_editor_text(text, basic)
    if basic:
        content = _preserve_basic_payload(current, content)
    filetype = row.get("filetype") or None
    protection = str(row.get("protectionText") or row.get("protection") or "") or None
    comment = str(row.get("comment") or "") or None
    # A locked entry shows neither the write nor the delete flag.
    attributes = str(row.get("attr") or "")
    was_locked = bool(attributes) and ("w" not in attributes or "d" not in attributes)
    with tempfile.NamedTemporaryFile(dir=service.work_dir, prefix="file-save-as-", delete=False) as temporary:
        temporary.write(content)
        temporary_path = Path(temporary.name)
    try:
        service.put(session, destination, temporary_path, protection, comment, filetype, side)
        if was_locked:
            service.set_access(session, [destination], writable=False, side=side)
    finally:
        temporary_path.unlink(missing_ok=True)
    return service.summary(session), destination


def file_range(service, session, path, side, offset: int, length: int) -> dict:
    if length < 1 or length > MAX_HEX_READ:
        raise DiskError(f"Read between 1 and {MAX_HEX_READ:,} bytes at a time.")
    data = service.read_file(session, path, side)
    return data_range(data, amiga_paths.leaf(path), offset, length,
                      read_only=bool(session.hfe_read_only or session.kind == "dms"))


def data_range(data: bytes, target_name: str, offset: int, length: int, *, read_only: bool) -> dict:
    """Return a hex-editor page for bytes not stored as a direct filesystem file."""
    if length < 1 or length > MAX_HEX_READ:
        raise DiskError(f"Read between 1 and {MAX_HEX_READ:,} bytes at a time.")
    offset = max(0, min(offset, max(0, len(data) - 1))) if data else 0
    chunk = data[offset:offset + length]
    return {"offset": offset, "length": len(chunk), "size": len(data), "data": chunk.hex().upper(),
            "version": sha256_bytes(data), "target": "file", "targetName": target_name,
            "readOnly": bool(read_only)}


def search_file(service, session, path, side, query, mode, start, direction, wrap) -> dict:
    data = service.read_file(session, path, side)
    return search_data(data, query, mode, start, direction, wrap)


def search_data(data: bytes, query, mode, start, direction, wrap) -> dict:
    """Search already-extracted bytes with the normal hex-editor semantics."""
    pattern = _search_pattern(query, mode)
    if direction not in {"forward", "backward"}:
        raise DiskError("Choose forward or backward search.")
    if direction == "forward":
        found = data.find(pattern, max(0, start)) if start < len(data) else -1
        wrapped = found < 0 and wrap
        if wrapped:
            found = data.find(pattern, 0, min(len(data), max(0, start) + len(pattern) - 1))
    else:
        found = data.rfind(pattern, 0, min(len(data), start + len(pattern))) if start >= 0 else -1
        wrapped = found < 0 and wrap
        if wrapped:
            found = data.rfind(pattern, min(len(data), max(0, start + 1)))
    return {"offset": found if found >= 0 else None, "wrapped": bool(wrapped and found >= 0),
            "version": sha256_bytes(data)}


def write_file_range(service, session, path, side, expected_version, changes, confirmed):
    if not confirmed:
        raise DiskError("Raw file writes require explicit dangerous-change confirmation.")
    data = service.read_file(session, path, side)
    if sha256_bytes(data) != expected_version:
        raise DiskError("The file changed after the hex editor loaded it. Reopen it before writing.")
    result = bytearray(data)
    decoded = _decode_changes(changes, len(result))
    for offset, replacement in decoded:
        result[offset:offset + len(replacement)] = replacement
    image = replace_file_bytes(service, session, path, side, bytes(result), expected_version)
    return {"written": sum(len(value) for _offset, value in decoded),
            "version": sha256_bytes(result), "image": image}
