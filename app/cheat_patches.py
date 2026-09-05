"""Exact-hash, rollback-oriented cheat patch records.

The module does not claim that user observations prove gameplay semantics. It
does ensure a reviewed patch can only touch the bytes and source revision that
were actually examined.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from .checksum import sha256_bytes


CHEAT_PATCH_FORMAT = "amiga-file-forge-cheat-patch"
CHEAT_PATCH_VERSION = 1
_HEX = re.compile(r"^(?:[0-9A-Fa-f]{2})(?:[\s,]*[0-9A-Fa-f]{2})*$")


class CheatPatchError(ValueError):
    pass


def _bytes(value: object, label: str) -> bytes:
    text = str(value or "").strip()
    if not text or not _HEX.fullmatch(text):
        raise CheatPatchError(f"{label} must contain complete hexadecimal bytes.")
    data = bytes.fromhex(re.sub(r"[\s,]", "", text))
    if not 1 <= len(data) <= 32:
        raise CheatPatchError(f"{label} must contain between 1 and 32 bytes.")
    return data


def build_guarded_cheat_patch(original: bytes, document: dict) -> dict:
    source_hash = sha256_bytes(original)
    if str(document.get("sourceSha256") or "") != source_hash:
        raise CheatPatchError("The file changed after cheat analysis. Reopen it before preparing a patch.")
    try:
        offset = int(document.get("offset"))
    except (TypeError, ValueError) as exc:
        raise CheatPatchError("Choose a candidate with an exact file offset.") from exc
    expected = _bytes(document.get("originalHex"), "Original bytes")
    replacement = _bytes(document.get("replacementHex"), "Replacement bytes")
    if len(expected) != len(replacement):
        raise CheatPatchError("A guarded cheat patch must preserve the selected byte length.")
    if offset < 0 or offset + len(expected) > len(original):
        raise CheatPatchError("The selected patch bytes fall outside the analysed file.")
    if original[offset:offset + len(expected)] != expected:
        raise CheatPatchError("The supplied original bytes do not match the analysed file.")
    if expected == replacement:
        raise CheatPatchError("The replacement bytes are identical to the original bytes.")
    rationale = str(document.get("rationale") or "").strip()
    author = str(document.get("author") or "").strip()
    if len(rationale) < 12 or len(rationale) > 2000:
        raise CheatPatchError("Describe the observed gameplay effect in 12 to 2,000 characters.")
    if not author or len(author) > 120:
        raise CheatPatchError("Record a patch author of at most 120 characters.")
    observations = []
    for row in document.get("observations") or []:
        if not isinstance(row, dict):
            continue
        event = str(row.get("event") or "").strip()[:160]
        before = str(row.get("before") or "").strip()[:80]
        after = str(row.get("after") or "").strip()[:80]
        if event and before and after and before != after:
            observations.append({
                "event": event,
                "before": before,
                "after": after,
                "emulator": str(row.get("emulator") or "").strip()[:160],
            })
    if len(observations) < 2 or len({row["event"].casefold() for row in observations}) < 2:
        raise CheatPatchError(
            "Record at least two distinct emulator gameplay events with changing watched values."
        )
    watch_address = str(document.get("watchAddress") or "").strip()[:40]
    if not watch_address:
        raise CheatPatchError("Record the emulator watchpoint address used for the observations.")
    profile = dict(document.get("hardwareProfile") or {})
    record = {
        "format": CHEAT_PATCH_FORMAT,
        "version": CHEAT_PATCH_VERSION,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "title": str(document.get("title") or "Untitled patch").strip()[:160],
        "path": str(document.get("path") or "")[:1024],
        "sourceSha256": source_hash,
        "sourceSize": len(original),
        "offset": offset,
        "originalHex": expected.hex().upper(),
        "replacementHex": replacement.hex().upper(),
        "watchAddress": watch_address,
        "observations": observations[:20],
        "hardwareProfile": profile,
        "rationale": rationale,
        "author": author,
        "rollback": (
            f"Restore {expected.hex().upper()} at file offset &{offset:X}, or use the automatic checkpoint created before Apply."
        ),
    }
    identity_source = json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    record["id"] = sha256_bytes(identity_source)
    return record


def apply_guarded_cheat_patch(original: bytes, patch: dict) -> bytes:
    if patch.get("format") != CHEAT_PATCH_FORMAT or patch.get("version") != CHEAT_PATCH_VERSION:
        raise CheatPatchError("That is not a supported Amiga File Forge cheat patch.")
    if sha256_bytes(original) != str(patch.get("sourceSha256") or ""):
        raise CheatPatchError("This patch belongs to a different exact file revision.")
    try:
        offset = int(patch.get("offset"))
    except (TypeError, ValueError) as exc:
        raise CheatPatchError("The patch offset is invalid.") from exc
    expected = _bytes(patch.get("originalHex"), "Original bytes")
    replacement = _bytes(patch.get("replacementHex"), "Replacement bytes")
    if len(expected) != len(replacement) or original[offset:offset + len(expected)] != expected:
        raise CheatPatchError("The guarded original bytes do not match this file.")
    return original[:offset] + replacement + original[offset + len(expected):]
