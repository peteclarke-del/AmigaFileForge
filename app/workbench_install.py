"""Preparing a drive by installing AmigaOS onto it from the Workbench floppies.

A blank hard drive is not a machine you can use. Before a title can be
installed, or a Shell opened, or an icon double-clicked, the drive has to carry
an operating system, and the only lawful source of one is the operator's own
Workbench disks. This installs them.

AmigaOS is unusual in being installable without running any Amiga code. The
Install disk's script does ask questions - which printer, which language - but
the part that produces a working system is a copy: Workbench and Extras merge
into the root of the drive, and Fonts, Locale, Storage, Classes and Backdrops
become drawers of their own. That is a job this application can do in full and
correctly, which is why it is offered as an install rather than as a staging
drawer the operator has to finish by hand.

Disks are recognised by the **volume name inside the image** rather than by
file name. ADF collections are named inconsistently - ``wb31_workbench.adf``,
``Workbench 3.1 (Disk 2 of 6).adf``, ``disk02.adf`` - and a renamed file says
nothing at all about its contents, while the volume name is written by
Commodore and travels with the data.

The release is decided once, from the Workbench disk, and every other disk is
then matched against it. Mixing releases is the classic way to end up with a
drive that boots to a Guru: a 2.0 Extras drawer on a 3.1 system, or a Locale
disk from a release that had no Locale, produces a system whose parts disagree
about what the others provide.

Where two disks carry the same file, the first one copied wins, and the order
is fixed so that the winner is the right one: the Workbench disk is copied
before Extras so that its full ``C:``, ``L:`` and ``Libs:`` are not overwritten
by the cut-down copies the other disks carry. The Install disk is kept in its
own drawer for exactly the same reason.

This follows the layout the AmigaOS 3.x install script produces, which is also
what the PiStorm imager writes, so a drive prepared here looks to a person and
to a program like one prepared the usual way.
"""

from __future__ import annotations

import dataclasses
import re

from . import volume_copy
from . import progress as progress_module
from .errors import DiskError
from .image_session import ImageSession


@dataclasses.dataclass(frozen=True)
class Role:
    """One disk of an AmigaOS release, and where its contents belong."""

    key: str
    label: str
    #: Where the disk lands on the drive. An empty string means the volume root.
    destination: str
    #: Volume-name prefixes that identify this disk, lower case, spaces removed.
    prefixes: tuple[str, ...]
    required: bool = False
    #: Copy order. Lower numbers are copied first and therefore win a clash.
    order: int = 50
    note: str = ""


#: Order matters where disks merge into the same place: the Workbench disk must
#: be copied before Extras so that its versions of shared files win.
ROLES: tuple[Role, ...] = (
    Role("workbench", "Workbench", "", ("workbench",), required=True, order=10,
         note="The system itself: C, L, Libs, Devs, S and the desktop."),
    Role("extras", "Extras", "", ("extras",), order=20,
         note="Tools, utilities and demonstration programs, merged into the root."),
    Role("fonts", "Fonts", "Fonts", ("fonts",), order=30,
         note="Screen and printer fonts."),
    Role("locale", "Locale", "Locale", ("locale",), order=40,
         note="Languages, countries and keymaps. AmigaOS 3.x only."),
    Role("storage", "Storage", "Storage", ("storage",), order=50,
         note="Drivers and monitors held back until they are wanted."),
    Role("classes", "Classes", "Classes", ("classes",), order=55,
         note="BOOPSI classes and datatypes."),
    Role("glowicons", "GlowIcons", "", ("glowicons",), order=60,
         note="The AmigaOS 3.5 icon set, merged into the root."),
    Role("backdrops", "Backdrops", "Backdrops", ("backdrops",), order=65,
         note="Desktop pictures."),
    Role("install", "Install", "Install", ("install",), order=70,
         note="Kept in its own drawer: its cut-down C and Libs must not "
              "overwrite the full ones from the Workbench disk."),
)

ROLES_BY_KEY = {role.key: role for role in ROLES}

#: Drawers the AmigaOS install script creates that no disk provides. A system
#: without ``T:`` cannot write a temporary file, and Workbench will not show a
#: Trashcan that is not there.
CREATED_DRAWERS = ("T", "Trashcan", "Devs/DOSDrivers", "Prefs/Env-Archive")

