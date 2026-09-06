"""Conservative cheat-candidate analysis for BASIC and machine-code files."""

from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path
from . import amiga_paths


_GAMEPLAY_TERMS = {
    "lives": ("life", "lives", "men", "ship", "player", "death", "dead", "gameover"),
    "energy": ("energy", "health", "shield", "strength", "damage"),
    "ammo": ("ammo", "ammunition", "bullets", "bombs", "fuel", "weapons"),
    "time": ("time", "timer", "clock", "countdown"),
    "score": ("score", "points", "bonus", "highscore"),
    "collision": ("collision", "collide", "hit", "hurt", "invulnerable", "invincible"),
    "level": ("level", "stage", "round", "wave"),
    "inventory": ("inventory", "keys", "objects", "items", "money", "credits"),
}
_SEMANTIC_VARIABLE = re.compile(
    r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_]*(?:[$%&])?)(?![A-Za-z0-9_$%&])", re.I,
)
_BASIC_LINE = re.compile(r"^\s*(\d+)\s*(.*)$")
_ASSIGNMENT = re.compile(r"(?<![<>=])\b([A-Za-z][A-Za-z0-9_]*(?:[$%&])?)\s*=\s*([^:]+)", re.I)
_MEMORY_WRITE = re.compile(
    r"(?<![A-Za-z0-9_])((?:[A-Za-z][A-Za-z0-9_]*%?|&[0-9A-F]+|\d+)?\s*[?!]\s*"
    r"(?:[A-Za-z][A-Za-z0-9_]*%?|&[0-9A-F]+|\d+))\s*=\s*([^:]+)", re.I,
)
_CONDITION = re.compile(
    r"\bIF\s*([^:]+?)(?:\s+THEN\b|\s+GOTO\b|\s+GOSUB\b)", re.I,
)
_DIRECT_ADDRESS = re.compile(r"(?:^|[^A-Za-z0-9_])&([0-9A-F]{2,6})\b", re.I)
_SMALL_INTEGER = re.compile(r"^\s*(?:&([0-9A-F]+)|(\d+))\s*$", re.I)
_TRAINER_BYTES = {0xEA: "NOP", 0x60: "RTS", 0x4C: "JMP", 0x2C: "BIT"}
_REFERENCE_SOURCES = json.loads(
    Path(__file__).with_name("cheat_sources.json").read_text("utf-8")
)


def _operation(mnemonic: object) -> str:
    """Return an instruction's operation without its size suffix.

    Every 68000 mnemonic that matters here carries one: the decrement is
    ``SUBQ.B``, the store is ``MOVE.W``, the branch is ``BEQ.B``. Comparing
    the whole string against a set of bare operations never matches, which
    left this analysis silent on Amiga code entirely.
    """
    return str(mnemonic or "").upper().split(".", 1)[0]


def _category(value: str, fallback: str = "counter") -> str:
    folded = str(value or "").casefold()
    if re.search(r"\bgame[\s_-]*over\b", folded):
        return "lives"
    identifiers = re.findall(r"[a-z][a-z0-9]*", folded)
    for category, terms in _GAMEPLAY_TERMS.items():
        if any(identifier == term or identifier.startswith(term) or identifier.endswith(term)
               for identifier in identifiers for term in terms):
            return category
    return fallback


def _candidate(
    *, category: str, confidence: str, location: str, summary: str,
    evidence: str, suggestion: str, risk: str = "Review in an emulator before changing this code.",
    navigation: dict | None = None,
) -> dict:
    return {
        "category": category,
        "confidence": confidence,
        "location": location,
        "summary": summary,
        "evidence": evidence,
        "suggestion": suggestion,
        "risk": risk,
        "navigation": dict(navigation or {}),
    }


