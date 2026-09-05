"""Higher-level ROM maintenance tools built on the conservative ROM decoder.

The functions in this module never execute bytes from an uploaded image.  They
return bounded, serialisable reports and require source fingerprints before a
patch can alter an image.
"""

from __future__ import annotations

import base64
import json
import io
import zipfile
import zlib
import re
from pathlib import Path

from .checksum import sha256_bytes
from .rom import (
    DEFAULT_ROM_BASE,
    inspect_bank,
    make_expansion_rom,
    parse_extended_rom_header,
    parse_rom_header,
    rom_base,
)

try:
    from capstone import (
        Cs,
        CS_ARCH_M68K,
        CS_MODE_BIG_ENDIAN,
        CS_MODE_M68K_000,
        CS_MODE_M68K_010,
        CS_MODE_M68K_020,
        CS_MODE_M68K_030,
        CS_MODE_M68K_040,
        CS_MODE_M68K_060,
    )
except ImportError:  # Host-side lightweight tests may not install production dependencies.
    Cs = None


PATCH_FORMAT = "amiga-file-forge-rom-patch-1"
PROJECT_FORMAT = "amiga-file-forge-rom-project-1"
MAX_DISASSEMBLY_BYTES = 256 * 1024
MAX_PATCH_BYTES = 16 * 1024 * 1024


class RomWorkbenchError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Amiga system vocabulary
# ---------------------------------------------------------------------------
# An Amiga program does almost nothing through absolute addresses. It puts a
# library base in A6 and calls a negative offset from it, so the interesting
# annotation is not "what address is this" but "which library vector is this".
# These tables turn ``JSR -$0198(A6)`` into ``exec.library OpenLibrary``.

EXEC_LVOS = {
    -30: ("Supervisor", "Run a routine in supervisor mode"),
    -72: ("InitCode", "Initialise resident modules of a given priority"),
    -78: ("InitStruct", "Initialise a structure from an init table"),
    -84: ("MakeLibrary", "Build a library from a vector table"),
    -90: ("MakeFunctions", "Fill in a library's jump table"),
    -96: ("FindResident", "Find a resident tag by name"),
    -102: ("InitResident", "Initialise one resident module"),
    -108: ("Alert", "Display a system alert"),
    -114: ("Debug", "Enter the ROM debugger"),
    -120: ("Disable", "Disable interrupts"),
    -126: ("Enable", "Enable interrupts"),
    -132: ("Forbid", "Forbid task switching"),
    -138: ("Permit", "Permit task switching"),
    -144: ("SetSR", "Read or change the status register"),
    -150: ("SuperState", "Enter supervisor state"),
    -156: ("UserState", "Return to user state"),
    -162: ("SetIntVector", "Install an interrupt server vector"),
    -168: ("AddIntServer", "Add an interrupt server"),
    -174: ("RemIntServer", "Remove an interrupt server"),
    -180: ("Cause", "Cause a software interrupt"),
    -186: ("Allocate", "Allocate from a memory header"),
    -192: ("Deallocate", "Return memory to a header"),
    -198: ("AllocMem", "Allocate memory of a requested type"),
    -204: ("AllocAbs", "Allocate memory at an absolute address"),
    -210: ("FreeMem", "Free previously allocated memory"),
    -216: ("AvailMem", "Report free memory of a given type"),
    -222: ("AllocEntry", "Allocate several memory blocks at once"),
    -228: ("FreeEntry", "Free a memory-list allocation"),
    -234: ("Insert", "Insert a node into a list"),
    -240: ("AddHead", "Add a node to the head of a list"),
    -246: ("AddTail", "Add a node to the tail of a list"),
    -252: ("Remove", "Remove a node from a list"),
    -258: ("RemHead", "Remove the first node of a list"),
    -264: ("RemTail", "Remove the last node of a list"),
    -270: ("Enqueue", "Insert a node by priority"),
    -276: ("FindName", "Find a named node in a list"),
    -282: ("AddTask", "Add a task to the system"),
    -288: ("RemTask", "Remove a task"),
    -294: ("FindTask", "Find a task by name, or the current task"),
    -300: ("SetTaskPri", "Change a task's priority"),
    -306: ("SetSignal", "Read or change a task's signals"),
    -312: ("SetExcept", "Change a task's exception signals"),
    -318: ("Wait", "Wait for one of a set of signals"),
    -324: ("Signal", "Signal a task"),
    -330: ("AllocSignal", "Allocate a signal bit"),
    -336: ("FreeSignal", "Free a signal bit"),
    -342: ("AllocTrap", "Allocate a trap vector"),
    -348: ("FreeTrap", "Free a trap vector"),
    -354: ("AddPort", "Add a public message port"),
    -360: ("RemPort", "Remove a public message port"),
    -366: ("PutMsg", "Send a message to a port"),
    -372: ("GetMsg", "Receive a message from a port"),
    -378: ("ReplyMsg", "Reply to a message"),
    -384: ("WaitPort", "Wait for a message to arrive"),
    -390: ("FindPort", "Find a public message port by name"),
    -396: ("AddLibrary", "Add a library to the system"),
    -402: ("RemLibrary", "Remove a library"),
    -408: ("OldOpenLibrary", "Open a library, 1.0 compatible"),
    -414: ("CloseLibrary", "Close a library"),
    -420: ("SetFunction", "Patch one library vector"),
    -426: ("SumLibrary", "Recalculate a library's checksum"),
    -432: ("AddDevice", "Add a device to the system"),
    -438: ("RemDevice", "Remove a device"),
    -444: ("OpenDevice", "Open a device unit"),
    -450: ("CloseDevice", "Close a device unit"),
    -456: ("DoIO", "Perform a synchronous I/O request"),
    -462: ("SendIO", "Start an asynchronous I/O request"),
    -468: ("CheckIO", "Test whether an I/O request has finished"),
    -474: ("WaitIO", "Wait for an I/O request to finish"),
    -480: ("AbortIO", "Abort an I/O request"),
    -486: ("AddResource", "Add a resource"),
    -492: ("RemResource", "Remove a resource"),
    -498: ("OpenResource", "Open a resource by name"),
    -516: ("RawDoFmt", "Format a string with a per-character callback"),
    -522: ("GetCC", "Read the condition codes portably"),
    -528: ("TypeOfMem", "Report which memory type an address is in"),
    -534: ("Procure", "Take a semaphore"),
    -540: ("Vacate", "Release a semaphore"),
    -552: ("OpenLibrary", "Open a library by name and minimum version"),
    -558: ("InitSemaphore", "Initialise a signal semaphore"),
    -564: ("ObtainSemaphore", "Take a signal semaphore, waiting if needed"),
    -570: ("ReleaseSemaphore", "Release a signal semaphore"),
    -576: ("AttemptSemaphore", "Take a semaphore without waiting"),
    -582: ("ObtainSemaphoreList", "Take a list of semaphores"),
    -594: ("FindSemaphore", "Find a named semaphore"),
    -600: ("AddSemaphore", "Add a public semaphore"),
    -606: ("RemSemaphore", "Remove a public semaphore"),
    -612: ("SumKickData", "Checksum the KickTag data"),
    -618: ("AddMemList", "Add a memory region to the free list"),
    -624: ("CopyMem", "Copy memory"),
    -630: ("CopyMemQuick", "Copy long-aligned memory quickly"),
    -636: ("CacheClearU", "Clear the instruction and data caches"),
    -684: ("CreateIORequest", "Create an I/O request structure"),
    -690: ("DeleteIORequest", "Delete an I/O request structure"),
    -696: ("CreateMsgPort", "Create a message port"),
    -702: ("DeleteMsgPort", "Delete a message port"),
    -732: ("AllocVec", "Allocate memory that remembers its own size"),
    -738: ("FreeVec", "Free an AllocVec allocation"),
}

