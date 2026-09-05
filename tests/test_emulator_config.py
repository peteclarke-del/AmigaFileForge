from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask

from app.emulator_config import (
    KICKSTART_DIR,
    configured_emulator,
    emulator_command,
    emulator_status,
    kickstart_for,
    profile_machine,
)
from app.hardware_profiles import hardware_catalogue, normalise_hardware_profile
from app.operations import OperationRegistry
from app.disk_service import DiskService
from app.routes.images import create_images_blueprint
from app.routes.tools import clean_emulator_output, create_tools_blueprint


def _kickstart(path: Path):
    """Patch the Kickstart lookup so tests never depend on a real ROM."""
    return patch("app.emulator_config.kickstart_for", return_value=path)


class EmulatorConfigurationTests(unittest.TestCase):
    KICK = Path("/roms/kick31.rom")

    def test_every_machine_defaults_to_fs_uae(self):
        for machine in ("a500", "a600", "a1200", "a4000", "cd32"):
            with self.subTest(machine=machine):
                session = SimpleNamespace(
                    hardware_profile={"machine": machine, "emulator": "auto"},
                    target_hardware="auto",
                )
                self.assertEqual(profile_machine(session), machine)
                self.assertEqual(configured_emulator(session).identifier, "fs-uae")

    def test_the_target_machine_is_inferred_when_no_profile_is_set(self):
        session = SimpleNamespace(hardware_profile={}, target_hardware="a500-ofs")
        self.assertEqual(profile_machine(session), "a500")
        session = SimpleNamespace(hardware_profile={}, target_hardware="amigaos")
        self.assertEqual(profile_machine(session), "a4000")

    @patch("app.emulator_config.Path.is_file", return_value=True)
    def test_fs_uae_attaches_a_floppy_and_names_the_kickstart(self, _available):
        session = SimpleNamespace(
            hardware_profile={"machine": "a1200", "emulator": "auto", "emulatorBoot": "boot"},
            target_hardware="a1200-ffs",
        )
        with _kickstart(self.KICK):
            command, cwd = emulator_command(session, "/work/game.adf")
        self.assertIn("--amiga_model=A1200", command)
        self.assertIn(f"--kickstart_file={self.KICK}", command)
        self.assertIn("--floppy_drive_0=/work/game.adf", command)
        self.assertNotIn("--hard_drive_0=/work/game.adf", command)
        self.assertTrue(cwd)

    @patch("app.emulator_config.Path.is_file", return_value=True)
    def test_fs_uae_attaches_a_whole_drive_as_a_hard_drive(self, _available):
        session = SimpleNamespace(
            hardware_profile={"machine": "a1200", "emulator": "auto"},
            target_hardware="amigaos",
        )
        with _kickstart(self.KICK):
            command, _cwd = emulator_command(session, "/work/library.hdf")
        self.assertIn("--hard_drive_0=/work/library.hdf", command)
        self.assertNotIn("--floppy_drive_0=/work/library.hdf", command)

    @patch("app.emulator_config.Path.is_file", return_value=True)
    def test_memory_additions_reach_the_fs_uae_command(self, _available):
        session = SimpleNamespace(
            hardware_profile={
                "machine": "a1200", "emulator": "auto",
                "addons": ["chip-2048", "fast-ram"],
            },
            target_hardware="a1200-ffs",
        )
        with _kickstart(self.KICK):
            command, _cwd = emulator_command(session, "/work/game.adf")
        self.assertIn("--chip_memory=2048", command)
        self.assertIn("--fast_memory=8192", command)

    @patch("app.emulator_config.Path.is_file", return_value=True)
    def test_an_accelerator_upgrades_the_fs_uae_model(self, _available):
        session = SimpleNamespace(
            hardware_profile={
                "machine": "a1200", "emulator": "auto", "addons": ["acc-68040"],
            },
            target_hardware="a1200-ffs",
        )
        with _kickstart(self.KICK):
            command, _cwd = emulator_command(session, "/work/game.adf")
        self.assertIn("--amiga_model=A4000/040", command)

    @patch("app.emulator_config.Path.is_file", return_value=True)
    def test_a_missing_kickstart_is_reported_rather_than_guessed(self, _available):
        session = SimpleNamespace(
            hardware_profile={"machine": "a1200", "emulator": "auto"},
            target_hardware="a1200-ffs",
        )
        with patch("app.emulator_config.kickstart_for", return_value=None):
            status = emulator_status(session)
            self.assertFalse(status["available"])
            self.assertIn("Kickstart", status["message"])
            with self.assertRaisesRegex(ValueError, "No Kickstart ROM"):
                emulator_command(session, "/work/game.adf")

    def test_the_kickstart_directory_is_the_only_place_roms_are_read_from(self):
        """No firmware is shipped, so the lookup must be user-supplied only."""
        self.assertIn("kickstart", str(KICKSTART_DIR).lower())
        with tempfile.TemporaryDirectory() as temporary:
            with patch("app.emulator_config.KICKSTART_DIR", Path(temporary)):
                self.assertIsNone(kickstart_for("a1200"))
                rom = Path(temporary) / "kick31.rom"
                rom.write_bytes(b"\0" * 16)
                self.assertEqual(kickstart_for("a1200"), rom)

    @patch("app.emulator_config.Path.is_file", return_value=True)
    def test_interactive_sessions_use_the_shared_browser_display(self, _available):
        session = SimpleNamespace(
            hardware_profile={"machine": "a500", "emulator": "auto"},
            target_hardware="auto",
        )
        with _kickstart(self.KICK):
            command, _cwd = emulator_command(session, "/work/game.adf", interactive=True)
        self.assertIn("DISPLAY=:99", command)
        self.assertNotIn("xvfb-run", command)
        self.assertEqual(command[3], "900")

    @patch("app.emulator_config.Path.is_file", return_value=True)
    def test_a_native_interactive_session_uses_the_host_display(self, _available):
        session = SimpleNamespace(
            hardware_profile={"machine": "a500", "emulator": "auto"},
            target_hardware="auto",
        )
        with _kickstart(self.KICK):
            command, _cwd = emulator_command(
                session, "/work/game.adf", interactive=True, native=True
            )
        self.assertNotIn("DISPLAY=:99", command)
        self.assertNotIn("timeout", command)

    @patch("app.emulator_config.Path.is_file", return_value=True)
    def test_a_container_that_cannot_be_attached_is_refused(self, _available):
        session = SimpleNamespace(
            hardware_profile={"machine": "a500", "emulator": "auto"},
            target_hardware="auto",
        )
        with _kickstart(self.KICK):
            with self.assertRaisesRegex(ValueError, "floppy image"):
                emulator_command(session, "/work/kick31.rom")

    def test_expected_headless_shutdown_noise_is_removed(self):
        output = "\n".join([
            "FS-UAE 3.1.66",
            "ALSA lib confmisc.c:855:(parse_card) cannot find card '0'",
            "Using Kickstart 40.68",
            "X connection to :99 broken (explicit kill or server shutdown).",
        ])
        self.assertEqual(
            clean_emulator_output(output),
            "\n".join(["FS-UAE 3.1.66", "Using Kickstart 40.68"]),
        )


