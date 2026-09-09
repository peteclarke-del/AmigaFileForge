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
from app.ffs_items import delete_ffs_items
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
    """Staging writes onto the drive, so every check is made against the drive.

    The point of the change these tests describe is that a staged set has to be
    reachable from the machine the title will run on. Reading the results back
    off the host filesystem would pass just as well against the old behaviour,
    which put the discs somewhere no Amiga could ever see them.
    """

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.service = DiskService(self.root / "work")
        self.addCleanup(self._temporary.cleanup)
        self.drive = self._drive()

    def _drive(self, name: str = "SYSTEM"):
        drive = self.service.create_blank("ffs-hard", name, "40MB")
        self.service.select_partition(drive, 0)
        return drive

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

    def _names(self, directory: str) -> set[str]:
        return {
            str(row["name"])
            for row in self.service.list_directory(self.drive, directory)["entries"]
        }

    def test_a_staged_disc_lands_on_the_target_image_not_on_the_host(self) -> None:
        """The whole reason for staging is that the Amiga can reach the result.

        A staged set left in a directory on the machine running this program is
        unreachable from the emulator and from the real hardware, which is
        exactly where the install has to be finished.
        """
        staged = self.service.stage_disk(
            self._floppy("GAME", payload=b"data"), self.drive, "Hyper Sports"
        )

        self.assertEqual(staged["path"], "Storage/Install/Hyper Sports")
        self.assertEqual(
            self.service.read_file(self.drive, "Storage/Install/Hyper Sports/s/Startup-Sequence"),
            b"Loader\n",
        )
        # Nothing is left behind on the host: the working directory holds
        # sessions and checkpoints, never a staging tree.
        self.assertFalse((self.service.work_dir / "staging").exists())

    def test_the_staging_drawer_can_be_chosen(self) -> None:
        staged = self.service.stage_disk(
            self._floppy("GAME", payload=b"data"), self.drive, "Title", parent="Games/Waiting"
        )
        self.assertEqual(staged["path"], "Games/Waiting/Title")

    def test_staging_keeps_clear_of_the_amigaos_install_drawer(self) -> None:
        """A Workbench install copies the Install disk to ``Install:``.

        Staging into the same drawer listed that disk's own ``c`` and ``Libs``
        as though somebody had staged titles by those names, which is how the
        collision was found. ``Storage`` is where Workbench keeps what is not
        in use yet, so a staged set belongs there instead.
        """
        self.assertEqual(self.service.staging_parent(), "Storage/Install")
        self.service.make_directory(self.drive, "Install/c")
        self.service.stage_disk(self._floppy("GAME", payload=b"data"), self.drive, "Real Title")

        titles = [row["name"] for row in self.service.staged_titles(self.drive)]

        self.assertEqual(titles, ["Real Title"])

    def test_a_multi_disc_set_stages_into_one_tree(self) -> None:
        first = self._floppy("GAME1", payload=b"IDENTICAL")
        second = self._floppy("GAME2", payload=b"IDENTICAL")

        self.service.stage_disk(first, self.drive, "Hyper Sports", disc_label="Disk 1")
        staged = self.service.stage_disk(second, self.drive, "Hyper Sports", disc_label="Disk 2")

        self.assertEqual(staged["discCount"], 2)
        self.assertEqual([disc["volume"] for disc in staged["discs"]], ["GAME1", "GAME2"])
        self.assertIn("Loader", self._names(staged["path"]))
        self.assertIn("Startup-Sequence", self._names(f"{staged['path']}/s"))
        # Identical files across discs are stored once, not twice.
        self.assertEqual(staged["conflicts"], [])

    def test_two_discs_carrying_different_files_under_one_name_both_survive(self) -> None:
        """Keeping only the last disc would silently destroy half the set."""
        self.service.stage_disk(
            self._floppy("GAME1", payload=b"LEVEL ONE"), self.drive, "Title", disc_label="Disk 1"
        )
        staged = self.service.stage_disk(
            self._floppy("GAME2", payload=b"LEVEL TWO, LONGER"), self.drive, "Title",
            disc_label="Disk 2",
        )

        self.assertEqual(len(staged["conflicts"]), 1)
        conflict = staged["conflicts"][0]
        self.assertEqual(conflict["path"], "Shared.dat")
        self.assertEqual(conflict["alsoIn"], "Disk 2")
        self.assertEqual(
            self.service.read_file(self.drive, f"{staged['path']}/Shared.dat"), b"LEVEL ONE"
        )
        self.assertEqual(
            self.service.read_file(self.drive, conflict["storedAs"]), b"LEVEL TWO, LONGER"
        )

    def test_the_payload_drawer_holds_nothing_but_the_title(self) -> None:
        """What is installed has to be the disc, not the disc plus bookkeeping."""
        self.service.stage_disk(
            self._floppy("GAME1", payload=b"ONE"), self.drive, "Title", disc_label="Disk 1"
        )
        staged = self.service.stage_disk(
            self._floppy("GAME2", payload=b"TWO"), self.drive, "Title", disc_label="Disk 2"
        )

        self.assertEqual(self._names(staged["path"]), {"Loader", "Shared.dat", "s"})
        self.assertIn("Forge-Staging", self._names("Storage/Install"))

    def test_protection_bits_and_comments_survive_the_round_trip(self) -> None:
        """A loader that loses its ``e`` bit will not start, and looks fine."""
        floppy = self._floppy("GAME", payload=b"data", protection="----rw-d", comment="do not delete")
        staged = self.service.stage_disk(floppy, self.drive, "Protected Title")

        result = self.service.install_staged_title(self.drive, staged["name"], parent="Games")

        entries = {
            row["name"]: row
            for row in self.service.list_directory(self.drive, result["path"])["entries"]
        }
        self.assertEqual(entries["Loader"]["comment"], "do not delete")
        self.assertEqual(
            self.service.file_metadata(self.drive, f"{result['path']}/Loader")["protection"],
            self.service.file_metadata(floppy, "Loader")["protection"],
        )

    def test_a_staged_title_installs_with_its_drawers_intact(self) -> None:
        staged = self.service.stage_disk(
            self._floppy("GAME", payload=b"data"), self.drive, "Nested Title"
        )

        result = self.service.install_staged_title(self.drive, staged["name"], parent="Games")

        self.assertEqual(result["path"], "Games/Nested Title")
        self.assertEqual(
            self.service.read_file(self.drive, "Games/Nested Title/s/Startup-Sequence"), b"Loader\n"
        )

    def test_installing_a_title_empties_its_staging_drawer(self) -> None:
        """A set that stayed staged after installing would be counted twice."""
        staged = self.service.stage_disk(
            self._floppy("GAME", payload=b"data"), self.drive, "Moved Title"
        )
        self.service.install_staged_title(self.drive, staged["name"], parent="Games")

        self.assertEqual(self.service.staged_titles(self.drive), [])
        self.assertNotIn("Moved Title", self._names("Storage/Install"))
        self.assertNotIn("Moved Title", self._names("Storage/Install/Forge-Staging"))

    def test_restaging_a_disc_replaces_it_rather_than_adding_another(self) -> None:
        """A set that grew every time it was corrected could not be reasoned about."""
        self.service.stage_disk(
            self._floppy("GAME1", payload=b"one"), self.drive, "Title", disc_label="Disk 1"
        )
        self.service.stage_disk(
            self._floppy("GAME2", payload=b"two"), self.drive, "Title", disc_label="Disk 2"
        )

        staged = self.service.stage_disk(
            self._floppy("GAME1B", payload=b"one"), self.drive, "Title", disc_label="Disk 1"
        )

        self.assertEqual(staged["discCount"], 2)
        self.assertEqual([disc["label"] for disc in staged["discs"]], ["Disk 1", "Disk 2"])
        self.assertEqual(staged["discs"][0]["volume"], "GAME1B")

    def test_restaging_a_corrected_disc_overwrites_it_instead_of_filing_it_aside(self) -> None:
        """Filing the correction as an alternate would leave the bad file in place."""
        self.service.stage_disk(
            self._floppy("GAME1", payload=b"BROKEN"), self.drive, "Title", disc_label="Disk 1"
        )

        staged = self.service.stage_disk(
            self._floppy("GAME1FIXED", payload=b"CORRECTED"), self.drive, "Title", disc_label="Disk 1"
        )

        self.assertEqual(staged["conflicts"], [])
        self.assertEqual(
            self.service.read_file(self.drive, f"{staged['path']}/Shared.dat"), b"CORRECTED"
        )
        self.assertNotIn("Disk 1", self._names("Storage/Install/Forge-Staging"))

    def test_a_conflict_stops_being_reported_once_the_disc_behind_it_is_restaged(self) -> None:
        self.service.stage_disk(
            self._floppy("GAME1", payload=b"ONE"), self.drive, "Title", disc_label="Disk 1"
        )
        conflicted = self.service.stage_disk(
            self._floppy("GAME2", payload=b"TWO"), self.drive, "Title", disc_label="Disk 2"
        )
        self.assertEqual(len(conflicted["conflicts"]), 1)

        resolved = self.service.stage_disk(
            self._floppy("GAME2AGAIN", payload=b"ONE"), self.drive, "Title", disc_label="Disk 2"
        )

        self.assertEqual(resolved["conflicts"], [])

    def test_an_unlabelled_disc_takes_the_first_free_slot(self) -> None:
        self.service.stage_disk(self._floppy("GAME1", payload=b"one"), self.drive, "Title")
        staged = self.service.stage_disk(self._floppy("GAME2", payload=b"two"), self.drive, "Title")
        self.assertEqual([disc["label"] for disc in staged["discs"]], ["Disc 1", "Disc 2"])

    def test_a_staged_title_can_be_listed_and_discarded(self) -> None:
        self.service.stage_disk(self._floppy("GAME", payload=b"data"), self.drive, "Listed Title")
        self.assertEqual(
            [row["title"] for row in self.service.staged_titles(self.drive)], ["Listed Title"]
        )

        self.service.discard_staged_title(self.drive, "Listed Title")

        self.assertEqual(self.service.staged_titles(self.drive), [])
        with self.assertRaises(DiskError):
            self.service.discard_staged_title(self.drive, "Listed Title")

    def test_the_list_is_read_off_the_drive_rather_than_remembered_here(self) -> None:
        """A drive built elsewhere still has to report what is waiting on it.

        Deleting the record and finding the title gone would mean the list was
        being kept on this machine after all, which is the thing being fixed.
        """
        self.service.stage_disk(self._floppy("GAME", payload=b"data"), self.drive, "Derived")
        delete_ffs_items(self.service, self.drive, ["Storage/Install/Forge-Staging"])

        titles = self.service.staged_titles(self.drive)

        self.assertEqual([row["name"] for row in titles], ["Derived"])
        self.assertEqual(titles[0]["fileCount"], 3)

    def test_a_title_name_becomes_a_directory_every_filesystem_accepts(self) -> None:
        """Staged trees end up on FAT cards and Amiga volumes, not only here."""
        self.assertEqual(slugify("Hyper Sports"), "hyper-sports")
        self.assertEqual(slugify("Turrican II: The Final Fight"), "turrican-ii-the-final-fight")
        self.assertEqual(slugify("../../etc"), "etc")
        self.assertEqual(slugify(""), "untitled")

    def test_a_staged_name_cannot_reach_outside_the_staging_drawer(self) -> None:
        staged = self.service.stage_disk(self._floppy("GAME", payload=b"data"), self.drive, "Safe")
        self.assertTrue(staged["path"].startswith("Storage/Install/"))
        with self.assertRaises(DiskError):
            self.service.discard_staged_title(self.drive, "../../work")

    def test_a_partition_table_is_not_a_place_to_stage_onto(self) -> None:
        """A drive with no partition selected is an index, not a volume."""
        drive = self.service.create_blank("ffs-hard", "TARGET", "40MB")
        with self.assertRaises(DiskError):
            self.service.stage_disk(self._floppy("GAME", payload=b"data"), drive, "Title")


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
