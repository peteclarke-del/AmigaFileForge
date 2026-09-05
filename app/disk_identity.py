"""Working out what a disk is and how it starts, from the disk itself.

An imported disk arrives with a volume name, a set of files and nothing else.
This module reads that evidence and proposes a title, the file that starts the
software, and the stack that file needs, along with the reasoning behind each
conclusion so a person can check it rather than being asked to trust it.

Nothing here guesses silently. Every proposal carries its evidence, and a
proposal the evidence does not support is marked ambiguous so the caller asks
rather than writes. That distinction matters because these conclusions are
offered as defaults during an import, where a confident wrong answer is worse
than an admitted uncertainty.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .errors import DiskError
from .filename_policy import AMIGA_NAME_LIMIT
from .metadata_lookup import enrich_from_distribution_filename
from .ofs_compat import (
    _EXECUTE_TARGET,
    _STACK_SETTING,
    MAX_STACK,
    MIN_STACK,
    _looks_like_amiga_script,
)
from . import amiga_paths

if TYPE_CHECKING:  # pragma: no cover - imported for type checkers only
    from .disk_service import DiskService
    from .image_session import ImageSession


#: The stack AmigaDOS gives a shell command when nothing sets one.
DEFAULT_STACK = 4096

#: The first longword of every AmigaDOS load file.
HUNK_HEADER = b"\x00\x00\x03\xf3"

#: Names a disk's own launcher conventionally uses, in priority order.
CONVENTIONAL_LAUNCHERS = (
    "DISKMENU",
    "GAMEMENU",
    "MENU",
    "LOADER",
    "STARTUP",
    "START",
    "GAME",
    "RUN",
)

#: A file by this name is the disk's intended entry point and outranks the
#: Startup-Sequence, because it is what the disk's own author wrote to be run.
PRIORITY_LAUNCHER = "DISKMENU"

#: AmigaDOS commands that start something, mapped to the single letter a
#: record stores. An empty letter means the interpreter starts it.
LAUNCH_ACTIONS = {
    "AMIGABASIC": "",
    "BASIC": "",
    "CHAIN": "",
    "CH": "",
    "RUN": "R",
    "EXECUTE": "E",
    "EXEC": "E",
    "LOADWB": "L",
    "LOAD": "L",
}


def _clean_title(value: str) -> str:
    value = re.sub(r"[_-]+", " ", value or "")
    value = re.sub(r"\b(?:SIDE|DISC|DISK)\s*[012AB]?\b", "", value, flags=re.I)
    return re.sub(r"\s+", " ", value).strip().title()


def _exec_script(data: bytes) -> str | None:
    """Return an AmigaDOS command file as text, or None when it looks binary."""
    if not data or b"\0" in data:
        return None
    text = data.decode("latin-1")
    meaningful = [character for character in text if character not in "\r\n\t\f"]
    if not meaningful:
        return None
    printable = sum(character.isprintable() for character in meaningful)
    return text if printable / len(meaningful) >= 0.9 else None


def _read_launcher(
    service: DiskService,
    session: ImageSession,
    row: dict,
    *,
    path: str,
) -> bytes:
    """Read one entry's contents, or empty bytes when it cannot be read."""
    source_path = amiga_paths.join(path, str(row["name"]))
    try:
        return service.read_file(session, source_path) or b""
    except (KeyError, OSError, UnicodeError, RuntimeError):
        return b""


def _read_exec_script(
    service: DiskService,
    session: ImageSession,
    row: dict,
    *,
    path: str,
) -> str | None:
    return _exec_script(_read_launcher(service, session, row, path=path))


def is_executable(data: bytes) -> bool:
    """Report whether the bytes are an AmigaDOS load file."""
    return data[:4] == HUNK_HEADER


def is_amigabasic(data: bytes) -> bool:
    """Report whether the bytes are a saved AmigaBASIC program."""
    if not data or is_executable(data):
        return False
    try:
        from amiganut.basic import Verdict, detect
    except ImportError:  # pragma: no cover - the tokeniser is always present
        return False
    return detect(data).verdict in {Verdict.BASIC, Verdict.BASIC_TRAILING}


