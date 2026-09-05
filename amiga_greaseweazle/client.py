"""Safe subprocess adapter for writing Amiga images with Greaseweazle.

The module deliberately has no Flask, GTK or Nautilus dependencies. Both
Amiga File Forge and a file-manager extension can therefore use the same
probe, validation, progress and verification policy.
"""

from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator


class GreaseweazleError(RuntimeError):
    """A user-facing hardware, media or command failure."""


@dataclass(frozen=True)
class ImageFormat:
    suffix: str
    label: str
    automatic_verification: bool


@dataclass(frozen=True)
class ProbeResult:
    available: bool
    command: str | None
    detail: str


@dataclass(frozen=True)
class ReadResult:
    drive: str
    image: str
    tracks_read: int
    size: int
    output_tail: tuple[str, ...]


@dataclass(frozen=True)
class WriteResult:
    drive: str
    image: str
    verified: bool
    verification_supported: bool
    tracks_written: int
    output_tail: tuple[str, ...]


#: What Greaseweazle can put on, or take off, a real Amiga floppy. A sector
#: image can be verified by reading the disk back and comparing it; a flux
#: capture cannot, because two reads of the same disk are never bit-identical.
IMAGE_FORMATS = {
    ".adf": ImageFormat(".adf", "AmigaDOS sector image", True),
    ".adz": ImageFormat(".adz", "Compressed AmigaDOS sector image", True),
    ".dms": ImageFormat(".dms", "DiskMasher archive", True),
    ".hfe": ImageFormat(".hfe", "HFE flux-level disk", False),
    ".scp": ImageFormat(".scp", "SuperCard Pro flux capture", False),
    ".ipf": ImageFormat(".ipf", "SPS preservation image", False),
}
DRIVE_CHOICES = ("A", "B", "0", "1", "2", "3")
_DRIVE_PATTERN = re.compile(r"[A-Za-z0-9]+")
_TRACK_PATTERN = re.compile(r"^\s*T(\d+)\.(\d+):")
_GEOMETRY_PATTERN = re.compile(r"(?:Writing|Reading) c=(\d+)-(\d+):h=(\d+)-(\d+)", re.I)


def image_format(path_or_name: str | Path) -> ImageFormat:
    suffix = Path(path_or_name).suffix.casefold()
    try:
        return IMAGE_FORMATS[suffix]
    except KeyError as exc:
        supported = ", ".join(sorted(IMAGE_FORMATS))
        raise GreaseweazleError(
            f"Greaseweazle writing supports {supported}; {suffix or 'this file'} is not a floppy image."
        ) from exc


@contextmanager
def stable_snapshot(source: str | Path, directory: str | Path | None = None) -> Iterator[Path]:
    """Copy a stable image snapshot so later edits cannot alter a live write."""
    source_path = Path(source)
    if not source_path.is_file():
        raise GreaseweazleError(f"The image to write no longer exists: {source_path}")
    before = source_path.stat()
    temporary = tempfile.NamedTemporaryFile(
        prefix="amiga-floppy-",
        suffix=source_path.suffix.lower(),
        dir=directory,
        delete=False,
    )
    snapshot = Path(temporary.name)
    try:
        with temporary, source_path.open("rb") as stream:
            shutil.copyfileobj(stream, temporary, length=1024 * 1024)
        after = source_path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise GreaseweazleError(
                "The working image changed while its physical-write snapshot was being made. Retry after the current edit finishes."
            )
        yield snapshot
    finally:
        snapshot.unlink(missing_ok=True)


