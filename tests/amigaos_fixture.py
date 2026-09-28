"""Release discs and update packs small enough to build for every test.

The real things are a commercial CD and archives nobody can commit, so these
are made to the same shape: the drawers the installer script names, with a
file in each that says where it came from. What matters to an installation is
which layer supplies each path and where it lands, and that is decided by the
layout and not by the size of the files.

The update packs are genuine LhA archives, written with stored members so that
no compressor is needed, and the locked payload is a genuine ZIP whose entries
are marked encrypted in its directory, which is all that is ever read of one.
"""

from __future__ import annotations

import io
import struct
import zipfile
from pathlib import Path

from tests.iso_fixture import amiga_entry, build_iso, directory, file

#: AmigaDOS protection longs. Pure is granted when set. Execute is denied
#: when set, which is how a script is marked.
PURE = 0x20
SCRIPT = 0x40 | 0x02

STARTUP = "C:SetPatch QUIET\nC:LoadWB\nEndCLI >NIL:\n"


def _add(tree, path: str, data: bytes, protection: int | None = None):
    """Add one file at a path, making the drawers above it as needed."""
    parts = path.split("/")
    node = tree
    for part in parts[:-1]:
        found = next(
            (child for child in node.children if child.name == part and child.directory),
            None,
        )
        node = found if found is not None else node.add(directory(part))
    use = amiga_entry(protection=protection) if protection is not None else b""
    node.add(file(parts[-1], data, system_use=use))


def release_39_files() -> dict[str, tuple[bytes, int | None]]:
    """Every file on the test 3.9 disc, by its path on the disc."""
    base = "OS-Version3.9"
    return {
        f"{base}/OS3.9Install": (b"(script)", None),
        f"{base}/Workbench3.5/S/Startup-Sequence": (STARTUP.encode("latin-1"), SCRIPT),
        f"{base}/Workbench3.5/C/LoadWB": (b"\x00\x00\x03\xf3 loadwb 3.5", None),
        f"{base}/Workbench3.5/C/AddBuffers": (b"\x00\x00\x03\xf3 addbuffers", PURE),
        f"{base}/Workbench3.5/C/SetPatch": (b"\x00\x00\x03\xf3 setpatch 3.5", None),
        f"{base}/Workbench3.5/Libs/icon.library": (b"icon 3.5" * 500, None),
        f"{base}/Workbench3.5/Devs.info": (b"icon", 0x02),
        f"{base}/Workbench3.9/C/SetPatch": (b"\x00\x00\x03\xf3 setpatch 3.9", None),
        f"{base}/Workbench3.9/Libs/icon.library": (b"icon 3.9" * 600, None),
        f"{base}/Workbench3.9/Devs/AmigaOS ROM Update": (b"rom update", None),
        f"{base}/Workbench3.9/Storage/Keymaps/gb": (b"keymap gb", None),
        f"{base}/Workbench3.9/Storage/Printers/EpsonQ": (b"printer", None),
        f"{base}/Locale/Catalogs/deutsch/Sys/workbench.catalog": (b"katalog", None),
        f"{base}/c/loadwb": (b"\x00\x00\x03\xf3 loadwb 3.9", None),
        f"{base}/L/FastFileSystem": (b"ffs 45", None),
        f"{base}/Extras/Libs/68040.library": (b"68040", None),
        f"{base}/Extras/Backdrops/Boing.jpg": (b"picture", None),
    }


def release_35_files() -> dict[str, tuple[bytes, int | None]]:
    return {
        "OS-Version3.1/Workbench3.1/S/Startup-Sequence": (STARTUP.encode("latin-1"), SCRIPT),
        "OS-Version3.1/Workbench3.1/C/LoadWB": (b"\x00\x00\x03\xf3 loadwb 3.1", None),
        "OS-Version3.1/Workbench3.1/C/SetPatch": (b"\x00\x00\x03\xf3 setpatch 3.1", None),
        "OS-Version3.1/Extras3.1/Tools/Calculator": (b"calculator", None),
        "OS-Version3.5/OS3.5Install": (b"(script)", None),
        "OS-Version3.5/Workbench/C/SetPatch": (b"\x00\x00\x03\xf3 setpatch 3.5", None),
        "OS-Version3.5/Keymaps/gb": (b"keymap gb", None),
        "OS-Version3.5/Printers/EpsonQ": (b"printer", None),
    }


EMERGENCY = {
    "Emergency-Boot/S/Startup-Sequence": (STARTUP.encode("latin-1"), None),
    "Emergency-Boot/C/LoadWB": (b"\x00\x00\x03\xf3 loadwb", None),
    "Emergency-Boot/C/SetPatch": (b"\x00\x00\x03\xf3 setpatch" * 400, None),
    "Emergency-Boot/Libs/icon.library": (b"icon" * 10_000, None),
    "Emergency-Boot/Devs/Monitors/PAL": (b"monitor", None),
    "Emergency-Boot/Disk.info": (b"icon", None),
}


