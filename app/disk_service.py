from __future__ import annotations

import gzip
import io
import os
import re
import shutil
import subprocess
import threading
import uuid
import zipfile
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Callable

from .ffs_install_service import FFSInstallMixin
from .hardfile_geometry import (
    BLOCK_SIZE as HARDFILE_SECTOR_SIZE,
    MAX_SIZE as HARDFILE_MAX_SIZE,
    block_checksum,
    descriptor_size as HARDFILE_DESCRIPTOR_SIZE,
    volume_extent,
    range_is_zero,
)
from .checkpoints import CheckpointStore
from .content_kind import LISTING_SNIFF_LIMIT, analyse_content, metadata_kind
from .disk_tools import decode_disc_json, friendly_engine_error, run_disc, run_hxcfe
from .errors import DiskError
from .image_session import (
    ImageSession as ImageSession,
    SESSION_OWNER as SESSION_OWNER,
)
from .formats import (
    DMS_EXTENSIONS,
    FFS_EXTENSIONS,
    HDF_EXTENSIONS,
    HFE_EXTENSIONS,
    IPF_EXTENSIONS,
    OFS_EXTENSIONS,
    ROM_EXTENSIONS,
    SCP_EXTENSIONS,
)
from .amiga_metadata import format_protection, parse_protection
from .filename_policy import session_name_policy
from .filesystem_disk_service import FilesystemDiskMixin
from .amiganut_internals import (
    ensure_directory_chain,
    file_copy_item,
    in_storage_order,
    natural_name_key,
    write_copy_item,
)
from .rom_disk_service import RomDiskMixin
from .rdb_service import RdbPartitionMixin
from .session_disk_service import SessionDiskMixin
from .dms_disk_service import DMSDiskMixin
from .session_state import normalise_warnings
from .rom import (
    DEFAULT_BANK_SIZE,
    MAX_ROM_SIZE,
    RomError,
    bank_number,
    make_expansion_rom,
    parse_rom_header,
    validate_bank_size,
    validate_layout,
    validate_platform,
)
from .ofs_compat import (
    is_two_volume_dump,
    root_block_number,
)
from .flux_containers import (
    BROWSEABLE_KINDS,
    FLOPPY_SIZES,
    FLUX_CONTAINERS,
    HFE,
    SCP,
    FluxContainer,
    FluxEngine,
    is_flux_encodable,
    restore_omitted_tail_sector,
    sector_image_suffix,
)
from .hfe import HFEError, HFEHeader, parse_hfe_header
from .dms import (
    DMSError,
    parse_dms,
)
from . import amiga_paths


