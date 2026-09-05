import gzip
import io
import tarfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

from app.archive_browser import (
    ArchiveError,
    MAX_ENTRIES,
    archive_member_editable,
    list_archive,
    preview_archive_member_replacement,
    read_archive_member_details,
    replace_archive_member,
)
from tests.dms_fixture import minimal_dms
from app.dms import TRACK_SIZE
from app.dms import dms_project

try:
    from flask import Flask, jsonify
    from app.disk_service import DiskError
    from app.operations import OperationRegistry
    from app.routes.files import create_files_blueprint
    from app.routes.hex_editor import create_hex_editor_blueprint
    from app.routes.tools import create_tools_blueprint
except ModuleNotFoundError:  # Flask is installed in the production image.
    Flask = None


def read_archive_member(data: bytes, filename: str, member_name: str) -> bytes:
    """Read one member's bytes, discarding the metadata the tests do not assert."""
    return read_archive_member_details(data, filename, member_name)[0]


class ArchiveBrowserTests(unittest.TestCase):
    def test_zip_is_presented_as_a_safe_hierarchy(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("Games/Arcadians/Startup-Sequence", b"*BASIC\r")
            archive.writestr("Games/README", b"Games collection")
        root = list_archive(stream.getvalue(), "collection.zip")
        self.assertEqual(root["entries"], [{
            "name": "Games", "type": "dir", "length": 0,
            "attr": "RO", "archiveEntry": True,
        }])
        games = list_archive(stream.getvalue(), "collection.zip", "Games")
        self.assertEqual([row["name"] for row in games["entries"]], ["Arcadians", "README"])
        self.assertEqual(games["entries"][1]["contentKind"], "text")
        boot = list_archive(stream.getvalue(), "collection.zip", "Games/Arcadians")
        self.assertEqual(boot["entries"][0]["contentKind"], "script")
        self.assertEqual(read_archive_member(stream.getvalue(), "collection.zip", "Games/README"), b"Games collection")

    def test_a_metadata_sidecar_is_exposed_by_archive_members(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("Games/Program", b"payload")
            archive.writestr("Games/Program.inf", b'Games/Program ----r-e- 00000007 "The game loader"\n')
        listing = list_archive(stream.getvalue(), "collection.zip", "Games")
        program = next(row for row in listing["entries"] if row["name"] == "Program")
        self.assertEqual(program["comment"], "The game loader")
        _content, metadata = read_archive_member_details(
            stream.getvalue(), "collection.zip", "Games/Program",
        )
        self.assertTrue(metadata["metadataAvailable"])
        self.assertEqual(metadata["access"], 0x05)
        self.assertEqual(metadata["comment"], "The game loader")

    def test_a_sidecar_reports_the_protection_and_comment_it_records(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("PROGRAM", b"payload")
            archive.writestr("PROGRAM.inf", b'PROGRAM ----r-e- 00000007 "Locked copy"\n')
        _content, metadata = read_archive_member_details(
            stream.getvalue(), "collection.zip", "PROGRAM",
        )
        # Bit 2 is the inverted write bit: set means the file cannot be written.
        self.assertEqual(metadata["access"], 0x05)
        self.assertEqual(metadata["comment"], "Locked copy")

    def test_tar_and_standalone_gzip_are_supported(self):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            info = tarfile.TarInfo("Docs/Manual")
            info.size = 6
            archive.addfile(info, io.BytesIO(b"Manual"))
        self.assertEqual(read_archive_member(stream.getvalue(), "docs.tar", "Docs/Manual"), b"Manual")
        compressed = gzip.compress(b"10 PRINT \"HELLO\"\r")
        listing = list_archive(compressed, "HELLO.bas.gz")
        self.assertEqual(listing["entries"][0]["name"], "HELLO.bas")
        self.assertEqual(read_archive_member(compressed, "HELLO.bas.gz", "HELLO.bas"), b"10 PRINT \"HELLO\"\r")

    def test_editable_archives_are_rebuilt_with_only_the_selected_member_changed(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.comment = b"kept"
            archive.writestr("Docs/README", b"Old")
            archive.writestr("Docs/OTHER", b"Untouched")
        rebuilt = replace_archive_member(stream.getvalue(), "docs.zip", "Docs/README", b"New text")
        self.assertTrue(archive_member_editable(rebuilt, "docs.zip"))
        with zipfile.ZipFile(io.BytesIO(rebuilt)) as archive:
            self.assertEqual(archive.comment, b"kept")
            self.assertEqual(archive.read("Docs/README"), b"New text")
            self.assertEqual(archive.read("Docs/OTHER"), b"Untouched")

        compressed = gzip.compress(b"Before")
        rebuilt = replace_archive_member(compressed, "README.gz", "README", b"After")
        self.assertEqual(gzip.decompress(rebuilt), b"After")

    def test_dms_track_replacement_requires_same_length_and_preserves_structure(self):
        data = minimal_dms()
        self.assertTrue(archive_member_editable(data, "game.dms", "Track 000"))
        with self.assertRaisesRegex(ArchiveError, "exactly the same length"):
            replace_archive_member(data, "game.dms", "Track 000", b"replacement")
        replacement = b"Z" * TRACK_SIZE
        preview = preview_archive_member_replacement(
            data, "game.dms", "Track 000", replacement
        )
        self.assertTrue(preview["structuralProofRequired"])
        self.assertEqual(preview["name"], "Track 000")
        self.assertEqual(preview["length"], TRACK_SIZE)
        rebuilt = replace_archive_member(data, "game.dms", "Track 000", replacement)
        self.assertEqual(read_archive_member(rebuilt, "game.dms", "Track 000"), replacement)
        # The untouched track still reads back, so the rebuild rewrote one
        # track rather than the whole archive.
        self.assertEqual(
            read_archive_member(rebuilt, "game.dms", "Track 001"),
            read_archive_member(data, "game.dms", "Track 001"),
        )

    def test_compressed_dms_rebuild_remains_compressed_and_readable(self):
        data = gzip.compress(minimal_dms(), mtime=123)
        replacement = b"Z" * TRACK_SIZE
        rebuilt = replace_archive_member(data, "game.dms", "Track 000", replacement)
        self.assertTrue(rebuilt.startswith(b"\x1f\x8b"))
        self.assertEqual(read_archive_member(rebuilt, "game.dms", "Track 000"), replacement)

    def test_dms_project_lists_tracks_and_their_compression_modes(self):
        project = dms_project(minimal_dms())
        self.assertEqual(project["schema"], "amiga-file-forge/dms-project/v1")
        self.assertEqual(project["diskType"], "AmigaDOS FFS")
        self.assertEqual(project["modes"], {"NOCOMP": 2})
        self.assertEqual(project["tracks"][0]["name"], "Track 000")
        self.assertEqual(project["tracks"][0]["mode"], "NOCOMP")
        self.assertTrue(project["tracks"][0]["complete"])
        self.assertTrue(project["tracks"][0]["checksumValid"])

    def test_unsafe_parent_members_are_rejected(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("../escape", b"bad")
        with self.assertRaises(ArchiveError):
            list_archive(stream.getvalue(), "unsafe.zip")

    def test_oversized_archive_inventory_is_rejected_before_member_reads(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for index in range(MAX_ENTRIES + 1):
                archive.writestr(f"empty-{index}", b"")
        with self.assertRaisesRegex(ArchiveError, "more than"):
            list_archive(stream.getvalue(), "too-many.zip")

    def test_raw_and_compressed_dms_are_browsable_disk_archives(self):
        raw = minimal_dms()
        for data in (raw, gzip.compress(raw)):
            listing = list_archive(data, "game.dms")
            self.assertEqual(listing["archiveKind"], "dms")
            self.assertIn("DMS disk archive", listing["description"])
            self.assertEqual(
                [row["name"] for row in listing["entries"]], ["Track 000", "Track 001"]
            )
            first = listing["entries"][0]
            self.assertTrue(first["complete"])
            self.assertEqual(first["length"], TRACK_SIZE)
            # A track has checksums rather than an AmigaDOS load address.
            self.assertNotIn("load", first)
            self.assertEqual(first["packedChecksum"], first["unpackedChecksum"])
            self.assertEqual(
                read_archive_member(data, "game.dms", "Track 000"), b"A" * TRACK_SIZE
            )

    @unittest.skipIf(Flask is None, "Flask is installed in the production image")
    def test_archive_routes_mark_browse_and_download_members(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("Games/README", b"Games collection")
            archive.writestr("Games/Startup-Sequence", b"*BASIC\rSTACK=8192\r")
            archive.writestr("Games/CODE", bytes.fromhex("A90020F4FF60"))

        class Service:
            session = SimpleNamespace(
                kind="ffs", target_hardware="a1200-ffs", hardware_profile={}, hfe_read_only=False,
            )
            written = None

            def get(self, _image_id):
                return self.session

            def mountable(self, session):
                return session.kind in {"ffs", "ofs"}

            def browse_directory(self, *_args):
                return {"entries": [{"name": "games.zip", "type": "file", "length": len(stream.getvalue())}]}

            def file_metadata(self, *_args):
                return {"length": len(stream.getvalue())}

            def read_file(self, *_args):
                return stream.getvalue()

            def validate_leaf_name(self, _session, name, _slot=None):
                if not name or len(name) > 10:
                    raise DiskError("Invalid FFS filename.")
                return name

            def list_directory(self, *_args):
                return {"entries": []}

            def put(self, _session, destination, host_path, protection=None, comment=None, filetype=None, side=None):
                self.written = (destination, host_path.read_bytes(), protection, comment, filetype, side)

            def summary(self, _session):
                return {"id": "test", "kind": "ffs"}

        service = Service()
        app = Flask(__name__)
        app.register_blueprint(create_files_blueprint(service, Path("/tmp"), OperationRegistry()))
        app.register_blueprint(create_hex_editor_blueprint(service))
        app.register_blueprint(create_tools_blueprint(service, OperationRegistry()))
        app.register_error_handler(DiskError, lambda error: (jsonify(error=str(error)), 400))
        client = app.test_client()
        tree = client.get("/api/images/test/tree?path=$").get_json()
        self.assertTrue(tree["entries"][0]["archive"])
        listing = client.get("/api/images/test/archive/tree?path=$.games.zip&name=games.zip").get_json()
        self.assertEqual(listing["entries"][0]["name"], "Games")
        member = client.get("/api/images/test/archive/file?path=$.games.zip&name=games.zip&member=Games/README")
        self.assertEqual(member.data, b"Games collection")
        inspected = client.get(
            "/api/images/test/archive/inspect?path=$.games.zip&name=games.zip&member=Games/Startup-Sequence"
        ).get_json()
        self.assertEqual(inspected["view"], "script")
        self.assertFalse(inspected["readOnly"])
        self.assertTrue(inspected["archiveEditable"])
        self.assertIn("STACK=8192", inspected["text"])
        disassembly = client.get(
            "/api/images/test/archive/disassembly?path=$.games.zip&name=games.zip"
            "&member=Games/CODE&architecture=68000&origin=0x8000"
        ).get_json()
        self.assertEqual(disassembly["architecture"], "68000")
        self.assertEqual(disassembly["origin"], 0x8000)
        cheat_report = client.get(
            "/api/images/test/cheat-candidates?path=$.games.zip&name=games.zip"
            "&member=Games/CODE"
        ).get_json()
        self.assertEqual(cheat_report["path"], "Games/CODE")
        self.assertEqual(cheat_report["kind"], "68000")
        hex_page = client.get(
            "/api/images/test/archive-hex?path=$.games.zip&name=games.zip"
            "&member=Games/CODE&offset=0&length=16"
        ).get_json()
        self.assertEqual(hex_page["data"], "A90020F4FF60")
        self.assertTrue(hex_page["readOnly"])
        found = client.get(
            "/api/images/test/archive-hex/search?path=$.games.zip&name=games.zip"
            "&member=Games/CODE&query=20F4FF&mode=hex&start=0"
        ).get_json()
        self.assertEqual(found["offset"], 2)
        created = client.post("/api/images/test/empty-file", json={
            "destination": "$.Games", "name": "NEWFILE",
            "protection": "----r-e-", "comment": "New file",
        })
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.get_json()["path"], "Games/NEWFILE")
        # Protection reaches the service in the form the person typed, which
        # the service parses once rather than each route guessing at it.
        self.assertEqual(
            service.written[:5],
            ("Games/NEWFILE", b"", "----r-e-", "New file", None),
        )


if __name__ == "__main__":
    unittest.main()
