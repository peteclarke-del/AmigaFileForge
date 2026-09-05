import inspect
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.hardfile_geometry import block_checksum, descriptor_size, volume_extent
from app.ffs_install_service import FFSInstallMixin
from app.disk_identity import analyse_directory
from app.ffs_items import delete_ffs_items, move_ffs_items
from app.rdb_service import RdbPartitionMixin
from app.disk_service import DiskService
from app.flux_containers import FLUX_CONTAINERS
from app.filesystem_disk_service import FilesystemDiskMixin
from app.rom_disk_service import RomDiskMixin
from app.session_disk_service import SessionDiskMixin
from app.dms_disk_service import DMSDiskMixin


class ComponentBoundaryTests(unittest.TestCase):
    def test_partition_reading_is_owned_by_the_rdb_component(self):
        self.assertTrue(issubclass(DiskService, RdbPartitionMixin))
        self.assertNotIn("list_partitions", DiskService.__dict__)
        self.assertIs(DiskService.list_partitions, RdbPartitionMixin.list_partitions)

    def test_hardfile_geometry_helpers_are_pure(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            descriptor_path = root / "drive.hda.geo"
            descriptor_path.write_text(
                "surfaces=2\nblockspertrack=32\ncylinders=80\nblocksize=512\n"
            )
            self.assertEqual(descriptor_size(descriptor_path), 80 * 2 * 32 * 512)

            # A volume's root block sits at the midpoint of its block count, so
            # finding it is the same as measuring the volume.
            blocks = 640
            image = bytearray(blocks * 512)
            image[0:4] = b"DOS\x03"
            root_block = blocks // 2
            base = root_block * 512
            image[base : base + 4] = (2).to_bytes(4, "big")
            image[base + 508 : base + 512] = (1).to_bytes(4, "big")
            checksum = block_checksum(bytes(image[base : base + 512]))
            image[base + 20 : base + 24] = checksum.to_bytes(4, "big")
            map_path = root / "drive.hda"
            map_path.write_bytes(bytes(image))
            self.assertEqual(volume_extent(map_path), blocks * 512)

    def test_disk_identity_and_item_moves_have_one_home_each(self):
        """Neither is a service method, so neither can drift into two copies."""
        self.assertTrue(callable(analyse_directory))
        self.assertTrue(callable(move_ffs_items))
        self.assertTrue(callable(delete_ffs_items))
        routes = Path(__file__).parents[1] / "app" / "routes"
        offenders = [
            path.name
            for path in routes.glob("*.py")
            if "def analyse_directory" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [])

    def test_dms_operations_are_owned_by_the_dms_component(self):
        self.assertTrue(issubclass(DiskService, DMSDiskMixin))
        self.assertNotIn("convert_dms", DiskService.__dict__)
        self.assertIs(DiskService.convert_dms, DMSDiskMixin.convert_dms)

    def test_rom_operations_are_owned_by_the_rom_component(self):
        self.assertTrue(issubclass(DiskService, RomDiskMixin))
        self.assertNotIn("put_rom_bank", DiskService.__dict__)
        self.assertIs(DiskService.put_rom_bank, RomDiskMixin.put_rom_bank)

    def test_session_operations_are_owned_by_the_session_component(self):
        self.assertTrue(issubclass(DiskService, SessionDiskMixin))
        self.assertNotIn("recoverable_sessions", DiskService.__dict__)
        self.assertIs(DiskService.recoverable_sessions, SessionDiskMixin.recoverable_sessions)

    def test_filesystem_mounts_are_owned_by_the_filesystem_component(self):
        self.assertTrue(issubclass(DiskService, FilesystemDiskMixin))
        self.assertNotIn("ffs_mount", DiskService.__dict__)
        self.assertIs(DiskService.ffs_mount, FilesystemDiskMixin.ffs_mount)
        self.assertIs(DiskService.kickfs_details, FilesystemDiskMixin.kickfs_details)

    def test_ffs_installation_audit_is_owned_by_its_component(self):
        self.assertTrue(issubclass(DiskService, FFSInstallMixin))
        self.assertNotIn("audit_ffs_installations", DiskService.__dict__)
        self.assertIs(DiskService.audit_ffs_installations, FFSInstallMixin.audit_ffs_installations)

    def test_byte_checksums_have_one_canonical_implementation(self):
        app = Path(__file__).parents[1] / "app"
        offenders = [
            path.relative_to(app).as_posix()
            for path in app.rglob("*.py")
            if path.name != "checksum.py"
            and "hashlib.sha256" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [])

    def test_flux_geometry_rules_have_one_canonical_definition(self):
        """HFE and SCP drifted apart once; the shared module is what prevents it."""
        app = Path(__file__).parents[1] / "app"
        offenders = [
            path.relative_to(app).as_posix()
            for path in app.rglob("*.py")
            if path.name != "flux_containers.py"
            and any(
                layout in path.read_text(encoding="utf-8")
                for layout in ("AMIGA_DD_880K", "AMIGA_HD_1760K")
            )
        ]
        self.assertEqual(offenders, [])

    def test_both_flux_containers_share_one_save_implementation(self):
        self.assertIsNot(
            DiskService._prepare_hfe_download,
            DiskService._prepare_scp_download,
        )
        for container in ("hfe", "scp"):
            with self.subTest(container=container):
                source = inspect.getsource(
                    getattr(DiskService, f"_prepare_{container}_download")
                )
                self.assertIn("_prepare_flux_download", source)

    def test_the_hxcfe_conversion_plugins_are_declared_once(self):
        self.assertEqual(
            {identifier: container.plugin for identifier, container in FLUX_CONTAINERS.items()},
            {"hfe": "HXC_HFE", "scp": "SCP_FLUX_STREAM"},
        )
        app = Path(__file__).parents[1] / "app"
        offenders = [
            path.relative_to(app).as_posix()
            for path in app.rglob("*.py")
            if path.name != "flux_containers.py"
            and "SCP_FLUX_STREAM" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
