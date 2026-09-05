from __future__ import annotations

import bz2
import gzip
import io
import lzma
import posixpath
import stat
import tarfile
import zipfile
from copy import copy

from .amiga_metadata import amiga_zip_metadata, format_protection, parse_inf
from .content_kind import LISTING_SNIFF_LIMIT, analyse_content, is_dms_container, metadata_kind
from .errors import DiskError
from .dms import DMSError, parse_dms, replace_dms_file, dms_editability


ARCHIVE_EXTENSIONS = (
    ".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz", ".tbz2",
    ".tar.xz", ".txz", ".gz", ".gzip", ".bz2", ".xz", ".dms",
)
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
MAX_MEMBER_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 20_000
MAX_EXPANDED_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_LISTING_SNIFF_BYTES = 16 * 1024 * 1024


class ArchiveError(DiskError):
    pass


def is_archive_name(name: str) -> bool:
    lowered = str(name or "").casefold()
    return any(lowered.endswith(extension) for extension in ARCHIVE_EXTENSIONS)


def _safe_name(value: str) -> str:
    name = str(value or "").replace("\\", "/").lstrip("/")
    normalised = posixpath.normpath(name)
    if normalised in {"", "."}:
        return ""
    if normalised == ".." or normalised.startswith("../"):
        raise ArchiveError("The archive contains an unsafe parent path.")
    return normalised


def _standalone_name(filename: str) -> str:
    lowered = filename.casefold()
    for suffix in (".gzip", ".gz", ".bz2", ".xz"):
        if lowered.endswith(suffix):
            return filename[:-len(suffix)] or "contents"
    return "contents"


def _dms_member_kind(name: str, data: bytes) -> str:
    return metadata_kind(name, None) or analyse_content(data, name)[0]


def _bounded_member_kind(name: str, size: int, reader) -> str | None:
    """Classify a small archive member while its parent archive is already open."""
    hint = metadata_kind(name, None)
    if hint:
        return hint
    if size <= 0 or size > LISTING_SNIFF_LIMIT:
        return None
    try:
        data = reader()
    except (EOFError, OSError, RuntimeError, ValueError, zipfile.BadZipFile, tarfile.TarError):
        return None
    return analyse_content(data, name)[0] if len(data) == size else None


def _listing_member_kind(
    name: str,
    size: int,
    reader,
    remaining: int,
) -> tuple[str | None, int]:
    """Classify one listing entry without exceeding the archive sniff budget."""
    hint = metadata_kind(name, None)
    if hint:
        return hint, remaining
    if size > remaining:
        return None, remaining
    return _bounded_member_kind(name, size, reader), remaining - max(0, size)


def _dms_payload(data: bytes) -> tuple[bytes, bool]:
    """Return a DMS archive's raw bytes, unwrapping a gzip transport layer.

    Archives are often served ``.dms.gz``. The container inside is what the
    parser and the rebuilder work on, so the wrapper is stripped here and put
    back afterwards rather than being handled at every call site.
    """
    if not data.startswith(b"\x1f\x8b"):
        return data, False
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
            return compressed.read(MAX_ARCHIVE_BYTES + 1), True
    except (gzip.BadGzipFile, EOFError, OSError) as exc:
        raise ArchiveError("That compressed DMS archive could not be expanded.") from exc


def _sidecar_fields(metadata: dict) -> dict:
    """Turn a parsed sidecar record into the fields a listing row carries.

    A record written by an earlier release holds two address words instead of
    a protection field; those are still surfaced so an old download still
    shows what it recorded.
    """
    fields: dict = {}
    if metadata.get("protection") is not None:
        fields["access"] = int(metadata["protection"])
    elif metadata.get("locked"):
        fields["access"] = 0x04
    if metadata.get("comment"):
        fields["comment"] = str(metadata["comment"])
    for legacy in ("load", "execute"):
        if metadata.get(legacy) is not None:
            fields[legacy] = int(metadata[legacy])
    return fields


