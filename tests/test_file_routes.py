import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask

from app.errors import DiskError
from app.operations import OperationRegistry
from app.routes.files import create_files_blueprint


class FileRouteTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.service = Mock()
        self.session = SimpleNamespace(kind="ofs")
        self.service.get.return_value = self.session
        self.service.summary.return_value = {"id": "a" * 32, "kind": "ofs"}
        self.service.inner_for.side_effect = lambda _session, path, _side: path
        app = Flask(__name__)
        app.register_blueprint(
            create_files_blueprint(
                self.service,
                Path(self.temporary.name),
                OperationRegistry(),
            )
        )
        # The production server turns a DiskError into a 400; the blueprint
        # under test is registered on its own, so the same handler is added
        # here rather than letting a refusal look like a crash.
        app.register_error_handler(DiskError, lambda error: ({"error": str(error)}, 400))
        self.client = app.test_client()

    def tearDown(self):
        self.temporary.cleanup()

    def test_delete_sends_all_selected_slot_files_in_one_mutation(self):
        """A slot inside a container is addressed through the engine."""
        self.session.kind = "hdf"
        response = self.client.post(
            "/api/images/test/delete",
            json={
                "slot": 7,
                "side": 2,
                "items": [
                    {"path": "ONE", "recursive": False},
                    {"path": "TWO", "recursive": False},
                ],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.service.mutate.assert_called_once_with(
            self.session,
            ["rm", "--force", "{image}:ONE", "{image}:TWO"],
            2,
        )
        self.assertEqual(len(response.get_json()["deletedItems"]), 2)

    def test_access_change_sends_all_selected_files_in_one_mutation(self):
        self.service.set_access.return_value = ["$.ONE", "$.TWO"]
        response = self.client.post(
            "/api/images/test/lock",
            json={
                "slot": 3,
                "paths": ["$.ONE", "$.TWO"],
                "unlock": True,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.service.set_access.assert_called_once_with(
            self.session,
            ["$.ONE", "$.TWO"],
            True,
            None,
        )

    def test_metadata_change_writes_protection_and_comment_together(self):
        self.service.set_file_metadata.return_value = {
            "protection": 0x05,
            "comment": "Locked loader",
            "datestamp": None,
            "length": 2048,
        }
        response = self.client.post(
            "/api/images/test/metadata",
            json={
                "partition": 0,
                "side": 2,
                "path": "Games/Loader",
                "protection": "&00000005",
                "comment": "Locked loader",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.service.set_file_metadata.assert_called_once_with(
            self.session, "Games/Loader", "&00000005", "Locked loader", 2,
        )
        self.assertEqual(response.get_json()["metadata"]["comment"], "Locked loader")

    def test_mkdir_validates_and_creates_a_drawer(self):
        self.session.kind = "ffs"

        response = self.client.post(
            "/api/images/test/mkdir",
            json={"path": "Games/NewDrawer", "side": 2},
        )

        self.assertEqual(response.status_code, 200)
        self.service.validate_leaf_name.assert_called_once_with(
            self.session,
            "NewDrawer",
        )
        self.service.make_directory.assert_called_once_with(
            self.session,
            "Games/NewDrawer",
            2,
        )

    def test_mkdir_creates_a_drawer_inside_a_partition(self):
        """A partition is a real AmigaDOS volume, so it nests drawers."""
        self.session.kind = "hdf"
        self.session.partition = 0

        response = self.client.post(
            "/api/images/test/mkdir",
            json={"path": "Games/NewDrawer", "partition": 0},
        )

        self.assertEqual(response.status_code, 200)
        self.service.select_partition.assert_called_once_with(self.session, 0)
        self.service.make_directory.assert_called_once_with(
            self.session,
            "Games/NewDrawer",
            None,
        )

    def test_mkdir_is_refused_where_there_are_no_directories(self):
        self.session.kind = "rom"

        response = self.client.post(
            "/api/images/test/mkdir",
            json={"path": "Games"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("no directories", response.get_json()["error"])
        self.service.make_directory.assert_not_called()

    def test_folder_import_sends_the_complete_reviewed_batch_once(self):
        self.session.kind = "ffs"
        self.service.put_host_tree.return_value = {
            "imported": ["$.Games.Pack.One", "$.Games.Pack.Sub.Two"],
            "conflicts": [],
        }

        response = self.client.post(
            "/api/images/test/folder-import",
            data={
                "files": [
                    (io.BytesIO(b"one"), "one.bin"),
                    (io.BytesIO(b"two"), "two.bin"),
                ],
                "targetPaths": '["Pack/One", "Pack/Sub/Two"]',
                "metadata": '[{"load":"0x1900","execute":"0x8023"},{}]',
                "destination": "$.Games",
                "mode": "preserve",
                "replace": "false",
                "side": "2",
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 200)
        call = self.service.put_host_tree.call_args
        self.assertEqual(call.args[:2], (self.session, "$.Games"))
        self.assertEqual(
            [item["targetPath"] for item in call.args[2]],
            ["Pack/One", "Pack/Sub/Two"],
        )
        self.assertEqual(call.args[2][0]["metadata"]["load"], "0x1900")
        self.assertTrue(all(item["hostPath"].exists() is False for item in call.args[2]))
        self.assertEqual(
            call.kwargs,
            {"preserve_directories": True, "replace": False, "side": 2},
        )

    def test_loose_file_download_can_include_a_metadata_sidecar(self):
        exported = Path(self.temporary.name) / "exported"
        exported.write_bytes(b"payload")
        self.service.export_file.return_value = exported
        # Bit 2 is the inverted write bit and bit 0 the inverted delete bit,
        # so a locked file shows neither w nor d.
        self.service.file_metadata.return_value = {
            "access": 0x05,
            "comment": "The game loader",
            "length": 7,
        }

        response = self.client.get(
            "/api/images/test/file?path=Program&bundle=metadata"
        )

        self.assertEqual(response.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            self.assertEqual(archive.read("Program"), b"payload")
            self.assertEqual(
                archive.read("Program.inf"),
                b'Program ----r-e- 00000007 "The game loader"\n',
            )
        response.close()

    def test_the_sidecar_retains_the_file_drawer(self):
        exported = Path(self.temporary.name) / "exported-drawer"
        exported.write_bytes(b"payload")
        self.service.export_file.return_value = exported
        self.service.file_metadata.return_value = {"access": 0, "length": 7}

        response = self.client.get(
            "/api/images/test/file?path=Games/Demo&bundle=metadata"
        )
        with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
            self.assertEqual(
                archive.read("Demo.inf"),
                b"Games/Demo ----rwed 00000007\n",
            )
        response.close()

    def test_a_drawer_move_is_sent_as_one_route_operation(self):
        self.service.move_ofs_items.return_value = [{
            "source": "Hello",
            "destination": "Games/Hello",
        }]

        response = self.client.post(
            "/api/images/test/move-ofs",
            json={
                "slot": 4,
                "side": 2,
                "items": [{"source": "$.HELLO", "destination": "F.HELLO"}],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.service.move_ofs_items.assert_called_once_with(
            self.session,
            [{"source": "$.HELLO", "destination": "F.HELLO"}],
            2,
        )

    @patch("app.routes.files.delete_ffs_items")
    def test_ffs_batch_delete_rewrites_menus_once(self, delete_items):
        self.session.kind = "ffs"
        delete_items.return_value = {
            "deletedItems": [
                {"path": "$.ONE", "isDirectory": False},
                {"path": "$.TWO", "isDirectory": False},
            ],
            "menuEntriesRemoved": 4,
        }

        response = self.client.post(
            "/api/images/test/delete",
            json={"items": [{"path": "$.ONE"}, {"path": "$.TWO"}]},
        )

        self.assertEqual(response.status_code, 200)
        delete_items.assert_called_once_with(
            self.service,
            self.session,
            ["$.ONE", "$.TWO"],
        )
        self.assertEqual(response.get_json()["menuEntriesRemoved"], 4)


if __name__ == "__main__":
    unittest.main()
