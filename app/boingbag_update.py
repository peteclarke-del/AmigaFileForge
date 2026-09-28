"""Applying a locked BoingBag by running its own Updater under emulation.

BoingBags 1 and 2 for AmigaOS 3.9 keep every system file they fix inside
``AmigaOS-Update``, a ZIP whose entries are all encrypted. The password is
inside Haage & Partner's ``Updater``, and their installer runs
``C/Updater AmigaOS-Update <target>``. So that command is run, on an emulated
Amiga, and what it writes is collected and installed with everything else.
The publisher's tool is given the publisher's archive and does the job it was
written for. The archive itself is never opened here.

This is the approach the PiStorm imager takes, and the things it learned by
watching the run are kept:

The machine starts from a copy of the system that is being installed, laid out
as a directory the emulator mounts as a drive. ``Updater`` is taken from the
pack, because a 3.9 system that has not been updated has none.

``Updater`` will not apply anything until it has seen the CD it is an update
for. What it looks for is a volume of that name, so the disc is attached to the
emulated machine, which mounts it under the name the disc carries.

XAD, which ``Updater`` unpacks with, refuses to write over a file that is
already there. So it is given an empty drawer to write into, separate from the
system the machine started from, and nothing collides.

An Amiga cannot close its emulator, so the Amiga side writes a marker file when
it has finished and this side watches for it. The run is given more time for as
long as files keep arriving, and is stopped when nothing has been written for
some minutes.

Everything staged for the run is scaffolding, held in a folder the emulator
can read and removed afterwards. What the update produced is written into the
drive image with the rest of the system.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Callable

from . import emulator_config
from .boingbag import FoundPack
from .emulator_media import SNAP_COMMON, STAGING_NAME, is_confined
from .errors import DiskError
from .operations import OperationCancelled
from .system_tree import SystemTree

#: How long to wait with nothing happening before giving up. A limit on the
#: whole run was the wrong rule in the PiStorm imager: a real BoingBag 1 pass
#: writes for over ten minutes on a slow host.
IDLE_TIMEOUT = 240
#: A ceiling, so a run that writes slowly for ever still ends.
RUN_TIMEOUT = 3600
#: How often the staged folders are looked at.
POLL_SECONDS = 2.0

MARKER = "BoingBag-Applied"
PACK_LABEL = "BoingBagPack"
TARGET_LABEL = "Updating"
BOOT_LABEL = "UpdaterBoot"

#: The sound configuration the container ships, which sends audio nowhere.
SILENT_ALSA = Path("/app/alsa-null.conf")

#: FS-UAE keeps Amiga protection bits and comments for a directory drive in a
#: file beside each one.
SIDECAR = ".uaem"
_FLAGS = "hsparwed"


def sidecar_text(protection: int | None, comment: str = "") -> str:
    """The metadata file FS-UAE reads for one file on a directory drive."""
    value = int(protection or 0) & 0xFF
    # The low four bits deny a permission when set. The rest grant a flag.
    shown = value ^ 0x0F
    flags = "".join(
        letter if shown & (0x80 >> index) else "-" for index, letter in enumerate(_FLAGS)
    )
    return f"{flags} 2000-01-01 00:00:00.00 {comment}\n"


def protection_from_sidecar(text: str) -> tuple[int, str]:
    """Read the protection bits and comment back out of a metadata file."""
    flags, _space, rest = text.strip("\n").partition(" ")
    if len(flags) != len(_FLAGS):
        return 0, ""
    shown = 0
    for index, letter in enumerate(flags):
        if letter != "-":
            shown |= 0x80 >> index
    parts = rest.split(" ", 2)
    comment = parts[2] if len(parts) == 3 else ""
    return shown ^ 0x0F, comment.strip()


def work_area(service) -> Path:
    """A folder the emulator is able to read, made for one run."""
    executable = emulator_config.EMULATORS["fs-uae"].executable
    if is_confined(executable):
        root = SNAP_COMMON / STAGING_NAME
    else:
        root = Path(service.work_dir) / "emulator-media"
    folder = root / f"update-{uuid.uuid4().hex}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _host_path(folder: Path, amiga_path: str) -> Path | None:
    """Where an Amiga path lands in a host folder, or None if it cannot.

    AmigaDOS allows names a host directory does not, and a name that would
    step outside the folder is refused.
    """
    parts = [part for part in amiga_path.split("/") if part]
    if not parts or any(part in {".", ".."} or "\x00" in part for part in parts):
        return None
    return folder.joinpath(*parts)


def lay_out_system(tree: SystemTree, folder: Path) -> int:
    """Write the system as it stands into a folder the machine can start from."""
    count = 0
    for drawer in tree.drawers():
        target = _host_path(folder, drawer)
        if target is not None:
            target.mkdir(parents=True, exist_ok=True)
    for item in tree.files():
        target = _host_path(folder, item.path)
        if target is None:
            continue
        try:
            data = item.read()
        except Exception:  # noqa: BLE001 - reported when the drive is written
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        if item.protection or item.comment:
            Path(str(target) + SIDECAR).write_text(
                sidecar_text(item.protection, item.comment), encoding="latin-1"
            )
        count += 1
    return count


def lay_out_pack(found: FoundPack, folder: Path) -> int:
    """Unpack the tools and payloads of a pack for the machine to run.

    Only what the update needs is unpacked: the pack's ``C`` drawer, which
    holds ``Updater``, and the payloads themselves. The rest of the pack is
    installed from the archive and never touches the host.
    """
    count = 0
    wanted = [(f"C/{relative}", member) for relative, member in found.members("C")]
    for name in found.bag.locked_payloads:
        member = found.member(name)
        if member is not None:
            wanted.append((name, member))
    for relative, member in wanted:
        if member.is_directory:
            continue
        target = _host_path(folder, relative)
        if target is None:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(found.read(member))
        count += 1
    return count


def startup_lines(found: FoundPack) -> list[str]:
    """What the Amiga runs once it has started."""
    lines = [
        "; Written by Amiga File Forge to apply an update pack.",
        f"Assign BB: {PACK_LABEL}:",
    ]
    for payload in found.bag.locked_payloads:
        if found.member(payload) is not None:
            lines.append(f'BB:C/Updater BB:{payload} "{TARGET_LABEL}:"')
    # Written last, so its presence means every payload has been through.
    lines.append(f'Echo >SYS:{MARKER} "applied"')
    return lines


def add_hook(boot: Path, found: FoundPack) -> None:
    """Add the update to the startup of the copy the machine starts from."""
    startup = boot / "S" / "User-Startup"
    startup.parent.mkdir(parents=True, exist_ok=True)
    body = startup.read_bytes() if startup.exists() else b""
    if body and not body.endswith(b"\n"):
        body += b"\n"
    body += ("\n".join(startup_lines(found)) + "\n").encode("latin-1")
    startup.write_bytes(body)


def configuration(
    *,
    model: str,
    kickstart: Path,
    boot: Path,
    pack: Path,
    target: Path,
    disc: Path | None,
) -> str:
    """The FS-UAE configuration for one run of the Updater."""
    lines = [
        "[fs-uae]",
        "# Written by Amiga File Forge to apply a BoingBag with its own Updater.",
        f"amiga_model = {model}",
        "cpu = 68040",
        "fpu = 68040",
        f"kickstart_file = {kickstart}",
        "chip_memory = 2048",
        "fast_memory = 8192",
        f"hard_drive_0 = {boot}",
        f"hard_drive_0_label = {BOOT_LABEL}",
        "hard_drive_0_priority = 10",
        f"hard_drive_1 = {pack}",
        f"hard_drive_1_label = {PACK_LABEL}",
        "hard_drive_1_priority = -128",
        f"hard_drive_2 = {target}",
        f"hard_drive_2_label = {TARGET_LABEL}",
        "hard_drive_2_priority = -128",
        "fullscreen = 0",
        "window_width = 640",
        "window_height = 512",
        "automatic_input_grab = 0",
        "floppy_drive_volume = 0",
        "volume = 0",
    ]
    if disc is not None:
        lines.append(f"cdrom_drive_0 = {disc}")
    return "\n".join(lines) + "\n"


def emulator_arguments(executable: str, config: Path) -> list[str]:
    """The command that runs the emulator, with a display of its own if needed."""
    arguments = [executable, str(config)]
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return arguments
    if shutil.which("xvfb-run"):
        # The container has neither a screen nor a sound card. The same
        # settings the other emulator runs use there keep FS-UAE from
        # stopping to look for one.
        quiet = ["env", "ALSOFT_DRIVERS=null"]
        if SILENT_ALSA.is_file():
            quiet.append(f"ALSA_CONFIG_PATH={SILENT_ALSA}")
        return [*quiet, "xvfb-run", "-a", *arguments]
    return arguments


def _written(folder: Path) -> tuple[int, int]:
    """How much is in a folder, as something cheap to compare."""
    count = total = 0
    for item in folder.rglob("*"):
        try:
            if item.is_file() and not item.name.endswith(SIDECAR):
                count += 1
                total += item.stat().st_size
        except OSError:
            continue
    return count, total


def collect(target: Path, tree: SystemTree, origin: str) -> list[str]:
    """Add what the Updater wrote to the system, replacing what was there."""
    added: list[str] = []
    for item in sorted(target.rglob("*")):
        if not item.is_file() or item.name.endswith(SIDECAR):
            continue
        relative = item.relative_to(target).as_posix()
        data = item.read_bytes()
        protection, comment = 0, ""
        sidecar = Path(str(item) + SIDECAR)
        if sidecar.is_file():
            protection, comment = protection_from_sidecar(
                sidecar.read_text(encoding="latin-1", errors="replace")
            )
        if tree.add(
            relative,
            lambda data=data: data,
            len(data),
            origin,
            protection=protection or None,
            comment=comment,
        ):
            added.append(relative)
    return added


def run_updater(
    service,
    found: FoundPack,
    tree: SystemTree,
    *,
    disc: Path,
    kickstart: Path,
    model: str,
    progress: Callable[..., None],
    idle_timeout: float = IDLE_TIMEOUT,
    run_timeout: float = RUN_TIMEOUT,
) -> list[str] | None:
    """Run one pack's Updater. Returns the files it wrote, or None on failure."""
    emulator = emulator_config.EMULATORS["fs-uae"]
    folder = work_area(service)
    boot, pack, target = folder / "Boot", folder / "Pack", folder / "Target"
    process: subprocess.Popen | None = None
    try:
        for made in (boot, pack, target):
            made.mkdir(parents=True, exist_ok=True)
        progress(f"{found.bag.label}: preparing the machine that runs its Updater", 0, None)
        lay_out_system(tree, boot)
        lay_out_pack(found, pack)
        add_hook(boot, found)
        rom = folder / "kickstart.rom"
        shutil.copyfile(kickstart, rom)
        staged_disc = folder / f"disc{Path(disc).suffix or '.iso'}"
        try:
            os.link(os.path.realpath(disc), staged_disc)
        except OSError:
            shutil.copyfile(disc, staged_disc)
        config = folder / "updater.fs-uae"
        config.write_text(configuration(
            model=model, kickstart=rom, boot=boot, pack=pack, target=target,
            disc=staged_disc,
        ))
        marker = boot / MARKER
        process = subprocess.Popen(
            emulator_arguments(emulator.executable, config),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        started = time.monotonic()
        idle_until = started + idle_timeout
        seen = _written(target)
        applied = False
        while time.monotonic() - started < run_timeout:
            if marker.exists():
                applied = True
                break
            if process.poll() is not None:
                applied = marker.exists()
                break
            now = _written(target)
            if now != seen:
                seen = now
                idle_until = time.monotonic() + idle_timeout
            elif time.monotonic() > idle_until:
                break
            progress(
                f"{found.bag.label}: its Updater has written {now[0]} files", 0, None
            )
            time.sleep(POLL_SECONDS)
        if not applied:
            return None
        # The marker is written after the last payload, but the emulator may
        # still be flushing the final file to the host.
        time.sleep(POLL_SECONDS)
        return collect(target, tree, found.bag.label)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=15)
        shutil.rmtree(folder, ignore_errors=True)


