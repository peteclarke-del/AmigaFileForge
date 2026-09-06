"""Read LHA and LZX-era ``.lha`` archives without an external decompressor.

Amiga software is distributed as LHA. WHDLoad itself ships as
``WHDLoad_usr.lha``, and the installer needs to open it to put ``C:WHDLoad``
and the ``S:`` prefs into a hard-disk image. Debian's ``lhasa`` would do the
job, but adding it would make the feature depend on a package that is present
in the container and absent from the Debian and Snap builds, which is exactly
the kind of split that shows up only in the field. DMS is already decoded in
this tree for the same reason, so LHA is decoded here too.

Three header levels and four methods cover what the Amiga world actually
produced: ``-lh0-`` stored, ``-lh5-``, ``-lh6-`` and ``-lh7-`` sliding-window
LZH, and ``-lhd-`` directory markers. Anything else is reported by name rather
than guessed at, because a wrong guess would write corrupt files into a disk
image and the corruption would not surface until the Amiga tried to run them.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass


class LHAError(Exception):
    """An archive that cannot be read, with the reason a user can act on."""


#: Methods that carry no data at all: a directory entry, and an empty file.
EMPTY_METHODS = frozenset({"-lhd-"})

#: Stored, with the bytes following the header unchanged.
STORED_METHODS = frozenset({"-lh0-", "-lz4-"})

#: Sliding-window LZH, keyed by the dictionary width each method uses.
LZH_DICTIONARY_BITS = {"-lh4-": 12, "-lh5-": 13, "-lh6-": 15, "-lh7-": 16}

SUPPORTED_METHODS = frozenset(EMPTY_METHODS | STORED_METHODS | set(LZH_DICTIONARY_BITS))

#: Every method identifier LHA ever defined. Recognising one this build cannot
#: expand is still useful: the listing works and the message can name it.
KNOWN_METHODS = frozenset(
    SUPPORTED_METHODS | {"-lh1-", "-lh2-", "-lh3-", "-lzs-", "-lz5-", "-pm0-", "-pm2-"}
)

#: The Amiga path separator inside an LHA directory extended header.
_EXTENDED_SEPARATOR = 0xFF

_MAX_MATCH = 256
_THRESHOLD = 3
_NC = 255 + _MAX_MATCH + 2 - _THRESHOLD
_CBIT = 9
_NT = 19
_TBIT = 5


@dataclass(frozen=True)
class LHAMember:
    """One entry in an archive, located but not yet decompressed."""

    path: str
    method: str
    packed_size: int
    original_size: int
    crc: int | None
    offset: int
    is_directory: bool

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1]


def _crc16(data: bytes) -> int:
    """CRC-16/ARC, the check LHA stores for every member."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _decode_text(raw: bytes) -> str:
    """Amiga archives are Latin-1; never fail a listing over one odd byte."""
    return raw.decode("latin-1").rstrip("\x00")


