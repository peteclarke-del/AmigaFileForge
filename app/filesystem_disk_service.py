from __future__ import annotations

from contextlib import contextmanager

from .ffs_capabilities import capabilities_from_mount
from .errors import DiskError
from .image_session import ImageSession


class FilesystemDiskMixin:
    """Trusted FFS and Kickstart ROM mounts plus Kickstart ROM filesystem metadata edits."""

    @staticmethod
    def mountable(session: ImageSession) -> bool:
        """Whether this session currently addresses one AmigaDOS volume.

        A floppy or single-volume image always does. A partitioned hard drive
        does once a partition has been chosen; until then the pane is showing
        the drive's partition table, which is not a volume.
        """
        if session.kind in {"ffs", "ofs"}:
            return True
        return session.kind == "hdf" and session.partition is not None

    @staticmethod
    def require_mounted_volume(session: ImageSession) -> None:
        """Refuse a volume operation on a drive with no partition chosen.

        A partitioned drive opens on its partition table, which is not a
        volume: there is no single filesystem to write into until one is
        selected. Saying so here keeps the message the same wherever the
        attempt is made, rather than letting a caller reach the raw drive and
        fail later on a mount that has no file operations at all.
        """
        if session.kind == "hdf" and session.partition is None:
            raise DiskError("Choose a partition on this hard drive first.")

    @contextmanager
    def ffs_mount(self, session: ImageSession):
        """Open an identified FFS image without probing or copying it again.

        A partition of a hard drive is an ordinary AmigaDOS volume that starts
        part way into a larger file, so it is handed back through the same
        contract and every caller downstream stays unaware of the difference.
        """
        if session.kind == "hdf":
            if session.partition is None:
                raise DiskError("Choose a partition on this hard drive first.")
            with self.rdb_mount(session) as mount:
                yield mount
            return
        if session.kind not in {"ffs", "ofs"}:
            raise DiskError("This operation requires an AmigaDOS volume.")
        try:
            from amiganut.filesystem import create_filesystem, geometry_from_dsc, reader_for
        except ImportError as exc:
            raise DiskError("The Amiganut FFS filesystem API is unavailable.") from exc

        with session.lock:
            reader = reader_for(session.path, writable=True)
            mount = None
            try:
                geometry = None
                if session.descriptor_path and session.descriptor_path.is_file():
                    geometry = geometry_from_dsc(session.descriptor_path.read_bytes())
                mount = create_filesystem("amigados").open(reader, geometry)
                yield mount
            except DiskError:
                raise
            except Exception as exc:
                raise DiskError(self._friendly_engine_error(str(exc))) from exc
            finally:
                if mount is not None:
                    ffs = getattr(mount, "_ffs", None)
                    unified = getattr(ffs, "_d", None)
                    disc_image = getattr(unified, "_disc_image", None)
                    try:
                        close_ffs = getattr(ffs, "close", None)
                        if callable(close_ffs):
                            close_ffs()
                    finally:
                        close_disc = getattr(disc_image, "close", None)
                        if callable(close_disc):
                            close_disc()
                reader.close()

    def refresh_ffs_capabilities(self, session: ImageSession) -> dict:
        """Cache the mounted volume's format and its real name limits.

        A volume that will not mount is still worth opening: its bytes can be
        inspected, compared and repaired in the hex editor. A failure here
        therefore records empty capabilities and a warning rather than making
        the whole session unopenable.
        """
        if session.kind not in {"ffs", "ofs"}:
            session.ffs_capabilities = {}
            return {}
        try:
            with self.ffs_mount(session) as mount:
                capabilities = capabilities_from_mount(mount).to_dict()
        except (DiskError, TypeError) as exc:
            session.ffs_capabilities = {}
            self._append_warning(
                session,
                f"The filing-system capabilities could not be read: {exc}",
            )
            return {}
        session.ffs_capabilities = {
            "format": capabilities["format"],
            "map": capabilities["map"],
            "directories": capabilities["directories"],
            "nameLimit": capabilities["name_limit"],
            "directoryEntryLimit": capabilities["directory_entry_limit"],
        }
        return session.ffs_capabilities

    @contextmanager
    def kickfs_mount(self, session: ImageSession, *, writable: bool = False):
        """Open an identified Kickstart ROM without probing it again."""
        if session.kind != "kickfs":
            raise DiskError("This operation requires an Amiga Kickstart ROM.")
        if writable:
            self.require_writable_geometry(session)
        try:
            from amiganut.filesystem import create_filesystem, reader_for
        except ImportError as exc:
            raise DiskError("The Amiganut Kickstart ROM filesystem API is unavailable.") from exc
        with session.lock:
            reader = reader_for(session.path, writable=writable)
            try:
                mount = create_filesystem("kickfs").open(reader, None)
                yield mount
            except DiskError:
                raise
            except Exception as exc:
                raise DiskError(self._friendly_engine_error(str(exc))) from exc
            finally:
                reader.close()

    def kickfs_details(self, session: ImageSession) -> dict:
        """Return decoded Kickstart ROM identity, safety and capacity information."""
        try:
            from amiganut.kickfs.kickfs import KICKFS
            kickfs = KICKFS.from_bytes(session.path.read_bytes())
        except Exception as exc:
            raise DiskError(f"The Kickstart ROM catalogue is invalid: {exc}") from exc
        warnings = []
        if not kickfs.is_complete:
            warnings.append(
                "The Kickstart ROM block chain has no end marker. It may be truncated or one part of a multi-ROM set."
            )
        if kickfs.is_complete and not kickfs.is_plain:
            warnings.append(
                "Executable or opaque content follows the Kickstart ROM catalogue, so this composite image is read-only."
            )
        fs_end = int(getattr(kickfs, "_fs_end", session.path.stat().st_size))
        total = session.path.stat().st_size
        return {
            "title": kickfs.title,
            "headerTitle": kickfs.header_title,
            "version": kickfs.version,
            "copyright": kickfs.copyright,
            "romType": kickfs.rom_type,
            "dataOffset": kickfs.data_offset,
            "fileCount": len(kickfs.data_files),
            "complete": kickfs.is_complete,
            "plain": kickfs.is_plain,
            "readOnly": not kickfs.is_complete or not kickfs.is_plain,
            "capacity": {
                "available": kickfs.is_complete and kickfs.is_plain,
                "unit": "bytes",
                "total": total,
                "used": min(total, fs_end),
                "free": max(0, total - fs_end),
                "reason": "Composite and multi-ROM images cannot report safely writable tail space."
                if not (kickfs.is_complete and kickfs.is_plain) else None,
            },
            "warnings": warnings,
        }

    def set_kickfs_properties(
        self,
        session: ImageSession,
        *,
        title: str,
        version: int,
        copyright_text: str,
    ) -> None:
        """Update a ROM's version word and identification string as one edit.

        A ROM's module *names* are fixed by its resident tags, so ``title`` is
        accepted and reported back but never written: renaming a module would
        move the pointer the ROM scan follows. The version word and the
        identification string are both rewritten in place, and the reset
        checksum is repaired afterwards so the ROM still starts.
        """
        if session.kind != "kickfs":
            raise DiskError("This image does not contain a readable Kickstart ROM.")
        copyright_text = str(copyright_text or "").strip()
        if not copyright_text:
            raise DiskError("A resident identification string cannot be empty.")
        if len(copyright_text) > 120:
            raise DiskError(
                "A resident identification string can contain at most 120 characters."
            )
        try:
            version = int(version)
        except (TypeError, ValueError) as exc:
            raise DiskError("A ROM version must be from 0 to 65535.") from exc
        if not 0 <= version <= 0xFFFF:
            raise DiskError("A ROM version must be from 0 to 65535.")
        original = session.path.read_bytes()
        try:
            from amiganut.kickfs.kickfs import set_copyright, set_version

            data = set_version(original, version)
            session.path.write_bytes(set_copyright(data, copyright_text))
        except Exception as exc:
            session.path.write_bytes(original)
            raise DiskError(f"The ROM identity could not be updated: {exc}") from exc
        self._mark_mutated(session)
