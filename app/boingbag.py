"""BoingBags: the update packs published for AmigaOS 3.5 and 3.9.

A CD installation is not a finished system. Both releases had update packs
published after them, and 3.9 without BoingBag 2 is missing a great deal. They
are LhA archives rather than part of the disc, so they are further layers laid
over whatever the CD supplied, in the order they came out.

They are not all the same shape, and the difference decides what can be done
with each from outside the Amiga.

BoingBag 1 and 2 for 3.5 are plain trees. ``Workbench`` goes to the root of the
drive, ``Printers`` to ``Devs/Printers``, and so on.

BoingBags 3 and 4 for 3.9 are a community release and also plain, but the
``Files2`` drawer holds several builds of the same file, one for each processor
and machine, and the pack's installer picks one. The rules here are the ones
that script tests, and the names are its ``newname`` clauses.

BoingBag 1 and 2 for 3.9 keep their system fixes in ``AmigaOS-Update``, a ZIP
in which every entry is encrypted. The password is inside Haage & Partner's
``Updater``, and their installer runs ``C/Updater AmigaOS-Update <target>``.
That archive is not opened here. The publisher's own tool is run on it under
emulation, by ``app.boingbag_update``, and where no emulator is available the
files that are in the clear are still installed and the ones that were not are
named.

Every source and destination below was read out of each pack's own Installer
script, and matches what the PiStorm imager applies.
"""

from __future__ import annotations

import dataclasses
import io
import zipfile

from .errors import DiskError
from .lha import LHAArchive, LHAError, LHAMember
from .system_tree import SystemTree, join_path


@dataclasses.dataclass(frozen=True)
class Layer:
    #: Relative to the pack's own drawer.
    source: str
    #: Relative to the root of the drive. An empty string is the root itself.
    destination: str
    label: str
    order: int


@dataclasses.dataclass(frozen=True)
class Variant:
    """One file chosen from several builds of it.

    The candidates are tried in order and the first whose rule holds, and
    whose file is in the pack, is installed under ``newname``.
    """

    candidates: tuple[tuple[str, str], ...]
    destination: str
    newname: str
    label: str


@dataclasses.dataclass(frozen=True)
class Bag:
    key: str
    label: str
    release: str
    #: The order the packs were published in.
    order: int
    #: The drawer the archive unpacks into.
    root: str
    layers: tuple[Layer, ...] = ()
    variants: tuple[Variant, ...] = ()
    #: Payloads only the publisher's own tool can open.
    locked_payloads: tuple[str, ...] = ()
    official: bool = True
    #: BoingBags 3 and 4 replace core components, so somebody chasing a fault
    #: has to be able to leave them off and get a stock system.
    default_on: bool = True
    notes: str = ""


def _os35_layers(internet_destination: str) -> tuple[Layer, ...]:
    return (
        Layer("Workbench", "", "Workbench updates", 10),
        Layer("Printers", "Devs/Printers", "Printer drivers", 20),
        Layer("ROM-Update", "Devs", "ROM update", 30),
        Layer("Internet", internet_destination, "Internet software", 40),
    )


#: Files1 is copied whole to the root of the drive. Files2 is picked over.
BB34_LAYERS = (
    Layer("Files1", "", "System updates", 10),
    Layer("Files2/Devs/Keymaps", "Devs/Keymaps", "Keymaps", 20),
    Layer("Files2/Fonts", "Fonts", "Fonts", 25),
    Layer("Files2/Locale/Countries", "Locale/Countries", "Countries", 30),
    Layer("Files2/Locale/Catalogs", "Locale/Catalogs", "Catalogs", 35),
    Layer("Files2/Libs/Picasso96", "Libs/Picasso96", "Picasso96", 40),
)

BB34_VARIANTS = (
    Variant(
        candidates=(("cpu68060", "Files2/Libs/xadmaster_060.library"),
                    ("always", "Files2/Libs/xadmaster_020.library")),
        destination="Libs", newname="xadmaster.library", label="xadmaster"),
    Variant(
        candidates=(("cpu68060+fpu", "Files2/Libs/mpega060FPU.library"),
                    ("cpu68060", "Files2/Libs/mpega060.library"),
                    ("cpu68040+fpu", "Files2/Libs/mpega040FPU.library"),
                    ("cpu68040", "Files2/Libs/mpega040.library"),
                    ("always", "Files2/Libs/mpega020FPU.library")),
        destination="Libs", newname="mpega.library", label="mpega"),
    # scsi.device is the machine's own IDE controller, so it is only replaced
    # on the machines that have one. The packs in circulation do not all agree
    # with their own script about the file's name, so both spellings are
    # listed and whichever is present is used.
    Variant(
        candidates=(("a600", "Files2/Devs/scsi_A600_A1200.device"),
                    ("a600", "Files2/Devs/scsi_A600.device"),
                    ("a1200", "Files2/Devs/scsi_A600_A1200.device"),
                    ("a1200", "Files2/Devs/scsi_A1200.device")),
        destination="Devs", newname="scsi.device", label="IDE driver"),
)

