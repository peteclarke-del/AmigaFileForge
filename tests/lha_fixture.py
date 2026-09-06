"""Build genuine LHA archives so the reader is tested against real streams.

There is no LHA compressor in this tree and none in the container, so a test
that wanted a ``-lh5-`` stream would otherwise have to embed a binary blob
whose provenance nobody could check. Encoding one here instead means the
Huffman table construction, the run-length escapes in the code-length table
and the sliding-window matches are all exercised by data the test itself
produced, and a failure points at a specific construct rather than at an
opaque fixture.

The encoder follows the same canonical assignment the decoder's table builder
assumes: codes are handed out in order of length and then symbol, aligned to
the top of a 16-bit field. That is the one place the two sides have to agree,
so writing the encoder against it is what gives the round trip its value.
"""

from __future__ import annotations

import heapq
import struct

from app.lha import _crc16


_MAX_MATCH = 256
_THRESHOLD = 3
_NC = 255 + _MAX_MATCH + 2 - _THRESHOLD
_CBIT = 9
_NT = 19
_TBIT = 5

#: How far back the fixture encoder looks for a match. Real archivers search
#: the whole dictionary; a test only needs matches to occur at all.
_SEARCH_LOOKBACK = 1024


class _BitWriter:
    """Most significant bit first, which is the order LHA reads."""

    def __init__(self) -> None:
        self._bytes = bytearray()
        self._pending = 0
        self._count = 0

    def write(self, value: int, bits: int) -> None:
        for shift in range(bits - 1, -1, -1):
            self._pending = (self._pending << 1) | ((value >> shift) & 1)
            self._count += 1
            if self._count == 8:
                self._bytes.append(self._pending)
                self._pending = 0
                self._count = 0

    def finish(self) -> bytes:
        if self._count:
            self._bytes.append((self._pending << (8 - self._count)) & 0xFF)
            self._pending = 0
            self._count = 0
        return bytes(self._bytes)


def _code_lengths(frequencies: dict[int, int], limit: int = 16) -> dict[int, int]:
    """Huffman lengths for the symbols that occur, complete by construction.

    A code over a single symbol is not a complete code, and the decoder
    rightly rejects one, so a second symbol is always present. Test payloads
    are small enough that the natural tree stays inside the format's 16-bit
    ceiling; the assertion says so rather than assuming it.
    """
    present = {symbol: count for symbol, count in frequencies.items() if count}
    while len(present) < 2:
        filler = next(value for value in range(_NC) if value not in present)
        present[filler] = 1
    heap = [(count, index, {symbol}) for index, (symbol, count) in enumerate(sorted(present.items()))]
    heapq.heapify(heap)
    lengths = dict.fromkeys(present, 0)
    counter = len(heap)
    while len(heap) > 1:
        left_count, _, left = heapq.heappop(heap)
        right_count, _, right = heapq.heappop(heap)
        for symbol in left | right:
            lengths[symbol] += 1
        heapq.heappush(heap, (left_count + right_count, counter, left | right))
        counter += 1
    longest = max(lengths.values())
    if longest > limit:
        raise ValueError(f"This fixture needs a code of {longest} bits, over the {limit}-bit limit.")
    return lengths


def _canonical(lengths: list[int]) -> dict[int, int]:
    """Assign codes exactly as the decoder's lookup table expects them."""
    start = [0] * 18
    occurrences = [0] * 17
    for length in lengths:
        occurrences[length] += 1
    for length in range(1, 17):
        start[length + 1] = start[length] + (occurrences[length] << (16 - length))
    codes: dict[int, int] = {}
    cursor = list(start)
    for symbol, length in enumerate(lengths):
        if length == 0:
            continue
        codes[symbol] = cursor[length] >> (16 - length)
        cursor[length] += 1 << (16 - length)
    return codes


