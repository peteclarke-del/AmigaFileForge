"""Recognising the AmigaOS release CDs, and knowing how each one is laid out.

AmigaOS 3.5 and 3.9 were published on CD rather than on floppies. What a CD
offers is better than seven disks to merge: the system is already laid out on
the disc as directory trees, so an installation is a copy made in the right
order, and this application makes it.

**Where the layout comes from.** Every source and destination below was read
out of the Installer script each disc carries, ``OS-Version3.5/OS3.5Install``
and ``OS-Version3.9/OS3.9Install``. Several destinations are not the obvious
ones. ``Extras/Backdrops`` goes to ``Prefs/Presets/Backdrops`` and not to
``Backdrops``, and the printer and keymap sets are lifted out of the Workbench
tree's own ``Storage`` drawer and copied again into ``Devs``. Guessing them
would produce a system that looks installed and is subtly wrong. The same
layout is what the PiStorm imager writes, so a drive prepared by either looks
the same to a person and to a program.

**Why the order matters.** The two discs are not layered the same way. The 3.9
disc carries ``Workbench3.5``, which is a complete system, and ``Workbench3.9``,
the overlay that turns it into 3.9. The 3.5 disc carries a delta with no ``S``
drawer at all, because it expects to land on Workbench 3.1, which the disc also
supplies under ``OS-Version3.1``. So a release is a list of layers copied
newest last, each replacing what the one before it laid down.

**What is checked first.** Both releases need a 68020 or better. A stock A500
or A600 cannot run either, and the hardware profile already says which machine
the drive is for, so an installation that could never start is refused before
anything is written.
"""

from __future__ import annotations

import dataclasses

from .system_tree import SystemTree, join_path

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
    #: What the release needs of the machine, in words an operator can act on.
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
            "Kickstart 3.1 ROMs and a 68020 or better. The disc carries Workbench 3.1 "
            "as well, so the drive does not need a system on it beforehand."
        ),
        disk_space_mb=30,
    ),
    Release(
        key="3.9",
        label="AmigaOS 3.9",
        volume="AmigaOS3.9",
        payload="OS-Version3.9",
        requires=(
            "Kickstart 3.1 ROMs and a 68020 or better. The disc carries the whole "
            "system, so the drive does not need one on it beforehand."
        ),
        disk_space_mb=40,
    ),
)



@dataclasses.dataclass(frozen=True)
class Layer:
    """One tree copied off the disc, and where it lands on the system drive."""

    source: str
    #: Relative to the root of the drive. An empty string is the root itself.
    destination: str
    label: str
    order: int
    #: A release cannot be installed from a disc that lacks a required layer.
    required: bool = False


#: "Workbench" on the 3.5 disc is a delta, so the 3.1 trees the disc also
#: carries are laid down first. Without them there is no Startup-Sequence.
OS35_LAYERS: tuple[Layer, ...] = (
    Layer("OS-Version3.1/Workbench3.1", "", "Workbench 3.1", 10, required=True),
    Layer("OS-Version3.1/Extras3.1", "", "Extras 3.1", 20),
    Layer("OS-Version3.5/Workbench", "", "Workbench 3.5", 30, required=True),
    Layer("OS-Version3.5/Locale", "Locale", "Locale", 40),
    Layer("OS-Version3.5/Keymaps", "Devs/Keymaps", "Keymaps", 50),
    Layer("OS-Version3.5/Printers", "Devs/Printers", "Printer drivers", 55),
    Layer("OS-Version3.5/C", "C", "Updated commands", 60),
    Layer("OS-Version3.5/L", "L", "FastFileSystem", 65),
    Layer("OS-Version3.5/Extras/Libs", "Libs", "Extra libraries", 70),
    Layer("OS-Version3.5/Extras/Backdrops", "Prefs/Presets/Backdrops", "Backdrops", 75),
)

#: Workbench3.5 is complete on the 3.9 disc, so no 3.1 layer is needed.
#: Printers and keymaps come out of the 3.9 tree's own Storage drawer, which is
#: where its installer takes them from.
OS39_LAYERS: tuple[Layer, ...] = (
    Layer("OS-Version3.9/Workbench3.5", "", "Workbench 3.5 base", 10, required=True),
    Layer("OS-Version3.9/Workbench3.9", "", "Workbench 3.9", 20, required=True),
    Layer("OS-Version3.9/Locale", "Locale", "Locale", 30),
    Layer("OS-Version3.9/Workbench3.9/Storage/Keymaps", "Devs/Keymaps", "Keymaps", 40),
    Layer("OS-Version3.9/Workbench3.9/Storage/Printers", "Devs/Printers",
          "Printer drivers", 45),
    Layer("OS-Version3.9/C", "C", "Updated commands", 50),
    Layer("OS-Version3.9/L", "L", "FastFileSystem", 55),
    Layer("OS-Version3.9/Extras/Libs", "Libs", "Extra libraries", 60),
    Layer("OS-Version3.9/Extras/Backdrops", "Prefs/Presets/Backdrops", "Backdrops", 65),
)

LAYERS: dict[str, tuple[Layer, ...]] = {"3.5": OS35_LAYERS, "3.9": OS39_LAYERS}


def layers_on(image, release: Release) -> tuple[list[Layer], list[Layer]]:
    """The layers of a release this disc carries, and the ones it lacks.

    ``image`` is an open CD image. A layer counts as present when its drawer is
    on the disc, so a disc that is missing a tree says so before an
    installation is started rather than part way through one.
    """
    present: list[Layer] = []
    missing: list[Layer] = []
    for layer in sorted(LAYERS[release.key], key=lambda item: item.order):
        try:
            image.list_directory(layer.source)
        except Exception:  # noqa: BLE001 - any failure means the tree is not usable
            missing.append(layer)
            continue
        present.append(layer)
    return present, missing


def release_by_layout(image) -> Release | None:
    """Recognise a disc whose volume name has been changed, by its trees.

    A disc that was mastered again keeps the layout and loses the label, so a
    volume name that is not known is not the end of the question.
    """
    for release in RELEASES:
        _present, missing = layers_on(image, release)
        if not any(layer.required for layer in missing):
            return release
    return None


def plan_release(image, release: Release, tree: SystemTree | None = None) -> dict:
    """Resolve a release's layers into the system the drive should hold.

    Nothing is read from the disc but its directories. Each file in the
    returned tree knows how to fetch its own bytes, so the files that an
    overlay replaced are never read at all.
    """
    tree = tree if tree is not None else SystemTree()
    present, missing = layers_on(image, release)
    described: list[dict] = []
    for layer in present:
        count = 0
        for entry in image.walk(layer.source):
            relative = entry.path[len(layer.source):].lstrip("/")
            if entry.path.casefold()[:len(layer.source)] != layer.source.casefold():
                continue
            target = join_path(layer.destination, relative)
            if entry.directory:
                tree.add_drawer(target)
                continue
            if tree.add(
                target,
                lambda entry=entry: image.read_entry(entry),
                entry.length,
                layer.label,
                protection=entry.protection,
                comment=entry.comment,
            ):
                count += 1
        described.append({
            "label": layer.label,
            "source": layer.source,
            "destination": layer.destination or ":",
            "files": count,
        })
    return {
        "tree": tree,
        "layers": described,
        "missing": [
            {"label": layer.label, "source": layer.source, "required": layer.required}
            for layer in missing
        ],
    }


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
    "LAYERS",
    "Layer",
    "OS35_LAYERS",
    "OS39_LAYERS",
    "layers_on",
    "plan_release",
    "release_by_layout",
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
