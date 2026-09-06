"""Managed emulator selection and command construction.

The workbench can hand an image to an emulator so a change can be watched
running rather than only inspected. FS-UAE is the one supported emulator: it
covers every machine from an A500 to an A4000 and the CD32, it takes floppies
and hard drives alike, and it is driven entirely from the command line, which
is what makes a test run repeatable and scriptable on every platform this
application runs on.

No Kickstart ROM is shipped or downloaded. Each emulator needs one that the
user supplies, and a profile that cannot find its ROM reports that plainly
instead of starting and failing at a black screen.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .hardware_profiles import profile_addons


@dataclass(frozen=True)
class ManagedEmulator:
    identifier: str
    label: str
    executable: str
    debugger: str
    platforms: tuple[str, ...]

    @property
    def available(self) -> bool:
        return Path(self.executable).is_file() or shutil.which(self.executable) is not None


FSUAE_ROOT = Path(os.environ.get("AMIGA_FSUAE_ROOT", "/usr/bin"))

#: The names FS-UAE is installed under. The Debian package provides fs-uae; a
#: Snap exposes it as fsuae.fs-uae, and a Flatpak through its own wrapper. An
#: installation that works from a terminal should work here, so all of them are
#: looked for rather than only the Debian spelling.
FSUAE_EXECUTABLE_NAMES = ("fs-uae", "fsuae.fs-uae", "fsuae", "fs-uae-launcher")


def _fsuae_executable() -> str:
    """Locate FS-UAE, or return the conventional path so the error names it.

    ``AMIGA_FSUAE_EXECUTABLE`` names one exact binary and wins outright, which
    is what a build or a test needs. Otherwise the configured root is tried
    first, then PATH, under each name a packaging format uses.
    """
    override = os.environ.get("AMIGA_FSUAE_EXECUTABLE", "").strip()
    if override:
        return override
    for name in FSUAE_EXECUTABLE_NAMES:
        candidate = FSUAE_ROOT / name
        if candidate.is_file():
            return str(candidate)
    for name in FSUAE_EXECUTABLE_NAMES:
        found = shutil.which(name)
        if found:
            return found
    return str(FSUAE_ROOT / "fs-uae")

#: Where a user's own Kickstart ROMs are looked for. Nothing is copied out.
KICKSTART_DIR = Path(
    os.environ.get(
        "AMIGA_FILE_FORGE_KICKSTART_DIR",
        Path.home() / ".config" / "amiga-file-forge" / "kickstarts",
    )
)

ALL_MACHINES = ("a500", "a500plus", "a600", "a1200", "a2000", "a3000", "a4000", "cd32")

EMULATORS = {
    "fs-uae": ManagedEmulator(
        "fs-uae", "FS-UAE",
        _fsuae_executable(), "fs-uae --console-debugger", ALL_MACHINES,
    ),
}

#: Retained under its previous name so existing profiles keep resolving.
EMULATORS["fs-uae-pistorm"] = EMULATORS["fs-uae"]

#: FS-UAE's amiga_model values, by workbench machine and fitted accelerator.
FSUAE_MODELS = {
    "a500": "A500", "a500plus": "A500+", "a600": "A600", "a1200": "A1200",
    "a2000": "A500+", "a3000": "A4000/040", "a4000": "A4000/040", "cd32": "CD32",
}

#: The Kickstart each machine expects, in the order the workbench looks.
KICKSTART_NAMES = {
    "a500": ("kick13.rom", "kick34005.A500", "kick.rom"),
    "a500plus": ("kick204.rom", "kick37175.A500", "kick.rom"),
    "a600": ("kick205.rom", "kick37350.A600", "kick31.rom", "kick.rom"),
    "a1200": ("kick31.rom", "kick40068.A1200", "kick30.rom", "kick.rom"),
    "a2000": ("kick13.rom", "kick34005.A500", "kick.rom"),
    "a3000": ("kick31.rom", "kick40068.A4000", "kick204.rom", "kick.rom"),
    "a4000": ("kick31.rom", "kick40068.A4000", "kick30.rom", "kick.rom"),
    "cd32": ("kick40060.CD32", "kick31.rom"),
}

#: Media FS-UAE can attach directly.
FLOPPY_SUFFIXES = {".adf", ".adz", ".dms", ".ipf", ".hfe", ".dsk"}
DRIVE_SUFFIXES = {".hdf", ".hda", ".hdz", ".img", ".raw", ".rdsk"}


def kickstart_for(machine: str) -> Path | None:
    """Return the user-supplied Kickstart this machine would boot from."""
    for name in KICKSTART_NAMES.get(machine, ()):
        candidate = KICKSTART_DIR / name
        if candidate.is_file():
            return candidate
    # No fallback to "whatever ROM is there". The directory legitimately holds
    # ROMs for several machines, and an extended or cartridge ROM among them is
    # not a Kickstart at all, so the first name alphabetically was as likely to
    # be wrong as right. A machine with no matching ROM has none, and saying so
    # is what lets the caller name the file that is missing.
    return None


def profile_machine(session) -> str:
    profile = getattr(session, "hardware_profile", {}) or {}
    machine = str(profile.get("machine") or "").strip().lower()
    aliases = {
        "amiga 500": "a500", "amiga500": "a500",
        "amiga 500+": "a500plus", "amiga500plus": "a500plus",
        "amiga 600": "a600", "a600": "a600",
        "amiga 1200": "a1200", "amiga1200": "a1200",
        "amiga 2000": "a2000", "amiga 3000": "a3000", "amiga 4000": "a4000",
        "amigaos": "a4000", "cd 32": "cd32",
    }
    if machine in ALL_MACHINES:
        return machine
    if machine in aliases:
        return aliases[machine]
    target = str(getattr(session, "target_hardware", "") or "")
    return {
        "a500-ofs": "a500",
        "a1200-ffs": "a1200",
        "amigaos": "a4000",
        "hardfile": "a1200",
    }.get(target, "a500")


def configured_emulator(session) -> ManagedEmulator:
    profile = getattr(session, "hardware_profile", {}) or {}
    machine = profile_machine(session)
    selected = str(profile.get("emulator") or "auto").strip().lower()
    if selected == "auto" or selected not in EMULATORS:
        selected = "fs-uae"
    emulator = EMULATORS[selected]
    if machine not in emulator.platforms:
        return EMULATORS["fs-uae"]
    return emulator


def emulator_status(session) -> dict:
    emulator = configured_emulator(session)
    machine = profile_machine(session)
    available = emulator.available
    firmware_message = ""
    kickstart = kickstart_for(machine)
    if available and kickstart is None:
        available = False
        firmware_message = (
            f" No Kickstart ROM for {machine} was found in {KICKSTART_DIR}. "
            "Kickstart is not redistributable, so it is not shipped: supply your own "
            "and put it there."
        )
    return {
        "id": emulator.identifier,
        "label": emulator.label,
        "available": available,
        "machine": machine,
        "kickstart": str(kickstart) if kickstart else "",
        "debugger": emulator.debugger,
        "configuredBy": "managed workbench profile",
        "message": (
            f"{emulator.label} is installed and configured for the {machine.upper()}."
            if available else
            f"{emulator.label} is selected for the {machine.upper()}, but it cannot start yet."
            if emulator.available and firmware_message else
            f"{emulator.label} is selected for the {machine.upper()}, but its executable is missing from this build."
        ) + firmware_message,
    }


def emulator_command(
    session,
    media_path: str | Path,
    *,
    debug: bool = False,
    interactive: bool = False,
    native: bool = False,
) -> tuple[list[str], str]:
    emulator = configured_emulator(session)
    if not emulator.available:
        raise ValueError(f"{emulator.label} is not installed in this build.")
    profile = getattr(session, "hardware_profile", {}) or {}
    addons = profile_addons(session)
    media = Path(media_path)
    suffix = media.suffix.lower()
    boot = str(profile.get("emulatorBoot") or "auto")
    machine = profile_machine(session)
    kickstart = kickstart_for(machine)

    if emulator.identifier in {"fs-uae", "fs-uae-pistorm"}:
        if kickstart is None:
            raise ValueError(
                f"No Kickstart ROM for the {machine.upper()} was found in {KICKSTART_DIR}."
            )
        if suffix not in FLOPPY_SUFFIXES | DRIVE_SUFFIXES:
            raise ValueError(
                "FS-UAE can start from a floppy image (ADF, ADZ, DMS, IPF, HFE) or a "
                "hard-drive image (HDF, HDA, RAW). Export one of those first."
            )
        arguments = _desktop_command(
            emulator.executable, debug=debug, interactive=interactive, native=native
        )
        arguments += [
            f"--amiga_model={_fsuae_model(machine, addons)}",
            f"--kickstart_file={kickstart}",
        ]
        chip = "2048" if "chip-2048" in addons else "1024" if "chip-1024" in addons else "512"
        arguments.append(f"--chip_memory={chip}")
        if "fast-ram" in addons:
            arguments.append("--fast_memory=8192")
        if "slow-ram" in addons:
            arguments.append("--slow_memory=512")
        if suffix in DRIVE_SUFFIXES:
            arguments.append(f"--hard_drive_0={media}")
        else:
            arguments.append(f"--floppy_drive_0={media}")
            arguments.append(f"--floppy_image_0={media}")
        if boot in {"auto", "boot"}:
            arguments.append("--automatic_input_grab=0")
        if debug:
            arguments.append("--console_debugger=1")
        return arguments, str(FSUAE_ROOT)

    raise ValueError(
        f"{emulator.label} is not a managed emulator in this build. "
        "Choose FS-UAE in the hardware profile."
    )


def _command_environment(arguments: list[str], values: dict[str, str]) -> list[str]:
    assignments = [f"{key}={value}" for key, value in values.items()]
    if "env" in arguments:
        position = arguments.index("env") + 1
        return [*arguments[:position], *assignments, *arguments[position:]]
    return ["env", *assignments, *arguments]


def _desktop_command(
    executable: str,
    *,
    debug: bool,
    interactive: bool,
    native: bool = False,
) -> list[str]:
    """Run in the shared browser display or a bounded private X server."""
    if native and interactive:
        return [executable]
    environment = ["env", "ALSA_CONFIG_PATH=/app/alsa-null.conf", "ALSOFT_DRIVERS=null"]
    if interactive:
        return [
            "timeout", "--signal=TERM", "--kill-after=2", "900",
            *environment, "DISPLAY=:99", executable,
        ]
    duration = "15" if debug else "8"
    return [
        "timeout", "--signal=TERM", "--kill-after=2", duration,
        *environment,
        "xvfb-run", "-a", executable,
    ]


def _fsuae_model(machine: str, addons: set[str]) -> str:
    """Choose FS-UAE's machine model, upgraded by any fitted accelerator.

    A PiStorm is a CPU replacement rather than a turbo board, so it is modelled
    as the fastest 68k FS-UAE offers for that machine. That is an
    approximation and it is the honest one available: FS-UAE emulates 68k
    cores, not a Raspberry Pi running Musashi, so the profile's own
    "Validation only" marking is what tells the difference.
    """
    model = FSUAE_MODELS.get(machine, "A500")
    accelerated = {"acc-68040", "acc-68060", "pistorm32"} & addons
    if machine in {"a1200", "a4000"}:
        if accelerated:
            return "A4000/040"
        if "acc-68030" in addons and machine == "a1200":
            return "A1200/020"
    if "pistorm" in addons and machine in {"a500", "a500plus", "a600", "a2000"}:
        # The 68000-socket board turns a stock machine into something closer to
        # an accelerated 68020 than to its original CPU.
        return "A1200/020"
    return model


__all__ = [
    "ALL_MACHINES",
    "DRIVE_SUFFIXES",
    "EMULATORS",
    "FLOPPY_SUFFIXES",
    "FSUAE_MODELS",
    "FSUAE_ROOT",
    "KICKSTART_DIR",
    "KICKSTART_NAMES",
    "ManagedEmulator",
    "configured_emulator",
    "emulator_command",
    "emulator_status",
    "kickstart_for",
    "profile_machine",
]