def analyse_basic(source: str) -> list[dict]:
    """Find gameplay-state and direct-memory evidence in an AmigaBASIC listing."""
    findings: list[dict] = []
    seen: set[tuple[str, str, str]] = set()
    lines = []
    variables: dict[str, dict] = {}
    for physical, raw_line in enumerate(str(source or "").splitlines(), 1):
        match = _BASIC_LINE.match(raw_line)
        line_number, code = (match.group(1), match.group(2)) if match else (str(physical), raw_line)
        lines.append((line_number, code))
        for assignment in _ASSIGNMENT.finditer(code):
            variable, expression = assignment.groups()
            prefix = code[:assignment.start()]
            statement_prefix = prefix.rsplit(":", 1)[-1].upper()
            if "IF" in statement_prefix and not re.search(r"\b(?:THEN|ELSE)\b", statement_prefix):
                continue
            key = variable.casefold()
            signal = variables.setdefault(key, {"name": variable, "category": _category(variable, "counter"), "semantic": bool(_category(variable, "")), "updates": [], "initial": [], "tests": []})
            reference = rf"(?<![A-Za-z0-9_]){re.escape(variable)}(?![A-Za-z0-9_$%&])"
            if re.search(rf"{reference}\s*-\s*(?:1|&1)\b", expression, re.I):
                signal["updates"].append((line_number, expression.strip(), "decrement"))
            elif re.search(rf"{reference}\s*\+\s*(?:1|&1)\b", expression, re.I):
                signal["updates"].append((line_number, expression.strip(), "increment"))
            else:
                numeric = _SMALL_INTEGER.match(expression)
                if numeric:
                    value = int(numeric.group(1), 16) if numeric.group(1) else int(numeric.group(2))
                    if 0 <= value <= 99:
                        signal["initial"].append((line_number, value))
        for condition in _CONDITION.finditer(code):
            expression = condition.group(1).strip()
            if not re.search(r"(?:=|<>|<=|>=|<|>)\s*(?:0|1|&0|&1)\b", expression, re.I):
                continue
            for variable in _SEMANTIC_VARIABLE.findall(expression):
                key = variable.casefold()
                signal = variables.setdefault(key, {"name": variable, "category": _category(variable, "counter"), "semantic": bool(_category(variable, "")), "updates": [], "initial": [], "tests": []})
                signal["tests"].append((line_number, expression))

    line_code = {int(number): code for number, code in lines if str(number).isdigit()}
    for signal in variables.values():
        terminal_contexts = []
        for line_number, expression in signal["tests"]:
            code = line_code.get(int(line_number), "")
            destination = re.search(r"\b(?:THEN|GOTO|GOSUB)\s*(\d+)\b", code, re.I)
            destination_code = line_code.get(int(destination.group(1)), "") if destination else ""
            terminal_category = _category(f"{code} {destination_code}", "")
            if terminal_category:
                terminal_contexts.append((line_number, destination.group(1) if destination else "", terminal_category))
        score = ((5 if signal["semantic"] else 0) + (3 if signal["updates"] else 0)
                 + (2 if signal["tests"] else 0) + (1 if signal["initial"] else 0)
                 + (4 if terminal_contexts else 0))
        if score < 7:
            continue
        locations = [int(item[0]) for group in (signal["initial"], signal["updates"], signal["tests"]) for item in group]
        evidence = []
        if signal["initial"]:
            evidence.append("initialised to " + ", ".join(str(item[1]) for item in signal["initial"][:3]))
        if signal["updates"]:
            evidence.append("updated at " + ", ".join(f"line {item[0]} ({item[2]})" for item in signal["updates"][:4]))
        if signal["tests"]:
            evidence.append("terminal test at " + ", ".join(f"line {item[0]}" for item in signal["tests"][:4]))
        if terminal_contexts:
            evidence.append("terminal path reaches " + ", ".join(
                f"line {item[1]} ({item[2]})" if item[1] else f"{item[2]} handling on line {item[0]}"
                for item in terminal_contexts[:3]
            ))
        findings.append(_candidate(
            category=signal["category"] if signal["category"] != "counter" else terminal_contexts[0][2],
            confidence="strong" if score >= 10 else "likely",
            location=f"BASIC lines {min(locations)}–{max(locations)}" if len(set(locations)) > 1 else f"BASIC line {locations[0]}",
            summary=f"Correlated gameplay counter {signal['name']}",
            evidence="; ".join(evidence),
            suggestion=f"Trace {signal['name']} through the listed update and terminal-test paths. Preventing only the loss path is safer than forcing every read.",
            navigation={"kind": "basic-line", "line": int((signal["updates"] or signal["tests"] or signal["initial"])[0][0])},
        ))

    for line_number, code in lines:
        location = f"BASIC line {line_number}"
        for write in _MEMORY_WRITE.finditer(code):
            target, value = write.groups()
            address = _DIRECT_ADDRESS.search(target)
            category = _category(code, "memory-write")
            confidence = "strong" if category != "memory-write" else "possible"
            key = (location, "memory-write", target.casefold())
            if key in seen:
                continue
            seen.add(key)
            numeric = _SMALL_INTEGER.match(value)
            numeric_value = (int(numeric.group(1), 16) if numeric and numeric.group(1) else int(numeric.group(2))) if numeric else None
            trainer_opcode = _TRAINER_BYTES.get(numeric_value)
            if not trainer_opcode and category == "memory-write":
                continue
            findings.append(_candidate(
                category="code-patch" if trainer_opcode else category,
                confidence="strong" if trainer_opcode else confidence,
                location=location,
                summary=(f"Possible trainer patch writes {trainer_opcode} to {target.strip()}" if trainer_opcode else f"Direct byte or word write to {target.strip()}"),
                evidence=f"The program writes {value.strip()} to {target.strip()}"
                         + (f" at &{address.group(1).upper()}" if address else ""),
                suggestion=("Compare the target bytes before and after the write. NOP, RTS, JMP and BIT are commonly used by trainers to bypass existing code." if trainer_opcode else "Trace this location while the relevant game value changes. A fixed value may expose a lives, energy, ammunition or timer store."),
                risk="The address may hold screen, sound, loader or operating-system state rather than a game variable.",
                navigation={"kind": "basic-line", "line": int(line_number)},
            ))
    return findings


