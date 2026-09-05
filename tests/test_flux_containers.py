from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.errors import DiskError
from app.flux_containers import (
    FLUX_CONTAINERS,
    HFE,
    SCP,
    SECTOR_SIZE,
    FluxEngine,
    flux_layout_for,
    is_flux_encodable,
    restore_omitted_tail_sector,
    sector_image_suffix,
)


def _argument(arguments: list[str], prefix: str) -> str | None:
    return next(
        (item.removeprefix(prefix) for item in arguments if item.startswith(prefix)),
        None,
    )


def _write_output(arguments: list[str], content: bytes) -> None:
    output = _argument(arguments, "-foutput:")
    if output:
        Path(output).write_bytes(content)


class SectorGeometryTests(unittest.TestCase):
    def test_every_floppy_geometry_is_an_adf_whatever_formatted_it(self) -> None:
        # OFS and FFS share the same media, so the filing system never changes
        # the extension, and neither does the side count: an Amiga floppy is
        # always double sided.
        for kind in ("ofs", "ffs"):
            for size in (450_560, 901_120, 1_802_240):
                with self.subTest(kind=kind, size=size):
                    self.assertEqual(sector_image_suffix(kind, size), ".adf")
        self.assertEqual(sector_image_suffix("ofs", 901_120, 2), ".adf")

    def test_anything_larger_than_a_floppy_is_a_hard_disk_image(self) -> None:
        self.assertEqual(sector_image_suffix("ffs", 20 * 1024 * 1024), ".hdf")
        self.assertEqual(sector_image_suffix("ffs", 800 * 1024), ".hdf")

    def test_the_two_three_and_a_half_inch_densities_have_a_flux_layout(self) -> None:
        self.assertEqual(flux_layout_for("ffs", 901_120), "AMIGADOS_DD")
        self.assertEqual(flux_layout_for("ofs", 901_120), "AMIGADOS_DD")
        self.assertEqual(flux_layout_for("ffs", 1_802_240), "AMIGADOS_HD")
        self.assertIsNone(flux_layout_for("ffs", 800 * 1024))

    def test_the_five_inch_geometry_has_no_flux_layout(self) -> None:
        self.assertIsNone(flux_layout_for("ofs", 450_560))
        self.assertFalse(is_flux_encodable("ofs", 450_560))

    def test_a_hard_disk_image_is_never_flux_encodable(self) -> None:
        self.assertTrue(is_flux_encodable("ffs", 901_120))
        self.assertFalse(is_flux_encodable("ffs", 20 * 1024 * 1024))
        self.assertFalse(is_flux_encodable("hdf", 901_120))