def _detect_launcher(
    service: DiskService,
    session: ImageSession,
    entries: list[dict],
    *,
    path: str,
) -> tuple[tuple[str, str] | None, dict | None, int, list[str], list[str]]:
    """Choose the file that starts this software, and say why."""
    by_name = {str(row.get("name", "")).upper(): row for row in entries}
    evidence: list[str] = []
    warnings: list[str] = []
    chosen: tuple[str, str] | None = None
    launch_signals = 0

    priority_row = by_name.get(PRIORITY_LAUNCHER)
    if priority_row:
        content = _read_launcher(service, session, priority_row, path=path)
        command = "AMIGABASIC" if is_amigabasic(content) else "RUN"
        chosen = (command, str(priority_row["name"]))
        launch_signals = 1
        evidence.append(
            f"Found {priority_row['name']}; it takes priority over the "
            f"Startup-Sequence and will be launched with {command.title()}"
        )

    boot_row = by_name.get("STARTUP-SEQUENCE")
    if chosen is None and boot_row:
        boot = _read_exec_script(service, session, boot_row, path=path)
        if boot is not None:
            chosen = ("EXECUTE", str(boot_row["name"]))
            launch_signals = 1
            evidence.append(
                "Found a readable Startup-Sequence; it will be launched with Execute"
            )
            commands = [
                match.group(1).upper()
                for match in re.finditer(
                    r"(?im)^\s*(AmigaBASIC|Run|Execute|LoadWB)\b",
                    boot,
                )
            ]
            if commands:
                evidence.append(
                    f"Startup-Sequence contains {len(commands)} recognised launch "
                    f"command{'' if len(commands) == 1 else 's'}"
                )
        else:
            warnings.append(
                "The Startup-Sequence is empty, unreadable, or appears to be binary, "
                "so it cannot be used with Execute."
            )

    if chosen is None:
        conventional_rows = [
            by_name[name] for name in CONVENTIONAL_LAUNCHERS if name in by_name
        ]
        if not conventional_rows:
            conventional_rows = sorted(
                (
                    row
                    for row in entries
                    if re.fullmatch(
                        r"[A-Z0-9_-]*MENU[A-Z0-9_-]*",
                        str(row.get("name", "")),
                        re.I,
                    )
                ),
                key=lambda row: (
                    len(str(row.get("name", ""))),
                    str(row.get("name", "")).casefold(),
                ),
            )
        if conventional_rows:
            conventional = conventional_rows[0]
            script = _read_exec_script(service, session, conventional, path=path)
            if script is not None:
                chosen = ("EXECUTE", str(conventional["name"]))
                evidence.append(
                    f"Examined {conventional['name']} and found a readable "
                    "AmigaDOS script; it will be launched with Execute"
                )
            else:
                content = _read_launcher(service, session, conventional, path=path)
                action = "AMIGABASIC" if is_amigabasic(content) else "RUN"
                chosen = (action, str(conventional["name"]))
                evidence.append(
                    f"Examined conventional launcher {conventional['name']}; "
                    f"its contents indicate {action.title()}"
                )
            launch_signals = 1
            if len(conventional_rows) > 1:
                warnings.append(
                    "Several conventional launchers were found; "
                    f"{conventional['name']} was selected by launcher priority."
                )

    if chosen is None:
        basic = [
            row
            for row in entries
            if is_amigabasic(_read_launcher(service, session, row, path=path))
        ]
        if len(basic) == 1:
            chosen = ("AMIGABASIC", str(basic[0]["name"]))
            launch_signals = 1
            evidence.append("Found one saved AmigaBASIC program on the disk")
        elif not entries:
            warnings.append("The disk is empty.")
        else:
            warnings.append("No single launch program could be identified.")
    row = by_name.get(chosen[1].upper()) if chosen else None
    return chosen, row, launch_signals, evidence, warnings


