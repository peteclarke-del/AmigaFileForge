from __future__ import annotations

import gzip
import io
import re
from . import amiga_paths


LISTING_SNIFF_LIMIT = 128 * 1024

#: Names AmigaDOS itself runs, or that a disk conventionally starts from.
SCRIPT_NAMES = {
    "startup-sequence",
    "user-startup",
    "shell-startup",
    "diskmenu",
    "startup",
    "start",
    "loader",
    "menu",
    "boot",
}

#: A script line is either an AmigaDOS shell command or an AmigaBASIC
#: statement. The two are told apart so the editor can say which language it
#: is looking at, and AmigaDOS names its commands without any sigil, so the
#: distinction is made by vocabulary rather than by punctuation.
AMIGADOS_COMMANDS = (
    "ADDBUFFERS|ALIAS|ASSIGN|AVAIL|BINDDRIVERS|BREAK|CD|CHANGETASKPRI|COPY|CD|DATE|"
    "DELETE|DIR|DISKCHANGE|DISKDOCTOR|ECHO|ELSE|ENDCLI|ENDIF|ENDSKIP|EVAL|EXECUTE|"
    "FAILAT|FAULT|FILENOTE|FORMAT|GETENV|IF|INFO|INSTALL|JOIN|LAB|LIST|LOADWB|LOCK|"
    "MAKEDIR|MAKELINK|MOUNT|NEWSHELL|PATH|PROMPT|PROTECT|QUIT|RELABEL|REMRAD|RENAME|"
    "RESIDENT|RUN|SEARCH|SETCLOCK|SETDATE|SETENV|SETPATCH|SKIP|SORT|STACK|STATUS|"
    "TYPE|UNALIAS|UNSET|UNSETENV|VERSION|WAIT|WHICH|WHY"
)
AMIGABASIC_STATEMENTS = (
    "CALL|CHAIN|CIRCLE|CLEAR|CLOSE|CLS|COLOR|DATA|DEFINT|DIM|END|FOR|GET|GOSUB|GOTO|"
    "IF|INPUT|LIBRARY|LINE|LOAD|LOCATE|MENU|NEXT|OBJECT|ON|OPEN|PAINT|PALETTE|POKE|"
    "PRINT|PSET|PUT|RANDOMIZE|READ|REM|RESTORE|RETURN|RUN|SAY|SCREEN|SOUND|STOP|"
    "SUB|SWAP|SYSTEM|WAVE|WEND|WHILE|WIDTH|WINDOW|WRITE"
)
SCRIPT_COMMAND_RE = re.compile(
    rf"^\s*(?:(?:C:)?({AMIGADOS_COMMANDS})\b\s*(.*)|"
    rf"(?:\d+\s+)?({AMIGABASIC_STATEMENTS})\b\s*(.*))$",
    re.IGNORECASE,
)


def format_basic_listing(source: str) -> str:
    """Give every numbered AmigaBASIC line one visible separator after its number."""
    formatted = []
    for line in source.splitlines():
        match = re.match(r"^(\d+)(.*)$", line)
        if not match:
            formatted.append(line)
            continue
        number, body = match.groups()
        formatted.append(f"{number} {body[1:] if body.startswith((' ', chr(9))) else body}")
    return "\n".join(formatted)


def basic_details(data: bytes) -> dict | None:
    try:
        from amiganut.basic import (
            AMIGABASIC_10,
            AMIGABASIC_12,
            EXTENDED_ESCAPE_BYTE,
            Verdict,
            detect,
            detokenise,
            scan_program,
        )
    except ImportError:
        return None
    detection = detect(data)
    if detection.verdict not in {Verdict.BASIC, Verdict.BASIC_TRAILING}:
        return None
    program_length = int(detection.program_length or len(data))
    program = data[:program_length]
    basic_v_lines = list(scan_program(program, dialect=AMIGABASIC_12))
    # Only the &FF bank is exclusive to 1.2. The &FE overflow bank is shared,
    # so its presence says nothing about which release wrote the program.
    uses_basic_v_escape = any(
        token.token == EXTENDED_ESCAPE_BYTE
        and token.value in AMIGABASIC_12.escape[EXTENDED_ESCAPE_BYTE].values()
        for line in basic_v_lines
        for token in line.tokens
    )
    dialect = AMIGABASIC_12 if uses_basic_v_escape else AMIGABASIC_10
    try:
        source = format_basic_listing(detokenise(program, dialect=dialect))
        lines = basic_v_lines if dialect is AMIGABASIC_12 else list(scan_program(program, dialect=dialect))
    except Exception:
        return None
    return {
        "source": source,
        "dialect": dialect.name,
        "lineCount": len(lines),
        "firstLine": lines[0].line_number if lines else None,
        "lastLine": lines[-1].line_number if lines else None,
        "trailingBytes": len(data) - program_length,
        "programLength": program_length,
        "compound": len(data) > program_length,
        # The tokenised prefix can be replaced independently while retaining a
        # known trailing payload byte-for-byte. AmigaBASIC 1.2 remains read-only until
        # a dialect-correct tokeniser is available.
        "editable": dialect is AMIGABASIC_10 and program_length <= 64 * 1024,
        "editNote": (
            f"The {len(data) - program_length:,}-byte trailing payload will be preserved unchanged."
            if len(data) > program_length else ""
        ),
    }


