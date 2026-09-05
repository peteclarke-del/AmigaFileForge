from __future__ import annotations

import re

from flask import request

from ..amiga_metadata import parse_protection

from ..errors import DiskError


def payload() -> dict:
    return request.get_json(force=True, silent=False)


def optional_int(value) -> int | None:
    if value in (None, "", "null"):
        return None
    return int(value)


def apply_partition(service, session, value) -> None:
    """Point a hard-drive session at the partition this request names.

    A partition selection is session state rather than a parameter threaded
    through every service call, because a partition is simply which volume the
    pane has open. A request that says nothing about partitions leaves the
    current selection alone, so an operation on a floppy never has to mention
    one.
    """
    if session.kind != "hdf" or value in (None, ""):
        return
    if value == "null":
        service.select_partition(session, None)
        return
    service.select_partition(session, int(value))


def protection_field(value) -> str | None:
    """Normalise a protection value a person supplied, or None when absent.

    A person may type either form: the eight letters ``List`` prints, such as
    ``----rwed``, or the raw long as hexadecimal. Both are accepted here so
    every route reads one written value as one number. An empty field means
    "leave it alone" and is returned as ``None`` rather than as zero, because
    zero is itself meaningful -- it is the ordinary fully permitted file.
    """
    text = str(value or "").strip()
    if not text:
        return None
    if parse_protection(text) is not None:
        return text
    if re.fullmatch(r"(?:&|0x)?[0-9a-fA-F]{1,8}", text):
        return text
    raise DiskError(
        f"“{text}” is not a valid protection value. Use the eight letters "
        "List prints, such as ----rwed, or one to eight hexadecimal digits."
    )