class TailSectorRepairTests(unittest.TestCase):
    """The repair must be usable for its one real case and refuse everything else."""

    def _image(self, folder: str, size: int) -> Path:
        path = Path(folder) / "decoded.img"
        path.write_bytes(b"\xAA" * size)
        return path

    def test_one_short_sector_is_restored_to_the_canonical_size(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image = self._image(folder, 901_120 - SECTOR_SIZE)
            self.assertTrue(restore_omitted_tail_sector(image, "ffs"))
            self.assertEqual(image.stat().st_size, 901_120)

    def test_the_restored_sector_is_blank_and_existing_bytes_are_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image = self._image(folder, 450_560 - SECTOR_SIZE)
            restore_omitted_tail_sector(image, "ofs")
            data = image.read_bytes()
            self.assertEqual(data[: 450_560 - SECTOR_SIZE], b"\xAA" * (450_560 - SECTOR_SIZE))
            self.assertEqual(data[450_560 - SECTOR_SIZE :], bytes(SECTOR_SIZE))

    def test_an_already_complete_image_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image = self._image(folder, 901_120)
            self.assertFalse(restore_omitted_tail_sector(image, "ffs"))
            self.assertEqual(image.stat().st_size, 901_120)

    def test_more_than_one_missing_sector_is_never_padded(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image = self._image(folder, 901_120 - (SECTOR_SIZE * 2))
            self.assertFalse(restore_omitted_tail_sector(image, "ffs"))
            self.assertEqual(image.stat().st_size, 901_120 - (SECTOR_SIZE * 2))

    def test_a_size_unrelated_to_any_geometry_is_never_padded(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image = self._image(folder, 500_000)
            self.assertFalse(restore_omitted_tail_sector(image, "ffs"))
            self.assertEqual(image.stat().st_size, 500_000)

    def test_a_kind_without_canonical_geometry_is_never_padded(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image = self._image(folder, 901_120 - SECTOR_SIZE)
            self.assertFalse(restore_omitted_tail_sector(image, "hdf"))

    def test_expected_size_selects_the_geometry_it_names(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image = self._image(folder, 450_560 - SECTOR_SIZE)
            self.assertTrue(
                restore_omitted_tail_sector(image, "ofs", expected_size=450_560)
            )
            self.assertEqual(image.stat().st_size, 450_560)

    def test_an_implausible_expected_size_is_ignored_not_obeyed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            image = self._image(folder, 1_802_240 - SECTOR_SIZE)
            # 999 is not a canonical geometry, so the repair falls back to the
            # size the file is actually one sector short of.
            self.assertTrue(restore_omitted_tail_sector(image, "ofs", expected_size=999))
            self.assertEqual(image.stat().st_size, 1_802_240)

    def test_a_missing_file_is_reported_rather_than_created(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            absent = Path(folder) / "never-written.img"
            self.assertFalse(restore_omitted_tail_sector(absent, "ffs"))
            self.assertFalse(absent.exists())


class FluxContainerRegistryTests(unittest.TestCase):
    def test_both_containers_are_registered_under_their_identifier(self) -> None:
        self.assertEqual(set(FLUX_CONTAINERS), {"hfe", "scp"})
        for identifier, container in FLUX_CONTAINERS.items():
            self.assertEqual(container.identifier, identifier)
            self.assertEqual(container.extension, f".{identifier}")
            self.assertEqual(container.display, identifier.upper())
            self.assertTrue(container.plugin)
            self.assertTrue(container.label)


class FluxEngineTests(unittest.TestCase):
    def test_encode_adds_a_layout_hint_only_when_the_geometry_needs_one(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            sectors = Path(folder) / "disk.adf"
            sectors.write_bytes(bytes(901_120))
            seen: list[list[str]] = []

            def run(arguments):
                seen.append(arguments)
                _write_output(arguments, b"FLUX")
                return ""

            FluxEngine(run).encode_from_sectors(
                sectors, SCP, Path(folder) / "out.scp", kind="ffs"
            )
            self.assertIn("-uselayout:AMIGADOS_DD", seen[0])
            self.assertIn("-conv:SCP_FLUX_STREAM", seen[0])

            seen.clear()
            # The 5.25-inch geometry has no blank layout HxCFE can build, so
            # no hint is passed and the conversion is refused elsewhere.
            ofs = Path(folder) / "disk.adf"
            ofs.write_bytes(bytes(450_560))
            FluxEngine(run).encode_from_sectors(
                ofs, HFE, Path(folder) / "out.hfe", kind="ofs"
            )
            self.assertFalse([item for item in seen[0] if item.startswith("-uselayout:")])
            self.assertIn("-conv:HXC_HFE", seen[0])

    def test_encode_passes_the_reference_container_only_when_given(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            sectors = Path(folder) / "disk.adf"
            sectors.write_bytes(bytes(450_560))
            original = Path(folder) / "capture.scp"
            original.write_bytes(b"SCP")
            seen: list[list[str]] = []

            def run(arguments):
                seen.append(arguments)
                _write_output(arguments, b"FLUX")
                return ""

            engine = FluxEngine(run)
            engine.encode_from_sectors(
                sectors, SCP, Path(folder) / "a.scp", kind="ofs", reference=original
            )
            self.assertEqual(_argument(seen[0], "-reffile:"), str(original))

            seen.clear()
            engine.encode_from_sectors(sectors, SCP, Path(folder) / "b.scp", kind="ofs")
            self.assertIsNone(_argument(seen[0], "-reffile:"))

    def test_round_trip_check_tolerates_one_omitted_tail_sector(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            sectors = Path(folder) / "disk.adf"
            sectors.write_bytes(bytes(901_120))
            container = Path(folder) / "disk.scp"
            container.write_bytes(b"SCP")

            def run(arguments):
                _write_output(arguments, bytes(901_120 - SECTOR_SIZE))
                return ""

            self.assertTrue(FluxEngine(run).decodes_back_to(container, sectors, "ffs"))

    def test_round_trip_check_rejects_genuinely_different_sectors(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            sectors = Path(folder) / "disk.adf"
            sectors.write_bytes(b"\x01" * 450_560)
            container = Path(folder) / "disk.hfe"
            container.write_bytes(b"HXC")

            def run(arguments):
                _write_output(arguments, b"\x02" * 450_560)
                return ""

            self.assertFalse(FluxEngine(run).decodes_back_to(container, sectors, "ofs"))

    def test_round_trip_check_treats_an_engine_failure_as_no_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            sectors = Path(folder) / "disk.adf"
            sectors.write_bytes(bytes(450_560))
            container = Path(folder) / "disk.hfe"
            container.write_bytes(b"HXC")

            def run(_arguments):
                raise DiskError("HxCFE failed")

            self.assertFalse(FluxEngine(run).decodes_back_to(container, sectors, "ofs"))

    def test_round_trip_check_removes_its_temporary_decode(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            sectors = Path(folder) / "disk.adf"
            sectors.write_bytes(bytes(450_560))
            container = Path(folder) / "disk.hfe"
            container.write_bytes(b"HXC")

            def run(arguments):
                _write_output(arguments, bytes(450_560))
                return ""

            FluxEngine(run).decodes_back_to(container, sectors, "ofs")
            self.assertEqual(list(Path(folder).glob("*-verify.img")), [])

    def test_encode_and_verify_returns_a_container_that_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            sectors = Path(folder) / "disk.adf"
            sectors.write_bytes(bytes(450_560))
            output = Path(folder) / "out.scp"

            def run(arguments):
                if any(item.startswith("-conv:SCP_FLUX_STREAM") for item in arguments):
                    _write_output(arguments, b"SCP-FLUX")
                else:
                    _write_output(arguments, bytes(450_560))
                return ""

            result = FluxEngine(run).encode_and_verify(
                sectors, SCP, output, kind="ofs", failure_message="nope"
            )
            self.assertEqual(result, output)
            self.assertEqual(output.read_bytes(), b"SCP-FLUX")

    def test_encode_and_verify_discards_a_container_that_does_not_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            sectors = Path(folder) / "disk.adf"
            sectors.write_bytes(bytes(450_560))
            output = Path(folder) / "out.scp"

            def run(arguments):
                if any(item.startswith("-conv:SCP_FLUX_STREAM") for item in arguments):
                    _write_output(arguments, b"SCP-FLUX")
                else:
                    _write_output(arguments, bytes(1))
                return ""

            with self.assertRaisesRegex(DiskError, "sectors did not match"):
                FluxEngine(run).encode_and_verify(
                    sectors,
                    SCP,
                    output,
                    kind="ofs",
                    failure_message="The sectors did not match.",
                )
            self.assertFalse(
                output.exists(),
                "an unverified flux image must not be left on disk",
            )

    def test_encode_and_verify_rejects_an_empty_engine_result(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            sectors = Path(folder) / "disk.adf"
            sectors.write_bytes(bytes(450_560))

            def run(_arguments):
                return ""

            with self.assertRaisesRegex(DiskError, "did not produce a usable SCP"):
                FluxEngine(run).encode_and_verify(
                    sectors,
                    SCP,
                    Path(folder) / "out.scp",
                    kind="ofs",
                    failure_message="unused",
                )


if __name__ == "__main__":
    unittest.main()
