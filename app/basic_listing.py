"""Turning a saved AmigaBASIC program back into readable lines.

AmigaBASIC stores a program tokenised, not as text: each keyword is one or two
bytes and only names and literals survive as characters. Reading one therefore
means running the token tables backwards, which is work the ``amiganut.basic``
package owns. This module is the single place the workbench asks it to do that,
so a listing shown in the file inspector, the editor and a report all come from
the same decode.

Anything that is not a saved AmigaBASIC program returns ``None``, rather than a
list of lines assembled out of coincidence.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BasicLine:
    """One numbered line of a saved AmigaBASIC program."""

    number: int
    body: bytes
    text: str


def decode_basic(program: bytes) -> list[BasicLine] | None:
    """Decode a saved AmigaBASIC program into its numbered lines.

    Both released dialects are tried, newest first, because a program saved by
    AmigaBASIC 1.2 uses token values 1.0 does not have. The first dialect that
    yields any lines is the one the program was saved with.
    """
    try:
        from amiganut.basic import (
            AMIGABASIC_10,
            AMIGABASIC_12,
            Verdict,
            detect,
            scan_program,
        )
    except ImportError:  # pragma: no cover - the tokeniser ships with the app
        return None
    detection = detect(program)
    if detection.verdict not in {Verdict.BASIC, Verdict.BASIC_TRAILING}:
        return None
    body = program[: int(detection.program_length or len(program))]
    lines: list[BasicLine] = []
    for dialect in (AMIGABASIC_12, AMIGABASIC_10):
        try:
            scanned = list(scan_program(body, dialect=dialect))
        except Exception:
            continue
        if not scanned:
            continue
        lines = [
            BasicLine(
                number=int(line.line_number),
                body=body[line.start : line.end],
                text="".join(token.text for token in line.tokens),
            )
            for line in scanned
        ]
        break
    return lines or None


__all__ = ["BasicLine", "decode_basic"]