def _write_pt_lengths(writer: _BitWriter, lengths: list[int], width: int, special: int) -> None:
    """Emit a code-length table in the 3-bit-plus-unary form LHA uses."""
    total = len(lengths)
    while total > 0 and lengths[total - 1] == 0:
        total -= 1
    writer.write(total, width)
    index = 0
    while index < total:
        length = lengths[index]
        if length < 7:
            writer.write(length, 3)
        else:
            writer.write(7, 3)
            writer.write((1 << (length - 7)) - 1, length - 7)
            writer.write(0, 1)
        index += 1
        if index == special:
            skipped = 0
            while skipped < 3 and index < total and lengths[index] == 0:
                skipped += 1
                index += 1
            writer.write(skipped, 2)


def _code_length_escapes(lengths: list[int]) -> tuple[list[tuple[int, int, int]], int]:
    """Turn the 510-entry code-length table into its run-length escapes."""
    escapes: list[tuple[int, int, int]] = []
    index = 0
    used = len(lengths)
    while used > 0 and lengths[used - 1] == 0:
        used -= 1
    while index < used:
        if lengths[index] != 0:
            escapes.append((lengths[index] + 2, 0, 0))
            index += 1
            continue
        run = 0
        while index + run < used and lengths[index + run] == 0:
            run += 1
        while run > 0:
            if run >= 20:
                taken = min(run, 531)
                escapes.append((2, _CBIT, taken - 20))
            elif run >= 3:
                taken = min(run, 18)
                escapes.append((1, 4, taken - 3))
            else:
                taken = 1
                escapes.append((0, 0, 0))
            run -= taken
            index += taken
    return escapes, used


def compress_lh5(payload: bytes, dictionary_bits: int = 13) -> bytes:
    """Encode one payload as a single LZH block.

    Matches are found with a plain longest-match scan over a short lookback.
    It is not trying to compress well, and the short lookback keeps the scan
    from turning quadratic on a payload of a few kilobytes; what matters is
    that the stream it produces is one a real decoder must accept, including
    back-references that overlap their own source.
    """
    window = min(1 << dictionary_bits, _SEARCH_LOOKBACK)
    offset_alphabet = dictionary_bits + 1
    offset_width = 5 if dictionary_bits > 13 else 4

    items: list[tuple[int, int]] = []
    position = 0
    while position < len(payload):
        best_length = 0
        best_distance = 0
        earliest = max(0, position - window)
        for candidate in range(earliest, position):
            length = 0
            while (
                length < _MAX_MATCH
                and position + length < len(payload)
                and payload[candidate + length] == payload[position + length]
            ):
                length += 1
            if length > best_length:
                best_length, best_distance = length, position - candidate
        if best_length >= _THRESHOLD:
            items.append((best_length - _THRESHOLD + 256, best_distance - 1))
            position += best_length
        else:
            items.append((payload[position], -1))
            position += 1

    code_frequencies: dict[int, int] = {}
    offset_frequencies: dict[int, int] = {}
    for symbol, offset in items:
        code_frequencies[symbol] = code_frequencies.get(symbol, 0) + 1
        if offset >= 0:
            key = offset.bit_length()
            offset_frequencies[key] = offset_frequencies.get(key, 0) + 1

    code_lengths = [0] * _NC
    for symbol, length in _code_lengths(code_frequencies).items():
        code_lengths[symbol] = length
    offset_lengths = [0] * offset_alphabet
    for symbol, length in _code_lengths(offset_frequencies).items():
        offset_lengths[symbol] = length

    escapes, _ = _code_length_escapes(code_lengths)
    table_frequencies: dict[int, int] = {}
    for symbol, _bits, _extra in escapes:
        table_frequencies[symbol] = table_frequencies.get(symbol, 0) + 1
    table_lengths = [0] * _NT
    for symbol, length in _code_lengths(table_frequencies).items():
        table_lengths[symbol] = length

    table_codes = _canonical(table_lengths)
    codes = _canonical(code_lengths)
    offset_codes = _canonical(offset_lengths)

    writer = _BitWriter()
    writer.write(len(items), 16)
    _write_pt_lengths(writer, table_lengths, _TBIT, 3)

    used = len(code_lengths)
    while used > 0 and code_lengths[used - 1] == 0:
        used -= 1
    writer.write(used, _CBIT)
    for symbol, extra_bits, extra in escapes:
        writer.write(table_codes[symbol], table_lengths[symbol])
        if extra_bits:
            writer.write(extra, extra_bits)

    _write_pt_lengths(writer, offset_lengths, offset_width, -1)

    for symbol, offset in items:
        writer.write(codes[symbol], code_lengths[symbol])
        if offset < 0:
            continue
        key = offset.bit_length()
        writer.write(offset_codes[key], offset_lengths[key])
        if key > 1:
            writer.write(offset - (1 << (key - 1)), key - 1)
    return writer.finish()


