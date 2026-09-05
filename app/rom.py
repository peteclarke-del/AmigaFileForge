"""Inspection and safe editing helpers for Amiga ROM images.

A ROM is a byte image, not a filing system, so the workbench presents fixed
size *banks* as its objects and keeps layout choices in session metadata. What
makes an Amiga ROM readable rather than opaque is that it describes itself in
three independent ways, and this module decodes all three:

* The **image header**: ``$1111`` or ``$1114`` followed by a ``JMP``, which
  says how large the ROM is and where the machine starts executing.
* The **resident tags**: ``$4AFC`` followed by a pointer back to itself, one
  per module, each carrying a name, a version, a priority and an
  identification string. That self-reference is what separates a real tag from
  the same two bytes occurring inside code.
* The **footer**: the declared size and the ones-complement checksum that the
  ROM overlay logic verifies at reset.

A bank with none of those is reported as raw data rather than guessed at.
"""

from __future__ import annotations

import math
import struct
import zlib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from .checksum import sha256_bytes

#: Kickstart 1.x is one 256 KiB bank; 2.0 and later are 512 KiB.
DEFAULT_BANK_SIZE = 256 * 1024
MIN_BANK_SIZE = 256
MAX_ROM_SIZE = 64 * 1024 * 1024

#: A 512 KiB Kickstart is programmed as one 16-bit device or as a pair of
#: byte-wide 27C400 EPROMs holding the even and odd bytes.
ROM_LAYOUTS = {"linear", "byte-interleaved-2", "byte-interleaved-4"}
ROM_PLATFORMS = {"kickstart", "cartridge", "custom"}

#: Where each ROM size appears in the 68000 address space.
ROM_BASES = {
    256 * 1024: 0xFC0000,
    512 * 1024: 0xF80000,
    1024 * 1024: 0xF00000,
}
DEFAULT_ROM_BASE = 0xF80000

HEADER_256K = 0x1111
HEADER_512K = 0x1114
JMP_ABSOLUTE_LONG = 0x4EF9
RTC_MATCHWORD = 0x4AFC
RESIDENT_SIZE = 0x1A

#: The CD32 and CDTV extended ROMs identify themselves with this trailer.
EXTENDED_ROM_SIGNATURE = b"EXTROM00"


class RomError(ValueError):
    pass


@dataclass(frozen=True)
class RomHeader:
    """The decoded identity of one ROM bank."""

    title: str
    version: str
    copyright: str
    version_byte: int
    type_byte: int
    language_entry: int | None
    service_entry: int | None
    title_capacity: int
    metadata_end: int
    base: int = DEFAULT_ROM_BASE
    declared_size: int = 0
    checksum: int = 0
    calculated_checksum: int = 0
    module_count: int = 0

    @property
    def roles(self) -> str:
        roles = []
        if self.type_byte & 0x40:
            roles.append("Kickstart")
        if self.type_byte & 0x80:
            roles.append("autoboot")
        return " + ".join(roles) or "resident"

    @property
    def processor(self) -> str:
        return {
            0x0: "68000",
            0x1: "68010",
            0x2: "68020",
            0x3: "68030",
            0x4: "68040",
            0x6: "68060",
        }.get(self.type_byte & 0x0F, "68000 or later")

    @property
    def checksum_valid(self) -> bool:
        return self.checksum == self.calculated_checksum

    @property
    def features(self) -> list[str]:
        features = []
        if self.type_byte & 0x20:
            features.append("extended ROM overlay")
        if self.type_byte & 0x10:
            features.append("diagnostic entry point")
        return features


@dataclass(frozen=True)
class ExtendedRomHeader:
    """The size and checksum trailer of a CD32 or CDTV extended ROM."""

    declared_size: int
    checksum: int
    calculated_checksum: int

    @property
    def checksum_valid(self) -> bool:
        return self.checksum == self.calculated_checksum


#: Retained under its previous name so established call sites keep working.


