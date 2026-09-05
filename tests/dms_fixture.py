from __future__ import annotations

import struct

from app.dms import HEADER_SIZE, MAGIC, TRACK_SIZE, crc16


def dms_track(number: int, data: bytes, *, mode: int = 0, flags: int = 0) -> bytes:
    """Build one valid, complete DMS track header and its payload."""
    header = bytearray(20)
    header[0:2] = b"TR"
    struct.pack_into(
        ">HHHHHBBHH",
        header,
        2,
        number,
        0,
        len(data),
        0,
        len(data),
        flags,
        mode,
        crc16(data),
        crc16(data),
    )
    struct.pack_into(">H", header, 18, crc16(bytes(header[:18])))
    return bytes(header) + data


def minimal_dms(
    tracks: int = 2, *, disk_type: int = 2, compression: int = 0
) -> bytes:
    """Build a complete, checksum-valid DiskMasher archive for parser tests.

    Every field a real archive carries is present and correct, so a test that
    passes here is exercising the same path a downloaded ``.dms`` takes.
    """
    payloads = [
        bytes((0x41 + number,)) * TRACK_SIZE for number in range(max(1, tracks))
    ]
    body = b"".join(
        dms_track(number, payload) for number, payload in enumerate(payloads)
    )
    header = bytearray(HEADER_SIZE)
    header[0:4] = MAGIC
    struct.pack_into(
        ">IIIHHIIHHHHHHHIHHHHH",
        header,
        4,
        0,                      # header checksum, unused by this build
        0,                      # info bits
        0,                      # creation date
        0,                      # low track
        len(payloads) - 1,      # high track
        len(body),              # packed size
        TRACK_SIZE * len(payloads),  # unpacked size
        39, 106,                # OS version and revision
        0, 0, 500, 0, 0, 0,     # cpu, coprocessor, speed, extra, time fields
        0x0207,                 # created with DMS 2.07
        0x0100,                 # needs DMS 1.00
        disk_type,
        compression,
        0,                      # info header
    )
    return bytes(header) + body
