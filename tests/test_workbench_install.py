"""Installing AmigaOS onto a drive from the operator's own Workbench floppies.

The failures worth guarding against here are all of the same shape: the files
are present and the drive still does not work. A disk identified by its file
name rather than its volume name, an Extras disk allowed to overwrite the
Workbench disk's ``C:``, or a set quietly assembled from two different releases
all produce a drive that looks complete and boots to a Guru. Each of those is
asserted against a real image, built and read back through the public service.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.disk_service import DiskError, DiskService
from app.workbench_install import (
    CREATED_DRAWERS,
    available_versions,
    choose_set,
    describe_roles,
    missing_roles,
    normalise_version,
    quality_of,
    role_for,
    version_of,
)


class DiskRecognitionTests(unittest.TestCase):
    """Recognition is done on the volume name, because file names lie."""

    def test_a_volume_name_identifies_the_disk(self) -> None:
        self.assertEqual(role_for("Workbench3.1").key, "workbench")
        self.assertEqual(role_for("Extras3.1").key, "extras")
        self.assertEqual(role_for("Fonts").key, "fonts")
        self.assertEqual(role_for("Locale").key, "locale")
        self.assertEqual(role_for("Storage3.1").key, "storage")
        self.assertEqual(role_for("Install3.1").key, "install")
        self.assertEqual(role_for("GlowIcons").key, "glowicons")

    def test_a_disk_that_is_not_part_of_a_release_is_not_claimed(self) -> None:
        self.assertIsNone(role_for("Hyper Sports"))
        self.assertIsNone(role_for(""))

    def test_spacing_and_case_do_not_change_the_answer(self) -> None:
        self.assertEqual(role_for("WORKBENCH 3.1").key, "workbench")
        self.assertEqual(role_for("workbench3.1").key, "workbench")

    def test_the_release_is_read_from_the_volume_name_first(self) -> None:
        self.assertEqual(version_of("Workbench3.1", "anything.adf"), "3.1")
        self.assertEqual(version_of("Extras2.05", "x.adf"), "2.0")

    def test_an_unversioned_volume_falls_back_to_the_file_name(self) -> None:
        self.assertEqual(version_of("Fonts", "amiga-fonts-v3.1.adf"), "3.1")

    def test_a_disk_number_is_not_mistaken_for_a_release(self) -> None:
        """"disk2.0" names the second disk, not AmigaOS 2.0."""
        self.assertEqual(version_of("Fonts", "disk9.0.adf"), "")

    def test_release_spellings_fold_together(self) -> None:
        self.assertEqual(normalise_version("2.05"), "2.0")
        self.assertEqual(normalise_version("3.10"), "3.1")
        self.assertEqual(normalise_version("3.1"), "3.1")

    def test_a_dump_is_scored_by_what_it_says_about_itself(self) -> None:
        """A collection holds several dumps of each disk, of varying honesty.

        The scores only have to order them: a dump that says it was verified
        should be preferred to an unmarked one, and one that says it was
        modified or cracked should lose to both.
        """
        verified = quality_of("Workbench3.1", "Workbench v3.1 (verified).adf")
        tosec = quality_of("Workbench3.1", "Workbench v3.1 [!].adf")
        plain = quality_of("Workbench3.1", "Workbench v3.1.adf")
        alternate = quality_of("Workbench3.1", "Workbench v3.1 (alternate).adf")
        modified = quality_of("Workbench3.1", "Workbench v3.1 (modified).adf")
        cracked = quality_of("Workbench3.1", "Workbench v3.1 (cracked).adf")

        self.assertEqual(verified, tosec)
        self.assertGreater(verified, plain)
        self.assertGreater(plain, alternate)
        self.assertGreater(alternate, modified)
        self.assertEqual(modified, cracked)

    def test_the_volume_name_counts_as_well_as_the_file_name(self) -> None:
        """A dump often records what it is in the volume name rather than the file."""
        self.assertGreater(
            quality_of("Workbench3.1 verified", "disk2.adf"),
            quality_of("Workbench3.1 modified", "disk2.adf"),
        )

    def test_the_required_disk_is_declared(self) -> None:
        required = [role["key"] for role in describe_roles() if role["required"]]
        self.assertEqual(required, ["workbench"])


class SetSelectionTests(unittest.TestCase):
    """A set assembled from two releases is the classic way to break a drive."""

    @staticmethod
    def _match(role: str, version: str, *, quality: int = 2, files: int = 100, name: str = "") -> dict:
        return {
            "imageId": name or f"{role}-{version}",
            "source": name or f"{role}{version}.adf",
            "role": role,
            "version": version,
            "quality": quality,
            "fileCount": files,
        }

    def test_the_release_is_decided_by_the_workbench_disk(self) -> None:
        chosen, version = choose_set([
            self._match("workbench", "3.1"),
            self._match("extras", "2.0"),
            self._match("extras", "3.1"),
        ])
        self.assertEqual(version, "3.1")
        self.assertEqual(chosen["extras"]["version"], "3.1")

    def test_a_disk_from_another_release_is_left_out_rather_than_mixed_in(self) -> None:
        chosen, _version = choose_set([
            self._match("workbench", "3.1"),
            self._match("locale", "2.0"),
        ])
        self.assertNotIn("locale", chosen)

    def test_an_unversioned_disk_is_accepted_into_the_set(self) -> None:
        """Fonts and Locale often carry no release in either name."""
        chosen, _version = choose_set([
            self._match("workbench", "3.1"),
            self._match("fonts", ""),
        ])
        self.assertIn("fonts", chosen)

    def test_a_verified_dump_beats_a_modified_one(self) -> None:
        chosen, _version = choose_set([
            self._match("workbench", "3.1", quality=0, name="cracked"),
            self._match("workbench", "3.1", quality=3, name="verified"),
        ])
        self.assertEqual(chosen["workbench"]["imageId"], "verified")

    def test_a_set_without_a_workbench_disk_says_so(self) -> None:
        chosen, _version = choose_set([self._match("extras", "3.1")])
        self.assertEqual([role.key for role in missing_roles(chosen)], ["workbench"])

    def test_the_newest_release_is_offered_first(self) -> None:
        versions = available_versions([
            self._match("workbench", "2.0"),
            self._match("workbench", "3.1"),
        ])
        self.assertEqual(versions, ["3.1", "2.0"])


class WorkbenchInstallTests(unittest.TestCase):
    """Built and read back through the service, against real Amiga volumes."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.service = DiskService(self.root / "work")
        self.addCleanup(self._temporary.cleanup)
        self.drive = self.service.create_blank("ffs-hard", "SYSTEM", "40MB")
        self.service.select_partition(self.drive, 0)

    def _write(self, session, path: str, data: bytes, *, protection: str = "") -> None:
        directory = "/".join(path.split("/")[:-1])
        if directory:
            try:
                self.service.make_directory(session, directory)
            except DiskError:
                pass
        host = self.root / f"payload-{abs(hash((session.name, path)))}"
        host.write_bytes(data)
        self.service.put(session, path, host, protection=protection or None)

    def _disc(self, volume: str, files: dict[str, bytes]):
        disc = self.service.create_blank("adf", volume)
        for path, data in files.items():
            self._write(disc, path, data)
        return disc

    def _workbench(self, volume: str = "Workbench3.1"):
        return self._disc(volume, {
            "c/Dir": b"full Dir command",
            "libs/icon.library": b"full icon library",
            "s/Startup-Sequence": b"C:SetPatch\nC:IPrefs\n",
            "System/Shell": b"shell",
        })

    def _names(self, directory: str) -> set[str]:
        return {
            str(row["name"])
            for row in self.service.list_directory(self.drive, directory)["entries"]
        }

    def test_a_workbench_disk_lands_in_the_root_of_the_drive(self) -> None:
        result = self.service.install_workbench(
            self.drive, {"workbench": self._workbench()}
        )

        self.assertEqual(
            self.service.read_file(self.drive, "s/Startup-Sequence"), b"C:SetPatch\nC:IPrefs\n"
        )
        self.assertEqual(self.service.read_file(self.drive, "c/Dir"), b"full Dir command")
        self.assertEqual(result["discs"][0]["role"], "workbench")

    def test_the_workbench_disk_wins_over_extras_for_a_shared_file(self) -> None:
        """Extras carries a cut-down C:. Letting it win breaks the system.

        This is the whole reason the copy order is fixed rather than incidental,
        so it is asserted on the bytes that end up on the drive.
        """
        extras = self._disc("Extras3.1", {
            "c/Dir": b"cut-down Dir",
            "Tools/Calculator": b"calculator",
        })

        self.service.install_workbench(
            self.drive, {"workbench": self._workbench(), "extras": extras}
        )

        self.assertEqual(self.service.read_file(self.drive, "c/Dir"), b"full Dir command")
        self.assertEqual(self.service.read_file(self.drive, "Tools/Calculator"), b"calculator")

    def test_a_supporting_disk_lands_in_its_own_drawer(self) -> None:
        fonts = self._disc("Fonts", {"topaz.font": b"topaz", "topaz/8": b"eight"})

        self.service.install_workbench(
            self.drive, {"workbench": self._workbench(), "fonts": fonts}
        )

        self.assertEqual(self.service.read_file(self.drive, "Fonts/topaz.font"), b"topaz")
        self.assertEqual(self.service.read_file(self.drive, "Fonts/topaz/8"), b"eight")

    def test_the_install_disk_is_kept_out_of_the_root(self) -> None:
        """Its cut-down C: and Libs: must not replace the real ones."""
        install = self._disc("Install3.1", {
            "c/Dir": b"install-disk Dir",
            "Install": b"the install script",
        })

        self.service.install_workbench(
            self.drive, {"workbench": self._workbench(), "install": install}
        )

        self.assertEqual(self.service.read_file(self.drive, "c/Dir"), b"full Dir command")
        self.assertEqual(self.service.read_file(self.drive, "Install/Install"), b"the install script")

    def test_the_drawers_the_install_script_makes_are_created(self) -> None:
        """A system with no T: cannot write a temporary file."""
        self.service.install_workbench(self.drive, {"workbench": self._workbench()})

        present = self._names("")
        for drawer in CREATED_DRAWERS:
            top = drawer.split("/")[0]
            self.assertIn(top, present, f"{drawer} was not created")
        self.assertIn("DOSDrivers", self._names("Devs"))

    def test_protection_bits_travel_with_the_files(self) -> None:
        """A command that loses its ``e`` bit cannot be run."""
        disc = self.service.create_blank("adf", "Workbench3.1")
        self._write(disc, "s/Startup-Sequence", b"C:SetPatch\n")
        self._write(disc, "c/Dir", b"dir", protection="----rwed")
        self._write(disc, "libs/icon.library", b"lib", protection="----rw-d")

        self.service.install_workbench(self.drive, {"workbench": disc})

        self.assertEqual(
            self.service.file_metadata(self.drive, "libs/icon.library")["protection"],
            self.service.file_metadata(disc, "libs/icon.library")["protection"],
        )

    def test_installing_without_a_workbench_disk_is_refused(self) -> None:
        """Anything else on its own produces a drive that cannot boot."""
        extras = self._disc("Extras3.1", {"Tools/Calculator": b"calculator"})
        with self.assertRaises(DiskError) as raised:
            self.service.install_workbench(self.drive, {"extras": extras})
        self.assertIn("Workbench", str(raised.exception))

    def test_a_second_install_leaves_hand_edits_alone(self) -> None:
        """Preparing a drive twice must not undo work done in between."""
        self.service.install_workbench(self.drive, {"workbench": self._workbench()})
        edited = self.root / "edited"
        edited.write_bytes(b"C:SetPatch\nMyOwnLine\n")
        self.service.put(self.drive, "s/Startup-Sequence", edited)

        self.service.install_workbench(self.drive, {"workbench": self._workbench()})

        self.assertEqual(
            self.service.read_file(self.drive, "s/Startup-Sequence"), b"C:SetPatch\nMyOwnLine\n"
        )

    def test_the_volume_is_written_into_rather_than_formatted(self) -> None:
        """An operator's existing drive is not thrown away to prepare it."""
        existing = self.root / "existing"
        existing.write_bytes(b"my data")
        self.service.make_directory(self.drive, "Games")
        self.service.put(self.drive, "Games/Keeper", existing)

        self.service.install_workbench(self.drive, {"workbench": self._workbench()})

        self.assertEqual(self.service.read_file(self.drive, "Games/Keeper"), b"my data")

    def test_a_survey_names_what_it_recognised_and_what_it_chose(self) -> None:
        workbench = self._workbench()
        extras = self._disc("Extras3.1", {"Tools/Calculator": b"calc"})
        stranger = self._disc("Hyper Sports", {"Loader": b"game"})

        survey = self.service.survey_workbench_discs([workbench, extras, stranger])

        self.assertEqual(survey["version"], "3.1")
        self.assertEqual(survey["chosen"]["workbench"], workbench.id)
        self.assertEqual(survey["chosen"]["extras"], extras.id)
        self.assertEqual(survey["unrecognised"], [stranger.name])
        self.assertEqual(survey["missing"], [])

    def test_a_survey_says_which_required_disk_is_absent(self) -> None:
        survey = self.service.survey_workbench_discs(
            [self._disc("Extras3.1", {"Tools/Calculator": b"calc"})]
        )
        self.assertEqual([row["key"] for row in survey["missing"]], ["workbench"])

    def test_a_partition_table_is_not_a_place_to_install_onto(self) -> None:
        drive = self.service.create_blank("ffs-hard", "TARGET", "40MB")
        with self.assertRaises(DiskError):
            self.service.install_workbench(drive, {"workbench": self._workbench()})


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
