"""The progress callback every long-running service operation accepts.

Anything here that can take a while reports as it goes, so the interface can
show what is happening and the operator can tell a slow job from a stuck one.
The contract is one callable taking a message and an optional position within
an optional total, which the operation registry turns into something a browser
can poll.

Reporting is always optional. Every caller that does not want it passed the
same do-nothing lambda, written out by hand in eighteen places, which is one
definition of the contract per file and eighteen chances for them to disagree
about it. ``none`` is that lambda, named once.
"""

from __future__ import annotations

from typing import Callable

#: A progress callback: a message, and where the work has reached, if known.
Progress = Callable[[str, "int | None", "int | None"], None]


def none(_message: str, _current: int | None = None, _total: int | None = None) -> None:
    """Accept a progress report and do nothing with it."""


def reporter(progress: Progress | None) -> Progress:
    """The callback to report through, whether or not the caller supplied one."""
    return progress or none


__all__ = ["Progress", "none", "reporter"]
