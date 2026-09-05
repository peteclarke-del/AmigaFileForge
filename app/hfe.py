"""Small, dependency-free helpers for validating HxC HFE containers."""

from __future__ import annotations

from dataclasses import dataclass


class HFEError(ValueError):
    pass


@dataclass(frozen=True)
class HFEHeader:
    version: str
    tracks: int
    sides: int
    encoding: int
    bitrate: int

    @property
    def advanced(self) -> bool:
        return self.version != "v1"


def parse_hfe_header(data: bytes) -> HFEHeader:
    if len(data) < 512:
        raise HFEError("The HFE header is incomplete.")
    signature = data[:8]
    revision = data[8]
    if signature == b"HXCPICFE":
        if revision not in (0, 1):
            raise HFEError(f"Unsupported HFE revision {revision}.")
        version = "v1" if revision == 0 else "v2"
    elif signature == b"HXCHFEV3":
        version = "v3"
    else:
        raise HFEError("The file does not have a valid HFE signature.")
    tracks, sides = data[9], data[10]
    if not tracks or sides not in (1, 2):
        raise HFEError("The HFE track geometry is incomplete or invalid.")
    return HFEHeader(
        version=version,
        tracks=tracks,
        sides=sides,
        encoding=data[11],
        bitrate=int.from_bytes(data[12:14], "little"),
    )
