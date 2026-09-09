"""Reading a CD image, which is how AmigaOS 3.5, 3.9 and OS4 were published.

The interesting failures here are all about names and metadata rather than
about bytes. A disc publishes the same files under as many as three different
names, and reading it through the wrong tree gives an operator either
eight-and-three shouting or, worse, a listing that looks perfect and has
quietly dropped every protection bit and comment the disc recorded.

Each image is built in this tree by ``tests.iso_fixture`` rather than embedded,
so a failure names the field that broke.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.iso9660 import Iso9660Error, Iso9660Image, is_iso_bytes, is_iso_name
from tests.iso_fixture import amiga_entry, build_iso, directory, file, name_entry


class IsoReadingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def _open(self, tree, **options) -> Iso9660Image:
        path = self.root / "disc.iso"
        path.write_bytes(build_iso(tree, **options))
        image = Iso9660Image(path)
        self.addCleanup(image.close)
        return image

    def test_a_disc_lists_its_volume_and_contents(self) -> None:
        tree = directory("")
        tree.add(file("Disk.info", b"icon"))
        tree.add(directory("Extras"))

        iso = self._open(tree, volume="AmigaOS3.9")

        self.assertEqual(iso.volume, "AmigaOS3.9")
        self.assertEqual([e.name for e in iso.list_directory()], ["Extras", "Disk.info"])

    def test_drawers_are_listed_before_files(self) -> None:
        """A person browsing a disc wants its drawers first, as elsewhere."""
        tree = directory("")
        tree.add(file("aaa.txt", b"a"))
        tree.add(directory("zzz"))

        self.assertEqual(
            [e.name for e in self._open(tree).list_directory()], ["zzz", "aaa.txt"]
        )

    def test_the_version_suffix_is_not_part_of_the_name(self) -> None:
        """ISO writes "Disk.info;1"; nobody wants to read that."""
        tree = directory("")
        tree.add(file("Disk.info", b"icon"))

        self.assertEqual([e.name for e in self._open(tree).list_directory()], ["Disk.info"])

    def test_a_file_reads_back_exactly(self) -> None:
        payload = bytes(range(256)) * 40
        tree = directory("")
        tree.add(file("Startup-Sequence", payload))

        self.assertEqual(self._open(tree).read_file("Startup-Sequence"), payload)

    def test_an_empty_file_reads_as_no_bytes(self) -> None:
        tree = directory("")
        tree.add(file("Empty", b""))

        self.assertEqual(self._open(tree).read_file("Empty"), b"")

    def test_nested_drawers_are_reachable_by_path(self) -> None:
        tree = directory("")
        version = tree.add(directory("OS-Version3.9"))
        version.add(directory("Extras")).add(file("Tool", b"tool"))

        iso = self._open(tree)

        self.assertEqual(iso.read_file("OS-Version3.9/Extras/Tool"), b"tool")
        self.assertEqual(
            [e.path for e in iso.list_directory("OS-Version3.9/Extras")],
            ["OS-Version3.9/Extras/Tool"],
        )

    def test_a_walk_reports_every_entry_once(self) -> None:
        tree = directory("")
        first = tree.add(directory("One"))
        first.add(file("a", b"a"))
        second = tree.add(directory("Two"))
        second.add(directory("Deep")).add(file("b", b"b"))

        paths = sorted(entry.path for entry in self._open(tree).walk())

        self.assertEqual(paths, ["One", "One/a", "Two", "Two/Deep", "Two/Deep/b"])


class AmigaMetadataTests(unittest.TestCase):
    """The Amiga extension is the reason this reader is not a generic one.

    A loader that arrives without its ``e`` bit does not run, and the failure
    looks nothing like a missing permission, so a disc that records protection
    bits and comments must not have them dropped on the way in.
    """

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def _open(self, tree, **options) -> Iso9660Image:
        path = self.root / "disc.iso"
        path.write_bytes(build_iso(tree, **options))
        image = Iso9660Image(path)
        self.addCleanup(image.close)
        return image

    def test_protection_bits_are_read_from_the_amiga_entry(self) -> None:
        tree = directory("")
        tree.add(file("Loader", b"x", system_use=amiga_entry(protection=0x0002)))

        entry = self._open(tree).list_directory()[0]

        self.assertEqual(entry.protection, 0x0002)

    def test_a_file_comment_is_read_from_the_amiga_entry(self) -> None:
        tree = directory("")
        tree.add(file("Note", b"x", system_use=amiga_entry(comment="do not delete")))

        self.assertEqual(self._open(tree).list_directory()[0].comment, "do not delete")

    def test_both_travel_together(self) -> None:
        tree = directory("")
        tree.add(file(
            "Both", b"x", system_use=amiga_entry(protection=0x0005, comment="kept"),
        ))

        entry = self._open(tree).list_directory()[0]

        self.assertEqual((entry.protection, entry.comment), (0x0005, "kept"))

    def test_a_disc_without_the_extension_reports_no_protection(self) -> None:
        """Absent is not the same as zero, and must not be reported as it."""
        tree = directory("")
        tree.add(file("Plain", b"x"))

        self.assertIsNone(self._open(tree).list_directory()[0].protection)


class NamingSchemeTests(unittest.TestCase):
    """Which of a disc's three name schemes is read, and why it matters."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def _open(self, tree, **options) -> Iso9660Image:
        path = self.root / "disc.iso"
        path.write_bytes(build_iso(tree, **options))
        image = Iso9660Image(path)
        self.addCleanup(image.close)
        return image

    def test_rock_ridge_supplies_the_name_the_base_tree_shouts(self) -> None:
        tree = directory("")
        tree.add(file(
            "Emergency-Boot.info", b"x",
            iso_name="EMERGENC.INF",
            system_use=name_entry("Emergency-Boot.info"),
        ))

        self.assertEqual(
            [e.name for e in self._open(tree).list_directory()],
            ["Emergency-Boot.info"],
        )

    def test_the_amiga_tree_wins_over_joliet(self) -> None:
        """Reading through Joliet loses the Amiga metadata, so it does not.

        The AmigaOS 3.5 disc carries both. Preferring Joliet is the usual
        choice and it gave a listing that looked perfect while dropping the
        protection bits and comments on all six thousand of its files.
        """
        tree = directory("")
        tree.add(file(
            "Loader", b"x",
            system_use=name_entry("Loader") + amiga_entry(protection=0x0002, comment="kept"),
        ))

        iso = self._open(tree, joliet=True)
        entry = iso.list_directory()[0]

        self.assertFalse(iso.joliet)
        self.assertEqual(entry.name, "Loader")
        self.assertEqual(entry.protection, 0x0002)
        self.assertEqual(entry.comment, "kept")

    def test_joliet_is_used_when_the_base_tree_has_nothing_attached(self) -> None:
        """A disc mastered for other systems has no Rock Ridge to prefer."""
        tree = directory("")
        tree.add(file("LongMixedCaseName.txt", b"x", iso_name="LONGMIXE.TXT"))

        iso = self._open(tree, joliet=True)

        self.assertTrue(iso.joliet)
        self.assertEqual([e.name for e in iso.list_directory()], ["LongMixedCaseName.txt"])

    def test_a_second_primary_descriptor_does_not_change_the_tree(self) -> None:
        """The AmigaOS 3.9 disc carries two, which the standard does not describe."""
        tree = directory("")
        tree.add(file("Disk.info", b"icon"))

        iso = self._open(tree, volume="AmigaOS3.9", duplicate_primary=True)

        self.assertEqual(iso.volume, "AmigaOS3.9")
        self.assertEqual([e.name for e in iso.list_directory()], ["Disk.info"])