#: The script Kickstart runs to bring the system up. Its absence is the
#: difference between a drive that boots and one that asks for a floppy.
STARTUP_SEQUENCE = "S/Startup-Sequence"

#: How the volume root is written when a disk lands in it rather than in a
#: drawer of its own. AmigaDOS spells a volume root as a bare colon.
ROOT_DESTINATION = ":"

#: "Workbench3.1" and "Extras3.2" carry the release in the volume name; Fonts
#: and Locale do not, so their release has to come from the file name instead.
_VERSION_IN_VOLUME = re.compile(r"([0-9]+\.[0-9]+(?:\.[0-9]+)?)$")
_VERSION_IN_FILENAME = re.compile(r"\bv?([0-9]+\.[0-9]+(?:\.[0-9]+)?)")

#: Words a dump adds to a volume name to say how good it is. They are not part
#: of the release and must not be read as one.
_QUALITY_MARKERS = (
    ("verified", 3), ("!", 3), ("good", 2), ("alternate", 1), ("alt", 1),
    ("modified", 0), ("hack", 0), ("crack", 0), ("beta", 0),
)


def normalise_version(text: str) -> str:
    """Fold release spellings together: 2.05 and 2.0 are the same release."""
    parts = str(text or "").split(".")
    if len(parts) >= 2:
        minor = parts[1].rstrip("0") or "0"
        if len(parts[1]) > 1 and parts[1].startswith("0"):
            minor = "0"
        return f"{parts[0]}.{minor}"
    return str(text or "")


def role_for(volume_name: str) -> Role | None:
    """Which install disk this volume name identifies, if any."""
    lowered = str(volume_name or "").strip().lower().replace(" ", "")
    for role in ROLES:
        if any(lowered.startswith(prefix) for prefix in role.prefixes):
            return role
    return None


def version_of(volume_name: str, file_name: str = "") -> str:
    """Work out which AmigaOS release a disk belongs to."""
    match = _VERSION_IN_VOLUME.search(str(volume_name or "").strip().replace(" ", ""))
    if match:
        return normalise_version(match.group(1))
    match = _VERSION_IN_FILENAME.search(str(file_name or ""))
    if match:
        candidate = normalise_version(match.group(1))
        # A file named "disk2.0" is a disk number, not a release. Only accept a
        # release the Amiga actually had.
        major = candidate.split(".")[0]
        if major.isdigit() and 1 <= int(major) <= 4:
            return candidate
    return ""


def quality_of(volume_name: str, file_name: str = "") -> int:
    """How much a dump's own labelling says it should be trusted.

    A collection normally holds several dumps of each disk. Where they are
    otherwise equal, the one that says it was verified is the one to use, and
    the one that says it was modified or cracked is the one to avoid.
    """
    haystack = f"{volume_name} {file_name}".lower()
    for marker, score in _QUALITY_MARKERS:
        if marker in haystack:
            return score
    return 2


def describe_roles() -> list[dict]:
    """The disk set, for an interface that has to explain what it wants."""
    return [
        {
            "key": role.key,
            "label": role.label,
            "destination": role.destination or ROOT_DESTINATION,
            "required": role.required,
            "order": role.order,
            "note": role.note,
        }
        for role in sorted(ROLES, key=lambda item: item.order)
    ]


def choose_set(matches: list[dict], version: str = "") -> tuple[dict[str, dict], str]:
    """Pick the best disk for each role, keeping the whole set one release.

    The release is decided first, from the Workbench disk, because that is the
    disk that defines the system. Every other disk is then matched to it, and a
    disk from another release is left out rather than mixed in.
    """
    usable = [match for match in matches if match.get("role")]
    if not version:
        candidates = [match for match in usable if match["role"] == "workbench"]
        if candidates:
            version = max(
                candidates,
                key=lambda match: (match["quality"], match["fileCount"]),
            )["version"]
        if not version:
            releases = available_versions(usable)
            version = releases[0] if releases else ""

    def score(match: dict) -> tuple:
        # A matching release outranks dump quality; an unversioned disk (Fonts,
        # Locale) is acceptable but never beats an exact match.
        return (
            2 if match["version"] == version else (1 if not match["version"] else 0),
            match["quality"],
            match["fileCount"],
        )

    best: dict[str, dict] = {}
    for match in usable:
        if match["version"] and version and match["version"] != version:
            continue
        current = best.get(match["role"])
        if current is None or score(match) > score(current):
            best[match["role"]] = match
    return best, version