class GreaseweazleClient:
    """Discover and run the official ``gw`` command without invoking a shell."""

    def __init__(self, command: str | None = None, timeout: float = 1800) -> None:
        self.command = command or shutil.which("gw")
        self.timeout = float(timeout)

    @staticmethod
    def _environment() -> dict[str, str]:
        return {
            "HOME": os.environ.get("HOME", "/tmp"),
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        }

    def probe(self) -> ProbeResult:
        if not self.command:
            return ProbeResult(
                False,
                None,
                "The gw command is not installed. Install the official Greaseweazle tools and reconnect the device.",
            )
        try:
            result = subprocess.run(
                [self.command, "info"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=10,
                env=self._environment(),
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return ProbeResult(False, self.command, f"Could not query Greaseweazle: {exc}")
        detail = (result.stdout or "").strip()
        if result.returncode:
            return ProbeResult(
                False,
                self.command,
                detail or "Greaseweazle did not find an accessible device. Check USB access and udev rules.",
            )
        return ProbeResult(True, self.command, detail or "Greaseweazle device detected.")

    @staticmethod
    def _drive(value: str) -> str:
        drive = str(value or "").strip().upper()
        if drive not in DRIVE_CHOICES or not _DRIVE_PATTERN.fullmatch(drive):
            raise GreaseweazleError("Choose Greaseweazle drive A, B, 0, 1, 2 or 3.")
        return drive

    def _stream(
        self,
        command: list[str],
        report,
        *,
        activity: str,
        limit_message: str,
    ) -> tuple[int, list[str], set[tuple[int, int]], int | None]:
        """Run one gw command, following its per-track progress as it goes.

        Reading and writing differ only in the command and how the result is
        judged, so the process handling, cancellation boundary, timeout and
        track accounting live here once.
        """
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=self._environment(),
            )
        except OSError as exc:
            raise GreaseweazleError(f"Could not start Greaseweazle: {exc}") from exc

        lines: queue.Queue[str | None] = queue.Queue()

        def read_output() -> None:
            assert process.stdout is not None
            try:
                for line in process.stdout:
                    lines.put(line.rstrip())
            finally:
                lines.put(None)

        reader = threading.Thread(target=read_output, name="greaseweazle-output", daemon=True)
        reader.start()
        deadline = time.monotonic() + self.timeout
        output: list[str] = []
        tracks: set[tuple[int, int]] = set()
        total: int | None = None
        try:
            finished_output = False
            while not finished_output:
                if time.monotonic() >= deadline:
                    raise GreaseweazleError(limit_message)
                try:
                    line = lines.get(timeout=0.25)
                except queue.Empty:
                    # Calling progress is also the cooperative cancellation boundary.
                    report(activity, len(tracks), total)
                    continue
                if line is None:
                    finished_output = True
                    continue
                if line:
                    output.append(line)
                geometry = _GEOMETRY_PATTERN.search(line)
                if geometry:
                    first_cylinder, last_cylinder, first_head, last_head = map(int, geometry.groups())
                    total = (last_cylinder - first_cylinder + 1) * (last_head - first_head + 1)
                track = _TRACK_PATTERN.match(line)
                if track:
                    tracks.add((int(track.group(1)), int(track.group(2))))
                report(line or activity, len(tracks), total)
            return_code = process.wait(timeout=5)
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            raise
        finally:
            if process.stdout is not None:
                process.stdout.close()
            reader.join(timeout=1)
        return return_code, output, tracks, total

    def _ready_command(self) -> str:
        probe = self.probe()
        if not probe.available or not probe.command:
            raise GreaseweazleError(probe.detail)
        return probe.command

    def read(
        self,
        destination: str | Path,
        drive: str,
        progress: Callable[[str, int | None, int | None], None] | None = None,
        *,
        revolutions: int | None = None,
    ) -> ReadResult:
        """Capture a physical disk into an image file.

        The destination suffix selects what gw produces, so an ``.scp`` target
        captures flux and a sector suffix such as ``.adf`` or ``.adf`` decodes
        as it reads. The file is only returned once gw has exited cleanly and
        left a non-empty image behind.
        """
        path = Path(destination)
        image_type = image_format(path)
        selected_drive = self._drive(drive)
        command = self._ready_command()
        report = progress or (lambda _message, _current=None, _total=None: None)
        arguments = [command, "read", f"--drive={selected_drive}"]
        if revolutions is not None:
            if not 1 <= int(revolutions) <= 10:
                raise GreaseweazleError("Choose between 1 and 10 revolutions per track.")
            arguments.append(f"--revs={int(revolutions)}")
        arguments.append(str(path))
        report(f"Starting physical read on drive {selected_drive}", 0, None)
        return_code, output, tracks, total = self._stream(
            arguments,
            report,
            activity="Reading physical floppy",
            limit_message=(
                "Greaseweazle exceeded the 30 minute read limit. The capture was abandoned."
            ),
        )
        if return_code:
            tail = "\n".join(output[-12:])
            path.unlink(missing_ok=True)
            raise GreaseweazleError(
                "Greaseweazle could not read the physical disk."
                + (f"\n\n{tail}" if tail else "")
            )
        if not path.is_file() or not path.stat().st_size:
            raise GreaseweazleError(
                "Greaseweazle finished without producing an image. Check that a disk is inserted "
                "and that the drive is selected correctly."
            )
        size = path.stat().st_size
        report(
            f"Physical disk captured as {image_type.label}",
            total or len(tracks),
            total or len(tracks),
        )
        return ReadResult(
            drive=selected_drive,
            image=path.name,
            tracks_read=len(tracks),
            size=size,
            output_tail=tuple(output[-12:]),
        )

    def write(
        self,
        image: str | Path,
        drive: str,
        progress: Callable[[str, int | None, int | None], None] | None = None,
    ) -> WriteResult:
        path = Path(image)
        image_type = image_format(path)
        selected_drive = self._drive(drive)
        command = self._ready_command()
        report = progress or (lambda _message, _current=None, _total=None: None)
        report(f"Starting physical write on drive {selected_drive}", 0, None)
        return_code, output, tracks, total = self._stream(
            [command, "write", f"--drive={selected_drive}", str(path)],
            report,
            activity="Writing physical floppy",
            limit_message=(
                "Greaseweazle exceeded the 30 minute write limit. "
                "The physical disk may be incomplete."
            ),
        )
        transcript = "\n".join(output)
        folded_transcript = transcript.casefold()
        if return_code:
            tail = "\n".join(output[-12:])
            raise GreaseweazleError(
                "Greaseweazle could not complete the physical write. The disk may be incomplete."
                + (f"\n\n{tail}" if tail else "")
            )
        if "verify failure" in folded_transcript:
            raise GreaseweazleError(
                "Greaseweazle wrote the disk but verification failed. Do not rely on this physical copy."
            )
        verified = "all tracks verified" in folded_transcript
        if image_type.automatic_verification and not verified:
            raise GreaseweazleError(
                "Greaseweazle finished without confirming that all tracks verified. Treat the physical disk as unverified."
            )
        report(
            "Physical disk written and verified" if verified else "Physical disk written; HFE verification is not available",
            total or len(tracks),
            total or len(tracks),
        )
        return WriteResult(
            drive=selected_drive,
            image=path.name,
            verified=verified,
            verification_supported=image_type.automatic_verification,
            tracks_written=len(tracks),
            output_tail=tuple(output[-12:]),
        )
