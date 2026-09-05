"""Rigid Disk Block hard drives, addressed as the partitions they declare.

An Amiga hard drive describes itself. Block 0 carries an ``RDSK`` block, which
chains to one ``PART`` block per partition, and each of those names a device
(``DH0:``, ``Work:``) and gives the geometry and filing system of the volume
inside it. That is how a real machine finds its drives at boot, and it is the
only description this workbench trusts: nothing here assumes a fixed number of
partitions, a fixed partition size, or a fixed place for the partition table.

A drive is therefore opened as a list of partitions, and one of them is
selected. Everything downstream - listing, reading, editing, validating - then
works on that partition exactly as it works on a floppy, because a partition is
an ordinary AmigaDOS volume that happens to start part way into a larger file.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from .errors import DiskError

if TYPE_CHECKING:  # pragma: no cover - imported for type checkers only
    from .image_session import ImageSession


class RdbPartitionMixin:
    """Read a hard drive's partition table and mount one partition."""

    def rigid_disk(self, session: ImageSession) -> dict:
        """Return the drive's decoded Rigid Disk Block.

        The report is the drive's own description: its geometry, the vendor
        strings it carries, the filesystem drivers embedded in the RDB, and
        every partition with its device name, DOS type, size and boot
        priority.
        """
        if session.kind != "hdf":
            raise DiskError("This image is not a partitioned hard drive.")
        try:
            from amiganut.filesystem import reader_for
            from amiganut.filesystem.rdb import read_rigid_disk
        except ImportError as exc:  # pragma: no cover - packaging failure
            raise DiskError("The Amiganut Rigid Disk Block API is unavailable.") from exc

        with session.lock:
            reader = reader_for(session.path, writable=False)
            try:
                return read_rigid_disk(reader).to_dict()
            except Exception as exc:
                raise DiskError(self._friendly_engine_error(str(exc))) from exc
            finally:
                reader.close()

    def list_partitions(self, session: ImageSession) -> list[dict]:
        """Return every partition the drive declares, in RDB order."""
        return list(self.rigid_disk(session)["partitions"])

    def selected_partition(self, session: ImageSession) -> int:
        """Return the partition index in use, defaulting to the first one.

        A drive that has just been opened has no explicit selection. Falling
        back to partition zero matches what a machine does when it boots the
        highest-priority partition, and means every read path has a volume to
        work on without the caller having to choose first.
        """
        if session.partition is not None:
            return session.partition
        return 0

    def select_partition(self, session: ImageSession, index: int | None) -> int | None:
        """Choose which partition subsequent operations act on."""
        if session.kind != "hdf":
            raise DiskError("This image is not a partitioned hard drive.")
        if index is None:
            session.partition = None
            self._persist_session(session)
            return None
        partitions = self.list_partitions(session)
        chosen = int(index)
        if not 0 <= chosen < len(partitions):
            raise DiskError(
                f"This drive has {len(partitions)} partition(s), so there is no "
                f"partition {chosen}."
            )
        session.partition = chosen
        session.content_kind_cache.clear()
        self._persist_session(session)
        return chosen

    def partition_label(self, session: ImageSession) -> str:
        """Name the open partition the way Workbench would."""
        try:
            partitions = self.list_partitions(session)
        except DiskError:
            return ""
        index = self.selected_partition(session)
        if not 0 <= index < len(partitions):
            return ""
        return str(partitions[index].get("device") or partitions[index].get("name") or "")

    @contextmanager
    def rdb_mount(self, session: ImageSession, *, writable: bool = True):
        """Mount the selected partition as an ordinary AmigaDOS volume."""
        try:
            from amiganut.disc.mount import mount_image
        except ImportError as exc:  # pragma: no cover - packaging failure
            raise DiskError("The Amiganut mount API is unavailable.") from exc

        index = self.selected_partition(session)
        with session.lock:
            try:
                mount, _name = mount_image(
                    session.path, writable=writable, partition=index
                )
            except Exception as exc:
                raise DiskError(self._friendly_engine_error(str(exc))) from exc
            try:
                yield mount
            finally:
                close = getattr(mount, "close", None)
                if callable(close):
                    close()


__all__ = ["RdbPartitionMixin"]
