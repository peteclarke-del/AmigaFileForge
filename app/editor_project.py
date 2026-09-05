from __future__ import annotations

import re


EDITOR_PROJECT_FORMAT = "amiga-file-forge-editor-project-1"
REGION_KINDS = {"code", "text", "bytes", "words", "addresses", "bitmap"}


def editor_project_key(path: str, side: int | None) -> str:
    """Identify one editor annotation set by the view it was made in."""
    return f"{side if side is not None else '-'}|{path}"


def _number(value: object, default: int = 0) -> int:
    try:
        if isinstance(value, str):
            return int(value.strip(), 0)
        return int(value)
    except (TypeError, ValueError):
        return default


def normalise_editor_project(document: dict | None) -> dict:
    source = document if isinstance(document, dict) else {}
    symbols = {}
    for address, label in dict(source.get("symbols") or {}).items():
        numeric = _number(address, -1)
        clean = re.sub(r"[^A-Za-z0-9_.]", "_", str(label or "").strip())[:80]
        if numeric >= 0 and clean:
            symbols[str(numeric)] = clean
    regions = []
    for row in source.get("regions") or []:
        if not isinstance(row, dict):
            continue
        start, end = _number(row.get("start"), -1), _number(row.get("end"), -1)
        kind = str(row.get("kind") or "bytes").lower()
        if start < 0 or end <= start or kind not in REGION_KINDS:
            continue
        regions.append({
            "start": start,
            "end": end,
            "kind": kind,
            "name": str(row.get("name") or kind.title())[:120],
            "width": max(1, min(64, _number(row.get("width"), 8))),
        })
    bookmarks = []
    for row in source.get("bookmarks") or []:
        if not isinstance(row, dict):
            continue
        offset = _number(row.get("offset"), -1)
        if offset < 0:
            continue
        bookmarks.append({
            "offset": offset,
            "name": str(row.get("name") or f"Offset {offset}")[:120],
            "note": str(row.get("note") or "")[:2000],
        })
    comments = {}
    for offset, value in dict(source.get("comments") or {}).items():
        numeric = _number(offset, -1)
        text = str(value or "").strip()[:2000]
        if numeric >= 0 and text:
            comments[str(numeric)] = text
    history = [
        {
            "time": str(row.get("time") or "")[:40],
            "action": str(row.get("action") or "Editor change")[:160],
            "detail": str(row.get("detail") or "")[:1000],
        }
        for row in source.get("history") or []
        if isinstance(row, dict)
    ][-200:]
    tests = [row for row in source.get("tests") or [] if isinstance(row, dict)][-100:]
    return {
        "format": EDITOR_PROJECT_FORMAT,
        "notes": str(source.get("notes") or "")[:20000],
        "symbols": symbols,
        "regions": regions[-2048:],
        "bookmarks": bookmarks[-1024:],
        "comments": dict(list(comments.items())[-4096:]),
        "history": history,
        "tests": tests,
    }
