from __future__ import annotations

import json
import re
import secrets
import shutil
from pathlib import Path

from .checkpoints import CheckpointError
from .editor_project import normalise_editor_project
from .errors import DiskError
from .filename_policy import session_name_policy, target_name_policy
from .image_session import ImageSession, SESSION_OWNER
from .rom import DEFAULT_BANK_SIZE, bank_count, validate_bank_size
from .rom_workbench import normalise_project
from .session_state import session_metadata
from .dms import DMSError, parse_dms


class SessionDiskMixin:
    """Persistence, recovery, ownership, checkpoints and image summaries."""

    def _persist_session(self, session: ImageSession) -> None:
        session.warnings = self._normalise_warnings(session.warnings)
        target = session.path.parent / "session.json"
        temporary = session.path.parent / "session.json.tmp"
        temporary.write_text(json.dumps(session_metadata(session), separators=(",", ":")), encoding="utf-8")
        temporary.replace(target)

    def _restore_session(self, image_id: str) -> ImageSession:
        if not re.fullmatch(r"[0-9a-f]{32}", image_id):
            raise DiskError("That image session no longer exists.")
        folder = self.work_dir / image_id
        metadata_path = folder / "session.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            name = self.safe_filename(metadata["name"])
            path = folder / self.safe_filename(metadata.get("workingFile") or name)
            if not path.is_file() or path.parent != folder:
                raise ValueError
            descriptor_name = metadata.get("descriptorName")
            descriptor_file = metadata.get("descriptorFile")
            descriptor_path = folder / descriptor_file if descriptor_file else None
            if descriptor_path and (not descriptor_path.is_file() or descriptor_path.parent != folder):
                descriptor_path = None
                descriptor_name = None
            kind = metadata.get("kind") or self.detect_kind(name)
            if kind not in {"hdf", "ofs", "ffs", "dms", "rom", "kickfs", "raw"}:
                raise ValueError
            session = ImageSession(
                id=image_id,
                name=name,
                kind=kind,
                path=path,
                descriptor_name=descriptor_name,
                descriptor_path=descriptor_path,
                dirty=bool(metadata.get("dirty", True)),
                partition=(
                    int(metadata["partition"])
                    if metadata.get("partition") is not None
                    else None
                ),
                ffs_source_names={
                    str(path): str(name)
                    for path, name in metadata.get("ffsSourceNames", {}).items()
                },
                distribution_name=metadata.get("distributionName"),
                target_hardware=str(metadata.get("targetHardware") or "auto"),
                hardware_profile=(
                    dict(metadata.get("hardwareProfile") or {})
                    if isinstance(metadata.get("hardwareProfile"), dict)
                    else {}
                ),
                hfe_original_path=(
                    folder / self.safe_filename(metadata["hfeOriginalFile"])
                    if metadata.get("hfeOriginalFile")
                    else None
                ),
                hfe_version=metadata.get("hfeVersion"),
                hfe_read_only=bool(metadata.get("hfeReadOnly")),
                hfe_export_path=(
                    folder / self.safe_filename(metadata["hfeExportFile"])
                    if metadata.get("hfeExportFile")
                    else None
                ),
                scp_original_path=(
                    folder / self.safe_filename(metadata["scpOriginalFile"])
                    if metadata.get("scpOriginalFile")
                    else None
                ),
                scp_read_only=bool(metadata.get("scpReadOnly")),
                scp_export_path=(
                    folder / self.safe_filename(metadata["scpExportFile"])
                    if metadata.get("scpExportFile")
                    else None
                ),
                rom_bank_size=validate_bank_size(int(metadata.get("romBankSize", DEFAULT_BANK_SIZE))),
                rom_erase_byte=int(metadata.get("romEraseByte", 0xFF)) & 0xFF,
                rom_platform=str(metadata.get("romPlatform") or "kickstart"),
                rom_layout=str(metadata.get("romLayout") or "linear"),
                rom_component_names=[
                    self.safe_filename(name)
                    for name in metadata.get("romComponentNames", [])
                    if name
                ],
                rom_project=normalise_project(metadata.get("romProject")),
                editor_projects={
                    str(key): normalise_editor_project(value)
                    for key, value in dict(metadata.get("editorProjects") or {}).items()
                },
                compatibility_reports=[
                    dict(report)
                    for report in list(metadata.get("compatibilityReports") or [])[-10:]
                    if isinstance(report, dict)
                ],
                finalised_mtime_ns=(
                    int(metadata["finalisedMtimeNs"])
                    if metadata.get("finalisedMtimeNs") is not None
                    else None
                ),
                owner_id=metadata.get("ownerId"),
                warnings=self._normalise_warnings(
                    [str(warning) for warning in metadata.get("warnings", [])]
                ),
                ffs_capabilities=(
                    dict(metadata.get("ffsCapabilities") or {})
                    if isinstance(metadata.get("ffsCapabilities"), dict)
                    else {}
                ),
            )
            if session.hfe_original_path and not session.hfe_original_path.is_file():
                raise ValueError
            if session.hfe_export_path and not session.hfe_export_path.is_file():
                session.hfe_export_path = None
            if session.scp_original_path and not session.scp_original_path.is_file():
                raise ValueError
            if session.scp_export_path and not session.scp_export_path.is_file():
                session.scp_export_path = None
            if session.kind == "dms":
                session.dms = parse_dms(path.read_bytes())
            elif session.kind in {"ffs", "ofs"} and not session.ffs_capabilities:
                self.refresh_ffs_capabilities(session)
            self._normalise_hardfile_dat_size(session)
        except (OSError, KeyError, ValueError, json.JSONDecodeError, DMSError) as exc:
            raise DiskError("That image session no longer exists.") from exc
        with self._lock:
            self.sessions[image_id] = session
        return session

    def get(self, image_id: str) -> ImageSession:
        try:
            session = self.sessions[image_id]
        except KeyError:
            session = self._restore_session(image_id)
        owner_id = SESSION_OWNER.get()
        if owner_id is None:
            return session
        if not session.owner_id or not secrets.compare_digest(
            session.owner_id,
            owner_id,
        ):
            raise DiskError("That image session no longer exists.")
        return session

    def recoverable_sessions(self, limit: int = 50) -> list[dict]:
        """List persisted working images without opening their large data files."""
        recovered: list[dict] = []
        owner_id = SESSION_OWNER.get()
        for metadata_path in self.work_dir.glob("*/session.json"):
            image_id = metadata_path.parent.name
            if not re.fullmatch(r"[0-9a-f]{32}", image_id):
                continue
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                persisted_owner = metadata.get("ownerId")
                if owner_id is not None and persisted_owner != owner_id:
                    continue
                name = self.safe_filename(metadata["name"])
                working_name = self.safe_filename(metadata.get("workingFile") or name)
                image_path = metadata_path.parent / working_name
                if not image_path.is_file() or image_path.parent != metadata_path.parent:
                    continue
                stat = image_path.stat()
                descriptor_file = metadata.get("descriptorFile")
                descriptor_path = (
                    metadata_path.parent / self.safe_filename(descriptor_file)
                    if descriptor_file
                    else None
                )
                recovered.append({
                    "id": image_id,
                    "name": name,
                    "kind": str(metadata.get("kind") or self.detect_kind(name)),
                    "size": stat.st_size,
                    "modified": stat.st_mtime_ns // 1_000_000,
                    "hasDescriptor": bool(descriptor_path and descriptor_path.is_file()),
                    "targetHardware": str(metadata.get("targetHardware") or "auto"),
                })
            except (OSError, KeyError, ValueError, json.JSONDecodeError):
                continue
        recovered.sort(key=lambda item: item["modified"], reverse=True)
        return recovered[: max(1, min(int(limit), 100))]

    def clear_recoverable_sessions(self, image_ids: list[str] | None = None) -> int:
        """Delete only working copies owned by the current browser identity."""
        owner_id = SESSION_OWNER.get()
        if owner_id is None:
            raise DiskError("Session ownership is unavailable for this request.")
        requested = set(image_ids) if image_ids is not None else None
        removed = 0
        for metadata_path in tuple(self.work_dir.glob("*/session.json")):
            image_id = metadata_path.parent.name
            if requested is not None and image_id not in requested:
                continue
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                persisted_owner = str(metadata.get("ownerId") or "")
                if not persisted_owner or not secrets.compare_digest(persisted_owner, owner_id):
                    continue
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            with self._lock:
                self.sessions.pop(image_id, None)
            shutil.rmtree(metadata_path.parent, ignore_errors=True)
            removed += 1
        return removed

    def discard_session(self, session: ImageSession) -> None:
        with self._lock:
            self.sessions.pop(session.id, None)
        shutil.rmtree(session.path.parent, ignore_errors=True)

    def rename_session(self, session: ImageSession, requested_name: str) -> None:
        """Rename an image for display, recovery and download without moving its working file."""
        requested_name = str(requested_name or "").strip()
        if not requested_name or requested_name != Path(requested_name).name:
            raise DiskError("Enter a filename without a directory path.")

        current_suffix = Path(session.name).suffix
        requested_suffix = Path(requested_name).suffix
        if current_suffix:
            if not requested_suffix:
                requested_name += current_suffix
            elif requested_suffix.casefold() != current_suffix.casefold():
                raise DiskError(f"Keep the {current_suffix} extension for this image.")
        elif requested_suffix:
            raise DiskError("This image has no extension; keep its filename extensionless.")

        safe_name = self.safe_filename(requested_name)
        if safe_name != requested_name:
            raise DiskError("Use letters, numbers, spaces and ordinary filename punctuation only.")
        if not Path(safe_name).stem:
            raise DiskError("Enter a filename before the extension.")
        if safe_name == session.name:
            return

        with session.lock:
            session.name = safe_name
            if session.descriptor_path:
                descriptor_suffix = Path(session.descriptor_name or ".geo").suffix or ".geo"
                session.descriptor_name = f"{Path(safe_name).stem}{descriptor_suffix}"
            session.hfe_export_path = None
            session.scp_export_path = None
            self._persist_session(session)

    def list_checkpoints(self, session: ImageSession) -> list[dict]:
        with session.lock:
            return self.checkpoints.list(session)

    def oldest_checkpoint_snapshot(
        self, session: ImageSession
    ) -> tuple[Path, Path | None, dict] | None:
        with session.lock:
            try:
                return self.checkpoints.oldest_snapshot(session)
            except CheckpointError as exc:
                raise DiskError(str(exc)) from exc

    def create_checkpoint(
        self,
        session: ImageSession,
        name: str,
        *,
        automatic: bool = False,
        reason: str | None = None,
    ) -> dict:
        with session.lock:
            try:
                return self.checkpoints.create(
                    session,
                    name,
                    automatic=automatic,
                    reason=reason,
                )
            except CheckpointError as exc:
                raise DiskError(str(exc)) from exc

    def begin_automatic_checkpoint(self, session: ImageSession, reason: str) -> dict:
        """Capture the image before one API operation and return a finalisation token."""
        with session.lock:
            fingerprint = self.checkpoints.fingerprint(session)
            checkpoint = self.create_checkpoint(
                session,
                f"Before {reason}",
                automatic=True,
                reason=reason,
            )
        return {"checkpoint": checkpoint, "fingerprint": fingerprint}

    def finish_automatic_checkpoint(self, session: ImageSession, token: dict) -> None:
        """Discard a speculative undo point when the request changed nothing."""
        with session.lock:
            try:
                unchanged = self.checkpoints.fingerprint(session) == token["fingerprint"]
            except OSError:
                unchanged = False
            if unchanged:
                try:
                    self.checkpoints.delete(session, token["checkpoint"]["id"])
                except CheckpointError:
                    pass
            else:
                self.checkpoints.prune_automatic(session)
            # Every API mutation passes through this finaliser. Persist the
            # resulting dirty/export state here so recovery cannot resurrect
            # an edited image as though it were still saved.
            self._persist_session(session)

    def rollback_automatic_checkpoint(self, session: ImageSession, token: dict) -> None:
        """Restore a failed mutation and remove its now-redundant undo point."""
        checkpoint_id = str(token["checkpoint"]["id"])
        self.restore_checkpoint(session, checkpoint_id)
        try:
            self.delete_checkpoint(session, checkpoint_id)
        except DiskError:
            pass

    def restore_checkpoint(self, session: ImageSession, checkpoint_id: str) -> dict:
        with session.lock:
            try:
                restored = self.checkpoints.restore(session, checkpoint_id)
            except CheckpointError as exc:
                raise DiskError(str(exc)) from exc
            session.invalidate_cached_views()
            if session.kind == "dms":
                try:
                    session.dms = parse_dms(session.path.read_bytes())
                except DMSError as exc:
                    raise DiskError(str(exc)) from exc
            self._persist_session(session)
            return restored

    def undo_last_change(self, session: ImageSession) -> dict:
        with session.lock:
            latest = self.checkpoints.latest_automatic(session)
            if latest is None:
                raise DiskError("There is no automatic checkpoint to undo.")
            restored = self.restore_checkpoint(session, latest["id"])
            try:
                self.checkpoints.delete(session, latest["id"])
            except CheckpointError as exc:
                raise DiskError(str(exc)) from exc
            return restored

    def delete_checkpoint(self, session: ImageSession, checkpoint_id: str) -> None:
        with session.lock:
            try:
                self.checkpoints.delete(session, checkpoint_id)
            except CheckpointError as exc:
                raise DiskError(str(exc)) from exc

    def summary(self, session: ImageSession) -> dict:
        checkpoints = self.list_checkpoints(session)
        kickfs = self.kickfs_details(session) if session.kind == "kickfs" else None
        image_stat = session.path.stat()
        image_size = image_stat.st_size
        file_policy = session_name_policy(session)
        partition_policy = target_name_policy("hdf", item_type="partition")
        return {
            "id": session.id,
            "name": session.name,
            "kind": session.kind,
            "size": image_size,
            "revision": f"{image_size:x}-{image_stat.st_mtime_ns:x}",
            "hardDisk": self.is_bare_hard_drive(session, image_size),
            "dirty": session.dirty,
            "hasDescriptor": bool(session.descriptor_path),
            "descriptorName": session.descriptor_name,
            "doubleSided": self.is_two_volume_image(session),
            "containerFormat": "hfe" if session.hfe_original_path else "scp" if session.scp_original_path else None,
            "readOnly": session.hfe_read_only or session.scp_read_only or bool(kickfs and kickfs["readOnly"]),
            "exportFormats": self.export_formats(session),
            "rom": ({
                "bankSize": session.rom_bank_size,
                "bankCount": bank_count(image_size, session.rom_bank_size),
                "eraseByte": session.rom_erase_byte,
                "platform": session.rom_platform,
                "layout": session.rom_layout,
                "componentNames": session.rom_component_names,
                "project": session.rom_project,
            } if session.kind == "rom" else None),
            "kickfs": kickfs,
            "filesystemCapabilities": session.ffs_capabilities or None,
            "filenamePolicies": {
                "file": file_policy.public_contract(),
                "disk": partition_policy.public_contract() if session.kind == "hdf" else None,
            },
            "targetHardware": session.target_hardware,
            "hardwareProfile": session.hardware_profile,
            "warnings": [
                *self._normalise_warnings(session.warnings),
                *(list(session.dms.warnings) if session.dms else []),
            ],
            "checkpoints": {
                "total": len(checkpoints),
                "named": sum(not item["automatic"] for item in checkpoints),
                "canUndo": any(item["automatic"] for item in checkpoints),
            },
        }
