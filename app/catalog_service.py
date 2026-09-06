from __future__ import annotations

import copy
import html
import io
import json
import re
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .archive_utils import validated_zip_members
from .checksum import sha256_bytes
from .errors import DiskError
from .outbound import checked_url as outbound_checked_url, http_url as outbound_http_url


#: The media an Amiga catalogue result may offer for download. LHA and LZX
#: are the Amiga's own archivers, DMS is DiskMasher, and the rest are disk
#: images this workbench opens directly.
DOWNLOADABLE_MEDIA = re.compile(
    r"\.(?:zip|lha|lzx|dms|adf|adz|hdf|hda|hfe|ipf)(?:$|[?#])", re.I
)

DEFAULT_SOURCES = json.loads((Path(__file__).with_name("catalog_sources.json")).read_text("utf-8"))


@dataclass
class CachedPage:
    expires: float
    body: bytes


class CatalogueService:
    """Configurable, cached catalogue discovery with server-side download tokens."""

    @staticmethod
    def _http_url(
        url: object,
        message: str = "The catalogue supplied an invalid URL.",
    ) -> str:
        """Validate one outbound URL against the shared network policy."""
        return outbound_http_url(url, message)

    def __init__(self, work_dir: Path):
        work_path = Path(work_dir)
        self.config_path = work_path / "catalog-sources.json"
        self._item_dir = work_path / "catalog-items"
        self._item_dir.mkdir(parents=True, exist_ok=True)
        self._pages: dict[str, CachedPage] = {}
        self._items: dict[str, tuple[float, dict]] = {}
        self._catalogues: dict[str, tuple[float, list[dict]]] = {}

    def sources(self) -> list[dict]:
        if not self.config_path.exists():
            return copy.deepcopy(DEFAULT_SOURCES)
        try:
            rows = json.loads(self.config_path.read_text("utf-8"))
            if not isinstance(rows, list):
                raise ValueError
            defaults_by_id = {row["id"]: row for row in DEFAULT_SOURCES}
            migrated = []
            for row in rows:
                default = defaults_by_id.get(str(row.get("id") or "")) if isinstance(row, dict) else None
                if default and row.get("type") not in {"configured", "links"}:
                    replacement = copy.deepcopy(default)
                    for key in ("name", "url", "machines", "enabled", "direct"):
                        replacement[key] = row.get(key, replacement[key])
                    replacement["options"] = _merge_settings(default.get("options", {}), row.get("options", {}))
                    migrated.append(replacement)
                else:
                    migrated.append(row)
            cleaned = [self._clean_source(row) for row in migrated]
            by_id = {row["id"]: row for row in cleaned}
            for default in DEFAULT_SOURCES:
                existing = by_id.get(default["id"])
                if existing and existing["type"] == default["type"]:
                    existing["options"] = _merge_settings(default.get("options", {}), existing.get("options", {}))
                elif existing:
                    replacement = copy.deepcopy(default)
                    for key in ("name", "url", "machines", "enabled", "direct"):
                        replacement[key] = existing.get(key, replacement[key])
                    replacement["options"] = _merge_settings(default.get("options", {}), existing.get("options", {}))
                    by_id[default["id"]] = replacement
                else:
                    by_id.setdefault(default["id"], copy.deepcopy(default))
            return list(by_id.values())
        except (OSError, ValueError, json.JSONDecodeError):
            return copy.deepcopy(DEFAULT_SOURCES)

    def save_sources(self, rows: list[dict]) -> list[dict]:
        if not isinstance(rows, list) or len(rows) > 30:
            raise DiskError("Online sources must be a list containing at most 30 sites.")
        cleaned = [self._clean_source(row) for row in rows]
        temporary = self.config_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(cleaned, indent=2) + "\n", "utf-8")
        temporary.replace(self.config_path)
        self._pages.clear()
        return cleaned

    @staticmethod
    def _clean_source(row: dict) -> dict:
        if not isinstance(row, dict):
            raise DiskError("Each online source must be an object.")
        source_id = re.sub(r"[^a-z0-9_-]", "-", str(row.get("id") or row.get("name") or "source").lower())[:40]
        url = CatalogueService._http_url(
            row.get("url"),
            f"{row.get('name') or source_id} needs an HTTP or HTTPS URL.",
        )
        source_type = str(row.get("type") or "links")
        if source_type not in {"configured", "links"}:
            raise DiskError(f"Unsupported online source parser: {source_type}")
        return {
            "id": source_id or "source", "name": str(row.get("name") or source_id)[:100],
            "type": source_type, "url": url, "machines": [str(item)[:30] for item in row.get("machines", [])][:12],
            "enabled": bool(row.get("enabled", True)), "direct": bool(row.get("direct", source_type != "links")),
            "options": dict(row.get("options") or {}) if isinstance(row.get("options") or {}, dict) else {},
        }

    def _fetch(
        self,
        url: str,
        *,
        limit: int = 32 * 1024 * 1024,
        ttl: int = 900,
        form: dict[str, str] | None = None,
    ) -> bytes:
        """Read a catalogue page, optionally by submitting its search form.

        Some archives only search through a POST form: OS4Depot's index takes
        an ``f_fields`` field and ignores anything in the query string. A
        posted request is cached under a key that includes the fields, so two
        different searches are not served each other's results.
        """
        url = self._http_url(url)
        key = url if not form else f"{url}#" + urllib.parse.urlencode(sorted(form.items()))
        cached = self._pages.get(key)
        if cached and cached.expires > time.time():
            return cached.body
        url = outbound_checked_url(url)
        headers = {"User-Agent": "AmigaFileForge/1.0 (+local archival tool)", "Accept-Encoding": "identity"}
        payload = None
        if form:
            payload = urllib.parse.urlencode(form).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = urllib.request.Request(url, data=payload, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                length = int(response.headers.get("Content-Length") or 0)
                if length > limit:
                    raise DiskError(f"The remote file is larger than the {limit // (1024 * 1024)} MB safety limit.")
                body = response.read(limit + 1)
        except DiskError:
            raise
        except Exception as exc:
            raise DiskError(f"Could not contact {urllib.parse.urlparse(url).netloc}: {exc}") from exc
        if len(body) > limit:
            raise DiskError(f"The remote file is larger than the {limit // (1024 * 1024)} MB safety limit.")
        self._pages[key] = CachedPage(time.time() + ttl, body)
        return body

    def search(self, query: str, machine: str, source_ids: set[str] | None = None) -> tuple[list[dict], list[dict]]:
        results, failures, _continuation = self.search_page(query, machine, source_ids)
        return results, failures

    def search_page(
        self,
        query: str,
        machine: str,
        source_ids: set[str] | None = None,
        cursors: dict[str, int] | None = None,
    ) -> tuple[list[dict], list[dict], dict[str, int]]:
        query = query.strip().casefold()
        results, failures = [], []
        continuing = cursors is not None
        cursors = cursors or {}
        sources = [
            source for source in self.sources()
            if source["enabled"]
            and (not source_ids or source["id"] in source_ids)
            and (not machine or machine == "all" or machine in source["machines"] or "all" in source["machines"])
            and (not continuing or source["id"] in cursors)
        ]

        def load(source):
            try:
                return source, self._load_catalogue(source, query, machine), None
            except (DiskError, OSError, ValueError) as exc:
                return source, [], str(exc)

        with ThreadPoolExecutor(max_workers=min(6, len(sources) or 1)) as pool:
            loaded = list(pool.map(load, sources))
        continuation = {}
        for source, rows, error in loaded:
            if error:
                failures.append({"source": source["name"], "error": error})
                continue
            candidates = []
            for row in rows:
                haystack = " ".join(str(row.get(key, "")) for key in ("title", "publisher", "description", "year")).casefold()
                if query and query not in haystack:
                    continue
                if not row.get("downloadable"):
                    continue
                candidates.append(row)
            if any(row.get("resolver") for row in candidates):
                # Some indexes contain thousands of records but do not promise
                # downloadable media. Resolve one bounded window and return a
                # source-specific cursor so the browser can safely request the
                # rest without treating unchecked rows as downloadable.
                limit = max(1, int(source["options"].get("resultValidationLimit", 120)))
                start = max(0, int(cursors.get(source["id"], 0)))
                end = min(len(candidates), start + limit)
                if end < len(candidates):
                    continuation[source["id"]] = end
                candidates = candidates[start:end]
                threads = max(1, min(16, int(source["options"].get("detailThreads", 8))))
                with ThreadPoolExecutor(max_workers=min(threads, len(candidates) or 1)) as pool:
                    candidates = [
                        row for row in pool.map(lambda item: self._resolve_row(item) if item.get("resolver") else item, candidates)
                        if row
                    ]
            for row in candidates:
                row.update(sourceId=source["id"], sourceName=source["name"], machines=row.get("machines") or source["machines"])
                token = sha256_bytes(
                    f"{source['id']}\0{row.get('downloadUrl')}\0{row.get('pageUrl')}\0{row.get('title')}".encode()
                )[:32]
                row["id"] = token
                self._remember_item(token, row)
                row.pop("downloadUrl", None)
                results.append(row)
        results.sort(key=lambda item: (str(item.get("title", "")).casefold(), str(item.get("publisher", "")).casefold()))
        return results[:1000], failures, continuation

    def _load_catalogue(self, source: dict, query: str, machine: str) -> list[dict]:
        options = source.get("options", {})
        loader_name = str(options.get("loader") or "page")
        loaders = {
            "page": self._load_page,
            "form-post": self._load_form_post,
            "category-crawl": self._load_category_crawl,
            "machine-index": self._load_machine_index,
        }
        loader = loaders.get(loader_name)
        if loader is None:
            raise DiskError(f"Unsupported catalogue loading strategy: {loader_name}")
        return loader(source, query, machine)

    def _load_page(self, source: dict, query: str, machine: str) -> list[dict]:
        options = source.get("options", {})
        url = source["url"]
        template = str(options.get("queryTemplate") or "")
        machine_queries = options.get("machineQueries", {})
        machine_query = str(machine_queries.get(machine, machine_queries.get("all", ""))) if isinstance(machine_queries, dict) else ""
        if template and (query or machine_query):
            relative = template.replace("{query}", urllib.parse.quote_plus(query)).replace("{machineQuery}", urllib.parse.quote_plus(machine_query))
            url = urllib.parse.urljoin(url, relative)
        body = self._fetch(
            url,
            limit=max(1, int(options.get("pageLimitMb", 8))) * 1024 * 1024,
            ttl=max(60, int(options.get("cacheSeconds", 900))),
        ).decode(str(options.get("encoding") or "utf-8"), "replace")
        return self._parse_rows(source, body, str(options.get("parser") or "links"), {"url": url, "machine": machine})

    def _load_form_post(self, source: dict, query: str, machine: str) -> list[dict]:
        """Search an archive whose only search is a POST form.

        ``formFields`` names the fields and their values, with ``{query}``
        standing for the search text. OS4Depot is the reason this exists: its
        index accepts nothing in the query string and answers only a posted
        ``f_fields``.
        """
        options = source.get("options", {})
        url = urllib.parse.urljoin(source["url"], str(options.get("queryTemplate") or ""))
        fields = options.get("formFields") or {}
        if not isinstance(fields, dict) or not fields:
            raise DiskError(f"{source.get('name') or source['id']} has no search form fields configured.")
        form = {str(name): str(value).replace("{query}", query) for name, value in fields.items()}
        body = self._fetch(
            url,
            limit=max(1, int(options.get("pageLimitMb", 8))) * 1024 * 1024,
            ttl=max(60, int(options.get("cacheSeconds", 900))),
            form=form,
        ).decode(str(options.get("encoding") or "utf-8"), "replace")
        return self._parse_rows(source, body, str(options.get("parser") or "links"), {"url": url, "machine": machine})

    def _parse_rows(self, source: dict, body: str, parser_name: str, context: dict | None = None) -> list[dict]:
        parsers = {
            "thumbnail-cards": self._parse_thumbnail_cards,
            "section-catalogue": self._parse_section_catalogue,
            "function-calls": self._parse_function_calls,
            "item-rows": self._parse_item_rows,
            "zip-links": self._parse_zip_links,
            "package-paragraphs": self._parse_package_paragraphs,
            "query-media-tiles": self._parse_query_media_tiles,
            "html-cards": self._parse_html_cards,
            "links": self._parse_links,
        }
        parser = parsers.get(parser_name)
        if parser is None:
            raise DiskError(f"Unsupported catalogue parser: {parser_name}")
        return parser(source, body, context or {})

    def _resolve_row(self, row: dict) -> dict | None:
        try:
            body = self._fetch(row["pageUrl"], limit=2 * 1024 * 1024, ttl=86400).decode("utf-8", "replace")
        except DiskError:
            return None
        resolver = str(row.get("resolver") or "media-links")
        if resolver == "upload-buttons":
            return self._resolve_upload_buttons(row, body)
        if resolver != "media-links":
            return None
        choices = [
            urllib.parse.urljoin(row["pageUrl"], href)
            for href in re.findall(r'href\s*=\s*["\']([^"\']+)', body, re.I)
            if DOWNLOADABLE_MEDIA.search(href)
            and str(row.get("resolverOptions", {}).get("downloadPathContains") or "/download/").casefold()
            in urllib.parse.urlparse(urllib.parse.urljoin(row["pageUrl"], href)).path.casefold()
        ]
        if not choices:
            return None
        resolved = dict(row)
        resolved["downloadUrl"] = choices[0]
        resolved["downloadChoices"] = list(dict.fromkeys(choices))
        resolved["artifactType"] = "disk-image"
        resolved["description"] = resolved["description"].replace(" Download availability is checked when installed.", "")
        return resolved

    @staticmethod
    def _resolve_upload_buttons(row: dict, body: str) -> dict | None:
        options = row.get("resolverOptions", {})
        extensions = tuple(
            str(value).lower().lstrip(".")
            for value in options.get("mediaExtensions", ["adf", "adz", "dms", "adf", "hfe"])
        )
        archive_terms = [str(value).casefold() for value in options.get("archiveTerms", [])]
        requests = []
        for block in re.findall(r'<div[^>]+class=["\'][^"\']*\bupload\b[^"\']*["\'][^>]*>(.*?)(?=<div[^>]+class=["\'][^"\']*\bupload\b|</div>\s*</div>)', body, re.I | re.S):
            upload_id = re.search(r'data-upload_id=["\'](\d+)["\']', block, re.I)
            filename_match = re.search(r'<strong[^>]+(?:title|data-name)=["\']([^"\']+)["\']', block, re.I)
            if not upload_id or not filename_match:
                continue
            filename = html.unescape(filename_match.group(1)).strip()
            suffix = Path(filename).suffix.lower().lstrip(".")
            if suffix not in extensions and suffix != "zip":
                continue
            evidence = f"{row.get('title', '')} {row.get('description', '')} {_plain_text(body)} {filename}".casefold()
            if suffix == "zip" and archive_terms and not any(term in evidence for term in archive_terms):
                continue
            template = str(options.get("requestTemplate") or "{pageUrl}/file/{uploadId}?source=view_game&as_props=1")
            request_url = template.replace("{pageUrl}", str(row["pageUrl"]).rstrip("/")).replace("{uploadId}", upload_id.group(1))
            requests.append({"url": request_url, "filename": filename})
        if not requests:
            return None
        resolved = dict(row)
        resolved["downloadRequests"] = requests
        resolved["artifactType"] = "disk-image"
        resolved["description"] = resolved["description"].replace(" Download availability is checked when installed.", "")
        return resolved

    def _load_category_crawl(self, source: dict, _query: str, _machine: str) -> list[dict]:
        cached = self._catalogues.get(source["id"])
        if cached and cached[0] > time.time():
            return [dict(row) for row in cached[1]]
        options = source["options"]
        cache_seconds = max(60, int(options.get("cacheSeconds", 86400)))
        categories = [dict(category) for category in options.get("categories", []) if isinstance(category, dict)]

        def category_pages(category):
            label = str(category.get("name") or "Amiga 600 software")
            root_url = urllib.parse.urljoin(source["url"], str(category.get("url") or ""))
            required_path = str(category.get("childPath") or "")
            if not required_path:
                return [(label, root_url, str(category.get("parser") or options.get("parser") or "links"))]
            try:
                body = self._fetch(root_url, limit=2 * 1024 * 1024, ttl=cache_seconds).decode("latin-1", "replace")
            except DiskError:
                return []
            links = {
                urllib.parse.urljoin(root_url, href)
                for href in re.findall(r'href\s*=\s*["\']([^"\']+)', body, re.I)
                if required_path in urllib.parse.urljoin(root_url, href)
                and urllib.parse.urlparse(urllib.parse.urljoin(root_url, href)).path.lower().endswith(".html")
            }
            return [(label, url, str(category.get("parser") or options.get("parser") or "links")) for url in links]

        pages = []
        with ThreadPoolExecutor(max_workers=4) as pool:
            for found in pool.map(category_pages, categories):
                pages.extend(found)

        def parse_page(page):
            category, url, parser = page
            try:
                body = self._fetch(url, limit=2 * 1024 * 1024, ttl=cache_seconds).decode("latin-1", "replace")
            except DiskError:
                return []
            return self._parse_rows(source, body, parser, {"url": url, "category": category})

        rows = []
        crawl_threads = max(1, min(16, int(options.get("crawlThreads", 8))))
        with ThreadPoolExecutor(max_workers=crawl_threads) as pool:
            for found in pool.map(parse_page, sorted(set(pages))):
                rows.extend(found)
        unique = {}
        for row in rows:
            key = (row["pageUrl"].casefold(), row["title"].casefold())
            unique[key] = row
        catalogue = list(unique.values())
        self._catalogues[source["id"]] = (time.time() + cache_seconds, catalogue)
        return [dict(row) for row in catalogue]

    @staticmethod
    def _parse_section_catalogue(source: dict, body: str, context: dict) -> list[dict]:
        url = str(context.get("url") or source["url"])
        category = str(context.get("category") or source["name"])
        publisher_match = re.search(r'id\s*=\s*["\']cat_table_title_bar["\'][^>]*>(.*?)</h6>', body, re.I | re.S)
        publisher = _plain_text(publisher_match.group(1)) if publisher_match else str(source.get("name") or "")
        rows = []
        sections = re.findall(r'<section\s+id\s*=\s*["\']([^"\']+)["\'][^>]*>(.*?)</section>', body, re.I | re.S)
        for section_id, block in sections:
            title_match = re.search(r'<b>(.*?)</b>', block, re.I | re.S)
            if not title_match:
                continue
            title = _plain_text(title_match.group(1))
            if not title:
                continue
            date_match = re.search(r'Release Date:\s*<br\s*/?>\s*([^<]+)', block, re.I)
            year_match = re.search(r'\b(19\d{2}|20\d{2})\b', date_match.group(1) if date_match else "")
            compatibility = re.search(r'Stated Compatibility:\s*<br\s*/?>\s*(.*?)\s*<br', block, re.I | re.S)
            compatibility_text = _plain_text(compatibility.group(1)) if compatibility else ""
            media = [
                urllib.parse.urljoin(url, href)
                for href in re.findall(r'href\s*=\s*["\']([^"\']+)', block, re.I)
                if DOWNLOADABLE_MEDIA.search(href)
            ]
            download = media[0] if media else None
            description = category
            if compatibility_text:
                description += f". {compatibility_text}"
            rows.append(_item(
                title, publisher, year_match.group(1) if year_match else "", download,
                f"{url}#{urllib.parse.quote(section_id)}", "disk-image" if download else "external",
                description=description,
                machines=_machines_from_compatibility(compatibility_text, source.get("machines", [])),
                downloadable=bool(download),
            ))
        if rows:
            return rows
        downloads = [
            urllib.parse.urljoin(url, href)
            for href in re.findall(r'href\s*=\s*["\']([^"\']+)', body, re.I)
            if DOWNLOADABLE_MEDIA.search(href)
        ]
        if not downloads:
            return []
        title_match = re.search(r'<title>(.*?)</title>', body, re.I | re.S)
        title = _plain_text(title_match.group(1)).split(" - ")[0] if title_match else Path(urllib.parse.urlparse(url).path).stem
        return [_item(title, publisher, "", downloads[0], url, "disk-image", description=category, machines=["a600"])]

    def _load_machine_index(self, source: dict, _query: str, machine: str) -> list[dict]:
        options = source["options"]
        profiles = options.get("machineProfiles", {})
        selected_profile_items = list(profiles.items()) if machine == "all" else [(machine, profiles.get(machine, {}))]
        machine_ids = list(dict.fromkeys(
            int(machine_id)
            for _machine_name, profile in selected_profile_items if isinstance(profile, dict)
            for machine_id in profile.get("ids", [])
        ))
        if not machine_ids:
            return []
        profile_by_id = {
            int(machine_id): {"label": str(profile.get("label") or machine_name), "machines": [machine_name]}
            for machine_name, profile in selected_profile_items if isinstance(profile, dict)
            for machine_id in profile.get("ids", [])
        }
        cache_key = f"{source['id']}:{','.join(map(str, machine_ids))}"
        cached = self._catalogues.get(cache_key)
        if cached and cached[0] > time.time():
            return [dict(row) for row in cached[1]]

        def load_machine(machine_id):
            template = str(options.get("landingTemplate") or "")
            url = urllib.parse.urljoin(source["url"], template.replace("{machineId}", str(machine_id)))
            cache_seconds = max(60, int(options.get("cacheSeconds", 86400)))
            body = self._fetch(url, limit=8 * 1024 * 1024, ttl=cache_seconds).decode("utf-8", "replace")
            return self._parse_rows(
                source,
                body,
                str(options.get("parser") or "links"),
                {"url": url, "profile": profile_by_id.get(machine_id, {})},
            )

        rows = []
        with ThreadPoolExecutor(max_workers=min(4, len(machine_ids))) as pool:
            for found in pool.map(load_machine, machine_ids):
                rows.extend(found)
        unique = {(row["pageUrl"], row["title"]): row for row in rows}
        catalogue = list(unique.values())
        self._catalogues[cache_key] = (time.time() + max(60, int(options.get("cacheSeconds", 86400))), catalogue)
        return [dict(row) for row in catalogue]

    @staticmethod
    def _parse_item_rows(source: dict, body: str, context: dict) -> list[dict]:
        profile = context.get("profile", {})
        rows = []
        item_path = str(source.get("options", {}).get("itemPathPrefix") or "/litem/")
        pattern = re.compile(
            r'<a\s+href\s*=\s*["\'](' + re.escape(item_path) + r'[^"\']+)["\'][^>]*>(.*?)</a>(.*?)</td>',
            re.I | re.S,
        )
        for href, label, tail in pattern.findall(body):
            title = _plain_text(label)
            if not title:
                continue
            details = _plain_text(tail)
            groups = re.findall(r'\(([^()]*)\)', details)
            publisher = groups[1] if len(groups) > 1 else ""
            year = next(iter(re.findall(r'\b(?:19|20)\d{2}\b', details)), "")
            compatibility = groups[-1].split(",", 1)[0].strip() if groups else ""
            page_url = urllib.parse.urljoin(source["url"], href)
            rows.append(_item(
                title, publisher, year, None, page_url, "remote-item",
                description=f"{profile.get('label', 'Amiga software')}. Download availability is checked when installed.",
                machines=_machines_from_compatibility(compatibility, profile.get("machines", [])), downloadable=True,
                resolver=str(source.get("options", {}).get("resolver") or "media-links"),
            ))
            rows[-1]["resolverOptions"] = {"downloadPathContains": source["options"].get("downloadPathContains", "/download/")}
        return rows

    @staticmethod
    def _parse_thumbnail_cards(source: dict, body: str, _context: dict) -> list[dict]:
        rows = []
        pattern = re.compile(r'<div class="thumbnail[^>]*>(.*?)</div>\s*</div>', re.I | re.S)
        for block in pattern.findall(body):
            title = re.search(r'class="row-title".*?<a[^>]*>([^<]+)</a>', block, re.I | re.S)
            download = next(
                (
                    href for href in re.findall(r'href="([^"]+)"', block, re.I)
                    if DOWNLOADABLE_MEDIA.search(href)
                ),
                None,
            )
            if not title or not download:
                continue
            publisher = re.search(r'class="row-pub"[^>]*>.*?<a[^>]*>([^<]+)', block, re.I | re.S)
            year = re.search(r'class="row-dt"[^>]*>.*?<a[^>]*>([^<]+)', block, re.I | re.S)
            page = re.search(r'href="(game\.php\?id=\d+)"', block, re.I)
            rows.append(_item(title.group(1), publisher.group(1) if publisher else "", year.group(1) if year else "", urllib.parse.urljoin(source["url"], download), urllib.parse.urljoin(source["url"], page.group(1)) if page else source["url"], "disk-image"))
        return rows

    @staticmethod
    def _parse_function_calls(source: dict, body: str, context: dict) -> list[dict]:
        rows = []
        options = source.get("options", {})
        call_name = re.escape(str(options.get("callName") or "record"))
        fields = options.get("callFields", {})
        for call in re.findall(call_name + r'\((.*?)\);', body, re.I | re.S):
            values = [html.unescape(single or double) for double, single in re.findall(r'"((?:\\.|[^"])*)"|\'((?:\\.|[^\'])*)\'', call)]
            indexes = [int(fields.get(key, default)) for key, default in (("publisher", 0), ("stem", 3), ("title", 4), ("description", 7))]
            if len(values) <= max(indexes):
                continue
            publisher, stem, title, summary = (values[index] for index in indexes)
            template = str(options.get("downloadTemplate") or "")
            download = template.replace("{publisher}", urllib.parse.quote(publisher)).replace("{stem}", urllib.parse.quote(stem)) if template else None
            description = f"{context.get('category', '')}. {summary}".strip(". ")
            rows.append(_item(title, publisher.replace("_", " "), "", download, str(context.get("url") or source["url"]), "disk-image", description=description, machines=source.get("machines", [])))
        return rows

    @staticmethod
    def _parse_zip_links(source: dict, body: str, _context: dict) -> list[dict]:
        pattern = re.compile(
            r'<a\s+href=["\']?([^"\'> ]+\.(?:zip|lha|lzx|dms|adf|adz|hdf|hda|hfe|ipf))["\']?[^>]*>([^<]+)</a>'
            r'\s*(?:<b>([^<]*)</b>)?',
            re.I,
        )
        publisher = str(source.get("options", {}).get("defaultPublisher") or source.get("name") or "")
        options = source.get("options", {})
        # Some archives name the file in a query parameter and link the actual
        # download from a description page, usually behind an icon with no text
        # for this pattern to match. Such a source sets rowResolver, and the
        # row is carried as a page for the resolver to follow rather than as a
        # download address that would not fetch anything.
        resolver = str(options.get("rowResolver") or "") or None
        # The resolver needs to know which links on the page are the download.
        # A default of "/download/" suits sites that use that path; OS4Depot
        # serves its files from /share/, so the source says so.
        resolver_options = options.get("resolverOptions") if isinstance(options.get("resolverOptions"), dict) else {}
        rows, seen = [], set()
        for url, code, title in pattern.findall(body):
            # An href is HTML, so &amp; in a query string is an escaped
            # ampersand rather than part of the address.
            resolved = urllib.parse.urljoin(source["url"], html.unescape(url))
            if resolved in seen:
                continue
            seen.add(resolved)
            entry = _item(
                (title or code).strip(), publisher, "",
                None if resolver else resolved,
                resolved if resolver else source["url"],
                "disk-image",
                description=code.strip(),
                resolver=resolver,
            )
            if resolver and resolver_options:
                entry["resolverOptions"] = dict(resolver_options)
            rows.append(entry)
        return rows

    @staticmethod
    def _parse_package_paragraphs(source: dict, body: str, _context: dict) -> list[dict]:
        rows = []
        for paragraph in re.split(r"\r?\n\r?\n", body):
            fields = {}
            for line in paragraph.splitlines():
                if ": " in line:
                    key, value = line.split(": ", 1); fields[key] = value
            if not fields.get("Package") or not fields.get("URL"):
                continue
            rows.append(_item(fields.get("Package"), fields.get("Maintainer", ""), "", urllib.parse.urljoin(source["url"], fields["URL"]), source["url"], "amigaos-package", description=fields.get("Description", ""), machines=source.get("machines", []), version=fields.get("Version", "")))
        return rows

    @staticmethod
    def _parse_query_media_tiles(source: dict, body: str, context: dict) -> list[dict]:
        options = source.get("options", {})
        parameter = str(options.get("mediaQueryParameter") or "disk0")
        publisher = str(options.get("defaultPublisher") or source.get("name") or "")
        page_url = str(context.get("url") or source["url"])
        rows = []
        for block in re.findall(r'<td\b[^>]*>(.*?)</td>', body, re.I | re.S):
            links = re.findall(r'href=["\']([^"\']+)["\']', block, re.I)
            media = None
            for link in links:
                values = urllib.parse.parse_qs(urllib.parse.urlparse(html.unescape(link)).query).get(parameter, [])
                if values and DOWNLOADABLE_MEDIA.search(values[0]):
                    media = values[0]
                    break
            if not media:
                continue
            title_match = re.search(r'</a>\s*<br\s*/?>\s*(.*?)\s*<br\s*/?>', block, re.I | re.S)
            title = _plain_text(title_match.group(1)) if title_match else Path(urllib.parse.urlparse(media).path).stem
            page = next((urllib.parse.urljoin(page_url, link) for link in links if parameter not in link), page_url)
            rows.append(_item(title, publisher, "", media, page, "disk-image", machines=source.get("machines", [])))
        return rows

    @staticmethod
    def _parse_html_cards(source: dict, body: str, context: dict) -> list[dict]:
        options = source.get("options", {})
        card_class = re.escape(str(options.get("cardClass") or "game_cell"))
        starts = list(re.finditer(r'<div[^>]+class=["\'][^"\']*\b' + card_class + r'\b[^"\']*["\'][^>]*>', body, re.I))
        rows = []
        for number, start in enumerate(starts):
            block = body[start.start() : starts[number + 1].start() if number + 1 < len(starts) else len(body)]
            title_block = _html_class_content(block, str(options.get("titleClass") or "game_title"))
            title_match = re.search(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', title_block, re.I | re.S)
            if not title_match:
                continue
            title = _plain_text(title_match.group(2))
            page = urllib.parse.urljoin(str(context.get("url") or source["url"]), html.unescape(title_match.group(1)))
            description = _plain_text(_html_class_content(block, str(options.get("descriptionClass") or "game_text")))
            publisher = _plain_text(_html_class_content(block, str(options.get("publisherClass") or "game_author")))
            rows.append(_item(
                title, publisher, "", None, page, "remote-item",
                description=f"{description} Download availability is checked when installed.".strip(),
                machines=[str(context["machine"])] if context.get("machine") and context["machine"] != "all" else source.get("machines", []), downloadable=True,
                resolver=str(options.get("resolver") or "media-links"),
            ))
            rows[-1]["resolverOptions"] = dict(options.get("resolverOptions") or {})
        return rows

    @staticmethod
    def _parse_links(source: dict, body: str, _context: dict) -> list[dict]:
        """Collect the result links on a catalogue's search page.

        A site writes its own results as relative hrefs -- Lemon Amiga links a
        game as ``/game/defender`` -- so matching only ``https://`` finds the
        outbound links in the page furniture and none of the results. Each href
        is resolved against the page it came from before anything else looks at
        it.

        ``linkPattern`` is how a source says which of its links are results. A
        search page also carries navigation, help and social links, and without
        a pattern every one of them arrives as a catalogue entry.
        """
        options = source.get("options") or {}
        base = str((_context or {}).get("url") or source.get("url") or "")
        try:
            pattern = re.compile(str(options.get("linkPattern") or ""), re.I) if options.get("linkPattern") else None
        except re.error:
            pattern = None
        rows, seen = [], set()
        for href, title in re.findall(r'<a[^>]+href="([^"#][^"]*)"[^>]*>(.*?)</a>', body, re.I | re.S):
            resolved = urllib.parse.urljoin(base, html.unescape(href)) if base else href
            if not resolved.lower().startswith(("http://", "https://")):
                continue
            if pattern and not pattern.search(resolved):
                continue
            clean = re.sub(r"<[^>]+>", " ", title); clean = html.unescape(re.sub(r"\s+", " ", clean)).strip()
            if len(clean) < 2 or resolved in seen:
                continue
            seen.add(resolved); rows.append(_item(clean, "", "", None, resolved, "external", downloadable=False))
        return rows[:300]

    def item(self, token: str) -> dict:
        cached = self._items.get(token)
        if cached and cached[0] >= time.time():
            return dict(cached[1])
        if not re.fullmatch(r"[a-f0-9]{32}", token):
            raise DiskError("That online catalogue result has expired. Search again before installing it.")
        path = self._item_dir / f"{token}.json"
        try:
            payload = json.loads(path.read_text("utf-8"))
            expires = float(payload["expires"])
            item = payload["item"]
            if expires < time.time() or not isinstance(item, dict):
                raise ValueError
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            raise DiskError("That online catalogue result has expired. Search again before installing it.")
        self._items[token] = (expires, dict(item))
        return dict(item)

    def _remember_item(self, token: str, item: dict) -> None:
        expires = time.time() + 3600
        stored = dict(item)
        self._items[token] = (expires, stored)
        path = self._item_dir / f"{token}.json"
        temporary = self._item_dir / f".{token}.{uuid.uuid4().hex}.tmp"
        try:
            temporary.write_text(json.dumps({"expires": expires, "item": stored}), "utf-8")
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def download(self, token: str, preferred: str = "ofs") -> tuple[str, bytes, dict]:
        item = self.item(token)
        requests = item.get("downloadRequests") or []
        if requests:
            request_row = max(requests, key=lambda row: self._download_score(row.get("filename", ""), preferred))
            url = self._generated_download_url(str(request_row["url"]))
            name = str(request_row.get("filename") or Path(urllib.parse.urlparse(url).path).name or f"{item['title']}.zip")
            return name, self._fetch(url, ttl=1, limit=128 * 1024 * 1024), item
        choices = item.get("downloadChoices") or [item.get("downloadUrl")]
        choices = [url for url in choices if url]
        url = max(choices, key=lambda value: self._download_score(value, preferred)) if choices else None
        if not item.get("downloadable", True) or not url:
            raise DiskError("This catalogue item links to its publisher page and cannot be installed automatically.")
        name = Path(urllib.parse.urlparse(url).path).name or f"{item['title']}.zip"
        return name, self._fetch(url, ttl=60, limit=128 * 1024 * 1024), item

    @staticmethod
    def _download_score(value: str, preferred: str) -> tuple[bool, bool]:
        lowered = urllib.parse.urlparse(value).path.casefold()
        ofs_hint = any(hint in lowered for hint in ("/ofs/", "5_25", ".adf", ".adz"))
        ffs_hint = any(hint in lowered for hint in ("/ffs/", "3_5", ".adf"))
        return (ffs_hint if preferred == "ffs" else ofs_hint, not (ofs_hint or ffs_hint))

    @staticmethod
    def _generated_download_url(url: str) -> str:
        url = CatalogueService._http_url(
            url, "The catalogue supplied an invalid download request URL."
        )
        url = outbound_checked_url(url, "The catalogue supplied an invalid download request URL.")
        request = urllib.request.Request(url, data=b"", method="POST", headers={"User-Agent": "AmigaFileForge/1.0 (+local archival tool)", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                payload = json.loads(response.read(256 * 1024).decode("utf-8"))
        except Exception as exc:
            raise DiskError(f"Could not generate the catalogue download: {exc}") from exc
        generated = str(payload.get("url") or "") if isinstance(payload, dict) else ""
        return outbound_checked_url(
            generated, "The catalogue did not return a usable download URL."
        )


#: Every machine identifier, in the order the workbench lists them.
MACHINE_ORDER = ("a500", "a500plus", "a600", "a1200", "a2000", "a3000", "a4000", "cd32")

#: How a catalogue's compatibility prose names a machine. A chipset name is
#: the usual form on an Amiga database, because software is written for OCS,
#: ECS or AGA rather than for one model.
COMPATIBILITY_TOKENS = (
    ("cd32", ("cd32",)),
    ("a4000", ("a4000",)),
    ("a3000", ("a3000",)),
    ("a2000", ("a2000",)),
    ("a1200", ("a1200",)),
    ("a600", ("a600",)),
    ("«plus500»", ("a500plus",)),
    ("a500", ("a500",)),
    ("aga", ("a1200", "a4000", "cd32")),
    ("ecs", ("a500plus", "a600", "a3000")),
    ("ocs", ("a500", "a2000")),
)


def _machines_from_compatibility(value: str, fallback: list[str]) -> list[str]:
    """Translate catalogue compatibility prose into stable machine filters."""
    compact = re.sub(r"[\s.]+", "", str(value)).casefold().replace("amiga", "a")
    # "A500+" contains "A500", so the plus is folded to its own token before
    # anything is matched and the bare model can no longer match inside it.
    compact = re.sub(r"a500(?:\+|plus)", "«plus500»", compact)
    found: set[str] = set()
    for token, machines in COMPATIBILITY_TOKENS:
        if token in compact:
            found.update(machines)
    ordered = [machine for machine in MACHINE_ORDER if machine in found]
    return ordered or list(fallback)


def _item(title, publisher, year, download, page, artifact, *, description="", machines=None, downloadable=True, version="", resolver=None):
    return {"title": html.unescape(str(title)).strip(), "publisher": html.unescape(str(publisher)).strip(), "year": str(year).strip(), "description": html.unescape(str(description)).strip(), "downloadUrl": download, "pageUrl": page, "artifactType": artifact, "downloadable": bool(downloadable and (download or resolver)), "machines": machines or [], "version": version, "resolver": resolver}


def _plain_text(value: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(value)))).strip()


def _html_class_content(block: str, name: str) -> str:
    match = re.search(
        r'<div[^>]+class=["\'][^"\']*\b'
        + re.escape(name)
        + r'\b[^"\']*["\'][^>]*>(.*?)</div>',
        block,
        re.I | re.S,
    )
    return match.group(1) if match else ""


def _merge_settings(defaults: dict, configured: dict) -> dict:
    """Add newly supported settings without replacing a user's configured values."""
    merged = copy.deepcopy(defaults)
    for key, value in configured.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_settings(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def archive_members(name: str, data: bytes) -> list[tuple[str, bytes]]:
    if not name.lower().endswith(".zip") and not data.startswith(b"PK"):
        return [(name, data)]
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = []
            for info in validated_zip_members(
                archive, max_expanded_bytes=256 * 1024 * 1024
            ):
                if info.is_dir() or info.filename.startswith(("/", "\\")) or ".." in Path(info.filename).parts:
                    continue
                members.append((info.filename, archive.read(info)))
            return members
    except zipfile.BadZipFile as exc:
        raise DiskError("The downloaded ZIP file is damaged or incomplete.") from exc
