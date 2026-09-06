import re
import unittest
from pathlib import Path
from xml.etree import ElementTree

from app.version import application_version

ROOT = Path(__file__).resolve().parents[1]
METAINFO = ROOT / "packaging/linux/uk.co.amigafileforge.AmigaFileForge.metainfo.xml"


class VersionTests(unittest.TestCase):
    def test_packaged_version_matches_stable_release(self):
        self.assertEqual(application_version(), "1.2.0")


class ReleaseRecordTests(unittest.TestCase):
    """A release must carry its own notes and AppStream entry.

    Bumping `VERSION` alone produces a build that installs and reports the new
    number while the desktop software centre still describes the previous
    release and the documentation index still points at its notes. Nothing in
    the package build fails in that case, so the omission is only visible to a
    user reading the release history.
    """

    def test_the_newest_appstream_entry_is_this_release(self):
        releases = ElementTree.parse(METAINFO).getroot().find("releases")
        self.assertIsNotNone(releases, "the metainfo file declares no releases")
        versions = [entry.get("version") for entry in releases.findall("release")]
        self.assertEqual(versions[0], application_version())
        self.assertEqual(
            len(versions),
            len(set(versions)),
            f"a version is listed twice in the AppStream history: {versions}",
        )

    def test_this_release_has_notes_linked_from_the_documentation_index(self):
        notes = ROOT / "docs/releases" / f"{application_version()}.md"
        self.assertTrue(notes.is_file(), f"{notes} is missing")
        self.assertTrue(
            notes.read_text(encoding="utf-8").startswith(
                f"# Amiga File Forge {application_version()}\n"
            ),
            "the notes do not open with a heading naming this release",
        )
        index = (ROOT / "docs/README.md").read_text(encoding="utf-8")
        self.assertIn(f"releases/{application_version()}.md", index)

    def test_no_document_still_advertises_a_superseded_release(self):
        current = application_version()
        # A package filename embeds the version after an underscore, which is a
        # word character, so \b would not see a boundary there. A trailing
        # "-rc" marks a pre-release named in historical prose, not a claim
        # about the current release.
        pattern = re.compile(r"(?<![\d.])\d+\.\d+\.\d+(?![\d.])(?!-rc)")
        superseded = {
            path.stem
            for path in (ROOT / "docs/releases").glob("*.md")
            if path.stem != current
        }
        stale: list[str] = []
        for path in [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]:
            for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                found = set(pattern.findall(line)) & superseded
                if found:
                    stale.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
        self.assertEqual(stale, [], "\n".join(["stale version references:", *stale]))


if __name__ == "__main__":
    unittest.main()
