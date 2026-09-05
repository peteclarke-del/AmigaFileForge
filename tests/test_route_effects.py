from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.routes.effects import (
    ImageMutation,
    effect_for,
    image_mutation,
    mutation_for,
)
try:
    from app.server import create_app
except ModuleNotFoundError:  # Flask is intentionally absent from the light host test env.
    create_app = None


class RouteEffectTests(unittest.TestCase):
    def test_image_mutation_metadata_stays_on_the_registered_view(self) -> None:
        @image_mutation("changing a test image", target="targetImage")
        def view():
            return None

        self.assertEqual(
            mutation_for(view),
            ImageMutation("changing a test image", target="targetImage"),
        )

    @unittest.skipIf(create_app is None, "Flask is available in the application container")
    def test_every_checkpointed_route_owns_its_metadata(self) -> None:
        required = {
            "catalog.install",
            "files.append_blank_rom_bank", "files.create_empty_file",
            "files.delete", "files.extract_to_directory", "files.lock",
            "files.mkdir", "files.move_ofs_items", "files.move_items",
            "files.move_rom_banks", "files.put_file", "files.put_folder",
            "files.rename", "files.save_archive_inspect", "files.transfer",
            "files.transfer_image_to_directory", "hex_editor.write_file_hex",
            "hex_editor.write_hex", "images.compact", "images.configure_rom_layout",
            "images.configure_kickfs", "images.prepare_image_download",
            "images.rename_image", "images.set_hardware_profile",
            "rom_tools.rom_build", "rom_tools.rom_patch", "rom_tools.rom_project",
            "rom_tools.rom_repair",
            "tools.apply_image_patch",
            "tools.repair_ffs_installations",
            "tools.save_editor_project", "tools.save_inspected_properties",
            "tools.save_inspected_text",
        }
        with tempfile.TemporaryDirectory() as folder, patch(
            "app.server.WORK_DIR", Path(folder)
        ):
            application = create_app()

        missing = sorted(
            endpoint for endpoint in required
            if mutation_for(application.view_functions.get(endpoint)) is None
        )
        self.assertEqual(missing, [])

        transfers = {"files.transfer", "files.transfer_image_to_directory"}
        for endpoint in transfers:
            self.assertEqual(
                mutation_for(application.view_functions[endpoint]).target,
                "targetImage",
            )

    @unittest.skipIf(create_app is None, "Flask is available in the application container")
    def test_every_unsafe_route_explicitly_classifies_its_effect(self) -> None:
        with tempfile.TemporaryDirectory() as folder, patch(
            "app.server.WORK_DIR", Path(folder)
        ):
            application = create_app()

        unsafe = {"POST", "PUT", "PATCH", "DELETE"}
        missing = sorted({
            rule.endpoint
            for rule in application.url_map.iter_rules()
            if rule.methods & unsafe
            and effect_for(application.view_functions.get(rule.endpoint)) is None
        })
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