def _cstring(data: bytes, start: int, limit: int = 255) -> tuple[str, int] | None:
    if start < 0 or start >= len(data):
        return None
    end = data.find(b"\0", start, min(len(data), start + limit + 1))
    if end < 0:
        return None
    raw = data[start:end]
    if not raw or any(byte < 32 or byte > 126 for byte in raw):
        return None
    return raw.decode("latin-1"), end


def rom_base(size: int) -> int:
    """Return the address a ROM of this size is mapped at."""
    return ROM_BASES.get(int(size), 0x1000000 - int(size) if size else DEFAULT_ROM_BASE)


def rom_checksum(data: bytes, skip_offset: int | None = None) -> int:
    """Return the ones-complement checksum a machine verifies at reset."""
    total = 0
    for offset in range(0, len(data) - 3, 4):
        if skip_offset is not None and offset == skip_offset:
            continue
        (value,) = struct.unpack_from(">I", data, offset)
        total += value
        if total > 0xFFFFFFFF:
            total = (total & 0xFFFFFFFF) + 1
    return (~total) & 0xFFFFFFFF


def _jmp_target(data: bytes, offset: int) -> int | None:
    """Decode ``JMP <32-bit address>`` at ``offset``, if one is present."""
    if len(data) < offset + 6:
        return None
    (opcode,) = struct.unpack_from(">H", data, offset)
    if opcode != JMP_ABSOLUTE_LONG:
        return None
    (target,) = struct.unpack_from(">I", data, offset + 2)
    return target if 0xF00000 <= target <= 0xFFFFFF else None


def parse_rom_header(data: bytes) -> RomHeader | None:
    """Return a ROM's identity when its structures are sound.

    Two shapes are accepted, because both appear in the wild: a complete
    machine ROM with a size header and a reset vector, and a bare expansion
    ROM that begins directly with its resident tag. Anything else returns
    ``None`` rather than a header built from coincidence.
    """
    if len(data) < 32:
        return None

    (magic,) = struct.unpack_from(">H", data, 0)
    entry = _jmp_target(data, 2)
    base = rom_base(len(data))
    tags = list(_resident_tags(data, base))

    if magic in (HEADER_256K, HEADER_512K) and entry is not None:
        (version, revision) = struct.unpack_from(">HH", data, 12)
        declared = struct.unpack_from(">I", data, len(data) - 20)[0] if len(data) >= 20 else 0
        stored = struct.unpack_from(">I", data, len(data) - 24)[0] if len(data) >= 24 else 0
        first = tags[0] if tags else None
        return RomHeader(
            title=first["name"] if first else "Kickstart",
            version=f"{version}.{revision}",
            copyright=first["idString"] if first else "",
            version_byte=version & 0xFF,
            type_byte=0x40 | ((0x20 if declared and declared != len(data) else 0)),
            language_entry=entry,
            service_entry=tags[0]["init"] if tags and tags[0]["init"] else None,
            title_capacity=len(first["name"]) if first else 0,
            metadata_end=tags[0]["end"] if tags else 16,
            base=base,
            declared_size=declared,
            checksum=stored,
            calculated_checksum=rom_checksum(data, skip_offset=len(data) - 24),
            module_count=len(tags),
        )

    if tags and tags[0]["offset"] < 0x40:
        first = tags[0]
        return RomHeader(
            title=first["name"],
            version=str(first["version"]),
            copyright=first["idString"],
            version_byte=first["version"] & 0xFF,
            type_byte=0x80 if first["autoinit"] else 0x00,
            language_entry=None,
            service_entry=first["init"] or None,
            title_capacity=len(first["name"]),
            metadata_end=first["end"],
            base=base,
            module_count=len(tags),
        )
    return None


def parse_extended_rom_header(data: bytes) -> ExtendedRomHeader | None:
    """Return the CD32 and CDTV extended-ROM trailer, if present."""
    if len(data) < 16 or len(data) % 4:
        return None
    if data[-8:] != EXTENDED_ROM_SIGNATURE:
        return None
    declared_size = int.from_bytes(data[-16:-12], "big")
    if declared_size != len(data):
        return None
    checksum = int.from_bytes(data[-12:-8], "big")
    return ExtendedRomHeader(declared_size, checksum, rom_checksum(data[:-12]))