def _operand_address(operand: str) -> str:
    value = str(operand or "")
    matches = [match for match in re.finditer(r"(?:&|\$)([0-9A-F]{2,8})\b", value, re.I)
               if match.start() == 0 or value[match.start() - 1] != "#"]
    return f"&{matches[-1].group(1).upper()}" if matches else ""


def _address_value(value: str) -> int | None:
    match = re.fullmatch(r"&([0-9A-F]+)", str(value or ""), re.I)
    return int(match.group(1), 16) if match else None


def _is_hardware_address(value: str) -> bool:
    address = _address_value(value)
    return address is not None and 0xFC00 <= address <= 0xFEFF


def _small_immediate(operand: str) -> int | None:
    match = re.search(r"#\s*([&$]?)([0-9A-F]+)\b", str(operand or ""), re.I)
    if not match:
        return None
    try:
        return int(match.group(2), 16 if match.group(1) else 10)
    except ValueError:
        return None


def _initialised_targets(rows: list[dict]) -> dict[str, list[tuple[int, int]]]:
    """Find constant-to-memory initialisation without pretending to emulate code."""
    loads = {"LDA", "LDX", "LDY", "LDR", "LDRB", "MOV", "MOVE", "MOVE.B", "MOVE.W", "MOVE.L"}
    stores = {"STA", "STX", "STY", "STR", "STRB", "MOVE", "MOVE.B", "MOVE.W", "MOVE.L"}
    result: dict[str, list[tuple[int, int]]] = {}
    for index, row in enumerate(rows):
        if _operation(row.get("mnemonic")) not in stores:
            continue
        target = _operand_address(str(row.get("operand") or ""))
        if not target or _is_hardware_address(target):
            continue
        preceding = rows[max(0, index - 3):index]
        control_flow = {"BEQ", "BNE", "BPL", "BMI", "BCC", "BCS", "BVC", "BVS", "BHI", "BLS", "BLE", "BLT", "BGE", "BGT", "JMP", "JSR", "RTS", "BRA", "BL", "BX", "BRK"}
        barrier = next((position for position, item in reversed(list(enumerate(preceding)))
                        if _operation(item.get("mnemonic")) in control_flow), None)
        if barrier is not None:
            preceding = preceding[barrier + 1:]
        loaded = next((item for item in reversed(preceding)
                       if _operation(item.get("mnemonic")) in loads
                       and _small_immediate(str(item.get("operand") or "")) is not None), None)
        if loaded is None:
            continue
        value = _small_immediate(str(loaded.get("operand") or ""))
        if value is not None and 0 <= value <= 255:
            result.setdefault(target, []).append((int(row.get("address") or 0), value))
    return result


