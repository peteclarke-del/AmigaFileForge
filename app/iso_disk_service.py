"""Browsing a CD image as an ordinary pane, and copying things off it.

A CD is read-only by nature, so this mixin offers listing and reading and
nothing else. That puts it beside the DMS component rather than beside the
filing systems: both are containers an operator opens to take things out of,
and neither can be written back.

The Amiga metadata a disc records is carried through into the same fields an
AmigaDOS volume reports, so a file dragged off a CD arrives with the
protection bits and comment the disc gave it. Getting that right is the whole
reason for reading the Amiga extension at all, and it is what makes copying
from a CD equivalent to copying from a floppy rather than a lossy import.
"""

from __future__ import annotations

from contextlib import contextmanager

from . import amiga_paths
from .amiga_metadata import format_protection
from .errors import DiskError
from .image_session import ImageSession
from .iso9660 import Iso9660Error, Iso9660Image


class IsoDiskMixin:
    """List and read an ISO 9660 disc through the ordinary session API."""

    @contextmanager
    def iso_image(self, session: ImageSession):
        """Open the disc for one operation and close it again.

        A CD image is held open only for as long as it is being read. Keeping
        a handle on the session would mean a pane that has been open all day
        is still holding a file descriptor on a seven-hundred-megabyte file
        nobody has touched since breakfast.
        """
        try:
            image = Iso9660Image(self.resolve(session))
        except Iso9660Error as exc:
            raise DiskError(str(exc)) from exc
        try:
            yield image
        except Iso9660Error as exc:
            raise DiskError(str(exc)) from exc
        finally:
            image.close()

    def iso_listing(self, session: ImageSession, inner: str) -> dict:
        """One drawer of the disc, in the shape every pane expects."""
        directory = "" if inner in {"", "$", ":"} else amiga_paths.normalise(inner)
        with self.iso_image(session) as image:
            entries = image.list_directory(directory)
            volume = image.volume
        rows = [
            {
                "name": entry.name,
                "path": entry.path,
                "type": "dir" if entry.directory else "file",
                # A drawer's extent size is not a count of what is in it, and
                # the pane renders a directory's length as one. An AmigaDOS
                # volume reports zero here, so a disc does too.
                "length": 0 if entry.directory else entry.length,
                # A disc that records no protection reports none, rather than
                # an invented ``----rwed`` that would look like a real value.
                "protection": (
                    format_protection(entry.protection)
                    if entry.protection is not None else ""
                ),
                "comment": entry.comment,
                "filetype": "",
                "datestamp": entry.datestamp,
            }
            for entry in entries
        ]
        files = sum(1 for row in rows if row["type"] == "file")
        drawers = len(rows) - files
        return {
            "entries": rows,
            "title": volume,
            "description": (
                f"CD image · {volume} · {drawers} drawer{'s' if drawers != 1 else ''}, "
                f"{files} file{'s' if files != 1 else ''} here"
            ),
            "path": directory or "$",
            "readOnly": True,
        }

    def iso_file(self, session: ImageSession, inner: str) -> bytes:
        with self.iso_image(session) as image:
            return image.read_file(amiga_paths.normalise(inner))

    def iso_file_metadata(self, session: ImageSession, inner: str) -> dict:
        """The Amiga metadata the disc records for one file."""
        path = amiga_paths.normalise(inner)
        parent = amiga_paths.parent(path)
        leaf = amiga_paths.leaf(path).casefold()
        with self.iso_image(session) as image:
            entry = next(
                (item for item in image.list_directory(parent) if item.name.casefold() == leaf),
                None,
            )
        if entry is None:
            raise DiskError(f"{inner} is not on this disc.")
        return {
            "path": path,
            "protection": (
                format_protection(entry.protection) if entry.protection is not None else ""
            ),
            "comment": entry.comment,
            "length": entry.length,
            "datestamp": entry.datestamp,
        }

    def iso_summary(self, session: ImageSession) -> dict:
        """What the disc calls itself, for the pane heading."""
        try:
            with self.iso_image(session) as image:
                return {"volume": image.volume, "joliet": image.joliet}
        except DiskError:
            return {"volume": session.name, "joliet": False}


__all__ = ["IsoDiskMixin"]
