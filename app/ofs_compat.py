"""Reading an AmigaDOS volume's catalogue directly, and repairing its loaders.

Two jobs live here, and both exist because mounting a volume is more work than
the question deserves. Menu scanning wants to know what is on 511 disks, and a
compatibility check wants to know what one loader will do. Both are answered by
reading the root block's hash table straight out of the image bytes.

The loader repairs are the compatibility half. Software written for a floppy
refers to ``DF0:`` and relies on the stack a startup script set; installed on a
hard drive, both are wrong. Every repair here is length-preserving, so a file's
extent and a script's line structure are unchanged by it.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass

BLOCK_SIZE = 512
RESERVED_BLOCKS = 2
T_HEADER = 2
ST_ROOT = 1
ST_USERDIR = 2
ST_FILE = -3

#: Offsets measured back from the end of a header block.
OFF_NAME = 80
OFF_SIZE = 188
OFF_PROTECTION = 192
OFF_HASH_CHAIN = 16
OFF_SECONDARY_TYPE = 4

#: Retained under their previous names so callers stay stable.
OFS_CATALOGUE_SIZE = BLOCK_SIZE
OFS_SECTOR_SIZE = BLOCK_SIZE
OFS_MAX_FILES = 0

#: Device names a floppy-era loader refers to, and their hard-drive equivalent.
FLOPPY_DEVICES = ("DF0:", "DF1:", "DF2:", "DF3:")

#: Stack sizes a startup script may set. Anything outside this range is a
#: transcription error rather than a deliberate choice.
MIN_STACK = 1024
MAX_STACK = 262144


@dataclass(frozen=True)
class OFSFile:
    """One catalogue entry, located directly in the image bytes."""

    directory: str
    name: str
    start: int
    length: int
    load: int
    execute: int

    @property
    def path(self) -> str:
        return f"{self.directory}/{self.name}" if self.directory else self.name


def _long(image: bytes, offset: int) -> int:
    if offset + 4 > len(image):
        return 0
    (value,) = struct.unpack_from(">I", image, offset)
    return value


def _signed_long(image: bytes, offset: int) -> int:
    if offset + 4 > len(image):
        return 0
    (value,) = struct.unpack_from(">i", image, offset)
    return value


def _entry_name(image: bytes, block: int) -> str:
    base = block * BLOCK_SIZE + BLOCK_SIZE - OFF_NAME
    if base + 1 > len(image):
        return ""
    length = min(image[base], 30)
    return image[base + 1 : base + 1 + length].decode("latin-1", "replace")


def _root_block(image: bytes) -> int | None:
    total = len(image) // BLOCK_SIZE
    if total <= RESERVED_BLOCKS:
        return None
    candidate = root_block_number(total)
    for block in (candidate, candidate - 1, candidate + 1):
        if not RESERVED_BLOCKS <= block < total:
            continue
        base = block * BLOCK_SIZE
        if (
            _long(image, base) == T_HEADER
            and _signed_long(image, base + BLOCK_SIZE - OFF_SECONDARY_TYPE) == ST_ROOT
        ):
            return block
    return None


#: Blocks in a double-density AmigaDOS floppy, and the position of its root.
DOUBLE_DENSITY_BLOCKS = 1760
DOUBLE_DENSITY_BYTES = DOUBLE_DENSITY_BLOCKS * BLOCK_SIZE


def root_block_number(total_blocks: int) -> int:
    """Where AmigaDOS keeps a volume's root block.

    Half way through the volume, counted over every block including the two
    reserved boot blocks: 880 on a double-density floppy, 1760 on a
    high-density one, and the midpoint of a partition on a hard drive. Every
    reader and writer in the workbench uses this one definition so a volume it
    creates is the volume a real Amiga expects to find.
    """
    return total_blocks // 2


def block_is_root(block: bytes) -> bool:
    """Whether these 512 bytes are an AmigaDOS root block."""
    if len(block) != BLOCK_SIZE:
        return False
    return (
        _long(block, 0) == T_HEADER
        and _signed_long(block, BLOCK_SIZE - OFF_SECONDARY_TYPE) == ST_ROOT
    )


def is_two_volume_dump(read_block, total_blocks: int) -> bool:
    """Whether a file holds two double-density volumes rather than one.

    A two-disk set is sometimes preserved as a single file, and a file of that
    length is otherwise indistinguishable from one high-density floppy. The
    two are told apart by where the root block sits: a high-density volume
    keeps its root half way through the whole file, while a pair of
    double-density volumes each keep one half way through their own half.

    ``read_block`` takes a block number and returns its bytes, so the decision
    costs three block reads rather than loading the image.
    """
    if total_blocks != DOUBLE_DENSITY_BLOCKS * 2:
        return False
    if block_is_root(read_block(total_blocks // 2)):
        return False
    return block_is_root(read_block(DOUBLE_DENSITY_BLOCKS // 2)) and block_is_root(
        read_block(DOUBLE_DENSITY_BLOCKS + DOUBLE_DENSITY_BLOCKS // 2)
    )


def ofs_catalogue_files(image: bytes) -> list[OFSFile]:
    """List every file on an AmigaDOS volume, without mounting it.

    Only the structures the answer needs are read: the root block, each
    directory's hash table, and each file header. A damaged chain stops that
    branch rather than the whole scan, so a partly corrupted disk still reports
    the files it can still reach.
    """
    root = _root_block(image)
    if root is None:
        return []
    total = len(image) // BLOCK_SIZE
    table_size = _long(image, root * BLOCK_SIZE + 12) or (BLOCK_SIZE // 4 - 56)
    if not 8 <= table_size <= BLOCK_SIZE // 4:
        return []

    files: list[OFSFile] = []
    visited: set[int] = set()
    ffs = image[3:4] not in (b"\x00", b"\x02", b"\x04")
    capacity = BLOCK_SIZE if ffs else BLOCK_SIZE - 24

    def walk(directory_block: int, prefix: str, depth: int) -> None:
        if depth > 12:
            return
        base = directory_block * BLOCK_SIZE
        for index in range(table_size):
            block = _long(image, base + 24 + index * 4)
            while block:
                if block in visited or not RESERVED_BLOCKS <= block < total:
                    break
                visited.add(block)
                header = block * BLOCK_SIZE
                secondary = _signed_long(image, header + BLOCK_SIZE - OFF_SECONDARY_TYPE)
                name = _entry_name(image, block)
                if secondary == ST_USERDIR:
                    walk(block, f"{prefix}/{name}" if prefix else name, depth + 1)
                elif secondary == ST_FILE and name:
                    size = _long(image, header + BLOCK_SIZE - OFF_SIZE)
                    protection = _long(image, header + BLOCK_SIZE - OFF_PROTECTION)
                    first = _long(image, header + 16)
                    files.append(
                        OFSFile(
                            directory=prefix,
                            name=name,
                            start=first * BLOCK_SIZE + (0 if ffs else 24),
                            length=min(size, max(0, len(image) - first * BLOCK_SIZE)),
                            load=protection,
                            execute=capacity,
                        )
                    )
                block = _long(image, header + BLOCK_SIZE - OFF_HASH_CHAIN)

    walk(root, "", 0)
    files.sort(key=lambda item: item.path.casefold())
    return files


def read_ofs_file(image: bytes, entry: OFSFile) -> bytes:
    """Return one catalogue entry's contents, following its block chain.

    An OFS file's data blocks each carry a 24-byte header naming the next
    block, so the chain can be followed without the file header's pointer
    table. An FFS file's blocks are raw, so the header table is used instead.
    """
    if entry.execute == BLOCK_SIZE:
        # FFS: read the block list from the file header.
        return _read_ffs_file(image, entry)
    chunks: list[bytes] = []
    block_offset = entry.start - 24
    remaining = entry.length
    seen: set[int] = set()
    while remaining > 0 and 0 <= block_offset + BLOCK_SIZE <= len(image):
        if block_offset in seen:
            break
        seen.add(block_offset)
        used = _long(image, block_offset + 12)
        payload = image[block_offset + 24 : block_offset + 24 + min(used, BLOCK_SIZE - 24)]
        chunks.append(payload[:remaining])
        remaining -= len(chunks[-1])
        next_block = _long(image, block_offset + 16)
        if not next_block:
            break
        block_offset = next_block * BLOCK_SIZE
    return b"".join(chunks)


def _read_ffs_file(image: bytes, entry: OFSFile) -> bytes:
    start = entry.start
    return image[start : start + entry.length]


def _looks_like_amiga_script(data: bytes) -> bool:
    """Accept a plain-text AmigaDOS script, including one without a final newline."""
    if not data or b"\0" in data[:512]:
        return False
    printable = sum(1 for byte in data[:512] if 9 <= byte <= 13 or 32 <= byte <= 126)
    return printable / max(1, len(data[:512])) > 0.9


def _resolve_ofs_reference(
    files: list[OFSFile], reference: str, current_directory: str
) -> OFSFile | None:
    """Resolve a path a script names, relative to the script's own drawer."""
    value = str(reference or "").strip().strip('"')
    for device in FLOPPY_DEVICES + ("DH0:", "DH1:", "SYS:", "C:", "S:", "L:", "LIBS:", "DEVS:"):
        if value.upper().startswith(device):
            value = value[len(device) :]
            current_directory = ""
            break
    value = value.lstrip("/")
    if "/" in value:
        directory, _, leaf = value.rpartition("/")
    else:
        directory, leaf = current_directory, value
    return next(
        (
            item
            for item in files
            if item.directory.casefold() == directory.casefold()
            and item.name.casefold() == leaf.casefold()
        ),
        None,
    )