def cannot_run(target) -> str:
    """Why the Updater cannot be run here, or an empty string when it can."""
    emulator = emulator_config.EMULATORS["fs-uae"]
    if not emulator.available:
        return "FS-UAE is not installed, so the pack's own Updater cannot be run."
    machine = emulator_config.profile_machine(target)
    if emulator_config.kickstart_for(machine) is None:
        return (
            f"No Kickstart ROM for the {machine.upper()} was found in "
            f"{emulator_config.KICKSTART_DIR}, so there is nothing to start the "
            "machine that runs the pack's Updater."
        )
    return ""


def apply_locked_pack(
    service,
    target,
    disc,
    tree: SystemTree,
    found: FoundPack,
    *,
    use_emulator: bool = True,
    progress: Callable[..., None],
) -> dict:
    """Apply one pack's encrypted payloads, or name what could not be applied."""
    names = found.locked_entries()
    outcome = {"locked": True, "lockedFiles": len(names), "applied": False,
               "leftOut": [], "reason": ""}
    reason = "" if use_emulator else "Running the pack's Updater was switched off."
    reason = reason or cannot_run(target)
    written: list[str] | None = None
    if not reason:
        machine = emulator_config.profile_machine(target)
        try:
            written = run_updater(
                service, found, tree,
                disc=Path(service.resolve(disc)),
                kickstart=emulator_config.kickstart_for(machine),
                model=emulator_config.FSUAE_MODELS.get(machine, "A1200"),
                progress=progress,
            )
        except OperationCancelled:
            raise
        except (OSError, subprocess.SubprocessError, DiskError) as exc:
            reason = f"The emulator could not run the pack's Updater: {exc}"
        else:
            if written is None:
                reason = (
                    "The emulator did not finish the update. Nothing had been "
                    f"written for {IDLE_TIMEOUT // 60} minutes when it was stopped."
                )
    if written is not None:
        outcome["applied"] = True
        outcome["updaterFiles"] = len(written)
        return outcome
    outcome["reason"] = reason
    outcome["leftOut"] = sorted(names)
    return outcome


__all__ = [
    "IDLE_TIMEOUT",
    "MARKER",
    "RUN_TIMEOUT",
    "add_hook",
    "apply_locked_pack",
    "cannot_run",
    "collect",
    "configuration",
    "emulator_arguments",
    "lay_out_pack",
    "lay_out_system",
    "protection_from_sidecar",
    "run_updater",
    "sidecar_text",
    "startup_lines",
    "work_area",
]
