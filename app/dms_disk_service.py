from __future__ import annotations

import uuid
import tempfile
from pathlib import Path

from .errors import DiskError
from .image_session import ImageSession
from .dms import (
    DMSContents,
    DMSFile,
    DMSError,
    TRACK_SIZE,
    dms_editability,
    parse_dms,
    replace_dms_file,
    to_adf,
)
from . import amiga_paths


class DMSDiskMixin:
    """DMS parsing and DMS-to-OFS conversion for ``DiskService``."""

    @staticmethod
    def _dms(session: ImageSession) -> DMSContents:
        if session.dms is None:
            try:
                session.dms = parse_dms(session.path.read_bytes())
            except DMSError as exc:
                raise DiskError(str(exc)) from exc
        return session.dms

    def _dms_file(self, session: ImageSession, inner: str) -> DMSFile:
        name = amiga_paths.leaf(inner)
        for item in self._dms(session).files:
            if item.name.casefold() == name.casefold():
                return item
        raise DiskError(f"DMS file “{name}” was not found.")

    def _dms_file_index(self, session: ImageSession, inner: str) -> int:
        name = amiga_paths.leaf(inner)
        for index, item in enumerate(self._dms(session).files):
            if item.name.casefold() == name.casefold():
                return index
        raise DiskError(f"DMS file “{name}” was not found.")

    def dms_member_editability(self, session: ImageSession, inner: str) -> dict:
        if session.kind != "dms":
            raise DiskError("Only a DMS track carries a reconstruction proof.")
        try:
            return dms_editability(session.path.read_bytes(), self._dms_file_index(session, inner))
        except DMSError as exc:
            raise DiskError(str(exc)) from exc

    def preview_dms_member_replacement(
        self, session: ImageSession, inner: str, replacement: bytes,
    ) -> dict:
        if session.kind != "dms":
            raise DiskError("Only DMS DMS tracks use this structural comparison.")
        try:
            _rebuilt, report = replace_dms_file(
                session.path.read_bytes(), self._dms_file_index(session, inner), replacement,
            )
        except DMSError as exc:
            raise DiskError(str(exc)) from exc
        return report

    def replace_dms_member(self, session: ImageSession, inner: str, replacement: bytes) -> dict:
        """Atomically replace one proven member and refresh the parsed dms model."""
        if session.kind != "dms":
            raise DiskError("Only DMS DMS tracks can be rebuilt this way.")
        with session.lock:
            try:
                rebuilt, report = replace_dms_file(
                    session.path.read_bytes(), self._dms_file_index(session, inner), replacement,
                )
                parsed = parse_dms(rebuilt)
            except DMSError as exc:
                raise DiskError(str(exc)) from exc
            with tempfile.NamedTemporaryFile(
                dir=session.path.parent, prefix="dms-rebuild-", delete=False,
            ) as temporary:
                temporary.write(rebuilt)
                temporary_path = Path(temporary.name)
            try:
                temporary_path.replace(session.path)
            finally:
                temporary_path.unlink(missing_ok=True)
            session.dms = parsed
            self._mark_mutated(session)
            self._persist_session(session)
            return report

    def _ofs_conversion_name(self, name: str, used: set[str]) -> str:
        return self._unique_import_name(name, used, 7)

    def convert_dms(self, session: ImageSession, disk_format: str) -> tuple[ImageSession, list[dict]]:
        """Rebuild the disk a DMS archive was made from.

        A DMS is a whole floppy, not a bag of files: every track is written
        back at the cylinder it came from, so the result is the image the
        archive was created from rather than a new disk with the same contents
        laid out differently. A track the archive omits, which DiskMasher does
        for an empty one, is left as zeroes.
        """
        if session.kind != "dms":
            raise DiskError("Only DMS archives can be converted.")
        if disk_format not in {"adf", "adz"}:
            raise DiskError("A DMS can be converted to ADF or ADZ.")
        dms = self._dms(session)
        if not dms.files:
            raise DiskError("The archive contains no disk tracks to rebuild.")
        try:
            image = to_adf(session.path.read_bytes())
        except DMSError as exc:
            raise DiskError(str(exc)) from exc

        stem = Path(session.name).stem or "Disk"
        new_name = self.safe_filename(f"{stem}.{'adf' if disk_format == 'adf' else 'adz'}")
        folder = self.work_dir / uuid.uuid4().hex
        folder.mkdir(parents=True)
        path = folder / new_name
        if disk_format == "adz":
            import gzip

            path.write_bytes(gzip.compress(image, mtime=0))
        else:
            path.write_bytes(image)
        target = self.create_from_path(path)

        converted = [
            {
                "source": track.name,
                "destination": f"cylinder {track.number}",
                "offset": track.number * TRACK_SIZE,
                "length": len(track.data),
                "mode": track.mode,
                "complete": track.complete,
                "checksumValid": track.crc_ok,
            }
            for track in dms.files
            if track.number < 200
        ]
        for warning in dms.warnings:
            self._append_warning(target, f"DiskMasher archive: {warning}")
        return target, converted
