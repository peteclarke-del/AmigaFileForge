"""Describe the AmigaDOS layout exposed by a mounted volume.

The pane needs to know four things before it lets a user type a name or
create a directory: which filing-system variant this is, whether names are
folded with the international rules, whether the volume keeps a directory
cache, and how long a name may be. All four come from the boot block's DOS
type and the mounted volume, not from the file extension, because an ``.adf``
says nothing about the filing system inside it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

#: AmigaDOS names are 30 characters; the long-filename variants allow 107.
STANDARD_NAME_LIMIT = 30
LONG_NAME_LIMIT = 107


@dataclass(frozen=True)
class FFSCapabilities:
    """Pane-facing limits derived from the mounted on-disc structures."""

    format: str
    map: str
    directories: str
    name_limit: int
    directory_entry_limit: int | None

    def to_dict(self) -> dict:
        return asdict(self)


def capabilities_from_mount(mount) -> FFSCapabilities:
    """Return the format and directory limits for a mounted AmigaDOS volume.

    ``directory_entry_limit`` is ``None`` because an AmigaDOS directory is a
    hash table with overflow chains: it has no fixed entry count, and the only
    real limit is free blocks. Reporting a made-up number here would put a
    false ceiling in front of the user.
    """
    volume = getattr(mount, "volume", None)
    if volume is None:
        raise TypeError("The mounted filesystem is not an AmigaDOS volume.")

    return FFSCapabilities(
        format=volume.format,
        map="ffs" if volume.ffs else "ofs",
        directories="dircache" if volume.dircache else "hashed",
        name_limit=LONG_NAME_LIMIT if "LNFS" in volume.format else STANDARD_NAME_LIMIT,
        directory_entry_limit=None,
    )


def format_label(volume_format: str, size_bytes: int) -> str:
    """Return the familiar name for a volume, including its media size."""
    if size_bytes == 880 * 1024:
        media = "880 KiB floppy"
    elif size_bytes == 1760 * 1024:
        media = "1.76 MiB high-density floppy"
    elif size_bytes > 4 * 1024 * 1024:
        media = "hard-drive volume"
    else:
        media = "volume"
    return f"{volume_format} {media}"


__all__ = [
    "LONG_NAME_LIMIT",
    "STANDARD_NAME_LIMIT",
    "FFSCapabilities",
    "capabilities_from_mount",
    "format_label",
]
