"""Creating hard drives and changing their partition tables.

Three kinds of drive are made here. A partitioned drive carries a Rigid Disk
Block, several volumes and the handlers they need. A bare volume is one filing
system from the first block, which is what an emulator calls a hardfile. And a
card attached through USB can be given either, in place, so that whatever is
installed afterwards goes straight onto the thing that will be put in the
Amiga.

A drive image is a sparse file. It reports the size of the drive it describes
and occupies only what has been written to it, which is what lets a desktop
machine hold the image of a card larger than its own free space.
"""

from __future__ import annotations

import fcntl
import os
import shutil
import uuid
from pathlib import Path

from . import drive_layout, filesystem_handlers
from . import progress as progress_module
from .errors import DiskError
from .image_session import ImageSession

#: Ask the kernel to read a drive's partition table again.
BLKRRPART = 0x125F


def _engine():
    try:
        from amiganut.filesystem import drive, rdb
        from amiganut.filesystem.blocks import BlockReader
    except ImportError as exc:  # pragma: no cover - packaging failure
        raise DiskError("The Amiganut drive API is unavailable.") from exc
    return drive, rdb, BlockReader


class DriveLayoutMixin:
    """Create drives from a layout and edit the partition table of one."""

    # ---- creating ------------------------------------------------------
    def create_drive(
        self,
        title: str,
        drive_bytes,
        rows,
        progress: progress_module.Progress | None = None,
    ) -> ImageSession:
        """Create a partitioned drive image and open it as a working session."""
        drive, _rdb, _reader = _engine()
        planned = drive_layout.require_plan(drive_bytes, rows)
        handlers, _missing = filesystem_handlers.engine_handlers(
            [drive.dos_type_for(row["filesystem"]) for row in planned["partitions"]]
        )
        self._require_working_room(planned["driveBytes"])
        image_id = uuid.uuid4().hex
        folder = self.work_dir / image_id
        folder.mkdir()
        name = f"{self.safe_filename(str(title or '').strip()) or 'HardDrive'}.hdf"
        path = folder / name
        try:
            drive.create_drive(
                path,
                planned["driveBytes"],
                drive_layout.engine_partitions(planned),
                handlers=handlers,
                progress=progress_module.reporter(progress),
            )
            session = ImageSession(
                image_id,
                path.name,
                "hdf",
                path,
                dirty=True,
                target_hardware="amigaos",
            )
            for warning in planned["warnings"]:
                self._append_warning(session, warning)
        except DiskError:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        except Exception as exc:
            shutil.rmtree(folder, ignore_errors=True)
            raise DiskError(self._friendly_engine_error(str(exc))) from exc
        with self._lock:
            self.sessions[session.id] = session
        self._persist_session(session)
        return session

    def create_bare_volume(
        self,
        title: str,
        drive_bytes,
        filesystem: str,
    ) -> ImageSession:
        """Create one volume with no partition table, in any filing system.

        The FastFileSystem variants are made by the older route, which also
        writes an emulator's geometry sidecar. This is for the filing systems
        that name themselves in their first block and need no geometry.
        """
        drive, _rdb, _reader = _engine()
        chosen = drive_layout.filesystem(filesystem)
        if chosen["family"] == "ffs":
            raise DiskError("An FFS volume is created as a hardfile or a raw drive image.")
        size = drive_layout.parse_size(drive_bytes, what="volume size")
        size -= size % 512
        if size < drive_layout.MIB:
            raise DiskError("A volume in this filing system needs at least 1 MiB.")
        if size > chosen["largest"]:
            raise DiskError(
                f"The {chosen['label']} stops at "
                f"{drive_layout.human_size(chosen['largest'])} for one volume."
            )
        label = str(title or "Empty").strip()[:30] or "Empty"
        self._require_working_room(size)
        image_id = uuid.uuid4().hex
        folder = self.work_dir / image_id
        folder.mkdir()
        path = folder / f"{self.safe_filename(label) or 'Volume'}.hdf"
        try:
            drive.create_volume(path, size, chosen["id"], label)
            session = ImageSession(
                image_id,
                path.name,
                self.identify_kind(path, "ffs"),
                path,
                dirty=True,
                target_hardware="auto",
            )
            self.refresh_ffs_capabilities(session)
            self._append_warning(
                session,
                f"This is one {chosen['label']} volume with no partition table, "
                "so nothing on it can carry the handler. The machine or "
                "emulator that mounts it has to be given the handler and the "
                "geometry separately. A partitioned drive carries both.",
            )
        except DiskError:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        except Exception as exc:
            shutil.rmtree(folder, ignore_errors=True)
            raise DiskError(self._friendly_engine_error(str(exc))) from exc
        with self._lock:
            self.sessions[session.id] = session
        self._persist_session(session)
        return session

    def _require_working_room(self, drive_bytes: int) -> None:
        """Refuse a drive the working storage could not hold even when empty.

        A new drive occupies a few megabytes however large it is, so the test
        is for a host whose working storage cannot hold sparse files at all,
        where the image would take its full size at once.
        """
        try:
            free = shutil.disk_usage(self.work_dir).free
        except OSError:
            return
        probe = self.work_dir / f".sparse-probe-{uuid.uuid4().hex}"
        try:
            with probe.open("wb") as handle:
                handle.truncate(64 * 1024 * 1024)
            sparse = probe.stat().st_blocks * 512 < 1024 * 1024
        except OSError:
            sparse = False
        finally:
            probe.unlink(missing_ok=True)
        if sparse:
            needed = min(drive_bytes, 64 * 1024 * 1024)
        else:
            needed = drive_bytes
        if free < needed:
            raise DiskError(
                f"The working storage has {drive_layout.human_size(free)} free"
                + (
                    ", which is not enough to start a new drive."
                    if sparse
                    else f" and cannot hold sparse files, so a drive of "
                    f"{drive_layout.human_size(drive_bytes)} does not fit."
                )
            )

    # ---- describing ----------------------------------------------------
    def drive_layout(self, session: ImageSession) -> dict:
        """Describe a drive's table, its unused space and what it lacks."""
        if session.kind != "hdf":
            raise DiskError("This image is not a partitioned hard drive.")
        drive, rdb, BlockReader = _engine()
        with session.lock:
            try:
                with BlockReader(session.path) as reader:
                    disk = rdb.read_rigid_disk(reader)
                    media_bytes = reader.total_blocks * reader.block_size
            except Exception as exc:
                raise DiskError(self._friendly_engine_error(str(exc))) from exc
        described = disk.cylinders * disk.cylinder_bytes
        missing = []
        for dos_type in drive.missing_handlers(disk.partitions, disk.handlers):
            family = filesystem_handlers.family_for(dos_type)
            missing.append({
                "dosType": dos_type.decode("latin-1"),
                "family": family.key if family else "",
                "label": family.label if family else "an unknown filing system",
                "available": bool(family and filesystem_handlers.load(family.key)),
            })
        report = disk.to_dict()
        report.update({
            "mediaBytes": media_bytes,
            "describedBytes": described,
            "beyondTableBytes": max(0, media_bytes - media_bytes % disk.cylinder_bytes - described),
            "missingHandlers": missing,
            "legacyLayout": disk.legacy_layout,
            "editable": disk.bad_block_list in (0, 0xFFFFFFFF)
            and disk.drive_init in (0, 0xFFFFFFFF),
        })
        return report

    # ---- changing ------------------------------------------------------
    def _change_table(self, session: ImageSession, change):
        """Run one change to the partition table and tidy the session after it."""
        if session.kind != "hdf":
            raise DiskError("This image is not a partitioned hard drive.")
        self.require_writable_geometry(session)
        with session.lock:
            try:
                result = change()
            except DiskError:
                raise
            except Exception as exc:
                raise DiskError(self._friendly_engine_error(str(exc))) from exc
            # The pane may have had a partition open that has moved in the
            # list or is no longer there, so it goes back to the table.
            session.partition = None
            session.ffs_capabilities = {}
            self._mark_mutated(session)
            self._persist_session(session)
        return result

    def _handlers_for(self, filesystem_id: str) -> list:
        drive, _rdb, _reader = _engine()
        chosen = drive_layout.filesystem(filesystem_id)
        handlers, missing = filesystem_handlers.engine_handlers(
            [drive.dos_type_for(chosen["id"])]
        )
        if missing and missing[0]["family"] != "ffs":
            raise DiskError(
                f"No {missing[0]['label']} handler has been supplied, so the "
                f"machine could not mount the partition. Add {missing[0]['name']} "
                "under Filing-system handlers first."
            )
        return handlers

    def _check_partition_fits(self, filesystem_id: str, size: int, name: str) -> None:
        chosen = drive_layout.filesystem(filesystem_id)
        if size <= chosen["largest"]:
            return
        if chosen["family"] == "ffs":
            raise DiskError(
                f"{name} would be {drive_layout.human_size(size)}, and an FFS "
                f"partition is kept to "
                f"{drive_layout.human_size(drive_layout.LARGEST_FFS_PARTITION)} here. "
                "Use the Professional File System for a partition this size."
            )
        raise DiskError(
            f"{name} would be {drive_layout.human_size(size)}, and the "
            f"{chosen['label']} stops at "
            f"{drive_layout.human_size(chosen['largest'])} for one partition."
        )

    def add_partition(self, session: ImageSession, row: dict) -> dict:
        """Add a partition in a drive's unused space and format it."""
        drive, _rdb, _reader = _engine()
        cleaned = drive_layout.normalise_rows([row])[0]
        layout = self.drive_layout(session)
        if not layout["editable"]:
            raise DiskError(
                "This drive's partition table carries a bad-block list or drive "
                "initialisation code, which this build cannot rewrite safely."
            )
        ranges = layout["freeRanges"]
        if not ranges:
            raise DiskError("Every cylinder of this drive belongs to a partition already.")
        wanted = row.get("lowCylinder")
        if wanted in (None, ""):
            space = max(ranges, key=lambda item: item["sizeBytes"])
        else:
            space = next(
                (item for item in ranges if item["lowCylinder"] <= int(wanted) <= item["highCylinder"]),
                None,
            )
            if space is None:
                raise DiskError("That part of the drive is not free.")
        size = cleaned["sizeBytes"] or space["sizeBytes"]
        if size > space["sizeBytes"]:
            raise DiskError(
                f"There are {drive_layout.human_size(space['sizeBytes'])} unused "
                "there, which is less than the partition asks for."
            )
        self._check_partition_fits(cleaned["filesystem"], size, cleaned["name"])
        handlers = self._handlers_for(cleaned["filesystem"])
        spec = {
            **cleaned,
            "sizeBytes": cleaned["sizeBytes"],
            "lowCylinder": space["lowCylinder"],
        }
        created = self._change_table(
            session,
            lambda: drive.add_formatted_partition(session.path, spec, handlers=handlers),
        )
        return created.to_dict()

    def remove_partition(self, session: ImageSession, index: int) -> None:
        """Take a partition out of the table. Its blocks are left as they are."""
        _drive, rdb, BlockReader = _engine()

        def change():
            with BlockReader(session.path, writable=True) as reader:
                rdb.remove_partition(reader, int(index))
                reader.sync()

        self._change_table(session, change)

    def format_partition(
        self,
        session: ImageSession,
        index: int,
        label: str,
        filesystem: str | None = None,
    ) -> dict:
        """Empty a partition, optionally changing its filing system."""
        drive, _rdb, _reader = _engine()
        partitions = self.list_partitions(session)
        if not 0 <= int(index) < len(partitions):
            raise DiskError("There is no such partition on this drive.")
        current = partitions[int(index)]
        name = str(current.get("name") or f"DH{index}")
        # Only the volume name and the filing system are being chosen, so the
        # device name the drive already has is not judged again here.
        cleaned = drive_layout.normalise_rows([{
            "name": "DH0",
            "label": label or name,
            "filesystem": filesystem or self._filesystem_id_for(current),
        }])[0]
        self._check_partition_fits(
            cleaned["filesystem"], int(current.get("sizeBytes") or 0), name
        )
        handlers = self._handlers_for(cleaned["filesystem"])
        formatted = self._change_table(
            session,
            lambda: drive.reformat_partition(
                session.path,
                int(index),
                cleaned["label"],
                cleaned["filesystem"] if filesystem else None,
                handlers=handlers,
            ),
        )
        return formatted.to_dict()

    @staticmethod
    def _filesystem_id_for(partition: dict) -> str:
        """Name the editor's filing system for a partition's DOS type."""
        drive, _rdb, _reader = _engine()
        dos_type = str(partition.get("dosType") or "").encode("latin-1")
        for entry in drive_layout.FILESYSTEMS:
            if drive.dos_type_for(entry["id"]) == dos_type:
                return entry["id"]
        if dos_type[:3] in (b"PFS", b"PDS"):
            return "pds3" if dos_type[:3] == b"PDS" else "pfs3"
        raise DiskError(
            "This partition's filing system is not one this build can create. "
            "Choose a filing system to format it with."
        )

    def change_partition(self, session: ImageSession, index: int, changes: dict) -> dict:
        """Rename a partition or change how it boots, without touching its volume."""
        _drive, rdb, BlockReader = _engine()
        allowed: dict = {}
        if "name" in changes:
            name = str(changes["name"] or "").strip()
            drive_layout.normalise_rows([{"name": name, "label": "x", "filesystem": "ffs-intl"}])
            allowed["name"] = name
        if "bootable" in changes:
            allowed["bootable"] = bool(changes["bootable"])
        if "automount" in changes:
            allowed["automount"] = bool(changes["automount"])
        if "bootPriority" in changes:
            try:
                priority = int(changes["bootPriority"])
            except (TypeError, ValueError) as exc:
                raise DiskError("A boot priority is a number from -128 to 127.") from exc
            if not -128 <= priority <= 127:
                raise DiskError("A boot priority is a number from -128 to 127.")
            allowed["bootPriority"] = priority
        if not allowed:
            raise DiskError("Nothing about the partition was changed.")

        def change():
            with BlockReader(session.path, writable=True) as reader:
                part = rdb.change_partition(reader, int(index), allowed)
                reader.sync()
                return part.to_dict()

        return self._change_table(session, change)

    def extend_drive(self, session: ImageSession) -> dict:
        """Let the table describe the whole of the card or file it sits on."""
        _drive, rdb, BlockReader = _engine()

        def change():
            with BlockReader(session.path, writable=True) as reader:
                before = rdb.read_rigid_disk(reader).cylinders
                disk = rdb.extend_to_media(reader)
                reader.sync()
                if disk.cylinders == before:
                    raise DiskError(
                        "The partition table already describes the whole drive."
                    )
                return disk.to_dict()

        return self._change_table(session, change)

    def resize_drive_image(self, session: ImageSession, drive_bytes) -> dict:
        """Make a drive image larger, so that partitions can be added to it."""
        if session.attached_device:
            raise DiskError("A real drive is the size it is.")
        if session.kind != "hdf":
            raise DiskError("This image is not a partitioned hard drive.")
        size = drive_layout.parse_size(drive_bytes, what="drive size")
        size -= size % 512
        current = session.path.stat().st_size
        if size <= current:
            raise DiskError(
                "A drive image can be made larger, not smaller: its last "
                "partition ends where the image does."
            )
        if size > drive_layout.LARGEST_DRIVE:
            raise DiskError("A drive can hold at most 2 TB, which is as far as AmigaDOS counts.")
        self._require_working_room(size)
        with session.lock:
            with session.path.open("r+b") as handle:
                handle.truncate(size)
        return self.extend_drive(session)

    def embed_handlers(self, session: ImageSession) -> list[str]:
        """Give a drive the handlers its partitions need and its table lacks."""
        drive, rdb, BlockReader = _engine()
        layout = self.drive_layout(session)
        wanted = [row for row in layout["missingHandlers"] if row["available"]]
        if not wanted:
            raise DiskError(
                "There is no handler to add. Either the drive carries every "
                "handler its partitions need, or the one it lacks has not been "
                "supplied under Filing-system handlers."
            )

        def change():
            added = []
            with BlockReader(session.path, writable=True) as reader:
                for row in wanted:
                    dos_type = row["dosType"].encode("latin-1")
                    binary = filesystem_handlers.load(row["family"])
                    rdb.set_handler(reader, drive.make_handler(dos_type, binary))
                    added.append(row["label"])
                reader.sync()
            return added

        return self._change_table(session, change)

    # ---- real drives ---------------------------------------------------
    @staticmethod
    def _reread_partition_table(device: str) -> None:
        """Tell the host the drive's partitions have changed, if it will listen."""
        try:
            descriptor = os.open(device, os.O_RDONLY)
        except OSError:
            return
        try:
            fcntl.ioctl(descriptor, BLKRRPART)
        except OSError:
            pass
        finally:
            os.close(descriptor)

    def initialise_attached_drive(
        self,
        device: str,
        rows=None,
        *,
        volume: dict | None = None,
        progress: progress_module.Progress | None = None,
    ) -> dict:
        """Give a drive attached to the host a new table, or one bare volume.

        Everything the drive held is given up. The caller has already checked
        that the drive is the one the user confirmed, that the host has none
        of it mounted and that no pane has it open.
        """
        drive, _rdb, BlockReader = _engine()
        report = progress_module.reporter(progress)
        try:
            with BlockReader(device) as reader:
                media_bytes = reader.total_blocks * reader.block_size
        except OSError as exc:
            raise DiskError(f"The drive could not be read: {exc.strerror or exc}.") from exc
        except Exception as exc:
            raise DiskError(self._friendly_engine_error(str(exc))) from exc
        if not os.access(device, os.W_OK):
            raise DiskError(
                "Linux has not given this account permission to write to the "
                "drive. Install the udev rule that comes with Amiga File Forge, "
                "then attach the drive again."
            )
        try:
            if volume is not None:
                chosen = drive_layout.filesystem(volume.get("filesystem"))
                if media_bytes > chosen["largest"]:
                    raise DiskError(
                        f"The drive is {drive_layout.human_size(media_bytes)}, and "
                        + (
                            "an FFS volume is kept to "
                            f"{drive_layout.human_size(drive_layout.LARGEST_FFS_PARTITION)} "
                            "here. Use the Professional File System, or give the "
                            "drive partitions."
                            if chosen["family"] == "ffs"
                            else f"the {chosen['label']} stops at "
                            f"{drive_layout.human_size(chosen['largest'])}."
                        )
                    )
                label = drive_layout.normalise_rows([{
                    "name": "DH0",
                    "label": volume.get("label") or "Empty",
                    "filesystem": chosen["id"],
                }])[0]["label"]
                report("Formatting the drive", 0, 1)
                drive.create_volume(device, None, chosen["id"], label)
                report("The drive is ready", 1, 1)
                result = {"kind": "volume", "filesystem": chosen["label"], "label": label}
            else:
                planned = drive_layout.require_plan(media_bytes, rows)
                handlers, _missing = filesystem_handlers.engine_handlers(
                    [drive.dos_type_for(row["filesystem"]) for row in planned["partitions"]]
                )
                _disk, formatted = drive.initialise_drive(
                    device,
                    drive_layout.engine_partitions(planned),
                    handlers=handlers,
                    progress=report,
                )
                result = {
                    "kind": "drive",
                    "partitions": [item.to_dict() for item in formatted],
                    "warnings": planned["warnings"],
                }
        except DiskError:
            raise
        except OSError as exc:
            raise DiskError(
                f"The drive could not be written: {exc.strerror or exc}. It may "
                "be part way through and should be initialised again."
            ) from exc
        except Exception as exc:
            raise DiskError(self._friendly_engine_error(str(exc))) from exc
        self._reread_partition_table(device)
        result["mediaBytes"] = media_bytes
        return result

    def save_scopes(self, session: ImageSession) -> list[dict]:
        """List the shapes a working drive image can be saved to a file in."""
        size = session.path.stat().st_size
        if session.kind == "hdf":
            scopes = [{
                "scope": "image",
                "label": "The whole drive, as it is",
                "bytes": size,
                "suffix": ".hdf",
                "sidecar": False,
            }]
            for index, row in enumerate(self.list_partitions(session)):
                scopes.append({
                    "scope": f"partition:{index}",
                    "label": f"Partition {row.get('name') or index} alone, as a hardfile with its .geo",
                    "bytes": int(row.get("sizeBytes") or 0),
                    "suffix": ".hdf",
                    "sidecar": True,
                })
            return scopes
        scopes = [{
            "scope": "image",
            "label": "The volume as it is, with no partition table",
            "bytes": size,
            "suffix": session.path.suffix or ".hdf",
            "sidecar": bool(session.descriptor_path),
        }]
        if self.is_bare_hard_drive(session, size):
            scopes.append({
                "scope": "drive",
                "label": "As a partitioned drive, with a Rigid Disk Block and the handler it needs",
                "bytes": size,
                "suffix": ".hdf",
                "sidecar": False,
            })
        return scopes

    def save_drive_image(
        self,
        session: ImageSession,
        destination: Path,
        progress: progress_module.Progress | None = None,
        scope: str = "image",
    ) -> dict:
        """Save a working drive image to a file on this machine, sparsely.

        The usual save builds a ZIP of the image, which for a drive of a
        hundred gigabytes would write a hundred gigabytes. This writes only
        what the image holds, to a file that reports the drive's full size.
        One partition can be saved on its own, and a volume with no partition
        table can be saved as a drive that has one.
        """
        if session.attached_device:
            raise DiskError("A drive opened in place is exported, not saved.")
        drive, _rdb, _reader = _engine()
        from .hardfile_geometry import format_geometry

        chosen = next(
            (entry for entry in self.save_scopes(session) if entry["scope"] == str(scope or "image")),
            None,
        )
        if chosen is None:
            raise DiskError("Choose what to save.")
        report = progress_module.reporter(progress)
        mib = 1024 * 1024

        def copied(done: int, total: int) -> None:
            report(
                f"Copied {done / 1e9:.1f} of {total / 1e9:.1f} GB",
                done // mib,
                max(1, -(-total // mib)),
            )

        partial = destination.with_name(destination.name + ".part")
        sidecar = None
        with session.lock:
            source = self.prepare_download(session, progress)
            report("Saving the image", 0, 1)
            try:
                if chosen["scope"] == "image":
                    # The host's own copy knows which parts of the image are
                    # holes, so it neither reads nor writes them.
                    self._copy_local_file(source, partial)
                    if session.descriptor_path and session.descriptor_path.is_file():
                        sidecar = destination.with_name(destination.name + ".geo")
                        shutil.copyfile(session.descriptor_path, sidecar)
                elif chosen["scope"] == "drive":
                    dos_type = drive.volume_dos_type(source)
                    handlers, missing = filesystem_handlers.engine_handlers([dos_type])
                    if missing and missing[0]["family"] != "ffs":
                        raise DiskError(
                            f"No {missing[0]['label']} handler has been supplied, so "
                            "the drive could not be mounted. Add it under "
                            "Filing-system handlers first."
                        )
                    drive.wrap_volume(
                        source,
                        partial,
                        name=self._rdb_device_name(session),
                        handlers=handlers,
                        progress=copied,
                    )
                else:
                    index = int(chosen["scope"].split(":", 1)[1])
                    partition = drive.extract_partition(source, index, partial, progress=copied)
                    sidecar = destination.with_suffix(".geo")
                    sidecar.write_text(
                        format_geometry(
                            surfaces=partition.surfaces,
                            blocks_per_track=partition.blocks_per_track,
                            cylinders=partition.high_cylinder - partition.low_cylinder + 1,
                            block_size=partition.block_size,
                        ),
                        encoding="ascii",
                    )
                with partial.open("r+b") as handle:
                    os.fsync(handle.fileno())
                written = partial.stat().st_blocks * 512
                length = partial.stat().st_size
                partial.replace(destination)
            except DiskError:
                partial.unlink(missing_ok=True)
                raise
            except OSError as exc:
                partial.unlink(missing_ok=True)
                raise DiskError(
                    f"The image could not be saved: {exc.strerror or exc}."
                ) from exc
            except Exception as exc:
                partial.unlink(missing_ok=True)
                raise DiskError(self._friendly_engine_error(str(exc))) from exc
            if chosen["scope"] == "image":
                self.mark_saved(session)
            report("The image has been saved", 1, 1)
        return {
            "path": str(destination),
            "scope": chosen["scope"],
            "bytes": length,
            "written": min(written, length),
            "geometry": str(sidecar) if sidecar else None,
        }


__all__ = ["DriveLayoutMixin"]
