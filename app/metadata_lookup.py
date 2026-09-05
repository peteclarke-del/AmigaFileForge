"""Filename and online metadata discovery for imported Amiga software."""

from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from functools import lru_cache
from pathlib import Path


#: Hall of Light is the reference catalogue of Amiga software: title,
#: publisher and year for essentially everything released for the machine.
HALL_OF_LIGHT = "https://hol.abime.net"
ARCHIVE_ORG_SEARCH = "https://archive.org/advancedsearch.php"
ITCH_SEARCH = "https://itch.io/search"

_DISTRIBUTION_SUFFIXES = {
    ".zip", ".adf", ".adz", ".dms", ".hdf", ".adf", ".adz", ".dsk",
    ".adf", ".hda", ".dsk", ".hdz", ".hda", ".img", ".raw", ".hfe", ".scp",
}


def parse_distribution_filename(filename: str) -> dict:
    """Extract cautious TOSEC/Ghostware-style metadata from a host filename."""
    name = Path(str(filename or "").replace("\\", "/")).name
    stem = name
    while Path(stem).suffix.lower() in _DISTRIBUTION_SUFFIXES:
        stem = Path(stem).stem
    stem = re.sub(r"\s*\[[^\]]*]\s*$", "", stem).strip()
    groups = [value.strip() for value in re.findall(r"\(([^()]*)\)", stem)]
    title_part = re.split(r"\s*\(", stem, maxsplit=1)[0]
    title = re.sub(r"[_\s]+", " ", title_part).strip(" ._-")
    if re.fullmatch(r"ZZZ[-_ ]UNK.*", title, re.I):
        title = ""
    article = re.fullmatch(r"(.+),\s*(The|A|An)", title, re.I)
    if article:
        title = f"{article.group(2)} {article.group(1)}"

    date_index = next(
        (
            offset
            for offset, value in enumerate(groups)
            if re.fullmatch(
                r"(?:19|20)[0-9x]{2}(?:-[0-9x]{2}(?:-[0-9x]{2})?)?",
                value,
                re.I,
            )
        ),
        None,
    )
    date = groups[date_index] if date_index is not None else ""
    publisher = ""
    if date_index is not None and date_index + 1 < len(groups):
        candidate = groups[date_index + 1].strip()
        if candidate != "-" and not re.fullmatch(
            r"(?:UK|US|USA|EU|Europe|World|GB|DE|FR|ES|IT|JP|AU|"
            r"Amiga|Amiga 600|Master|A3000|A5000|Amiga 4000|RISC ?OS)",
            candidate,
            re.I,
        ):
            publisher = candidate

    if not date:
        loose = re.fullmatch(r"(.+?)\s+-\s+((?:19|20)\d{2})\s+-\s+(.+)", stem)
        if loose:
            title, date, publisher = (
                loose.group(1).strip(),
                loose.group(2),
                loose.group(3).strip(),
            )
    return {
        "title": title,
        "year": date,
        "publisher": publisher,
        "sourceFilename": name,
    }


def best_distribution_filename(filenames: list[str]) -> str:
    """Prefer the archive/member name carrying the richest usable metadata."""
    candidates = [str(name) for name in filenames if name]
    if not candidates:
        return ""
    parsed = [(name, parse_distribution_filename(name)) for name in candidates]
    return max(
        parsed,
        key=lambda item: (
            bool(item[1]["publisher"]),
            bool(item[1]["year"]),
            len(item[1]["title"]),
        ),
    )[0]


def enrich_from_distribution_filename(metadata: dict, filename: str) -> dict:
    """Apply distribution-name facts before an ambiguous online lookup."""
    parsed = parse_distribution_filename(filename)
    facts = []
    if parsed["title"]:
        metadata["title"] = parsed["title"]
        metadata["confidence"] = min(
            100, int(metadata.get("confidence", 0)) + 15
        )
        facts.append(f"title “{parsed['title']}”")
    if parsed["publisher"]:
        metadata["publisher"] = parsed["publisher"]
        metadata["confidence"] = min(
            100, int(metadata.get("confidence", 0)) + 10
        )
        facts.append(f"publisher “{parsed['publisher']}”")
    if parsed["year"]:
        metadata["year"] = parsed["year"]
        metadata["confidence"] = min(
            100, int(metadata.get("confidence", 0)) + 5
        )
        facts.append(f"date {parsed['year']}")
    if facts:
        metadata.setdefault("evidence", []).append(
            f"Distribution filename {parsed['sourceFilename']} supplied "
            + ", ".join(facts)
        )
        metadata["distributionFilename"] = parsed["sourceFilename"]
        metadata["ambiguous"] = (
            int(metadata.get("confidence", 0)) < 75
            or not metadata.get("filename")
        )
    return metadata


def _plain_text(source: str) -> str:
    source = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", source)
    source = re.sub(r"(?s)<[^>]+>", "\n", source)
    return re.sub(r"[ \t]+", " ", html.unescape(source))