BAGS: tuple[Bag, ...] = (
    Bag("3.5-1", "BoingBag 1 for AmigaOS 3.5", "3.5", 10, "BoingBag_1",
        layers=_os35_layers("")),
    Bag("3.5-2", "BoingBag 2 for AmigaOS 3.5", "3.5", 20, "BoingBag2",
        layers=_os35_layers("Internet")),
    Bag("3.9-1", "BoingBag 1 for AmigaOS 3.9", "3.9", 10, "BoingBag3.9-1",
        layers=(Layer("Locale", "Locale", "Locale", 10),
                Layer("Contribution", "Contribution", "Contributions", 20),
                Layer("Internet", "Internet", "Internet software", 30)),
        locked_payloads=("AmigaOS-Update",),
        notes="The system fixes are in an encrypted archive that only Haage & "
              "Partner's Updater can open."),
    Bag("3.9-2", "BoingBag 2 for AmigaOS 3.9", "3.9", 20, "BoingBag3.9-2",
        locked_payloads=("AmigaOS-Update", "XAD-Update"),
        notes="Every fix it makes is inside its two encrypted archives."),
    Bag("3.9-34", "BoingBags 3 and 4 for AmigaOS 3.9", "3.9", 30,
        "BoingBag3.9-3&4", layers=BB34_LAYERS, variants=BB34_VARIANTS,
        official=False,
        notes="A community release that replaces much of BoingBags 1 and 2 "
              "with newer versions and adds support for large drives. It "
              "expects BoingBags 1 and 2 underneath it."),
)

BAGS_BY_KEY = {bag.key: bag for bag in BAGS}


def for_release(release: str) -> tuple[Bag, ...]:
    """The packs that belong to one AmigaOS release, oldest first."""
    return tuple(sorted(
        (bag for bag in BAGS if bag.release == release), key=lambda bag: bag.order
    ))


def describe_bags() -> list[dict]:
    return [
        {
            "key": bag.key,
            "label": bag.label,
            "release": bag.release,
            "official": bag.official,
            "needsUpdater": bool(bag.locked_payloads),
            "notes": bag.notes,
        }
        for bag in BAGS
    ]


# ----------------------------------------------------------------------
# The machine the drive is for
# ----------------------------------------------------------------------

def processor_of(machine: str, addons) -> tuple[str, bool]:
    """The processor a profile describes, and whether it has an FPU.

    A PiStorm presents a 68040 with its FPU, whatever board it is fitted to,
    which is how Emu68 describes itself.
    """
    chosen = set(addons or [])
    if "acc-68060" in chosen:
        return "68060", True
    if chosen & {"acc-68040", "pistorm", "pistorm32"}:
        return "68040", True
    if str(machine).casefold() == "a4000":
        return "68040", True
    if "acc-68030" in chosen or str(machine).casefold() == "a3000":
        return "68030", True
    return "68020", False


def rule_holds(rule: str, machine: str, processor: str, has_fpu: bool) -> bool:
    if rule == "always":
        return True
    if rule == "cpu68060":
        return processor == "68060"
    if rule == "cpu68060+fpu":
        return processor == "68060" and has_fpu
    if rule == "cpu68040":
        return processor == "68040"
    if rule == "cpu68040+fpu":
        return processor == "68040" and has_fpu
    return rule == str(machine).casefold()


# ----------------------------------------------------------------------
# Packs inside the archives a person supplied
# ----------------------------------------------------------------------

@dataclasses.dataclass
class OpenedArchive:
    name: str
    archive: LHAArchive


@dataclasses.dataclass
class FoundPack:
    """One pack, located inside one of the archives."""

    bag: Bag
    source: OpenedArchive
    #: The path of the pack's drawer inside the archive.
    root: str

    def members(self, below: str = "") -> list[tuple[str, LHAMember]]:
        """Members under a drawer of the pack, with paths relative to it."""
        prefix = (join_path(self.root, below) + "/").casefold()
        return [
            (member.path[len(prefix):], member)
            for member in self.source.archive.members
            if member.path.casefold().startswith(prefix) and len(member.path) > len(prefix)
        ]

    def member(self, path: str) -> LHAMember | None:
        found = self.source.archive.find(join_path(self.root, path))
        return None if found is None or found.is_directory else found

    def read(self, member: LHAMember) -> bytes:
        return self.source.archive.read(member)

    def _payloads(self) -> list[zipfile.ZipFile]:
        opened: list[zipfile.ZipFile] = []
        for name in self.bag.locked_payloads:
            member = self.member(name)
            if member is None:
                continue
            try:
                opened.append(zipfile.ZipFile(io.BytesIO(self.read(member))))
            except (zipfile.BadZipFile, LHAError, OSError):
                continue
        return opened

    @property
    def locked(self) -> bool:
        """Whether the payload really is encrypted, rather than assumed to be."""
        return any(
            item.flag_bits & 0x1
            for bundle in self._payloads() for item in bundle.infolist()
        )

    def locked_entries(self) -> list[str]:
        """The names inside the encrypted payloads.

        A ZIP lists its entries in the clear even when their contents are
        encrypted, which is what lets a report name each fix that could not be
        applied instead of saying only that some exist.
        """
        return [
            item.filename
            for bundle in self._payloads() for item in bundle.infolist()
            if not item.is_dir()
        ]


