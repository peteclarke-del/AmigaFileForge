"""Reading SPS IPF captures, when the decoder library is installed.

IPF is the Software Preservation Society's preservation format. Unlike an ADF
it records what was actually on the disk, including the timing and the
deliberate irregularities copy protection relies on, which is why a protected
disk survives as an IPF and not as a sector image.

Decoding one needs the SPS decoder library (``libcapsimage``). That library is
source-available under a non-commercial licence and is not ours to ship, so
nothing here bundles it: the workbench looks for it at run time and, when it is
absent, says so plainly instead of failing in an obscure way. Installing it is
documented in ``docs/IPF-GUIDE.md``.

What the library returns is a track's MFM bit cells, not its files. Turning
those into an ADF is done here, in the ordinary Amiga way: find each sector's
sync mark, split the odd and even bit planes the format interleaves, and check
the header and data checksums before accepting a sector. A sector that does not
check out is reported rather than written, because a preservation image is
exactly where a silently wrong byte does the most damage.
"""

from __future__ import annotations

import ctypes
import os
from dataclasses import dataclass, field
from pathlib import Path

from .errors import DiskError

#: Names the decoder library is installed under, newest first.
LIBRARY_NAMES = (
    "libcapsimage.so.5.1",
    "libcapsimage.so.5",
    "libcapsimage.so.4.2",
    "libcapsimage.so.4",
    "libcapsimage.so",
    "capsimg.so",
    "CAPSImg.dll",
    "libcapsimage.dylib",
)

#: Where the workbench looks, in order. The environment variable wins so a
#: build or a test can point at one exact file.
ENVIRONMENT_VARIABLE = "AMIGA_FILE_FORGE_CAPSIMAGE"


def _search_directories() -> list[Path]:
    return [
        Path.home() / ".config" / "amiga-file-forge" / "lib",
        Path("/opt/amiga-file-forge/native/lib"),
        Path("/usr/local/lib"),
        Path("/usr/lib"),
    ]


class IPFError(DiskError):
    """An IPF capture could not be read."""


# Lock flags, from the library's own header. Together they ask the decoder for
# a fully decoded, index-aligned track with variable density applied, which is
# what an emulator asks for and what a sector decoder needs.
DI_LOCK_INDEX = 1 << 0
DI_LOCK_DENVAR = 1 << 2
DI_LOCK_DENAUTO = 1 << 3
DI_LOCK_DENNOISE = 1 << 4
DI_LOCK_UPDATEFD = 1 << 8
DI_LOCK_TYPE = 1 << 9

_LOCK_FLAGS = (
    DI_LOCK_INDEX
    | DI_LOCK_DENVAR
    | DI_LOCK_DENAUTO
    | DI_LOCK_DENNOISE
    | DI_LOCK_UPDATEFD
    | DI_LOCK_TYPE
)

_UDWORD = ctypes.c_uint32
_SDWORD = ctypes.c_int32


class _CapsDateTimeExt(ctypes.Structure):
    _fields_ = [
        ("year", _UDWORD), ("month", _UDWORD), ("day", _UDWORD),
        ("hour", _UDWORD), ("min", _UDWORD), ("sec", _UDWORD), ("tick", _UDWORD),
    ]


class _CapsImageInfo(ctypes.Structure):
    _fields_ = [
        ("type", _UDWORD),
        ("release", _UDWORD),
        ("revision", _UDWORD),
        ("mincylinder", _UDWORD),
        ("maxcylinder", _UDWORD),
        ("minhead", _UDWORD),
        ("maxhead", _UDWORD),
        ("crdt", _CapsDateTimeExt),
        ("platform", _UDWORD * 4),
    ]


class _CapsTrackInfoT2(ctypes.Structure):
    """The version 2 track block, which returns one decoded buffer."""

    _fields_ = [
        ("type", _UDWORD),
        ("cylinder", _UDWORD),
        ("head", _UDWORD),
        ("sectorcnt", _UDWORD),
        ("sectorsize", _UDWORD),
        ("trackbuf", ctypes.POINTER(ctypes.c_ubyte)),
        ("tracklen", _UDWORD),
        ("timelen", _UDWORD),
        ("timebuf", ctypes.POINTER(_UDWORD)),
        ("overlap", _SDWORD),
        ("startbit", _UDWORD),
        ("wseed", _UDWORD),
        ("weakcnt", _UDWORD),
    ]


@dataclass
class IPFReport:
    """What an IPF conversion produced, and what it could not."""

    sectors: bytes = b""
    cylinders: int = 0
    heads: int = 0
    sectors_per_track: int = 0
    recovered: int = 0
    expected: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return self.expected > 0 and self.recovered == self.expected


def library_path() -> Path | None:
    """Return the decoder library this machine has, or None."""
    override = os.environ.get(ENVIRONMENT_VARIABLE, "").strip()
    if override:
        candidate = Path(override)
        return candidate if candidate.is_file() else None
    for directory in _search_directories():
        for name in LIBRARY_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def available() -> bool:
    """Whether IPF captures can be read on this machine."""
    return library_path() is not None


def unavailable_message() -> str:
    """One sentence explaining what is missing and what to do about it."""
    return (
        "Reading an IPF capture needs the SPS decoder library, which is not "
        "ours to distribute and is not installed here. Build libcapsimage and "
        f"put it in {Path.home() / '.config' / 'amiga-file-forge' / 'lib'}, or "
        f"set {ENVIRONMENT_VARIABLE} to its path. docs/IPF-GUIDE.md has the steps."
    )


