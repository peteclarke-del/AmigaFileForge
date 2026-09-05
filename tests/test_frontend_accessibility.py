import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def _luminance(colour: str) -> float:
    channels = [int(colour[offset:offset + 2], 16) / 255 for offset in (1, 3, 5)]
    linear = [
        channel / 12.92
        if channel <= 0.04045
        else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(first: str, second: str) -> float:
    light, dark = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def _tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\s*;", block))


class FrontendAccessibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = (STATIC / "index.html").read_text()
        cls.styles = (STATIC / "styles.css").read_text()
        cls.theme = (STATIC / "theme.css").read_text()
        cls.core = (STATIC / "core.js").read_text()
        cls.app = (STATIC / "app.js").read_text()
        light, dark = cls.theme.split('html[data-theme="dark"]', 1)
        cls.palettes = {"light": _tokens(light), "dark": _tokens(dark)}

    def test_theme_is_separate_from_component_styles(self):
        self.assertIn('href="/theme.css', self.index)
        self.assertNotRegex(self.styles, r"#[0-9a-fA-F]{3,8}|rgba?\(")
        self.assertNotIn('data-theme="dark"', self.styles)

    def test_text_contrast_meets_wcag_aa(self):
        for mode, palette in self.palettes.items():
            with self.subTest(mode=mode):
                for foreground in ("ink", "muted", "teal", "blue", "danger", "warning"):
                    self.assertGreaterEqual(
                        _contrast(palette[foreground], palette["paper"]),
                        4.5,
                        f"{mode} {foreground} on paper",
                    )
                for foreground, background in (
                    ("on-accent", "teal"),
                    ("on-accent", "blue"),
                    ("on-accent", "danger"),
                    ("on-accent", "orange"),
                    ("on-warning", "warning"),
                    ("on-yellow", "yellow"),
                    ("ink", "row-selected"),
                    ("format-icon-text", "yellow"),
                    ("hdf-format-text", "orange"),
                    ("ffs-format-text", "ffs-format"),
                    ("dms-format-text", "dms-format"),
                ):
                    self.assertGreaterEqual(
                        _contrast(palette[foreground], palette[background]),
                        4.5,
                        f"{mode} {foreground} on {background}",
                    )

    def test_control_boundaries_meet_non_text_contrast(self):
        for mode, palette in self.palettes.items():
            with self.subTest(mode=mode):
                self.assertGreaterEqual(_contrast(palette["line"], palette["paper"]), 3.0)
                self.assertGreaterEqual(
                    _contrast(palette["input-border"], palette["input-background"]),
                    3.0,
                )

    def test_core_accessibility_landmarks_are_present(self):
        self.assertIn('<html lang="en">', self.index)
        self.assertIn('class="skip-link"', self.index)
        self.assertIn('<main id="workspace"', self.index)
        self.assertIn('aria-live="polite"', self.index)
        self.assertIn('aria-live="assertive"', self.index)
        self.assertIn('aria-label="Amiga File Forge dialog"', self.index)
        self.assertIn(":focus-visible", self.styles)
        self.assertIn("prefers-reduced-motion: reduce", self.styles)
        self.assertIn('event.key !== "Tab"', self.core)
        self.assertIn("modalReturnFocus", self.core)
        self.assertIn('<button class="image-title" type="button"', self.app)
        self.assertNotIn('class="image-title" role="button"', self.app)

    def test_rom_decoder_initial_focus_does_not_select_a_command(self):
        self.assertIn(
            'class="modal-heading rom-decoder-heading" tabindex="-1" autofocus',
            self.app,
        )

    def test_deployment_assistant_has_labelled_controls_and_live_review(self):
        self.assertIn('class="deployment-review" aria-live="polite"', self.app)
        self.assertIn('name="deploymentTarget"', self.app)
        self.assertIn('data-plan-deployment', self.app)
        self.assertIn('data-download-deployment disabled', self.app)

    def test_cross_format_entry_points_share_the_preflight_service(self):
        self.assertIn('"cross-format-transfer"', self.app)
        self.assertIn('"file-menu-file-import"', self.app)
        self.assertIn('"file-menu-folder-import"', self.app)
        self.assertIn('"online-library-install"', self.app)
        self.assertIn("requestCompatibilityReport", self.app)

    def test_ffs_image_import_uses_its_specialised_planner_before_file_preflight(self):
        start = self.app.index("async function addSelectedHostFiles")
        end = self.app.index("async function addRomHostFiles", start)
        implementation = self.app[start:end]
        self.assertIn(
            'preparedFiles.filter(item => !formats.isImportableImage(item.file.name))',
            implementation,
        )
        self.assertIn(
            '"file-menu-file-import",',
            implementation,
        )
        self.assertIn(
            '"file-menu-file-import",',
            implementation,
        )

    def test_online_library_supports_complete_paging_and_actionable_preflight(self):
        self.assertIn("Find more downloadable results", self.app)
        self.assertIn("planOnlineDirectories", self.app)
        self.assertIn("existingDestination: !createDirectories", self.app)
        self.assertIn("acceptedOnlineDirectoryNames.get(itemId)", self.app)
        self.assertIn("Change selection or import options", self.app)
        self.assertNotIn('"Resolve compatibility findings"', self.app)

    def test_idle_job_history_does_not_poll_the_server_every_three_seconds(self):
        self.assertNotIn("setInterval(refreshJobsBadge, 3000)", self.app)
        self.assertIn("nextRefresh = active ? 3000 : 30000", self.app)
        self.assertIn('document.addEventListener("visibilitychange"', self.app)


if __name__ == "__main__":
    unittest.main()
