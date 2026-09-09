"""Recognising the AmigaOS release CDs, and saying whether a drive can take one.

AmigaOS 3.5 and 3.9 were published on CD rather than on floppies, and they are
not installed the way 3.1 is. There is no tree to copy: the disc carries a
Commodore Installer script of two hundred kilobytes that runs on the Amiga,
reads the versions of the libraries the live system has loaded, asks a great
many questions and patches an existing installation in place. Its own words
are that "Pretend mode cannot be used with this installation script", and 3.9
refuses outright unless it finds an earlier release already there.

So this component does not install anything. It does the three things that can
be done honestly from outside the Amiga, and which otherwise cost an operator a
long detour to discover:

**Recognise the disc.** The volume name is exact and Commodore wrote it, so a
disc is identified the same way the Workbench floppies are, and a disc that is
merely a coverdisk or a contribution CD is not mistaken for a release.

**Check the hardware.** Both releases need a 68020 or better. A stock A500 or
A600 cannot run either, and there is no point booting an emulator for twenty
minutes to be told so. An A1200 qualifies on its own, and so does any machine
with an accelerator or a PiStorm, which is exactly what the hardware profiles
already describe.

**Check the target.** 3.5 wants Kickstart 3.1 and a Workbench already
installed; 3.9 wants a release already there to update. A drive that has
neither is not ready, and saying so before the emulator starts is the whole
value of a preflight.

What happens after that is the emulator booting the drive with the CD attached,
and Commodore's installer doing the work. That is the honest division: this
application prepares and checks, and the installer installs.
"""

from __future__ import annotations

import dataclasses

#: The processor both CD releases need. AmigaOS 3.5 and 3.9 are compiled for
#: the 68020 and will not start on a 68000 or 68010.
REQUIRED_PROCESSOR = "68020"

#: Machines whose own processor already satisfies that, so no accelerator is
#: needed. The A1200 and CD32 are 68EC020, which counts.
NATIVE_68020_MACHINES = frozenset({"a1200", "a3000", "a4000", "cd32"})

#: Add-ons that put a 68020 or better into a machine that has neither. The
#: PiStorm entries are Raspberry Pi CPU replacements, and what they emulate is
#: well beyond an 020.
UPGRADE_ADDONS = frozenset({
    "acc-68020", "acc-68030", "acc-68040", "acc-68060", "pistorm", "pistorm32",
})


@dataclasses.dataclass(frozen=True)
class Release:
    """One AmigaOS release published on CD."""

    key: str
    label: str
    #: The exact volume name Commodore wrote on the disc.
    volume: str
    #: The drawer on the disc holding that release's own files.
    payload: str
    #: What has to be on the target drive already, in words an operator can act
    #: on. Both releases update a system rather than creating one.
    requires: str
    #: Roughly how much room the installation needs on the target volume.
    disk_space_mb: int


RELEASES: tuple[Release, ...] = (
    Release(
        key="3.5",
        label="AmigaOS 3.5",
        volume="AmigaOS3.5",
        payload="OS-Version3.5",
        requires=(
            "Kickstart 3.1 ROMs and a Workbench 3.1 installation already on the drive. "
            "The disc also carries an OS-Version3.1 drawer for a drive that has none."
        ),
        disk_space_mb=20,
    ),
    Release(
        key="3.9",
        label="AmigaOS 3.9",
        volume="AmigaOS3.9",
        payload="OS-Version3.9",
        requires=(
            "Kickstart 3.1 ROMs and an existing AmigaOS installation to update. "
            "The installer refuses a drive with no earlier release on it."
        ),
        disk_space_mb=20,
    ),
)

RELEASES_BY_VOLUME = {release.volume.casefold(): release for release in RELEASES}


def release_for_volume(volume: str) -> Release | None:
    """Which release this disc is, from the name Commodore gave the volume."""
    return RELEASES_BY_VOLUME.get(str(volume or "").strip().casefold())


def describe_releases() -> list[dict]:
    """The releases this recognises, for an interface that has to explain."""
    return [
        {
            "key": release.key,
            "label": release.label,
            "volume": release.volume,
            "payload": release.payload,
            "requires": release.requires,
            "diskSpaceMb": release.disk_space_mb,
        }
        for release in RELEASES
    ]


def processor_ready(machine: str, addons) -> tuple[bool, str]:
    """Whether this hardware can run a CD release, and why not when it cannot.

    Answered from the profile rather than from the emulator, because the point
    is to say so before anything is launched. The reason is returned in full
    because "unsupported" on its own tells an operator nothing about what to
    change.
    """
    chosen = set(addons or [])
    machine = str(machine or "").casefold()
    if machine in NATIVE_68020_MACHINES:
        return True, ""
    upgrade = sorted(chosen & UPGRADE_ADDONS)
    if upgrade:
        return True, ""
    return False, (
        f"AmigaOS 3.5 and 3.9 need a {REQUIRED_PROCESSOR} or better, and this profile "
        f"is a plain 68000 machine. Add an accelerator or a PiStorm to the profile, "
        f"or choose a machine that has one, such as an A1200."
    )


#: Where Workbench 3.1 leaves the CD-ROM driver, and where it has to be for
#: AmigaDOS to mount a disc. The Extras disk supplies the filing system and the
#: Storage disk supplies the mountlist, but Storage is the drawer Workbench
#: keeps things in until they are wanted, so a stock installation has the
#: driver present and inactive.
CD_FILESYSTEM = "L/CDFileSystem"
CD_DRIVER_PARKED = "Storage/DOSDrivers/CD0"
CD_DRIVER_ACTIVE = "Devs/DOSDrivers/CD0"

#: What the emulated CD drive is called. Commodore's mountlist leaves Device
#: and Unit commented out and takes them from tooltypes on the CD0 icon,
#: defaulting to a real SCSI drive at unit 2. Nothing emulated answers there,
#: so the two lines are written into the mountlist instead, which is the form
#: the file's own comment documents.
EMULATED_CD_DEVICE = "uaescsi.device"
EMULATED_CD_UNIT = 0


def mountlist_with_device(text: str, device: str, unit: int) -> str:
    """Point a CD mountlist at a named device and unit.

    Any Device or Unit already set is replaced rather than added to, because a
    mountlist with two of either is ambiguous and AmigaDOS reads whichever it
    saw last. Commented-out examples are left alone: they document the file and
    are not read.
    """
    kept = [
        line for line in text.splitlines()
        if not _assigns(line, "Device") and not _assigns(line, "Unit")
    ]
    while kept and not kept[-1].strip():
        kept.pop()
    kept.extend([f"Device\t\t= {device}", f"Unit\t\t= {unit}"])
    return "\n".join(kept) + "\n"


def _assigns(line: str, key: str) -> bool:
    """Whether this line actively sets ``key``, ignoring comments."""
    stripped = line.strip()
    if stripped.startswith(("*", "/*", ";")):
        return False
    name, separator, _value = stripped.partition("=")
    return bool(separator) and name.strip().casefold() == key.casefold()


__all__ = [
    "CD_DRIVER_ACTIVE",
    "CD_DRIVER_PARKED",
    "CD_FILESYSTEM",
    "EMULATED_CD_DEVICE",
    "EMULATED_CD_UNIT",
    "mountlist_with_device",
    "NATIVE_68020_MACHINES",
    "RELEASES",
    "REQUIRED_PROCESSOR",
    "Release",
    "UPGRADE_ADDONS",
    "describe_releases",
    "processor_ready",
    "release_for_volume",
]
