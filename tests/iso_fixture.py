"""Build genuine ISO 9660 images so the reader is tested against real discs.

The discs this reader exists for are commercial CDs nobody can commit, and
`genisoimage` is not in this tree or in the container, so a test wanting a
Joliet tree or an Amiga `AS` entry would otherwise embed an opaque blob. This
writes the structures instead, which means the volume descriptors, the
directory records, the Rock Ridge system-use areas and the Amiga extension are
all exercised against bytes the test itself laid down, and a failure points at
a field rather than at a fixture.

The builder is deliberately literal about the format's awkward parts, because
those are what the reader has to survive: every numeric field is stored twice
in opposite byte orders, records may not straddle a sector, a name carries a
`;1` version suffix, and the first two records of a directory name themselves
and their parent with a single zero or one byte.
"""

from __future__ import annotations

import struct

SECTOR = 2048


def _both_endian_32(value: int) -> bytes:
    """A number as ISO stores it: little endian, then big endian."""
    return struct.pack("<I", value) + struct.pack(">I", value)


def _both_endian_16(value: int) -> bytes:
    return struct.pack("<H", value) + struct.pack(">H", value)


def _recording_date(year=2000, month=11, day=29, hour=13, minute=45, second=0) -> bytes:
    """The seven-byte date a directory record carries."""
    return bytes([year - 1900, month, day, hour, minute, second, 0])


def amiga_entry(protection: int | None = None, comment: str = "") -> bytes:
    """An Amiga ``AS`` system-use entry, as an Amiga CD records one."""
    flags = 0
    body = b""
    if protection is not None:
        flags |= 0x01
        body += struct.pack(">I", protection & 0xFFFF)
    if comment:
        flags |= 0x02
        encoded = comment.encode("latin-1", "replace")[:255]
        body += bytes([len(encoded)]) + encoded
    payload = bytes([flags]) + body
    return b"AS" + bytes([len(payload) + 4, 1]) + payload


def name_entry(name: str) -> bytes:
    """A Rock Ridge ``NM`` entry carrying the name a person should see."""
    encoded = name.encode("latin-1", "replace")
    return b"NM" + bytes([len(encoded) + 5, 1, 0]) + encoded


def _directory_record(
    identifier: bytes,
    extent: int,
    length: int,
    *,
    directory: bool,
    system_use: bytes = b"",
) -> bytes:
    """One directory record, padded so the next one starts on an even byte."""
    name_padding = b"" if len(identifier) % 2 else b"\x00"
    record = (
        _both_endian_32(extent)
        + _both_endian_32(length)
        + _recording_date()
        + bytes([0x02 if directory else 0x00])
        + b"\x00\x00"
        + _both_endian_16(1)
        + bytes([len(identifier)])
        + identifier
        + name_padding
        + system_use
    )
    # The length byte counts itself and the extended-attribute byte.
    total = len(record) + 2
    if total % 2:
        record += b"\x00"
        total += 1
    return bytes([total, 0]) + record


class _Node:
    def __init__(self, name: str, *, directory: bool, data: bytes = b"",
                 iso_name: str | None = None, system_use: bytes = b"") -> None:
        self.name = name
        self.directory = directory
        self.data = data
        self.iso_name = iso_name
        self.system_use = system_use
        self.children: list[_Node] = []
        self.extent = 0
        self.length = 0

    def add(self, node: _Node) -> _Node:
        self.children.append(node)
        return node


def directory(name: str, *, iso_name: str | None = None, system_use: bytes = b"") -> _Node:
    return _Node(name, directory=True, iso_name=iso_name, system_use=system_use)


def file(name: str, data: bytes, *, iso_name: str | None = None, system_use: bytes = b"") -> _Node:
    return _Node(name, directory=False, data=data, iso_name=iso_name, system_use=system_use)


def _identifier(node: _Node) -> bytes:
    """What the base tree calls this entry.

    A file carries the ``;1`` version suffix the format requires; a directory
    does not. ``iso_name`` lets a test give the base tree a deliberately ugly
    eight-and-three name while Rock Ridge or Joliet carries the real one.
    """
    name = node.iso_name if node.iso_name is not None else node.name
    if node.directory:
        return name.encode("latin-1", "replace")
    return f"{name};1".encode("latin-1", "replace")


