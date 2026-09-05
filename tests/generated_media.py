from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from app.disk_service import DiskService, ImageSession
from tests.dms_fixture import minimal_dms


@dataclass(frozen=True)
class GeneratedMedium:
    format: str
    session: ImageSession


def generated_media_matrix(
    service: DiskService, *, include_flux: bool = False
) -> list[GeneratedMedium]:
    """Create representative media using only public application APIs.

    Flux containers need the HxC engine, which is present in the application
    container but not necessarily on a development host, so they are opt-in.
    """
    rows = [
        GeneratedMedium("adf", service.create_blank("adf", "TestOFS")),
        GeneratedMedium("adf-intl", service.create_blank("adf-intl", "TestOFSIntl")),
        GeneratedMedium("adf-dc", service.create_blank("adf-dc", "TestOFSCache")),
        GeneratedMedium("ffs", service.create_blank("ffs", "TestFFS")),
        GeneratedMedium("ffs-intl", service.create_blank("ffs-intl", "TestFFSIntl")),
        GeneratedMedium("ffs-dc", service.create_blank("ffs-dc", "TestFFSCache")),
        GeneratedMedium("adf-hd", service.create_blank("adf-hd", "TestOFSHD")),
        GeneratedMedium("ffs-hd", service.create_blank("ffs-hd", "TestFFSHD")),
        GeneratedMedium("ffs-hd-dc", service.create_blank("ffs-hd-dc", "TestFFSHDCache")),
        GeneratedMedium(
            "hardfile",
            service.create_blank("hardfile", "TestDrive", "20MB", "hardfile"),
        ),
        GeneratedMedium(
            "ffs-hard", service.create_blank("ffs-hard", "TestRDB", "20MB")
        ),
        GeneratedMedium(
            "rom",
            service.create_blank(
                "rom",
                "TestROM",
                options={"bankSize": 256 * 1024, "totalSize": 512 * 1024},
            ),
        ),
        GeneratedMedium("kickfs", service.create_blank("kickfs", "TestRom")),
    ]
    if include_flux:
        rows.append(GeneratedMedium("hfe", service.create_blank("hfe-adf", "TestHFE")))
    dms = service.create_from_stream("test.dms", io.BytesIO(minimal_dms()))
    rows.append(GeneratedMedium("dms", dms))
    return rows


def add_test_file(
    service: DiskService,
    session: ImageSession,
    host_root: Path,
    *,
    path: str = "Test",
    payload: bytes = b"Amiga File Forge generated fixture\n",
) -> None:
    source = host_root / f"fixture-{session.id}.bin"
    source.write_bytes(payload)
    service.put(session, path, source)