DOS_LVOS = {
    -30: ("Open", "Open a file"),
    -36: ("Close", "Close a file"),
    -42: ("Read", "Read from a file handle"),
    -48: ("Write", "Write to a file handle"),
    -54: ("Input", "Return the standard input handle"),
    -60: ("Output", "Return the standard output handle"),
    -66: ("Seek", "Move a file's position"),
    -72: ("DeleteFile", "Delete a file"),
    -78: ("Rename", "Rename a file"),
    -84: ("Lock", "Lock a file or drawer"),
    -90: ("UnLock", "Release a lock"),
    -96: ("DupLock", "Duplicate a lock"),
    -102: ("Examine", "Read a lock's FileInfoBlock"),
    -108: ("ExNext", "Read the next directory entry"),
    -114: ("Info", "Report a volume's free space"),
    -120: ("CreateDir", "Create a drawer"),
    -126: ("CurrentDir", "Change the current drawer"),
    -132: ("IoErr", "Return the last error code"),
    -138: ("CreateProc", "Create a process"),
    -144: ("Exit", "Exit the current process"),
    -150: ("LoadSeg", "Load an executable into memory"),
    -156: ("UnLoadSeg", "Unload a loaded segment"),
    -174: ("DeviceProc", "Find a device's handler process"),
    -180: ("SetComment", "Set a file's comment"),
    -186: ("SetProtection", "Set a file's protection bits"),
    -192: ("DateStamp", "Read the system date and time"),
    -198: ("Delay", "Wait for a number of ticks"),
    -204: ("WaitForChar", "Wait for input with a timeout"),
    -210: ("ParentDir", "Return a lock on the parent drawer"),
    -216: ("IsInteractive", "Test whether a handle is a console"),
    -222: ("Execute", "Run a command line"),
}

GRAPHICS_LVOS = {
    -30: ("BltBitMap", "Blit between bitmaps"),
    -228: ("LoadView", "Install a view"),
    -240: ("WaitBlit", "Wait for the blitter"),
    -246: ("SetRast", "Fill a raster with a colour"),
    -270: ("Text", "Render text into a RastPort"),
    -282: ("SetFont", "Select a font"),
    -288: ("OpenFont", "Open a font"),
    -294: ("CloseFont", "Close a font"),
    -306: ("Move", "Move the graphics pen"),
    -312: ("Draw", "Draw a line"),
    -324: ("AreaDraw", "Add a vertex to an area fill"),
    -330: ("AreaEnd", "Complete an area fill"),
    -354: ("SetAPen", "Set the primary drawing pen"),
    -360: ("SetBPen", "Set the secondary drawing pen"),
    -366: ("SetDrMd", "Set the drawing mode"),
    -558: ("OwnBlitter", "Take exclusive use of the blitter"),
    -564: ("DisownBlitter", "Release the blitter"),
}

INTUITION_LVOS = {
    -30: ("OpenIntuition", "Open Intuition, 1.0 compatible"),
    -36: ("Intuition", "Feed an input event to Intuition"),
    -60: ("ClearMenuStrip", "Detach a window's menus"),
    -72: ("CloseWindow", "Close a window"),
    -78: ("CloseWorkBench", "Close the Workbench screen"),
    -198: ("OpenScreen", "Open a screen"),
    -204: ("OpenWindow", "Open a window"),
    -210: ("PrintIText", "Render an IntuiText structure"),
    -222: ("RefreshGadgets", "Redraw a gadget list"),
    -264: ("SetMenuStrip", "Attach menus to a window"),
    -270: ("SetPointer", "Set a window's mouse pointer"),
    -276: ("SetWindowTitles", "Change a window's titles"),
    -342: ("DisplayBeep", "Flash the screen"),
    -348: ("AutoRequest", "Show a simple requester"),
    -462: ("CloseScreen", "Close a screen"),
}

#: The library a call belongs to cannot be known from the offset alone, so the
#: annotator reports the exec meaning by default and names the others when the
#: surrounding code proves which base is in A6.
LIBRARY_LVOS = {
    "exec.library": EXEC_LVOS,
    "dos.library": DOS_LVOS,
    "graphics.library": GRAPHICS_LVOS,
    "intuition.library": INTUITION_LVOS,
}


def _symbol_labels(symbols: dict | None) -> dict[int, str]:
    """Accept decimal or conventional hexadecimal addresses from project files."""
    labels = {}
    for key, value in (symbols or {}).items():
        try:
            address = int(str(key).strip().replace("&", "0x", 1).replace("$", "0x", 1), 0)
        except ValueError:
            continue
        labels[address] = str(value)
    return labels


BRANCH_MEANINGS = {
    "BRA": "Branch always",
    "BSR": "Branch to subroutine",
    "BEQ": "Branch if equal",
    "BNE": "Branch if not equal",
    "BCC": "Branch if carry clear",
    "BCS": "Branch if carry set",
    "BPL": "Branch if positive",
    "BMI": "Branch if negative",
    "BVC": "Branch if overflow clear",
    "BVS": "Branch if overflow set",
    "BGE": "Branch if greater or equal, signed",
    "BLT": "Branch if less than, signed",
    "BGT": "Branch if greater than, signed",
    "BLE": "Branch if less or equal, signed",
    "BHI": "Branch if higher, unsigned",
    "BLS": "Branch if lower or same, unsigned",
}

