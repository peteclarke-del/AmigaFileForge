from __future__ import annotations

import re


_INF_FIELDS = re.compile(r'"[^"]*"|\S+')


def _hex_field(value: str) -> int:
    return int(re.sub(r"^(?:&|0x)", "", value, flags=re.IGNORECASE), 16)


#: The protection letters AmigaDOS prints, most significant first. The low
#: four are inverted: a set bit means the operation is *denied*.
_PROTECTION_LETTERS = "hsparwed"
_INVERTED_BITS = 0x0F


def parse_protection(text: str) -> int | None:
    """Read an eight-letter protection field such as ``----rwed``."""
    cleaned = str(text or "").strip()
    if len(cleaned) != len(_PROTECTION_LETTERS):
        return None
    value = 0
    for index, letter in enumerate(_PROTECTION_LETTERS):
        bit = 1 << (len(_PROTECTION_LETTERS) - 1 - index)
        character = cleaned[index]
        if character == letter:
            present = True
        elif character == "-":
            present = False
        else:
            return None
        # A low bit is set when the operation is denied, so a printed letter
        # means the bit is clear.
        if present == bool(bit & _INVERTED_BITS):
            continue
        value |= bit
    return value


def format_protection(value: object) -> str:
    """Print a protection long the way ``List`` shows it."""
    protection = int(value or 0) & 0xFFFFFFFF
    letters = []
    for index, letter in enumerate(_PROTECTION_LETTERS):
        bit = 1 << (len(_PROTECTION_LETTERS) - 1 - index)
        allowed = not protection & bit if bit & _INVERTED_BITS else bool(protection & bit)
        letters.append(letter if allowed else "-")
    return "".join(letters)


def parse_inf(data: bytes | str) -> dict | None:
    """Parse the sidecar Amiga File Forge writes beside an exported file.

    The record is ``path protection length ["comment"]``, which is exactly the
    metadata an AmigaDOS entry carries. There is no address field: AmigaDOS
    records none, because a load file carries its own hunk header and the
    loader reads that.
    """
    text = data.decode("latin-1", "replace") if isinstance(data, bytes) else str(data)
    fields = _INF_FIELDS.findall(text.strip())
    if len(fields) < 2:
        return None
    name = fields[0].strip('"')
    protection = parse_protection(fields[1])
    if protection is not None:
        length = None
        comment = ""
        if len(fields) > 2:
            try:
                length = _hex_field(fields[2])
            except ValueError:
                length = None
        remainder = fields[3:] if length is not None else fields[2:]
        if remainder:
            comment = " ".join(remainder).strip('"')
        return {
            "name": name,
            "protection": protection,
            "access": protection,
            "length": length,
            "comment": comment,
            "locked": bool(protection & 0x04),
        }
    return None


def format_inf(path: str, metadata: dict) -> str:
    """Create one deterministic sidecar record from catalogue metadata."""
    catalogue_path = str(path or "File").strip() or "File"
    if any(character.isspace() for character in catalogue_path):
        catalogue_path = f'"{catalogue_path}"'
    protection = format_protection(metadata.get("protection", metadata.get("access")))
    length = int(metadata.get("length") or 0) & 0xFFFFFFFF
    comment = " ".join(str(metadata.get("comment") or "").split())
    trailing = f' "{comment}"' if comment else ""
    return f"{catalogue_path} {protection} {length:08X}{trailing}\n"


#: ZIP's host-system code for an Amiga. The Amiga port of Info-ZIP and the
#: AmigaOS archivers that follow it write this, and everything else does not.
ZIP_HOST_AMIGA = 1


def amiga_zip_metadata(info) -> dict | None:
    """Read AmigaDOS metadata from a ZIP entry's own central-directory fields.

    A ZIP written on an Amiga records host system 1 and keeps the file's
    protection long in the top 16 bits of the external attributes, in the same
    ``hsparwed`` form the volume itself uses. There is no extra field to
    decode: the information is part of the central directory, which is why an
    archive made on any other machine simply has nothing to report and this
    returns ``None`` rather than inventing a default.

    A per-entry comment is returned alongside it when the archive carries one,
    because that is where an AmigaDOS file comment survives a round trip
    through a ZIP.
    """
    if getattr(info, "create_system", None) != ZIP_HOST_AMIGA:
        return None
    protection = (int(getattr(info, "external_attr", 0) or 0) >> 16) & 0xFFFF
    metadata: dict = {"protection": protection}
    comment = getattr(info, "comment", b"") or b""
    if isinstance(comment, bytes):
        comment = comment.decode("latin-1", "replace")
    comment = " ".join(str(comment).split())
    if comment:
        metadata["comment"] = comment
    return metadata