def _branch_destination(row: dict | None) -> int | None:
    if not row:
        return None
    if row.get("target") is not None:
        try:
            return int(row["target"])
        except (TypeError, ValueError):
            pass
    address = _operand_address(str(row.get("operand") or ""))
    return _address_value(address)


def _context_near(rows: list[dict], index: int, radius: int = 4) -> str:
    nearby = rows[max(0, index - radius):index + radius + 1]
    return " ".join(str(item.get(key) or "") for item in nearby for key in ("label", "comment"))


def _branch_context(rows: list[dict], branch: dict | None) -> str:
    destination = _branch_destination(branch)
    if destination is None:
        return ""
    target_index = next((index for index, item in enumerate(rows) if int(item.get("address") or -1) == destination), None)
    return _context_near(rows, target_index, 3) if target_index is not None else ""


def analyse_disassembly(report: dict) -> list[dict]:
    """Find counter and state-transition evidence in a decoded instruction stream."""
    decoded = list(report.get("rows") or report.get("instructions") or [])
    # A linear disassembly also contains bytes decoded speculatively as code.
    # Once the disassembler has supplied reachability, those rows must not be
    # allowed to create candidates from graphics, compressed data or strings.
    rows = ([row for row in decoded if row.get("reachable")]
            if any("reachable" in row for row in decoded) else decoded)
    findings: list[dict] = []
    decrement = {"DEC", "DEA", "SBC", "SUB", "SUBQ", "SUBS"}
    comparison = {"CMP", "CMN", "TST", "BIT"}
    stores = {"STA", "STZ", "STR", "STRB", "MOVE", "MOVEQ"}
    branches = {"BEQ", "BNE", "BPL", "BMI", "BCC", "BCS", "BVC", "BVS", "BHI", "BLS", "BLE", "BLT", "BGE", "BGT", "CBZ", "CBNZ"}
    initialised = _initialised_targets(rows)
    for index, row in enumerate(rows):
        mnemonic = _operation(row.get("mnemonic"))
        operand = str(row.get("operand") or "")
        context = " ".join(str(row.get(key) or "") for key in ("label", "comment", "operand"))
        category = _category(context, "counter")
        address = int(row.get("address") or 0)
        location = f"&{address:X}"
        following = rows[index + 1:index + 4]
        nearby_branch = next((item for item in following if _operation(item.get("mnemonic")) in branches), None)
        target = _operand_address(operand)
        branch_destination = _branch_destination(nearby_branch)
        backward_branch = branch_destination is not None and branch_destination <= address
        branch_category = _category(_branch_context(rows, nearby_branch), "")
        loop_context = bool(re.search(r"\b(?:loop|copy|clear|scan|delay|row|column|byte)\b", context, re.I))
        structural_state = bool(target and nearby_branch and not backward_branch)
        corroborated_state = bool(structural_state and initialised.get(target))
        semantic_state = category != "counter" or bool(branch_category)
        if target and _is_hardware_address(target) and category == "counter":
            continue
        if mnemonic in decrement and (target or category != "counter") and (semantic_state or structural_state):
            if loop_context and not semantic_state:
                continue
            signals = []
            if target and initialised.get(target):
                signals.append("initialised to " + ", ".join(str(item[1]) for item in initialised[target][:3]))
            if nearby_branch:
                signals.append(f"followed by {nearby_branch.get('mnemonic')} {nearby_branch.get('operand', '')}")
            if branch_category:
                signals.append(f"branch destination is associated with {branch_category}")
            findings.append(_candidate(
                category=category if category != "counter" else branch_category or "game-state",
                confidence=("strong" if semantic_state and target in initialised
                            else "likely" if semantic_state or corroborated_state else "possible"),
                location=location,
                summary=f"{mnemonic} updates corroborated game-state storage" + (f" at {target}" if target else ""),
                evidence="; ".join([f"{mnemonic} {operand}", *signals]),
                suggestion="Watch the target while losing a life, taking damage or advancing the timer. If confirmed, patch the loss path rather than every use of the variable.",
                risk=("This is an unlabelled state transition inferred from reachable control flow. "
                      "It may still be an animation, delay or object counter; confirm it changes when the gameplay event occurs."
                      if not semantic_state and not corroborated_state else
                      "This passed several state checks, but only runtime behaviour can distinguish player state from another persistent counter."),
                navigation={"kind": "disassembly", "address": address, "offset": int(row.get("offset") or 0)},
            ))
        elif mnemonic in comparison and semantic_state and (target or re.search(r"[#&$](?:0+|1)\b", operand, re.I)) and nearby_branch:
            findings.append(_candidate(
                category=category,
                confidence="possible",
                location=location,
                summary="Comparison controls a nearby branch",
                evidence=f"{mnemonic} {operand}; {nearby_branch.get('mnemonic')} {nearby_branch.get('operand', '')}",
                suggestion="Trace both branch outcomes and identify whether one reaches death, damage, timeout or game-over handling.",
                navigation={"kind": "disassembly", "address": address, "offset": int(row.get("offset") or 0)},
            ))
        elif mnemonic in stores and category != "counter":
            findings.append(_candidate(
                category=category,
                confidence="likely",
                location=location,
                summary=f"Store is labelled or annotated as {category}",
                evidence=f"{mnemonic} {operand}; {context.strip()}",
                suggestion="Set a data watchpoint on the destination and compare writes during normal play and the relevant gameplay event.",
                navigation={"kind": "disassembly", "address": address, "offset": int(row.get("offset") or 0)},
            ))

        if mnemonic in {"SBC", "SUB", "SUBQ", "SUBS"} and _small_immediate(operand) == 1:
            before = rows[max(0, index - 4):index]
            after = rows[index + 1:index + 5]
            loaded = next((item for item in reversed(before) if _operation(item.get("mnemonic")) in {"LDA", "LDR", "LDRB", "MOVE"} and _operand_address(str(item.get("operand") or ""))), None)
            stored = next((item for item in after if _operation(item.get("mnemonic")) in stores and _operand_address(str(item.get("operand") or ""))), None)
            loaded_target = _operand_address(str((loaded or {}).get("operand") or ""))
            stored_target = _operand_address(str((stored or {}).get("operand") or ""))
            if loaded_target and stored_target and loaded_target == stored_target and not _is_hardware_address(stored_target):
                branch = next((item for item in after if _operation(item.get("mnemonic")) in branches), None)
                sequence_context = " ".join(str(item.get(key) or "") for item in before + after for key in ("label", "comment"))
                sequence_category = _category(f"{context} {sequence_context}", "counter")
                destination = _branch_destination(branch)
                backward = destination is not None and destination <= address
                destination_category = _category(_branch_context(rows, branch), "")
                semantic = sequence_category != "counter" or bool(destination_category)
                structural = bool(branch and not backward)
                corroborated = bool(initialised.get(stored_target) and structural)
                if not semantic and not structural:
                    continue
                findings.append(_candidate(
                    category=sequence_category if sequence_category != "counter" else destination_category or "game-state",
                    confidence=("strong" if semantic and initialised.get(stored_target)
                                else "likely" if semantic or corroborated else "possible"),
                    location=location,
                    summary=f"Load, subtract one and store sequence at {stored_target}",
                    evidence=f"{loaded.get('mnemonic')} {loaded.get('operand')}; {mnemonic} {operand}; {stored.get('mnemonic')} {stored.get('operand')}" + (f"; {branch.get('mnemonic')} {branch.get('operand', '')}" if branch else "") + (f"; initialised to {initialised[stored_target][0][1]}" if initialised.get(stored_target) else "") + (f"; destination associated with {destination_category}" if destination_category else ""),
                    suggestion="Watch this address during a loss, hit or timer tick. Preserve the flags expected by the following branch when testing any replacement.",
                    navigation={"kind": "disassembly", "address": address, "offset": int(row.get("offset") or 0)},
                ))
        if mnemonic in comparison and _small_immediate(operand) in {0, 1}:
            before = rows[max(0, index - 3):index]
            loaded = next((item for item in reversed(before) if _operation(item.get("mnemonic")) in {"LDA", "LDR", "LDRB", "MOVE"} and _operand_address(str(item.get("operand") or ""))), None)
            loaded_target = _operand_address(str((loaded or {}).get("operand") or ""))
            branch = next((item for item in following if _operation(item.get("mnemonic")) in branches), None)
            test_category = _category(f"{context} {_branch_context(rows, branch)}", "")
            if loaded_target and branch and not _is_hardware_address(loaded_target) and test_category:
                findings.append(_candidate(
                    category=test_category, confidence="likely", location=location,
                    summary=f"Loaded state at {loaded_target} is tested against {_small_immediate(operand)}",
                    evidence=f"{loaded.get('mnemonic')} {loaded.get('operand')}; {mnemonic} {operand}; {branch.get('mnemonic')} {branch.get('operand', '')}",
                    suggestion="Trace the branch target and fall-through path to identify which one handles death, timeout, damage or level completion.",
                    navigation={"kind": "disassembly", "address": address, "offset": int(row.get("offset") or 0)},
                ))
    unique = {}
    for item in findings:
        unique[(item["location"], item["summary"], item["evidence"])] = item
    return list(unique.values())