RETURN_MNEMONICS = {"RTS", "RTE", "RTR", "RTD"}

#: The Amiga's memory map, as a real machine decodes it.
HARDWARE_REGIONS = (
    (0x000000, 0x1FFFFF, "Chip RAM"),
    (0x200000, 0x9FFFFF, "Zorro II expansion space"),
    (0xA00000, 0xBEFFFF, "reserved expansion space"),
    (0xBFD000, 0xBFDF00, "CIA-B (8520, timers and disk control)"),
    (0xBFE001, 0xBFEF01, "CIA-A (8520, keyboard and parallel port)"),
    (0xC00000, 0xD7FFFF, "Slow (ranger) RAM"),
    (0xDC0000, 0xDC003F, "battery-backed clock"),
    (0xDFF000, 0xDFF1FE, "custom chips (Agnus, Denise, Paula)"),
    (0xE80000, 0xE8FFFF, "Autoconfig expansion board space"),
    (0xF00000, 0xF7FFFF, "extended ROM"),
    (0xF80000, 0xFFFFFF, "Kickstart ROM"),
)

#: The custom-chip registers a ROM touches most, by their hardware address.
CUSTOM_REGISTERS = {
    0xDFF000: "BLTDDAT", 0xDFF002: "DMACONR", 0xDFF004: "VPOSR",
    0xDFF006: "VHPOSR", 0xDFF00A: "JOY0DAT", 0xDFF00C: "JOY1DAT",
    0xDFF010: "ADKCONR", 0xDFF016: "POTGOR", 0xDFF01A: "DSKBYTR",
    0xDFF01C: "INTENAR", 0xDFF01E: "INTREQR", 0xDFF020: "DSKPTH",
    0xDFF024: "DSKLEN", 0xDFF02A: "VPOSW", 0xDFF034: "POTGO",
    0xDFF03E: "COPCON", 0xDFF040: "BLTCON0", 0xDFF042: "BLTCON1",
    0xDFF058: "BLTSIZE", 0xDFF080: "COP1LCH", 0xDFF084: "COP2LCH",
    0xDFF088: "COPJMP1", 0xDFF08A: "COPJMP2", 0xDFF08E: "DIWSTRT",
    0xDFF090: "DIWSTOP", 0xDFF092: "DDFSTRT", 0xDFF094: "DDFSTOP",
    0xDFF096: "DMACON", 0xDFF09A: "INTENA", 0xDFF09C: "INTREQ",
    0xDFF09E: "ADKCON", 0xDFF0A0: "AUD0LCH", 0xDFF100: "BPLCON0",
    0xDFF102: "BPLCON1", 0xDFF104: "BPLCON2", 0xDFF108: "BPL1MOD",
    0xDFF10A: "BPL2MOD", 0xDFF180: "COLOR00", 0xDFF182: "COLOR01",
    0xDFF1FC: "FMODE",
}

#: The 68000 exception vectors, which live in the first kilobyte of Chip RAM.
EXCEPTION_VECTORS = {
    0x000: "Initial SSP", 0x004: "Initial PC", 0x008: "Bus error",
    0x00C: "Address error", 0x010: "Illegal instruction", 0x014: "Divide by zero",
    0x018: "CHK instruction", 0x01C: "TRAPV instruction", 0x020: "Privilege violation",
    0x024: "Trace", 0x028: "Line-A emulator", 0x02C: "Line-F emulator",
    0x060: "Spurious interrupt", 0x064: "Level 1 autovector (soft/DSK/TBE)",
    0x068: "Level 2 autovector (CIA-A / ports)", 0x06C: "Level 3 autovector (COPER/VERTB/BLIT)",
    0x070: "Level 4 autovector (audio)", 0x074: "Level 5 autovector (DSKSYN/RBF)",
    0x078: "Level 6 autovector (CIA-B / EXTER)", 0x07C: "Level 7 autovector (NMI)",
}

#: Retained under the previous name so the annotator's call sites are stable.
MOS_VECTORS = EXCEPTION_VECTORS
MOS_CALLS = {offset: name for offset, (name, _summary) in EXEC_LVOS.items()}
MOS_PURPOSES = {offset: summary for offset, (_name, summary) in EXEC_LVOS.items()}


def _hex_value(operand: str) -> int | None:
    """Parse the first numeric literal in a Capstone operand string.

    Capstone renders operands in many shapes -- ``#$1F``, ``$dff180.l``,
    ``-$228(a6)``, ``$f80014(pc)`` -- so the value is extracted by pattern
    rather than by trimming, which is what made the earlier version silently
    return nothing for program-counter-relative addresses.
    """
    match = re.search(r"(-?)\$([0-9A-Fa-f]+)|(-?)\b(\d+)\b", str(operand or ""))
    if not match:
        return None
    if match.group(2) is not None:
        value = int(match.group(2), 16)
        return -value if match.group(1) == "-" else value
    value = int(match.group(4))
    return -value if match.group(3) == "-" else value


def _hex_values(operand: str) -> list[int]:
    """Return every numeric literal in an operand, in the order they appear.

    ``MOVE.W #$0FFF,$DFF180`` carries two: the immediate and the destination.
    Only the second identifies a hardware register, so the annotator needs to
    see both rather than only the first.
    """
    values: list[int] = []
    for match in re.finditer(r"(-?)\$([0-9A-Fa-f]+)|(-?)\b(\d+)\b", str(operand or "")):
        if match.group(2) is not None:
            value = int(match.group(2), 16)
            values.append(-value if match.group(1) == "-" else value)
        else:
            value = int(match.group(4))
            values.append(-value if match.group(3) == "-" else value)
    return values


def _character(value: int | None) -> str:
    if value is None or not 32 <= value <= 126:
        return ""
    return f"'{chr(value)}'"


def _cstring(data: bytes, origin: int, address: int | None, limit: int = 120) -> str:
    if address is None:
        return ""
    offset = address - origin
    if not 0 <= offset < len(data):
        return ""
    end = data.find(b"\0", offset, min(len(data), offset + limit))
    if end < 0:
        return ""
    raw = data[offset:end]
    if not raw or any(byte < 32 or byte > 126 for byte in raw):
        return ""
    return raw.decode("latin-1")


def _hardware_region(address: int | None) -> str:
    if address is None:
        return ""
    return next(
        (name for start, end, name in HARDWARE_REGIONS if start <= address <= end), ""
    )


