"""Read an ISO 9660 CD image, including the Amiga extensions Amiga CDs use.

AmigaOS 3.5 and 3.9 were published on CD, as were the OS4 releases and a great
deal of other Amiga material, so a workshop that cannot open an ISO cannot see
any of it. This reads one.

It is read directly from the file rather than loaded into memory, because a CD
image is up to seven hundred megabytes and the interesting ones here are
already close to five hundred. A directory listing should not cost half a
gigabyte of resident memory, and an ISO is random access by construction, so
seeking is both cheaper and simpler than holding the whole disc.

Three naming schemes have to be reconciled, and real Amiga discs use all of
them. The base ISO name is upper case, eight-and-three by default, and carries
a ``;1`` version suffix that nobody wants to see. **Joliet** publishes proper
names in UCS-2 in a second directory tree. **Rock Ridge** publishes them in an
``NM`` entry attached to the ordinary record. The AmigaOS 3.5 disc has Joliet,
the 3.9 disc has Rock Ridge, and the OS4 disc has Rock Ridge with continuation
areas, so all three paths are exercised by discs somebody will actually open.

The one that matters most here is ``AS``, the Amiga extension to Rock Ridge. It
carries the file's protection bits and its comment, which is exactly the
metadata this application refuses to lose everywhere else: a loader that
arrives without its ``e`` bit does not run, and the failure looks nothing like
a missing permission. An Amiga CD records them, so they are read.

The reader is deliberately suspicious of its input. A CD image is a file from
somewhere else, and a malformed or hostile one must not be able to walk this
process into a loop, a hundred-thousand-entry listing or a path that escapes
the disc.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

#: Every ISO 9660 structure is addressed in 2048-byte logical sectors.
SECTOR = 2048

#: The volume descriptors begin here, after the sixteen reserved system
#: sectors a bootable disc uses.
FIRST_DESCRIPTOR_SECTOR = 16

#: Descriptor types this reader acts on.
PRIMARY, SUPPLEMENTARY, TERMINATOR = 1, 2, 255

#: A descriptor set that never terminates is a malformed disc, not a disc with
#: a great many descriptors.
MAX_DESCRIPTORS = 64

#: Bounds on what one disc may describe. A real CD holds a few tens of
#: thousands of files; these are set above anything genuine and below what
#: would make the interface unusable or exhaust memory.
MAX_ENTRIES = 200_000
MAX_DEPTH = 32

#: How much of a directory this reader will read. A directory extent larger
#: than this is corrupt: the largest real one is a few hundred kilobytes.
MAX_DIRECTORY_BYTES = 16 * 1024 * 1024

#: Rock Ridge lets an entry continue into another sector. Following a chain
#: for ever is the obvious way a crafted disc could hang the reader.
MAX_CONTINUATIONS = 8

#: The escape sequences that mark a supplementary descriptor as Joliet, which
#: is to say its names are UCS-2 rather than the base character set.
JOLIET_ESCAPES = (b"%/@", b"%/C", b"%/E")

#: Directory record flags.
FLAG_DIRECTORY = 0x02


class Iso9660Error(Exception):
    """A CD image that cannot be read, with the reason a person can act on."""


@dataclass
class IsoEntry:
    """One file or drawer on the disc, named the way a person expects."""

    name: str
    path: str
    directory: bool
    length: int = 0
    extent: int = 0
    #: Amiga protection bits from an ``AS`` entry, as the raw AmigaDOS long.
    #: ``None`` when the disc does not record them.
    protection: int | None = None
    #: The Amiga file comment from an ``AS`` entry.
    comment: str = ""
    #: Recording date, as the seven-byte ISO field decoded to a datestamp.
    datestamp: str = ""

    @property
    def is_file(self) -> bool:
        return not self.directory


@dataclass
class _Descriptor:
    """A primary or supplementary volume descriptor, reduced to what is used."""

    kind: int
    volume: str
    root_extent: int
    root_length: int
    joliet: bool = False
    system_use: bytes = field(default=b"", repr=False)


def _both_endian_32(data: bytes, offset: int) -> int:
    """Read an ISO "both byte orders" long, trusting the little-endian half.

    Every such field stores the value twice, once each way. Reading one and
    ignoring the other is what every implementation does, because a disc whose
    halves disagree is corrupt in a way this reader cannot repair anyway.
    """
    return struct.unpack_from("<I", data, offset)[0]


def _decode_name(raw: bytes, joliet: bool) -> str:
    """Turn an ISO file identifier into the name to show.

    Joliet identifiers are UCS-2 big endian. Base identifiers are ASCII, upper
    case, and carry a ``;1`` version suffix that is an artefact of the format
    rather than part of the name.
    """
    if joliet:
        try:
            name = raw.decode("utf-16-be")
        except UnicodeDecodeError:
            name = raw.decode("latin-1", "replace")
    else:
        name = raw.decode("latin-1", "replace")
    version = name.rfind(";")
    if version > 0:
        name = name[:version]
    # A file with no extension is written "NAME." by some mastering tools.
    if name.endswith(".") and len(name) > 1:
        name = name[:-1]
    return name


def _decode_datestamp(raw: bytes) -> str:
    """Decode the seven-byte directory-record recording date.

    Returned in the same ISO-like spelling the rest of the workshop shows, and
    left empty rather than guessed at when the field is unset, which is what a
    disc mastered without dates leaves behind.
    """
    if len(raw) < 7 or not raw[0]:
        return ""
    year = 1900 + raw[0]
    month, day, hour, minute, second = raw[1], raw[2], raw[3], raw[4], raw[5]
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return ""
    return f"{year:04d}-{month:02d}-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}"


def _susp_entries(area: bytes) -> list[tuple[str, int, bytes]]:
    """Split a system-use area into its Rock Ridge entries.

    Each is a two-letter signature, a length that includes the four-byte
    header, a version and a payload. A length below the header size cannot
    advance, so it ends the walk rather than looping on the same offset.
    """
    entries: list[tuple[str, int, bytes]] = []
    offset = 0
    while offset + 4 <= len(area):
        signature = area[offset:offset + 2]
        length = area[offset + 2]
        version = area[offset + 3]
        if length < 4 or offset + length > len(area):
            break
        if not signature.isalpha():
            break
        entries.append((signature.decode("ascii"), version, area[offset + 4:offset + length]))
        offset += length
    return entries


def _amiga_metadata(payload: bytes) -> tuple[int | None, str]:
    """Decode an ``AS`` entry into Amiga protection bits and a comment.

    The Amiga extension records what AmigaDOS keeps and ISO 9660 has nowhere to
    put. A flags byte says which of the two are present, the protection is a
    four-byte field whose low half is the ``hsparwed`` long, and the comment is
    a counted string.
    """
    if not payload:
        return None, ""
    flags = payload[0]
    offset = 1
    protection: int | None = None
    comment = ""
    if flags & 0x01:
        if len(payload) < offset + 4:
            return None, ""
        protection = struct.unpack_from(">I", payload, offset)[0] & 0xFFFF
        offset += 4
    if flags & 0x02 and len(payload) > offset:
        size = payload[offset]
        offset += 1
        comment = payload[offset:offset + size].decode("latin-1", "replace")
    return protection, comment


class Iso9660Image:
    """A CD image, read from the file rather than held in memory."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        try:
            self._handle = self.path.open("rb")
        except OSError as exc:
            raise Iso9660Error(f"{self.path.name} could not be opened: {exc}") from exc
        try:
            self._size = self.path.stat().st_size
            self._descriptor = self._read_descriptors()
        except Exception:
            self._handle.close()
            raise

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()

    def __enter__(self) -> Iso9660Image:
        return self

    def __exit__(self, *_exception) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Volume
    # ------------------------------------------------------------------

    @property
    def volume(self) -> str:
        """The name the disc calls itself."""
        return self._descriptor.volume

    @property
    def joliet(self) -> bool:
        """Whether names are being read from a Joliet tree."""
        return self._descriptor.joliet

    def _sector(self, index: int, count: int = 1) -> bytes:
        if index < 0 or (index + count) * SECTOR > self._size + SECTOR:
            raise Iso9660Error("The disc refers to a sector beyond its own end.")
        self._handle.seek(index * SECTOR)
        return self._handle.read(SECTOR * count)

    def _read_descriptors(self) -> _Descriptor:
        """Choose the tree to read the disc through.

        A disc may publish the same files twice: once in the base tree and
        again in a Joliet tree carrying long names. Preferring Joliet is the
        usual choice and it is the wrong one here, because the Amiga
        extensions hang off the base tree's records. Reading the AmigaOS 3.5
        disc through Joliet produced perfectly good names and lost the
        protection bits and comments on all six thousand files.

        So the base tree wins whenever it carries Rock Ridge, which is what
        ``AS`` and ``NM`` indicate, and Joliet is the fallback for a disc whose
        base tree really is eight-and-three upper case with nothing attached.

        The first primary descriptor wins. The AmigaOS 3.9 disc carries two,
        which is not what the standard describes, and taking the last would
        read a different tree from the one every other reader uses.
        """
        primary: _Descriptor | None = None
        joliet: _Descriptor | None = None
        for index in range(MAX_DESCRIPTORS):
            sector = FIRST_DESCRIPTOR_SECTOR + index
            block = self._sector(sector)
            if len(block) < SECTOR or block[1:6] != b"CD001":
                break
            kind = block[0]
            if kind == TERMINATOR:
                break
            if kind not in (PRIMARY, SUPPLEMENTARY):
                continue
            is_joliet = kind == SUPPLEMENTARY and any(
                block[88:120].startswith(escape) for escape in JOLIET_ESCAPES
            )
            root = block[156:190]
            descriptor = _Descriptor(
                kind=kind,
                volume=block[40:72].decode("latin-1").rstrip(" \x00").strip(),
                root_extent=_both_endian_32(root, 2),
                root_length=_both_endian_32(root, 10),
                joliet=is_joliet,
            )
            if is_joliet:
                descriptor.volume = (
                    block[40:72].decode("utf-16-be", "replace").rstrip(" \x00").strip()
                )
                if joliet is None:
                    joliet = descriptor
            elif kind == PRIMARY and primary is None:
                primary = descriptor
        chosen = primary if primary is not None and self._has_rock_ridge(primary) else (joliet or primary)
        if chosen is None:
            raise Iso9660Error(
                f"{self.path.name} has no ISO 9660 volume descriptor, so it is not a CD image."
            )
        if not chosen.volume:
            chosen.volume = self.path.stem
        return chosen

    def _has_rock_ridge(self, descriptor: _Descriptor) -> bool:
        """Whether this tree's records carry Rock Ridge or the Amiga extension.

        Answered by looking at the root's own records rather than by trusting
        the ``SP`` indicator alone, because what matters is whether the
        entries an operator will read actually carry ``NM`` and ``AS``.
        """
        try:
            block = self._sector(
                descriptor.root_extent,
                -(-min(descriptor.root_length, SECTOR * 4) // SECTOR) or 1,
            )
        except Iso9660Error:
            return False
        offset = 0
        seen = 0
        while offset + 33 <= len(block) and seen < 8:
            record_length = block[offset]
            if record_length < 33 or offset + record_length > len(block):
                break
            name_length = block[offset + 32]
            system_start = 33 + name_length + ((name_length + 1) % 2)
            for signature, _version, _payload in _susp_entries(
                block[offset + system_start:offset + record_length]
            ):
                if signature in ("NM", "AS"):
                    return True
            seen += 1
            offset += record_length
        return False

    # ------------------------------------------------------------------
    # Directories
    # ------------------------------------------------------------------

    def _continuation(self, payload: bytes) -> bytes:
        """Read a Rock Ridge ``CE`` continuation area."""
        if len(payload) < 24:
            return b""
        block = _both_endian_32(payload, 0)
        offset = _both_endian_32(payload, 8)
        length = _both_endian_32(payload, 16)
        if length <= 0 or length > SECTOR:
            return b""
        sectors = self._sector(block, 1 + (offset + length) // SECTOR)
        return sectors[offset:offset + length]

    def _rock_ridge(self, area: bytes) -> tuple[str, int | None, str]:
        """Read the alternate name and Amiga metadata out of a system-use area.

        ``CE`` chains are followed a bounded number of times: a disc that
        pointed a continuation at itself would otherwise never finish.
        """
        name_parts: list[str] = []
        protection: int | None = None
        comment = ""
        pending = area
        for _ in range(MAX_CONTINUATIONS):
            if not pending:
                break
            following = b""
            for signature, _version, payload in _susp_entries(pending):
                if signature == "NM" and payload:
                    # The low flag bit says another NM entry continues this
                    # name, which is how a long name spans continuations.
                    name_parts.append(payload[1:].decode("latin-1", "replace"))
                elif signature == "AS":
                    amiga_protection, amiga_comment = _amiga_metadata(payload)
                    if amiga_protection is not None:
                        protection = amiga_protection
                    if amiga_comment:
                        comment = amiga_comment
                elif signature == "CE":
                    following = self._continuation(payload)
            pending = following
        return "".join(name_parts), protection, comment

    def _records(self, extent: int, length: int, parent: str) -> list[IsoEntry]:
        """Decode one directory extent into entries.

        The first two records of every directory are the directory itself and
        its parent, identified by a single zero or one byte rather than by
        name. They are skipped rather than shown, because an operator browsing
        a disc does not want two unnamed entries at the top of every drawer.
        """
        if length <= 0:
            return []
        if length > MAX_DIRECTORY_BYTES:
            raise Iso9660Error("A directory on this disc is implausibly large.")
        block = self._sector(extent, -(-length // SECTOR))[:length]
        entries: list[IsoEntry] = []
        offset = 0
        while offset < len(block):
            record_length = block[offset]
            if record_length == 0:
                # Records do not straddle sectors, so a zero length means the
                # rest of this sector is padding.
                offset = (offset // SECTOR + 1) * SECTOR
                continue
            if record_length < 33 or offset + record_length > len(block):
                break
            record = block[offset:offset + record_length]
            name_length = record[32]
            raw_name = record[33:33 + name_length]
            flags = record[25]
            if name_length == 1 and raw_name in (b"\x00", b"\x01"):
                offset += record_length
                continue
            system_start = 33 + name_length + ((name_length + 1) % 2)
            alternate, protection, comment = self._rock_ridge(record[system_start:])
            name = alternate or _decode_name(raw_name, self._descriptor.joliet)
            if not name or name in (".", "..") or "/" in name:
                offset += record_length
                continue
            entries.append(IsoEntry(
                name=name,
                path=f"{parent}/{name}" if parent else name,
                directory=bool(flags & FLAG_DIRECTORY),
                length=_both_endian_32(record, 10),
                extent=_both_endian_32(record, 2),
                protection=protection,
                comment=comment,
                datestamp=_decode_datestamp(record[18:25]),
            ))
            offset += record_length
        return entries

    def _resolve(self, path: str) -> IsoEntry:
        """Find one entry by path, walking from the root a step at a time."""
        parts = [part for part in str(path or "").replace("\\", "/").split("/") if part and part != "."]
        if any(part == ".." for part in parts):
            raise Iso9660Error("A path on this disc cannot step outside it.")
        if len(parts) > MAX_DEPTH:
            raise Iso9660Error("That path is nested more deeply than any real disc.")
        entry = IsoEntry(
            name=self.volume,
            path="",
            directory=True,
            extent=self._descriptor.root_extent,
            length=self._descriptor.root_length,
        )
        for index, part in enumerate(parts):
            if not entry.directory:
                raise Iso9660Error(f"{entry.path} is a file, so it has no contents.")
            wanted = part.casefold()
            found = next(
                (item for item in self._records(entry.extent, entry.length, "/".join(parts[:index]))
                 if item.name.casefold() == wanted),
                None,
            )
            if found is None:
                raise Iso9660Error(f"{path} is not on this disc.")
            entry = found
        return entry

    def list_directory(self, path: str = "") -> list[IsoEntry]:
        """Everything directly inside one drawer, drawers first then by name."""
        entry = self._resolve(path)
        if not entry.directory:
            raise Iso9660Error(f"{path} is a file, so it has no contents.")
        entries = self._records(entry.extent, entry.length, entry.path)
        return sorted(entries, key=lambda item: (not item.directory, item.name.casefold()))

    def walk(self, path: str = "") -> list[IsoEntry]:
        """Every entry at or below one drawer, parents before their contents."""
        collected: list[IsoEntry] = []
        pending = [(path, 0)]
        while pending:
            current, depth = pending.pop(0)
            if depth > MAX_DEPTH:
                raise Iso9660Error("This disc nests more deeply than any real one.")
            for entry in self.list_directory(current):
                collected.append(entry)
                if len(collected) > MAX_ENTRIES:
                    raise Iso9660Error("This disc lists more objects than any real one holds.")
                if entry.directory:
                    pending.append((entry.path, depth + 1))
        return collected

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------

    def read_file(self, path: str) -> bytes:
        """The bytes of one file on the disc."""
        entry = self._resolve(path)
        if entry.directory:
            raise Iso9660Error(f"{path} is a drawer, not a file.")
        return self.read_entry(entry)

    def read_entry(self, entry: IsoEntry) -> bytes:
        if entry.length <= 0:
            return b""
        data = self._sector(entry.extent, -(-entry.length // SECTOR))
        return data[:entry.length]


def is_iso_bytes(data: bytes) -> bool:
    """Whether a prefix looks like an ISO 9660 image.

    The identifier sits at the start of sector sixteen, so a shorter prefix
    cannot answer and is reported as "not an ISO" rather than guessed at.
    """
    offset = FIRST_DESCRIPTOR_SECTOR * SECTOR
    return len(data) >= offset + 6 and data[offset + 1:offset + 6] == b"CD001"


def is_iso_name(filename: str) -> bool:
    return str(filename or "").casefold().endswith((".iso", ".cdr"))


__all__ = [
    "Iso9660Error",
    "Iso9660Image",
    "IsoEntry",
    "SECTOR",
    "is_iso_bytes",
    "is_iso_name",
]