def release_disc(
    folder: Path,
    *,
    release: str = "3.9",
    emergency: bool = True,
    volume: str | None = None,
    without: tuple[str, ...] = (),
    empty_drawers: tuple[str, ...] = ("OS-Version3.9/Workbench3.5/Expansion",),
    name: str | None = None,
    emergency_startup: str | None = None,
) -> Path:
    """Write a release disc and return where it is.

    ``without`` names drawers to leave off the disc, for a disc that is
    incomplete.
    """
    files = dict(release_39_files() if release == "3.9" else release_35_files())
    if emergency:
        files.update(EMERGENCY)
        if emergency_startup is not None:
            files["Emergency-Boot/S/Startup-Sequence"] = (
                emergency_startup.encode("latin-1"), None,
            )
    tree = directory("")
    tree.add(file("Disk.info", b"icon"))
    for path, (data, protection) in files.items():
        if any(path.casefold().startswith(f"{left.casefold()}/") for left in without):
            continue
        _add(tree, path, data, protection)
    if release == "3.9":
        for drawer in empty_drawers:
            parts = drawer.split("/")
            node = tree
            for part in parts:
                found = next(
                    (child for child in node.children
                     if child.name == part and child.directory),
                    None,
                )
                node = found if found is not None else node.add(directory(part))
    path = Path(folder) / (name or f"AmigaOS{release.replace('.', '')}.iso")
    path.write_bytes(build_iso(tree, volume=volume or f"AmigaOS{release}"))
    return path


# ----------------------------------------------------------------------
# LhA archives
# ----------------------------------------------------------------------

def _crc16(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def lha_archive(members: dict[str, bytes], comments: dict[str, str] | None = None) -> bytes:
    """An LhA archive of stored members, with level 0 headers.

    A comment is written the way LhA on the Amiga writes one, after a zero
    byte in the name field.
    """
    comments = comments or {}
    out = bytearray()
    for path, data in members.items():
        name = path.replace("/", "\\").encode("latin-1")
        if path in comments:
            name += b"\x00" + comments[path].encode("latin-1")
        body = (
            b"-lh0-"
            + struct.pack("<II", len(data), len(data))
            + struct.pack("<I", 0)
            + bytes([0x20, 0])
            + bytes([len(name)]) + name
            + struct.pack("<H", _crc16(data))
        )
        out += bytes([len(body), sum(body) & 0xFF]) + body + data
    out += b"\x00"
    return bytes(out)


def locked_payload(names: list[str]) -> bytes:
    """A ZIP whose every entry is marked encrypted, as AmigaOS-Update is."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as bundle:
        for name in names:
            bundle.writestr(name, b"ciphertext " + name.encode("latin-1"))
    data = bytearray(buffer.getvalue())
    position = 0
    while True:
        position = data.find(b"PK\x03\x04", position)
        if position < 0:
            break
        data[position + 6] |= 0x01
        position += 4
    position = 0
    while True:
        position = data.find(b"PK\x01\x02", position)
        if position < 0:
            break
        data[position + 8] |= 0x01
        position += 4
    return bytes(data)


#: What the test BoingBag 1 fixes, inside its locked payload.
LOCKED_1 = ["C/SetPatch", "C/IPrefs", "Libs/icon.library"]
LOCKED_2 = ["C/SetPatch", "Tools/HDToolBox"]


def boingbag_1() -> bytes:
    root = "BoingBag3.9-1"
    return lha_archive({
        f"{root}.info": b"icon",
        f"{root}/AmigaOS-Update": locked_payload(LOCKED_1),
        f"{root}/C/Updater": b"\x00\x00\x03\xf3 updater 1",
        f"{root}/Install": b"(script)",
        f"{root}/Locale/Catalogs/deutsch/Sys/workbench.catalog": b"katalog BB1",
        f"{root}/Contribution/Readme": b"contributions",
        f"{root}/Internet/AWeb/cache/paper.gif": b"GIF89a",
    }, comments={f"{root}/Internet/AWeb/cache/paper.gif": "http://example.org/paper.gif"})


def boingbag_2() -> bytes:
    root = "BoingBag3.9-2"
    return lha_archive({
        f"{root}/AmigaOS-Update": locked_payload(LOCKED_2),
        f"{root}/XAD-Update": locked_payload(["Libs/xadmaster.library"]),
        f"{root}/C/Updater": b"\x00\x00\x03\xf3 updater 2",
    })


def boingbag_34(*, nested: str = "") -> dict[str, bytes]:
    root = f"{nested}BoingBag3.9-3&4"
    return {
        f"{root}/Files1/C/SetPatch": b"\x00\x00\x03\xf3 setpatch BB4",
        f"{root}/Files1/Libs/dos.library": b"dos 42.1",
        f"{root}/Files2/Devs/Keymaps/pl": b"keymap pl",
        f"{root}/Files2/Libs/xadmaster_020.library": b"xad 020",
        f"{root}/Files2/Libs/xadmaster_060.library": b"xad 060",
        f"{root}/Files2/Libs/mpega020FPU.library": b"mpega 020",
        f"{root}/Files2/Libs/mpega040.library": b"mpega 040",
        f"{root}/Files2/Libs/mpega040FPU.library": b"mpega 040 fpu",
        f"{root}/Files2/Libs/mpega060FPU.library": b"mpega 060 fpu",
        f"{root}/Files2/Devs/scsi_A600_A1200.device": b"scsi",
    }


def collection() -> bytes:
    """One archive holding all three packs side by side, as BB1-4.lha does."""
    members: dict[str, bytes] = {}
    members["BoingBag3.9-1/AmigaOS-Update"] = locked_payload(LOCKED_1)
    members["BoingBag3.9-1/C/Updater"] = b"\x00\x00\x03\xf3 updater 1"
    members["BoingBag3.9-2/AmigaOS-Update"] = locked_payload(LOCKED_2)
    members["BoingBag3.9-2/C/Updater"] = b"\x00\x00\x03\xf3 updater 2"
    members.update(boingbag_34())
    return lha_archive(members)


def boingbag_35_1() -> bytes:
    return lha_archive({
        "BoingBag_1/Workbench/C/SetPatch": b"\x00\x00\x03\xf3 setpatch BB 3.5",
        "BoingBag_1/Printers/EpsonQ": b"printer BB",
        "BoingBag_1/ROM-Update/AmigaOS ROM Update": b"rom update",
    })