def _library_vector(operand: str) -> int | None:
    """Return the LVO offset when an operand addresses a library base in A6.

    Capstone renders the call as ``-$0228(a6)``. Only negative displacements
    through A6 are treated as library vectors, because that is the calling
    convention every Amiga library uses and the one thing that distinguishes a
    vector call from an ordinary structure access.
    """
    text = str(operand or "").strip().lower()
    if not text.endswith("(a6)"):
        return None
    displacement = text[: -len("(a6)")].strip()
    if not displacement.startswith("-"):
        return None
    value = _hex_value(displacement)
    if value is None or value >= 0 or value % 6:
        return None
    return value


def _library_call_comment(offset: int, library: str | None) -> str:
    """Describe a library vector call, naming the library when it is known."""
    table = LIBRARY_LVOS.get(library or "", EXEC_LVOS)
    entry = table.get(offset)
    if entry is None and library:
        entry = EXEC_LVOS.get(offset)
        library = "exec.library"
    if entry is None:
        return f"Call library vector {offset} through A6"
    name, summary = entry
    prefix = f"{library} " if library else ""
    return f"{prefix}{name}: {summary}"


def _semantic_68000_labels(report: dict) -> None:
    """Assign stable labels from proved control flow and routine behaviour."""
    rows = report["rows"]
    by_address = {int(row["address"]): row for row in rows}
    index_by_address = {int(row["address"]): index for index, row in enumerate(rows)}
    call_mnemonics = {"JSR", "BSR"}
    call_targets = {
        int(row["target"])
        for row in rows
        if base_mnemonic(row.get("mnemonic")) in call_mnemonics
        and isinstance(row.get("target"), int)
        and int(row["target"]) in by_address
    }

    def routine_rows(target: int) -> list[dict]:
        start = index_by_address.get(target)
        if start is None:
            return []
        block = []
        for row in rows[start : start + 96]:
            if block and int(row["address"]) in call_targets:
                break
            block.append(row)
            if base_mnemonic(row.get("mnemonic")) in RETURN_MNEMONICS | {"JMP", "BRA"}:
                break
        return block

    for target in sorted(call_targets):
        target_row = by_address[target]
        existing = str(target_row.get("label") or "")
        if existing and not existing.startswith(("sub_", "loc_", "subroutine_")):
            continue
        block = routine_rows(target)
        endings = {base_mnemonic(row.get("mnemonic")) for row in block}
        vectors = [
            _library_vector(str(row.get("operand") or ""))
            for row in block
            if row.get("mnemonic") in call_mnemonics
        ]
        vectors = [value for value in vectors if value is not None]
        backwards_branch = any(
            isinstance(row.get("target"), int) and int(row["target"]) <= int(row["address"])
            for row in block
            if base_mnemonic(row.get("mnemonic")) in BRANCH_MEANINGS
        )
        if "RTE" in endings:
            purpose = "interrupt_handler"
        elif "TRAP" in endings:
            purpose = "raise_exception"
        elif vectors:
            name = EXEC_LVOS.get(vectors[0], ("library_call",))[0]
            purpose = f"call_{name.lower()}"
        elif backwards_branch:
            purpose = "loop_routine"
        else:
            hardware = next(
                (
                    _hardware_region(int(row["target"]))
                    for row in block
                    if isinstance(row.get("target"), int) and _hardware_region(int(row["target"]))
                ),
                "",
            )
            purpose = "access_hardware" if hardware else "subroutine"
        target_row["label"] = f"{purpose}_{target:06X}"

    branch_names = {
        "BEQ": "equal", "BNE": "not_equal", "BCC": "carry_clear",
        "BCS": "carry_set", "BMI": "negative", "BPL": "positive",
        "BVC": "overflow_clear", "BVS": "overflow_set", "BGE": "greater_equal",
        "BLT": "less_than", "BGT": "greater_than", "BLE": "less_equal",
        "BHI": "higher", "BLS": "lower_same", "BRA": "always",
    }
    flow_references: dict[int, list[dict]] = {}
    for source in rows:
        target = source.get("target")
        if base_mnemonic(source.get("mnemonic")) in {*BRANCH_MEANINGS, "JMP"} and isinstance(target, int):
            flow_references.setdefault(target, []).append(source)
    for target, references in flow_references.items():
        target_row = by_address.get(target)
        if target_row is None or target in call_targets or target_row.get("label"):
            continue
        if any(target <= int(source["address"]) for source in references):
            purpose = "loop"
        else:
            mnemonics = {base_mnemonic(source.get("mnemonic")) for source in references}
            if len(mnemonics) == 1:
                mnemonic = next(iter(mnemonics))
                purpose = branch_names.get(mnemonic, "dispatch" if mnemonic == "JMP" else "continue")
            else:
                purpose = "continue"
        target_row["label"] = f"{purpose}_{target:06X}"


def base_mnemonic(mnemonic: str) -> str:
    """Return an instruction's operation without its ``.B``/``.W``/``.L`` size."""
    return str(mnemonic or "").upper().split(".", 1)[0]