def _timestamp() -> int:
    """A fixed MS-DOS date, so fixtures are byte-for-byte reproducible."""
    return (20 << 25) | (1 << 21) | (1 << 16)


def level0_member(name: str, payload: bytes, method: str = "-lh0-") -> bytes:
    """A level 0 header, where the path lives in the name field."""
    packed = payload if method == "-lh0-" else compress_lh5(payload)
    encoded = name.encode("latin-1")
    body = bytearray()
    body += method.encode("ascii")
    body += struct.pack("<II", len(packed), len(payload))
    body += struct.pack("<I", _timestamp())
    body += bytes([0x20, 0x00, len(encoded)])
    body += encoded
    body += struct.pack("<H", _crc16(payload))
    header = bytes([len(body), 0]) + bytes(body)
    return header + packed


def level1_member(path: str, payload: bytes, method: str = "-lh0-") -> bytes:
    """A level 1 header, with the directory in an extended header.

    The size field covers the payload and the extended chain together, and the
    first two bytes of that chain are counted as part of the base header. That
    accounting is the part a reader most often gets wrong, so the fixture
    builds it the way real archivers do rather than the way it reads.
    """
    packed = payload if method == "-lh0-" else compress_lh5(payload)
    parts = path.split("/")
    name = parts[-1].encode("latin-1")
    directories = parts[:-1]

    extensions: list[bytes] = []
    if directories:
        encoded = b"".join(part.encode("latin-1") + b"\xff" for part in directories)
        extensions.append(b"\x02" + encoded)
    extensions.append(b"\x54" + struct.pack("<I", 0x5C7BAE63))

    chain = bytearray()
    for extension in extensions:
        chain += struct.pack("<H", len(extension) + 2)
        chain += extension
    chain += struct.pack("<H", 0)
    # The leading size field belongs to the base header, so the skip size
    # covers everything after it.
    chain_cost = len(chain) - 2

    body = bytearray()
    body += method.encode("ascii")
    body += struct.pack("<II", len(packed) + chain_cost, len(payload))
    body += struct.pack("<I", _timestamp())
    body += bytes([0x20, 0x01, len(name)])
    body += name
    body += struct.pack("<H", _crc16(payload))
    body += b"U"
    header = bytes([len(body) + 2, 0]) + bytes(body)
    return header + bytes(chain) + packed


def level2_member(path: str, payload: bytes, method: str = "-lh0-") -> bytes:
    """A level 2 header, which states its own total size up front."""
    packed = payload if method == "-lh0-" else compress_lh5(payload)
    parts = path.split("/")
    name = parts[-1].encode("latin-1")
    directories = parts[:-1]

    extensions = [b"\x01" + name]
    if directories:
        extensions.append(
            b"\x02" + b"".join(part.encode("latin-1") + b"\xff" for part in directories)
        )
    chain = bytearray()
    for extension in extensions:
        chain += struct.pack("<H", len(extension) + 2)
        chain += extension
    chain += struct.pack("<H", 0)

    header = bytearray(24)
    header[2:7] = method.encode("ascii")
    struct.pack_into("<II", header, 7, len(packed), len(payload))
    struct.pack_into("<I", header, 15, 0x5C7BAE63)
    header[19] = 0x00
    header[20] = 0x02
    struct.pack_into("<H", header, 21, _crc16(payload))
    header[23] = ord("U")
    struct.pack_into("<H", header, 0, len(header) + len(chain))
    return bytes(header) + bytes(chain) + packed


def archive(*members: bytes) -> bytes:
    """Join members and close the archive the way LHA closes one."""
    return b"".join(members) + b"\x00\x00"