COPY_BUFFER_SIZE = 8 * 1024 * 1024
FICLONE = 0x40049409
class DiskService(
    SessionDiskMixin,
    FilesystemDiskMixin,
    FFSInstallMixin,
    RdbPartitionMixin,
    RomDiskMixin,
    DMSDiskMixin,
):
    _hardfile_descriptor_size = staticmethod(HARDFILE_DESCRIPTOR_SIZE)
    _volume_extent = staticmethod(volume_extent)
    _range_is_zero = staticmethod(range_is_zero)
    _block_checksum = staticmethod(block_checksum)
    _normalise_warnings = staticmethod(normalise_warnings)

    def __init__(self, work_dir: str | Path):
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.sessions: dict[str, ImageSession] = {}
        self._lock = threading.RLock()
        self.checkpoints = CheckpointStore(self._copy_local_file)

    @staticmethod
    @contextmanager
    def _locked_sessions(*sessions: ImageSession):
        """Acquire one or more session locks once, in a stable order."""
        locks = {
            id(session.lock): session.lock
            for session in sessions
        }
        with ExitStack() as stack:
            for _identity, lock in sorted(locks.items()):
                stack.enter_context(lock)
            yield

    @staticmethod
    def _append_warning(session: ImageSession, warning: str) -> None:
        if warning not in session.warnings:
            session.warnings.append(warning)

    @staticmethod
    def safe_filename(name: str) -> str:
        name = Path(name or "image").name
        return re.sub(r"[^A-Za-z0-9._() +!-]", "_", name)[:180] or "image"

    @staticmethod
    def detect_kind(name: str) -> str:
        ext = Path(name).suffix.lower()
        if ext in HDF_EXTENSIONS:
            return "hdf"
        if ext in OFS_EXTENSIONS:
            return "ofs"
        if ext in FFS_EXTENSIONS:
            return "ffs"
        if ext in DMS_EXTENSIONS:
            return "dms"
        if ext in HFE_EXTENSIONS:
            return "hfe"
        if ext in SCP_EXTENSIONS:
            return "scp"
        if ext in IPF_EXTENSIONS:
            return "ipf"
        if ext in ROM_EXTENSIONS:
            return "rom"
        return "unknown"

    def identify_kind(self, path: Path, expected_kind: str | None = None) -> str:
        """Identify media, constraining probes when its format is already known.

        Amiganut's generic identifier asks every installed filing system to
        inspect the image. That is right for an extensionless file, but
        needlessly expensive when the format is already known: a Kickstart
        probe over a hard-drive-sized file reads the whole thing. Restricting
        the cascade still validates the bytes and leaves the generic path
        available for ambiguous names.
        """
        expected_filesystems = {
            "ffs": ("ffs", "ofs", "rdb"),
            "ofs": ("ofs", "ffs"),
            "hdf": ("rdb", "ffs", "ofs"),
            "kickfs": ("kickfs",),
        }.get(expected_kind or "")
        if expected_filesystems:
            try:
                from amiganut.filesystem import create_filesystem, identify

                filesystems = {
                    name: create_filesystem(name) for name in expected_filesystems
                }
                candidates = identify(
                    path,
                    suffix_hint=path.suffix.lower(),
                    filesystems=filesystems,
                )
                rows = [
                    {"filesystem": candidate.filesystem}
                    for candidate in candidates
                ]
            except Exception as exc:
                raise DiskError(friendly_engine_error(str(exc))) from exc
        else:
            result = self._run_json(["identify", "--as", "json", str(path)])
            rows = result.get("reports", {}).get("candidates", {}).get("rows", [])
        if not rows:
            raise DiskError(
                "No AmigaDOS filing system was found in the uploaded bytes. "
                "The filename extension is only a hint. Supply the raw, uncompressed "
                "image rather than an emulator wrapper, an archive member or a flux "
                "capture. This build recognises OFS and FFS volumes (DOS\\0 to "
                "DOS\\5, including International and Directory Cache), RDB "
                "partitioned hard drives and Kickstart ROMs. The source image "
                "has not been changed."
            )
        filesystem = str(rows[0].get("filesystem", "")).lower()
        if filesystem in {"ofs", "amigados"}:
            return "ofs"
        if filesystem == "ffs":
            return "ffs"
        if filesystem == "rdb":
            return "hdf"
        if filesystem == "kickfs":
            return "kickfs"
        raise DiskError(f"The detected {filesystem or 'unknown'} filesystem is not supported.")

    @staticmethod
    def validate_leaf_name(session: ImageSession, name: str) -> str:
        return session_name_policy(session).validate(name)

    @staticmethod
    def require_writable_geometry(session: ImageSession) -> None:
        if session.hfe_read_only:
            raise DiskError(
                "This HFE uses advanced track features or contains unreadable sectors. "
                "It can be browsed and copied from, but cannot be rewritten safely."
            )
        if session.scp_read_only:
            raise DiskError(
                "This SCP flux capture could not be re-encoded and decoded back to identical sectors. "
                "It can be browsed and copied from, but cannot be rewritten safely."
            )
        if session.kind == "kickfs":
            try:
                from amiganut.kickfs.kickfs import KICKFS
                kickfs = KICKFS.from_bytes(session.path.read_bytes())
            except Exception as exc:
                raise DiskError(f"The Kickstart ROM cannot be edited safely: {exc}") from exc
            if not kickfs.is_complete:
                raise DiskError(
                    "This Kickstart ROM is incomplete or part of a multi-ROM set. "
                    "It can be browsed and extracted, but not rebuilt safely."
                )
            if not kickfs.is_plain:
                raise DiskError(
                    "This composite Kickstart ROM contains executable code after its files. "
                    "It is read-only because moving that code could break absolute addresses."
                )
        if (
            session.kind in {"ffs", "ofs"}
            and session.path.suffix.lower() in {".hdf", ".hda"}
            and session.descriptor_path is None
            and session.ffs_capabilities.get("map") != "new"
        ):
            raise DiskError(
                "This bare Hardfile HDA image was opened without its matching GEO "
                "geometry file, which is where its surfaces, blocks per track and "
                "cylinders are recorded. Reopen the original HDA and GEO together "
                "before making changes. An image carrying a Rigid Disk Block "
                "describes its own geometry and needs no sidecar."
            )

    def create_from_stream(
        self,
        name: str,
        stream: BinaryIO,
        descriptor: tuple[str, BinaryIO] | None = None,
        target_hardware: str = "auto",
        rom_options: dict | None = None,
        force_kind: str | None = None,
    ) -> ImageSession:
        safe_name, kind, descriptor_name = self._new_session_source(
            name,
            descriptor[0] if descriptor else None,
            force_kind,
        )
        image_id = uuid.uuid4().hex
        folder = self.work_dir / image_id
        folder.mkdir()
        path = folder / safe_name
        try:
            self._copy_stream(stream, path)
            descriptor_path = None
            if descriptor and descriptor_name:
                descriptor_path = folder / descriptor_name
                self._copy_stream(descriptor[1], descriptor_path)
            return self._finalize_new_session(
                image_id,
                safe_name,
                path,
                descriptor_name,
                descriptor_path,
                kind,
                target_hardware,
                rom_options,
            )
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def create_from_path(
        self,
        source: Path,
        descriptor: Path | None = None,
        target_hardware: str = "auto",
        rom_options: dict | None = None,
        force_kind: str | None = None,
    ) -> ImageSession:
        """Create a private session from a trusted local desktop path.

        Local paths use the filesystem clone/sparse-copy path instead of
        passing hundreds of megabytes through multipart and a spooled upload.
        The source remains untouched and all edits still target the session.
        """
        source = Path(source)
        descriptor = Path(descriptor) if descriptor is not None else None
        safe_name, kind, descriptor_name = self._new_session_source(
            source.name,
            descriptor.name if descriptor else None,
            force_kind,
        )
        image_id = uuid.uuid4().hex
        folder = self.work_dir / image_id
        folder.mkdir()
        path = folder / safe_name
        try:
            self._copy_local_file(source, path)
            descriptor_path = None
            if descriptor and descriptor_name:
                descriptor_path = folder / descriptor_name
                self._copy_local_file(descriptor, descriptor_path)
            return self._finalize_new_session(
                image_id,
                safe_name,
                path,
                descriptor_name,
                descriptor_path,
                kind,
                target_hardware,
                rom_options,
            )
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def _new_session_source(
        self,
        name: str,
        descriptor_name: str | None,
        force_kind: str | None,
    ) -> tuple[str, str, str | None]:
        """Validate and normalise names shared by stream and local opens."""
        safe_name = self.safe_filename(name)
        kind = self.detect_kind(safe_name)
        if force_kind:
            if force_kind != "rom":
                raise DiskError("Only the raw ROM format override is supported.")
            kind = force_kind
        safe_descriptor = self.safe_filename(descriptor_name) if descriptor_name else None
        if safe_descriptor and not safe_name.lower().endswith((".hdf", ".hda")):
            raise DiskError("A GEO descriptor can only accompany a Hardfile HDA image.")
        if safe_descriptor and Path(safe_descriptor).suffix.lower() != ".geo":
            raise DiskError("The Hardfile geometry file must use the GEO extension.")
        if (
            safe_name.lower().endswith((".hdf", ".hda"))
            and safe_descriptor
            and Path(safe_descriptor).stem.casefold()
            != Path(safe_name).stem.casefold()
        ):
            raise DiskError(f"Choose {Path(safe_name).stem}.geo for this HDA image.")
        return safe_name, kind, safe_descriptor

    #: The largest image a gzip container is expanded into. An ADZ or an HDZ
    #: holds an ordinary disk image, so anything past a full hard drive is a
    #: decompression bomb rather than a disk.
    MAX_EXPANDED_IMAGE = 2 * 1024 * 1024 * 1024

    @staticmethod
    def _expand_gzip_image(path: Path) -> Path:
        """Expand a gzip-compressed disk image in place.

        An ``.adz`` is an ``.adf`` that has been gzipped, and an ``.hdz`` is a
        gzipped ``.hdf``; that is the whole of what those extensions mean. The
        workbench reads sectors, so a compressed image is expanded once as it
        arrives and keeps the name the user gave it. A file that is not gzip is
        returned untouched, because the extension is a hint and never a
        decision.
        """
        try:
            with path.open("rb") as image:
                if image.read(2) != b"\x1f\x8b":
                    return path
        except OSError:
            return path
        expanded = path.with_name(path.name + ".expanded")
        try:
            with gzip.open(path, "rb") as compressed, expanded.open("wb") as output:
                written = 0
                while True:
                    chunk = compressed.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > DiskService.MAX_EXPANDED_IMAGE:
                        raise DiskError(
                            "This compressed image expands to more than 2 GiB, "
                            "which is larger than any disk the workbench opens."
                        )
                    output.write(chunk)
        except DiskError:
            expanded.unlink(missing_ok=True)
            raise
        except (OSError, EOFError) as exc:
            expanded.unlink(missing_ok=True)
            raise DiskError(
                "This image is gzip compressed but could not be expanded. "
                "The file is truncated or damaged."
            ) from exc
        expanded.replace(path)
        return path

    def is_two_volume_image(self, session: ImageSession) -> bool:
        """Whether one file holds two double-density volumes rather than one.

        A two-disk set is sometimes preserved as a single file, which is the
        same length as one high-density floppy. The two are told apart by
        where the root blocks sit, which costs three block reads.
        """
        if session.kind not in {"ofs", "ffs"}:
            return False
        try:
            size = session.path.stat().st_size
            with session.path.open("rb") as image:

                def read_block(number: int) -> bytes:
                    image.seek(number * 512)
                    return image.read(512)

                return is_two_volume_dump(read_block, size // 512)
        except OSError:
            return False

    def _finalize_new_session(
        self,
        image_id: str,
        name: str,
        path: Path,
        descriptor_name: str | None,
        descriptor_path: Path | None,
        kind: str,
        target_hardware: str = "auto",
        rom_options: dict | None = None,
    ) -> ImageSession:
        path = self._expand_gzip_image(path)
        if kind == "hfe":
            path, kind, hfe_original, hfe_header, hfe_read_only, hfe_warnings = self._open_hfe(path)
        else:
            hfe_original = None
            hfe_header = None
            hfe_read_only = False
            hfe_warnings = []
        if kind == "ipf":
            path, kind, ipf_warnings = self._open_ipf(path)
        else:
            ipf_warnings = []
        if kind == "scp":
            path, kind, scp_original, scp_read_only, scp_warnings = self._open_scp(path)
        else:
            scp_original = None
            scp_read_only = False
            scp_warnings = []
        # BIN is also used for FFS images.  Prefer ROM only when the contents
        # carry a structurally valid Amiga ROM header; .rom is explicit.
        if kind == "ffs" and path.suffix.lower() == ".bin":
            with path.open("rb") as source:
                if parse_rom_header(source.read(DEFAULT_BANK_SIZE)):
                    kind = "rom"
        if kind == "rom":
            try:
                if self.identify_kind(path, "kickfs") == "kickfs":
                    kind = "kickfs"
            except DiskError:
                pass
        identified = kind == "unknown"
        if identified:
            kind = self.identify_kind(path)
        session = ImageSession(
            id=image_id,
            name=name,
            kind=kind,
            path=path,
            descriptor_name=descriptor_name,
            descriptor_path=descriptor_path,
            target_hardware=self._target_hardware(target_hardware),
            hfe_original_path=hfe_original,
            hfe_version=hfe_header.version if hfe_header else None,
            hfe_read_only=hfe_read_only,
            scp_original_path=scp_original,
            scp_read_only=scp_read_only,
            warnings=hfe_warnings + scp_warnings + ipf_warnings,
        )
        if kind == "ffs":
            self.refresh_ffs_capabilities(session)
        if kind == "dms":
            try:
                session.dms = parse_dms(path.read_bytes())
            except DMSError as exc:
                raise DiskError(str(exc)) from exc
        elif kind == "rom":
            rom_options = rom_options or {}
            try:
                session.rom_platform = validate_platform(rom_options.get("platform"))
                session.rom_layout = validate_layout(rom_options.get("layout"))
            except RomError as exc:
                raise DiskError(str(exc)) from exc
            session.rom_component_names = [
                self.safe_filename(name)
                for name in rom_options.get("componentNames", [])
                if str(name).strip()
            ]
            size = path.stat().st_size
            if not size or size > MAX_ROM_SIZE:
                raise DiskError("ROM images must contain between 1 byte and 64 MiB.")
            if size % DEFAULT_BANK_SIZE:
                session.warnings.append(
                    f"The final ROM bank is partial ({size % DEFAULT_BANK_SIZE:,} bytes). "
                    "It is preserved exactly; choose another bank size if this layout is intentional."
                )
        elif kind == "kickfs":
            details = self.kickfs_details(session)
            if details["readOnly"]:
                session.warnings.extend(details["warnings"])
        elif not identified:
            detected_kind = self.identify_kind(path, kind)
            if detected_kind != kind:
                session.kind = detected_kind
        self._normalise_hardfile_dat_size(session)
        self._apply_target_hardware(session)
        with self._lock:
            self.sessions[image_id] = session
        self._persist_session(session)
        return session

    # Flux geometry policy is shared with the SCP container and unit tested
    # without HxCFE; see app/flux_containers.py.
    _hfe_working_suffix = staticmethod(sector_image_suffix)
    _normalise_decoded_flux_size = staticmethod(restore_omitted_tail_sector)

    @property
    def _flux(self) -> FluxEngine:
        return FluxEngine(self._run_hxcfe)

    def _decode_flux_to_sectors(
        self,
        original: Path,
        container: FluxContainer,
        *,
        sides: int = 1,
    ) -> tuple[Path, str, bool, str]:
        """Decode a flux container and place its sectors under a working name.

        Shared by both containers: decode, refuse an empty or non-Amiga result,
        repair a single omitted tail sector, then rename to the extension that
        matches the recovered geometry so the rest of the workbench sees an
        ordinary sector image.

        Returns the working path, the filesystem kind, whether a tail sector was
        restored, and HxCFE's decode output.
        """
        raw = original.parent / f"{container.identifier}-decoded.img"
        decode_info = self._flux.decode_to_sectors(original, raw)
        if not raw.is_file() or not raw.stat().st_size:
            raise DiskError(
                f"The {container.noun} did not contain a usable sector filesystem."
            )
        try:
            kind = self.identify_kind(raw)
        except DiskError as exc:
            raise DiskError(
                f"HxCFE decoded the {container.noun}, but the resulting sectors do not "
                "contain a supported OFS or FFS filesystem. The "
                f"{container.display} container is valid, but its contents cannot be "
                "browsed as an Amiga disk image."
            ) from exc
        if kind not in BROWSEABLE_KINDS:
            raise DiskError(
                f"HxCFE decoded the {container.noun} as {kind.upper()}, but only "
                f"OFS- and FFS-formatted {container.display} images are browseable."
            )
        padded_tail = restore_omitted_tail_sector(raw, kind)
        working = raw.with_suffix(sector_image_suffix(kind, raw.stat().st_size, sides))
        raw.replace(working)
        return working, kind, padded_tail, decode_info

    def _open_hfe(self, original: Path) -> tuple[Path, str, Path, HFEHeader, bool, list[str]]:
        try:
            with original.open("rb") as source:
                header = parse_hfe_header(source.read(512))
        except (OSError, HFEError) as exc:
            raise DiskError(str(exc)) from exc
        info = self._flux.container_info(original)
        working, kind, _padded_tail, _decode_info = self._decode_flux_to_sectors(
            original, HFE, sides=header.sides
        )
        bad_match = re.search(r"Number of bad sectors\s*:\s*(\d+)", info, re.IGNORECASE)
        bad_sectors = int(bad_match.group(1)) if bad_match else 0
        read_only = header.advanced or bad_sectors > 0
        warnings = [
            f"Opened HFE {header.version}: {header.tracks} tracks, {header.sides} side"
            f"{'s' if header.sides != 1 else ''}, {header.bitrate or 'variable'} Kbit/s."
        ]
        if read_only:
            reason = "advanced timing/track features" if header.advanced else f"{bad_sectors} unreadable sector(s)"
            warnings.append(
                f"This HFE contains {reason}. It is read-only to preserve data that a sector editor cannot represent."
            )
        return working, kind, original, header, read_only, warnings

    def _open_scp(self, original: Path) -> tuple[Path, str, Path, bool, list[str]]:
        try:
            with original.open("rb") as source:
                signature = source.read(3)
        except OSError as exc:
            raise DiskError(f"The SCP flux capture could not be read: {exc}") from exc
        if signature != b"SCP":
            raise DiskError("The selected file does not have a valid SuperCard Pro SCP signature.")
        working, kind, padded_tail, decode_info = self._decode_flux_to_sectors(original, SCP)
        try:
            self._run(["validate", str(working)])
        except DiskError as exc:
            raise DiskError(
                "The SCP capture contains missing or inconsistent filesystem sectors. "
                "HxCFE recovered an Amiga filesystem header, but the complete directory "
                f"tree is not safe to browse: {exc}"
            ) from exc
        read_only = not self._scp_round_trips(working, original, kind)
        warnings = [
            f"Opened SCP flux capture: HxCFE decoded an {kind.upper()} sector filesystem "
            f"({working.stat().st_size:,} bytes)."
        ]
        if padded_tail:
            warnings.append(
                "HxCFE omitted the blank final 256-byte sector from the capture. "
                "Amiga File Forge restored the declared floppy geometry before validation."
            )
        if "Invalid rpm or tracklen" in decode_info:
            warnings.append(
                "The capture contains non-standard index timing reported by HxCFE. "
                "The recovered sectors passed full filesystem validation."
            )
        if read_only:
            warnings.append(
                "This SCP capture could not be re-encoded and decoded back to identical sectors, so it is "
                "read-only. It can be browsed and copied from, but not rewritten safely."
            )
        return working, kind, original, read_only, warnings

    def _open_ipf(self, original: Path) -> tuple[Path, str, list[str]]:
        """Decode an SPS capture into the sectors an AmigaDOS volume holds.

        The capture itself is kept beside the working image and never edited:
        an IPF records timing and protection an ADF cannot express, so the
        image the workbench opens is a reading of it rather than a copy.
        """
        from .ipf import IPFError, read_ipf

        try:
            report = read_ipf(original)
        except IPFError as exc:
            raise DiskError(str(exc)) from exc
        working = original.with_suffix(".adf")
        if working == original:
            working = original.with_name(f"{original.stem}-decoded.adf")
        working.write_bytes(report.sectors)
        warnings = [
            f"{original.name} was decoded from an SPS capture. "
            f"{report.recovered:,} of {report.expected:,} standard sectors were "
            "recovered; anything the capture holds that a sector image cannot "
            "represent is not in this image."
        ]
        warnings.extend(report.warnings[:20])
        if len(report.warnings) > 20:
            warnings.append(
                f"{len(report.warnings) - 20} further track warnings were not listed."
            )
        return working, self.identify_kind(working), warnings

    def _scp_round_trips(self, working: Path, original: Path, kind: str) -> bool:
        """Confirm HxCFE can re-encode these sectors before allowing edits.

        An SCP capture that cannot be rebuilt from its own decoded sectors is
        opened read-only rather than risking a save the user cannot verify.
        """
        probe = working.parent / "scp-open-check.scp"
        probe.unlink(missing_ok=True)
        try:
            self._flux.encode_from_sectors(
                working, SCP, probe, kind=kind, reference=original
            )
            return self._flux.decodes_back_to(probe, working, kind)
        except DiskError:
            return False
        finally:
            probe.unlink(missing_ok=True)

    @staticmethod
    def _target_hardware(value: str | None) -> str:
        profile = str(value or "auto").strip().lower()
        if profile not in {
            "auto",
            "a500-ofs",
            "a1200-ffs",
            "hardfile",
            "amigaos",
        }:
            raise DiskError("Unknown AmigaDOS target hardware profile.")
        return profile

    #: Which filing-system variants each Kickstart mounts without help.
    #: Kickstart 1.3 has no FastFileSystem in ROM: an FFS volume needs
    #: ``L:FastFileSystem`` on a bootable OFS disk before it will mount at all.
    TARGET_FILESYSTEMS = {
        "a500-ofs": ({"OFS"}, {"FFS"}, "Amiga 500 or 2000 with Kickstart 1.3"),
        "a1200-ffs": (
            {"OFS", "FFS", "OFS-INTL", "FFS-INTL", "OFS-DC", "FFS-DC"},
            set(),
            "Amiga 600 or 1200 with Kickstart 3.x",
        ),
        "amigaos": (
            {"OFS", "FFS", "OFS-INTL", "FFS-INTL", "OFS-DC", "FFS-DC"},
            set(),
            "an AmigaOS hard drive",
        ),
        "hardfile": (
            {"OFS", "FFS", "OFS-INTL", "FFS-INTL", "OFS-DC", "FFS-DC"},
            set(),
            "a UAE hardfile",
        ),
    }

    def _apply_target_hardware(self, session: ImageSession) -> None:
        """Check and repair a volume for the machine it is destined for.

        Two things are worth checking before an image leaves the workbench,
        because both produce a disk that looks fine here and fails on real
        hardware. The first is the filing-system variant: a Kickstart 1.3
        machine has no FastFileSystem in ROM, so an FFS floppy simply will not
        mount. The second is the block-allocation bitmap's valid flag, which a
        machine reads as "this volume was not shut down cleanly" and answers by
        refusing to write to it.

        Only the second is repaired. The filing system is reported, never
        changed, because silently reformatting a volume would destroy exactly
        the data the user is trying to move.
        """
        if session.kind not in {"ffs", "ofs"} or session.target_hardware == "auto":
            return
        profile = self.TARGET_FILESYSTEMS.get(session.target_hardware)
        if profile is None:
            return
        supported, needs_handler, machine = profile
        if session.target_hardware == "hardfile" and (
            session.path.suffix.lower() not in {".hdf", ".hda"}
            or session.descriptor_path is None
        ):
            raise DiskError(
                "The UAE hardfile target requires a bare hard-drive image and "
                "its matching GEO geometry sidecar."
            )
        if session.path.suffix.lower() in {".hdf", ".hda"} and session.descriptor_path is None:
            raise DiskError(
                "This hardfile target requires the HDA and its matching GEO to be "
                "opened together, because a hardfile carries no geometry of its own."
            )

        try:
            with self.ffs_mount(session) as mount:
                volume_format = str(getattr(mount, "format", ""))
                has_handler = mount.exists("L/FastFileSystem")
                bitmap_repaired = self._finalise_hardfile_directories(session)
        except DiskError as exc:
            raise DiskError(f"This image is not compatible with {machine}: {exc}") from exc

        if volume_format in needs_handler and not has_handler:
            self._append_warning(
                session,
                f"This is a {volume_format} volume. {machine.capitalize()} has no "
                "FastFileSystem in ROM, so it will not mount until "
                "L/FastFileSystem and a Mountlist entry are present.",
            )
        elif volume_format not in supported:
            self._append_warning(
                session,
                f"{machine.capitalize()} cannot mount a {volume_format} volume. "
                "Copy the files to an OFS or FFS volume before using this image.",
            )
        if bitmap_repaired:
            self._append_warning(
                session,
                f"Repaired {bitmap_repaired} block checksum"
                f"{'s' if bitmap_repaired != 1 else ''} and revalidated the "
                f"block-allocation bitmap for {machine}.",
            )

    @staticmethod
    def _validate_created_hardfile_pair(session: ImageSession) -> None:
        """Reject a newly created pair that an emulator or Amiga cannot mount."""
        descriptor_path = session.descriptor_path
        if descriptor_path is None:
            raise DiskError("The disk engine did not create the hardfile GEO sidecar.")
        try:
            declared = HARDFILE_DESCRIPTOR_SIZE(descriptor_path)
            actual_size = session.path.stat().st_size
        except OSError as exc:
            raise DiskError("The new hardfile pair could not be verified.") from exc
        if declared is None:
            raise DiskError(
                "The new GEO sidecar does not declare surfaces, sectors and cylinders."
            )
        if declared > HARDFILE_MAX_SIZE:
            raise DiskError(
                "The requested hardfile exceeds this build's "
                f"{HARDFILE_MAX_SIZE // (1024 * 1024):,} MiB limit."
            )
        if declared != actual_size:
            raise DiskError(
                f"The GEO sidecar declares {declared:,} bytes but the HDA holds "
                f"{actual_size:,}. A hardfile must match its geometry exactly."
            )
        if DiskService._volume_extent(session.path) is None:
            raise DiskError(
                "The new HDA does not contain a readable AmigaDOS root block."
            )

    @staticmethod
    def _canonicalise_created_hardfile_root(
        session: ImageSession,
        title: str,
    ) -> None:
        """Confirm the new volume's root block carries the requested name."""
        try:
            with session.path.open("rb") as image:
                size = session.path.stat().st_size
                root_block = root_block_number(size // HARDFILE_SECTOR_SIZE)
                image.seek(root_block * HARDFILE_SECTOR_SIZE)
                root = image.read(HARDFILE_SECTOR_SIZE)
        except OSError as exc:
            raise DiskError(
                "The new hardfile root directory could not be verified."
            ) from exc
        if len(root) != HARDFILE_SECTOR_SIZE or int.from_bytes(root[0:4], "big") != 2:
            raise DiskError("The disk engine created an invalid AmigaDOS root block.")
        offset = HARDFILE_SECTOR_SIZE - 80
        length = min(root[offset], 30)
        stored = root[offset + 1 : offset + 1 + length].decode("latin-1", "replace")
        if stored != str(title or "")[:30]:
            raise DiskError(
                f"The new volume is named “{stored}” rather than “{title}”."
            )

    def _normalise_hardfile_dat_size(self, session: ImageSession) -> None:
        """Keep an HDA exactly the size its GEO sidecar declares."""
        if (
            session.path.suffix.lower() not in {".hdf", ".hda"}
            or session.descriptor_path is None
        ):
            return
        geometry_size = self._hardfile_descriptor_size(session.descriptor_path)
        if geometry_size is None:
            self._append_warning(
                session,
                "The GEO geometry could not be read; the HDA size was left unchanged.",
            )
            return
        actual = session.path.stat().st_size
        if actual == geometry_size:
            return
        if actual > geometry_size and self._range_is_zero(session.path, geometry_size):
            with session.path.open("r+b") as image:
                image.truncate(geometry_size)
            session.dirty = True
            self._append_warning(
                session,
                f"Removed an all-zero {actual - geometry_size:,}-byte tail so the HDA "
                "matches the capacity its GEO sidecar declares.",
            )
        elif actual > geometry_size:
            self._append_warning(
                session,
                "The HDA holds non-zero data beyond the capacity its GEO sidecar "
                "declares and was not truncated.",
            )
        else:
            self._append_warning(
                session,
                f"The HDA is {geometry_size - actual:,} bytes shorter than its GEO "
                "sidecar declares and was not padded, because filesystem data may "
                "be missing.",
            )

    @staticmethod
    def _optimise_sparse_file(path: Path) -> None:
        """Turn allocated zero ranges into holes without changing file bytes."""
        try:
            original = path.stat()
            subprocess.run(
                ["fallocate", "--dig-holes", str(path)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            os.utime(
                path,
                ns=(original.st_atime_ns, original.st_mtime_ns),
                follow_symlinks=False,
            )
        except (OSError, subprocess.CalledProcessError):
            # Sparse optimisation is an optional performance improvement. The
            # image remains valid on filesystems or platforms without it.
            return

    @staticmethod
    def _finalise_hardfile_directories(session: ImageSession) -> int:
        """Repair block checksums and revalidate the allocation bitmap.

        Every AmigaDOS block ends with a checksum over its own longs, and the
        root block carries a flag saying whether the block-allocation bitmap
        can be trusted. A machine that finds either wrong refuses to write to
        the volume and offers ``DiskDoctor`` instead. Both are recomputable
        from the structures themselves, so they are repaired here rather than
        left for the user to discover on real hardware.

        Returns the number of blocks changed.
        """
        if session.kind not in {"ffs", "ofs"}:
            return 0

        from amiganut.filesystem.blocks import (
            apply_checksum,
            long_at,
            verify_checksum,
        )

        repaired = 0
        block_size = HARDFILE_SECTOR_SIZE
        with session.lock, session.path.open("r+b") as image:
            size = session.path.stat().st_size
            total_blocks = size // block_size
            root_block = root_block_number(total_blocks)
            visited: set[int] = set()
            pending = [root_block]
            while pending:
                number = pending.pop()
                if number in visited or not 0 <= number < total_blocks:
                    continue
                visited.add(number)
                image.seek(number * block_size)
                block = bytearray(image.read(block_size))
                if len(block) != block_size:
                    continue
                if long_at(block, 0) != 2:
                    continue
                if not verify_checksum(bytes(block)):
                    apply_checksum(block)
                    image.seek(number * block_size)
                    image.write(bytes(block))
                    repaired += 1
                table_size = long_at(block, 12) or (block_size // 4 - 56)
                if not 8 <= table_size <= block_size // 4:
                    continue
                for index in range(table_size):
                    child = long_at(block, 24 + index * 4)
                    if child:
                        pending.append(child)
                chain = long_at(block, block_size - 16)
                if chain:
                    pending.append(chain)

            # The bitmap flag is the last thing to restore, so it is only set
            # once every block it accounts for has been checked.
            image.seek(root_block * block_size)
            root = bytearray(image.read(block_size))
            if len(root) == block_size and long_at(root, block_size - 200) != 0xFFFFFFFF:
                root[block_size - 200 : block_size - 196] = b"\xff\xff\xff\xff"
                apply_checksum(root)
                image.seek(root_block * block_size)
                image.write(bytes(root))
                repaired += 1

        if repaired:
            session.dirty = True
        return repaired

    @staticmethod
    def _advance_hardfile_disc_id(session: ImageSession) -> bool:
        """Restamp a changed volume so a machine notices it was modified.

        AmigaDOS caches a mounted volume by its name and creation datestamp.
        Writing a new modification datestamp into the root block is what makes
        a real machine, and an emulator holding the image open, re-read the
        volume instead of serving a stale cache.
        """
        from amiganut.file import datetime_to_datestamp
        from amiganut.filesystem.blocks import apply_checksum, long_at, put_long

        with session.lock:
            source_mtime = session.path.stat().st_mtime_ns
            if session.finalised_mtime_ns == source_mtime:
                return False
            block_size = HARDFILE_SECTOR_SIZE
            with session.path.open("r+b") as image:
                total_blocks = session.path.stat().st_size // block_size
                root_block = root_block_number(total_blocks)
                image.seek(root_block * block_size)
                root = bytearray(image.read(block_size))
                if len(root) != block_size or long_at(root, 0) != 2:
                    raise DiskError("The volume root block could not be read.")
                days, mins, ticks = datetime_to_datestamp(
                    datetime.fromtimestamp(source_mtime / 1_000_000_000, timezone.utc)
                )
                # The stamp has to move forward, or a machine that already
                # cached the volume will not notice the edit. Writing the file
                # time alone is not enough, because an image edited within the
                # same tick would carry the stamp it already had.
                stored = tuple(long_at(root, block_size - 92 + step) for step in (0, 4, 8))
                if (days, mins, ticks) <= stored:
                    days, mins, ticks = stored
                    ticks += 1
                    if ticks >= 3000:
                        ticks, mins = 0, mins + 1
                    if mins >= 1440:
                        mins, days = 0, days + 1
                for back in (92, 40):
                    base = block_size - back
                    put_long(root, base, days)
                    put_long(root, base + 4, mins)
                    put_long(root, base + 8, ticks)
                apply_checksum(root)
                image.seek(root_block * block_size)
                image.write(bytes(root))
            session.dirty = True
            session.finalised_mtime_ns = session.path.stat().st_mtime_ns
            return True

    def prepare_download(
        self,
        session: ImageSession,
        progress: Callable[[str, int | None, int | None], None] | None = None,
    ) -> Path:
        """Finalise an image so the downloaded bytes are hardware-ready."""
        report = progress or (lambda _message, _current=None, _total=None: None)
        is_hardfile = bool(
            session.descriptor_path and session.path.suffix.lower() in {".hdf", ".hda"}
        )
        if is_hardfile:
            self._optimise_sparse_file(session.path)
        if (
            is_hardfile
            and not session.dirty
            and session.finalised_mtime_ns == session.path.stat().st_mtime_ns
        ):
            report("The previously validated hardware-ready pair is prepared", 1, 1)
            return session.path
        total = 5 if is_hardfile else 2
        report("Applying the selected hardware profile", 0, total)
        # A paired HDA receives the same directory validation immediately
        # below. Avoid traversing a large directory tree twice during save.
        if not is_hardfile:
            self._apply_target_hardware(session)
        if is_hardfile:
            report("Checking HDA size against the GEO geometry", 1, total)
            self._normalise_hardfile_dat_size(session)
            report("Checking directory block checksums and the bitmap", 2, total)
            repairs = self._finalise_hardfile_directories(session)
            if repairs:
                self._append_warning(
                    session,
                    f"Repaired {repairs} directory block checksum"
                    f"{'s' if repairs != 1 else ''} and refreshed the volume bitmap.",
                )
            report("Restamping the volume so a machine re-reads it", 3, total)
            if self._advance_hardfile_disc_id(session):
                self._append_warning(
                    session,
                    "Advanced the volume datestamp and rebuilt its root checksum so "
                    "a machine that already cached the volume notices the edit.",
                )
            report("Validating the final HDA and GEO pair", 4, total)
            self._validate_created_hardfile_pair(session)
            self._optimise_sparse_file(session.path)
            report("The hardware-ready pair is prepared", total, total)
        if session.hfe_original_path:
            report("Encoding and verifying the HFE image", 1, total)
            output = self._prepare_hfe_download(session)
            report("The hardware-ready image is prepared", total, total)
            return output
        if session.scp_original_path:
            report("Encoding and verifying the SCP flux image", 1, total)
            output = self._prepare_scp_download(session)
            report("The hardware-ready image is prepared", total, total)
            return output
        if not is_hardfile:
            report("The hardware-ready image is prepared", total, total)
        return session.path

    def _prepare_hfe_download(self, session: ImageSession) -> Path:
        return self._prepare_flux_download(session, HFE)

    def _prepare_scp_download(self, session: ImageSession) -> Path:
        return self._prepare_flux_download(session, SCP)

    def _prepare_flux_download(
        self,
        session: ImageSession,
        container: FluxContainer,
    ) -> Path:
        """Re-encode an edited flux image, or hand back the untouched original.

        Both containers follow the same rule: an unedited session downloads the
        bytes it was opened from, and an edited one is only released after the
        new container decodes back to exactly the sectors on screen.
        """
        original = getattr(session, f"{container.identifier}_original_path")
        export_attribute = f"{container.identifier}_export_path"
        if not session.dirty:
            existing = getattr(session, export_attribute)
            if existing and existing.is_file():
                return existing
            return original
        self.require_writable_geometry(session)
        output = session.path.parent / (
            f"{Path(session.name).stem}-edited{container.extension}"
        )
        self._flux.encode_and_verify(
            session.path,
            container,
            output,
            kind=session.kind,
            reference=original,
            failure_message=(
                f"The edited sectors did not survive {container.display} encoding "
                f"exactly, so the original {container.display} was left unchanged."
            ),
        )
        setattr(session, export_attribute, output)
        return output

    @staticmethod
    def is_bare_hard_drive(session: ImageSession, size: int | None = None) -> bool:
        """Whether this is one hard-drive-sized volume with no partition table.

        A ``.hdf`` carrying a Rigid Disk Block opens as ``kind == "hdf"`` and is
        never this. What this recognises is the bare hardfile: a single volume
        too large to be a floppy, which the host has to be told the geometry
        for because the file itself declares none.
        """
        if session.kind not in {"ffs", "ofs"}:
            return False
        if session.descriptor_path or session.path.suffix.lower() in {".hdf", ".hda", ".geo"}:
            return True
        measured = session.path.stat().st_size if size is None else int(size)
        return measured > 2 * 1024 * 1024

    def export_formats(self, session: ImageSession) -> list[dict]:
        """List container formats this image's decoded sectors can be exported as.

        Export is independent of how the image was opened: an OFS/FFS image
        can always be exported back to its canonical raw sector extension, and
        additionally wrapped as HFE or SCP flux when HxCFE has a known blank
        layout for its geometry, which is the double- and high-density
        3.5-inch floppy.
        """
        if session.kind not in BROWSEABLE_KINDS | {"hdf"} or session.descriptor_path is not None:
            return []
        size = session.path.stat().st_size
        native_extension = sector_image_suffix(session.kind, size).lstrip(".")
        formats = [{
            "format": "native",
            "extension": native_extension,
            "label": f"Native sector image (.{native_extension})",
        }]
        # A hard drive can be written either way round. Which conversion is
        # offered depends on which form it is in now, because converting a
        # drive to the shape it already has is not a conversion.
        if session.kind == "hdf":
            formats.append({
                "format": "hardfile",
                "extension": "zip",
                "label": "Bare hardfile and geometry sidecar (.hdf + .geo)",
            })
        elif self.is_bare_hard_drive(session, size):
            formats.append({
                "format": "rdb",
                "extension": "hdf",
                "label": "Partitioned drive with a Rigid Disk Block (.hdf)",
            })
        if size in FLOPPY_SIZES:
            # An ADZ is the same sector image gzipped, which is how Amiga
            # floppies are usually distributed.
            formats.append({
                "format": "adz",
                "extension": "adz",
                "label": "Gzip-compressed sector image (.adz)",
            })
        if is_flux_encodable(session.kind, size):
            formats.extend(
                {
                    "format": container.identifier,
                    "extension": container.extension.lstrip("."),
                    "label": container.label,
                }
                for container in FLUX_CONTAINERS.values()
            )
        return formats

    def export_image(self, session: ImageSession, target_format: str) -> tuple[Path, str]:
        """Convert this image's current decoded sectors to another compatible container."""
        with session.lock:
            available = {entry["format"] for entry in self.export_formats(session)}
            if target_format not in available:
                raise DiskError(f"“{target_format}” is not an available export format for this image.")
            stem = self.safe_filename(Path(session.name).stem) or "image"
            size = session.path.stat().st_size
            if target_format == "native":
                extension = sector_image_suffix(session.kind, size).lstrip(".")
                output = session.path.parent / f"{stem}-export.{extension}"
                shutil.copyfile(session.path, output)
                return output, output.name
            if target_format == "rdb":
                return self._export_with_rigid_disk(session, stem)
            if target_format == "hardfile":
                return self._export_bare_hardfile(session, stem)
            if target_format == "adz":
                output = session.path.parent / f"{stem}-export.adz"
                output.unlink(missing_ok=True)
                # No timestamp or original name is stored, so the same disk
                # always compresses to the same bytes and two exports can be
                # compared directly.
                with session.path.open("rb") as sectors, output.open("wb") as raw:
                    with gzip.GzipFile(
                        filename="", mode="wb", fileobj=raw, mtime=0
                    ) as compressed:
                        shutil.copyfileobj(sectors, compressed)
                return output, output.name
            container = FLUX_CONTAINERS[target_format]
            output = session.path.parent / f"{stem}-export{container.extension}"
            self._flux.encode_and_verify(
                session.path,
                container,
                output,
                kind=session.kind,
                failure_message=(
                    f"The exported {container.display} image did not decode back to "
                    "identical sectors, so the export was discarded."
                ),
            )
            return output, output.name

    def _export_with_rigid_disk(self, session: ImageSession, stem: str) -> tuple[Path, str]:
        """Wrap a bare volume in a Rigid Disk Block so a drive describes itself.

        A hardfile holds one volume and nothing else, so the machine reading it
        has to be told the geometry. Giving it an RDB puts that description
        inside the file, which is what lets `HDToolBox` and an emulator mount
        it without being configured first.

        The volume's own bytes are copied across unchanged. What the export
        adds is the reserved cylinder in front of them, so the result is larger
        than the source by exactly that much.
        """
        try:
            from amiganut.filesystem.blocks import BlockReader
            from amiganut.filesystem.rdb import write_rigid_disk
        except ImportError as exc:  # pragma: no cover - packaging failure
            raise DiskError("The Amiganut Rigid Disk Block API is unavailable.") from exc

        source_size = session.path.stat().st_size
        volume_blocks = source_size // HARDFILE_SECTOR_SIZE
        if volume_blocks < 2:
            raise DiskError("This image is too small to describe as a hard drive.")
        heads, sectors = 16, 63
        blocks_per_cylinder = heads * sectors
        # One cylinder for the RDB itself, then whole cylinders for the volume.
        partition_cylinders = max(1, -(-volume_blocks // blocks_per_cylinder))
        total_blocks = (1 + partition_cylinders) * blocks_per_cylinder

        output = session.path.parent / f"{stem}-export.hdf"
        output.unlink(missing_ok=True)
        with output.open("wb") as target:
            target.truncate(total_blocks * HARDFILE_SECTOR_SIZE)
        dos_type = session.path.read_bytes()[:4] if source_size >= 4 else b"DOS\x03"
        if not dos_type.startswith(b"DOS"):
            dos_type = b"DOS\x03"
        reader = BlockReader(output, writable=True)
        try:
            disk = write_rigid_disk(
                reader,
                [{
                    "name": self._rdb_device_name(session),
                    "dosType": dos_type,
                    "cylinders": partition_cylinders,
                    "bootable": True,
                    "bootPriority": 0,
                }],
                heads=heads,
                sectors=sectors,
            )
            partition = disk.partitions[0]
            with session.path.open("rb") as volume:
                for index in range(volume_blocks):
                    block = volume.read(HARDFILE_SECTOR_SIZE)
                    if len(block) < HARDFILE_SECTOR_SIZE:
                        block = block.ljust(HARDFILE_SECTOR_SIZE, b"\x00")
                    reader.write_block(partition.start_block + index, block)
        except DiskError:
            raise
        except Exception as exc:
            output.unlink(missing_ok=True)
            raise DiskError(self._friendly_engine_error(str(exc))) from exc
        finally:
            reader.close()
        return output, output.name

    def _export_bare_hardfile(self, session: ImageSession, stem: str) -> tuple[Path, str]:
        """Lift one partition out of a drive as a hardfile and its sidecar.

        The partition's blocks are copied out verbatim. The geometry the RDB
        declared for it is written beside them as a ``.geo``, because once the
        partition table is gone that description has nowhere else to live, and
        the two files are only usable together.
        """
        try:
            from amiganut.filesystem.blocks import BlockReader
            from amiganut.filesystem.rdb import read_rigid_disk
        except ImportError as exc:  # pragma: no cover - packaging failure
            raise DiskError("The Amiganut Rigid Disk Block API is unavailable.") from exc
        from .hardfile_geometry import format_geometry

        index = self.selected_partition(session)
        reader = BlockReader(session.path, writable=False)
        try:
            disk = read_rigid_disk(reader)
            if not disk.partitions:
                raise DiskError("This drive declares no partitions to export.")
            if index >= len(disk.partitions):
                index = 0
            partition = disk.partitions[index]
            device = str(partition.name or f"DH{index}")
            data_path = session.path.parent / f"{stem}-{self.safe_filename(device)}.hdf"
            data_path.unlink(missing_ok=True)
            with data_path.open("wb") as target:
                for offset in range(partition.total_blocks):
                    target.write(reader.read_block(partition.start_block + offset))
        except DiskError:
            raise
        except Exception as exc:
            raise DiskError(self._friendly_engine_error(str(exc))) from exc
        finally:
            reader.close()

        cylinders = partition.high_cylinder - partition.low_cylinder + 1
        descriptor = format_geometry(
            surfaces=partition.surfaces,
            blocks_per_track=partition.blocks_per_track,
            cylinders=cylinders,
            block_size=partition.block_size,
        )
        archive_path = session.path.parent / f"{stem}-hardfile.zip"
        archive_path.unlink(missing_ok=True)
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(data_path, f"Hardfile0/{data_path.name}")
            archive.writestr(f"Hardfile0/{data_path.stem}.geo", descriptor)
        data_path.unlink(missing_ok=True)
        return archive_path, archive_path.name

    @staticmethod
    def _rdb_device_name(session: ImageSession) -> str:
        """A legal RDB device name for a volume that never had one."""
        candidate = re.sub(r"[^A-Za-z0-9]", "", Path(session.name).stem).upper()[:30]
        return candidate or "DH0"

    def mark_saved(self, session: ImageSession) -> None:
        """Record that the current working bytes have been prepared for download."""
        with session.lock:
            session.dirty = False
            self._persist_session(session)

    @staticmethod
    def _copy_stream(stream: BinaryIO, target: Path) -> None:
        """Use an in-kernel copy for spooled uploads, with a portable fallback."""
        seekable = getattr(stream, "seekable", lambda: False)()
        start = stream.tell() if seekable else None
        with target.open("wb") as output:
            try:
                source_fd = stream.fileno()
                while os.sendfile(
                    output.fileno(),
                    source_fd,
                    None,
                    COPY_BUFFER_SIZE,
                ):
                    pass
                return
            except (AttributeError, io.UnsupportedOperation, OSError):
                output.seek(0)
                output.truncate()
                if start is not None:
                    stream.seek(start)
                shutil.copyfileobj(stream, output, length=COPY_BUFFER_SIZE)

    @staticmethod
    def _copy_local_file(source: Path, target: Path) -> None:
        """Clone or sparsely copy a local image without allocating zero ranges."""
        try:
            import fcntl

            with source.open("rb") as source_file, target.open("wb") as target_file:
                fcntl.ioctl(target_file.fileno(), FICLONE, source_file.fileno())
            return
        except OSError:
            target.unlink(missing_ok=True)
        try:
            subprocess.run(
                [
                    "cp",
                    "--reflink=auto",
                    "--sparse=always",
                    "--",
                    str(source),
                    str(target),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except (OSError, subprocess.CalledProcessError):
            target.unlink(missing_ok=True)
        shutil.copyfile(source, target)

    def preview_image_contents(
        self,
        session: ImageSession,
        limit: int = 500,
    ) -> dict:
        """Return a bounded, read-only preview suitable for an import plan."""
        limit = max(1, min(int(limit), 1000))
        if session.kind == "rom":
            rows = self.list_rom_banks(session)
            return {
                "entries": [{
                    "path": f"Bank {row['bank']:03d}",
                    "name": row["name"],
                    "type": "ROM bank",
                    "size": row["length"],
                    "detail": row["filetype"],
                } for row in rows[:limit]],
                "total": len(rows),
                "truncated": len(rows) > limit,
                "summary": f"{len(rows)} ROM bank(s) of {session.rom_bank_size:,} bytes",
            }
        if session.kind == "kickfs":
            listing = self.list_directory(session, "")
            rows = listing["entries"]
            return {
                "entries": [{
                    "path": row["path"],
                    "name": row["name"],
                    "type": "Kickstart ROM file",
                    "size": row["length"],
                    "detail": f"{format_protection(row['protection'])} · {row['attr']}",
                } for row in rows[:limit]],
                "total": len(rows),
                "truncated": len(rows) > limit,
                "summary": f"{len(rows)} file(s) in {listing['title']} Kickstart ROM",
            }
        if session.kind == "dms":
            dms = self._dms(session)
            entries = [
                {
                    "path": "$",
                    "name": item.name,
                    "type": "file",
                    "size": len(item.data),
                    "detail": "complete" if item.complete else "incomplete",
                }
                for item in dms.files[:limit]
            ]
            return {
                "entries": entries,
                "total": len(dms.files),
                "truncated": len(dms.files) > limit,
                "summary": f"{len(dms.files)} reconstructed DMS track(s)",
            }

        entries: list[dict] = []
        pending: list[tuple[str, int | None]] = [("", None)]
        visited: set[tuple[str, int | None]] = set()
        truncated = False
        while pending:
            path, side = pending.pop(0)
            identity = (path.casefold(), side)
            if identity in visited:
                continue
            visited.add(identity)
            listing = self.list_directory(session, path, side)
            prefix = f"Side {side}" if side is not None else path
            for row in listing["entries"]:
                if len(entries) >= limit:
                    truncated = True
                    break
                name = str(row.get("name") or "Untitled")
                item_path = (
                    amiga_paths.join(path, name)
                )
                entries.append({
                    "path": prefix,
                    "name": name,
                    "type": row.get("type", "file"),
                    "size": row.get("size"),
                    "detail": (
                        f"load {row.get('loadHex')} · exec {row.get('executeHex')}"
                        if row.get("loadHex") or row.get("executeHex")
                        else ""
                    ),
                })
                if session.kind in {"ffs", "ofs"} and row.get("type") == "dir":
                    pending.append((item_path, None))
            if truncated:
                break
        return {
            "entries": entries,
            "total": len(entries),
            "truncated": truncated or bool(pending),
            "summary": f"{len(entries)} visible object(s)" + (" or more" if truncated else ""),
        }

    def create_blank(
        self,
        format_name: str,
        title: str,
        capacity: str | None = None,
        target_hardware: str = "auto",
        options: dict | None = None,
    ) -> ImageSession:
        hfe_formats = {
            "hfe-adf": "adf",
            "hfe-adf-hd": "adf-hd",
            "hfe-ffs": "ffs",
            "hfe-ffs-intl": "ffs-intl",
            "hfe-ffs-dc": "ffs-dc",
            "hfe-ffs-hd": "ffs-hd",
        }
        target_hardware = self._blank_target_hardware(
            format_name,
            target_hardware,
        )
        native_format = hfe_formats.get(format_name, format_name)
        # ``ofs`` names the filing system rather than a format, and ``adz`` is
        # a compressed ADF; both are accepted as spellings of a plain
        # double-density OFS floppy.
        native_format = {"ofs": "adf", "adz": "adf"}.get(native_format, native_format)
        # Every floppy variant is the same 880 KiB or 1.76 MiB of blocks; only
        # the DOS type in the boot block differs, which is exactly how a real
        # machine distinguishes them.
        formats = {
            "adf": ("blank.adf", ["--variant", "OFS", "--geometry", "dd"]),
            "adf-intl": ("blank.adf", ["--variant", "OFS-INTL", "--geometry", "dd"]),
            "adf-dc": ("blank.adf", ["--variant", "OFS-DC", "--geometry", "dd"]),
            "adf-hd": ("blank.adf", ["--variant", "OFS-INTL", "--geometry", "hd"]),
            "ffs": ("blank.adf", ["--variant", "FFS", "--geometry", "dd"]),
            "ffs-intl": ("blank.adf", ["--variant", "FFS-INTL", "--geometry", "dd"]),
            "ffs-dc": ("blank.adf", ["--variant", "FFS-DC", "--geometry", "dd"]),
            "ffs-hd": ("blank.adf", ["--variant", "FFS-INTL", "--geometry", "hd"]),
            "ffs-hd-dc": ("blank.adf", ["--variant", "FFS-DC", "--geometry", "hd"]),
            "hardfile": (
                "hardfile.hdf",
                [
                    "--variant", "FFS-INTL",
                    "--geometry", f"capacity={capacity or '20MB'}",
                    "--geometry-sidecar",
                ],
            ),
            "ffs-hard": (
                "HardDisk.hdf",
                [
                    "--filesystem", "rdb",
                    "--partitions", "1",
                    "--variant", "FFS-INTL",
                    "--geometry", f"capacity={capacity or '100MB'}",
                ],
            ),
            "ffs-physical": (
                "physical-drive.raw",
                ["--variant", "FFS-INTL", "--geometry", f"capacity={capacity or '100MB'}"],
            ),
        }
        if native_format == "kickfs":
            options = options or {}
            # ``capacity`` is the pane's floppy or drive size field. It only
            # applies to a ROM when it names a ROM size, so anything else
            # falls through to the default rather than failing the create.
            requested_capacity = str(capacity or "").strip().lower().replace("ib", "").replace(" ", "")
            if requested_capacity not in {"256k", "512k", "1m", "262144", "524288", "1048576", "1024k"}:
                requested_capacity = ""
            geometry = (
                str(options.get("geometry") or requested_capacity or "256k")
                .lower()
                .replace("ib", "")
                .replace(" ", "")
            )
            geometry = {
                "256k": "256k", "262144": "256k",
                "512k": "512k", "524288": "512k",
                "1m": "1m", "1024k": "1m", "1048576": "1m",
            }.get(geometry, geometry)
            if geometry not in {"256k", "512k", "1m"}:
                raise DiskError("A ROM image is 256 KiB, 512 KiB or 1 MiB.")
            kickfs_title = str(title or "FORGE").strip()
            if not kickfs_title or len(kickfs_title) > 20:
                raise DiskError("A created ROM title can contain 1 to 20 characters.")
            copyright_text = str(
                options.get("copyright") or f"{kickfs_title}.library 1.0 (2026)"
            ).strip()
            if not copyright_text:
                raise DiskError("A resident identification string cannot be empty.")
            if len(copyright_text) > 120:
                raise DiskError("A resident identification string can hold at most 120 characters.")
            try:
                version = int(str(options.get("version", 1)), 0)
            except ValueError as exc:
                raise DiskError("A ROM version must be from 0 to 65535.") from exc
            if not 0 <= version <= 0xFFFF:
                raise DiskError("A ROM version must be from 0 to 65535.")
            image_id = uuid.uuid4().hex
            folder = self.work_dir / image_id
            folder.mkdir()
            path = folder / f"{self.safe_filename(kickfs_title) or 'forge'}.rom"
            try:
                self._run([
                    "create", "--filesystem", "kickfs", "--geometry", geometry,
                    "--title", kickfs_title, str(path),
                ])
                from amiganut.kickfs.kickfs import set_copyright, set_version
                data = set_version(path.read_bytes(), version)
                data = set_copyright(data, copyright_text)
                path.write_bytes(data)
                session = ImageSession(
                    image_id, path.name, "kickfs", path, dirty=True,
                    target_hardware=self._target_hardware(target_hardware),
                )
            except Exception as exc:
                shutil.rmtree(folder, ignore_errors=True)
                if isinstance(exc, DiskError):
                    raise
                raise DiskError(f"The ROM image could not be created: {exc}") from exc
        elif native_format == "rom":
            options = options or {}
            try:
                bank_size = validate_bank_size(int(options.get("bankSize", DEFAULT_BANK_SIZE)))
                total_size = int(options.get("totalSize", bank_size))
            except (TypeError, ValueError, RomError) as exc:
                raise DiskError(str(exc) or "Choose valid ROM dimensions.") from exc
            if total_size < 1 or total_size > MAX_ROM_SIZE:
                raise DiskError("ROM images must contain between 1 byte and 64 MiB.")
            erase_byte = int(options.get("eraseByte", 0xFF)) & 0xFF
            image_id = uuid.uuid4().hex
            folder = self.work_dir / image_id
            folder.mkdir()
            path = folder / f"{self.safe_filename(title) or 'blank'}.rom"
            try:
                template = str(options.get("template") or "blank")
                first = (
                    make_expansion_rom(bank_size, title, erase_byte)
                    if template == "kickstart"
                    else bytes((erase_byte,)) * min(bank_size, total_size)
                )
                with path.open("wb") as image:
                    image.write(first[:total_size])
                    if total_size > len(first):
                        chunk = bytes((erase_byte,)) * min(COPY_BUFFER_SIZE, total_size - len(first))
                        remaining = total_size - len(first)
                        while remaining:
                            part = chunk[:remaining]
                            image.write(part)
                            remaining -= len(part)
                session = ImageSession(
                    image_id, path.name, "rom", path, dirty=True,
                    rom_bank_size=bank_size,
                    rom_erase_byte=erase_byte,
                    rom_platform=validate_platform(options.get("platform")),
                    rom_layout=validate_layout(options.get("layout")),
                    rom_component_names=[
                        self.safe_filename(name)
                        for name in options.get("componentNames", [])
                        if name
                    ],
                )
            except Exception:
                shutil.rmtree(folder, ignore_errors=True)
                raise
        else:
            try:
                filename, extra = formats[native_format]
            except KeyError as exc:
                raise DiskError("Unknown blank image format.") from exc
            image_id = uuid.uuid4().hex
            folder = self.work_dir / image_id
            folder.mkdir()
            path = folder / filename
            try:
                self._run(["create", *extra, "--title", title[:30], str(path)])
                # The engine writes the sidecar as ``name.hda.geo``, which is
                # the spelling every emulator looks for. The bare
                # ``name.geo`` form is accepted too, for images prepared by
                # hand.
                generated_descriptor = Path(str(path) + ".geo")
                if not generated_descriptor.is_file():
                    generated_descriptor = path.with_suffix(".geo")
                # ``.hdf`` is what every Amiga emulator calls a hard-drive
                # file, whether or not it carries a partition table, so both
                # kinds of drive are written under that extension. A drive is
                # told from a bare hardfile by reading its Rigid Disk Block,
                # not by its name.
                output_names = {
                    "ffs-hard": "HardDrive.hdf",
                    "ffs-physical": "physical-drive.raw",
                }
                if native_format in output_names:
                    output_path = folder / output_names[native_format]
                    path.replace(output_path)
                    path = output_path
                    generated_descriptor.unlink(missing_ok=True)
                descriptor_path = (
                    generated_descriptor
                    if native_format == "hardfile" and generated_descriptor.is_file()
                    else None
                )
                if native_format == "hardfile" and descriptor_path is None:
                    raise DiskError(
                        "The disk engine did not create the Hardfile GEO descriptor."
                    )
                session = ImageSession(
                    image_id,
                    path.name,
                    self.identify_kind(path, self.detect_kind(path.name)),
                    path,
                    descriptor_name=descriptor_path.name if descriptor_path else None,
                    descriptor_path=descriptor_path,
                    dirty=True,
                    target_hardware=self._target_hardware(target_hardware),
                )
                self._normalise_hardfile_dat_size(session)
                if native_format == "hardfile":
                    self._canonicalise_created_hardfile_root(session, title[:12])
                    self._validate_created_hardfile_pair(session)
                self._apply_target_hardware(session)
                if native_format == "hardfile":
                    self._optimise_sparse_file(session.path)
                if format_name in hfe_formats:
                    original = folder / f"{self.safe_filename(title) or 'blank'}.hfe"
                    # The flux layout follows from the blank image's geometry,
                    # so creation uses the same rule as opening and saving.
                    self._flux.encode_from_sectors(
                        path, HFE, original, kind=session.kind
                    )
                    header = parse_hfe_header(original.read_bytes()[:512])
                    session.name = original.name
                    session.hfe_original_path = original
                    session.hfe_version = header.version
                    session.warnings.append(
                        f"Created an editable HFE {header.version} container around {path.suffix[1:].upper()}."
                    )
            except Exception:
                shutil.rmtree(folder, ignore_errors=True)
                raise
        if session.kind in {"ffs", "ofs"}:
            self.refresh_ffs_capabilities(session)
        with self._lock:
            self.sessions[session.id] = session
        self._persist_session(session)
        return session

    @staticmethod
    def _blank_target_hardware(
        format_name: str,
        requested: str | None,
    ) -> str:
        """Apply only target profiles that are meaningful for a new format."""
        forced = {
            "hardfile": "hardfile",
            "ffs-hard": "amigaos",
            "ffs-physical": "amigaos",
        }
        if format_name in forced:
            return forced[format_name]
        # Every floppy is the same disk on every Amiga, so the machine a new
        # one is meant for stays the user's choice.
        selectable_floppies = {
            "adf",
            "adf-intl",
            "adf-dc",
            "adf-hd",
            "ffs",
            "ffs-intl",
            "ffs-dc",
            "ffs-hd",
            "ffs-hd-dc",
            "hfe-adf",
            "hfe-adf-hd",
            "hfe-ffs",
            "hfe-ffs-intl",
            "hfe-ffs-dc",
            "hfe-ffs-hd",
        }
        # A floppy can be aimed at a Kickstart 1.3 machine or a Kickstart 3.x
        # one, which is what decides whether an FFS volume mounts without help.
        # The hard-drive profiles are not choices a floppy can make.
        floppy_profiles = {"auto", "a500-ofs", "a1200-ffs"}
        if format_name == "kickfs" or format_name in selectable_floppies:
            requested = str(requested or "auto")
            return requested if requested in floppy_profiles else "auto"
        return "auto"

    @staticmethod
    def _ofs_title(data: bytes) -> str:
        """Read a volume's disk name out of a raw ADF image."""
        from .ofs_compat import BLOCK_SIZE

        if len(data) < BLOCK_SIZE * 4:
            return ""
        total = len(data) // BLOCK_SIZE
        candidate = root_block_number(total)
        # A volume written by another tool may round the midpoint the other
        # way, so its neighbours are tried before giving up on the name.
        for block in (candidate, candidate - 1, candidate + 1):
            if not 0 <= block < total:
                continue
            offset = block * BLOCK_SIZE
            root = data[offset : offset + BLOCK_SIZE]
            if len(root) != BLOCK_SIZE:
                continue
            if int.from_bytes(root[0:4], "big") != 2:
                continue
            if int.from_bytes(root[BLOCK_SIZE - 4 :], "big", signed=True) != 1:
                continue
            name_offset = BLOCK_SIZE - 80
            length = min(root[name_offset], 30)
            return (
                root[name_offset + 1 : name_offset + 1 + length]
                .decode("latin-1", "replace")
                .strip()
            )
        return ""

    def set_ffs_source_name(
        self,
        session: ImageSession,
        path: str,
        source_name: str,
    ) -> None:
        session.ffs_source_names[str(path)] = str(source_name).replace(
            "\\",
            "/",
        )[-500:]
        self._persist_session(session)

    def set_distribution_name(
        self,
        session: ImageSession,
        source_name: str,
    ) -> None:
        session.distribution_name = str(source_name).replace("\\", "/")[-500:]
        self._persist_session(session)

    def _mark_mutated(self, session: ImageSession) -> None:
        """Record that this image has been edited since it was opened."""
        session.dirty = True
        session.hfe_export_path = None
        session.content_kind_cache.clear()

    def resolve(self, session: ImageSession) -> Path:
        """Return the working file the engine should be pointed at."""
        return session.path

    @staticmethod
    def inner_for(session: ImageSession, inner: str, side: int | None) -> str:
        if session.kind == "kickfs":
            return "" if inner in {"", "$"} else inner
        if not session.path.name.lower().endswith(".adz"):
            return inner
        drive = 2 if side == 2 else 0
        if inner == "":
            return f":{drive}"
        if inner in amiga_paths.ROOT_TOKENS:
            return f":{drive}.$"
        return f":{drive}.{inner}"

    @staticmethod
    def compound(path: Path, inner: str | None = None) -> str:
        return f"{path}:{inner}" if inner is not None else str(path)

    @staticmethod
    def _capacity_from_mount(mount) -> dict:
        try:
            total = max(0, int(mount.size_bytes()))
            free = min(total, max(0, int(mount.free_bytes())))
        except (AttributeError, TypeError, ValueError):
            return {
                "available": False,
                "reason": "This filesystem does not report free-space capacity.",
            }
        return {
            "available": total > 0,
            "unit": "bytes",
            "total": total,
            "used": total - free,
            "free": free,
        }

    def _listing_content_kind(
        self,
        session: ImageSession,
        side: int | None,
        path: str,
        row: dict,
        reader: Callable[[], bytes],
    ) -> str | None:
        """Classify one listed file without remounting or reading large payloads."""
        hint = metadata_kind(str(row.get("name") or ""), row.get("filetype"))
        if hint:
            return hint
        length = int(row.get("length") or 0)
        if length <= 0 or length > LISTING_SNIFF_LIMIT:
            return None
        key = (
            side, str(path).casefold(), length,
            int(row.get("protection") or 0), str(row.get("filetype") or ""),
        )
        cached = session.content_kind_cache.get(key)
        if cached:
            return cached
        try:
            kind = analyse_content(reader(), path)[0]
        except Exception:
            # A damaged or unusually encoded file must not prevent its parent
            # directory from being listed. It can still be inspected on open.
            return None
        session.content_kind_cache[key] = kind
        return kind

    def partition_index(self, session: ImageSession) -> dict:
        """List a hard drive's partitions in the same shape as a directory.

        A drive that has not had a partition chosen shows its partition table,
        which is what a machine sees before it mounts anything. Each row is
        presented as a drawer so the pane can be opened into exactly as a
        directory is, and carries the device name, filing system and boot flag
        the RDB declares.
        """
        partitions = self.list_partitions(session)
        rows = [
            {
                "name": str(partition.get("device") or partition.get("name") or f"Partition {index}"),
                "type": "dir",
                "protection": 0,
                "comment": "",
                "filetype": "",
                "datestamp": "",
                "length": int(partition.get("sizeBytes") or 0),
                "attr": "bootable" if partition.get("bootable") else "",
                "format": str(partition.get("format") or ""),
                "bootable": bool(partition.get("bootable")),
                "partition": index,
            }
            for index, partition in enumerate(partitions)
        ]
        return {
            "entries": rows,
            "title": session.name,
            "description": (
                f"{len(rows)} RDB partition{'s' if len(rows) != 1 else ''}"
            ),
            "path": "",
        }

    def _list_ffs_mount(self, mount, inner: str, session: ImageSession) -> dict:
        """Return the same stable row schema as ``disc ls --as json``."""
        try:
            from amiganut.file import format_access_text
            from amiganut.filesystem import AmigaMetadata, Datestamped
        except ImportError as exc:
            raise DiskError("The Amiganut FFS listing API is unavailable.") from exc

        target = inner or ""
        if not mount.exists(target):
            raise DiskError(f"Path not found: {target}")
        if not mount.stat(target).is_dir:
            raise DiskError(f"{target} is not a directory.")

        rows: list[dict] = []
        for child in sorted(mount.iter_entries(target), key=lambda entry: natural_name_key(entry.name)):
            # A drawer carries the same header fields a file does, so both
            # report their protection bits, comment and datestamp.
            protection = 0
            attr = ""
            comment = ""
            datestamp = ""
            if isinstance(mount, AmigaMetadata):
                metadata = mount.amiga_meta(child.path)
                protection = int(metadata.protection or 0)
                comment = str(metadata.comment or "")
                if metadata.access is not None:
                    attr = format_access_text(metadata.access)
            if isinstance(mount, Datestamped):
                value = mount.datestamp(child.path)
                if value is not None:
                    datestamp = value.isoformat(sep="T", timespec="milliseconds")
            if child.is_dir:
                rows.append({
                    "name": child.name,
                    "type": "dir",
                    "protection": protection,
                    "comment": comment,
                    "datestamp": datestamp,
                    "length": sum(1 for _entry in mount.iter_entries(child.path)),
                    "attr": attr,
                })
                continue

            row = {
                "name": child.name,
                "type": "file",
                "protection": protection,
                "comment": comment,
                "datestamp": datestamp,
                "length": int(child.length),
                "attr": attr,
            }
            content_kind = self._listing_content_kind(
                session, None, str(child.path), row,
                lambda child_path=str(child.path): mount.read_bytes(child_path),
            )
            if content_kind:
                row["contentKind"] = content_kind
            rows.append(row)

        capacity = DiskService._capacity_from_mount(mount)
        free = capacity.get("free")
        return {
            "entries": rows,
            "title": str(getattr(mount, "title", "") or session.name),
            "description": f"Free: {free:,} bytes" if isinstance(free, int) else "",
            "path": target,
            "capacity": capacity,
        }

    def browse_directory(
        self,
        session: ImageSession,
        inner: str,
        side: int | None = None,
    ) -> dict:
        """List one directory and return its capacity without a second mount."""
        if session.kind == "rom":
            listing = self.list_directory(session, "")
            listing["capacity"] = self.capacity(session)
            return listing
        if session.kind == "kickfs":
            listing = self.list_directory(session, "")
            listing["capacity"] = self.capacity(session)
            return listing
        if self.mountable(session):
            with self.ffs_mount(session) as mount:
                return self._list_ffs_mount(mount, amiga_paths.normalise(inner or ""), session)
        listing = self.list_directory(session, inner, side)
        listing["capacity"] = self.capacity(session)
        return listing

    def list_directory(self, session: ImageSession, inner: str, side: int | None = None) -> dict:
        if session.kind == "rom":
            if inner not in {"", "$"}:
                raise DiskError("ROM images contain banks, not directories.")
            rows = self.list_rom_banks(session)
            partial = session.path.stat().st_size % session.rom_bank_size
            description = (
                f"{len(rows)} bank{'s' if len(rows) != 1 else ''} × {session.rom_bank_size:,} bytes"
                + (f" · final bank has {partial:,} bytes" if partial else "")
            )
            return {"entries": rows, "title": session.name, "description": description, "path": "$"}
        if session.kind == "kickfs":
            if inner not in {"", "$"}:
                raise DiskError("Kickstart ROM is flat and does not contain directories.")
            rows = []
            with self.kickfs_mount(session) as mount:
                for entry in mount.iter_entries(""):
                    metadata = mount.amiga_meta(entry.name)
                    access = int(metadata.access or 0)
                    row = {
                        "name": entry.name,
                        "path": entry.name,
                        "type": "file",
                        "protection": int(metadata.protection or 0),
                        "comment": str(metadata.comment or ""),
                        "filetype": "",
                        "datestamp": "",
                        "length": int(entry.length or 0),
                        "attr": "RUN" if access & 0x40 else "LOAD",
                        "runOnly": bool(access & 0x40),
                    }
                    content_kind = self._listing_content_kind(
                        session, None, entry.name, row,
                        lambda name=entry.name: mount.read_bytes(name),
                    )
                    if content_kind:
                        row["contentKind"] = content_kind
                    rows.append(row)
                title = str(mount.title or session.name)
            details = self.kickfs_details(session)
            return {
                "entries": rows,
                "title": title,
                "description": (
                    f"Kickstart ROM {session.path.stat().st_size // 1024} KiB · "
                    f"{len(rows)} file{'s' if len(rows) != 1 else ''} · "
                    f"version {details['version']}"
                ),
                "path": "$",
            }
        if session.kind == "dms":
            dms = self._dms(session)
            if inner not in {"", "$"}:
                raise DiskError("DMS archives do not contain directories.")
            entries = []
            for item in dms.files:
                row = {
                    "name": item.name,
                    "type": "file",
                    # A DMS track carries no AmigaDOS metadata; what it does
                    # carry is DiskMasher's own pair of checksums.
                    "unpackedChecksum": item.unpacked_crc,
                    "packedChecksum": item.packed_crc,
                    "filetype": "",
                    "datestamp": "",
                    "length": len(item.data),
                    "attr": "R/" if item.complete else "R/?",
                    "blocks": item.blocks,
                    "complete": item.complete,
                }
                content_kind = metadata_kind(item.name, None) or analyse_content(item.data, item.name)[0]
                if content_kind:
                    row["contentKind"] = content_kind
                entries.append(row)
            return {
                "entries": entries,
                "title": session.name,
                "description": f"DMS {dms.version} · {len(dms.files)} DMS tracks",
                "path": "$",
            }
        if session.kind == "hdf" and session.partition is None:
            return self.partition_index(session)
        if self.mountable(session):
            with self.ffs_mount(session) as mount:
                return self._list_ffs_mount(mount, amiga_paths.normalise(inner or ""), session)
        disk_path = self.resolve(session)
        # An empty path is the volume root, on OFS exactly as on FFS: an
        # AmigaDOS root block holds a hash table whatever the DOS type is.
        requested_inner = "" if inner is None else inner
        resolved_inner = self.inner_for(session, requested_inner, side)
        result = self._run_json(["ls", "--as", "json", self.compound(disk_path, resolved_inner)])
        report = result["reports"]["entries"]
        rows = report["rows"]
        if session.kind == "ofs":
            rows = self._restore_ofs_catalogue_names(
                self.compound(disk_path, resolved_inner),
                rows,
                session,
                side,
            )
        return {
            "entries": rows,
            "title": report["metadata"].get("title", session.name),
            "description": report["metadata"].get("description", ""),
            "path": requested_inner,
        }

    @staticmethod
    def validate_ofs_prefix(prefix: str) -> str:
        """Validate and normalise a directory path inside an AmigaDOS volume.

        AmigaDOS drawers nest, so a destination is a full path rather than a
        single catalogue letter. Every component is checked against the same
        name policy that applies to a file, because a drawer that a real
        machine cannot name is no more useful than a file it cannot name.
        """
        path = amiga_paths.normalise(prefix)
        for part in amiga_paths.split(path):
            if len(part) > 30:
                raise DiskError(
                    f"“{part}” is longer than the 30 characters an Amiga name can hold."
                )
            if any(character in ":/\\" for character in part):
                raise DiskError("An Amiga name cannot contain : / or \\.")
        return path

    def move_ofs_items(
        self,
        session: ImageSession,
        items: list[dict],
        side: int | None = None,
    ) -> list[dict]:
        """Move files between drawers in one request."""
        if not self.mountable(session):
            raise DiskError("Drawer moves are available only inside a mounted volume.")
        if not isinstance(items, list) or not items:
            raise DiskError("Choose at least one file to move.")
        checked: list[dict] = []
        for item in items:
            source = amiga_paths.normalise(item.get("source"))
            destination = amiga_paths.normalise(item.get("destination"))
            if not source or not destination:
                raise DiskError("Both a source and a destination path are required.")
            self.validate_ofs_prefix(amiga_paths.parent(source))
            self.validate_ofs_prefix(amiga_paths.parent(destination))
            self.validate_leaf_name(session, amiga_paths.leaf(destination))
            checked.append({"source": source, "destination": destination})
        self.require_writable_geometry(session)
        with session.lock:
            disk_path = self.resolve(session)
            for item in checked:
                self._run([
                    "mv",
                    self.compound(
                        disk_path,
                        self.inner_for(session, item["source"], side),
                    ),
                    self.inner_for(session, item["destination"], side),
                ])
            self._mark_mutated(session)
        self.move_editor_projects(session, checked, side)
        return checked

    def list_ofs_catalogue_files(
        self,
        session: ImageSession,
        side: int | None = None,
    ) -> list[dict]:
        """Return every file from the populated OFS prefix groups.

        An OFS catalogue is flat even though its one-character prefixes are
        presented as folders in the workbench.  Asking ``disc ls`` to list
        the root and then starting it again for every prefix was particularly
        noticeable while importing a floppy into a large FFS image.  Mount
        the already-identified floppy once and walk that small catalogue in
        process instead.
        """
        if session.kind != "ofs":
            raise DiskError("Open an AmigaDOS image before listing its files.")
        try:
            from amiganut.disc.mount import resolve_mount
            from amiganut.file import format_access_text
            from amiganut.filesystem import AmigaMetadata
        except ImportError as exc:
            raise DiskError("The Amiganut OFS catalogue API is unavailable.") from exc

        disk_path = self.resolve(session)
        # Resolve the drive/catalogue root rather than ``$`` itself so OFS
        # exposes every one-character prefix, not just the default group.
        root = self.inner_for(session, "", side)
        files: list[dict] = []
        try:
            with session.lock, resolve_mount(self.compound(disk_path, root)) as resolved:
                mount = resolved.mount
                pending = [resolved.path]
                while pending:
                    directory = pending.pop()
                    for entry in mount.iter_entries(directory):
                        if entry.is_dir:
                            pending.append(str(entry.path))
                            continue
                        path = str(entry.path)
                        prefix = amiga_paths.parent(path)
                        protection = 0
                        comment = ""
                        attr = ""
                        if isinstance(mount, AmigaMetadata):
                            metadata = mount.amiga_meta(path)
                            protection = int(metadata.protection or 0)
                            comment = str(metadata.comment or "")
                            if metadata.access is not None:
                                attr = format_access_text(metadata.access)
                        files.append({
                            "name": entry.name,
                            "type": "file",
                            "protection": protection,
                            "comment": comment,
                            "filetype": "",
                            "datestamp": "",
                            "length": int(entry.length),
                            "attr": attr,
                            "prefix": prefix,
                            "path": path,
                        })
                        content_kind = self._listing_content_kind(
                            session, side, path, files[-1],
                            lambda path=path: mount.read_bytes(path),
                        )
                        if content_kind:
                            files[-1]["contentKind"] = content_kind
        except Exception:
            # Retain the command-backed path for unusual third-party variants
            # and for a useful engine error on damaged images. Drawers nest, so
            # this walks the whole volume rather than one level.
            files.clear()
            pending = [""]
            visited: set[str] = set()
            while pending:
                directory = pending.pop()
                if directory.casefold() in visited or len(files) > 100_000:
                    continue
                visited.add(directory.casefold())
                for row in self.list_directory(session, directory, side)["entries"]:
                    path = amiga_paths.join(directory, str(row["name"]))
                    if row.get("type") in {"dir", "directory"}:
                        pending.append(path)
                        continue
                    files.append({**row, "prefix": directory, "path": path})
        return files

    def _restore_ofs_catalogue_names(
        self,
        compound_path: str,
        rows: list[dict],
        session: ImageSession,
        side: int | None,
    ) -> list[dict]:
        """Restore literal dots and classify files in the same OFS mount."""
        try:
            from amiganut.disc.mount import resolve_mount

            with resolve_mount(compound_path) as resolved:
                mount = resolved.mount
                directory = resolved.path
                prefix = f"{directory}{amiga_paths.SEPARATOR}" if directory else ""
                names: dict[tuple[str, int, int, int], list[tuple[str, str]]] = {}
                for entry in mount.iter_entries(directory):
                    if entry.is_dir:
                        continue
                    path = str(entry.path)
                    literal_name = path[len(prefix) :] if path.startswith(prefix) else path
                    metadata = mount.amiga_meta(path)
                    key = (
                        amiga_paths.leaf(literal_name).casefold(),
                        int(metadata.protection or 0),
                        int(entry.length or 0),
                    )
                    names.setdefault(key, []).append((literal_name, path))

                restored = []
                for row in rows:
                    key = (
                        str(row.get("name", "")).casefold(),
                        int(row.get("protection") or 0),
                        int(row.get("length") or 0),
                    )
                    matches = names.get(key)
                    candidate = dict(row)
                    source_path = str(row.get("name") or "")
                    if matches:
                        literal_name, source_path = matches.pop(0)
                        candidate["name"] = literal_name
                    content_kind = self._listing_content_kind(
                        session, side, source_path, candidate,
                        lambda source_path=source_path: mount.read_bytes(source_path),
                    )
                    if content_kind:
                        candidate["contentKind"] = content_kind
                    restored.append(candidate)
                return restored
        except (AttributeError, ImportError, OSError, RuntimeError, ValueError):
            return rows

    def stat(self, session: ImageSession) -> dict:
        disk_path = self.resolve(session)
        return self._run_json(["stat", "--as", "json", str(disk_path)])

    def capacity(self, session: ImageSession) -> dict:
        """Return authoritative writable capacity for a pane-level filesystem."""
        if session.kind == "rom":
            rows = self.list_rom_banks(session)
            used = sum(not row["empty"] for row in rows)
            return {
                "available": True,
                "unit": "banks",
                "total": len(rows),
                "used": used,
                "free": len(rows) - used,
            }
        if session.kind == "dms":
            return {
                "available": False,
                "reason": "DMS images do not have a fixed free-space capacity.",
            }
        if session.kind == "kickfs":
            return self.kickfs_details(session)["capacity"]
        if session.kind == "hdf" and session.partition is None:
            partitions = self.list_partitions(session)
            allocated = sum(int(item.get("sizeBytes") or 0) for item in partitions)
            total = session.path.stat().st_size
            return {
                "available": True,
                "unit": "bytes",
                "total": total,
                "used": allocated,
                "free": max(0, total - allocated),
                "detail": (
                    f"{len(partitions)} partition{'s' if len(partitions) != 1 else ''}"
                ),
            }
        if self.mountable(session):
            with self.ffs_mount(session) as mount:
                return self._capacity_from_mount(mount)

        reports = self.stat(session).get("reports", {})
        rows = [
            row
            for report in reports.values()
            for row in report.get("rows", [])
            if isinstance(row, dict)
            and isinstance(row.get("size"), int)
            and isinstance(row.get("free"), int)
        ]
        if not rows:
            return {
                "available": False,
                "reason": "This filesystem does not report free-space capacity.",
            }
        total = sum(max(0, row["size"]) for row in rows)
        free = min(total, sum(max(0, row["free"]) for row in rows))
        return {
            "available": total > 0,
            "unit": "bytes",
            "total": total,
            "used": total - free,
            "free": free,
        }

    def validate(self, session: ImageSession) -> str:
        if session.kind == "rom":
            rows = self.list_rom_banks(session)
            recognised = sum(bool(row["header"]) for row in rows)
            partial = session.path.stat().st_size % session.rom_bank_size
            if partial:
                return (
                    f"ROM bytes are readable · {len(rows)} banks · {recognised} Amiga-family header(s) · "
                    f"final bank is partial ({partial:,} bytes)"
                )
            return f"ROM bytes are readable · {len(rows)} complete bank(s) · {recognised} Amiga-family header(s)"
        if session.kind == "dms":
            dms = self._dms(session)
            suffix = f" · {len(dms.warnings)} warning(s)" if dms.warnings else ""
            return f"Valid DMS {dms.version} · {len(dms.files)} reconstructed file(s){suffix}"
        if session.kind == "kickfs":
            details = self.kickfs_details(session)
            state = "plain and writable" if not details["readOnly"] else (
                "incomplete and read-only" if not details["complete"] else "composite and read-only"
            )
            return (
                f"Valid Kickstart ROM · all block CRCs passed · {details['fileCount']} file(s) · "
                f"{session.path.stat().st_size // 1024} KiB · {state}"
            )
        disk_path = self.resolve(session)
        self._run(["validate", str(disk_path)])
        return "No structural errors found"

    def mutate(self, session: ImageSession, args: list[str], side: int | None = None) -> None:
        if session.kind == "dms":
            raise DiskError("DMS archives are read-only; convert the DMS to ADF or ADZ before editing files.")
        self.require_writable_geometry(session)
        with session.lock:
            disk_path = self.resolve(session)
            expanded = []
            for part in args:
                if part.startswith("{image}:"):
                    inner = part[len("{image}:") :]
                    expanded.append(self.compound(disk_path, self.inner_for(session, inner, side)))
                else:
                    expanded.append(part.replace("{image}", str(disk_path)))
            self._run(expanded)
            self._mark_mutated(session)

    def make_directory(
        self,
        session: ImageSession,
        path: str,
        side: int | None = None,
    ) -> None:
        """Create one drawer without re-identifying the whole image.

        Every AmigaDOS volume nests drawers, OFS included, so this works
        wherever the workbench can mount a writable volume.
        """
        self.require_writable_geometry(session)
        if self.mountable(session):
            with self.ffs_mount(session) as mount:
                mount.make_directory(path, parents=True, exist_ok=False)
            self._mark_mutated(session)
            return
        try:
            from amiganut.disc.mount import resolve_mount
        except ImportError as exc:
            raise DiskError("The Amiganut directory API is unavailable.") from exc
        disk_path = self.resolve(session)
        with session.lock, resolve_mount(f"{disk_path}:", writable=True) as resolved:
            resolved.mount.make_directory(
                self.inner_for(session, path, side), parents=True, exist_ok=False
            )
            resolved.mount.flush()
        self._mark_mutated(session)

    def set_access(
        self,
        session: ImageSession,
        paths: list[str],
        writable: bool,
        side: int | None = None,
    ) -> list[str]:
        """Set Amiga access on several objects in one writable mount."""
        if session.kind == "dms":
            raise DiskError("DMS archives do not carry editable file access.")
        self.require_writable_geometry(session)
        targets = list(dict.fromkeys(str(path or "").strip() for path in paths))
        if not targets:
            raise DiskError("Choose at least one file or directory to update.")
        try:
            from amiganut.disc.mount import resolve_mount
            from amiganut.file import Access, AmigaMeta
            from amiganut.filesystem import AmigaMetadata
        except ImportError as exc:
            raise DiskError("The Amiganut access API is unavailable.") from exc

        if session.kind == "kickfs":
            with self.kickfs_mount(session, writable=True) as mount:
                original = session.path.read_bytes()
                try:
                    for target in targets:
                        if not mount.exists(target):
                            raise DiskError(f"“{target}” no longer exists.")
                    for target in targets:
                        meta = mount.amiga_meta(target)
                        current = Access(meta.access) if meta.access is not None else Access(0)
                        access = current & ~Access.X if writable else current | Access.X
                        mount.set_amiga_meta(
                            target,
                            AmigaMeta(
                                comment=meta.comment,
                                datestamp=meta.datestamp,
                                filetype=meta.filetype,
                                access=int(access),
                            ),
                        )
                except Exception:
                    session.path.write_bytes(original)
                    raise
            self._mark_mutated(session)
            return targets

        with session.lock:
            disk_path = self.resolve(session)
            root = self.compound(disk_path, self.inner_for(session, "", side))
            self.require_mounted_volume(session)
            mount_context = (
                self.ffs_mount(session)
                if self.mountable(session)
                else resolve_mount(root, writable=True)
            )
            with mount_context as opened:
                mount = opened if self.mountable(session) else opened.mount
                if not isinstance(mount, AmigaMetadata):
                    raise DiskError("This filesystem does not carry Amiga access bits.")
                resolved_targets = [self.inner_for(session, path, side) for path in targets]
                for target in resolved_targets:
                    if not mount.exists(target):
                        raise DiskError(f"“{target}” no longer exists.")
                for target in resolved_targets:
                    meta = mount.amiga_meta(target)
                    current = Access(meta.access) if meta.access is not None else Access(0)
                    access = current & ~Access.L if writable else current | Access.L
                    mount.set_amiga_meta(
                        target,
                        AmigaMeta(
                            comment=meta.comment,
                            datestamp=meta.datestamp,
                            filetype=meta.filetype,
                            access=int(access),
                        ),
                    )
            self._mark_mutated(session)
        return targets

    def set_file_metadata(
        self,
        session: ImageSession,
        path: str,
        protection: str,
        comment: str = "",
        side: int | None = None,
    ) -> dict:
        """Update an entry's protection bits and comment, leaving its data alone.

        These are the two things AmigaDOS lets a person change about a file
        without rewriting it. There is no load or execution address to change:
        an AmigaDOS load file carries its own relocation information, so where
        it goes in memory is decided when it is run.
        """
        if session.kind in {"rom", "dms"}:
            raise DiskError("This view does not contain editable file catalogue addresses.")
        self.require_writable_geometry(session)
        try:
            from amiganut.file import AmigaMeta
            from amiganut.filesystem import AmigaMetadata
        except ImportError as exc:
            raise DiskError("The Amiganut catalogue metadata API is unavailable.") from exc
        parsed_protection = self._protection_value(protection)
        new_comment = " ".join(str(comment or "").split())[:79]

        def update(mount, target: str) -> dict:
            if not isinstance(mount, AmigaMetadata):
                raise DiskError("This filesystem does not carry AmigaDOS protection bits.")
            if not mount.exists(target):
                raise DiskError(f"“{target}” no longer exists.")
            stat = mount.stat(target)
            if stat.is_dir:
                # A drawer has protection bits and a comment of its own, so
                # both are editable; only its length is meaningless.
                pass
            current = mount.amiga_meta(target)
            mount.set_amiga_meta(
                target,
                AmigaMeta(
                    protection=parsed_protection,
                    comment=new_comment,
                    datestamp=current.datestamp,
                ),
            )
            return {
                "protection": parsed_protection,
                "comment": new_comment,
                "datestamp": current.datestamp,
                "length": int(stat.length or 0),
            }

        if session.kind == "kickfs":
            with self.kickfs_mount(session, writable=True) as mount:
                metadata = update(mount, path)
            self._mark_mutated(session)
            return metadata

        try:
            from amiganut.disc.mount import resolve_mount
        except ImportError as exc:
            raise DiskError("The Amiganut filesystem mount API is unavailable.") from exc
        with session.lock:
            if self.mountable(session):
                with self.ffs_mount(session) as mount:
                    metadata = update(mount, path)
            else:
                disk_path = self.resolve(session)
                root = self.compound(disk_path, self.inner_for(session, "", side))
                with resolve_mount(root, writable=True) as resolved:
                    metadata = update(resolved.mount, self.inner_for(session, path, side))
            self._mark_mutated(session)
        return metadata

    @staticmethod
    @staticmethod
    def _protection_value(value: object) -> int:
        """Parse a protection long a person supplied.

        One rule everywhere a protection value arrives from a person: Amiga
        hexadecimal, with an optional ``&`` or ``0x`` prefix. An empty box is
        rejected rather than written as zero, because zero is itself a
        meaningful value (everything permitted) and silently choosing it would
        destroy the very metadata the editor exists to preserve.
        """
        letters = parse_protection(value)
        if letters is not None:
            return letters
        text = str(value or "").strip()
        if re.fullmatch(r"(?:&|0x)?[0-9a-fA-F]{1,8}", text):
            return int(re.sub(r"^(?:&|0x)", "", text, flags=re.IGNORECASE), 16)
        raise DiskError(
            "A protection value is either the eight letters List prints, such "
            "as ----rwed, or one to eight hexadecimal digits written &05 or 0x05."
        )

    def put(
        self,
        session: ImageSession,
        destination: str,
        host_path: Path,
        protection: str | None = None,
        comment: str | None = None,
        filetype: str | None = None,
        side: int | None = None,
    ) -> None:
        """Import one host file with the metadata AmigaDOS actually records.

        A host file arrives with no protection bits, comment or Workbench icon
        type of its own. Whatever the caller could establish -- from an ``.inf``
        sidecar, from an Amiga-written ZIP, or from the source volume in an
        image-to-image copy -- is applied here; anything it could not is left
        at the filing system's own default rather than invented.
        """
        if session.kind == "rom":
            self.put_rom_bank(session, host_path.read_bytes())
            return
        if session.kind == "dms":
            raise DiskError("Files cannot be added directly to a DMS archive.")
        self.require_writable_geometry(session)
        if session.kind == "kickfs":
            destination = self.validate_leaf_name(session, destination)
            try:
                from amiganut.file import AmigaMeta
            except ImportError as exc:
                raise DiskError("The Amiganut Kickstart ROM metadata API is unavailable.") from exc
            if filetype:
                raise DiskError("A ROM archive stores protection bits, not Workbench icon types.")
            with self.kickfs_mount(session, writable=True) as mount:
                original = session.path.read_bytes()
                try:
                    mount.write_bytes(destination, host_path.read_bytes())
                    current = mount.amiga_meta(destination)
                    mount.set_amiga_meta(
                        destination,
                        AmigaMeta(
                            protection=(
                                self._protection_value(protection)
                                if protection
                                else current.protection
                            ),
                            comment=str(comment or current.comment),
                            datestamp=current.datestamp,
                        ),
                    )
                except Exception:
                    session.path.write_bytes(original)
                    raise
            self._mark_mutated(session)
            return
        if self.mountable(session):
            # Every component of the destination must be a legal Amiga name,
            # including the drawers above the file.
            self.validate_ofs_prefix(amiga_paths.parent(destination))
        self.validate_leaf_name(session, amiga_paths.leaf(destination))
        if self.mountable(session):
            try:
                from amiganut.file import AmigaMeta
                from amiganut.file.filetypes import parse_filetype
            except ImportError as exc:
                raise DiskError("The Amiganut import API is unavailable.") from exc
            with self.ffs_mount(session) as mount:
                mount.write_bytes(destination, host_path.read_bytes())
                current = mount.amiga_meta(destination)
                mount.set_amiga_meta(
                    destination,
                    AmigaMeta(
                        protection=(
                            self._protection_value(protection)
                            if protection
                            else current.protection
                        ),
                        comment=str(comment or current.comment),
                        datestamp=current.datestamp,
                    ),
                )
                if filetype:
                    mount.set_filetype(destination, parse_filetype(filetype))
            self._mark_mutated(session)
            return
        args = ["put"]
        if protection:
            args += ["--protection", f"0x{self._protection_value(protection):X}"]
        if comment:
            args += ["--comment", str(comment)]
        if filetype:
            args += ["--filetype", filetype]
        # The engine's ``put`` takes the host file first and the image path
        # second, in the order a shell copy is written.
        args += [
            str(host_path),
            self.compound(self.resolve(session), self.inner_for(session, destination, side)),
        ]
        with session.lock:
            self._run(args)
            self._mark_mutated(session)

    def put_host_tree(
        self,
        session: ImageSession,
        destination_dir: str,
        items: list[dict],
        *,
        preserve_directories: bool,
        replace: bool = False,
        side: int | None = None,
    ) -> dict:
        """Import a reviewed host folder in one writable filesystem mount.

        Each item contains a validated target path relative to
        ``destination_dir`` and a local temporary ``hostPath``.  Keeping the
        complete batch in one mount avoids reopening and checkpointing a large
        FFS image for every small file.
        """
        if session.kind == "dms":
            raise DiskError("Open a writable disk before importing a host folder.")
        self.require_writable_geometry(session)
        is_kickfs = session.kind == "kickfs"
        if preserve_directories and is_kickfs:
            raise DiskError(
                "A ROM's module list is flat. Import the selected files without "
                "preserving host folders."
            )
        if not is_kickfs:
            # Every AmigaDOS volume nests, so a host tree can be preserved on
            # any of them. Only the names have to be legal.
            destination_dir = self.validate_ofs_prefix(destination_dir)
        if not items:
            raise DiskError("No relevant files were selected for import.")

        plans: list[dict] = []
        seen: set[str] = set()
        for item in items:
            relative = str(item.get("targetPath") or "").replace("\\", "/").strip("/")
            parts = [part for part in relative.split("/") if part]
            if not parts or any(part in {".", ".."} for part in parts):
                raise DiskError("A selected folder contains an invalid relative path.")
            if is_kickfs and len(parts) != 1:
                raise DiskError(
                    "A ROM's module list is flat, so an import must use flat target names."
                )
            for part in parts:
                self.validate_leaf_name(session, part)
            destination = (
                parts[0] if is_kickfs else amiga_paths.join(destination_dir, "/".join(parts))
            )
            key = destination.casefold()
            if key in seen:
                raise DiskError(f"More than one selected file maps to {destination}.")
            seen.add(key)
            plans.append({**item, "parts": parts, "destination": destination})

        try:
            from amiganut.disc.mount import resolve_mount
            from amiganut.file import AmigaMeta
        except ImportError as exc:
            raise DiskError("The Amiganut folder import API is unavailable.") from exc

        with session.lock:
            disk_path = self.resolve(session)
            root = self.compound(disk_path, self.inner_for(session, "", side))
            if not is_kickfs:
                self.require_mounted_volume(session)
            mount_context = (
                self.kickfs_mount(session, writable=True)
                if is_kickfs
                else
                self.ffs_mount(session)
                if self.mountable(session)
                else resolve_mount(root, writable=True)
            )
            with mount_context as opened:
                mount = opened if (self.mountable(session)) or is_kickfs else opened.mount
                original_kickfs = session.path.read_bytes() if is_kickfs else None
                conflicts: list[str] = []
                directories: set[str] = set()
                if preserve_directories:
                    for plan in plans:
                        for depth in range(1, len(plan["parts"])):
                            directories.add(
                                amiga_paths.SEPARATOR.join(
                                    [*amiga_paths.split(destination_dir), *plan["parts"][:depth]]
                                )
                            )
                for directory in sorted(
                    directories, key=lambda value: (value.count("/"), value.casefold())
                ):
                    if mount.exists(directory) and not mount.stat(directory).is_dir:
                        raise DiskError(f"{directory} is an ordinary file, so a folder cannot be created there.")
                for plan in plans:
                    destination = plan["destination"]
                    if mount.exists(destination):
                        if mount.stat(destination).is_dir:
                            raise DiskError(f"{destination} is a directory, so a file cannot replace it.")
                        conflicts.append(destination)
                if conflicts and not replace:
                    return {"imported": [], "conflicts": conflicts}
                for directory in sorted(
                    directories, key=lambda value: (value.count("/"), value.casefold())
                ):
                    mount.make_directory(directory, parents=True, exist_ok=True)
                imported: list[str] = []

                try:
                    for plan in plans:
                        parent = amiga_paths.parent(plan["destination"])
                        if parent and not mount.exists(parent):
                            mount.make_directory(parent, parents=True, exist_ok=True)
                        mount.write_bytes(
                            plan["destination"], Path(plan["hostPath"]).read_bytes()
                        )
                        metadata = plan.get("metadata") or {}
                        if metadata.get("protection") or metadata.get("comment"):
                            current = mount.amiga_meta(plan["destination"])
                            supplied = metadata.get("protection")
                            mount.set_amiga_meta(
                                plan["destination"],
                                AmigaMeta(
                                    protection=(
                                        self._protection_value(supplied)
                                        if supplied
                                        else current.protection
                                    ),
                                    comment=str(
                                        metadata.get("comment") or current.comment
                                    ),
                                    datestamp=current.datestamp,
                                ),
                            )
                        if metadata.get("filetype") and hasattr(mount, "set_filetype"):
                            mount.set_filetype(plan["destination"], metadata["filetype"])
                        imported.append(plan["destination"])
                except Exception:
                    if original_kickfs is not None:
                        session.path.write_bytes(original_kickfs)
                    raise
            self._mark_mutated(session)
        return {"imported": imported, "conflicts": []}

    def copy(
        self,
        source: ImageSession,
        source_inner: str,
        target: ImageSession,
        target_inner: str,
        recursive: bool,
        source_side: int | None = None,
        target_side: int | None = None,
    ) -> None:
        if target.kind == "dms":
            raise DiskError("DMS archives are read-only conversion sources.")
        if source.kind == "rom" or target.kind == "rom":
            if recursive:
                raise DiskError("ROM banks are byte images and cannot contain directories.")
            data = (
                self.rom_bank_bytes(source, source_inner)
                if source.kind == "rom"
                else self.read_file(source, source_inner, source_side)
            )
            if target.kind == "rom":
                requested_bank = None
                if str(target_inner).lower().startswith(("bank:", "bank-")):
                    try:
                        requested_bank = bank_number(target_inner)
                    except RomError as exc:
                        raise DiskError(str(exc)) from exc
                self.put_rom_bank(target, data, requested_bank)
            else:
                temp_path = self.work_dir / f"rom-copy-{uuid.uuid4().hex}"
                temp_path.write_bytes(data)
                try:
                    self.put(target, target_inner, temp_path, side=target_side)
                finally:
                    temp_path.unlink(missing_ok=True)
            return
        self.require_writable_geometry(target)
        if target.kind in {"ofs", "ffs"}:
            self.validate_ofs_prefix(amiga_paths.parent(target_inner))
        self.validate_leaf_name(
            target,
            target_inner if target.kind == "kickfs" else amiga_paths.leaf(target_inner),
        )
        if source.kind == "dms":
            dms_file = self._dms_file(source, source_inner)
            temp_path = self.work_dir / f"dms-copy-{uuid.uuid4().hex}"
            temp_path.write_bytes(dms_file.data)
            try:
                self.put(target, target_inner, temp_path, side=target_side)
            finally:
                temp_path.unlink(missing_ok=True)
            return
        source_path = self.resolve(source)
        target_path = self.resolve(target)
        if target.kind in {"ffs", "ofs"}:
            try:
                from amiganut.disc.mount import resolve_mount
            except ImportError as exc:
                raise DiskError("The Amiganut direct-copy API is unavailable.") from exc

            def copy_between_mounts(source_mount, target_mount) -> None:
                self._copy_between_ffs_mounts(
                    source_mount,
                    target_mount,
                    source_inner,
                    target_inner,
                    recursive=recursive,
                    destination_slash=False,
                )

            with self._locked_sessions(source, target):
                if source.kind in {"ffs", "ofs"}:
                    if source.id == target.id:
                        with self.ffs_mount(target) as mount:
                            copy_between_mounts(mount, mount)
                    else:
                        with self.ffs_mount(source) as source_mount:
                            with self.ffs_mount(target) as target_mount:
                                copy_between_mounts(source_mount, target_mount)
                else:
                    source_root = self.inner_for(source, "$", source_side)
                    with resolve_mount(self.compound(source_path, source_root)) as source_resolved:
                        with self.ffs_mount(target) as target_mount:
                            copy_between_mounts(source_resolved.mount, target_mount)
            target.dirty = True
            target.hfe_export_path = None
            return
        args = ["cp", "--no-wildcards"]
        if recursive:
            args.append("--recursive")
        args += [
            self.compound(source_path, self.inner_for(source, source_inner, source_side)),
            self.compound(target_path, self.inner_for(target, target_inner, target_side)),
        ]
        with self._locked_sessions(source, target):
            self._run(args)
            self._mark_mutated(target)

    def replace_blank_ofs_image(
        self,
        target: ImageSession,
        source: ImageSession,
        source_name: str,
        *,
        target_path: str,
    ) -> bool:
        """Install an ADF into a blank ADF without losing its title or catalogue."""
        if (
            target.kind != "ofs"
            or source.kind != "ofs"
            or target_path not in amiga_paths.ROOT_TOKENS
            or target.path.suffix.lower() != ".adf"
            or source.path.suffix.lower() != ".adf"
            or self.list_directory(target, "")["entries"]
        ):
            return False
        target_size = target.path.stat().st_size
        if source.path.stat().st_size > target_size:
            return False
        replacement = target.path.parent / f".online-replacement-{uuid.uuid4().hex}.adf"
        try:
            with self._locked_sessions(source, target):
                self._copy_local_file(source.path, replacement)
                with replacement.open("ab") as image:
                    image.truncate(target_size)
                replacement.replace(target.path)
                target.name = self.safe_filename(Path(source_name).name)
                target.dirty = True
                target.hfe_export_path = None
                target.finalised_mtime_ns = None
                self._persist_session(target)
        finally:
            replacement.unlink(missing_ok=True)
        return True

    @staticmethod
    def _collect_ofs_catalogue_items(
        source_mount,
        destination: str,
        file_item: Callable,
    ) -> list[dict]:
        """Collect every file on a volume, ready to be written under one drawer.

        The whole tree is walked rather than only its root, because an Amiga
        volume nests. Directory descriptors are emitted before the files that
        need them, so the destination is built top-down and never has to guess
        at a parent.
        """
        items: list[dict] = []
        order = 0

        def walk(path: str) -> None:
            nonlocal order
            for entry in sorted(
                source_mount.iter_entries(path),
                key=lambda item: natural_name_key(item.name),
            ):
                target = amiga_paths.join(
                    destination, amiga_paths.normalise(entry.path)
                )
                if entry.is_dir:
                    items.append({"kind": "mkdir", "dst": target, "order": order})
                    order += 1
                    walk(entry.path)
                    continue
                item = file_item(source_mount, entry.path, target)
                item["sourceName"] = amiga_paths.normalise(entry.path)
                items.append(item)

        walk("")
        return items

    @staticmethod
    def _repair_ffs_loader_items(items: list[dict]) -> tuple[list[str], list[str]]:
        """Make copied loaders work from the drawer they have been installed to.

        Software written for a floppy names its files through ``DF0:``. Copied
        to a hard drive that reference is wrong, and the failure looks like a
        corrupt disk rather than a path problem. Every script in the batch is
        checked, and the ones that can be repaired without changing their
        length are repaired in place.

        Returns the changes made and the warnings for the ones that could not
        be. Each item that changed is marked with ``loaderRepairs`` so the
        caller knows which files still need writing.
        """
        repairs: list[str] = []
        warnings: list[str] = []
        for item in items:
            data = item.get("data")
            if not isinstance(data, (bytes, bytearray)) or not data:
                continue
            if b"\0" in data[:512]:
                continue
            printable = sum(
                1 for byte in data[:512] if 9 <= byte <= 13 or 32 <= byte <= 126
            )
            if printable / max(1, len(data[:512])) < 0.9:
                continue
            text = bytes(data).decode("latin-1")
            replaced = re.sub(r"(?i)\bDF[0-3]:", lambda match: " " * len(match.group(0)), text)
            if replaced == text:
                continue
            name = str(item.get("sourceName") or item.get("dst") or "a copied file")
            item["data"] = replaced.encode("latin-1", "replace")
            item["loaderRepairs"] = True
            repairs.append(
                f"removed the floppy device prefix from {name} so it runs from its own drawer"
            )
        return repairs, warnings

    @staticmethod
    def _is_empty_directory(mount, path: str) -> bool:
        """True when a path exists, is a drawer, and holds nothing.

        An empty destination can be reused without asking. A populated one
        cannot, because reusing it would merge two unrelated disks into the
        same drawer, so the two cases are told apart before anything is
        written rather than after.
        """
        try:
            if not mount.exists(path):
                return False
            if not mount.stat(path).is_dir:
                return False
            return not any(True for _entry in mount.iter_entries(path))
        except Exception:
            return False

    @staticmethod
    def _relocate_ofs_boot_script(data: bytes, destination: str) -> bytes:
        """Point a startup script at the drawer it has been installed into.

        A script written for a floppy names its files from the volume root. On
        a hard drive those files are one drawer down, so every root reference
        has to gain the drawer in front of it. Device prefixes are removed for
        the same reason: ``DF0:`` is not where the software lives any more.

        Only whole path references are rewritten, and the result is returned
        rather than written, so the caller decides whether the change is worth
        making.
        """
        import re as _re

        prefix = amiga_paths.normalise(destination)
        if not prefix:
            return data
        text = data.decode("latin-1", "replace")

        def replace(match: "_re.Match[str]") -> str:
            quote, device, path = match.group(1), match.group(2), match.group(3)
            del device
            return f"{quote}{prefix}/{path}" if path else f"{quote}{prefix}"

        # ``"DF0:Game"``, ``"SYS:Game"`` and a bare leading ``:`` all mean the
        # volume root, which is exactly what has moved.
        relocated = _re.sub(
            r'(["\s])(?:(DF[0-3]|DH[0-9]|SYS):|:)([A-Za-z0-9_.\-/]*)',
            replace,
            text,
        )
        return relocated.encode("latin-1", "replace")

    @staticmethod
    def _write_ffs_copy_item(
        target_mount,
        destination: str,
        item: dict,
        fallback: Callable,
    ) -> None:
        """Write file data and its catalogue metadata in one update."""
        navigate = getattr(target_mount, "_navigate", None)
        if navigate is None or target_mount.exists(destination):
            fallback(target_mount, destination, item, False)
            return
        # A copy descriptor may name a file several drawers deep, so the chain
        # above it is built before the write rather than assumed.
        parent = amiga_paths.parent(destination)
        if parent and not target_mount.exists(parent):
            target_mount.make_directory(parent, parents=True, exist_ok=True)

        from amiganut.file import Access

        access_value = int(item.get("access") or 0)
        target = navigate(destination)
        target.write_bytes(
            item["data"],
            access=Access(access_value),
            comment=str(item.get("comment") or ""),
        )
        # write_bytes applies the filing system's own defaults beyond the lock
        # bit, so one chmod is still needed to match the source's complete
        # protection mask.
        target.chmod(access_value)
        filetype = item.get("filetype")
        if filetype is not None:
            target_mount.set_filetype(destination, filetype)
        datestamp = item.get("datestamp")
        if datestamp is not None:
            target_mount.set_datestamp(destination, datestamp)

    @staticmethod
    def _set_ffs_directory_title(mount, path: str, title: str) -> None:
        """Store the source disk title so later menu scans retain useful metadata."""
        try:
            target = mount._navigate(path)
            if getattr(target, "supports_title", False):
                target.title = str(title or "")[:19]
        except (AttributeError, OSError, RuntimeError, ValueError):
            pass

    @staticmethod
    def _unique_import_name(name: str, used: set[str], limit: int) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9!_-]", "_", amiga_paths.leaf(name)) or "FILE"
        base = cleaned[:limit]
        candidate = base
        number = 1
        while candidate.casefold() in used:
            suffix = str(number)
            candidate = f"{base[: limit - len(suffix)]}{suffix}"
            number += 1
        used.add(candidate.casefold())
        return candidate

    @staticmethod
    def _ffs_import_name(name: str, used: set[str]) -> str:
        return DiskService._unique_import_name(name, used, 10)

    def extract_image_to_ffs_directory(
        self,
        source: ImageSession,
        target: ImageSession,
        target_parent: str,
        directory_name: str | None,
        progress: Callable[[str, int | None, int | None], None] | None = None,
        *,
        create_directory: bool = True,
    ) -> str:
        with self._locked_sessions(source, target):
            return self._extract_image_to_ffs_directory(
                source,
                target,
                target_parent,
                directory_name,
                progress,
                create_directory=create_directory,
            )

    def _extract_image_to_ffs_directory(
        self,
        source: ImageSession,
        target: ImageSession,
        target_parent: str,
        directory_name: str | None,
        progress: Callable[[str, int | None, int | None], None] | None = None,
        *,
        create_directory: bool = True,
    ) -> str:
        report = progress or (lambda _message, _current=None, _total=None: None)
        if target.kind not in {"ffs", "ofs"}:
            raise DiskError("Disk images can only be expanded into an FFS destination.")
        self.require_writable_geometry(target)
        target_parent = target_parent or "$"
        if create_directory:
            directory_name = self.validate_leaf_name(target, directory_name or "")
            target_directory = (
                amiga_paths.join(target_parent, directory_name)
                if target_parent not in amiga_paths.ROOT_TOKENS
                else directory_name
            )
        else:
            # Resolve the destination before taking a rollback copy. This also
            # rejects stale browser paths without modifying the image.
            self.list_directory(target, target_parent, None)
            target_directory = target_parent
        ofs_rows: dict[int | None, list[dict]] = {}
        if source.kind == "ofs":
            if self.is_two_volume_image(source):
                ofs_rows[0] = self.list_ofs_catalogue_files(source, None, 0)
                ofs_rows[2] = self.list_ofs_catalogue_files(source, None, 2)
                source_has_files = bool(ofs_rows[0] or ofs_rows[2])
            else:
                ofs_rows[None] = self.list_ofs_catalogue_files(source, None)
                source_has_files = bool(ofs_rows[None])
            if not source_has_files:
                raise DiskError(
                    "The OFS disk image is empty. Nothing was extracted."
                )
        elif source.kind in {"ffs", "ofs"} and not self.list_directory(source, "")["entries"]:
            raise DiskError(
                "The FFS disk image is empty. Nothing was extracted."
            )
        if create_directory:
            # Check and create through one trusted mount.  This avoids two
            # complete FFS opens before an import can begin.
            with self.ffs_mount(target) as target_mount:
                if not target_mount.exists(target_parent):
                    raise DiskError(f"Path not found: {target_parent}")
                if target_mount.exists(target_directory):
                    raise DiskError(
                        f"“{directory_name}” already exists in the destination directory."
                    )
                report(f"Creating destination directory {target_directory}", 0, None)
                target_mount.make_directory(target_directory, parents=True, exist_ok=False)
            self._mark_mutated(target)

        rollback_path: Path | None = None
        dirty_before = target.dirty
        warnings_before = list(target.warnings)
        hfe_export_before = target.hfe_export_path
        if not create_directory:
            report(f"Preparing safe extraction into {target_directory}", 0, None)
            rollback_path = target.path.parent / f".import-rollback-{uuid.uuid4().hex}"
            self._copy_local_file(target.path, rollback_path)
        try:
            if source.kind == "dms":
                # A DMS is a whole floppy, so the honest extraction is to
                # rebuild the disk it was made from and copy that volume's
                # files. Treating its tracks as files would present raw
                # cylinders as though they were software.
                report("Rebuilding the disk from its DMS tracks", 0, None)
                rebuilt, _tracks = self.convert_dms(source, "adf")
                try:
                    self._copy_image_listing_to_ffs(
                        rebuilt, None, None, target, target_directory, report,
                    )
                    self.carry_boot_option(rebuilt, target, target_directory)
                finally:
                    self.discard_session(rebuilt)
            elif source.kind == "ofs" and self.is_two_volume_image(source):
                first = ofs_rows[0]
                second = ofs_rows[2]
                if first and second:
                    for side, rows in ((0, first), (2, second)):
                        number = side // 2 + 1
                        report(f"Extracting volume {number}", side // 2, 2)
                        volume_directory = amiga_paths.join(
                            target_directory, f"Volume{number}"
                        )
                        self.make_directory(target, volume_directory)
                        self._copy_rows_to_ffs(
                            source, None, side, rows, target, volume_directory, report
                        )
                        report(f"Extracted volume {number}", number, 2)
                else:
                    side = 0 if first else 2
                    self._copy_rows_to_ffs(
                        source, None, side, first or second, target, target_directory, report
                    )
            else:
                if source.kind == "ofs":
                    self._copy_image_listing_to_ffs(
                        source,
                        None,
                        None,
                        target,
                        target_directory,
                        report,
                        rows=ofs_rows[None],
                    )
                else:
                    self._copy_image_listing_to_ffs(
                        source, None, None, target, target_directory, report
                    )
            if source.kind in {"ofs", "dms", "ffs"}:
                # Extraction into the root can keep the source's boot option,
                # which is what lets the image start itself. carry_boot_option
                # declines any other destination, because a boot option names
                # $.Startup-Sequence and would otherwise point at a file that is not there.
                self.carry_boot_option(source, target, target_directory)
                report("Checking copied loaders for FFS command conflicts", None, None)
                loader_repairs, loader_warnings = self._repair_copied_ffs_loaders(
                    target,
                    target_directory,
                )
                for warning in loader_warnings:
                    self._append_warning(target, f"{target_directory}: {warning}")
                for repair in loader_repairs:
                    self._append_warning(
                        target,
                        f"{target_directory}: FFS compatibility change made: {repair}.",
                    )
                if loader_repairs:
                    report(
                        f"Repaired {len(loader_repairs)} FFS loader command conflict(s)",
                        None,
                        None,
                    )
                profile = target.hardware_profile or {}
                addons = {str(item).casefold() for item in profile.get("addons", [])}
                if profile.get("accelerated") or any(
                    item.startswith("acc-") or item == "pistorm" for item in addons
                ):
                    self._append_warning(
                        target,
                        f"{target_directory}: the selected hardware profile fits a CPU accelerator. "
                        "Many OCS and ECS games depend on 68000 timing or write directly to the custom "
                        "chips, and must be run with the accelerator and its Fast RAM disabled unless the "
                        "software explicitly supports them.",
                    )
        except Exception:
            if create_directory:
                try:
                    self._run([
                        "rm",
                        "--force",
                        "--recursive",
                        self.compound(target.path, target_directory),
                    ])
                except Exception:
                    pass
            elif rollback_path and rollback_path.is_file():
                rollback_path.replace(target.path)
                target.dirty = dirty_before
                target.warnings = warnings_before
                target.hfe_export_path = hfe_export_before
            raise
        finally:
            if rollback_path:
                rollback_path.unlink(missing_ok=True)
        target.dirty = True
        target.hfe_export_path = None
        return target_directory

    def _copy_image_listing_to_ffs(
        self,
        source: ImageSession,
        source_side: int | None,
        target: ImageSession,
        target_directory: str,
        progress: Callable[[str, int | None, int | None], None] | None = None,
        *,
        rows: list[dict] | None = None,
    ) -> None:
        if rows is None:
            rows = (
                self.list_ofs_catalogue_files(source, source_side)
                if source.kind in {"ofs", "hdf"}
                else self.list_directory(source, "$", source_side)["entries"]
            )
        self._copy_rows_to_ffs(
            source, source_side, rows, target, target_directory, progress
        )

    def _copy_rows_to_ffs(
        self,
        source: ImageSession,
        source_side: int | None,
        rows: list[dict],
        target: ImageSession,
        target_directory: str,
        progress: Callable[[str, int | None, int | None], None] | None = None,
    ) -> None:
        report = progress or (lambda _message, _current=None, _total=None: None)
        if not rows:
            return
        source_path = self.resolve(source)
        report("Copying the complete disk catalogue in one batch", 0, len(rows))
        if target.kind in {"ffs", "ofs"} and source.kind in {"ofs", "hdf"}:
            from amiganut.disc.mount import resolve_mount
            source_root = self.inner_for(source, "$", source_side)
            with self._locked_sessions(source, target):
                with resolve_mount(self.compound(source_path, source_root)) as source_resolved:
                    copy_items = self._collect_ofs_catalogue_items(
                        source_resolved.mount,
                        target_directory,
                        file_copy_item,
                    )
                    copy_items = in_storage_order(source_resolved.mount, copy_items)
                with self.ffs_mount(target) as target_mount:
                    for item in copy_items:
                        if item["kind"] == "mkdir":
                            ensure_directory_chain(target_mount, item["dst"])
                        else:
                            self._write_ffs_copy_item(
                                target_mount,
                                str(item["dst"]),
                                item,
                                write_copy_item,
                            )
            target.dirty = True
            target.hfe_export_path = None
            report("Copied the complete disk catalogue", len(rows), len(rows))
            return
        if target.kind in {"ffs", "ofs"} and source.kind in {"ffs", "ofs"}:
            def copy_between_mounts(source_mount, target_mount) -> None:
                self._copy_between_ffs_mounts(
                    source_mount,
                    target_mount,
                    "$",
                    target_directory,
                    recursive=True,
                    destination_slash=True,
                )

            with self._locked_sessions(source, target):
                if source.id == target.id:
                    with self.ffs_mount(target) as mount:
                        copy_between_mounts(mount, mount)
                else:
                    with self.ffs_mount(source) as source_mount:
                        with self.ffs_mount(target) as target_mount:
                            copy_between_mounts(source_mount, target_mount)
            target.dirty = True
            target.hfe_export_path = None
            report("Copied the complete disk catalogue", len(rows), len(rows))
            return
        source_pattern = "*"
        self._run(
            [
                "cp",
                "--recursive",
                self.compound(
                    source_path,
                    self.inner_for(source, source_pattern, source_side),
                ),
                self.compound(target.path, target_directory),
            ]
        )
        report("Copied the complete disk catalogue", len(rows), len(rows))

    def read_file(self, session: ImageSession, inner: str, side: int | None = None) -> bytes:
        if session.kind == "rom":
            return self.rom_bank_bytes(session, inner)
        if session.kind == "dms":
            return self._dms_file(session, inner).data
        if session.kind == "kickfs":
            with self.kickfs_mount(session) as mount:
                return mount.read_bytes(inner)
        if self.mountable(session):
            with self.ffs_mount(session) as mount:
                return mount.read_bytes(inner)
        disk_path = self.resolve(session)
        return self._run(["get", "--meta-format", "none", self.compound(disk_path, self.inner_for(session, inner, side)), "-"], binary=True)

    def file_metadata(
        self,
        session: ImageSession,
        inner: str,
        side: int | None = None,
    ) -> dict:
        """Return portable Amiga metadata for one exported loose file."""
        if session.kind == "rom":
            data = self.rom_bank_bytes(session, inner)
            return {"protection": 0, "comment": "", "access": 0, "length": len(data)}
        if session.kind == "dms":
            item = self._dms_file(session, inner)
            return {
                "protection": 0,
                "comment": "",
                "access": 0,
                "length": len(item.data),
                "unpackedChecksum": item.unpacked_crc,
                "packedChecksum": item.packed_crc,
            }
        if session.kind == "kickfs":
            with self.kickfs_mount(session) as mount:
                stat = mount.stat(inner)
                metadata = mount.amiga_meta(inner)
                return {
                    "protection": int(metadata.protection or 0),
                    "comment": str(metadata.comment or ""),
                    "access": int(metadata.access or 0),
                    "length": int(stat.length or 0),
                }
        try:
            from amiganut.disc.mount import resolve_mount
        except ImportError as exc:
            raise DiskError("The Amiganut metadata API is unavailable.") from exc
        if self.mountable(session):
            with self.ffs_mount(session) as mount:
                stat = mount.stat(inner)
                metadata = mount.amiga_meta(inner)
                return {
                    "protection": int(metadata.protection or 0),
                    "comment": str(metadata.comment or ""),
                    "access": int(metadata.access or 0),
                    "length": int(stat.length or 0),
                }
        disk_path = self.resolve(session)
        root = self.compound(disk_path, self.inner_for(session, "", side))
        with session.lock, resolve_mount(root) as resolved:
            target = self.inner_for(session, inner, side)
            stat = resolved.mount.stat(target)
            metadata = resolved.mount.amiga_meta(target)
            return {
                "protection": int(metadata.protection or 0),
                "comment": str(metadata.comment or ""),
                "access": int(metadata.access or 0),
                "length": int(stat.length or 0),
            }

    def export_file(
        self,
        session: ImageSession,
        inner: str,
        side: int | None = None,
    ) -> Path:
        """Export an image file without buffering its contents in application RAM."""
        target = self.work_dir / f"download-{uuid.uuid4().hex}"
        if session.kind == "rom":
            target.write_bytes(self.rom_bank_bytes(session, inner))
            return target
        if session.kind == "dms":
            target.write_bytes(self._dms_file(session, inner).data)
            return target
        if session.kind == "kickfs":
            with self.kickfs_mount(session) as mount:
                target.write_bytes(mount.read_bytes(inner))
            return target
        if self.mountable(session):
            with self.ffs_mount(session) as mount:
                target.write_bytes(mount.read_bytes(inner))
            return target
        disk_path = self.resolve(session)
        try:
            self._run(
                [
                    "get",
                    "--meta-format",
                    "none",
                    self.compound(
                        disk_path,
                        self.inner_for(session, inner, side),
                    ),
                    str(target),
                ]
            )
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return target

    def compact(self, session: ImageSession, order: str | None = None) -> None:
        if session.kind == "kickfs":
            raise DiskError("Kickstart ROM is rebuilt into storage order after every edit and does not need compaction.")
        if session.kind == "dms":
            raise DiskError("DMS archives cannot be compacted; convert to a disk image first.")
        self.require_writable_geometry(session)
        disk_path = self.resolve(session)
        args = ["compact"]
        if order:
            args += ["--order", order]
        args.append(str(disk_path))
        with session.lock:
            self._run(args)
            self._mark_mutated(session)

    @staticmethod
    def _friendly_engine_error(message: str) -> str:
        return friendly_engine_error(message)

    @staticmethod
    def _run(args: list[str], binary: bool = False) -> bytes | str:
        return run_disc(args, binary)

    @staticmethod
    def _run_hxcfe(args: list[str]) -> str:
        return run_hxcfe(args)

    @classmethod
    def _run_json(cls, args: list[str]) -> dict:
        return decode_disc_json(cls._run(args))