def build_iso(
    root: _Node,
    *,
    volume: str = "TESTDISC",
    joliet: bool = False,
    duplicate_primary: bool = False,
) -> bytes:
    """Assemble a complete ISO 9660 image from a tree of nodes.

    ``joliet`` writes a second, independent directory tree in UCS-2 with a
    supplementary descriptor pointing at it, which is how a disc mastered for
    both worlds really carries one. The two trees describe the same files and
    share their data extents, but only the base tree carries system-use areas.
    That asymmetry is the whole point: reading such a disc through Joliet gives
    perfect names and silently drops every Amiga protection bit and comment.

    ``duplicate_primary`` writes the primary descriptor twice, which the
    AmigaOS 3.9 disc really does and the standard does not describe.
    """
    directories: list[_Node] = []

    def collect(node: _Node) -> None:
        directories.append(node)
        for child in node.children:
            if child.directory:
                collect(child)

    collect(root)
    parents = {id(root): root}
    for node in directories:
        for child in node.children:
            parents[id(child)] = node

    descriptor_count = 1 + (1 if duplicate_primary else 0) + (1 if joliet else 0) + 1
    cursor = 16 + descriptor_count

    # File data is shared between the trees, so it is placed once and both
    # trees point at it.
    files = [child for node in directories for child in node.children if not child.directory]
    data_extent: dict[int, int] = {}

    def records_for(node: _Node, layout: dict[int, tuple[int, int]], use_joliet: bool) -> bytes:
        parent = parents[id(node)]
        entries = [
            _directory_record(b"\x00", *layout[id(node)], directory=True),
            _directory_record(b"\x01", *layout[id(parent)], directory=True),
        ]
        for child in node.children:
            identifier = (
                child.name.encode("utf-16-be") if use_joliet else _identifier(child)
            )
            if child.directory:
                extent, length = layout[id(child)]
            else:
                extent, length = data_extent[id(child)], len(child.data)
            entries.append(_directory_record(
                identifier, extent, length,
                directory=child.directory,
                system_use=b"" if use_joliet else child.system_use,
            ))
        block = b""
        for record in entries:
            # A record may not straddle a sector boundary.
            if len(block) % SECTOR + len(record) > SECTOR:
                block += b"\x00" * (SECTOR - len(block) % SECTOR)
            block += record
        return block + b"\x00" * (-len(block) % SECTOR)

    def lay_out(start: int, use_joliet: bool) -> tuple[dict[int, tuple[int, int]], int]:
        """Place every directory of one tree, sizing it against its own names.

        A record's size does not depend on the extent values it carries, since
        every such field is fixed width, so sizing with placeholder extents and
        filling the real ones in later is exact rather than approximate.
        """
        layout = {id(node): (0, SECTOR) for node in directories}
        end = start
        for _ in range(2):
            end = start
            for node in directories:
                length = len(records_for(node, layout, use_joliet))
                layout[id(node)] = (end, length)
                end += max(1, length // SECTOR)
        return layout, end

    # Sizing runs before the file data is placed, so the extents it reads are
    # placeholders. Only the values inside the records depend on them, and the
    # records are generated again from the final numbers when they are written.
    data_extent.update({id(child): 0 for child in files})
    base_layout, after_base = lay_out(cursor, False)
    joliet_layout, cursor = (
        lay_out(after_base, True) if joliet else (None, after_base)
    )
    for child in files:
        data_extent[id(child)] = cursor
        cursor += max(1, -(-len(child.data) // SECTOR))
    end = cursor

    image = bytearray()

    def put(extent: int, payload: bytes) -> None:
        needed = extent * SECTOR + len(payload)
        if len(image) < needed:
            image.extend(b"\x00" * (needed - len(image)))
        image[extent * SECTOR:extent * SECTOR + len(payload)] = payload

    for node in directories:
        put(base_layout[id(node)][0], records_for(node, base_layout, False))
    if joliet_layout is not None:
        for node in directories:
            put(joliet_layout[id(node)][0], records_for(node, joliet_layout, True))
    for child in files:
        put(data_extent[id(child)], child.data + b"\x00" * (-len(child.data) % SECTOR))

    def descriptor(kind: int, name: str, root_extent: int, root_length: int,
                   escapes: bytes = b"") -> bytes:
        block = bytearray(b"\x00" * SECTOR)
        block[0] = kind
        block[1:6] = b"CD001"
        block[6] = 1
        encoded = (
            name.encode("utf-16-be").ljust(32, b"\x00")
            if escapes else name.encode("latin-1").ljust(32, b" ")
        )
        block[40:72] = encoded[:32]
        block[80:88] = _both_endian_32(end)
        if escapes:
            block[88:88 + len(escapes)] = escapes
        block[120:124] = _both_endian_16(1) + _both_endian_16(1)
        block[128:132] = _both_endian_16(SECTOR)
        block[156:190] = _directory_record(
            b"\x00", root_extent, root_length, directory=True
        ).ljust(34, b"\x00")[:34]
        return bytes(block)

    sector = 16
    put(sector, descriptor(1, volume, *base_layout[id(root)])); sector += 1
    if duplicate_primary:
        put(sector, descriptor(1, volume, *base_layout[id(root)])); sector += 1
    if joliet_layout is not None:
        put(sector, descriptor(2, volume, *joliet_layout[id(root)], escapes=b"%/E"))
        sector += 1
    terminator = bytearray(b"\x00" * SECTOR)
    terminator[0] = 255
    terminator[1:6] = b"CD001"
    terminator[6] = 1
    put(sector, bytes(terminator))

    if len(image) < end * SECTOR:
        image.extend(b"\x00" * (end * SECTOR - len(image)))
    return bytes(image)


__all__ = ["amiga_entry", "build_iso", "directory", "file", "name_entry"]
