"""The one place Amiga File Forge builds and takes apart inner paths.

An AmigaDOS path separates its components with ``/`` and names its volume root
with a bare ``:``. The separator matters: Amiga filenames routinely contain
full stops -- ``Startup-Sequence`` sits beside ``Disk.info`` and ``game.exe``
in the same drawer -- so a dot cannot be a separator without corrupting
ordinary names.

Requests that arrive from an older client, a saved workspace or a stored
recipe may still spell the root ``$``. Those are accepted and normalised here
rather than being handled again at each call site.
"""

from __future__ import annotations

SEPARATOR = "/"

#: Every spelling of "the root of this volume" that the workbench accepts.
ROOT_TOKENS = {"", "$", ":", "/", "$."}

#: The canonical root, which is the empty path.
ROOT = ""

#: What the user sees when a pane is showing the volume root.
ROOT_DISPLAY = ":"


def is_root(path: str | None) -> bool:
    """True when this path names the volume root, however it was spelled."""
    return str(path or "").strip() in ROOT_TOKENS


def split(path: str | None) -> list[str]:
    """Split an inner path into its components, discarding root spellings.

    A path written ``$.C.List`` came from a saved workspace, a stored recipe
    or a bookmark made before the separator changed. It is recognised by its
    leading ``$`` together with the absence of any ``/``, and is split on full
    stops just this once so the entry it names is still reachable. Nothing
    writes that form any more.
    """
    text = str(path or "").strip()
    if is_root(text):
        return []
    if text.startswith("$") and SEPARATOR not in text:
        return [part for part in text[1:].strip(".").split(".") if part]
    if text.startswith(("$", ":")):
        text = text[1:]
    return [part for part in text.strip(SEPARATOR).split(SEPARATOR) if part]


def normalise(path: str | None) -> str:
    """Return the canonical form of an inner path."""
    return SEPARATOR.join(split(path))


def join(directory: str | None, name: str) -> str:
    """Join a directory and a leaf name into one inner path."""
    parts = split(directory)
    leaf = str(name).strip(SEPARATOR)
    if not leaf:
        return SEPARATOR.join(parts)
    return SEPARATOR.join([*parts, leaf])


def parent(path: str | None) -> str:
    """Return the directory holding this path, or the root."""
    return SEPARATOR.join(split(path)[:-1])


def leaf(path: str | None) -> str:
    """Return the final component of a path, or an empty string at the root."""
    parts = split(path)
    return parts[-1] if parts else ""


def display(path: str | None) -> str:
    """Render a path the way an Amiga shell prompt would."""
    parts = split(path)
    return ROOT_DISPLAY + SEPARATOR.join(parts)


def is_below(path: str | None, directory: str | None) -> bool:
    """True when ``path`` sits inside ``directory`` at any depth."""
    branch = split(directory)
    return split(path)[: len(branch)] == branch and len(split(path)) > len(branch)


def depth(path: str | None) -> int:
    return len(split(path))


__all__ = [
    "ROOT",
    "ROOT_DISPLAY",
    "ROOT_TOKENS",
    "SEPARATOR",
    "depth",
    "display",
    "is_below",
    "is_root",
    "join",
    "leaf",
    "normalise",
    "parent",
    "split",
]
