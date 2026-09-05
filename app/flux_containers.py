"""Shared policy for the flux containers HxCFE can decode and re-encode.

HFE and SCP are different files but the same workflow: decode the flux to raw
sectors, identify the Amiga filesystem inside, prove the sectors re-encode and
decode back byte-for-byte before permitting any edit, and prove it again before
handing the user a saved image.

That policy lived twice in ``disk_service``, once per container, and the two
copies had already drifted: only the HFE save path restored an omitted tail
sector, so saving an edited double-density SCP failed its own verification. Expressing
the rules once here means a container cannot quietly miss a fix made for its
sibling, and lets the geometry rules be unit tested without an HxCFE binary.

Nothing in this module runs a subprocess itself. ``FluxEngine`` is handed the
caller's ``run_hxcfe`` so the disk service keeps ownership of process
execution, error translation and timeouts.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .errors import DiskError


SECTOR_SIZE = 512

# The raw sector sizes an Amiga floppy geometry produces. A decode is only ever
# padded up to one of these, and only ever by a single trailing sector.
#
# OFS and FFS are the same media: an Amiga floppy is 80 cylinders of 11 sectors
# per side at double density, and 22 at high density. Which filing system
# formatted it is written in its boot block, not in its shape, so both kinds
# share one set of sizes. The 5.25-inch drive halves the cylinder count.
DOUBLE_DENSITY_SIZE = 80 * 2 * 11 * SECTOR_SIZE      # 901,120
HIGH_DENSITY_SIZE = 80 * 2 * 22 * SECTOR_SIZE        # 1,802,240
FIVE_INCH_SIZE = 40 * 2 * 11 * SECTOR_SIZE           # 450,560

FLOPPY_SIZES = frozenset({FIVE_INCH_SIZE, DOUBLE_DENSITY_SIZE, HIGH_DENSITY_SIZE})

CANONICAL_SIZES: dict[str, frozenset[int]] = {
    "ofs": FLOPPY_SIZES,
    "ffs": FLOPPY_SIZES,
}

# Filesystems the workbench can browse inside a flux container.
BROWSEABLE_KINDS = frozenset({"ofs", "ffs"})

# HxCFE's raw sector reader, used to decode every container back to sectors.
RAW_DECODER = "RAW_LOADER"

# HxCFE needs an explicit blank-disk layout to encode an Amiga track, because
# the Amiga writes whole tracks in its own MFM format rather than the
# PC-compatible sector layout HxCFE would otherwise assume. The 5.25-inch
# geometry has no blank layout of its own and so cannot be re-encoded.
_FLUX_LAYOUTS = {
    DOUBLE_DENSITY_SIZE: "AMIGADOS_DD",
    HIGH_DENSITY_SIZE: "AMIGADOS_HD",
}


@dataclass(frozen=True)
class FluxContainer:
    """One HxCFE-supported flux container and the words used to describe it."""

    identifier: str
    extension: str
    label: str
    plugin: str
    noun: str
    signature: bytes | None = None

    @property
    def display(self) -> str:
        return self.identifier.upper()


HFE = FluxContainer(
    identifier="hfe",
    extension=".hfe",
    label="HxC HFE flux image (.hfe)",
    plugin="HXC_HFE",
    noun="HFE image",
)

SCP = FluxContainer(
    identifier="scp",
    extension=".scp",
    label="SuperCard Pro flux image (.scp)",
    plugin="SCP_FLUX_STREAM",
    noun="SCP flux capture",
    signature=b"SCP",
)

FLUX_CONTAINERS: dict[str, FluxContainer] = {
    container.identifier: container for container in (HFE, SCP)
}


def sector_image_suffix(kind: str, size: int, sides: int = 1) -> str:
    """Return the canonical sector-image extension for a decoded geometry.

    Every Amiga floppy sector image is an ``.adf`` whichever filing system
    formatted it; ``.adz`` is that same file gzipped, so it is a compression
    choice made at export rather than a geometry. Anything larger than a
    floppy is a single-volume hard-disk image and takes ``.hdf``.

    ``sides`` is accepted because callers know it, but an Amiga floppy is
    always double sided and the shape alone settles the extension.
    """
    del kind, sides
    return ".adf" if size in FLOPPY_SIZES else ".hdf"


def flux_layout_for(kind: str, size: int) -> str | None:
    """Return HxCFE's blank-disk layout hint, or None when there is none."""
    if kind not in BROWSEABLE_KINDS:
        return None
    return _FLUX_LAYOUTS.get(size)


def is_flux_encodable(kind: str, size: int) -> bool:
    """Whether these sectors can be wrapped as flux by HxCFE.

    Only the double- and high-density 3.5-inch geometries have a blank layout
    HxCFE can build. The 5.25-inch geometry and every hard-disk image have no
    flux equivalent, and neither has a container to be wrapped in.
    """
    return flux_layout_for(kind, size) is not None


