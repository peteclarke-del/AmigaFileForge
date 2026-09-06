"""Installing a disc, as opposed to copying one, has to be checked end to end.

These tests build real images through the public service API and read the
results back the same way, because every interesting failure in this area is
one where the files are present and the thing still does not run: a lost
protection bit, a second disc that quietly replaced the first, a WHDLoad
installed over the operator's own preferences.

The WHDLoad archive is built rather than downloaded. Its layout is the part
that matters, and building it means the tests say what the code depends on
instead of depending on a network and a release that changes.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app import whdload
from app.disk_service import DiskError, DiskService
from app.install_service import slugify
from tests.lha_fixture import archive, level1_member


def whdload_archive(version: str = "20.0", *, omit: str = "") -> bytes:
    """A WHDLoad_usr.lha with the layout the installer relies on."""
    members = []
    for name in list(whdload.PROGRAM_FILES) + list(whdload.SCRIPT_FILES) + [whdload.PREFERENCES_FILE]:
        if name == omit:
            continue
        body = f"$VER: {Path(name).name} {version} [build 1] (01.01.2026)".encode("latin-1")
        members.append(level1_member(whdload.archive_path(name), body + b"\x00" * 8))
    return archive(*members)


class StagingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.service = DiskService(self.root / "work")
        self.addCleanup(self._temporary.cleanup)

    def _floppy(self, name: str, *, payload: bytes, protection: str = "----rwed", comment: str = "") -> object:
        floppy = self.service.create_blank("adf", name)
        self.service.make_directory(floppy, "s")
        loader = self.root / f"loader-{name}"
        loader.write_bytes(b"\x00\x00\x03\xf3loader")
        self.service.put(floppy, "Loader", loader, protection=protection, comment=comment or None)
        data = self.root / f"data-{name}"
        data.write_bytes(payload)
        self.service.put(floppy, "Shared.dat", data)
        sequence = self.root / f"seq-{name}"
        sequence.write_bytes(b"Loader\n")
        self.service.put(floppy, "s/Startup-Sequence", sequence)
        return floppy

    def test_a_multi_disc_set_stages_into_one_tree(self) -> None:
        """Two discs of one title are one thing to install, not two."""
        first = self._floppy("GAME1", payload=b"IDENTICAL")
        second = self._floppy("GAME2", payload=b"IDENTICAL")

        self.service.stage_disk(first, "Hyper Sports", disc_label="Disk 1")
        staged = self.service.stage_disk(second, "Hyper Sports", disc_label="Disk 2")

        self.assertEqual(staged["discCount"], 2)
        self.assertEqual([disc["volume"] for disc in staged["discs"]], ["GAME1", "GAME2"])
        payload = Path(staged["path"])
        self.assertTrue((payload / "Loader").is_file())
        self.assertTrue((payload / "s" / "Startup-Sequence").is_file())
        # Identical files across discs are stored once, not twice.
        self.assertEqual(staged["conflicts"], [])

    def test_two_discs_carrying_different_files_under_one_name_both_survive(self) -> None:
        """Keeping only the last disc would silently destroy half the set."""
        self.service.stage_disk(self._floppy("GAME1", payload=b"LEVEL ONE"), "Title", disc_label="Disk 1")
        staged = self.service.stage_disk(
            self._floppy("GAME2", payload=b"LEVEL TWO, LONGER"), "Title", disc_label="Disk 2"
        )

        self.assertEqual(len(staged["conflicts"]), 1)
        conflict = staged["conflicts"][0]
        self.assertEqual(conflict["path"], "Shared.dat")
        self.assertEqual(conflict["alsoIn"], "Disk 2")
        kept = Path(staged["path"]) / "Shared.dat"
        spare = Path(staged["path"]).parent / conflict["storedAs"]
        self.assertEqual(kept.read_bytes(), b"LEVEL ONE")
        self.assertEqual(spare.read_bytes(), b"LEVEL TWO, LONGER")

    def test_protection_bits_and_comments_survive_the_round_trip(self) -> None:
        """A loader that loses its ``e`` bit will not start, and looks fine."""
        floppy = self._floppy("GAME", payload=b"data", protection="----rw-d", comment="do not delete")
        staged = self.service.stage_disk(floppy, "Protected Title")
        drive = self.service.create_blank("ffs-hard", "SYSTEM", "40MB")
        self.service.select_partition(drive, 0)

        result = self.service.install_staged_title(drive, staged["slug"], parent="Games")

        entries = {
            row["name"]: row
            for row in self.service.list_directory(drive, result["path"])["entries"]
        }
        self.assertEqual(entries["Loader"]["comment"], "do not delete")
        self.assertEqual(
            self.service.file_metadata(drive, f"{result['path']}/Loader")["protection"],
            self.service.file_metadata(floppy, "Loader")["protection"],
        )

    def test_a_staged_title_installs_with_its_drawers_intact(self) -> None:
        staged = self.service.stage_disk(self._floppy("GAME", payload=b"data"), "Nested Title")
        drive = self.service.create_blank("ffs-hard", "SYSTEM", "40MB")
        self.service.select_partition(drive, 0)

        result = self.service.install_staged_title(drive, staged["slug"], parent="Games")

        self.assertEqual(result["path"], "Games/Nested Title")
        self.assertEqual(
            self.service.read_file(drive, "Games/Nested Title/s/Startup-Sequence"), b"Loader\n"
        )

    def test_restaging_a_disc_replaces_it_rather_than_adding_another(self) -> None:
        """A set that grew every time it was corrected could not be reasoned about."""
        self.service.stage_disk(self._floppy("GAME1", payload=b"one"), "Title", disc_label="Disk 1")
        self.service.stage_disk(self._floppy("GAME2", payload=b"two"), "Title", disc_label="Disk 2")

        staged = self.service.stage_disk(
            self._floppy("GAME1B", payload=b"one"), "Title", disc_label="Disk 1"
        )

        self.assertEqual(staged["discCount"], 2)
        self.assertEqual([disc["label"] for disc in staged["discs"]], ["Disk 1", "Disk 2"])
        self.assertEqual(staged["discs"][0]["volume"], "GAME1B")

    def test_restaging_a_corrected_disc_overwrites_it_instead_of_filing_it_aside(self) -> None:
        """Filing the correction as an alternate would leave the bad file in place."""
        self.service.stage_disk(self._floppy("GAME1", payload=b"BROKEN"), "Title", disc_label="Disk 1")

        staged = self.service.stage_disk(
            self._floppy("GAME1FIXED", payload=b"CORRECTED"), "Title", disc_label="Disk 1"
        )

        self.assertEqual(staged["conflicts"], [])
        self.assertEqual((Path(staged["path"]) / "Shared.dat").read_bytes(), b"CORRECTED")
        self.assertFalse((Path(staged["path"]).parent / "alternates" / "disk-1").exists())

    def test_a_conflict_stops_being_reported_once_the_disc_behind_it_is_restaged(self) -> None:
        self.service.stage_disk(self._floppy("GAME1", payload=b"ONE"), "Title", disc_label="Disk 1")
        conflicted = self.service.stage_disk(
            self._floppy("GAME2", payload=b"TWO"), "Title", disc_label="Disk 2"
        )
        self.assertEqual(len(conflicted["conflicts"]), 1)

        resolved = self.service.stage_disk(
            self._floppy("GAME2AGAIN", payload=b"ONE"), "Title", disc_label="Disk 2"
        )

        self.assertEqual(resolved["conflicts"], [])

    def test_an_unlabelled_disc_takes_the_first_free_slot(self) -> None:
        self.service.stage_disk(self._floppy("GAME1", payload=b"one"), "Title")
        staged = self.service.stage_disk(self._floppy("GAME2", payload=b"two"), "Title")
        self.assertEqual([disc["label"] for disc in staged["discs"]], ["Disc 1", "Disc 2"])

    def test_a_staged_title_can_be_listed_and_discarded(self) -> None:
        self.service.stage_disk(self._floppy("GAME", payload=b"data"), "Listed Title")
        self.assertEqual([row["title"] for row in self.service.staged_titles()], ["Listed Title"])

        self.service.discard_staged_title("listed-title")

        self.assertEqual(self.service.staged_titles(), [])
        with self.assertRaises(DiskError):
            self.service.discard_staged_title("listed-title")

    def test_a_title_name_becomes_a_directory_every_filesystem_accepts(self) -> None:
        """Staged trees end up on FAT cards and Amiga volumes, not only here."""
        self.assertEqual(slugify("Hyper Sports"), "hyper-sports")
        self.assertEqual(slugify("Turrican II: The Final Fight"), "turrican-ii-the-final-fight")
        self.assertEqual(slugify("../../etc"), "etc")
        self.assertEqual(slugify(""), "untitled")

    def test_a_staged_name_cannot_reach_outside_the_staging_area(self) -> None:
        staged = self.service.stage_disk(self._floppy("GAME", payload=b"data"), "Safe")
        self.assertTrue(Path(staged["path"]).is_relative_to(self.service.staging_root()))
        with self.assertRaises(DiskError):
            self.service.discard_staged_title("../../work")


class WHDLoadInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.service = DiskService(self.root / "work")
        self.drive = self.service.create_blank("ffs-hard", "SYSTEM", "40MB")
        self.service.select_partition(self.drive, 0)
        self.addCleanup(self._temporary.cleanup)

    def test_a_drive_without_whdload_says_so(self) -> None:
        status = self.service.whdload_status(self.drive)
        self.assertFalse(status["installed"])
        self.assertEqual([source["name"] for source in status["sources"]], ["whdload.de", "Aminet"])

    def test_installing_puts_the_loader_and_its_tools_where_amigados_looks(self) -> None:
        result = self.service.install_whdload(
            self.drive, whdload_archive(), source="test", url="https://example.invalid/x.lha"
        )

        self.assertEqual(result["version"], "20.0")
        self.assertFalse(result["replaced"])
        installed = {row["name"] for row in self.service.list_directory(self.drive, "C")["entries"]}
        self.assertIn("WHDLoad", installed)
        self.assertIn("WHDLoadCD32", installed)
        scripts = {row["name"] for row in self.service.list_directory(self.drive, "S")["entries"]}
        self.assertEqual(scripts, {"WHDLoad-Startup", "WHDLoad-Cleanup", "WHDLoad.prefs"})
        self.assertTrue(self.service.whdload_status(self.drive)["installed"])

    def test_reinstalling_keeps_preferences_the_operator_has_tuned(self) -> None:
        """The prefs file records where debug output goes on that machine."""
        self.service.install_whdload(self.drive, whdload_archive("19.0"), source="test", url="")
        tuned = self.root / "prefs"
        tuned.write_bytes(b";DebugKey=$58\nCoreDumpPath=DH0:Dumps\n")
        self.service.put(self.drive, whdload.PREFERENCES_FILE, tuned)

        result = self.service.install_whdload(self.drive, whdload_archive("20.0"), source="test", url="")

        self.assertTrue(result["replaced"])
        self.assertEqual(result["previousVersion"], "19.0")
        self.assertTrue(result["keptPreferences"])
        self.assertEqual(self.service.read_file(self.drive, whdload.PREFERENCES_FILE), tuned.read_bytes())
        self.assertEqual(self.service.whdload_status(self.drive)["version"], "20.0")

    def test_an_install_says_whether_it_moved_the_drive_forwards(self) -> None:
        """An operator who asked for an install assumes it was an upgrade."""
        first = self.service.install_whdload(self.drive, whdload_archive("19.0"), source="test", url="")
        self.assertTrue(first["upgraded"])

        upgrade = self.service.install_whdload(self.drive, whdload_archive("20.0"), source="test", url="")
        self.assertTrue(upgrade["upgraded"])
        self.assertEqual(upgrade["previousVersion"], "19.0")

        same = self.service.install_whdload(self.drive, whdload_archive("20.0"), source="test", url="")
        self.assertFalse(same["upgraded"])

        older = self.service.install_whdload(self.drive, whdload_archive("18.0"), source="test", url="")
        self.assertFalse(older["upgraded"])
        self.assertEqual(self.service.whdload_status(self.drive)["version"], "18.0")

    def test_an_incomplete_archive_is_refused_before_anything_is_written(self) -> None:
        """Half a WHDLoad looks installed and fails only when a game is run."""
        with self.assertRaises(DiskError) as raised:
            self.service.install_whdload(
                self.drive, whdload_archive(omit="C/WHDLoadCD32"), source="test", url=""
            )

        self.assertIn("C/WHDLoadCD32", str(raised.exception))
        self.assertFalse(self.service.whdload_status(self.drive)["installed"])

    def test_an_archive_that_is_not_whdload_is_named_as_such(self) -> None:
        with self.assertRaises(DiskError) as raised:
            self.service.install_whdload(
                self.drive, archive(level1_member("Game/Loader", b"x")), source="Aminet", url=""
            )
        self.assertIn("does not contain WHDLoad", str(raised.exception))

    def test_a_download_that_is_an_error_page_is_reported_not_installed(self) -> None:
        """Aminet answers a missing file with HTML and an HTTP 200."""
        with self.assertRaises(DiskError):
            self.service.install_whdload(
                self.drive, b"<!DOCTYPE HTML><html>Not found</html>", source="Aminet", url=""
            )


class WHDLoadSourceTests(unittest.TestCase):
    def test_the_first_source_that_answers_is_used(self) -> None:
        served = whdload_archive("20.0")
        asked: list[str] = []

        def fetch(url: str) -> bytes:
            asked.append(url)
            return served

        release = whdload.download(fetch)

        self.assertEqual(release.source, "whdload.de")
        self.assertEqual(asked, [whdload.WHDLOAD_ARCHIVE_URL])
        self.assertEqual(release.archive_bytes, served)

    def test_a_failing_first_source_falls_through_to_the_mirror(self) -> None:
        served = whdload_archive("20.0")

        def fetch(url: str) -> bytes:
            if "whdload.de" in url:
                raise OSError("connection refused")
            return served

        self.assertEqual(whdload.download(fetch).source, "Aminet")

    def test_every_source_failing_reports_all_of_them_and_what_to_do(self) -> None:
        def fetch(url: str) -> bytes:
            raise OSError("no route to host")

        with self.assertRaises(DiskError) as raised:
            whdload.download(fetch)

        message = str(raised.exception)
        self.assertIn("whdload.de", message)
        self.assertIn("Aminet", message)
        self.assertIn("WHDLoad_usr.lha", message)

    def test_versions_are_compared_as_numbers_not_as_text(self) -> None:
        """WHDLoad passed version 9, so "10.0" sorts below "9.0" as text."""
        self.assertTrue(whdload.newer("10.0", "9.0"))
        self.assertTrue(whdload.newer("20.0", "19.9"))
        self.assertFalse(whdload.newer("18.0", "20.0"))
        self.assertFalse(whdload.newer("", "20.0"))


class WHDLoadSlaveTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.service = DiskService(Path(self._temporary.name) / "work")
        self.drive = self.service.create_blank("ffs-hard", "SYSTEM", "40MB")
        self.service.select_partition(self.drive, 0)
        self.addCleanup(self._temporary.cleanup)

    def test_a_bare_slave_is_placed_in_the_title_drawer(self) -> None:
        result = self.service.install_whdload_slave(
            self.drive, "Games/Hyper Sports", b"slave bytes", "HyperSports.slave"
        )

        self.assertEqual(result["path"], "Games/Hyper Sports/HyperSports.slave")
        self.assertEqual(
            self.service.read_file(self.drive, result["path"]), b"slave bytes"
        )

    def test_a_slave_still_inside_its_archive_is_unpacked_on_the_way_in(self) -> None:
        """Requiring an LHA tool first is the dependency this build avoids."""
        packaged = archive(
            level1_member("HyperSports/HyperSports.slave", b"slave bytes"),
            level1_member("HyperSports/ReadMe", b"notes"),
        )

        result = self.service.install_whdload_slave(self.drive, "Games/HS", packaged, "hs.lha")

        self.assertEqual(result["name"], "HyperSports.slave")
        self.assertEqual(self.service.read_file(self.drive, result["path"]), b"slave bytes")

    def test_an_archive_of_several_slaves_asks_which_one(self) -> None:
        packaged = archive(
            level1_member("Pack/One.slave", b"a"),
            level1_member("Pack/Two.slave", b"b"),
        )
        with self.assertRaises(DiskError) as raised:
            self.service.install_whdload_slave(self.drive, "Games/Pack", packaged, "pack.lha")
        self.assertIn("2 slaves", str(raised.exception))

    def test_a_file_that_is_not_a_slave_is_refused(self) -> None:
        with self.assertRaises(DiskError) as raised:
            self.service.install_whdload_slave(self.drive, "Games/X", b"data", "readme.txt")
        self.assertIn(".slave", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