def _archive_kind(data: bytes, filename: str) -> str:
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ArchiveError("That archive is too large to browse safely in memory.")
    if is_dms_container(data):
        return "dms"
    stream = io.BytesIO(data)
    if zipfile.is_zipfile(stream):
        return "zip"
    stream.seek(0)
    try:
        with tarfile.open(fileobj=stream, mode="r:*"):
            return "tar"
    except (tarfile.TarError, EOFError, OSError):
        pass
    lowered = filename.casefold()
    if data.startswith(b"\x1f\x8b") or lowered.endswith((".gz", ".gzip")):
        return "gzip"
    if data.startswith(b"BZh") or lowered.endswith(".bz2"):
        return "bz2"
    if data.startswith(b"\xfd7zXZ\x00") or lowered.endswith(".xz"):
        return "xz"
    raise ArchiveError("That file is not a supported DMS, ZIP, TAR, GZIP, BZIP2 or XZ container.")


def _validate_archive_inventory(items, size_of) -> None:
    if len(items) > MAX_ENTRIES:
        raise ArchiveError(
            f"The archive contains more than {MAX_ENTRIES:,} entries."
        )
    expanded = sum(max(0, int(size_of(item) or 0)) for item in items)
    if expanded > MAX_EXPANDED_ARCHIVE_BYTES:
        raise ArchiveError("The archive expands beyond the safe 2 GiB browsing limit.")


def _members(
    data: bytes,
    filename: str,
    *,
    sniff_content: bool = True,
) -> tuple[str, list[dict]]:
    kind = _archive_kind(data, filename)
    rows: list[dict] = []
    if kind == "dms":
        try:
            dms = parse_dms(_dms_payload(data)[0])
        except DMSError as exc:
            raise ArchiveError(f"That DMS archive container is damaged: {exc}") from exc
        used: dict[str, int] = {}
        for index, item in enumerate(dms.files[:MAX_ENTRIES]):
            base = _safe_name(item.name).replace("/", "_") or f"DMSFile{index + 1}"
            used[base.casefold()] = used.get(base.casefold(), 0) + 1
            occurrence = used[base.casefold()]
            name = base if occurrence == 1 else f"{base}~{occurrence}"
            rows.append({
                "name": name, "size": len(item.data), "dir": False, "source": index,
                "unpackedChecksum": item.unpacked_crc,
                "packedChecksum": item.packed_crc,
                "complete": item.complete,
                "contentKind": _dms_member_kind(item.name, item.data),
            })
    elif kind == "zip":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            inventory = archive.infolist()
            _validate_archive_inventory(inventory, lambda item: item.file_size)
            if any(item.flag_bits & 0x1 for item in inventory if not item.is_dir()):
                raise ArchiveError("Password-protected ZIP members cannot be browsed safely.")
            sniff_remaining = MAX_LISTING_SNIFF_BYTES if sniff_content else 0
            for item in inventory:
                name = _safe_name(item.filename)
                if name:
                    row = {"name": name, "size": item.file_size, "dir": item.is_dir(), "source": item.filename}
                    if not item.is_dir():
                        metadata = amiga_zip_metadata(item)
                        if metadata:
                            row.update(metadata)
                        content_kind, sniff_remaining = _listing_member_kind(
                            name,
                            item.file_size,
                            lambda item=item: archive.read(item),
                            sniff_remaining,
                        )
                        if content_kind:
                            row["contentKind"] = content_kind
                    rows.append(row)
            sidecars = {
                row["name"][:-4].casefold(): parse_inf(archive.read(row["source"])[:4096])
                for row in rows
                if not row["dir"] and row["name"].casefold().endswith(".inf") and row["size"] <= 4096
            }
            for row in rows:
                metadata = sidecars.get(row["name"].casefold())
                if metadata and not row["dir"]:
                    row.update(_sidecar_fields(metadata))
    elif kind == "tar":
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
            inventory = archive.getmembers()
            _validate_archive_inventory(inventory, lambda item: item.size if item.isfile() else 0)
            sniff_remaining = MAX_LISTING_SNIFF_BYTES if sniff_content else 0
            for item in inventory:
                name = _safe_name(item.name)
                if name and (item.isdir() or item.isfile()):
                    row = {"name": name, "size": item.size, "dir": item.isdir(), "source": item.name}
                    if item.isfile():
                        def read_tar_member(item=item):
                            expanded = archive.extractfile(item)
                            return expanded.read() if expanded else b""
                        content_kind, sniff_remaining = _listing_member_kind(
                            name, item.size, read_tar_member, sniff_remaining,
                        )
                        if content_kind:
                            row["contentKind"] = content_kind
                    rows.append(row)
            sidecars = {}
            for row in rows:
                if row["dir"] or not row["name"].casefold().endswith(".inf") or row["size"] > 4096:
                    continue
                extracted = archive.extractfile(row["source"])
                if extracted:
                    sidecars[row["name"][:-4].casefold()] = parse_inf(extracted.read(4096))
            for row in rows:
                metadata = sidecars.get(row["name"].casefold())
                if metadata and not row["dir"]:
                    row.update(_sidecar_fields(metadata))
    else:
        rows.append({"name": _safe_name(_standalone_name(filename)), "size": None, "dir": False, "source": ""})
    return kind, rows