def available_versions(matches: list[dict]) -> list[str]:
    """Releases for which a Workbench disk is present, newest first."""
    versions = {
        match["version"] for match in matches
        if match.get("role") == "workbench" and match.get("version")
        and match["version"].split(".")[0].isdigit()
        and 1 <= int(match["version"].split(".")[0]) <= 4
    }
    return sorted(
        versions, key=lambda value: [int(part) for part in value.split(".")], reverse=True
    )


def missing_roles(chosen: dict[str, dict]) -> list[Role]:
    """Required disks that are not in the chosen set."""
    return [role for role in ROLES if role.required and role.key not in chosen]


class WorkbenchInstallMixin:
    """Install AmigaOS onto a volume from the operator's own Workbench disks."""

    def identify_workbench_disc(self, session: ImageSession) -> dict:
        """Read a disc's volume name and work out which install disk it is."""
        volume = ""
        entries: list[dict] = []
        error = ""
        try:
            volume = volume_copy.volume_name(self, session)
            entries = [
                entry for entry in volume_copy.walk_volume(self, session)
                if not entry["directory"]
            ]
        except DiskError as exc:
            error = str(exc)
        role = role_for(volume)
        return {
            "imageId": session.id,
            "source": session.name,
            "volume": volume,
            "role": role.key if role else "",
            "roleLabel": role.label if role else "",
            "destination": (role.destination or ROOT_DESTINATION) if role else "",
            "version": version_of(volume, session.name),
            "quality": quality_of(volume, session.name),
            "fileCount": len(entries),
            "bytes": sum(int(entry.get("length") or 0) for entry in entries),
            "error": error,
        }

    def survey_workbench_discs(
        self, sessions: list[ImageSession], *, version: str = ""
    ) -> dict:
        """Identify a pile of discs and propose the set to install from.

        The operator points at a folder rather than at seven individual files,
        so most of what arrives is not an install disk at all. Saying plainly
        which disc was recognised as what, and which of them was chosen, is the
        difference between a set the operator can correct and one they have to
        take on trust.
        """
        matches = [self.identify_workbench_disc(session) for session in sessions]
        chosen, release = choose_set(matches, version)
        for match in matches:
            match["chosen"] = chosen.get(match["role"], {}).get("imageId") == match["imageId"]
        return {
            "discs": matches,
            "chosen": {key: match["imageId"] for key, match in chosen.items()},
            "version": release,
            "versions": available_versions(matches),
            "missing": [
                {"key": role.key, "label": role.label} for role in missing_roles(chosen)
            ],
            "unrecognised": [
                match["source"] for match in matches if not match["role"]
            ],
            "roles": describe_roles(),
        }

    def install_workbench(
        self,
        target: ImageSession,
        discs: dict[str, ImageSession],
        *,
        version: str = "",
        create_drawers: bool = True,
        progress: progress_module.Progress | None = None,
    ) -> dict:
        """Copy the chosen Workbench disks into a volume, in the right order.

        The volume is written into rather than formatted. An operator who has
        already partitioned and named a drive does not expect preparing it to
        throw that away, and a Workbench install onto a drive that has a title
        on it already is a perfectly ordinary thing to want.

        Files already on the volume are left alone. That is what makes the copy
        order meaningful, Workbench before Extras, and it also means running
        this twice does not undo hand edits made in between.
        """
        report = progress_module.reporter(progress)
        self.require_mounted_volume(target)
        if self.summary(target).get("readOnly"):
            raise DiskError(f"{target.name} is open read-only, so nothing can be installed onto it.")
        self.require_writable_geometry(target)
        if not self.mountable(target):
            raise DiskError("Workbench can only be installed into an AmigaDOS volume.")

        ordered = self._ordered_install_set(discs)
        installed: list[dict] = []
        warnings: list[str] = []
        total_copied = 0
        total_skipped = 0

        for position, (role, source) in enumerate(ordered):
            where = role.destination or ROOT_DESTINATION
            report(f"Installing {role.label} into {where}", position, len(ordered))
            copied = volume_copy.copy_volume_tree(
                self, source, target, role.destination,
                existing="skip",
                progress=report,
                message=f"Reading {role.label}",
            )
            warnings.extend(copied.warnings)
            total_copied += copied.file_count
            total_skipped += len(copied.skipped)
            installed.append({
                "role": role.key,
                "label": role.label,
                "source": source.name,
                "volume": volume_copy.volume_name(self, source),
                "destination": where,
                "copied": copied.file_count,
                "skipped": len(copied.skipped),
            })

        drawers: list[str] = []
        if create_drawers:
            report("Creating the drawers the install script makes", len(ordered), len(ordered))
            drawers = self._create_system_drawers(target, warnings)

        warnings.extend(self._workbench_readiness(target))
        self._persist_session(target)
        report("Installed", len(ordered), len(ordered))
        return {
            "version": version,
            "discs": installed,
            "copied": total_copied,
            "skipped": total_skipped,
            "drawers": drawers,
            "warnings": warnings,
        }

    @staticmethod
    def _ordered_install_set(discs: dict[str, ImageSession]) -> list[tuple[Role, ImageSession]]:
        """The chosen discs in copy order, refusing a set that cannot work.

        The order is the whole reason a shared file ends up with the right
        contents, so it is decided here rather than left to whatever order the
        caller happened to build its mapping in.
        """
        selected = {key: session for key, session in discs.items() if key in ROLES_BY_KEY}
        if not selected:
            raise DiskError("Choose at least the Workbench disk before installing.")
        missing = [role for role in ROLES if role.required and role.key not in selected]
        if missing:
            raise DiskError(
                "Workbench cannot be installed without the "
                + ", ".join(role.label for role in missing)
                + " disk."
            )
        return sorted(
            ((ROLES_BY_KEY[key], session) for key, session in selected.items()),
            key=lambda item: item[0].order,
        )

    def _create_system_drawers(self, target: ImageSession, warnings: list[str]) -> list[str]:
        """Make the working drawers the AmigaOS install script creates.

        No disk provides them and the system needs them: a machine with no
        ``T:`` cannot write a temporary file, and Workbench will not show a
        Trashcan that is not there.
        """
        created: list[str] = []
        for drawer in CREATED_DRAWERS:
            if volume_copy.drawer_exists(self, target, drawer):
                continue
            try:
                self.make_directory(target, drawer)
            except DiskError as exc:
                warnings.append(f"{drawer} could not be created: {exc}")
                continue
            created.append(drawer)
        return created

    def _workbench_readiness(self, target: ImageSession) -> list[str]:
        """Reasons this volume still will not boot, said plainly.

        The files being right is only half of a drive that starts. A hard
        drive boots from the flag in its Rigid Disk Block rather than from a
        floppy boot block, and a volume with no startup script does not boot
        at all, so both are reported rather than left to be discovered when the
        machine will not come up.
        """
        warnings: list[str] = []
        summary = self.summary(target)
        if summary.get("hardDisk") and not summary.get("bootable"):
            warnings.append(
                "This partition is not marked bootable in the Rigid Disk Block, so the "
                "machine will not start from it. Mark it bootable to boot the drive."
            )
        if not volume_copy.entry_exists(self, target, STARTUP_SEQUENCE):
            warnings.append(
                f"{STARTUP_SEQUENCE} is not present, so the volume will not boot on its own. "
                "Check that the disc chosen as Workbench really is the Workbench disk."
            )
        return warnings


__all__ = [
    "CREATED_DRAWERS",
    "ROOT_DESTINATION",
    "STARTUP_SEQUENCE",
    "ROLES",
    "ROLES_BY_KEY",
    "Role",
    "WorkbenchInstallMixin",
    "available_versions",
    "choose_set",
    "describe_roles",
    "missing_roles",
    "normalise_version",
    "quality_of",
    "role_for",
    "version_of",
]
