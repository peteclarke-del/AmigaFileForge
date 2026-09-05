"""Canonical filename rules for every writable Amiga File Forge target."""

from __future__ import annotations

from dataclasses import dataclass
import unicodedata
from typing import Iterable

from .errors import DiskError


# AmigaDOS reserves only three characters in a name: the colon that separates
# a device from a path, and both slashes. Full stops, spaces, hashes and
# asterisks are all legal and common -- ``Disk.info`` and ``My Drawer`` are
# ordinary names -- so forbidding them here would refuse files a real machine
# creates every day.
_AMIGA_FORBIDDEN = frozenset(":/\\")
_BASE36 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

#: AmigaDOS names hold up to 30 characters; the long-filename variants hold 107.
AMIGA_NAME_LIMIT = 30


def _base36(value: int) -> str:
    digits = []
    while value:
        value, remainder = divmod(value, len(_BASE36))
        digits.append(_BASE36[remainder])
    return "".join(reversed(digits)) or "0"


@dataclass(frozen=True)
class TargetNamePolicy:
    """Validate, normalise and allocate one target filesystem leaf name."""

    kind: str
    label: str
    limit: int
    forbidden: frozenset[str]
    latin1: bool = False

    def public_contract(self) -> dict:
        return {
            "kind": self.kind,
            "label": self.label,
            "limit": self.limit,
            "forbidden": "".join(sorted(self.forbidden)),
            "latin1": self.latin1,
        }

    def validate(self, value: object) -> str:
        original = str(value or "")
        name = original
        if self.kind != "kickfs":
            name = name.strip()
        if not name:
            raise DiskError(f"Enter a {self.label} filename.")
        if name != original:
            raise DiskError(
                f"{self.label} filenames cannot start or end with whitespace."
            )
        if len(name) > self.limit:
            raise DiskError(
                f"{self.label} filenames can contain at most {self.limit} characters."
            )
        if self.latin1:
            try:
                name.encode("latin-1")
            except UnicodeEncodeError as exc:
                raise DiskError(
                    f"{self.label} filenames must use Latin-1 characters."
                ) from exc
        if any(ord(character) < 32 or character in self.forbidden for character in name):
            raise DiskError(
                f"“{name}” contains a character that cannot be used in a "
                f"{self.label} filename."
            )
        return name

    def normalise(self, value: object, fallback: str = "FILE") -> str:
        raw = str(value or "")
        if self.kind != "kickfs":
            raw = raw.strip()
        normalised = unicodedata.normalize("NFKC", raw) if self.latin1 else raw
        output: list[str] = []
        for character in normalised:
            if ord(character) < 32 or character in self.forbidden:
                output.append("_")
                continue
            if self.latin1:
                try:
                    character.encode("latin-1")
                except UnicodeEncodeError:
                    output.append("_")
                    continue
            output.append(character)
        candidate = "".join(output)[: self.limit]
        if candidate:
            return candidate
        safe_fallback = "".join(
            character
            for character in (
                unicodedata.normalize("NFKC", str(fallback or "FILE"))
                if self.latin1
                else str(fallback or "FILE")
            )
            if ord(character) >= 32
            and character not in self.forbidden
            and (not self.latin1 or ord(character) <= 0xFF)
        )
        return (safe_fallback or "FILE")[: self.limit]

    def allocate(self, preferred: object, used: Iterable[object]) -> str:
        base = self.normalise(preferred)
        occupied = {str(value or "").casefold() for value in used}
        candidate = base
        suffix = 1
        decimal_capacity = 10 ** (self.limit - 1)
        while candidate.casefold() in occupied:
            if suffix < decimal_capacity:
                decimal = str(suffix)
                candidate = f"{base[:self.limit - len(decimal)]}{decimal}"
            else:
                encoded = suffix - decimal_capacity
                if encoded >= len(_BASE36) ** self.limit:
                    raise DiskError(
                        f"No unused {self.label} filename can be allocated within "
                        f"the {self.limit}-character limit."
                    )
                candidate = _base36(encoded).rjust(self.limit, "0")
            suffix += 1
        return candidate


def target_name_policy(
    kind: object,
    *,
    item_type: object = "file",
    name_limit: object = None,
) -> TargetNamePolicy:
    """Return the one authoritative leaf-name policy for a target kind."""
    target = str(kind or "").strip().lower()
    row_type = str(item_type or "file").strip().lower()
    if target == "kickfs":
        # A resident module's name lives in the ROM and is read by the tag
        # scan, so it may hold anything printable up to the tag's own limit.
        return TargetNamePolicy("kickfs", "Kickstart module", 60, frozenset(), latin1=True)
    if target == "rom":
        return TargetNamePolicy("rom", "ROM bank", 180, frozenset("/"))
    if target == "hdf" and row_type in {"partition", "disk", "disk image"}:
        # A partition's name is its RDB device name, which is a 31-character
        # BSTR rather than an AmigaDOS filename.
        return TargetNamePolicy("hdf", "partition device name", 31, _AMIGA_FORBIDDEN, latin1=True)
    if target in {"host", "deployment"}:
        return TargetNamePolicy(target, "host", 255, frozenset("/"))
    try:
        limit = max(1, int(name_limit or AMIGA_NAME_LIMIT))
    except (TypeError, ValueError):
        limit = AMIGA_NAME_LIMIT
    label = "OFS" if target == "ofs" else "FFS"
    return TargetNamePolicy(
        target or "ffs", label, limit, _AMIGA_FORBIDDEN, latin1=True
    )


def session_name_policy(session) -> TargetNamePolicy:
    # An open partition is an ordinary AmigaDOS volume, so it takes the
    # volume's name policy rather than the drive's.
    kind = (
        "ffs"
        if session.kind == "hdf" and getattr(session, "partition", None) is not None
        else session.kind
    )
    capabilities = getattr(session, "ffs_capabilities", {}) or {}
    return target_name_policy(kind, name_limit=capabilities.get("nameLimit"))


__all__ = [
    "AMIGA_NAME_LIMIT",
    "TargetNamePolicy",
    "session_name_policy",
    "target_name_policy",
]