def open_archives(packs: list[tuple[str, bytes]]) -> list[OpenedArchive]:
    opened: list[OpenedArchive] = []
    for name, data in packs:
        try:
            opened.append(OpenedArchive(name, LHAArchive(data)))
        except LHAError as exc:
            raise DiskError(f"{name} could not be read as an update pack. {exc}") from exc
    return opened


def _root_in(archive: LHAArchive, bag: Bag) -> str | None:
    """Where a pack's drawer is inside an archive, at whatever depth.

    Archives are not consistent about nesting. ``BoingBag39-1.lha`` puts
    ``BoingBag3.9-1`` at the top, while a collection carries three packs side
    by side, so the drawer is looked for by name.
    """
    wanted = bag.root.casefold()
    for member in archive.members:
        parts = member.path.split("/")
        for depth, part in enumerate(parts[:-1]):
            if part.casefold() == wanted:
                return "/".join(parts[:depth + 1])
    return None


def packs_in(
    archives: list[OpenedArchive], release: str, chosen: list[str] | None = None
) -> list[FoundPack]:
    """The packs for a release found in the archives, oldest first.

    ``chosen`` names the packs wanted. When it is empty, every pack that is on
    by default is taken. A pack present in two archives is taken from the
    first.
    """
    wanted = {str(key) for key in (chosen or [])}
    found: list[FoundPack] = []
    for bag in for_release(release):
        if wanted and bag.key not in wanted:
            continue
        if not wanted and not bag.default_on:
            continue
        for source in archives:
            root = _root_in(source.archive, bag)
            if root is not None:
                found.append(FoundPack(bag, source, root))
                break
    return found


def unrecognised(archives: list[OpenedArchive], release: str) -> list[str]:
    """Archives that hold no pack for this release, so nothing came of them."""
    bags = for_release(release)
    return [
        source.name for source in archives
        if not any(_root_in(source.archive, bag) is not None for bag in bags)
    ]


def survey(archives: list[OpenedArchive]) -> list[dict]:
    """Every pack in the archives, of either release, for a dialog to show."""
    described: list[dict] = []
    seen: set[str] = set()
    for bag in sorted(BAGS, key=lambda item: (item.release, item.order)):
        for source in archives:
            root = _root_in(source.archive, bag)
            if root is None or bag.key in seen:
                continue
            seen.add(bag.key)
            found = FoundPack(bag, source, root)
            locked = found.locked_entries() if found.locked else []
            described.append({
                "key": bag.key,
                "label": bag.label,
                "release": bag.release,
                "archive": source.name,
                "official": bag.official,
                "defaultOn": bag.default_on,
                "lockedFiles": len(locked),
                "notes": bag.notes,
            })
    return described


def plan_pack(found: FoundPack, tree: SystemTree, machine: str, addons) -> dict:
    """Lay a pack's plain files over the system, and choose its variants.

    A layer that is not in this copy of the pack is passed over. The packs
    differ, BoingBag 2 for 3.5 ships no printer drivers where BoingBag 1 does,
    so an absent tree is a fact about the release and not a fault.
    """
    bag = found.bag
    processor, has_fpu = processor_of(machine, addons)
    layers: list[dict] = []
    total = 0
    for layer in sorted(bag.layers, key=lambda item: item.order):
        count = 0
        for relative, member in found.members(layer.source):
            target = join_path(layer.destination, relative)
            if member.is_directory:
                tree.add_drawer(target)
                continue
            if tree.add(
                target,
                lambda member=member: found.read(member),
                member.original_size,
                bag.label,
                comment=member.comment,
            ):
                count += 1
        if count:
            layers.append({
                "label": layer.label,
                "destination": layer.destination or ":",
                "files": count,
            })
            total += count

    variants: list[dict] = []
    for variant in bag.variants:
        for rule, source in variant.candidates:
            if not rule_holds(rule, machine, processor, has_fpu):
                continue
            member = found.member(source)
            if member is None:
                continue
            tree.add(
                join_path(variant.destination, variant.newname),
                lambda member=member: found.read(member),
                member.original_size,
                bag.label,
            )
            variants.append({
                "label": variant.label,
                "source": source.rsplit("/", 1)[-1],
                "installedAs": join_path(variant.destination, variant.newname),
            })
            total += 1
            break

    return {
        "key": bag.key,
        "label": bag.label,
        "archive": found.source.name,
        "official": bag.official,
        "files": total,
        "layers": layers,
        "variants": variants,
        "processor": processor,
        "locked": False,
        "warnings": [],
    }


__all__ = [
    "BAGS",
    "BAGS_BY_KEY",
    "Bag",
    "FoundPack",
    "Layer",
    "OpenedArchive",
    "Variant",
    "describe_bags",
    "for_release",
    "open_archives",
    "packs_in",
    "plan_pack",
    "processor_of",
    "rule_holds",
    "survey",
    "unrecognised",
]