def _lookup_hall_of_light(query: str, timeout: float) -> list[dict]:
    url = f"{HALL_OF_LIGHT}/hol_search.php?N_ref_search={urllib.parse.quote_plus(query)}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "AmigaFileForge/1.0 metadata lookup"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        source = response.read(1_000_000).decode("utf-8", "replace")
    links: list[tuple[str, str]] = []
    # Every result links to the game's own page, whose path is its record
    # number. The anchor text is the title as the catalogue spells it.
    for match in re.finditer(
        r'(?is)href=["\'](?:https?://hol\.abime\.net)?/(\d+)["\'][^>]*>(.*?)</a>',
        source,
    ):
        title = re.sub(r"<[^>]+>", "", html.unescape(match.group(2))).strip()
        item = (match.group(1), title)
        if title and item not in links:
            links.append(item)
    results = []
    for game_id, link_title in links[:5]:
        detail_url = f"{HALL_OF_LIGHT}/{game_id}"
        detail_request = urllib.request.Request(
            detail_url,
            headers={"User-Agent": "AmigaFileForge/1.0 metadata lookup"},
        )
        try:
            with urllib.request.urlopen(detail_request, timeout=timeout) as response:
                detail = _plain_text(
                    response.read(1_000_000).decode("utf-8", "replace")
                )
        except (OSError, urllib.error.URLError):
            detail = ""
        title_match = re.search(r"\bTitle\s*\n+\s*([^\n]+)", detail, re.I)
        # Hall of Light labels these "Publisher" and "Year of first release".
        publisher_match = re.search(r"\bPublishers?\s*\n+\s*([^\n]+)", detail, re.I)
        year_match = re.search(r"\bYear[^\n]*\s*\n+\s*(\d{4})", detail, re.I)
        results.append({
            "title": title_match.group(1).strip() if title_match else link_title,
            "publisher": publisher_match.group(1).strip() if publisher_match else "",
            "year": year_match.group(1) if year_match else "",
            "url": detail_url,
            "source": "Hall of Light",
        })
    return results


def _lookup_archive_org(query: str, timeout: float) -> list[dict]:
    params = urllib.parse.urlencode(
        {
            "q": f'title:"{query}" AND (amiga OR "commodore amiga" OR whdload)',
            "fl[]": ["identifier", "title", "creator", "date"],
            "rows": 5,
            "page": 1,
            "output": "json",
        },
        doseq=True,
    )
    request = urllib.request.Request(
        f"{ARCHIVE_ORG_SEARCH}?{params}",
        headers={"User-Agent": "AmigaFileForge/1.0 metadata lookup"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read(1_000_000).decode("utf-8", "replace"))
    return [
        {
            "title": str(item.get("title") or query),
            "publisher": str(item.get("creator") or ""),
            "year": str(item.get("date") or "")[:4],
            "url": f"https://archive.org/details/{urllib.parse.quote(str(item['identifier']))}",
            "source": "Internet Archive",
        }
        for item in payload.get("response", {}).get("docs", [])
        if item.get("identifier")
    ]


def _lookup_itch(query: str, timeout: float) -> list[dict]:
    url = f"{ITCH_SEARCH}?q={urllib.parse.quote_plus(query + ' amiga')}"
    request = urllib.request.Request(
        url, headers={"User-Agent": "AmigaFileForge/1.0 metadata lookup"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        source = response.read(1_000_000).decode("utf-8", "replace")
    results = []
    for match in re.finditer(r"(?is)<a\s+([^>]+)>(.*?)</a>", source):
        attributes = match.group(1)
        class_match = re.search(r'class=["\']([^"\']*)["\']', attributes, re.I)
        href_match = re.search(
            r'href=["\'](https://[^"\']+\.itch\.io/[^"\']+)["\']',
            attributes,
            re.I,
        )
        if not href_match or not class_match or "title" not in class_match.group(1).casefold():
            continue
        title = re.sub(r"<[^>]+>", "", html.unescape(match.group(2))).strip()
        if title:
            results.append({
                "title": title,
                "publisher": "",
                "year": "",
                "url": href_match.group(1),
                "source": "itch.io",
            })
        if len(results) == 5:
            break
    return results


@lru_cache(maxsize=512)
def lookup_online(query: str, timeout: float = 6.0) -> list[dict]:
    """Search specialist Amiga records, then broader archive catalogues."""
    query = re.sub(r"\s+", " ", query).strip()
    if len(query) < 2:
        return []
    results: list[dict] = []
    identities: set[tuple[str, str]] = set()
    for lookup in (_lookup_hall_of_light, _lookup_archive_org, _lookup_itch):
        try:
            found = lookup(query, timeout)
        except (OSError, ValueError, urllib.error.URLError, TimeoutError):
            continue
        for item in found:
            identity = (item["title"].casefold(), item["source"])
            if identity not in identities:
                identities.add(identity)
                results.append(item)
        if lookup is _lookup_hall_of_light and len(found) == 1:
            return results
    return results[:10]


def enrich_if_ambiguous(metadata: dict) -> dict:
    if not metadata["ambiguous"]:
        return metadata
    query = metadata["title"] or metadata["diskTitle"]
    leaf = str(query or "").rsplit(".", 1)[-1].strip()
    if re.fullmatch(
        r"(?:\d+|DISC-?\d+|DISK-?\d+|GAMES?\d+|DISCS?\d+)", leaf, re.I
    ):
        metadata["warnings"].append(
            "Online lookup was skipped because the generic directory name "
            "does not identify the software."
        )
        return metadata
    try:
        metadata["matches"] = lookup_online(query)
    except (OSError, urllib.error.URLError, TimeoutError) as exc:
        metadata["warnings"].append(f"Online lookup was unavailable: {exc}")
        return metadata
    if len(metadata["matches"]) == 1:
        match = metadata["matches"][0]
        metadata["title"] = match["title"]
        metadata["publisher"] = match["publisher"]
        metadata["sources"] = [{"label": match["source"], "url": match["url"]}]
        metadata["confidence"] = min(100, metadata["confidence"] + 15)
    elif metadata["matches"]:
        metadata["warnings"].append(
            "Several online matches were found; choose the correct one."
        )
    else:
        metadata["warnings"].append("No matching record was found online.")
    return metadata