#: Retained under its previous name so established call sites keep working.


def is_erased(data: bytes, erase_byte: int = 0xFF) -> bool:
    return not data or not data.strip(bytes((erase_byte & 0xFF,)))


def validate_bank_size(value: int) -> int:
    size = int(value)
    if size < MIN_BANK_SIZE or size > MAX_ROM_SIZE or size % 256:
        raise RomError("ROM bank size must be a multiple of 256 bytes between 256 bytes and 64 MiB.")
    return size


def validate_layout(value: str) -> str:
    layout = str(value or "linear")
    if layout not in ROM_LAYOUTS:
        raise RomError("Choose a linear, two-chip or four-chip ROM byte layout.")
    return layout


def validate_platform(value: str) -> str:
    platform = str(value or "kickstart")
    if platform not in ROM_PLATFORMS:
        raise RomError("Choose a Kickstart, cartridge or custom ROM target.")
    return platform


def bank_count(size: int, bank_size: int) -> int:
    return (max(0, int(size)) + bank_size - 1) // bank_size


def printable_strings(data: bytes, minimum: int = 4, limit: int = 513, base: int | None = None) -> list[dict]:
    """Return bounded printable ASCII runs as evidence, never as guessed files."""
    origin = DEFAULT_ROM_BASE if base is None else base
    found = []
    start = None
    for offset, value in enumerate(data + b"\0"):
        if 32 <= value <= 126:
            if start is None:
                start = offset
            continue
        if start is not None and offset - start >= minimum:
            text = data[start:offset].decode("latin-1")
            found.append({
                "offset": start,
                "address": origin + start,
                "length": offset - start,
                "text": text[:160] + ("…" if len(text) > 160 else ""),
            })
            if len(found) >= limit:
                break
        start = None
    return found


def byte_diagnostics(data: bytes, erase_byte: int, deep: bool = True) -> dict:
    fingerprints = {
        "sha256": sha256_bytes(data),
        "crc32": f"{zlib.crc32(data) & 0xFFFFFFFF:08X}",
    }
    if not deep:
        return fingerprints
    counts = Counter(data)
    length = len(data)
    entropy = -sum(
        (count / length) * math.log2(count / length)
        for count in counts.values()
    ) if length else 0.0
    erased = erase_byte & 0xFF
    used_start = next((offset for offset, value in enumerate(data) if value != erased), None)
    used_end = next(
        (offset for offset in range(length - 1, -1, -1) if data[offset] != erased),
        None,
    )
    return {
        **fingerprints,
        "entropy": round(entropy, 3),
        "uniqueByteValues": len(counts),
        "zeroBytes": counts.get(0, 0),
        "ffBytes": counts.get(0xFF, 0),
        "printableBytes": sum(counts.get(value, 0) for value in range(32, 127)),
        "erasedBytes": counts.get(erased, 0),
        "usedStart": used_start,
        "usedEnd": used_end,
    }


NODE_TYPES = {
    0: "unknown", 1: "task", 2: "interrupt", 3: "device", 4: "msgport",
    5: "message", 6: "freemsg", 7: "replymsg", 8: "resource", 9: "library",
    10: "memory", 11: "softint", 12: "font", 13: "process", 14: "semaphore",
    15: "signalsem", 16: "bootnode", 17: "kickmem", 18: "graphics",
}