_STACK_SETTING = re.compile(r"(?im)^\s*STACK\s+(\d+)\s*$")
_EXECUTE_TARGET = re.compile(r"(?im)^\s*(?:EXECUTE|RUN|C:RUN)\s+(\"[^\"]+\"|\S+)")


def infer_ofs_launch_page(
    image: bytes,
    filename: str,
    action: str,
) -> tuple[str | None, str]:
    """Infer the stack size a launch path actually needs.

    A WHDLoad slave or a game loader that ran from a floppy usually relies on
    the ``STACK`` its startup script set. Installed on a hard drive under a
    different startup, it gets whatever the shell's default is, and fails in a
    way that looks like a corrupt disk. Reading the value out of the script it
    really runs is the only way to state it rather than guess.
    """
    files = ofs_catalogue_files(image)
    requested = str(filename or "").strip()
    launch = _resolve_ofs_reference(files, requested, "")
    if launch is None:
        return None, f"launch file {requested or '(blank)'} is absent"
    data = read_ofs_file(image, launch)
    if not _looks_like_amiga_script(data):
        return None, f"{launch.path} is not a readable AmigaDOS script"
    text = data.decode("latin-1", "replace")

    explicit = _STACK_SETTING.search(text)
    if explicit:
        value = int(explicit.group(1))
        if MIN_STACK <= value <= MAX_STACK:
            return str(value), f"{launch.path} sets STACK {value}"
        return None, f"{launch.path} sets an out-of-range STACK of {value}"

    chained = _EXECUTE_TARGET.search(text)
    if chained:
        target = _resolve_ofs_reference(files, chained.group(1), launch.directory)
        if target is not None:
            nested = read_ofs_file(image, target)
            if _looks_like_amiga_script(nested):
                inner = _STACK_SETTING.search(nested.decode("latin-1", "replace"))
                if inner:
                    value = int(inner.group(1))
                    if MIN_STACK <= value <= MAX_STACK:
                        return str(value), f"{launch.path} runs {target.path}, which sets STACK {value}"
            else:
                return None, f"{launch.path} runs the executable {target.path}; no script stack applies"
    if str(action or "").upper() == "E":
        return None, f"{launch.path} does not set a stack size of its own"
    return None, f"{launch.path} does not expose a provable stack size"