def _normalise(path: str) -> str:
    """Present one separator, and refuse anything that escapes the target."""
    unified = path.replace("\\", "/").strip("/")
    parts = [part for part in unified.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise LHAError(f"The archive contains a path that leaves its own directory: {path}")
    if any(part.startswith("/") or ":" in part for part in parts):
        raise LHAError(f"The archive contains an absolute path: {path}")
    return "/".join(parts)


def _extended_headers(data: bytes, start: int, size_width: int) -> tuple[dict[int, bytes], int, int]:
    """Walk the chain of extended headers, returning their contents and extent.

    Returns the collected headers keyed by type, the offset just past the
    chain, and the total number of bytes the chain occupied. Level 1 needs
    that total because its size field covers the chain and the payload
    together, so the payload length is only known once the chain is measured.
    """
    collected: dict[int, bytes] = {}
    offset = start
    consumed = 0
    reader = "<H" if size_width == 2 else "<I"
    while offset + size_width <= len(data):
        (size,) = struct.unpack_from(reader, data, offset)
        if size == 0:
            consumed += size_width
            offset += size_width
            break
        if size < size_width + 1 or offset + size > len(data):
            raise LHAError("An extended header runs past the end of the archive.")
        collected.setdefault(data[offset + size_width], data[offset + size_width + 1 : offset + size])
        consumed += size
        offset += size
    return collected, offset, consumed


def _extended_path(headers: dict[int, bytes], fallback: str) -> str:
    """Prefer the extended filename and directory over the base header name."""
    name = _decode_text(headers[0x01]) if 0x01 in headers else fallback
    directory = headers.get(0x02)
    if not directory:
        return name
    parts = [
        _decode_text(part)
        for part in bytes(directory).split(bytes([_EXTENDED_SEPARATOR]))
        if part
    ]
    return "/".join([*parts, name]) if parts else name


def _read_header(data: bytes, offset: int) -> tuple[LHAMember | None, int]:
    """Parse one member header, returning it and the offset of the next one."""
    if offset + 21 > len(data):
        return None, len(data)
    level = data[offset + 20]
    if level == 2:
        (header_size,) = struct.unpack_from("<H", data, offset)
        if header_size == 0:
            return None, len(data)
        method = _decode_text(data[offset + 2 : offset + 7])
        packed, original = struct.unpack_from("<II", data, offset + 7)
        (crc,) = struct.unpack_from("<H", data, offset + 21)
        headers, _, _ = _extended_headers(data, offset + 24, 2)
        path = _extended_path(headers, "")
        payload = offset + header_size
    elif level in {0, 1}:
        base_size = data[offset]
        if base_size == 0:
            return None, len(data)
        method = _decode_text(data[offset + 2 : offset + 7])
        size_field, original = struct.unpack_from("<II", data, offset + 7)
        name_length = data[offset + 21]
        name = _decode_text(data[offset + 22 : offset + 22 + name_length])
        after_name = offset + 22 + name_length
        crc = None
        if after_name + 2 <= len(data) and after_name + 2 <= offset + 2 + base_size:
            (crc,) = struct.unpack_from("<H", data, after_name)
        if level == 0:
            # Level 0 has no extended chain: the whole header is the base
            # header, and the payload follows it directly.
            packed = size_field
            path = name
            payload = offset + 2 + base_size
        else:
            # The last two bytes of a level 1 base header are the size of the
            # first extended header, so the chain starts two bytes before the
            # base header ends rather than after it.
            chain = offset + base_size
            headers, _, consumed = _extended_headers(data, chain, 2)
            # That first size field is counted as part of the base header, so
            # only the rest of the chain comes out of the declared skip size.
            packed = size_field - (consumed - 2)
            if packed < 0:
                raise LHAError("A level 1 header declares a smaller payload than its own extensions.")
            path = _extended_path(headers, name)
            payload = chain + consumed
    else:
        raise LHAError(f"Unsupported LHA header level {level}.")

    if payload + packed > len(data):
        raise LHAError(f"{path or 'An entry'} is truncated: the archive ends before its data does.")
    directory_entry = method in EMPTY_METHODS or path.endswith("/")
    member = LHAMember(
        path=_normalise(path),
        method=method,
        packed_size=packed,
        original_size=original,
        crc=crc,
        offset=payload,
        is_directory=directory_entry,
    )
    return member, payload + packed


class _BitReader:
    """LHA's 16-bit bit window, fed from a wider accumulator.

    The format is defined in terms of a 16-bit buffer topped up one byte at a
    time, and the Huffman tables are indexed by that window, so ``bitbuf`` has
    to mean exactly what it means in the format. What is free to change is how
    often the source is touched: bytes are drawn in runs into a wide integer,
    so a refill happens roughly once every six symbols rather than once per
    call. The decoder is pure Python and is opening megabyte archives while
    somebody waits, which makes that difference worth the indirection.
    """

    __slots__ = ("bitbuf", "_data", "_position", "_accumulator", "_available")

    _HIGH_WATER = 48

    def __init__(self, data: bytes) -> None:
        # The pad lets the accumulator read past the end without a bounds test
        # on every byte. LHA streams end mid-symbol, so a decoder always reads
        # a little further than the encoder wrote.
        self._data = bytes(data) + bytes(8)
        self._position = 0
        self._accumulator = 0
        self._available = 0
        self.bitbuf = 0
        self.fill(0)

    def fill(self, bits: int) -> None:
        available = self._available - bits
        if available < 16:
            accumulator = self._accumulator
            position = self._position
            data = self._data
            while available < self._HIGH_WATER:
                accumulator = (accumulator << 8) | data[position]
                position += 1
                available += 8
            self._accumulator = accumulator & ((1 << available) - 1)
            self._position = position
        self._available = available
        self.bitbuf = (self._accumulator >> (available - 16)) & 0xFFFF

    def peek(self, bits: int) -> int:
        return self.bitbuf >> (16 - bits)

    def get(self, bits: int) -> int:
        value = self.bitbuf >> (16 - bits) if bits else 0
        self.fill(bits)
        return value


class _Huffman:
    """The decode side of LHA's three nested Huffman tables."""

    def __init__(self, bits: _BitReader, dictionary_bits: int) -> None:
        self._bits = bits
        self._np = dictionary_bits + 1
        self._pbit = 5 if dictionary_bits > 13 else 4
        self._c_len = bytearray(_NC)
        self._pt_len = bytearray(max(_NT, self._np))
        self._c_table = [0] * 4096
        self._pt_table = [0] * 256
        self._left = [0] * (2 * _NC)
        self._right = [0] * (2 * _NC)
        self._blocksize = 0

    def _make_table(self, count: int, lengths: bytearray, table_bits: int, table: list[int]) -> None:
        """Build a lookup table, spilling long codes into a left/right tree.

        This is LHA's ``make_table``. Codes no longer than ``table_bits`` are
        answered by direct lookup; longer ones walk the tree, which is why the
        decoder tests every symbol against the alphabet size before using it.
        """
        occurrences = [0] * 17
        for index in range(count):
            occurrences[lengths[index]] += 1
        start = [0] * 18
        for length in range(1, 17):
            start[length + 1] = start[length] + (occurrences[length] << (16 - length))
        if start[17] != 0x10000:
            raise LHAError("The archive's Huffman table is inconsistent.")
        spare = 16 - table_bits
        weight = [0] * 17
        for length in range(1, table_bits + 1):
            start[length] >>= spare
            weight[length] = 1 << (table_bits - length)
        for length in range(table_bits + 1, 17):
            weight[length] = 1 << (16 - length)
        filled = start[table_bits + 1] >> spare
        if filled != 0:
            for index in range(filled, 1 << table_bits):
                table[index] = 0
        available = count
        mask = 1 << (15 - table_bits)
        for symbol in range(count):
            length = lengths[symbol]
            if length == 0:
                continue
            next_code = start[length] + weight[length]
            if length <= table_bits:
                for index in range(start[length], min(next_code, len(table))):
                    table[index] = symbol
            else:
                # Walk into the tree, creating nodes as the code demands them.
                position = start[length]
                container, slot = table, position >> spare
                remaining = length - table_bits
                while remaining != 0:
                    if container[slot] == 0:
                        self._left[available] = 0
                        self._right[available] = 0
                        container[slot] = available
                        available += 1
                    node = container[slot]
                    container = self._right if position & mask else self._left
                    slot = node
                    position = (position << 1) & 0xFFFF
                    remaining -= 1
                container[slot] = symbol
            start[length] = next_code

    def _read_pt_len(self, count: int, width: int, special: int) -> None:
        total = self._bits.get(width)
        if total == 0:
            fixed = self._bits.get(width)
            for index in range(count):
                self._pt_len[index] = 0
            for index in range(256):
                self._pt_table[index] = fixed
            return
        index = 0
        while index < total and index < count:
            length = self._bits.peek(3)
            if length != 7:
                self._bits.fill(3)
            else:
                mask = 1 << 12
                while mask & self._bits.bitbuf:
                    mask >>= 1
                    length += 1
                self._bits.fill(length - 3)
            self._pt_len[index] = length
            index += 1
            if index == special:
                skip = self._bits.get(2)
                while skip > 0 and index < count:
                    self._pt_len[index] = 0
                    index += 1
                    skip -= 1
        while index < count:
            self._pt_len[index] = 0
            index += 1
        self._make_table(count, self._pt_len, 8, self._pt_table)

    def _read_c_len(self) -> None:
        total = self._bits.get(_CBIT)
        if total == 0:
            fixed = self._bits.get(_CBIT)
            for index in range(_NC):
                self._c_len[index] = 0
            for index in range(4096):
                self._c_table[index] = fixed
            return
        index = 0
        while index < total and index < _NC:
            symbol = self._pt_table[self._bits.peek(8)]
            if symbol >= _NT:
                mask = 1 << 7
                while symbol >= _NT:
                    symbol = self._right[symbol] if self._bits.bitbuf & mask else self._left[symbol]
                    mask >>= 1
            self._bits.fill(self._pt_len[symbol])
            if symbol <= 2:
                if symbol == 0:
                    run = 1
                elif symbol == 1:
                    run = self._bits.get(4) + 3
                else:
                    run = self._bits.get(_CBIT) + 20
                while run > 0 and index < _NC:
                    self._c_len[index] = 0
                    index += 1
                    run -= 1
            else:
                self._c_len[index] = symbol - 2
                index += 1
        while index < _NC:
            self._c_len[index] = 0
            index += 1
        self._make_table(_NC, self._c_len, 12, self._c_table)

    def next_symbol(self) -> int:
        if self._blocksize == 0:
            self._blocksize = self._bits.get(16)
            self._read_pt_len(_NT, _TBIT, 3)
            self._read_c_len()
            self._read_pt_len(self._np, self._pbit, -1)
        self._blocksize -= 1
        symbol = self._c_table[self._bits.peek(12)]
        if symbol >= _NC:
            mask = 1 << 3
            while symbol >= _NC:
                symbol = self._right[symbol] if self._bits.bitbuf & mask else self._left[symbol]
                mask >>= 1
        self._bits.fill(self._c_len[symbol])
        return symbol

    def next_offset(self) -> int:
        symbol = self._pt_table[self._bits.peek(8)]
        if symbol >= self._np:
            mask = 1 << 7
            while symbol >= self._np:
                symbol = self._right[symbol] if self._bits.bitbuf & mask else self._left[symbol]
                mask >>= 1
        self._bits.fill(self._pt_len[symbol])
        if symbol == 0:
            return 0
        return (1 << (symbol - 1)) + self._bits.get(symbol - 1)


def _decode_lzh(packed: bytes, original_size: int, dictionary_bits: int) -> bytes:
    """Expand one LZH stream.

    The decoded bytes are built behind a zeroed window the width of the
    method's dictionary, so a back-reference is always an index into the same
    buffer and never has to be range-checked. Matches are copied by slice
    rather than a byte at a time, which is what makes this fast enough to open
    a multi-megabyte archive while the user waits: a run that overlaps its own
    source doubles on each pass instead of looping per byte.
    """
    if original_size == 0:
        return b""
    window = 1 << dictionary_bits
    buffer = bytearray(window)
    reader = _Huffman(_BitReader(packed), dictionary_bits)
    append = buffer.append
    produced = 0
    while produced < original_size:
        symbol = reader.next_symbol()
        if symbol <= 255:
            append(symbol)
            produced += 1
            continue
        length = symbol - 256 + _THRESHOLD
        start = len(buffer) - reader.next_offset() - 1
        produced += length
        while length > 0:
            take = min(length, len(buffer) - start)
            buffer += buffer[start : start + take]
            start += take
            length -= take
    return bytes(buffer[window : window + original_size])


class LHAArchive:
    """An archive held in memory, listed once and extracted on demand."""

    def __init__(self, data: bytes) -> None:
        self._data = bytes(data)
        if not is_lha_bytes(self._data):
            # A download that returned an error page is the common case here,
            # and it is worth naming: reporting a nonsense header level would
            # send someone looking for a corrupt archive instead of a bad URL.
            raise LHAError("This file is not an LHA archive.")
        self.members: list[LHAMember] = []
        offset = 0
        while offset < len(self._data):
            if self._data[offset] == 0 and self._data[offset : offset + 2] == b"\x00\x00":
                break
            member, offset = _read_header(self._data, offset)
            if member is None:
                break
            self.members.append(member)
        if not self.members:
            raise LHAError("This file does not contain any LHA entries.")

    @classmethod
    def from_path(cls, path) -> "LHAArchive":
        return cls(open(path, "rb").read())

    def read(self, member: LHAMember, verify: bool = True) -> bytes:
        """Decompress one member, checking it against its stored CRC."""
        if member.is_directory:
            return b""
        packed = self._data[member.offset : member.offset + member.packed_size]
        if member.method in STORED_METHODS:
            content = packed[: member.original_size]
        elif member.method in LZH_DICTIONARY_BITS:
            try:
                content = _decode_lzh(packed, member.original_size, LZH_DICTIONARY_BITS[member.method])
            except (LHAError, IndexError) as exc:
                # Damage inside a compressed stream surfaces as a broken
                # Huffman table rather than a bad checksum, so the member has
                # to be named here or the report says only that something,
                # somewhere, was inconsistent.
                raise LHAError(f"{member.path} could not be decompressed: {exc}") from exc
        else:
            raise LHAError(
                f"{member.path} uses {member.method}, which this build cannot decompress. "
                "Unpack the archive on another machine and add the files directly."
            )
        if len(content) != member.original_size:
            raise LHAError(f"{member.path} decompressed to the wrong length.")
        if verify and member.crc is not None and _crc16(content) != member.crc:
            raise LHAError(f"{member.path} failed its checksum; the archive is damaged.")
        return content

    def find(self, path: str) -> LHAMember | None:
        """Locate a member by path, case-insensitively as AmigaDOS would."""
        wanted = path.replace("\\", "/").strip("/").casefold()
        for member in self.members:
            if member.path.casefold() == wanted:
                return member
        return None


def is_lha_bytes(data: bytes) -> bool:
    """Whether a buffer opens with something shaped like an LHA member header.

    The method identifier sits at a fixed place in every header level, so a
    single look decides it. Methods this build cannot expand still count as
    LHA, because the useful answer for one of those is a listing and a clear
    message about the method, not "this is not an archive".
    """
    if len(data) < 22:
        return False
    signature = data[2:7]
    if not (signature[:1] == b"-" and signature[4:5] == b"-"):
        return False
    return data[20] in {0, 1, 2} and signature.decode("latin-1", "replace") in KNOWN_METHODS


def is_lha_name(filename: str) -> bool:
    return filename.lower().endswith((".lha", ".lzh", ".lzx"))


__all__ = [
    "LHAArchive",
    "LHAError",
    "LHAMember",
    "KNOWN_METHODS",
    "SUPPORTED_METHODS",
    "is_lha_bytes",
    "is_lha_name",
]
