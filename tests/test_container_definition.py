from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ContainerDefinitionTests(unittest.TestCase):
    @staticmethod
    def _python_stages(dockerfile: str) -> tuple[int, int]:
        builder = dockerfile.index(" AS python-deps")
        builder = dockerfile.rfind("FROM python:", 0, builder)
        runtime = dockerfile.rindex("FROM python:")
        return builder, runtime

    def test_python_native_dependencies_are_built_outside_runtime_image(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        builder, runtime = self._python_stages(dockerfile)
        self.assertLess(builder, runtime)
        self.assertIn("build-essential", dockerfile[builder:runtime])
        self.assertIn("--root=/python-install", dockerfile[builder:runtime])
        self.assertIn('sysconfig.get_path("purelib")', dockerfile[builder:runtime])
        self.assertIn(
            "Staged Capstone 68000/68020/68040 support is available",
            dockerfile[builder:runtime],
        )
        runtime_definition = dockerfile[runtime:]
        self.assertIn("COPY --from=python-deps /python-install/usr/local /usr/local", runtime_definition)
        self.assertNotIn("/wheels", runtime_definition)
        self.assertNotIn("build-essential", runtime_definition)

    def test_the_runtime_image_carries_the_bundled_engine(self):
        """The engine ships in the repository, so it must reach the image."""
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        _builder, runtime = self._python_stages(dockerfile)
        runtime_definition = dockerfile[runtime:]
        self.assertIn("COPY amiganut ./amiganut", runtime_definition)
        self.assertIn("import amiganut", runtime_definition)

    def test_no_amiga_firmware_is_shipped_or_downloaded(self):
        """Kickstart is not redistributable, so no build step may fetch one."""
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        for forbidden in ("kick13", "kick31", "kickstart.rom", "amiga-os"):
            self.assertNotIn(forbidden, dockerfile.lower())
        firmware = ROOT / "firmware"
        self.assertEqual(
            sorted(path.name for path in firmware.iterdir()), ["README.md"]
        )

    def test_public_clone_instructions_do_not_require_a_github_key(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("git clone https://github.com/peteclarke-del/AmigaFileForge.git", readme)


if __name__ == "__main__":
    unittest.main()
