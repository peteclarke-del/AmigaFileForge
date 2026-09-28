"""Handing a drive to the emulator so that what it does there is kept.

An installer run under emulation writes to the drive it is given. For that to
be worth anything the drive has to be the image in the pane, not a copy that
is thrown away when the emulator closes, and it has to be somewhere the
emulator is allowed to read.

The second part is not a formality. FS-UAE installed as a snap is confined to
the visible part of the home directory and to its own folder. The workbench
keeps its images under ``~/.local/share``, which is hidden, so a confined
FS-UAE given a path there opens its window and finds no drive. What it can
always read is ``~/snap/fsuae/common``. A hard link there is the same file
under a second name: nothing is copied, a drive of a hundred gigabytes is
staged at once, and what the emulator writes is written to the image itself.

Where a hard link cannot be made, because the two places are on different
filing systems, the image is copied without its empty space and copied back
when the emulator closes.
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path
from typing import Callable

from .errors import DiskError

#: The folder a confined FS-UAE can always read, and its name for us in it.
SNAP_COMMON = Path.home() / "snap" / "fsuae" / "common"
STAGING_NAME = "amiga-file-forge"


def is_confined(executable: str | Path) -> bool:
    """Whether this emulator is a snap, and so cannot see everywhere."""
    text = str(executable or "")
    try:
        resolved = str(Path(text).resolve())
    except OSError:
        resolved = text
    return "/snap/" in text or resolved.startswith("/snap/") or resolved.endswith("/snap")


class StagedMedia:
    """The drive, and whatever goes with it, for one run of the emulator.

    Used as a context manager by the emulator's owner: entering stages the
    drive and marks the session as running, and leaving puts back what was
    copied, records that the image has changed and clears up.
    """

    def __init__(
        self,
        session,
        launch,
        *,
        executable: str | Path,
        work_dir: Path,
        copy_file: Callable[[Path, Path], None],
        writable: bool,
        finished: Callable[[object, bool], None] | None = None,
    ) -> None:
        self.session = session
        self.launch = launch
        self.writable = bool(writable)
        self.confined = is_confined(executable)
        self._copy_file = copy_file
        self._finished = finished
        self._copied_back: list[tuple[Path, Path]] = []
        root = SNAP_COMMON / STAGING_NAME if self.confined else Path(work_dir) / "emulator-media"
        self.folder = root / uuid.uuid4().hex
        self.drive: Path | None = None
        self._entered = False

    def _place(self, source: Path, name: str, *, keep_changes: bool) -> Path:
        """Put a file where the emulator can read it, as a link if possible."""
        self.folder.mkdir(parents=True, exist_ok=True)
        target = self.folder / name
        target.unlink(missing_ok=True)
        real = Path(os.path.realpath(source))
        try:
            os.link(real, target)
            return target
        except OSError:
            pass
        try:
            self._copy_file(real, target)
        except OSError as exc:
            raise DiskError(
                f"{source.name} could not be put where the emulator can read it: "
                f"{exc.strerror or exc}."
            ) from exc
        if keep_changes:
            self._copied_back.append((target, real))
        return target

    def attach(self, source: str | Path, name: str | None = None) -> Path:
        """Stage something that travels with the drive: a CD, or a boot volume.

        These are read by the emulator and not written back. They are staged
        even where the emulator could read the original, because the original
        may belong to a session that is closed while the emulator runs.
        """
        source = Path(source)
        return self._place(source, name or source.name, keep_changes=False)

    def __enter__(self):
        if getattr(self.session, "in_emulator", False):
            raise DiskError(
                "This drive is already attached to a running emulator. Close "
                "the emulator first."
            )
        source = Path(self.session.path)
        if self.confined:
            self.drive = self._place(
                source, f"drive{source.suffix or '.hdf'}", keep_changes=self.writable
            )
        else:
            self.drive = source
        self.session.in_emulator = True
        self._entered = True
        return self.launch, self.drive

    def __exit__(self, *_exception) -> None:
        if not self._entered:
            shutil.rmtree(self.folder, ignore_errors=True)
            return
        self._entered = False
        try:
            for staged, original in self._copied_back:
                try:
                    self._copy_file(staged, original)
                except OSError:
                    # The staged copy is the only one holding what the
                    # emulator wrote, so it is left where it is.
                    self.session.warnings.append(
                        f"What the emulator wrote could not be copied back. It is "
                        f"kept in {staged}."
                    )
                    return
            shutil.rmtree(self.folder, ignore_errors=True)
        finally:
            self.session.in_emulator = False
            if self._finished is not None:
                self._finished(self.session, self.writable)


__all__ = ["SNAP_COMMON", "STAGING_NAME", "StagedMedia", "is_confined"]