def launch_stack(
    service: DiskService,
    session: ImageSession,
    directory: str,
    filename: str,
    action: str,
) -> tuple[str | None, str, bool]:
    """Return (stack, evidence, applicable) for one launch path.

    The stack a program needs is a property of the script that starts it, so
    it is read out of that script rather than guessed. A program started
    directly by ``Run`` sets its own stack, which is why the third value says
    whether a stack figure applies at all.
    """
    entries = service.list_directory(session, directory)["entries"]
    launch = next(
        (
            item
            for item in entries
            if item.get("type") not in {"dir", "directory"}
            and str(item.get("name", "")).casefold() == filename.casefold()
        ),
        None,
    )
    launch_path = amiga_paths.join(directory, filename)
    if launch is None:
        return None, f"launch file {launch_path} is absent", True
    action = action.upper()
    if action not in {"", "E"}:
        return None, f"{action} starts a program directly, so no Stack command runs", False
    if action == "":
        # AmigaBASIC is started by the interpreter, which allocates the stack
        # itself, so the record carries the interpreter's own default.
        return (
            str(DEFAULT_STACK),
            f"{launch_path} is an AmigaBASIC program, which runs on the "
            f"default {DEFAULT_STACK}-byte stack",
            True,
        )

    data = service.read_file(session, launch_path)
    if not _looks_like_amiga_script(data):
        return None, f"{launch_path} is not a readable AmigaDOS script", True
    text = data.decode("latin-1", "replace")
    explicit = _STACK_SETTING.search(text)
    if explicit:
        value = int(explicit.group(1))
        if MIN_STACK <= value <= MAX_STACK:
            return str(value), f"{launch_path} explicitly sets Stack {value}", True
        return None, f"{launch_path} sets an out-of-range Stack of {value}", True
    chained = _EXECUTE_TARGET.search(text)
    if chained:
        reference = chained.group(1).strip().strip('"')
        target_path = (
            reference
            if reference.startswith((":", "/")) or ":" in reference
            else amiga_paths.join(directory, reference)
        )
        try:
            nested = service.read_file(session, target_path)
        except (KeyError, OSError, RuntimeError):
            nested = b""
        if nested and not _looks_like_amiga_script(nested):
            return (
                None,
                f"{launch_path} runs the executable {target_path}; no script stack applies",
                False,
            )
        inner = _STACK_SETTING.search(nested.decode("latin-1", "replace"))
        if inner:
            value = int(inner.group(1))
            if MIN_STACK <= value <= MAX_STACK:
                return (
                    str(value),
                    f"{launch_path} runs {target_path}, which sets Stack {value}",
                    True,
                )
    return None, f"{launch_path} does not set a stack size of its own", True


def analyse_directory(
    service: DiskService,
    session: ImageSession,
    path: str = "",
) -> dict:
    """Describe the software in one drawer, or in a volume root.

    The same reading serves a floppy and a drawer on a hard drive, because on
    an Amiga they are the same thing: a directory holding the files that make
    up one piece of software.
    """
    listing = service.list_directory(session, path)
    entries = [
        row for row in listing["entries"] if row.get("type") not in {"dir", "directory"}
    ]
    chosen, row, launch_signal_count, evidence, warnings = _detect_launcher(
        service, session, entries, path=path
    )
    filename = chosen[1] if chosen else ""
    action = LAUNCH_ACTIONS.get(chosen[0], "") if chosen else ""
    volume_title = str(
        listing.get("directoryTitle")
        or (amiga_paths.leaf(path) if amiga_paths.normalise(path) else listing.get("title") or "")
    )
    title = _clean_title(volume_title)
    generic_title = not title or bool(
        re.fullmatch(r"(?:DIS[CK]|UNTITLED|EMPTY)\s*\d*", title, re.I)
    )

    page = str(DEFAULT_STACK)
    if filename:
        try:
            inferred, page_evidence, applicable = launch_stack(
                service, session, path, filename, action
            )
        except (KeyError, OSError, RuntimeError):
            inferred, page_evidence, applicable = None, "", True
        if inferred and applicable:
            page = inferred
            evidence.append(f"Stack inferred from launch path: {page_evidence}")

    confidence = 0
    if title and not generic_title:
        confidence += 25
    if chosen:
        confidence += 45
    if launch_signal_count == 1:
        confidence += 20
    if row:
        confidence += 10

    candidates = [{"name": str(item["name"]), "path": path} for item in entries]
    for directory in (
        item for item in listing["entries"] if item.get("type") in {"dir", "directory"}
    ):
        child_path = amiga_paths.join(path, directory["name"])
        try:
            child_entries = service.list_directory(session, child_path)["entries"]
        except (DiskError, RuntimeError):
            continue
        candidates.extend(
            {"name": str(item["name"]), "path": child_path}
            for item in child_entries
            if item.get("type") not in {"dir", "directory"}
        )

    metadata = {
        "title": title,
        "publisher": "",
        "filename": filename[:AMIGA_NAME_LIMIT],
        "action": action,
        "page": page,
        "diskTitle": volume_title[:AMIGA_NAME_LIMIT],
        "path": path,
        "confidence": confidence,
        "ambiguous": confidence < 75 or not filename or generic_title,
        "launchObvious": bool(filename and launch_signal_count == 1),
        "evidence": evidence,
        "warnings": warnings,
        "sources": [],
        "matches": [],
        "launchCandidates": candidates,
    }
    source_name = getattr(session, "ffs_source_names", {}).get(path)
    if source_name:
        enrich_from_distribution_filename(metadata, source_name)
    elif getattr(session, "distribution_name", None):
        enrich_from_distribution_filename(metadata, session.distribution_name)
    return metadata


__all__ = [
    "CONVENTIONAL_LAUNCHERS",
    "DEFAULT_STACK",
    "LAUNCH_ACTIONS",
    "PRIORITY_LAUNCHER",
    "analyse_directory",
    "is_amigabasic",
    "is_executable",
    "launch_stack",
]