def _resident_tags(data: bytes, base: int, limit: int = 256):
    """Yield every structurally sound resident tag in the image.

    The self-referential ``rt_MatchTag`` pointer is the whole reason this can
    be done reliably: a tag must point at its own address, so ``$4AFC``
    occurring inside instruction data is rejected without needing heuristics.
    """
    offset = 0
    end_limit = len(data) - RESIDENT_SIZE
    found = 0
    while offset <= end_limit and found < limit:
        index = data.find(b"\x4a\xfc", offset)
        if index < 0 or index > end_limit:
            return
        offset = index + 2
        if index % 2:
            continue
        (match_tag,) = struct.unpack_from(">I", data, index + 2)
        if match_tag != base + index:
            continue
        (end_skip,) = struct.unpack_from(">I", data, index + 6)
        flags = data[index + 10]
        version = data[index + 11]
        node_type = data[index + 12]
        (priority,) = struct.unpack_from(">b", data, index + 13)
        (name_pointer,) = struct.unpack_from(">I", data, index + 14)
        (id_pointer,) = struct.unpack_from(">I", data, index + 18)
        (init_pointer,) = struct.unpack_from(">I", data, index + 22)
        name = _cstring(data, name_pointer - base, 60) if name_pointer else None
        if name is None:
            continue
        identification = _cstring(data, id_pointer - base, 160) if id_pointer else None
        module_end = end_skip - base
        if not 0 < module_end <= len(data):
            module_end = min(len(data), index + RESIDENT_SIZE)
        found += 1
        yield {
            "offset": index,
            "address": base + index,
            "name": name[0],
            "idString": identification[0] if identification else "",
            "version": version,
            "priority": priority,
            "flags": flags,
            "autoinit": bool(flags & 0x80),
            "nodeType": node_type,
            "nodeTypeName": NODE_TYPES.get(node_type, f"type {node_type}"),
            "init": init_pointer or None,
            "end": module_end,
            "length": max(0, module_end - index),
        }


def resident_module_candidates(data: bytes, limit: int = 64, base: int | None = None) -> list[dict]:
    """List the resident modules a machine's ROM scan would find."""
    origin = rom_base(len(data)) if base is None else base
    modules = []
    for tag in _resident_tags(data, origin, limit):
        modules.append({
            "offset": tag["offset"],
            "title": tag["name"],
            "help": tag["idString"],
            "start": tag["address"],
            "initialise": tag["init"],
            "finalise": None,
            "service": None,
            "commands": None,
            "commandKeywords": _library_function_names(data, tag, origin),
            "version": tag["version"],
            "priority": tag["priority"],
            "nodeType": tag["nodeTypeName"],
            "autoinit": tag["autoinit"],
            "length": tag["length"],
        })
        if len(modules) >= limit:
            break
    return modules


#: Retained under its previous name so established call sites keep working.


def _library_function_names(data: bytes, tag: dict, base: int, limit: int = 256) -> list[dict]:
    """Recover a library's function names from its auto-init name table.

    An auto-initialised library points at a table of ``LVO`` entries. When the
    build kept its symbol names, they appear as a NUL-terminated run just after
    the identification string. Only names that look like Amiga entry points are
    reported, and every one is marked as a candidate rather than as declared,
    because a ROM is not required to keep them at all.
    """
    if not tag["autoinit"] or not tag["init"]:
        return []
    start = tag["init"] - base
    if not 0 <= start < len(data):
        return []
    names: list[dict] = []
    cursor = start
    window = min(len(data), start + 4096)
    while cursor < window and len(names) < limit:
        found = _cstring(data, cursor, 40)
        if found is None:
            cursor += 1
            continue
        text, end = found
        cursor = end + 1
        if len(text) < 4 or not text[0].isalpha():
            continue
        if not all(character.isalnum() or character == "_" for character in text):
            continue
        names.append({
            "name": text,
            "offset": end - len(text),
            "address": base + end - len(text),
            "entryOffset": None,
            "confidence": "strong candidate",
            "helpText": "",
            "helpOnly": False,
        })
    return names