def repair_ofs_basic_wildcards(image: bytes) -> tuple[bytes, list[str]]:
    """Rewrite floppy device references in scripts so a hard-drive install works.

    ``DF0:Game`` is correct on a floppy and wrong the moment the software is
    copied to a drive, because ``DF0:`` is then either empty or a different
    disk. The reference is replaced with a path relative to the script's own
    drawer, padded with spaces so the file's length and every following offset
    are unchanged.

    A change is made only when exactly one file in the volume matches the name
    the reference uses. Anything ambiguous is left alone and reported, because
    a wrong repair is worse than none.
    """
    files = ofs_catalogue_files(image)
    if not files:
        return image, []
    by_name: dict[str, list[OFSFile]] = {}
    for item in files:
        by_name.setdefault(item.name.casefold(), []).append(item)

    repaired = bytearray(image)
    changes: list[str] = []
    for source in files:
        original = read_ofs_file(image, source)
        if not _looks_like_amiga_script(original):
            continue
        if source.execute != BLOCK_SIZE:
            # Only FFS files are stored contiguously, so only they can be
            # patched in place without rebuilding the block chain.
            continue
        patched = bytearray(original)
        text = original.decode("latin-1", "replace")
        for match in re.finditer(r"(?i)\b(DF[0-3]:)([A-Za-z0-9_.\-]*)", text):
            device, name = match.group(1), match.group(2)
            if not name:
                continue
            candidates = by_name.get(name.casefold(), [])
            if len(candidates) != 1:
                changes.append(
                    f"{source.path}: left {device}{name} unchanged because "
                    f"{len(candidates)} files match that name"
                )
                continue
            replacement = candidates[0].path.encode("latin-1", "replace")
            span = match.end() - match.start()
            if len(replacement) > span:
                changes.append(
                    f"{source.path}: {device}{name} could not be replaced by "
                    f"{candidates[0].path} without changing the file's length"
                )
                continue
            patched[match.start() : match.end()] = replacement.ljust(span, b" ")
            changes.append(f"{source.path}: {device}{name} → {candidates[0].path}")
        if patched != bytearray(original):
            repaired[source.start : source.start + len(patched)] = patched
    return bytes(repaired), changes


__all__ = [
    "BLOCK_SIZE",
    "DOUBLE_DENSITY_BLOCKS",
    "DOUBLE_DENSITY_BYTES",
    "FLOPPY_DEVICES",
    "MAX_STACK",
    "MIN_STACK",
    "OFSFile",
    "OFS_CATALOGUE_SIZE",
    "OFS_SECTOR_SIZE",
    "block_is_root",
    "infer_ofs_launch_page",
    "is_two_volume_dump",
    "root_block_number",
    "ofs_catalogue_files",
    "read_ofs_file",
    "repair_ofs_basic_wildcards",
]