def restore_omitted_tail_sector(
    path: Path,
    kind: str,
    expected_size: int | None = None,
) -> bool:
    """Restore one omitted trailing sector from an otherwise complete decode.

    HxCFE's raw writer can omit an unreadable final 512-byte sector while still
    reporting every sector on the final track. A double-density decode then
    arrives as 900,608 bytes instead of 901,120 and geometry detection can
    select a linear hard-disk view instead of a floppy.

    Padding is only ever safe at the physical end of a known geometry, so this
    refuses to act unless the file is exactly one sector short of a canonical
    size for ``kind``. It never fills a gap in the middle of an image, and never
    grows a file by more than a single sector.

    Returns True when a sector was appended.
    """
    if not path.is_file():
        return False
    canonical_sizes = CANONICAL_SIZES.get(kind, frozenset())
    if not canonical_sizes:
        return False
    size = path.stat().st_size
    target = expected_size if expected_size in canonical_sizes else None
    if target is None:
        target = next(
            (value for value in canonical_sizes if size + SECTOR_SIZE == value),
            None,
        )
    if target is None or size + SECTOR_SIZE != target:
        return False
    with path.open("ab") as image:
        image.write(bytes(SECTOR_SIZE))
    return True


class FluxEngine:
    """The four HxCFE conversions the workbench needs, in one vocabulary.

    The engine is constructed with the caller's ``run_hxcfe`` callable, which
    takes a list of HxCFE arguments and returns its combined output. Errors are
    raised by that callable as ``DiskError``.
    """

    def __init__(self, run_hxcfe: Callable[[list[str]], str]) -> None:
        self._run_hxcfe = run_hxcfe

    def decode_to_sectors(self, source: Path, output: Path) -> str:
        """Decode any flux container to a raw sector image."""
        return self._run_hxcfe([
            f"-finput:{source}",
            f"-conv:{RAW_DECODER}",
            f"-foutput:{output}",
        ])

    def container_info(self, source: Path) -> str:
        """Return HxCFE's descriptive report for a container."""
        return self._run_hxcfe([f"-finput:{source}", "-infos"])

    def encode_from_sectors(
        self,
        sectors: Path,
        container: FluxContainer,
        output: Path,
        *,
        kind: str,
        reference: Path | None = None,
    ) -> str:
        """Wrap a raw sector image as flux, reusing an original's timing.

        ``reference`` is the container the sectors were decoded from. HxCFE
        uses it to preserve track timing that the sector view cannot express,
        so an edited image stays as close to the capture as possible.
        """
        layout = flux_layout_for(kind, sectors.stat().st_size)
        return self._run_hxcfe([
            f"-finput:{sectors}",
            *([f"-uselayout:{layout}"] if layout else []),
            f"-conv:{container.plugin}",
            f"-foutput:{output}",
            *([f"-reffile:{reference}"] if reference else []),
        ])

    def decodes_back_to(self, container_file: Path, sectors: Path, kind: str) -> bool:
        """Whether a container decodes back to exactly these sectors.

        The decode is normalised for a single omitted tail sector first, using
        the source image's size as the expected geometry, so a container is not
        rejected for the one artefact the workbench knows how to repair.
        """
        check = container_file.parent / f"{container_file.stem}-verify.img"
        check.unlink(missing_ok=True)
        try:
            self.decode_to_sectors(container_file, check)
            restore_omitted_tail_sector(
                check,
                kind,
                expected_size=sectors.stat().st_size,
            )
            return check.is_file() and check.read_bytes() == sectors.read_bytes()
        except DiskError:
            return False
        finally:
            check.unlink(missing_ok=True)

    def encode_and_verify(
        self,
        sectors: Path,
        container: FluxContainer,
        output: Path,
        *,
        kind: str,
        reference: Path | None = None,
        failure_message: str,
    ) -> Path:
        """Encode sectors as flux and refuse to return an inexact container.

        A flux image the workbench cannot decode back to the bytes it started
        from is never handed to the user: it would look like a saved disk while
        silently differing from what they edited.
        """
        output.unlink(missing_ok=True)
        self.encode_from_sectors(
            sectors,
            container,
            output,
            kind=kind,
            reference=reference,
        )
        if not output.is_file() or not output.stat().st_size:
            raise DiskError(
                f"HxCFE did not produce a usable {container.display} image."
            )
        if not self.decodes_back_to(output, sectors, kind):
            output.unlink(missing_ok=True)
            raise DiskError(failure_message)
        return output


__all__ = [
    "BROWSEABLE_KINDS",
    "CANONICAL_SIZES",
    "DOUBLE_DENSITY_SIZE",
    "FIVE_INCH_SIZE",
    "FLOPPY_SIZES",
    "HIGH_DENSITY_SIZE",
    "FLUX_CONTAINERS",
    "HFE",
    "SCP",
    "SECTOR_SIZE",
    "FluxContainer",
    "FluxEngine",
    "flux_layout_for",
    "is_flux_encodable",
    "restore_omitted_tail_sector",
    "sector_image_suffix",
]