def star_command_inventory(data: bytes, modules: list[dict] | None = None) -> list[dict]:
    """List the libraries, devices and resources a ROM makes available.

    On an Amiga the equivalent of a command inventory is the set of resident
    modules the ROM scan installs, because that is what other software can
    call. Each is reported with its node type, version and priority, which is
    what decides the order the machine initialises them in.
    """
    inventory: list[dict] = []
    for module in modules or []:
        inventory.append({
            "name": module["title"],
            "offset": module["offset"],
            "address": module.get("start"),
            "module": module["title"],
            "confidence": "declared",
            "helpText": module.get("help", ""),
            "nodeType": module.get("nodeType", ""),
            "version": module.get("version"),
            "priority": module.get("priority"),
            "handlerOffset": module.get("initialise"),
        })
        for function in module.get("commandKeywords", []):
            inventory.append({
                **function,
                "module": module["title"],
                "handlerOffset": None,
            })
    confidence_rank = {"declared": 3, "strong candidate": 2}
    unique: dict[str, dict] = {}
    for entry in inventory:
        key = entry["name"].casefold()
        current = unique.get(key)
        if (
            current is None
            or confidence_rank[entry["confidence"]] > confidence_rank[current["confidence"]]
            or (
                confidence_rank[entry["confidence"]] == confidence_rank[current["confidence"]]
                and entry.get("helpText")
                and not current.get("helpText")
            )
        ):
            unique[key] = entry
    return sorted(unique.values(), key=lambda item: (item["name"].casefold(), item["offset"]))


def inspect_bank(
    data: bytes,
    number: int,
    erase_byte: int = 0xFF,
    extension_header: ExtendedRomHeader | None = None,
    include_contents: bool = False,
    include_resident_modules: bool = False,
) -> dict:
    header = parse_rom_header(data)
    blank = is_erased(data, erase_byte)
    base = header.base if header else rom_base(len(data))
    title = header.title if header else ("Empty bank" if blank else f"Bank {number:03d}")
    structures = []
    if header:
        structures.append({
            "kind": "header",
            "name": "ROM size header, reset vector and version",
            "offset": 0,
            "address": base,
            "length": 16,
        })
        for role, entry in (
            ("Reset entry point", header.language_entry),
            ("First module init routine", header.service_entry),
        ):
            if entry is not None:
                structures.append({
                    "kind": "entry",
                    "name": role,
                    "offset": entry - base,
                    "address": entry,
                    "length": None,
                })
        for tag in _resident_tags(data, base, 64):
            structures.append({
                "kind": "module",
                "name": f"{tag['name']} ({tag['nodeTypeName']} v{tag['version']})",
                "offset": tag["offset"],
                "address": tag["address"],
                "length": tag["length"],
            })
        if header.declared_size:
            structures.append({
                "kind": "footer",
                "name": "Declared size and reset checksum",
                "offset": max(0, len(data) - 24),
                "address": base + max(0, len(data) - 24),
                "length": 24,
            })
    elif not blank:
        structures.append({
            "kind": "payload",
            "name": "Raw code and data (no ROM header or resident tag recognised)",
            "offset": 0,
            "address": base,
            "length": len(data),
        })
    if extension_header:
        structures.append({
            "kind": "extension-header",
            "name": "Extended-ROM size and checksum trailer",
            "offset": max(0, len(data) - 16),
            "address": None,
            "length": 16,
        })
    strings = printable_strings(data, base=base) if include_contents and not blank else []
    modules = (
        resident_module_candidates(data, base=base)
        if include_contents and include_resident_modules and not blank
        else []
    )
    diagnostics = byte_diagnostics(data, erase_byte, deep=include_contents)
    programmed_bytes = len(data) - data.count(bytes((erase_byte & 0xFF,)))
    warnings = []
    if header:
        if header.declared_size and header.declared_size != len(data):
            warnings.append(
                f"The ROM declares {header.declared_size:,} bytes but this image holds "
                f"{len(data):,}. It is one part of a split set, or it was padded."
            )
        if header.declared_size and not header.checksum_valid:
            warnings.append(
                "The reset checksum does not match the ROM's contents. A real machine "
                "will refuse to start from it."
            )
        if not header.module_count:
            warnings.append("No resident module tags were found in this bank.")
    return {
        "slot": number,
        "bank": number,
        "name": title,
        "type": "rom-bank",
        "length": len(data),
        "attr": "EMPTY" if blank else "ROM",
        "empty": blank,
        "fileOffset": number * len(data),
        "programmedBytes": programmed_bytes,
        "programmedPercent": round(programmed_bytes * 100 / len(data), 1) if data else 0,
        "filetype": (
            f"Extended ROM ({'valid' if extension_header.checksum_valid else 'bad'} checksum)"
            if extension_header
            else f"{header.roles} · {header.module_count} module(s)" if header
            else "erased" if blank
            else "raw data"
        ),
        "header": ({
            "title": header.title,
            "version": header.version,
            "copyright": header.copyright,
            "versionByte": header.version_byte,
            "typeByte": header.type_byte,
            "typeHex": f"{header.type_byte:02X}",
            "roles": header.roles,
            "processor": header.processor,
            "features": header.features,
            "languageEntry": header.language_entry,
            "serviceEntry": header.service_entry,
            "titleCapacity": header.title_capacity,
            "metadataEnd": header.metadata_end,
            "base": header.base,
            "declaredSize": header.declared_size,
            "checksum": header.checksum,
            "calculatedChecksum": header.calculated_checksum,
            "checksumValid": header.checksum_valid,
            "moduleCount": header.module_count,
        } if header else None),
        "extensionHeader": ({
            "declaredSize": extension_header.declared_size,
            "checksum": extension_header.checksum,
            "calculatedChecksum": extension_header.calculated_checksum,
            "checksumValid": extension_header.checksum_valid,
        } if extension_header else None),
        "structures": structures,
        "strings": strings[:512],
        "stringsTruncated": len(strings) > 512,
        "diagnostics": diagnostics,
        "warnings": warnings,
        "modules": modules,
        "starCommands": star_command_inventory(data, modules) if include_contents and not blank else [],
    }


