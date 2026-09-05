import io
import copy
import tempfile
import unittest
import zipfile
from unittest.mock import Mock, patch


from app.amiga_metadata import amiga_zip_metadata
from app.catalog_service import CatalogueService, DEFAULT_SOURCES, archive_members
from app.disk_service import DiskError
from app.routes.catalog import (
    _available_ffs_directory_name,
    _catalogue_identities,
    _preferred_disk_members,
)


def source(source_id):
    return next(item for item in DEFAULT_SOURCES if item["id"] == source_id)


def configured_source(source_id, url, **options):
    """Build one catalogue source for a parser test.

    The shipped source list names real sites; a parser test is about the page
    shape, so it declares its own source rather than depending on which sites
    happen to be enabled.
    """
    return {
        "id": source_id, "name": source_id, "type": "configured", "url": url,
        "machines": ["a500", "a600", "a1200"], "enabled": True, "direct": True,
        "options": options,
    }


class CatalogueServiceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.service = CatalogueService(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    @patch("app.catalog_service.urllib.request.urlopen")
    def test_fetch_rejects_non_http_url_before_opening_it(self, urlopen):
        with self.assertRaisesRegex(DiskError, "invalid URL"):
            self.service._fetch("file:///etc/passwd")
        urlopen.assert_not_called()

    def test_online_install_source_name_matches_catalogue_title(self):
        self.assertIn("jetpac", _catalogue_identities("Jetpac (Ultimate Play The Game).adf"))
        self.assertTrue(
            _catalogue_identities("Repton 2")
            & _catalogue_identities("REPTON-2 (Superior Software).adf")
        )

    def test_parses_thumbnail_download_cards(self):
        body = '''<div class="thumbnail text-center">
          <div class="row-title"><span class="row-title"><a href="game.php?id=7">Ruff and Reddy</a></span></div>
          <div class="row-pub"><a>Hi-Tec</a></div><div class="row-dt"><a>1990</a></div>
          <a href="gameimg/discs/7/ruff.adf">Download</a></div></div>'''
        rows = self.service._parse_thumbnail_cards(
            configured_source("cards", "https://games.example/", parser="thumbnail-cards"),
            body, {},
        )
        self.assertEqual(rows[0]["title"], "Ruff and Reddy")
        self.assertEqual(rows[0]["publisher"], "Hi-Tec")
        self.assertEqual(rows[0]["year"], "1990")
        self.assertTrue(rows[0]["downloadUrl"].endswith("/gameimg/discs/7/ruff.adf"))

    def test_parses_a_featured_disc_declared_by_a_function_call(self):
        body = '''tabulatE("psygnosis", "green", "img", "Menace-1", "MENACE",
          "psygnosis", "-", "Shoot-em-up", "D. Jones", 150, 224, "1988", "", "Available",
          "Amiga 500, OCS", "Floppy", "FC", "Review");'''
        featured = configured_source(
            "featured", "https://library.example/",
            parser="function-calls", callName="tabulatE",
            callFields={"publisher": 0, "stem": 3, "title": 4, "description": 7},
            downloadTemplate="https://library.example/archive/{publisher}/ADF/{stem}.zip",
        )
        featured["machines"] = ["a500", "a2000"]
        rows = self.service._parse_function_calls(featured, body, {})
        self.assertEqual(rows[0]["title"], "MENACE")
        self.assertEqual(rows[0]["machines"], ["a500", "a2000"])
        self.assertTrue(rows[0]["downloadUrl"].endswith("/psygnosis/ADF/Menace-1.zip"))

    def test_section_catalogue_page_returns_only_real_downloads_as_installable(self):
        body = '''<h6 id="cat_table_title_bar">PSYGNOSIS</h6>
          <section id="Locked_1001"><b>LOCKED TITLE</b><p>Downloads:<a href="c-dvd.html">DVD</a></p></section>
          <section id="Public_1001"><b>PUBLIC TITLE</b><p>Release Date:<br/>1st May 1987</p>
          <a href="../../../download/amiga/psygnosis/DMS/Public.dms">DMS</a></section>'''
        rows = self.service._parse_section_catalogue(
            configured_source("sections", "https://library.example/", parser="section-catalogue"),
            body,
            {"url": "https://library.example/profs/amiga/cats/psygnosis.html", "category": "Professional Releases"},
        )
        self.assertFalse(rows[0]["downloadable"])
        self.assertTrue(rows[1]["downloadable"])
        self.assertEqual(rows[1]["year"], "1987")

    def test_machine_index_item_rows_use_the_configured_profile(self):
        body = '''<td><a href="/litem/Menace/123/">Menace</a> (1st May 1988) (Psygnosis)
          (Amiga 500, Floppy, English)</td>'''
        rows = self.service._parse_item_rows(
            configured_source("items", "https://catalogue.example/", parser="item-rows"),
            body,
            {"profile": {"label": "Amiga 500", "machines": ["a500"]}},
        )
        self.assertEqual(rows[0]["title"], "Menace")
        self.assertEqual(rows[0]["publisher"], "Psygnosis")
        self.assertEqual(rows[0]["machines"], ["a500"])

    def test_item_rows_mark_a_release_for_every_machine_it_names(self):
        body = '''<td><a href="/litem/Turrican/456/">Turrican</a> (1st May 1990) (Rainbow Arts)
          (Amiga 500/Amiga 1200, Floppy, English)</td>'''
        rows = self.service._parse_item_rows(
            configured_source("items", "https://catalogue.example/", parser="item-rows"),
            body,
            {"profile": {"label": "Amiga 500", "machines": ["a500"]}},
        )
        self.assertEqual(rows[0]["machines"], ["a500", "a1200"])

    def test_resolver_catalogues_return_continuation_without_claiming_unchecked_media(self):
        configured = copy.deepcopy(source("everygamegoing"))
        configured["enabled"] = True
        configured["options"]["resultValidationLimit"] = 2
        candidates = [
            {
                "title": f"Game {number}", "publisher": "Publisher", "description": "",
                "year": "", "downloadable": True, "resolver": "media-links",
                "pageUrl": f"https://example.test/game/{number}", "downloadUrl": None,
                "artifactType": "remote-item", "machines": ["a600"],
            }
            for number in range(5)
        ]
        self.service.sources = lambda: [configured]
        self.service._load_catalogue = lambda *_args: [dict(row) for row in candidates]
        self.service._resolve_row = lambda row: {**row, "downloadUrl": f"{row['pageUrl']}.adf"}

        first, failures, continuation = self.service.search_page("", "a600")
        second, _, next_continuation = self.service.search_page("", "a600", cursors=continuation)

        self.assertEqual([row["title"] for row in first], ["Game 0", "Game 1"])
        self.assertEqual([row["title"] for row in second], ["Game 2", "Game 3"])
        self.assertEqual(failures, [])
        self.assertEqual(continuation, {"everygamegoing": 2})
        self.assertEqual(next_continuation, {"everygamegoing": 4})

    def test_online_ffs_directory_allocator_avoids_existing_truncated_names(self):
        service = Mock()
        service.list_directory.return_value = {
            "entries": [{"name": "LONGTITLE"}, {"name": "LONGTITLE1"}],
        }
        name = _available_ffs_directory_name(
            service, Mock(), "$.Games", "LONGTITLE",
        )
        self.assertEqual(name, "LONGTITLE2")

    def test_everygamegoing_urls_and_machine_ids_come_from_source_settings(self):
        configured = copy.deepcopy(source("everygamegoing"))
        configured["url"] = "https://catalogue.example/"
        configured["options"]["landingTemplate"] = "machines/{machineId}/software.html"
        configured["options"]["machineProfiles"] = {
            "a600": {"ids": [99], "label": "Configured machine"},
        }
        requested = []
        self.service._fetch = lambda url, **_options: requested.append(url) or (
            b'<td><a href="/litem/Game/1/">Game</a> (1985) (Publisher)</td>'
        )
        rows = self.service._load_machine_index(configured, "", "a600")
        self.assertEqual(requested, ["https://catalogue.example/machines/99/software.html"])
        self.assertEqual(rows[0]["description"].split(".")[0], "Configured machine")

    def test_everygamegoing_item_without_supported_media_is_suppressed(self):
        row = {
            "pageUrl": "https://www.everygamegoing.com/litem/No-Media/1/",
            "description": "Amiga 500. Download availability is checked when installed.",
            "resolverOptions": {"downloadPathContains": "/download/"},
        }
        self.service._fetch = lambda *_args, **_kwargs: b'<a href="/images/cover.jpg">Cover</a>'
        self.assertIsNone(self.service._resolve_row(row))

    def test_everygamegoing_item_accepts_only_download_media_paths(self):
        row = {
            "pageUrl": "https://www.everygamegoing.com/litem/Menace/1/",
            "description": "Amiga 500. Download availability is checked when installed.",
            "resolverOptions": {"downloadPathContains": "/download/"},
        }
        self.service._fetch = lambda *_args, **_kwargs: (
            b'<a href="/gallery/not-a-download.zip">Gallery</a>'
            b'<a href="/download/amiga/psygnosis/Menace.zip">Download</a>'
        )
        resolved = self.service._resolve_row(row)
        self.assertEqual(
            resolved["downloadUrl"],
            "https://www.everygamegoing.com/download/amiga/psygnosis/Menace.zip",
        )

    def test_parses_amigaos_package_feed(self):
        body = (
            "Package: Blocks\nVersion: 0.15-2\nMaintainer: AmigaOS Open\n"
            "Description: falling blocks\nURL: https://example.test/Blocks.zip\n\n"
        )
        rows = self.service._parse_package_paragraphs(
            configured_source("packages", "https://packages.example/", parser="package-paragraphs"),
            body, {},
        )
        self.assertEqual(rows[0]["artifactType"], "amigaos-package")
        self.assertEqual(rows[0]["version"], "0.15-2")

    def test_query_media_tiles_extract_configured_download_parameter(self):
        body = '''<td><a href="https://player.example/run?ofs&amp;disk0=https://cdn.example/Pyjamarama.adf"><img></a>
          <br>Pyjamarama<br><span><a href="https://project.example/pyjamarama">Project page</a></span></td>'''
        rows = self.service._parse_query_media_tiles(
            configured_source(
                "tiles", "https://homebrew.example/",
                parser="query-media-tiles", mediaQueryParameter="disk0",
            ),
            body, {},
        )
        self.assertEqual(rows[0]["title"], "Pyjamarama")
        self.assertEqual(rows[0]["downloadUrl"], "https://cdn.example/Pyjamarama.adf")
        self.assertEqual(rows[0]["pageUrl"], "https://project.example/pyjamarama")

    def test_html_cards_use_configured_upload_resolver(self):
        body = '''<div class="game_cell"><div class="game_title"><a href="https://maker.example/game">Amiga Game</a></div>
          <div class="game_text">For the Amiga 500</div><div class="game_author"><a>Homebrew Author</a></div></div>'''
        rows = self.service._parse_html_cards(source("itch-amiga"), body, {})
        self.assertEqual(rows[0]["publisher"], "Homebrew Author")
        self.assertEqual(rows[0]["resolver"], "upload-buttons")

    def test_upload_resolver_suppresses_unrelated_archives_and_keeps_amiga_media(self):
        configured = source("itch-amiga")
        row = self.service._parse_html_cards(configured, '''<div class="game_cell"><div class="game_title">
          <a href="https://maker.example/amiga600-game">Amiga 600 Game</a></div>
          <div class="game_text">For the Amiga 600</div><div class="game_author">Maker</div></div>''', {})[0]
        detail = '''<div class="upload"><a data-upload_id="123">Download</a><div><strong title="Amiga 600 Game.adf">Amiga 600 Game.adf</strong></div></div>
          <div class="upload"><a data-upload_id="456">Download</a><div><strong title="Windows build.exe">Windows build.exe</strong></div></div>'''
        resolved = self.service._resolve_upload_buttons(row, detail)
        self.assertEqual(resolved["downloadRequests"], [{
            "url": "https://maker.example/amiga600-game/file/123?source=view_game&as_props=1",
            "filename": "Amiga 600 Game.adf",
        }])

    def test_page_loader_uses_configured_machine_query(self):
        configured = copy.deepcopy(source("itch-amiga"))
        requested = []
        self.service._fetch = lambda url, **_options: requested.append(url) or b""
        self.service._load_page(configured, "arcade", "a500")
        self.assertEqual(requested, ["https://itch.io/search?q=amiga+500+arcade"])

    def test_source_configuration_is_validated_and_persisted(self):
        rows = self.service.save_sources([{
            "id": "mine", "name": "Mine", "type": "links",
            "url": "https://example.test/catalogue", "machines": ["a500"], "enabled": True,
        }])
        self.assertEqual(rows[0]["id"], "mine")
        self.assertEqual(
            CatalogueService(self.temporary.name).sources()[0]["url"],
            "https://example.test/catalogue",
        )
        self.assertIn("everygamegoing", {row["id"] for row in CatalogueService(self.temporary.name).sources()})
        self.assertNotIn("dcford", {row["id"] for row in CatalogueService(self.temporary.name).sources()})
        with self.assertRaises(DiskError):
            self.service.save_sources([{"name": "Unsafe", "url": "file:///etc/passwd"}])

    def test_catalogue_result_survives_service_restart(self):
        token = "a" * 32
        expected = {"title": "Chuckie Egg", "downloadUrl": "https://example.test/chuckie.adf"}
        self.service._remember_item(token, expected)

        restarted = CatalogueService(self.temporary.name)

        self.assertEqual(restarted.item(token), expected)

    def test_new_default_settings_are_merged_without_overwriting_configuration(self):
        configured = copy.deepcopy(source("everygamegoing"))
        configured["options"].pop("itemPathPrefix")
        configured["options"]["landingTemplate"] = "custom/{machineId}/"
        self.service.save_sources([configured])
        loaded = next(row for row in self.service.sources() if row["id"] == "everygamegoing")
        self.assertEqual(loaded["options"]["itemPathPrefix"], "/litem/")
        self.assertEqual(loaded["options"]["landingTemplate"], "custom/{machineId}/")

    def test_search_is_sent_to_the_complete_remote_catalogue(self):
        body = b'''<div class="thumbnail"><div class="row-title"><a href="game.php?id=1">Menace</a></div>
          <div class="row-pub"><a>Psygnosis</a></div><div class="row-dt"><a>1988</a></div>
          <a href="gameimg/discs/1/menace.adf">Download</a></div></div>'''
        requested = []
        self.service.save_sources([configured_source(
            "cards", "https://games.example/",
            loader="page", parser="thumbnail-cards", queryTemplate="index.php?search={query}",
        )])
        self.service._fetch = lambda url, **_options: requested.append(url) or body
        rows, failures = self.service.search("Menace", "a500")
        self.assertFalse(failures)
        self.assertEqual(rows[0]["title"], "Menace")
        self.assertIn("search=menace", requested[0])

    def test_pipeline_uses_configuration_not_catalogue_identity(self):
        configured = configured_source(
            "cards", "https://games.example/", loader="page", parser="thumbnail-cards",
        )
        configured.update(id="arbitrary-provider", name="Arbitrary Provider", url="https://example.test/")
        self.service.save_sources([configured])
        self.service._fetch = lambda *_args, **_options: b'''<div class="thumbnail">
          <div class="row-title"><a href="game.php?id=1">Configured Game</a></div>
          <a href="files/configured.adf">Download</a></div></div>'''
        rows, failures = self.service.search("Configured", "a500", {"arbitrary-provider"})
        self.assertFalse(failures)
        self.assertEqual(rows[0]["title"], "Configured Game")
        self.assertEqual(rows[0]["sourceName"], "Arbitrary Provider")

    def test_archive_members_rejects_traversal_and_keeps_images(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("games/frak.adf", b"disk")
            archive.writestr("../escape.adf", b"bad")
        self.assertEqual(
            archive_members("games.zip", buffer.getvalue()),
            [("games/frak.adf", b"disk")],
        )

    def test_native_disk_is_preferred_over_dms_variant_in_same_download(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("Repton-2-Upgraded.adf", b"adf")
            archive.writestr("Repton-2-Upgraded.dms", b"dms")
        self.assertEqual(
            _preferred_disk_members("repton-2.zip", buffer.getvalue()),
            [("Repton-2-Upgraded.adf", b"adf")],
        )

    def test_amiga_zip_metadata_reads_protection_and_comment(self):
        import zipfile

        entry = zipfile.ZipInfo("Game")
        entry.create_system = 1
        # ----rwed with the archive bit set, in the form the volume stores it.
        entry.external_attr = 0x0010 << 16
        entry.comment = b"  Playable   demo  "
        metadata = amiga_zip_metadata(entry)
        self.assertEqual(metadata["protection"], 0x0010)
        self.assertEqual(metadata["comment"], "Playable demo")

    def test_a_zip_from_another_machine_reports_no_amiga_metadata(self):
        import zipfile

        entry = zipfile.ZipInfo("Game")
        entry.create_system = 3  # Unix
        entry.external_attr = 0o100644 << 16
        self.assertIsNone(amiga_zip_metadata(entry))


if __name__ == "__main__":
    unittest.main()