def list_archive(data: bytes, filename: str, directory: str = "") -> dict:
    kind, members = _members(data, filename)
    current = _safe_name(directory)
    prefix = f"{current}/" if current else ""
    children: dict[str, dict] = {}
    for member in members:
        if not member["name"].startswith(prefix) or member["name"] == current:
            continue
        remainder = member["name"][len(prefix):]
        leaf, separator, _tail = remainder.partition("/")
        if not leaf:
            continue
        child = children.setdefault(leaf, {
            "name": leaf, "type": "dir" if separator or member["dir"] else "file",
            "length": 0, "attr": "RO", "archiveEntry": True,
        })
        if separator or member["dir"]:
            child["type"] = "dir"
        elif child["type"] != "dir":
            child["length"] = int(member["size"] or 0)
            if member.get("contentKind"):
                child["contentKind"] = member["contentKind"]
            if member.get("access") is not None:
                child["access"] = int(member["access"])
                child["attr"] = format_protection(member["access"])
            if member.get("comment"):
                child["comment"] = str(member["comment"])
            # An address pair only appears on a sidecar written by an earlier
            # release, and is surfaced so an old download still shows it.
            if member.get("load") is not None or member.get("execute") is not None:
                child.update(
                    load=int(member.get("load") or 0), exec=int(member.get("execute") or 0),
                )
            if kind == "dms":
                # A DMS track has no load address; what it does carry is the
                # pair of checksums DiskMasher stores for it.
                child.update(
                    attr="R/" if member["complete"] else "R/?",
                    complete=member["complete"],
                    contentKind=member["contentKind"],
                    unpackedChecksum=member["unpackedChecksum"],
                    packedChecksum=member["packedChecksum"],
                )
    entries = sorted(children.values(), key=lambda row: (row["type"] != "dir", row["name"].casefold()))
    return {
        "entries": entries,
        "description": f"{'Proof-gated DMS disk archive project' if kind == 'dms' else f'{kind.upper()} archive'} · {len(members):,} member(s)",
        "archiveKind": kind,
        "member": current,
    }