def disassembly_diagnostics(report: dict) -> list[dict]:
    """Explain why a static scan may legitimately find little or nothing."""
    rows = list(report.get("rows") or report.get("instructions") or [])
    if not rows:
        return []
    reachable = [row for row in rows if row.get("reachable")]
    diagnostics: list[dict] = []
    strings = []
    for item in report.get("strings") or []:
        value = item.get("text") if isinstance(item, dict) else item
        if value:
            strings.append(str(value))
    loader_commands = []
    for text in strings:
        loader_commands.extend(
            match.group(0).strip()
            for match in re.finditer(
                r"(?i)(?:^|[^A-Z])(?:\*?(?:LOAD|RUN|CHAIN|EXEC)\s*[^\r\n:]+|[LR]\.[A-Z0-9_!+.-]+(?:\s+[0-9A-F]+)?)",
                text,
            )
        )
    if loader_commands:
        diagnostics.append({
            "kind": "loader",
            "title": "This file appears to be a loader",
            "detail": (
                "It refers to " + ", ".join(list(dict.fromkeys(loader_commands))[:6])
                + ". Gameplay state may live in a loaded, relocated or unpacked companion rather than this file."
            ),
        })
    if len(reachable) <= max(2, len(rows) // 100):
        diagnostics.append({
            "kind": "packed",
            "title": "Most bytes are not statically reachable as code",
            "detail": (
                f"Only {len(reachable)} of {len(rows)} decoded rows are reachable from the recorded entry point. "
                "The file is probably data, compressed code, encrypted code or a runtime-generated payload. "
                "A memory snapshot after its loader has run is needed for reliable cheat discovery."
            ),
        })
    elif len(reachable) < len(rows) // 3:
        diagnostics.append({
            "kind": "mixed-code-data",
            "title": "The file mixes reachable code with a large data region",
            "detail": (
                f"{len(reachable)} of {len(rows)} decoded rows are reachable. Candidate scoring ignored the remainder "
                "so embedded graphics and packed data were not mistaken for 68000 instructions."
            ),
        })
    return diagnostics


def reference_searches(title: str, machine: str = "") -> list[dict]:
    """Return explicit browser searches; callers decide whether to open them."""
    query = " ".join(part for part in (str(title or "").strip(), str(machine or "").strip(), "cheat") if part)
    encoded = urllib.parse.quote_plus(query)
    return [
        {"name": str(source["name"]), "url": str(source["queryTemplate"]).replace("{query}", encoded)}
        for source in _REFERENCE_SOURCES
        if source.get("enabled", True) and source.get("name") and "{query}" in str(source.get("queryTemplate") or "")
    ]


def cheat_report(*, path: str, kind: str, findings: list[dict], title: str = "", machine: str = "", matches: list[dict] | None = None, diagnostics: list[dict] | None = None) -> dict:
    order = {"strong": 0, "likely": 1, "possible": 2}
    findings = sorted(findings, key=lambda item: (order.get(item["confidence"], 9), item["location"], item["category"]))
    return {
        "path": path,
        "kind": kind,
        "title": title,
        "machine": machine,
        "findings": findings,
        "counts": {level: sum(item["confidence"] == level for item in findings) for level in ("strong", "likely", "possible")},
        "identificationMatches": list(matches or []),
        "diagnostics": list(diagnostics or []),
        "referenceSearches": reference_searches(title or amiga_paths.leaf(path), machine),
        "readOnly": True,
        "warning": "Only findings with multiple structural signals or explicit gameplay semantics are shown. They are still static candidates, not proven cheats; test a checkpointed copy in the configured emulator before changing code.",
    }