def _annotate_68000(report: dict, data: bytes) -> dict:
    """Explain a 68000 listing in Amiga terms.

    The two things that make Amiga machine code readable are knowing which
    library vector a call goes through, and knowing which chip a memory
    reference touches. Both are tracked here: the library base most recently
    loaded into A6, and the address ranges the hardware decodes.
    """
    rows = report["rows"]
    by_address = {int(row["address"]): row for row in rows}
    _semantic_68000_labels(report)
    call_mnemonics = {"JSR", "BSR"}
    call_targets = {
        int(row["target"])
        for row in rows
        if base_mnemonic(row.get("mnemonic")) in call_mnemonics
        and isinstance(row.get("target"), int)
    }
    flow_targets = {
        int(row["target"])
        for row in rows
        if base_mnemonic(row.get("mnemonic")) in set(BRANCH_MEANINGS) | {"JMP"}
        and isinstance(row.get("target"), int)
    }
    for target in call_targets | flow_targets:
        target_row = by_address.get(target)
        if target_row is not None and not target_row.get("label"):
            target_row["label"] = f"{'sub' if target in call_targets else 'loc'}_{target:06X}"
    for row in rows:
        target = row.get("target")
        if (
            isinstance(target, int)
            and target in by_address
            and base_mnemonic(row.get("mnemonic")) in {*call_mnemonics, "JMP", *BRANCH_MEANINGS}
        ):
            row["operand"] = by_address[target].get("label") or row["operand"]

    origin = int(report["origin"])
    library_in_a6: str | None = None
    pending_library: str | None = None
    for row in rows:
        mnemonic = base_mnemonic(row.get("mnemonic"))
        # Capstone spaces its operands; compare against a space-free form so a
        # destination register test does not depend on formatting.
        operand = str(row.get("operand") or "")
        compact = operand.replace(" ", "").lower()
        target = row.get("target")
        comment = ""

        # Track which library base is in A6. The name comes from the string an
        # OpenLibrary call was given, which is the only place it appears.
        if mnemonic.startswith("LEA") and compact.endswith(",a1"):
            text = _cstring(data, origin, _hex_value(operand))
            if text.endswith(".library"):
                pending_library = text
        if mnemonic.startswith("MOVE") and compact.endswith(",a6"):
            library_in_a6 = pending_library
        if mnemonic in {"JSR", "BSR"}:
            vector = _library_vector(operand)
            if vector is not None:
                comment = _library_call_comment(vector, library_in_a6)
                if library_in_a6 == "exec.library" and vector in (-552, -408):
                    library_in_a6 = pending_library
            elif isinstance(target, int):
                comment = f"Call subroutine {operand}"
        elif mnemonic in BRANCH_MEANINGS:
            comment = f"{BRANCH_MEANINGS[mnemonic]} to {operand}"
        elif mnemonic == "JMP":
            comment = f"Continue execution at {operand}"
        elif mnemonic in RETURN_MNEMONICS:
            comment = {
                "RTS": "Return from subroutine",
                "RTE": "Return from exception",
                "RTR": "Return and restore condition codes",
                "RTD": "Return and deallocate stack",
            }[mnemonic]
        elif mnemonic.startswith("TRAP"):
            comment = "Raise a processor trap"
        elif mnemonic.startswith(("MOVE", "BTST", "BSET", "BCLR", "BCHG", "AND", "OR")):
            literals = _hex_values(operand)
            # Prefer a literal that names something, so an immediate operand
            # does not hide the destination register beside it.
            value = next(
                (
                    candidate
                    for candidate in literals
                    if candidate in CUSTOM_REGISTERS or candidate in EXCEPTION_VECTORS
                ),
                literals[0] if literals else None,
            )
            register = CUSTOM_REGISTERS.get(value) if value is not None else None
            vector = EXCEPTION_VECTORS.get(value) if value is not None else None
            region = _hardware_region(value)
            if value == 4 and compact.endswith(",a6"):
                # ``MOVEA.L $4.W,A6`` is the first instruction of almost every
                # Amiga program: absolute address 4 holds ExecBase.
                comment = "Load ExecBase from absolute address 4 into A6"
                library_in_a6 = "exec.library"
                pending_library = "exec.library"
            elif register:
                comment = f"Access the {register} custom register at ${value:06X}"
            elif vector:
                comment = f"Access the {vector} exception vector at ${value:03X}"
            elif region and value is not None and value >= 0xBFD000:
                comment = f"Access {region} at ${value:06X}"
            elif operand.startswith("#") and value is not None:
                display = _character(value)
                comment = f"Load ${value:X}{f' ({display})' if display else ''}"
        if comment:
            row["comment"] = comment
        else:
            row["comment"] = str(row.get("comment") or "")
    return report


#: Capstone modes for each processor the workbench offers.
M68K_MODES = {
    "68000": "CS_MODE_M68K_000",
    "68010": "CS_MODE_M68K_010",
    "68020": "CS_MODE_M68K_020",
    "68030": "CS_MODE_M68K_030",
    "68040": "CS_MODE_M68K_040",
    "68060": "CS_MODE_M68K_060",
    "m68k": "CS_MODE_M68K_000",
}


def disassemble_68000(data: bytes, *, origin: int = DEFAULT_ROM_BASE, start: int = 0,
                      length: int | None = None, symbols: dict | None = None) -> dict:
    """Disassemble MC68000 code and annotate it in Amiga terms."""
    report = disassemble_capstone(
        data,
        architecture="68000",
        origin=origin,
        start=start,
        length=length,
        symbols=symbols,
    )
    return _annotate_68000(report, data)


def _with_control_flow(report: dict, entry_points: list[int]) -> dict:
    rows = report["rows"]
    by_address = {int(row["address"]): row for row in rows}
    xrefs: dict[int, list[int]] = {}
    for row in rows:
        target = row.get("target")
        if isinstance(target, int):
            xrefs.setdefault(target, []).append(int(row["address"]))
    starts = [point for point in entry_points if point in by_address]
    if not starts and rows:
        starts = [int(rows[0]["address"])]
    reachable, pending = set(), list(starts)
    while pending:
        address = pending.pop()
        row = by_address.get(address)
        if row is None or address in reachable:
            continue
        reachable.add(address)
        mnemonic = str(row.get("mnemonic") or "").upper()
        target = row.get("target")
        size = max(1, len(str(row.get("bytes") or "").split()))
        fallthrough = address + size
        if isinstance(target, int) and (mnemonic.startswith("B") or mnemonic in {"JSR", "JSL", "JMP", "JML", "BL", "BLX", "BSR", "BRA", "BRL"}):
            pending.append(target)
        if mnemonic not in {"JMP", "JML", "BRA", "BRL", "RTS", "RTL", "RTI", "BRK", "RTE"} and not mnemonic.startswith("B."):
            pending.append(fallthrough)
    for row in rows:
        row["reachable"] = int(row["address"]) in reachable
        row["references"] = xrefs.get(int(row["address"]), [])
    report["entryPoints"] = starts
    report["crossReferences"] = [
        {"target": target, "sources": sources}
        for target, sources in sorted(xrefs.items())
    ]
    report["reachableInstructions"] = len(reachable)
    return report


def _annotate_generic_control_flow(report: dict) -> dict:
    rows = report["rows"]
    by_address = {int(row["address"]): row for row in rows}
    call_names = {"BL", "BLX", "BSR", "JSR", "JSL"}
    jump_names = {"B", "BRA", "BRL", "JMP", "JML"}
    for row in rows:
        target = row.get("target")
        mnemonic = str(row.get("mnemonic") or "").upper()
        target_row = by_address.get(target) if isinstance(target, int) else None
        if target_row is not None and not target_row.get("label"):
            if mnemonic in call_names:
                purpose = "subroutine"
            elif int(target) <= int(row["address"]):
                purpose = "loop"
            elif mnemonic in jump_names:
                purpose = "dispatch"
            else:
                purpose = "continue"
            target_row["label"] = f"{purpose}_{int(target):X}"
    for row in rows:
        mnemonic = str(row.get("mnemonic") or "").upper()
        operand = str(row.get("operand") or "")
        target = row.get("target")
        target_row = by_address.get(target) if isinstance(target, int) else None
        if target_row is not None:
            operand = row["operand"] = target_row.get("label") or operand
        if row.get("comment"):
            continue
        if mnemonic in call_names:
            row["comment"] = f"Call subroutine {operand}"
        elif mnemonic in jump_names:
            row["comment"] = f"Continue execution at {operand}"
        elif mnemonic.startswith("B") and isinstance(target, int):
            row["comment"] = f"Conditional branch to {operand}"
        elif mnemonic in {"RTS", "RTL", "RTI", "RTE"} or (mnemonic == "BX" and operand.upper() == "LR"):
            row["comment"] = "Return from subroutine"
    return report


