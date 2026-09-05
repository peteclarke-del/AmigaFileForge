from __future__ import annotations

import io
import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from app.disk_service import DiskService
from app.ffs_items import delete_ffs_items


class DiskPerformanceTests(unittest.TestCase):
    def test_copy_stream_falls_back_for_an_in_memory_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "upload.img"
            content = b"Amiga" * 100_000

            DiskService._copy_stream(io.BytesIO(content), target)

            self.assertEqual(target.read_bytes(), content)

    def test_local_checkpoint_copy_preserves_sparse_zero_ranges(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.hda"
            target = Path(directory) / "checkpoint.hda"
            with source.open("wb") as output:
                output.write(b"FFS")
                output.seek(32 * 1024 * 1024 - 1)
                output.write(b"\0")

            DiskService._copy_local_file(source, target)

            self.assertEqual(target.read_bytes(), source.read_bytes())
            self.assertLess(target.stat().st_blocks * 512, target.stat().st_size // 4)

    def test_trusted_local_open_uses_filesystem_copy_not_upload_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "firmware.rom"
            source.write_bytes(bytes([0xFF]) * (16 * 1024))
            service = DiskService(root / "work")

            with patch.object(
                service,
                "_copy_local_file",
                wraps=service._copy_local_file,
            ) as local_copy, patch.object(
                service,
                "_copy_stream",
                side_effect=AssertionError("local open used the upload copy path"),
            ):
                session = service.create_from_path(source, force_kind="rom")

            self.assertEqual(local_copy.call_count, 1)
            self.assertEqual(session.path.read_bytes(), source.read_bytes())

    def test_known_ffs_local_open_skips_the_all_filesystem_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = DiskService(root / "source-work").create_blank(
                "ffs-intl", "SOURCE"
            ).path
            service = DiskService(root / "open-work")

            with patch.object(
                service,
                "_run_json",
                side_effect=AssertionError("known FFS media used the generic probe"),
            ):
                opened = service.create_from_path(source)

            self.assertEqual(opened.kind, "ffs")
            self.assertEqual(opened.path.stat().st_size, source.stat().st_size)

    def test_sparse_optimisation_does_not_look_like_an_image_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory) / "scsi0.hda"
            image.write_bytes(b"DOS\x03" + bytes(8 * 1024 * 1024))
            timestamp = 1_700_000_000_123_456_789
            os.utime(image, ns=(timestamp, timestamp))

            DiskService._optimise_sparse_file(image)

            self.assertEqual(image.stat().st_mtime_ns, timestamp)
            self.assertEqual(image.read_bytes()[:4], b"DOS\x03")

    def test_directory_tree_copy_avoids_the_cli_for_an_ffs_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = DiskService(root / "work")
            source = service.create_blank("adf", "SOURCE")
            target = service.create_blank("ffs-intl", "TARGET")
            for name in ("ONE", "TWO"):
                host = root / name.lower()
                host.write_bytes(name.encode("ascii"))
                service.put(source, name, host)
            rows = service.list_ofs_catalogue_files(source)

            with patch.object(service, "_run", wraps=service._run) as run:
                service._copy_rows_to_ffs(
                    source, None, rows, target, "SOFTWARE"
                )

            self.assertEqual(run.call_count, 0)
            self.assertEqual(
                {
                    row["name"]
                    for row in service.list_directory(target, "SOFTWARE")["entries"]
                },
                {"ONE", "TWO"},
            )

    def test_ffs_browse_returns_capacity_without_the_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = DiskService(root / "work")
            session = service.create_blank("ffs", "BROWSE")
            service.make_directory(session, "$.Games")

            with patch.object(service, "_run", wraps=service._run) as run:
                listing = service.browse_directory(session, "$", None)

            self.assertEqual(run.call_count, 0)
            self.assertEqual([row["name"] for row in listing["entries"]], ["Games"])
            self.assertTrue(listing["capacity"]["available"])
            self.assertGreater(listing["capacity"]["free"], 0)

    def test_multiple_ofs_files_change_access_in_one_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = DiskService(root / "work")
            session = service.create_blank("adf", "ACCESS")
            first = root / "one.bin"
            second = root / "two.bin"
            first.write_bytes(b"one")
            second.write_bytes(b"two")
            service.put(session, "ONE", first)
            service.put(session, "TWO", second)

            updated = service.set_access(session, ["ONE", "TWO"],
                False,
            )

            self.assertEqual(updated, ["ONE", "TWO"])
            entries = service.list_directory(session, "", None)["entries"]
            # A protected entry shows neither the write nor the delete flag.
            self.assertTrue(all("w" not in row["attr"] for row in entries), entries)
            self.assertTrue(all("d" not in row["attr"] for row in entries), entries)

            service.set_access(session, ["ONE", "TWO"], True)
            entries = service.list_directory(session, "", None)["entries"]
            self.assertTrue(all("w" in row["attr"] for row in entries), entries)
            self.assertTrue(all("d" in row["attr"] for row in entries), entries)

    def test_multiple_ofs_files_delete_in_one_command(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = DiskService(root / "work")
            session = service.create_blank("adf", "DELETE")
            for name in ("ONE", "TWO", "KEEP"):
                host = root / f"{name.lower()}.bin"
                host.write_bytes(name.encode("ascii"))
                service.put(session, name, host)

            service.mutate(session, ["rm", "--force", "{image}:ONE", "{image}:TWO"],
            )

            names = {row["name"] for row in service.list_directory(session, "", None)["entries"]}
            self.assertEqual(names, {"KEEP"})

    def test_multiple_ffs_items_delete_in_one_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = DiskService(root / "work")
            session = service.create_blank("ffs", "DELETE")
            for name in ("ONE", "TWO", "KEEP"):
                host = root / f"{name.lower()}.bin"
                host.write_bytes(name.encode("ascii"))
                service.put(session, name, host)

            result = delete_ffs_items(service, session, ["ONE", "TWO"])

            self.assertEqual(len(result["deletedItems"]), 2)
            names = {row["name"] for row in service.list_directory(session, "", None)["entries"]}
            self.assertEqual(names, {"KEEP"})

    def test_host_folder_import_preserves_an_ffs_tree_in_one_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = DiskService(root / "work")
            session = service.create_blank("ffs", "FOLDERS")
            one = root / "one.bin"
            two = root / "two.bin"
            one.write_bytes(b"one")
            two.write_bytes(b"two")

            result = service.put_host_tree(
                session,
                "$",
                [
                    {
                        "targetPath": "Pack/One",
                        "hostPath": one,
                        "metadata": {"protection": "----r-e-", "comment": "Imported"},
                    },
                    {"targetPath": "Pack/Sub/Two", "hostPath": two},
                ],
                preserve_directories=True,
            )

            self.assertEqual(result["conflicts"], [])
            self.assertEqual(
                {row["name"] for row in service.list_directory(session, "Pack", None)["entries"]},
                {"One", "Sub"},
            )
            self.assertEqual(
                [row["name"] for row in service.list_directory(session, "Pack/Sub", None)["entries"]],
                ["Two"],
            )
            imported = next(
                row for row in service.list_directory(session, "Pack", None)["entries"]
                if row["name"] == "One"
            )
            self.assertEqual(imported["protection"], 0x05)
            self.assertEqual(imported["comment"], "Imported")

    def test_host_folder_import_reports_existing_files_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = DiskService(root / "work")
            session = service.create_blank("adf", "FOLDERS")
            old = root / "old.bin"
            new = root / "new.bin"
            old.write_bytes(b"old")
            new.write_bytes(b"new")
            service.put(session, "SAME", old)

            result = service.put_host_tree(
                session,
                "",
                [{"targetPath": "SAME", "hostPath": new}],
                preserve_directories=False,
            )

            self.assertEqual(result["conflicts"], ["SAME"])
            self.assertEqual(service.read_file(session, "SAME"), b"old")

    def test_an_empty_drawer_is_safe_to_reuse(self):
        empty_mount = types.SimpleNamespace(
            exists=lambda _path: True,
            stat=lambda _path: types.SimpleNamespace(is_dir=True),
            iter_entries=lambda _path: iter(()),
        )
        populated_mount = types.SimpleNamespace(
            exists=lambda _path: True,
            stat=lambda _path: types.SimpleNamespace(is_dir=True),
            iter_entries=lambda _path: iter([types.SimpleNamespace(name="Startup-Sequence")]),
        )
        file_mount = types.SimpleNamespace(
            exists=lambda _path: True,
            stat=lambda _path: types.SimpleNamespace(is_dir=False),
            iter_entries=lambda _path: iter(()),
        )
        missing_mount = types.SimpleNamespace(
            exists=lambda _path: False,
            stat=lambda _path: types.SimpleNamespace(is_dir=True),
            iter_entries=lambda _path: iter(()),
        )

        self.assertTrue(DiskService._is_empty_directory(empty_mount, "Empty"))
        self.assertFalse(DiskService._is_empty_directory(populated_mount, "Software"))
        self.assertFalse(DiskService._is_empty_directory(file_mount, "NotADrawer"))
        self.assertFalse(DiskService._is_empty_directory(missing_mount, "Missing"))

    def test_a_whole_volume_is_collected_under_one_destination_drawer(self):
        entries = {
            "": [
                types.SimpleNamespace(name="Startup-Sequence", path="Startup-Sequence", is_dir=False),
                types.SimpleNamespace(name="Games", path="Games", is_dir=True),
            ],
            "Games": [
                types.SimpleNamespace(name="Adventure", path="Games/Adventure", is_dir=False),
            ],
        }
        mount = types.SimpleNamespace(iter_entries=lambda path: iter(entries.get(path, [])))

        def file_item(_mount, source, destination):
            return {"kind": "file", "dst": destination, "src": source}

        items = DiskService._collect_ofs_catalogue_items(mount, "Disks/Disk0026", file_item)

        self.assertIn({"kind": "mkdir", "dst": "Disks/Disk0026/Games", "order": 0}, items)
        files = [item for item in items if item["kind"] == "file"]
        self.assertEqual(
            [(item["sourceName"], item["dst"]) for item in files],
            [
                ("Games/Adventure", "Disks/Disk0026/Games/Adventure"),
                ("Startup-Sequence", "Disks/Disk0026/Startup-Sequence"),
            ],
        )

    def test_collected_names_are_carried_across_intact(self):
        """A full stop is a legal Amiga name character, so it is not split."""
        entries = {
            "": [
                types.SimpleNamespace(name="Disk.info", path="Disk.info", is_dir=False),
                types.SimpleNamespace(name="My Drawer", path="My Drawer", is_dir=True),
            ],
            "My Drawer": [
                types.SimpleNamespace(name="Art.iff", path="My Drawer/Art.iff", is_dir=False),
            ],
        }
        mount = types.SimpleNamespace(iter_entries=lambda path: iter(entries.get(path, [])))

        def file_item(_mount, source, destination):
            return {"kind": "file", "dst": destination, "src": source}

        items = DiskService._collect_ofs_catalogue_items(mount, "Disk0034", file_item)

        self.assertIn({"kind": "mkdir", "dst": "Disk0034/My Drawer", "order": 0}, items)
        files = [item for item in items if item["kind"] == "file"]
        self.assertEqual(
            sorted(item["dst"] for item in files),
            ["Disk0034/Disk.info", "Disk0034/My Drawer/Art.iff"],
        )

    def test_an_extracted_startup_script_is_pointed_at_its_new_drawer(self):
        boot = b'Assign C: SYS:C\nCD :\nExecute :Haven\n'

        relocated = DiskService._relocate_ofs_boot_script(boot, "Games/Disks2/Disk0055")

        self.assertEqual(
            relocated,
            b"Assign C: Games/Disks2/Disk0055/C\nCD Games/Disks2/Disk0055\n"
            b"Execute Games/Disks2/Disk0055/Haven\n",
        )

if __name__ == "__main__":
    unittest.main()
