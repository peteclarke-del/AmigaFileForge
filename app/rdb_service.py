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


#: The DOS types there is a driver for: AmigaDOS OFS and FFS in all eight
#: variants, the Smart File System and the Professional File System.
READABLE_DOS_TYPES = frozenset(
    [bytes([68, 79, 83, variant]) for variant in range(8)]
    + [b"SFS\x00", b"PFS\x01", b"PFS\x02", b"PFS\x03", b"PDS\x03"]
)


def describe_dos_type(dos_type: bytes) -> str:
    """Spell a DOS type the way HDToolBox does, such as ``DOS\\3`` or ``MSD\\0``."""
    letters = "".join(
        chr(byte) if 32 <= byte < 127 else f"\\{byte}" for byte in dos_type[:3]
    )
    return f"{letters}\\{dos_type[3]}" if len(dos_type) == 4 else letters or "unknown"


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
            session.ffs_capabilities = {}
            self._persist_session(session)
            return None
        chosen = int(index)
        if chosen == session.partition and session.ffs_capabilities:
            # Every request names its partition; one already open needs no
            # second look at its filing system.
            return chosen
        partitions = self.list_partitions(session)
        if not 0 <= chosen < len(partitions):
            raise DiskError(
                f"This drive has {len(partitions)} partition(s), so there is no "
                f"partition {chosen}."
            )
        session.partition = chosen
        session.content_kind_cache.clear()
        self.refresh_ffs_capabilities(session)
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

    def _require_readable_partition(self, session: ImageSession, index: int) -> None:
        """Refuse a partition whose DOS type names a filing system with no driver.

        A drive can carry partitions for other systems, such as an MS-DOS or a
        UNIX partition next to the Amiga ones. Mounting one of those would
        fail deep inside the engine with a message about a missing root
        block, so the partition table's own record is checked first.
        """
        try:
            partitions = self.list_partitions(session)
        except DiskError:
            return
        if not 0 <= index < len(partitions):
            return
        dos_type = str(partitions[index].get("dosType") or "").encode("latin-1")
        if dos_type in READABLE_DOS_TYPES:
            return
        name = partitions[index].get("device") or f"partition {index}"
        raise DiskError(
            f"{name} is formatted with a filing system this build cannot read "
            f"(DOS type {describe_dos_type(dos_type)}). Nothing on it has been changed."
        )

    @contextmanager
    def rdb_mount(
        self, session: ImageSession, *, writable: bool = True, partition: int | None = None
    ):
        """Mount a partition as an ordinary volume, the selected one by default.

        Naming the partition lets one request read from one partition of a
        drive and write to another, as dragging between two panes on the same
        drive does, without the selection shared by both panes changing.
        """
        try:
            from amiganut.disc.mount import mount_image
        except ImportError as exc:  # pragma: no cover - packaging failure
            raise DiskError("The Amiganut mount API is unavailable.") from exc

        index = self.selected_partition(session) if partition is None else int(partition)
        self._require_readable_partition(session, index)
        writable = writable and self.allows_writes(session)
        with session.lock:
            try:
                # The session already knows this is a partitioned drive, so
                # nothing is gained by probing a drive of many gigabytes for
                # every other kind of image before each change.
                mount, _name = mount_image(
                    session.path, writable=writable, partition=index, filesystem="rdb"
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