def read_archive_member_details(data: bytes, filename: str, member_name: str) -> tuple[bytes, dict]:
    wanted = _safe_name(member_name)
    kind, members = _members(data, filename, sniff_content=False)
    match = next((row for row in members if row["name"] == wanted and not row["dir"]), None)
    if not match:
        raise ArchiveError("That archive member does not exist or is not a regular file.")
    if match["size"] is not None and int(match["size"]) > MAX_MEMBER_BYTES:
        raise ArchiveError("That archive member is too large to open safely.")
    if kind == "dms":
        content = parse_dms(_dms_payload(data)[0]).files[int(match["source"])].data
    elif kind == "zip":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            with archive.open(match["source"]) as expanded:
                content = expanded.read(MAX_MEMBER_BYTES + 1)
    elif kind == "tar":
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
            extracted = archive.extractfile(match["source"])
            if extracted is None:
                raise ArchiveError("That TAR member could not be read.")
            content = extracted.read(MAX_MEMBER_BYTES + 1)
    elif kind == "gzip":
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as expanded:
            content = expanded.read(MAX_MEMBER_BYTES + 1)
    elif kind == "bz2":
        decompressor = bz2.BZ2Decompressor()
        content = decompressor.decompress(data, max_length=MAX_MEMBER_BYTES + 1)
    else:
        decompressor = lzma.LZMADecompressor()
        content = decompressor.decompress(data, max_length=MAX_MEMBER_BYTES + 1)
    if len(content) > MAX_MEMBER_BYTES:
        raise ArchiveError("That expanded archive member exceeds the safe opening limit.")
    content_kind = match.get("contentKind") or metadata_kind(wanted, None)
    if not content_kind:
        content_kind = analyse_content(content, wanted)[0]
    return content, {
        "length": len(content),
        "load": int(match.get("load") or 0),
        "execute": int(match.get("execute") or 0),
        "attr": format_protection(match.get("access")),
        "access": int(match.get("access") or 0),
        "comment": str(match.get("comment") or ""),
        "archiveKind": kind,
        "contentKind": content_kind,
        "metadataAvailable": any(
            match.get(field) is not None for field in ("access", "comment", "load", "execute")
        ),
    }


def archive_member_editable(data: bytes, filename: str, member_name: str | None = None) -> bool:
    """Return whether a container can be rebuilt without changing its semantics."""
    kind = _archive_kind(data, filename)
    if kind != "dms":
        return kind in {"zip", "tar", "gzip", "bz2", "xz"}
    if not member_name:
        return False
    _kind, members = _members(data, filename, sniff_content=False)
    wanted = _safe_name(member_name)
    match = next((row for row in members if row["name"] == wanted and not row["dir"]), None)
    if not match:
        return False
    try:
        return bool(dms_editability(_dms_payload(data)[0], int(match["source"]))["editable"])
    except DMSError:
        return False


def preview_archive_member_replacement(
    data: bytes, filename: str, member_name: str, content: bytes,
) -> dict:
    """Return the exact structural proof that would guard a DMS rebuild."""
    wanted = _safe_name(member_name)
    kind, members = _members(data, filename, sniff_content=False)
    if kind != "dms":
        return {
            "schema": "amiga-file-forge/archive-rebuild-preview/v1",
            "archiveKind": kind,
            "member": wanted,
            "structuralProofRequired": False,
        }
    match = next((row for row in members if row["name"] == wanted and not row["dir"]), None)
    if not match:
        raise ArchiveError("That DMS member no longer exists.")
    try:
        _rebuilt, report = replace_dms_file(_dms_payload(data)[0], int(match["source"]), content)
    except DMSError as exc:
        raise ArchiveError(str(exc)) from exc
    return report | {
        "archiveKind": "dms",
        "member": wanted,
        "structuralProofRequired": True,
    }


def _tar_write_mode(data: bytes, filename: str) -> str:
    lowered = filename.casefold()
    if data.startswith(b"\x1f\x8b") or lowered.endswith((".tar.gz", ".tgz")):
        return "w:gz"
    if data.startswith(b"BZh") or lowered.endswith((".tar.bz2", ".tbz", ".tbz2")):
        return "w:bz2"
    if data.startswith(b"\xfd7zXZ\x00") or lowered.endswith((".tar.xz", ".txz")):
        return "w:xz"
    return "w:"