def inspect_image(path: Path, bank_size: int, erase_byte: int = 0xFF) -> list[dict]:
    size = path.stat().st_size
    rows = []
    with path.open("rb") as image:
        for number in range(bank_count(size, bank_size)):
            row = inspect_bank(image.read(bank_size), number, erase_byte)
            row["fileOffset"] = number * bank_size
            rows.append(row)
        if rows and size >= 16 and size % 4 == 0:
            image.seek(size - 16)
            trailer = image.read(16)
            if trailer[-8:] == EXTENDED_ROM_SIGNATURE:
                declared_size = int.from_bytes(trailer[:4], "big")
                if declared_size == size:
                    image.seek(0)
                    remaining = size - 12
                    total = 0
                    while remaining:
                        chunk = image.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        for offset in range(0, len(chunk) - 3, 4):
                            total += int.from_bytes(chunk[offset : offset + 4], "big")
                            if total > 0xFFFFFFFF:
                                total = (total & 0xFFFFFFFF) + 1
                        remaining -= len(chunk)
                    extension_header = ExtendedRomHeader(
                        declared_size,
                        int.from_bytes(trailer[4:8], "big"),
                        (~total) & 0xFFFFFFFF,
                    )
                    final_data = read_bank(path, len(rows) - 1, bank_size)
                    rows[-1] = inspect_bank(
                        final_data,
                        len(rows) - 1,
                        erase_byte,
                        extension_header,
                    )
    matches: dict[str, list[int]] = {}
    for row in rows:
        matches.setdefault(row["diagnostics"]["sha256"], []).append(row["bank"])
    for row in rows:
        row["matchingBanks"] = [
            bank for bank in matches[row["diagnostics"]["sha256"]]
            if bank != row["bank"]
        ]
    return rows


def read_bank(path: Path, number: int, bank_size: int) -> bytes:
    count = bank_count(path.stat().st_size, bank_size)
    if number < 0 or number >= count:
        raise RomError(f"ROM bank {number} does not exist.")
    with path.open("rb") as image:
        image.seek(number * bank_size)
        return image.read(bank_size)


