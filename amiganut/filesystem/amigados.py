"""The AmigaDOS Old and Fast File Systems.

One class covers ``DOS\\0`` to ``DOS\\5`` because the variants differ in only
three decisions: whether data blocks carry a 24-byte header (OFS) or not
(FFS), whether name hashing folds the accented Latin-1 letters (international
mode), and whether a directory keeps a cache block chain (directory cache).
Each of those is a single branch rather than a separate implementation, and
keeping them together is what makes a copy between an OFS floppy and an FFS
partition an ordinary operation instead of a conversion.

Paths use AmigaDOS syntax. The volume root is an empty path or ``:``; nested
entries are separated by ``/``. Names may contain full stops, which is why the
separator is not one.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from datetime import datetime, timezone

from ..errors import ConfigurationError, DataError
from ..file import (
    Access,
    AmigaMeta,
    DEFAULT_PROTECTION,
    datestamp_to_datetime,
    datetime_to_datestamp,
)
from .blocks import (
    DOS_TYPES,
    MAX_COMMENT,
    MAX_NAME,
    RESERVED_BLOCKS,
    ST_FILE,
    ST_LINKDIR,
    ST_LINKFILE,
    ST_ROOT,
    ST_SOFTLINK,
    ST_USERDIR,
    T_DATA,
    T_HEADER,
    T_LIST,
    WRITABLE_FORMATS,
    BlockReader,
    Geometry,
    apply_checksum,
    hash_name,
    is_dircache,
    is_ffs,
    is_international,
    long_at,
    names_match,
    put_long,
    put_signed_long,
    read_bstr,
    signed_long_at,
    verify_checksum,
    write_bstr,
)

OFS_DATA_HEADER = 24

# Offsets shared by root, directory and file header blocks.
OFF_TYPE = 0
OFF_HEADER_KEY = 4
OFF_HIGH_SEQ = 8
OFF_HT_SIZE = 12
OFF_FIRST_DATA = 16
OFF_CHECKSUM = 20
OFF_HASH_TABLE = 24

ILLEGAL_NAME_CHARACTERS = set(':/\\')


def _tail(block_size: int, back: int) -> int:
    """Offset of a field measured from the end of the block."""
    return block_size - back


@dataclass(frozen=True)
class Entry:
    """One catalogue entry as the workbench sees it."""

    name: str
    path: str
    is_dir: bool
    length: int
    block: int
    secondary_type: int

    @property
    def is_link(self) -> bool:
        return self.secondary_type in (ST_SOFTLINK, ST_LINKFILE, ST_LINKDIR)


@dataclass(frozen=True)
class Stat:
    """The result of ``stat`` on one path."""

    name: str
    path: str
    is_dir: bool
    length: int
    blocks: int
    block: int
    secondary_type: int


def split_path(path: str | None) -> list[str]:
    """Split an inner path into components, accepting every root spelling."""
    text = str(path or "").strip()
    if text in {"", ":", "$", "/"}:
        return []
    if text.startswith(":"):
        text = text[1:]
    elif text.startswith("$"):
        # The workbench addresses a volume root as ``$`` in legacy requests.
        text = text[1:].lstrip("/.")
    text = text.strip("/")
    if not text:
        return []
    return [part for part in text.split("/") if part not in {"", "."}]


def join_path(parts) -> str:
    return "/".join(parts)


def validate_name(name: str) -> str:
    """Reject a name AmigaDOS could not store, before anything is written."""
    text = str(name or "").strip()
    if not text:
        raise DataError("A name cannot be empty.")
    if len(text) > MAX_NAME:
        raise DataError(f"An Amiga name can hold at most {MAX_NAME} characters.")
    if any(character in ILLEGAL_NAME_CHARACTERS for character in text):
        raise DataError("An Amiga name cannot contain : / or \\.")
    if any(ord(character) < 32 for character in text):
        raise DataError("An Amiga name cannot contain control characters.")
    return text


class AmigaDOSVolume:
    """A mounted OFS or FFS volume."""

    def __init__(self, reader: BlockReader, geometry: Geometry | None = None):
        self.reader = reader
        self.block_size = reader.block_size
        self.total_blocks = reader.total_blocks
        self.geometry = geometry
        self.reserved = geometry.reserved if geometry else RESERVED_BLOCKS
        boot = reader.read_block(0) if self.total_blocks else b"\0" * self.block_size
        self.dos_type = boot[:4]
        if self.dos_type[:3] not in (b"DOS", b"PFS", b"SFS"):
            raise DataError(
                "No AmigaDOS boot block was found. The first four bytes are "
                f"{self.dos_type!r}, not a DOS, PFS or SFS signature."
            )
        self.format = DOS_TYPES.get(self.dos_type)
        if self.format is None:
            raise DataError(
                f"DOS type {self.dos_type[3]} is not a filing system this build recognises."
            )
        self.ffs = is_ffs(self.dos_type)
        self.international = is_international(self.dos_type)
        self.dircache = is_dircache(self.dos_type)
        self.read_only = self.format not in WRITABLE_FORMATS or not reader.writable
        self.data_capacity = self.block_size if self.ffs else self.block_size - OFS_DATA_HEADER
        self.root_block = self._locate_root()
        root = self.reader.read_block(self.root_block)
        self.hash_table_size = long_at(root, OFF_HT_SIZE) or (self.block_size // 4 - 56)
        self._bitmap: bytearray | None = None
        self._bitmap_blocks: list[int] = []
        self._dirty_bitmap = False

    # ---- geometry ----------------------------------------------------
    def _locate_root(self) -> int:
        """Find the root block, preferring the standard mid-volume position.

        AmigaDOS puts the root block half way through the volume, counted over
        every block including the reserved boot blocks: block 880 on a
        double-density floppy and 1760 on a high-density one. The neighbours
        are tried as well because some formatters round the other way.
        """
        candidate = self.total_blocks // 2
        for block in (candidate, candidate - 1, candidate + 1):
            if 0 <= block < self.total_blocks and self._is_root(block):
                return block
        # A truncated or over-long image still mounts if a root block exists
        # anywhere sensible, which is common for hand-trimmed dumps.
        for block in range(self.reserved, self.total_blocks):
            if self._is_root(block):
                return block
        raise DataError(
            "The volume has an AmigaDOS boot block but no readable root block. "
            "It is unformatted, truncated or damaged."
        )

    def _is_root(self, block: int) -> bool:
        try:
            data = self.reader.read_block(block)
        except DataError:
            return False
        return (
            long_at(data, OFF_TYPE) == T_HEADER
            and signed_long_at(data, _tail(self.block_size, 4)) == ST_ROOT
            and verify_checksum(data)
        )

    # ---- volume identity ---------------------------------------------
    @property
    def title(self) -> str:
        root = self.reader.read_block(self.root_block)
        return read_bstr(root, _tail(self.block_size, 80), MAX_NAME)

    def set_title(self, value: str) -> None:
        name = validate_name(value)
        self._require_writable()
        root = bytearray(self.reader.read_block(self.root_block))
        write_bstr(root, _tail(self.block_size, 80), name, MAX_NAME)
        self._stamp(root, _tail(self.block_size, 92))
        self.reader.write_block(self.root_block, bytes(apply_checksum(root)))

    def volume_datestamp(self) -> datetime:
        root = self.reader.read_block(self.root_block)
        base = _tail(self.block_size, 40)
        return datestamp_to_datetime(
            long_at(root, base), long_at(root, base + 4), long_at(root, base + 8)
        )

    def created_datestamp(self) -> datetime:
        root = self.reader.read_block(self.root_block)
        base = _tail(self.block_size, 28)
        return datestamp_to_datetime(
            long_at(root, base), long_at(root, base + 4), long_at(root, base + 8)
        )

    def size_bytes(self) -> int:
        return (self.total_blocks - self.reserved) * self.data_capacity

    def free_bytes(self) -> int:
        return self._free_block_count() * self.data_capacity

    def used_bytes(self) -> int:
        return self.size_bytes() - self.free_bytes()

    # ---- bitmap ------------------------------------------------------
    def _load_bitmap(self) -> bytearray:
        if self._bitmap is not None:
            return self._bitmap
        root = self.reader.read_block(self.root_block)
        if long_at(root, _tail(self.block_size, 200)) == 0:
            raise DataError(
                "The volume's block-allocation bitmap is marked invalid. "
                "Run a validation pass before writing to it."
            )
        pages = []
        base = _tail(self.block_size, 196)
        for index in range(25):
            block = long_at(root, base + index * 4)
            if block:
                pages.append(block)
        extension = long_at(root, _tail(self.block_size, 96))
        seen = set(pages)
        while extension:
            if extension in seen or not 0 <= extension < self.total_blocks:
                raise DataError("The bitmap extension chain is damaged.")
            seen.add(extension)
            page = self.reader.read_block(extension)
            for index in range(self.block_size // 4 - 1):
                block = long_at(page, index * 4)
                if block:
                    pages.append(block)
            extension = long_at(page, self.block_size - 4)
        self._bitmap_blocks = pages
        covered = self.total_blocks - self.reserved
        bits = bytearray(covered)
        for page_index, page_block in enumerate(pages):
            page = self.reader.read_block(page_block)
            longs = self.block_size // 4 - 1
            for long_index in range(longs):
                value = long_at(page, 4 + long_index * 4)
                for bit in range(32):
                    position = page_index * longs * 32 + long_index * 32 + bit
                    if position >= covered:
                        break
                    bits[position] = 1 if value & (1 << bit) else 0
        self._bitmap = bits
        return bits

    def _store_bitmap(self) -> None:
        if self._bitmap is None or not self._dirty_bitmap:
            return
        bits = self._bitmap
        longs = self.block_size // 4 - 1
        for page_index, page_block in enumerate(self._bitmap_blocks):
            page = bytearray(self.block_size)
            for long_index in range(longs):
                value = 0
                for bit in range(32):
                    position = page_index * longs * 32 + long_index * 32 + bit
                    if position < len(bits) and bits[position]:
                        value |= 1 << bit
                put_long(page, 4 + long_index * 4, value)
            put_long(page, 0, 0)
            apply_checksum(page, 0)
            self.reader.write_block(page_block, bytes(page))
        self._dirty_bitmap = False

    def _free_block_count(self) -> int:
        try:
            return sum(self._load_bitmap())
        except DataError:
            return 0

    def _is_free(self, block: int) -> bool:
        bits = self._load_bitmap()
        index = block - self.reserved
        return 0 <= index < len(bits) and bool(bits[index])

    def _allocate(self, near: int | None = None) -> int:
        """Reserve one block, preferring one close to ``near``.

        Allocating outwards from the file's own header is what keeps an
        AmigaDOS volume readable at speed on real hardware, because the drive
        does not seek across the platter between consecutive data blocks.
        """
        bits = self._load_bitmap()
        start = (near if near is not None else self.root_block) - self.reserved
        start = max(0, min(start, len(bits) - 1)) if bits else 0
        for distance in range(len(bits)):
            for candidate in ((start + distance), (start - distance)):
                if 0 <= candidate < len(bits) and bits[candidate]:
                    bits[candidate] = 0
                    self._dirty_bitmap = True
                    return candidate + self.reserved
        raise DataError("The volume is full.")

    def _release(self, block: int) -> None:
        bits = self._load_bitmap()
        index = block - self.reserved
        if 0 <= index < len(bits):
            bits[index] = 1
            self._dirty_bitmap = True

    # ---- block helpers -----------------------------------------------
    def _require_writable(self) -> None:
        if self.read_only:
            raise DataError(
                f"A {self.format} volume opened this way cannot be modified."
            )

    def _stamp(self, block: bytearray, offset: int, moment: datetime | None = None) -> None:
        days, mins, ticks = datetime_to_datestamp(moment or datetime.now(timezone.utc))
        put_long(block, offset, days)
        put_long(block, offset + 4, mins)
        put_long(block, offset + 8, ticks)

    def _read_header(self, block: int) -> bytes:
        if not 0 <= block < self.total_blocks:
            raise DataError(f"Block {block} is outside this volume.")
        data = self.reader.read_block(block)
        # A long file's block chain alternates between its header block and
        # T_LIST extension blocks; both carry the same pointer layout.
        if long_at(data, OFF_TYPE) not in (T_HEADER, T_LIST):
            raise DataError(f"Block {block} is not a header block.")
        return data

    def _entry_name(self, block: int) -> str:
        return read_bstr(self._read_header(block), _tail(self.block_size, 80), MAX_NAME)

    def _secondary_type(self, block: int) -> int:
        return signed_long_at(self._read_header(block), _tail(self.block_size, 4))

    def _hash_table(self, block: int) -> list[int]:
        data = self._read_header(block)
        return [
            long_at(data, OFF_HASH_TABLE + index * 4)
            for index in range(self.hash_table_size)
        ]

    # ---- lookup ------------------------------------------------------
    def _find_in_directory(self, directory_block: int, name: str) -> int | None:
        slot = hash_name(name, self.international, self.hash_table_size)
        data = self._read_header(directory_block)
        candidate = long_at(data, OFF_HASH_TABLE + slot * 4)
        seen = set()
        while candidate:
            if candidate in seen or not 0 <= candidate < self.total_blocks:
                raise DataError("A directory hash chain is damaged.")
            seen.add(candidate)
            if names_match(self._entry_name(candidate), name, self.international):
                return candidate
            candidate = long_at(
                self._read_header(candidate), _tail(self.block_size, 16)
            )
        return None

    def _resolve(self, path: str | None) -> tuple[int, list[str]]:
        parts = split_path(path)
        block = self.root_block
        for index, part in enumerate(parts):
            found = self._find_in_directory(block, part)
            if found is None:
                raise DataError(f"Path not found: {join_path(parts[: index + 1])}")
            block = found
            if index < len(parts) - 1 and self._secondary_type(block) not in (
                ST_USERDIR,
                ST_ROOT,
                ST_LINKDIR,
            ):
                raise DataError(f"{join_path(parts[: index + 1])} is not a directory.")
        return block, parts

    def exists(self, path: str | None) -> bool:
        try:
            self._resolve(path)
        except DataError:
            return False
        return True

    def stat(self, path: str | None) -> Stat:
        block, parts = self._resolve(path)
        secondary = ST_ROOT if block == self.root_block else self._secondary_type(block)
        is_dir = secondary in (ST_ROOT, ST_USERDIR, ST_LINKDIR)
        length = 0 if is_dir else long_at(self._read_header(block), _tail(self.block_size, 188))
        return Stat(
            name=parts[-1] if parts else self.title,
            path=join_path(parts),
            is_dir=is_dir,
            length=length,
            blocks=self._entry_block_count(block, is_dir),
            block=block,
            secondary_type=secondary,
        )

    def _entry_block_count(self, block: int, is_dir: bool) -> int:
        if is_dir:
            return 1
        size = long_at(self._read_header(block), _tail(self.block_size, 188))
        if size <= 0:
            return 1
        data_blocks = (size + self.data_capacity - 1) // self.data_capacity
        extensions = max(0, (data_blocks - 1) // self.hash_table_size)
        return 1 + data_blocks + extensions

    # ---- listing -----------------------------------------------------
    def iter_entries(self, path: str | None = None):
        block, parts = self._resolve(path)
        secondary = ST_ROOT if block == self.root_block else self._secondary_type(block)
        if secondary not in (ST_ROOT, ST_USERDIR, ST_LINKDIR):
            raise DataError(f"{join_path(parts)} is not a directory.")
        prefix = join_path(parts)
        for candidate in self._chain_blocks(block):
            child = self._read_header(candidate)
            name = read_bstr(child, _tail(self.block_size, 80), MAX_NAME)
            child_secondary = signed_long_at(child, _tail(self.block_size, 4))
            is_dir = child_secondary in (ST_USERDIR, ST_LINKDIR)
            yield Entry(
                name=name,
                path=f"{prefix}/{name}" if prefix else name,
                is_dir=is_dir,
                length=0 if is_dir else long_at(child, _tail(self.block_size, 188)),
                block=candidate,
                secondary_type=child_secondary,
            )

    def _chain_blocks(self, directory_block: int) -> list[int]:
        blocks: list[int] = []
        seen: set[int] = set()
        for candidate in self._hash_table(directory_block):
            while candidate:
                if candidate in seen or not 0 <= candidate < self.total_blocks:
                    raise DataError("A directory hash chain is damaged.")
                seen.add(candidate)
                blocks.append(candidate)
                candidate = long_at(
                    self._read_header(candidate), _tail(self.block_size, 16)
                )
        return blocks

    # ---- reading -----------------------------------------------------
    def read_bytes(self, path: str) -> bytes:
        block, parts = self._resolve(path)
        header = self._read_header(block)
        if signed_long_at(header, _tail(self.block_size, 4)) not in (ST_FILE, ST_LINKFILE):
            raise DataError(f"{join_path(parts)} is not a file.")
        size = long_at(header, _tail(self.block_size, 188))
        chunks: list[bytes] = []
        remaining = size
        for data_block in self._data_blocks(block):
            if remaining <= 0:
                break
            raw = self.reader.read_block(data_block)
            payload = raw[OFS_DATA_HEADER:] if not self.ffs else raw
            if not self.ffs:
                used = long_at(raw, 12)
                payload = payload[: max(0, min(used, len(payload)))]
            chunks.append(payload[:remaining])
            remaining -= len(chunks[-1])
        data = b"".join(chunks)
        if len(data) < size:
            raise DataError(
                f"{join_path(parts)} declares {size:,} bytes but only "
                f"{len(data):,} are present. The file is truncated."
            )
        return data[:size]

    def _data_blocks(self, header_block: int) -> list[int]:
        blocks: list[int] = []
        current = header_block
        seen = {header_block}
        while current:
            header = self._read_header(current)
            count = long_at(header, OFF_HIGH_SEQ)
            for index in range(count):
                offset = OFF_HASH_TABLE + (self.hash_table_size - 1 - index) * 4
                block = long_at(header, offset)
                if block:
                    blocks.append(block)
            current = long_at(header, _tail(self.block_size, 8))
            if current:
                if current in seen or not 0 <= current < self.total_blocks:
                    raise DataError("A file extension chain is damaged.")
                seen.add(current)
        return blocks

    # ---- metadata ----------------------------------------------------
    def amiga_meta(self, path: str) -> AmigaMeta:
        block, _parts = self._resolve(path)
        header = self._read_header(block)
        protection = long_at(header, _tail(self.block_size, 192))
        comment = read_bstr(header, _tail(self.block_size, 184), MAX_COMMENT)
        base = _tail(self.block_size, 92)
        stamp = datestamp_to_datetime(
            long_at(header, base), long_at(header, base + 4), long_at(header, base + 8)
        )
        return AmigaMeta(protection=protection, comment=comment, datestamp=stamp)

    def set_amiga_meta(self, path: str, meta: AmigaMeta) -> None:
        self._require_writable()
        block, _parts = self._resolve(path)
        header = bytearray(self._read_header(block))
        put_long(header, _tail(self.block_size, 192), int(meta.protection) & 0xFFFFFFFF)
        comment = str(meta.comment or "")
        if len(comment) > MAX_COMMENT:
            raise DataError(f"A comment can hold at most {MAX_COMMENT} characters.")
        write_bstr(header, _tail(self.block_size, 184), comment, MAX_COMMENT)
        if meta.datestamp is not None:
            self._stamp(header, _tail(self.block_size, 92), meta.datestamp)
        self.reader.write_block(block, bytes(apply_checksum(header)))

    def access(self, path: str) -> Access:
        return self.amiga_meta(path).access

    def set_access(self, path: str, access: Access | int) -> None:
        value = access.value if isinstance(access, Access) else int(access)
        meta = self.amiga_meta(path)
        self.set_amiga_meta(path, meta.with_protection(value))

    def comment(self, path: str) -> str:
        return self.amiga_meta(path).comment

    def set_comment(self, path: str, value: str) -> None:
        self.set_amiga_meta(path, self.amiga_meta(path).with_comment(value))

    def datestamp(self, path: str) -> datetime:
        return self.amiga_meta(path).datestamp

    def set_datestamp(self, path: str, moment: datetime) -> None:
        self._require_writable()
        block, _parts = self._resolve(path)
        header = bytearray(self._read_header(block))
        self._stamp(header, _tail(self.block_size, 92), moment)
        self.reader.write_block(block, bytes(apply_checksum(header)))

    # ---- writing -----------------------------------------------------
    def mkdir(self, path: str) -> int:
        self._require_writable()
        parts = split_path(path)
        if not parts:
            raise DataError("The volume root already exists.")
        name = validate_name(parts[-1])
        parent_block, _ = self._resolve(join_path(parts[:-1]))
        if self._find_in_directory(parent_block, name) is not None:
            raise DataError(f"{path} already exists.")
        block = self._allocate(parent_block)
        header = bytearray(self.block_size)
        put_long(header, OFF_TYPE, T_HEADER)
        put_long(header, OFF_HEADER_KEY, block)
        put_long(header, _tail(self.block_size, 192), DEFAULT_PROTECTION)
        write_bstr(header, _tail(self.block_size, 80), name, MAX_NAME)
        self._stamp(header, _tail(self.block_size, 92))
        put_long(header, _tail(self.block_size, 12), parent_block)
        put_signed_long(header, _tail(self.block_size, 4), ST_USERDIR)
        self.reader.write_block(block, bytes(apply_checksum(header)))
        self._link_into(parent_block, block, name)
        self._store_bitmap()
        return block

    def write_bytes(self, path: str, data: bytes, meta: AmigaMeta | None = None) -> int:
        """Create or replace a file, allocating its data blocks in order."""
        self._require_writable()
        parts = split_path(path)
        if not parts:
            raise DataError("A file needs a name.")
        name = validate_name(parts[-1])
        parent_block, _ = self._resolve(join_path(parts[:-1]))
        existing = self._find_in_directory(parent_block, name)
        preserved = None
        if existing is not None:
            preserved = self.amiga_meta(join_path(parts))
            self.remove(join_path(parts))
        payload = bytes(data)
        needed = (len(payload) + self.data_capacity - 1) // self.data_capacity
        extensions = max(0, (needed - 1) // self.hash_table_size)
        if needed + extensions + 1 > self._free_block_count():
            raise DataError(
                f"{len(payload):,} bytes need {needed + extensions + 1:,} blocks but only "
                f"{self._free_block_count():,} are free."
            )
        header_block = self._allocate(parent_block)
        data_blocks = [self._allocate(header_block) for _ in range(needed)]
        extension_blocks = [self._allocate(header_block) for _ in range(extensions)]

        # Data blocks first, so a failure never leaves a header pointing at
        # blocks that were never written.
        for index, block in enumerate(data_blocks):
            chunk = payload[index * self.data_capacity : (index + 1) * self.data_capacity]
            if self.ffs:
                self.reader.write_block(block, chunk.ljust(self.block_size, b"\0"))
                continue
            raw = bytearray(self.block_size)
            put_long(raw, 0, T_DATA)
            put_long(raw, 4, header_block)
            put_long(raw, 8, index + 1)
            put_long(raw, 12, len(chunk))
            put_long(raw, 16, data_blocks[index + 1] if index + 1 < needed else 0)
            raw[OFS_DATA_HEADER : OFS_DATA_HEADER + len(chunk)] = chunk
            self.reader.write_block(block, bytes(apply_checksum(raw)))

        chain = [header_block, *extension_blocks]
        for position, block in enumerate(chain):
            first = position * self.hash_table_size
            slice_blocks = data_blocks[first : first + self.hash_table_size]
            raw = bytearray(self.block_size)
            put_long(raw, OFF_TYPE, T_HEADER if position == 0 else T_LIST)
            put_long(raw, OFF_HEADER_KEY, block)
            put_long(raw, OFF_HIGH_SEQ, len(slice_blocks))
            put_long(raw, OFF_FIRST_DATA, slice_blocks[0] if slice_blocks else 0)
            for index, data_block in enumerate(slice_blocks):
                put_long(
                    raw,
                    OFF_HASH_TABLE + (self.hash_table_size - 1 - index) * 4,
                    data_block,
                )
            if position == 0:
                source = meta or preserved
                put_long(
                    raw,
                    _tail(self.block_size, 192),
                    int(source.protection) & 0xFFFFFFFF if source else DEFAULT_PROTECTION,
                )
                put_long(raw, _tail(self.block_size, 188), len(payload))
                if source and source.comment:
                    write_bstr(raw, _tail(self.block_size, 184), source.comment, MAX_COMMENT)
                self._stamp(
                    raw,
                    _tail(self.block_size, 92),
                    source.datestamp if source and source.datestamp else None,
                )
                write_bstr(raw, _tail(self.block_size, 80), name, MAX_NAME)
                put_long(raw, _tail(self.block_size, 12), parent_block)
            else:
                put_long(raw, _tail(self.block_size, 12), header_block)
            put_long(
                raw,
                _tail(self.block_size, 8),
                chain[position + 1] if position + 1 < len(chain) else 0,
            )
            put_signed_long(raw, _tail(self.block_size, 4), ST_FILE)
            self.reader.write_block(block, bytes(apply_checksum(raw)))

        self._link_into(parent_block, header_block, name)
        self._store_bitmap()
        return header_block

    def _link_into(self, parent_block: int, child_block: int, name: str) -> None:
        slot = hash_name(name, self.international, self.hash_table_size)
        parent = bytearray(self._read_header(parent_block))
        head = long_at(parent, OFF_HASH_TABLE + slot * 4)
        child = bytearray(self._read_header(child_block))
        put_long(child, _tail(self.block_size, 16), head)
        self.reader.write_block(child_block, bytes(apply_checksum(child)))
        put_long(parent, OFF_HASH_TABLE + slot * 4, child_block)
        self._stamp(parent, _tail(self.block_size, 92))
        self.reader.write_block(parent_block, bytes(apply_checksum(parent)))

    def _unlink(self, parent_block: int, child_block: int, name: str) -> None:
        slot = hash_name(name, self.international, self.hash_table_size)
        parent = bytearray(self._read_header(parent_block))
        head = long_at(parent, OFF_HASH_TABLE + slot * 4)
        successor = long_at(self._read_header(child_block), _tail(self.block_size, 16))
        if head == child_block:
            put_long(parent, OFF_HASH_TABLE + slot * 4, successor)
            self._stamp(parent, _tail(self.block_size, 92))
            self.reader.write_block(parent_block, bytes(apply_checksum(parent)))
            return
        previous = head
        seen = set()
        while previous:
            if previous in seen:
                raise DataError("A directory hash chain is damaged.")
            seen.add(previous)
            block = bytearray(self._read_header(previous))
            following = long_at(block, _tail(self.block_size, 16))
            if following == child_block:
                put_long(block, _tail(self.block_size, 16), successor)
                self.reader.write_block(previous, bytes(apply_checksum(block)))
                return
            previous = following
        raise DataError(f"{name} is not linked into its parent directory.")

    def remove(self, path: str, *, recursive: bool = False) -> None:
        self._require_writable()
        parts = split_path(path)
        if not parts:
            raise DataError("The volume root cannot be deleted.")
        block, _ = self._resolve(path)
        parent_block, _ = self._resolve(join_path(parts[:-1]))
        secondary = self._secondary_type(block)
        if secondary in (ST_USERDIR, ST_LINKDIR):
            children = list(self.iter_entries(path))
            if children and not recursive:
                raise DataError(f"{path} is not empty.")
            for child in children:
                self.remove(child.path, recursive=True)
        else:
            if self.access(path).locked:
                raise DataError(f"{path} is protected against deletion.")
            for data_block in self._data_blocks(block):
                self._release(data_block)
            current = long_at(self._read_header(block), _tail(self.block_size, 8))
            while current:
                following = long_at(self._read_header(current), _tail(self.block_size, 8))
                self._release(current)
                current = following
        self._unlink(parent_block, block, self._entry_name(block))
        self._release(block)
        self._store_bitmap()

    def rename(self, source: str, destination: str) -> None:
        self._require_writable()
        source_parts = split_path(source)
        destination_parts = split_path(destination)
        if not source_parts or not destination_parts:
            raise DataError("Both a source and a destination name are required.")
        name = validate_name(destination_parts[-1])
        block, _ = self._resolve(source)
        old_parent, _ = self._resolve(join_path(source_parts[:-1]))
        new_parent, _ = self._resolve(join_path(destination_parts[:-1]))
        if self._find_in_directory(new_parent, name) is not None:
            raise DataError(f"{destination} already exists.")
        self._unlink(old_parent, block, self._entry_name(block))
        header = bytearray(self._read_header(block))
        write_bstr(header, _tail(self.block_size, 80), name, MAX_NAME)
        put_long(header, _tail(self.block_size, 12), new_parent)
        put_long(header, _tail(self.block_size, 16), 0)
        self.reader.write_block(block, bytes(apply_checksum(header)))
        self._link_into(new_parent, block, name)

    # ---- boot block --------------------------------------------------
    def boot_option(self) -> int:
        """Return 1 when the volume carries executable boot code, else 0."""
        boot = self.reader.read_block(0) + self.reader.read_block(1)
        return 1 if any(boot[12:]) else 0

    def set_boot_option(self, option: int) -> None:
        """Write or clear a standard AmigaDOS boot block."""
        self._require_writable()
        option = int(option)
        if option not in (0, 1):
            raise ConfigurationError("A boot option is either 0 (off) or 1 (bootable).")
        first = bytearray(self.block_size)
        second = bytearray(self.block_size)
        first[0:4] = self.dos_type
        if option:
            first[12 : 12 + len(STANDARD_BOOT_CODE)] = STANDARD_BOOT_CODE
        checksum = _boot_checksum(bytes(first) + bytes(second))
        struct.pack_into(">I", first, 4, checksum)
        self.reader.write_block(0, bytes(first))
        self.reader.write_block(1, bytes(second))

    # ---- maintenance -------------------------------------------------
    def validate(self) -> list[str]:
        """Walk every structure and report what a real machine would refuse."""
        problems: list[str] = []
        root = self.reader.read_block(self.root_block)
        if not verify_checksum(root):
            problems.append("The root block checksum is wrong.")
        if long_at(root, _tail(self.block_size, 200)) == 0:
            problems.append("The block-allocation bitmap is marked invalid.")
        allocated: dict[int, str] = {}

        def claim(block: int, owner: str) -> None:
            if block in allocated:
                problems.append(
                    f"Block {block} is claimed by both {allocated[block]} and {owner}."
                )
            allocated[block] = owner

        def walk(directory: str) -> None:
            for entry in self.iter_entries(directory):
                header = self.reader.read_block(entry.block)
                if not verify_checksum(header):
                    problems.append(f"{entry.path} has a bad header checksum.")
                claim(entry.block, entry.path)
                if entry.is_dir:
                    walk(entry.path)
                    continue
                if entry.is_link:
                    continue
                try:
                    blocks = self._data_blocks(entry.block)
                except DataError as error:
                    problems.append(f"{entry.path}: {error}")
                    continue
                expected = (
                    entry.length + self.data_capacity - 1
                ) // self.data_capacity
                if len(blocks) < expected:
                    problems.append(
                        f"{entry.path} is missing {expected - len(blocks)} data block(s)."
                    )
                for block in blocks:
                    claim(block, entry.path)

        try:
            walk("")
        except DataError as error:
            problems.append(str(error))

        try:
            bits = self._load_bitmap()
        except DataError as error:
            problems.append(str(error))
            return problems
        for block, owner in allocated.items():
            index = block - self.reserved
            if 0 <= index < len(bits) and bits[index]:
                problems.append(
                    f"Block {block} is used by {owner} but the bitmap marks it free."
                )
        return problems

    def defragment(self) -> int:
        """Rewrite every file so its data blocks are contiguous again.

        Returns the number of files that moved. The catalogue is walked
        depth-first and each file is rewritten in place, which is safe because
        the block is released before the replacement is allocated.
        """
        self._require_writable()
        moved = 0
        paths: list[str] = []

        def collect(directory: str) -> None:
            for entry in self.iter_entries(directory):
                if entry.is_dir:
                    collect(entry.path)
                elif not entry.is_link:
                    paths.append(entry.path)

        collect("")
        for path in paths:
            meta = self.amiga_meta(path)
            data = self.read_bytes(path)
            blocks_before = self._data_blocks(self._resolve(path)[0])
            contiguous = all(
                blocks_before[index + 1] == blocks_before[index] + 1
                for index in range(len(blocks_before) - 1)
            )
            if contiguous:
                continue
            self.write_bytes(path, data, meta)
            moved += 1
        self._store_bitmap()
        return moved

    def free_map(self) -> list[bool]:
        """Return one flag per addressable block: True when it is free."""
        bits = self._load_bitmap()
        return [False] * self.reserved + [bool(value) for value in bits]

    def flush(self) -> None:
        self._store_bitmap()
        self.reader.flush()

    def close(self) -> None:
        """Flush pending bitmap changes, then release the file handle."""
        try:
            self.flush()
        finally:
            self.reader.close()


def _boot_checksum(boot: bytes) -> int:
    total = 0
    for index in range(0, len(boot), 4):
        if index == 4:
            continue
        (value,) = struct.unpack_from(">I", boot, index)
        total += value
        if total > 0xFFFFFFFF:
            total = (total + 1) & 0xFFFFFFFF
    return (~total) & 0xFFFFFFFF


# The 68000 boot code Commodore shipped: open dos.library and return its base
# so the ROM continues the boot. Anything shorter is not accepted by 1.3.
STANDARD_BOOT_CODE = bytes.fromhex(
    "43fa003e 4eaeffa0 4a80670a 2040207a 00204e75"
    "70004e75 646f732e 6c696272 61727900".replace(" ", "")
)


def format_volume(
    reader: BlockReader,
    *,
    label: str = "Empty",
    dos_type: bytes = b"DOS\x00",
    bootable: bool = False,
    geometry: Geometry | None = None,
) -> AmigaDOSVolume:
    """Write a brand-new empty volume across the whole of ``reader``."""
    if dos_type not in DOS_TYPES:
        raise ConfigurationError(f"DOS type {dos_type!r} is not supported.")
    if DOS_TYPES[dos_type] not in WRITABLE_FORMATS:
        raise ConfigurationError(
            f"{DOS_TYPES[dos_type]} volumes can be read but not created by this build."
        )
    block_size = reader.block_size
    total = reader.total_blocks
    reserved = geometry.reserved if geometry else RESERVED_BLOCKS
    if total <= reserved + 4:
        raise ConfigurationError("The requested volume is too small to format.")
    blank = b"\0" * block_size
    for block in range(total):
        reader.write_block(block, blank)

    hash_table_size = block_size // 4 - 56
    # Half way through the whole volume: 880 on a double-density floppy, 1760
    # on a high-density one, which is where AmigaDOS and every tool that reads
    # an ADF expect to find it.
    root_block = total // 2
    covered = total - reserved
    longs_per_page = block_size // 4 - 1
    bits_per_page = longs_per_page * 32
    page_count = (covered + bits_per_page - 1) // bits_per_page
    if page_count > 25:
        raise ConfigurationError(
            "This build creates volumes with up to 25 bitmap blocks; "
            "choose a smaller partition."
        )
    bitmap_blocks = [root_block + 1 + index for index in range(page_count)]

    root = bytearray(block_size)
    put_long(root, OFF_TYPE, T_HEADER)
    put_long(root, OFF_HT_SIZE, hash_table_size)
    put_long(root, _tail(block_size, 200), 0xFFFFFFFF)
    for index, block in enumerate(bitmap_blocks):
        put_long(root, _tail(block_size, 196) + index * 4, block)
    now = datetime.now(timezone.utc)
    days, mins, ticks = datetime_to_datestamp(now)
    for offset in (_tail(block_size, 92), _tail(block_size, 40), _tail(block_size, 28)):
        put_long(root, offset, days)
        put_long(root, offset + 4, mins)
        put_long(root, offset + 8, ticks)
    write_bstr(root, _tail(block_size, 80), validate_name(label), MAX_NAME)
    put_signed_long(root, _tail(block_size, 4), ST_ROOT)
    reader.write_block(root_block, bytes(apply_checksum(root)))

    used = {root_block, *bitmap_blocks}
    for page_index, page_block in enumerate(bitmap_blocks):
        page = bytearray(block_size)
        for long_index in range(longs_per_page):
            value = 0
            for bit in range(32):
                position = page_index * bits_per_page + long_index * 32 + bit
                if position >= covered:
                    break
                if (position + reserved) not in used:
                    value |= 1 << bit
            put_long(page, 4 + long_index * 4, value)
        apply_checksum(page, 0)
        reader.write_block(page_block, bytes(page))

    boot_first = bytearray(block_size)
    boot_first[0:4] = dos_type
    if bootable:
        boot_first[12 : 12 + len(STANDARD_BOOT_CODE)] = STANDARD_BOOT_CODE
    checksum = _boot_checksum(bytes(boot_first) + blank)
    struct.pack_into(">I", boot_first, 4, checksum)
    reader.write_block(0, bytes(boot_first))
    reader.write_block(1, blank)
    reader.flush()
    return AmigaDOSVolume(reader, geometry)


__all__ = [
    "AmigaDOSVolume",
    "Entry",
    "OFS_DATA_HEADER",
    "STANDARD_BOOT_CODE",
    "Stat",
    "format_volume",
    "join_path",
    "split_path",
    "validate_name",
]