def disassemble_capstone(data: bytes, *, architecture: str, origin: int = 0,
                         start: int = 0, length: int | None = None,
                         symbols: dict | None = None,
                         entry_points: list[int] | None = None) -> dict:
    if Cs is None:
        raise RomWorkbenchError("The production disassembly engine is not installed.")
    if start < 0 or start >= len(data):
        raise RomWorkbenchError("The disassembly start is outside this ROM bank.")
    requested = len(data) - start if length is None else max(1, int(length))
    end = min(len(data), start + requested, start + MAX_DISASSEMBLY_BYTES)
    mode = {
        "68000": CS_MODE_M68K_000,
        "68010": CS_MODE_M68K_010,
        "68020": CS_MODE_M68K_020,
        "68030": CS_MODE_M68K_030,
        "68040": CS_MODE_M68K_040,
        "68060": CS_MODE_M68K_060,
        "m68k": CS_MODE_M68K_000,
    }.get(architecture)
    if mode is None:
        raise RomWorkbenchError(
            "Choose 68000, 68010, 68020, 68030, 68040 or 68060 disassembly."
        )
    # The 68000 family is big-endian in every Amiga, so the mode is fixed
    # rather than offered as a choice that could only ever be wrong.
    engine = Cs(CS_ARCH_M68K, mode | CS_MODE_BIG_ENDIAN)
    engine.skipdata = True
    rows = []
    labels = _symbol_labels(symbols)
    branch_names = {
        "bra", "bsr", "jmp", "jsr",
        "bcc", "bcs", "beq", "bne", "bmi", "bpl", "bvc", "bvs",
        "bge", "blt", "bgt", "ble", "bhi", "bls",
        "dbra", "dbf", "dbeq", "dbne",
    }
    for instruction in engine.disasm(data[start:end], origin + start):
        mnemonic = instruction.mnemonic.upper()
        operand = instruction.op_str
        target = None
        if instruction.mnemonic.lower().split(".", 1)[0] in branch_names:
            token = operand.rsplit(",", 1)[-1].strip().lstrip("#")
            try:
                target = int(token[1:], 16) if token.startswith("$") else int(token, 0)
            except ValueError:
                target = None
        rows.append({"offset": instruction.address - origin, "address": instruction.address,
                     "bytes": instruction.bytes.hex(" ").upper(), "mnemonic": mnemonic,
                     "operand": operand, "target": target,
                     "label": labels.get(instruction.address, ""), "comment": ""})
    report = {"architecture": architecture, "origin": origin, "start": start,
              "end": end, "truncated": end < start + requested, "rows": rows}
    return _annotate_generic_control_flow(_with_control_flow(report, entry_points or []))


def disassemble(data: bytes, *, architecture: str, origin: int, start: int = 0,
                length: int | None = None, symbols: dict | None = None,
                entry_points: list[int] | None = None) -> dict:
    """Disassemble a range and annotate it with Amiga system knowledge."""
    report = disassemble_capstone(
        data,
        architecture=architecture,
        origin=origin,
        start=start,
        length=length,
        symbols=symbols,
        entry_points=entry_points,
    )
    return _annotate_68000(report, data)


def bank_map(data: bytes, bank_size: int, erase_byte: int = 0xFF) -> dict:
    """Map each bank of a ROM image to the addresses it answers at.

    A Kickstart image is mapped as one contiguous block at the base its size
    implies, so a bank's window is its file offset added to that base rather
    than a fixed paging window.
    """
    rows, hashes = [], {}
    base = rom_base(len(data))
    for bank, offset in enumerate(range(0, len(data), bank_size)):
        block = data[offset:offset + bank_size]
        decoded = inspect_bank(block, bank, erase_byte)
        digest = decoded["diagnostics"]["sha256"]
        hashes.setdefault(digest, []).append(bank)
        rows.append({"bank": bank, "fileOffset": offset,
                     "cpuWindow": f"${base + offset:06X}-${base + offset + max(1, len(block)) - 1:06X}",
                     "length": len(block), "title": decoded["name"], "type": decoded["filetype"],
                     "empty": decoded["empty"], "sha256": digest})
    for row in rows:
        row["duplicates"] = [number for number in hashes[row["sha256"]] if number != row["bank"]]
    return {"bankSize": bank_size, "bankCount": len(rows), "banks": rows}


def compare_roms(left: bytes, right: bytes, *, max_ranges: int = 10000) -> dict:
    maximum = max(len(left), len(right))
    ranges, start, changed_bytes, captured_hex, omitted_bytes = [], None, 0, 0, False
    for offset in range(maximum + 1):
        different = offset < maximum and (
            offset >= len(left) or offset >= len(right) or left[offset] != right[offset]
        )
        if different and start is None:
            start = offset
        elif not different and start is not None:
            length = offset - start
            changed_bytes += length
            if len(ranges) < max_ranges:
                left_bytes, right_bytes = left[start:offset], right[start:offset]
                keep_bytes = captured_hex + 2 * (len(left_bytes) + len(right_bytes)) <= MAX_PATCH_BYTES * 4
                left_hex = left_bytes.hex().upper() if keep_bytes else ""
                right_hex = right_bytes.hex().upper() if keep_bytes else ""
                ranges.append({"start": start, "end": offset, "length": length,
                               "left": left_hex, "right": right_hex})
                captured_hex += len(left_hex) + len(right_hex)
                omitted_bytes = omitted_bytes or not keep_bytes
            start = None
    return {"leftSize": len(left), "rightSize": len(right), "leftSha256": sha256_bytes(left),
            "rightSha256": sha256_bytes(right), "changedBytes": changed_bytes,
            "ranges": ranges, "rangesTruncated": changed_bytes > sum(row["length"] for row in ranges),
            "bytesOmitted": omitted_bytes}