def replace_archive_member(data: bytes, filename: str, member_name: str, content: bytes) -> bytes:
    """Rebuild a supported archive with one regular member replaced.

    The caller performs the outer image transaction. This function keeps ZIP
    metadata and TAR member metadata where the standard libraries permit it.
    A DMS write is accepted only when the track map proves a same-length
    replacement preserves every following offset.
    """
    wanted = _safe_name(member_name)
    kind, members = _members(data, filename, sniff_content=False)
    match = next((row for row in members if row["name"] == wanted and not row["dir"]), None)
    if not match:
        raise ArchiveError("That archive member no longer exists.")
    if len(content) > MAX_MEMBER_BYTES:
        raise ArchiveError("The replacement member exceeds the safe 128 MiB limit.")

    output = io.BytesIO()
    if kind == "zip":
        with zipfile.ZipFile(io.BytesIO(data), "r") as source:
            regular = [info for info in source.infolist() if not info.is_dir()]
            if any(info.file_size > MAX_MEMBER_BYTES for info in regular):
                raise ArchiveError("A ZIP member exceeds the safe 128 MiB rebuilding limit.")
            if sum(info.file_size for info in regular) > MAX_ARCHIVE_BYTES:
                raise ArchiveError("The expanded ZIP exceeds the safe 512 MiB rebuilding limit.")
            with zipfile.ZipFile(output, "w", allowZip64=True) as destination:
                destination.comment = source.comment
                for info in source.infolist():
                    if info.flag_bits & 0x1:
                        raise ArchiveError("Password-protected ZIP members cannot be rebuilt safely.")
                    if stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF):
                        raise ArchiveError("ZIP symbolic links cannot be rebuilt safely.")
                    payload = content if info.filename == match["source"] else (b"" if info.is_dir() else source.read(info))
                    destination.writestr(copy(info), payload)
    elif kind == "tar":
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as source:
            regular = [info for info in source.getmembers() if info.isfile()]
            if any(info.size > MAX_MEMBER_BYTES for info in regular):
                raise ArchiveError("A TAR member exceeds the safe 128 MiB rebuilding limit.")
            if sum(info.size for info in regular) > MAX_ARCHIVE_BYTES:
                raise ArchiveError("The expanded TAR exceeds the safe 512 MiB rebuilding limit.")
            with tarfile.open(fileobj=output, mode=_tar_write_mode(data, filename)) as destination:
                for info in source.getmembers():
                    cloned = copy(info)
                    if info.isfile():
                        if info.name == match["source"]:
                            payload = content
                        else:
                            extracted = source.extractfile(info)
                            if extracted is None:
                                raise ArchiveError(f"TAR member {info.name} could not be read while rebuilding.")
                            payload = extracted.read(MAX_MEMBER_BYTES + 1)
                            if len(payload) > MAX_MEMBER_BYTES:
                                raise ArchiveError(f"TAR member {info.name} exceeds the safe rebuilding limit.")
                        cloned.size = len(payload)
                        destination.addfile(cloned, io.BytesIO(payload))
                    else:
                        destination.addfile(cloned)
    elif kind == "gzip":
        with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as compressed:
            compressed.write(content)
    elif kind == "bz2":
        output.write(bz2.compress(content))
    elif kind == "xz":
        output.write(lzma.compress(content))
    elif kind == "dms":
        payload, compressed_source = _dms_payload(data)
        try:
            rebuilt, _report = replace_dms_file(payload, int(match["source"]), content)
        except DMSError as exc:
            raise ArchiveError(str(exc)) from exc
        if compressed_source:
            # A container that arrived compressed leaves the same way.
            with gzip.GzipFile(fileobj=output, mode="wb", filename="", mtime=0) as repacked:
                repacked.write(rebuilt)
        else:
            output.write(rebuilt)
    else:
        raise ArchiveError("That archive format cannot be rebuilt safely.")
    rebuilt = output.getvalue()
    if len(rebuilt) > MAX_ARCHIVE_BYTES:
        raise ArchiveError("The rebuilt archive exceeds the safe 512 MiB limit.")
    return rebuilt