def _load() -> ctypes.CDLL:
    path = library_path()
    if path is None:
        raise IPFError(unavailable_message())
    try:
        library = ctypes.CDLL(str(path))
    except OSError as exc:
        raise IPFError(f"The SPS decoder library at {path} could not be loaded: {exc}") from exc
    if library.CAPSInit() != 0:
        raise IPFError("The SPS decoder library refused to initialise.")
    return library


# ---------------------------------------------------------------------------
# Amiga MFM
# ---------------------------------------------------------------------------
#: Every Amiga sector begins with two of these sync words.
SYNC_WORD = 0x4489


def _mfm_decode(odd: bytes, even: bytes) -> bytes:
    """Recombine the two bit planes Amiga MFM splits a value into.

    An Amiga sector stores every long twice: once holding the odd-numbered
    bits and once the even-numbered ones, each masked to remove the clock
    bits. Recombining them is a mask and a shift, and is the whole of the
    format's data encoding.
    """
    return bytes(
        ((left & 0x55) << 1) | (right & 0x55) for left, right in zip(odd, even)
    )


def _checksum(data: bytes) -> int:
    total = 0
    for offset in range(0, len(data), 4):
        total ^= int.from_bytes(data[offset : offset + 4], "big")
    return total & 0x55555555


def _decode_track(raw: bytes, sectors_per_track: int) -> tuple[dict[int, bytes], list[str]]:
    """Pull every checksummed sector out of one track's MFM bit cells."""
    found: dict[int, bytes] = {}
    warnings: list[str] = []
    # A track is read as a ring: a sector may straddle the index.
    stream = raw + raw[: 4 * 1088]
    position = 0
    limit = len(raw)
    while position < limit:
        marker = stream.find(b"\x44\x89\x44\x89", position)
        if marker < 0:
            break
        start = marker + 4
        block = stream[start : start + 1080]
        if len(block) < 1080:
            break
        position = marker + 4
        header = _mfm_decode(block[0:4], block[4:8])
        if len(header) != 4:
            continue
        _format, track, sector, _remaining = header
        header_checksum = int.from_bytes(_mfm_decode(block[40:44], block[44:48]), "big")
        data_checksum = int.from_bytes(_mfm_decode(block[48:52], block[52:56]), "big")
        if _checksum(block[0:40]) != header_checksum:
            warnings.append(f"Track {track}: a sector header failed its checksum.")
            continue
        payload = _mfm_decode(block[56:568], block[568:1080])
        if _checksum(block[56:1080]) != data_checksum:
            warnings.append(f"Track {track} sector {sector}: the data failed its checksum.")
            continue
        if sector < sectors_per_track:
            found.setdefault(sector, payload)
    return found, warnings


def read_ipf(path: Path, *, sectors_per_track: int = 11) -> IPFReport:
    """Decode an IPF capture into the sectors an ADF holds.

    Only the ordinary AmigaDOS track layout is decoded. A protected track that
    does not carry standard sectors is reported and left as zeroes, because
    what makes it worth preserving is exactly what an ADF cannot hold.
    """
    library = _load()
    report = IPFReport(sectors_per_track=sectors_per_track)
    image_id = library.CAPSAddImage()
    if image_id < 0:
        raise IPFError("The SPS decoder library would not allocate an image.")
    try:
        if library.CAPSLockImage(image_id, str(path).encode()) != 0:
            raise IPFError(f"{path.name} is not a capture the SPS decoder library accepts.")
        try:
            if library.CAPSLoadImage(image_id, _LOCK_FLAGS) != 0:
                raise IPFError(f"{path.name} could not be decoded by the SPS decoder library.")
            info = _CapsImageInfo()
            if library.CAPSGetImageInfo(ctypes.byref(info), image_id) != 0:
                raise IPFError("The capture's own description could not be read.")
            report.cylinders = int(info.maxcylinder) + 1
            report.heads = int(info.maxhead) + 1
            report.expected = report.cylinders * report.heads * sectors_per_track
            image = bytearray(report.expected * 512)
            for cylinder in range(int(info.mincylinder), int(info.maxcylinder) + 1):
                for head in range(int(info.minhead), int(info.maxhead) + 1):
                    track = _CapsTrackInfoT2()
                    track.type = 2
                    if library.CAPSLockTrack(
                        ctypes.byref(track), image_id, cylinder, head, _LOCK_FLAGS
                    ) != 0:
                        report.warnings.append(
                            f"Cylinder {cylinder} head {head} could not be locked."
                        )
                        continue
                    try:
                        length = int(track.tracklen)
                        if not track.trackbuf or length <= 0:
                            report.warnings.append(
                                f"Cylinder {cylinder} head {head} decoded to nothing."
                            )
                            continue
                        raw = bytes(bytearray(track.trackbuf[:length]))
                        sectors, warnings = _decode_track(raw, sectors_per_track)
                        report.warnings.extend(warnings)
                        base = (cylinder * report.heads + head) * sectors_per_track
                        for number, payload in sectors.items():
                            offset = (base + number) * 512
                            image[offset : offset + 512] = payload
                            report.recovered += 1
                    finally:
                        library.CAPSUnlockTrack(image_id, cylinder, head)
            report.sectors = bytes(image)
        finally:
            library.CAPSUnlockImage(image_id)
    finally:
        library.CAPSRemImage(image_id)
        library.CAPSExit()
    if not report.recovered:
        raise IPFError(
            f"No standard AmigaDOS sector was recovered from {path.name}. "
            "The capture may hold a protected format that an ADF cannot represent."
        )
    return report


__all__ = [
    "ENVIRONMENT_VARIABLE",
    "IPFError",
    "IPFReport",
    "available",
    "library_path",
    "read_ipf",
    "unavailable_message",
]