def make_patch(left: bytes, right: bytes) -> dict:
    report = compare_roms(left, right)
    if report["changedBytes"] > MAX_PATCH_BYTES or report["rangesTruncated"] or report["bytesOmitted"]:
        raise RomWorkbenchError("That patch exceeds the 16 MiB safety limit.")
    return {"format": PATCH_FORMAT, "sourceSha256": report["leftSha256"],
            "targetSha256": report["rightSha256"], "sourceSize": len(left), "targetSize": len(right),
            "ranges": [{"offset": row["start"], "remove": len(bytes.fromhex(row["left"])),
                        "data": base64.b64encode(bytes.fromhex(row["right"])).decode("ascii")}
                       for row in report["ranges"]]}


def make_selective_patch(left: bytes, right: bytes, indexes: list[int]) -> dict:
    report = compare_roms(left, right)
    selected = sorted(set(int(index) for index in indexes))
    if not selected or any(index < 0 or index >= len(report["ranges"]) for index in selected):
        raise RomWorkbenchError("Choose one or more valid changed ranges.")
    result = bytearray(left)
    adjustment = 0
    for index in selected:
        row = report["ranges"][index]
        if not row.get("right"):
            raise RomWorkbenchError("That changed range is too large for a selective patch.")
        offset = row["start"] + adjustment
        remove = len(bytes.fromhex(row["left"]))
        replacement = bytes.fromhex(row["right"])
        result[offset:offset + remove] = replacement
        adjustment += len(replacement) - remove
    return make_patch(left, bytes(result))


def apply_patch(source: bytes, document: dict) -> bytes:
    if document.get("format") != PATCH_FORMAT or sha256_bytes(source) != document.get("sourceSha256"):
        raise RomWorkbenchError("This patch does not match the selected source ROM checksum.")
    result = bytearray(source)
    adjustment = 0
    for row in document.get("ranges", []):
        offset, remove = int(row["offset"]) + adjustment, int(row["remove"])
        replacement = base64.b64decode(row["data"], validate=True)
        if offset < 0 or remove < 0 or offset + remove > len(result):
            raise RomWorkbenchError("The patch contains an invalid byte range.")
        result[offset:offset + remove] = replacement
        adjustment += len(replacement) - remove
    if len(result) != int(document.get("targetSize", -1)) or sha256_bytes(result) != document.get("targetSha256"):
        raise RomWorkbenchError("The patched bytes did not produce the expected target ROM.")
    return bytes(result)


def audit_rom(data: bytes, bank_size: int, erase_byte: int = 0xFF) -> dict:
    findings, repairable = [], []
    mapping = bank_map(data, bank_size, erase_byte)
    if len(data) % bank_size:
        findings.append({"level": "warning", "code": "partial-bank", "message":
                         f"The final bank contains {len(data) % bank_size:,} bytes."})
    for row in mapping["banks"]:
        block = data[row["fileOffset"]:row["fileOffset"] + bank_size]
        decoded = inspect_bank(block, row["bank"], erase_byte)
        for warning in decoded["warnings"]:
            findings.append({"level": "error", "code": "header-role", "bank": row["bank"], "message": warning})
            if "header-role-flags" not in repairable:
                repairable.append("header-role-flags")
        if row["duplicates"] and row["bank"] < min(row["duplicates"]):
            findings.append({"level": "info", "code": "duplicate-bank", "bank": row["bank"],
                             "message": f"Bank {row['bank']} is identical to bank(s) {', '.join(map(str, row['duplicates']))}."})
    extension = parse_extended_rom_header(data)
    if extension and not extension.checksum_valid:
        findings.append({"level": "error", "code": "extension-checksum", "message":
                         "The AmigaOS extension-ROM checksum is invalid."})
        repairable.append("extension-checksum")
    return {"healthy": not any(row["level"] == "error" for row in findings),
            "sha256": sha256_bytes(data), "crc32": f"{zlib.crc32(data) & 0xFFFFFFFF:08X}",
            "findings": findings, "repairable": repairable, "map": mapping}


def repair_extension_checksum(data: bytes) -> bytes:
    extension = parse_extended_rom_header(data)
    if extension is None:
        raise RomWorkbenchError("No standard AmigaOS extension-ROM trailer was found.")
    result = bytearray(data)
    result[-12:-8] = extension.calculated_checksum.to_bytes(4, "little")
    return bytes(result)


def repair_header_role_flags(data: bytes, bank_size: int) -> bytes:
    result = bytearray(data)
    repaired = 0
    for offset in range(0, len(result), bank_size):
        block = bytes(result[offset:offset + bank_size])
        header = parse_rom_header(block)
        if header is None:
            continue
        roles = (0x40 if header.language_entry is not None else 0) | (0x80 if header.service_entry is not None else 0)
        new_type = (header.type_byte & 0x3F) | roles
        if new_type != header.type_byte:
            result[offset + 6] = new_type
            repaired += 1
    if not repaired:
        raise RomWorkbenchError("No contradictory ROM header flags were found.")
    return bytes(result)


def normalise_project(document: dict | None) -> dict:
    source = document if isinstance(document, dict) else {}
    identity_source = source.get("identity") if isinstance(source.get("identity"), dict) else {}
    identity = {
        key: str(identity_source.get(key) or "")[:limit]
        for key, limit in {"title": 160, "version": 80, "publisher": 160,
                           "platform": 120, "notes": 2000}.items()
    }
    return {"format": PROJECT_FORMAT, "notes": str(source.get("notes") or "")[:20000],
            "hardware": str(source.get("hardware") or "")[:200],
            "symbols": {str(key): str(value)[:80] for key, value in dict(source.get("symbols") or {}).items()},
            "regions": [row for row in source.get("regions", []) if isinstance(row, dict)][:2048],
            "tests": [row for row in source.get("tests", []) if isinstance(row, dict)][:512],
            "identity": identity}


def project_json(document: dict) -> bytes:
    return json.dumps(normalise_project(document), indent=2, sort_keys=True).encode("utf-8") + b"\n"