class HardwareProfileTests(unittest.TestCase):
    def test_hardware_catalogue_has_the_common_machine_families(self):
        machines = {row["id"] for row in hardware_catalogue()["machines"]}
        self.assertEqual(
            machines,
            {"a500", "a500plus", "a600", "a1200", "a2000", "a3000", "a4000", "cd32"},
        )

    def test_a_profile_rejects_incompatible_and_conflicting_additions(self):
        with self.assertRaisesRegex(ValueError, "cannot be fitted"):
            normalise_hardware_profile({"machine": "a500", "addons": ["ide-internal"]})
        with self.assertRaisesRegex(ValueError, "cannot be fitted with"):
            normalise_hardware_profile({"machine": "a500", "addons": ["kick13", "kick31"]})

    def test_whdload_requires_fast_ram(self):
        """WHDLoad relocates a game into Fast RAM, so it is not optional."""
        with self.assertRaisesRegex(ValueError, "requires Fast RAM"):
            normalise_hardware_profile({"machine": "a1200", "addons": ["whdload"]})
        profile = normalise_hardware_profile(
            {"machine": "a1200", "addons": ["fast-ram", "whdload"]}
        )
        self.assertIn("whdload", profile["addons"])

    def test_an_fpu_requires_a_processor_that_can_carry_one(self):
        with self.assertRaisesRegex(ValueError, "requires"):
            normalise_hardware_profile({"machine": "a1200", "addons": ["fpu-68882"]})
        profile = normalise_hardware_profile(
            {"machine": "a1200", "addons": ["acc-68030", "fpu-68882"]}
        )
        self.assertTrue(profile["accelerated"])

    def test_only_one_processor_may_be_fitted(self):
        """The group cap is checked before the conflict list, so it reports first."""
        with self.assertRaisesRegex(ValueError, "no more than 1 option"):
            normalise_hardware_profile(
                {"machine": "a1200", "addons": ["acc-68030", "acc-68040"]}
            )

    def test_a_kickstart_remap_requires_fast_ram_to_copy_into(self):
        with self.assertRaisesRegex(ValueError, "requires Fast RAM"):
            normalise_hardware_profile(
                {"machine": "a1200", "addons": ["kickstart-remap"]}
            )

    def test_pcmcia_storage_is_offered_only_to_the_machines_that_have_a_slot(self):
        catalogue = hardware_catalogue()
        pcmcia = next(row for row in catalogue["addons"] if row["id"] == "pcmcia-sram")
        self.assertEqual(set(pcmcia["machines"]), {"a600", "a1200"})