def bank_number(path: str) -> int:
    value = str(path or "").strip().lower()
    for prefix in ("$.", ":", "/"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    if value.startswith("bank:"):
        value = value[5:]
    elif value.startswith("bank-"):
        value = value[5:]
    try:
        number = int(value, 10)
    except ValueError as exc:
        raise RomError("Choose a ROM bank.") from exc
    if number < 0:
        raise RomError("Choose a ROM bank.")
    return number


def make_expansion_rom(size: int, title: str, erase_byte: int = 0xFF) -> bytes:
    """Build an empty but structurally valid ROM around one resident tag.

    The result is what an expansion ROM looks like before its driver code is
    linked in. Its module init routine is ``MOVEQ #0,D0 / RTS``, which is a
    real, safe "nothing to install" answer rather than an address that would
    crash the machine if the ROM were fitted before it was finished.
    """
    size = validate_bank_size(size)
    if size < 1024:
        raise RomError("A ROM template needs at least 1 KiB.")
    clean_title = "".join(
        character for character in str(title or "forge") if 32 <= ord(character) <= 126
    )[:24] or "forge"
    from amiganut.kickfs.kickfs import SIZE_256K, SIZE_512K, build_rom

    if size in (SIZE_256K, SIZE_512K, 2 * SIZE_512K):
        return build_rom(
            size=size,
            name=f"{clean_title}.library",
            id_string=f"{clean_title}.library 1.0 (2026)",
        )

    # A non-standard size cannot carry a machine ROM header, so emit a bare
    # resident tag at the start of the bank instead. A real expansion ROM on a
    # non-standard device looks exactly like this.
    base = rom_base(size)
    data = bytearray(bytes((erase_byte & 0xFF,)) * size)
    tag_offset = 0x00
    name_offset = 0x40
    id_offset = 0x80
    init_offset = 0x100
    struct.pack_into(">H", data, tag_offset, RTC_MATCHWORD)
    struct.pack_into(">I", data, tag_offset + 2, base + tag_offset)
    struct.pack_into(">I", data, tag_offset + 6, base + init_offset + 4)
    data[tag_offset + 10] = 0x01
    data[tag_offset + 11] = 1
    data[tag_offset + 12] = 9
    struct.pack_into(">b", data, tag_offset + 13, 0)
    struct.pack_into(">I", data, tag_offset + 14, base + name_offset)
    struct.pack_into(">I", data, tag_offset + 18, base + id_offset)
    struct.pack_into(">I", data, tag_offset + 22, base + init_offset)
    name_bytes = f"{clean_title}.library".encode("latin-1") + b"\0"
    id_bytes = f"{clean_title}.library 1.0 (2026)".encode("latin-1") + b"\0"
    data[name_offset : name_offset + len(name_bytes)] = name_bytes
    data[id_offset : id_offset + len(id_bytes)] = id_bytes
    struct.pack_into(">HH", data, init_offset, 0x7000, 0x4E75)
    return bytes(data)


__all__ = [
    "DEFAULT_BANK_SIZE",
    "DEFAULT_ROM_BASE",
    "EXTENDED_ROM_SIGNATURE",
    "ExtendedRomHeader",
    "MAX_ROM_SIZE",
    "MIN_BANK_SIZE",
    "NODE_TYPES",
    "ROM_BASES",
    "ROM_LAYOUTS",
    "ROM_PLATFORMS",
    "RomError",
    "RomHeader",
    "bank_count",
    "bank_number",
    "byte_diagnostics",
    "inspect_bank",
    "inspect_image",
    "is_erased",
    "make_expansion_rom",
    "parse_extended_rom_header",
    "parse_rom_header",
    "printable_strings",
    "read_bank",
    "resident_module_candidates",
    "rom_base",
    "rom_checksum",
    "star_command_inventory",
    "validate_bank_size",
    "validate_layout",
    "validate_platform",
]