class MalformedDiscTests(unittest.TestCase):
    """A CD image is a file from somewhere else and is treated as one."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.addCleanup(self._temporary.cleanup)

    def test_a_file_that_is_not_a_disc_is_refused_with_a_reason(self) -> None:
        path = self.root / "not.iso"
        path.write_bytes(b"\x00" * (40 * 1024))

        with self.assertRaises(Iso9660Error) as raised:
            Iso9660Image(path)

        self.assertIn("not a CD image", str(raised.exception))

    def test_a_path_cannot_step_outside_the_disc(self) -> None:
        tree = directory("")
        tree.add(file("Disk.info", b"icon"))
        path = self.root / "disc.iso"
        path.write_bytes(build_iso(tree))

        with Iso9660Image(path) as iso:
            with self.assertRaises(Iso9660Error):
                iso.read_file("../../etc/passwd")

    def test_a_missing_path_says_so(self) -> None:
        tree = directory("")
        tree.add(file("Disk.info", b"icon"))
        path = self.root / "disc.iso"
        path.write_bytes(build_iso(tree))

        with Iso9660Image(path) as iso:
            with self.assertRaises(Iso9660Error) as raised:
                iso.read_file("Nowhere")
            self.assertIn("not on this disc", str(raised.exception))

    def test_listing_a_file_is_refused_rather_than_guessed_at(self) -> None:
        tree = directory("")
        tree.add(file("Disk.info", b"icon"))
        path = self.root / "disc.iso"
        path.write_bytes(build_iso(tree))

        with Iso9660Image(path) as iso:
            with self.assertRaises(Iso9660Error):
                iso.list_directory("Disk.info")


class RecognitionTests(unittest.TestCase):
    def test_a_disc_is_recognised_by_its_descriptor_not_its_name(self) -> None:
        tree = directory("")
        tree.add(file("Disk.info", b"icon"))
        self.assertTrue(is_iso_bytes(build_iso(tree)[:40 * 1024]))

    def test_a_short_prefix_cannot_claim_to_be_a_disc(self) -> None:
        """The identifier lives at sector sixteen, so a prefix cannot answer."""
        self.assertFalse(is_iso_bytes(b"CD001" * 10))

    def test_the_usual_extensions_are_recognised(self) -> None:
        self.assertTrue(is_iso_name("AmigaOS39.iso"))
        self.assertTrue(is_iso_name("disc.ISO"))
        self.assertFalse(is_iso_name("Workbench.adf"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
