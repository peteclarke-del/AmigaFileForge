"""The system a drive is going to hold, worked out before any of it is written.

AmigaOS 3.5 and 3.9 are installed in layers. The disc carries a base system and
an overlay that turns it into the release, and the update packs published
afterwards go over both. Every layer replaces files the one before it laid
down, so the order is the whole of what makes the result correct.

Writing each layer to the volume in turn would write most of the system two or
three times, and would leave a drive holding half of two releases if it were
stopped part way. So the layers are resolved here first, by name and without
reading a byte of any file: each path maps to the last layer that supplied it,
and the volume is then written once with the files that won.

Names are compared the way AmigaDOS compares them, without regard to case. The
disc spells a drawer ``c`` in one tree and ``C`` in another, and they are the
same drawer, so the first spelling seen for a drawer is the one kept.
"""

from __future__ import annotations

import dataclasses
from typing import Callable, Iterator


@dataclasses.dataclass(frozen=True)
class PlannedFile:
    """One file the finished system holds, and where its bytes come from."""

    path: str
    read: Callable[[], bytes]
    length: int
    #: What supplied it, in words: "Workbench 3.9", "BoingBag 2".
    origin: str
    protection: int | None = None
    comment: str = ""


def split_path(path: str) -> list[str]:
    """The parts of a path, whichever separator the source happened to use."""
    return [
        part for part in str(path or "").replace("\\", "/").replace(":", "/").split("/")
        if part and part != "."
    ]


def join_path(*parts: str) -> str:
    """Join paths relative to the root of the drive, skipping empty ones."""
    collected: list[str] = []
    for part in parts:
        collected.extend(split_path(part))
    return "/".join(collected)


class SystemTree:
    """Files and drawers keyed by their AmigaDOS name, later additions winning."""

    def __init__(self) -> None:
        self._files: dict[str, PlannedFile] = {}
        self._drawers: dict[str, str] = {}
        #: Additions refused because a file and a drawer wanted the same name.
        self.warnings: list[str] = []

    def __len__(self) -> int:
        return len(self._files)

    def __contains__(self, path: str) -> bool:
        return join_path(path).casefold() in self._files

    @property
    def total_bytes(self) -> int:
        return sum(item.length for item in self._files.values())

    def _drawer(self, parts: list[str]) -> str:
        """Record a drawer and every drawer above it, and return its spelling."""
        spelled: list[str] = []
        for part in parts:
            key = "/".join([*spelled, part]).casefold()
            known = self._drawers.get(key)
            if known is None:
                known = "/".join([*spelled, part])
                self._drawers[key] = known
            spelled = known.split("/")
        return "/".join(spelled)

    def add_drawer(self, path: str) -> bool:
        parts = split_path(path)
        if not parts:
            return False
        if "/".join(parts).casefold() in self._files:
            self.warnings.append(
                f"{'/'.join(parts)} is a file in one layer and a drawer in another. "
                "The file was kept."
            )
            return False
        self._drawer(parts)
        return True

    def add(
        self,
        path: str,
        read: Callable[[], bytes],
        length: int,
        origin: str,
        *,
        protection: int | None = None,
        comment: str = "",
    ) -> bool:
        """Add a file, replacing one of the same name from an earlier layer."""
        parts = split_path(path)
        if not parts:
            return False
        key = "/".join(parts).casefold()
        if key in self._drawers:
            self.warnings.append(
                f"{'/'.join(parts)} is a drawer in one layer and a file in another. "
                "The drawer was kept."
            )
            return False
        parent = self._drawer(parts[:-1]) if len(parts) > 1 else ""
        # Writing over a file on an Amiga keeps the name it already had, so a
        # later layer that spells ``LoadWB`` as ``loadwb`` replaces the bytes
        # and not the spelling.
        replaced = self._files.get(key)
        leaf = split_path(replaced.path)[-1] if replaced is not None else parts[-1]
        self._files[key] = PlannedFile(
            path=join_path(parent, leaf),
            read=read,
            length=int(length),
            origin=origin,
            protection=protection,
            comment=comment,
        )
        return True

    def get(self, path: str) -> PlannedFile | None:
        return self._files.get(join_path(path).casefold())

    def remove(self, path: str) -> PlannedFile | None:
        return self._files.pop(join_path(path).casefold(), None)

    def files(self) -> list[PlannedFile]:
        """Every file, drawer by drawer, in the order a person would list them."""
        return sorted(
            self._files.values(),
            key=lambda item: (item.path.casefold().count("/"), item.path.casefold()),
        )

    def drawers(self) -> list[str]:
        """Every drawer, parents before the drawers inside them."""
        return sorted(
            self._drawers.values(),
            key=lambda item: (item.count("/"), item.casefold()),
        )

    def empty_drawers(self) -> list[str]:
        """Drawers no file lands in, which have to be made on their own."""
        occupied: set[str] = set()
        for key in self._files:
            parts = key.split("/")[:-1]
            for depth in range(1, len(parts) + 1):
                occupied.add("/".join(parts[:depth]))
        return [
            drawer for drawer in self.drawers() if drawer.casefold() not in occupied
        ]

    def origins(self) -> dict[str, int]:
        """How many files each layer ended up supplying, after replacement."""
        counted: dict[str, int] = {}
        for item in self._files.values():
            counted[item.origin] = counted.get(item.origin, 0) + 1
        return counted

    def __iter__(self) -> Iterator[PlannedFile]:
        return iter(self.files())


__all__ = ["PlannedFile", "SystemTree", "join_path", "split_path"]