def identify_rom(data: bytes, catalogue_path: Path | None = None) -> dict:
    """Identify exact and common transformed dumps without guessing a title."""
    digest, crc = sha256_bytes(data), f"{zlib.crc32(data) & 0xFFFFFFFF:08X}"
    records = []
    if catalogue_path and catalogue_path.is_file():
        try:
            document = json.loads(catalogue_path.read_text(encoding="utf-8"))
            records = document.get("roms", []) if isinstance(document, dict) else []
        except (OSError, ValueError, json.JSONDecodeError):
            records = []
    exact = next((row for row in records if str(row.get("sha256", "")).lower() == digest), None)
    transformations = []
    if len(data) % 2 == 0 and data[:len(data)//2] == data[len(data)//2:]:
        transformations.append("The image contains two identical mirrored halves.")
    if len(data) in {8192, 16384, 32768, 65536, 131072, 262144}:
        transformations.append(f"The size is a conventional {len(data) // 1024} KiB ROM or bank set.")
    return {"matched": exact is not None, "record": exact, "sha256": digest, "crc32": crc,
            "transformations": transformations}


def build_expansion_rom(title: str, modules: list[dict] | None = None,
                        size: int = 16 * 1024, erase_byte: int = 0xFF) -> bytes:
    """Build an inert but structurally valid Amiga expansion ROM.

    Kickstart finds a ROM's contents by scanning for resident tags, so the
    scaffold is one real ``$4AFC`` tag whose init routine is ``MOVEQ #0,D0 /
    RTS``. That is a genuine "nothing to install" answer, so a scaffold fitted
    to a machine before its driver is written cannot do anything unexpected.
    Any further module names are recorded after the tag as an inventory the
    developer fills in; they are not pretended to be working modules.
    """
    if size not in {8192, 16384, 32768}:
        raise RomWorkbenchError("An Amiga expansion ROM scaffold must be 8K, 16K or 32K.")
    clean = "".join(
        character for character in str(title or "forge") if 32 <= ord(character) <= 126
    )[:24] or "forge"
    data = bytearray(make_expansion_rom(size, clean, erase_byte))
    inventory = bytearray(b"AFFMODULES\0")
    for row in modules or []:
        name = "".join(
            character for character in str(row.get("name") or "").strip()
            if character.isalnum() or character in "._-"
        )[:31]
        if name:
            purpose = str(row.get("syntax") or row.get("purpose") or "")[:80]
            inventory.extend(
                name.encode("latin-1", "replace") + b"\0"
                + purpose.encode("latin-1", "replace") + b"\0"
            )
    start = 0x200
    end = min(len(data), start + len(inventory))
    if start + len(inventory) > size:
        raise RomWorkbenchError("Those module names do not fit in the selected ROM size.")
    data[start:end] = inventory[:end - start]
    return bytes(data)


#: The identity of the workbench's own ROM file archive, so a reader can tell
#: which release wrote it.
DATA_ARCHIVE_SIGNATURE = b"AFFARCHIVE1"


def build_data_archive(title: str, files: list[tuple[str, bytes]], *,
                       size: int = 16 * 1024, erase_byte: int = 0xFF) -> bytes:
    """Build a documented file archive inside a valid expansion ROM.

    This is a deterministic storage layout for companion data. Kickstart will
    mount the ROM's resident tag but has no idea what the archive means, so a
    driver of the developer's own has to read it.
    """
    data = bytearray(build_expansion_rom(title, [{"name": "affarchive.library"}], size, erase_byte))
    directory = bytearray(DATA_ARCHIVE_SIGNATURE)
    payload = bytearray()
    for name, content in files:
        encoded = str(name).encode("latin-1", "replace")[:31]
        # Every field is big-endian, because the 68000 that reads it is.
        directory.extend(
            bytes((len(encoded),)) + encoded
            + len(payload).to_bytes(4, "big") + len(content).to_bytes(4, "big")
        )
        payload.extend(content)
    directory.append(0)
    start = 0x400
    if start + len(directory) + len(payload) > size:
        raise RomWorkbenchError("Those files do not fit in the selected ROM size.")
    data[start:start + len(directory)] = directory
    data[start + len(directory):start + len(directory) + len(payload)] = payload
    return bytes(data)


def hardware_export(data: bytes, *, device_size: int, erase_byte: int = 0xFF,
                    mirror: bool = False, lanes: int = 1, byte_swap: bool = False,
                    word_swap: bool = False,
                    address_swaps: list[tuple[int, int]] | None = None) -> dict:
    if device_size < len(data) or device_size > 64 * 1024 * 1024 or device_size & (device_size - 1):
        raise RomWorkbenchError("Choose a power-of-two device size large enough for the ROM.")
    if lanes not in {1, 2, 4} or device_size % lanes:
        raise RomWorkbenchError("Choose one, two or four equal byte lanes.")
    if mirror and data:
        repeats = (device_size + len(data) - 1) // len(data)
        prepared = (data * repeats)[:device_size]
    else:
        prepared = data.ljust(device_size, bytes((erase_byte & 0xFF,)))
    if byte_swap:
        swapped = bytearray(prepared)
        for offset in range(0, len(swapped) - 1, 2):
            swapped[offset], swapped[offset + 1] = swapped[offset + 1], swapped[offset]
        prepared = bytes(swapped)
    if word_swap:
        swapped = bytearray(prepared)
        for offset in range(0, len(swapped) - 3, 4):
            swapped[offset:offset + 4] = swapped[offset + 2:offset + 4] + swapped[offset:offset + 2]
        prepared = bytes(swapped)
    swaps = []
    maximum_bit = device_size.bit_length() - 1
    for left, right in address_swaps or []:
        left, right = int(left), int(right)
        if left == right or min(left, right) < 0 or max(left, right) >= maximum_bit:
            raise RomWorkbenchError("Address-line swaps must name two different address bits used by the device.")
        swaps.append((left, right))
    if swaps:
        rewired = bytearray(len(prepared))
        for source, value in enumerate(prepared):
            target = source
            for left, right in swaps:
                left_value, right_value = (target >> left) & 1, (target >> right) & 1
                if left_value != right_value:
                    target ^= (1 << left) | (1 << right)
            rewired[target] = value
        prepared = bytes(rewired)
    components = [prepared[index::lanes] for index in range(lanes)]
    return {"deviceSize": device_size, "lanes": lanes, "eraseByte": erase_byte & 0xFF,
            "mirrored": mirror, "byteSwapped": byte_swap, "wordSwapped": word_swap,
            "addressSwaps": [list(pair) for pair in swaps], "sha256": sha256_bytes(prepared),
            "components": components}


def hardware_export_zip(result: dict, stem: str = "rom") -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for index, content in enumerate(result["components"], 1):
            name = f"{stem}.rom" if len(result["components"]) == 1 else f"{stem}-lane-{index}.rom"
            archive.writestr(name, content)
        report = {key: value for key, value in result.items() if key != "components"}
        archive.writestr("PROGRAMMING.md", "# ROM programming export\n\n```json\n" + json.dumps(report, indent=2) + "\n```\n")
    return output.getvalue()
