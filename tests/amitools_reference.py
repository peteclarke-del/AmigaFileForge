"""Optional cross-checks against amitools, an independent AmigaDOS implementation.

amitools is not a dependency of this project. When ``AMITOOLS_PATH`` names a
checkout of it (the folder holding the ``amitools`` package), the long-name
and directory-cache tests use it to read what amiganut wrote and to write
volumes for amiganut to read. When it is not set those tests are skipped and
the rest still run.

amitools 0.8.1 has known faults in the areas under test, and the helpers
here allow for them rather than hide them:

* Writing a header in long-name mode stores the entry's name where the
  inline comment belongs, and changing a comment there fails because it asks
  for the length of a name object that has none. ``long_name_writer_fixed``
  replaces those two methods for the duration of a test with versions that
  do what the originals intended.
* Its directory-cache records leave the secondary-type byte at zero, so a
  cache it wrote disagrees with the headers on that one field.
"""

from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path

def _location() -> Path | None:
    configured = os.environ.get("AMITOOLS_PATH")
    if not configured:
        return None
    candidate = Path(configured)
    if (candidate / "amitools" / "fs" / "ADFSVolume.py").is_file():
        return candidate
    return None


LOCATION = _location()
AVAILABLE = LOCATION is not None

if AVAILABLE and str(LOCATION) not in sys.path:
    sys.path.insert(0, str(LOCATION))


def open_volume(path: Path, *, writable: bool = False):
    from amitools.fs.ADFSVolume import ADFSVolume
    from amitools.fs.blkdev.BlkDevFactory import BlkDevFactory

    blkdev = BlkDevFactory().open(str(path), read_only=not writable)
    volume = ADFSVolume(blkdev)
    volume.open()
    return volume, blkdev


def create_volume(path: Path, dos_type: int, label: str = "Amitools"):
    """Create a double-density ADF with amitools and return the open volume."""
    from amitools.fs.ADFSVolume import ADFSVolume
    from amitools.fs.blkdev.BlkDevFactory import BlkDevFactory
    from amitools.fs.FSString import FSString

    blkdev = BlkDevFactory().create(str(path))
    volume = ADFSVolume(blkdev)
    volume.create(FSString(label), dos_type=dos_type)
    return volume, blkdev


def fs_string(text: str):
    from amitools.fs.FSString import FSString

    return FSString(text)


def tree(volume) -> dict[str, tuple]:
    """Return every entry as ``path: (is_dir, protection, comment, data)``."""
    result: dict[str, tuple] = {}

    def walk(directory, prefix: str) -> None:
        for node in directory.get_entries():
            name = node.get_file_name().get_name().get_unicode()
            path = f"{prefix}{name}"
            meta = node.get_meta_info()
            comment = meta.get_comment()
            comment_text = comment.get_unicode() if comment else ""
            if node.is_dir():
                result[path] = (True, meta.get_protect(), comment_text, None)
                walk(node, f"{path}/")
            else:
                result[path] = (False, meta.get_protect(), comment_text, bytes(node.get_file_data()))

    walk(volume.get_root_dir(), "")
    return result


def cache_records(volume) -> dict[str, list]:
    """Return each directory's cache records as amitools decodes them."""
    result: dict[str, list] = {}

    def walk(directory, path: str) -> None:
        directory.ensure_entries()
        records = []
        for block in directory.dcache_blks or []:
            records.extend(block.records)
        result[path] = records
        for node in directory.get_entries():
            if node.is_dir():
                name = node.get_file_name().get_name().get_unicode()
                walk(node, f"{path}/{name}" if path else name)

    walk(volume.get_root_dir(), "")
    return result


def structure_errors(blkdev) -> list[str]:
    """Run the amitools validator over the directory tree and the files.

    Its bitmap pass is left out on purpose: it does not know about
    directory-cache or comment blocks, so it reports every one of them as a
    block marked in use that nothing owns.
    """
    from amitools.fs.validate.Log import Log
    from amitools.fs.validate.Validator import Validator

    validator = Validator(blkdev, Log.WARN)
    validator.scan_boot()
    validator.scan_root()
    validator.scan_dir_tree()
    validator.scan_files()
    return [str(entry) for entry in validator.log.entries]


@contextlib.contextmanager
def long_name_writer_fixed():
    """Make amitools store the comment, not the name, beside a long name."""
    from amitools.fs.block.EntryBlock import EntryBlock

    original = EntryBlock._write_nac_modts

    def write_nac_modts(self):
        if not self.is_longname:
            return original(self)
        area = bytearray()
        name = self.name.get_ami_str()
        area.append(len(name))
        area += name
        if self.comment_block_id:
            area.append(0)
        else:
            comment = self.comment.get_ami_str()
            area.append(len(comment))
            area += comment
        self._put_bytes(-46, bytes(area))
        self._put_long(-18, self.comment_block_id)
        self._put_timestamp(-15, self.mod_ts)

    def needs_extra_comment_block(name, comment):
        text = name.get_name() if hasattr(name, "get_name") else name
        return len(text.get_ami_str()) + len(comment.get_ami_str()) > 110

    original_needs = EntryBlock.__dict__["needs_extra_comment_block"]
    EntryBlock._write_nac_modts = write_nac_modts
    EntryBlock.needs_extra_comment_block = staticmethod(needs_extra_comment_block)
    try:
        yield
    finally:
        EntryBlock._write_nac_modts = original
        EntryBlock.needs_extra_comment_block = original_needs