def script_details(data: bytes, path: str, printable_ratio: float) -> dict | None:
    """Recognise AmigaDOS or AmigaBASIC scripts without trusting only the name."""
    if not data or b"\0" in data or printable_ratio < 0.70:
        return None
    text = data.decode("latin-1", "replace").replace("\r\n", "\n").replace("\r", "\n")
    meaningful = [line for line in text.splitlines() if line.strip()]
    commands = []
    for line_number, line in enumerate(text.splitlines(), 1):
        match = SCRIPT_COMMAND_RE.match(line)
        if not match:
            continue
        shell_command, shell_arguments, basic_command, basic_arguments = match.groups()
        commands.append({
            "line": line_number,
            "action": (shell_command or basic_command or "").upper(),
            "arguments": (shell_arguments if shell_command else basic_arguments or "").strip(),
            "osCommand": bool(shell_command),
        })
    leaf = amiga_paths.leaf(path).casefold()
    named_script = leaf in SCRIPT_NAMES
    enough_commands = commands and len(commands) >= max(1, (len(meaningful) + 1) // 2)
    if not named_script and not enough_commands:
        return None
    return {
        "lineCount": len(text.splitlines()),
        "commandCount": len(commands),
        "commands": commands,
        "namedScript": named_script,
    }


def is_dms_container(data: bytes) -> bool:
    """Recognise a raw or gzip-compressed DiskMasher archive from its prefix."""
    if data.startswith(b"DMS!"):
        return True
    if not data.startswith(b"\x1f\x8b"):
        return False
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
            return compressed.read(4) == b"DMS!"
    except (gzip.BadGzipFile, EOFError, OSError):
        return False


def analyse_content(data: bytes, path: str) -> tuple[str, dict | None, dict | None, float]:
    """Classify complete, bounded file bytes using the editor's content rules."""
    if is_dms_container(data):
        return "container", None, None, 0.0
    basic = basic_details(data)
    printable = sum(value in (9, 10, 13) or 32 <= value < 127 for value in data)
    printable_ratio = printable / len(data) if data else 0.0
    script = None if basic else script_details(data, path, printable_ratio)
    kind = "basic" if basic else "script" if script else "text" if data and printable_ratio >= 0.82 else "binary"
    return kind, basic, script, printable_ratio


#: Workbench object types stored in a ``.info`` icon. Only a few of them say
#: anything about the file's content on their own.
WB_TOOL = 3
WB_PROJECT = 4
WB_KICK = 7


def metadata_kind(name: str, filetype: int | str | None) -> str | None:
    """Return a reliable kind that needs no content read, or None to sniff bytes.

    AmigaDOS keeps no type in the catalogue, so the only metadata available is
    the Workbench object type from the entry's icon. A Tool is an executable
    and a Kickstart icon marks a ROM image; a Project says only that some tool
    opens it, so those still have to be recognised from their contents.
    """
    try:
        value = int(str(filetype), 0) if filetype not in (None, "") else None
    except (TypeError, ValueError):
        value = None
    lowered = str(name or "").casefold()
    leaf = amiga_paths.leaf(lowered)
    if value in {WB_TOOL, WB_KICK}:
        return "binary"
    if lowered.endswith((".bas", ".basic", ".abas")):
        return "basic"
    if leaf in SCRIPT_NAMES or leaf.endswith(".script"):
        return "script"
    if (
        lowered.endswith((".txt", ".text", ".doc", ".guide", ".readme", ".md"))
        or leaf in {"readme", "read.me", "license", "copying", "install"}
    ):
        return "text"
    return None