class EmulatorRouteTests(unittest.TestCase):
    def test_editor_status_uses_the_managed_profile(self):
        service = Mock()
        service.get.return_value = SimpleNamespace(
            hardware_profile={"machine": "a1200", "emulator": "fs-uae"},
            target_hardware="a1200-ffs", path=Path("/work/test.adf"),
        )
        app = Flask(__name__)
        app.register_blueprint(create_tools_blueprint(service, OperationRegistry()))
        with patch(
            "app.routes.tools.emulator_status",
            return_value={
                "available": True,
                "label": "FS-UAE",
                "configuredBy": "managed workbench profile",
            },
        ), patch(
            "app.routes.tools.emulator_command",
            return_value=(["/usr/bin/fs-uae", "--amiga_model=A1200"], "/app"),
        ):
            result = app.test_client().get("/api/images/test/editor-emulator").get_json()
        self.assertTrue(result["available"])
        self.assertEqual(result["command"], "/usr/bin/fs-uae --amiga_model=A1200")
        self.assertEqual(result["configuredBy"], "managed workbench profile")

    def test_editor_status_uses_the_effective_workbench_profile(self):
        service = Mock()
        service.get.return_value = SimpleNamespace(
            hardware_profile={"machine": "a500", "emulator": "fs-uae"},
            target_hardware="auto", path=Path("/work/test.adf"),
        )
        app = Flask(__name__)
        app.register_blueprint(create_tools_blueprint(service, OperationRegistry()))
        profile = {"machine": "a1200", "emulator": "fs-uae", "addons": []}
        with patch("app.emulator_config.Path.is_file", return_value=True), _kickstart(
            Path("/roms/kick31.rom")
        ):
            result = app.test_client().get(
                "/api/images/test/editor-emulator",
                query_string={"hardwareProfile": json.dumps(profile), "basic": "true"},
            ).get_json()
        self.assertEqual(result["id"], "fs-uae")
        self.assertEqual(result["machine"], "a1200")

    def test_a_drive_run_attaches_the_whole_drive(self):
        """A hard drive is handed to the emulator entire, not partition by partition."""
        with tempfile.TemporaryDirectory() as temporary:
            service = DiskService(temporary)
            drive = service.create_blank("ffs-hard", "Collection", capacity="4MB")
            app = Flask(__name__)
            app.register_blueprint(create_tools_blueprint(service, OperationRegistry()))
            with patch("app.routes.tools.run_emulator_process") as run, patch(
                "app.emulator_config.Path.is_file", return_value=True
            ), _kickstart(Path("/roms/kick13.rom")):
                run.return_value = SimpleNamespace(returncode=124, stdout="", stderr="")
                response = app.test_client().post(
                    f"/api/images/{drive.id}/editor-emulator",
                    json={
                        "path": "", "mode": "whole-drive-mount",
                        "hardwareProfile": {
                            "machine": "a500", "emulator": "fs-uae", "addons": [],
                        },
                    },
                )
                command = run.call_args.args[0]
            attached = next(
                argument for argument in command
                if str(argument).startswith("--hard_drive_0=")
            )
            media = Path(attached.split("=", 1)[1])
            self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
            self.assertFalse(media.exists())

    def test_hardware_profile_retains_only_bounded_managed_choices(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        service = Mock()
        session = SimpleNamespace(kind="ofs", hardware_profile={}, target_hardware="auto")
        service.get.return_value = session
        service.summary.return_value = {
            "id": "test", "kind": "ofs", "hardwareProfile": session.hardware_profile,
        }
        app = Flask(__name__)
        app.register_blueprint(
            create_images_blueprint(service, Path(temporary.name), OperationRegistry())
        )
        response = app.test_client().patch(
            "/api/images/test/hardware-profile",
            json={
                "name": "Test profile", "machine": "a1200", "filingSystem": "ffs-intl",
                "addons": ["kick31", "fast-ram"],
                "emulator": "fs-uae", "debugger": "fs-uae-debug",
                "emulatorRam": "2M", "emulatorBoot": "boot",
                "fileEmulatorCommand": "/untrusted/tool {file}",
            },
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertEqual(session.hardware_profile["emulator"], "fs-uae")
        self.assertEqual(session.hardware_profile["debugger"], "fs-uae-debug")
        self.assertEqual(session.hardware_profile["addons"], ["kick31", "fast-ram"])
        self.assertNotIn("fileEmulatorCommand", session.hardware_profile)

    def test_hardware_profile_catalogue_endpoint(self):
        app = Flask(__name__)
        app.register_blueprint(
            create_images_blueprint(Mock(), Path("/tmp"), OperationRegistry())
        )
        data = app.test_client().get("/api/hardware-profiles").get_json()
        self.assertIn("machines", data)
        self.assertTrue(any(row["id"] == "a1200" for row in data["machines"]))


if __name__ == "__main__":
    unittest.main()
