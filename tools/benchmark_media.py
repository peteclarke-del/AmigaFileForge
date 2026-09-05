#!/usr/bin/env python3
"""Repeatable generated-image performance benchmarks for release checks."""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path

from app.disk_service import DiskService
from app.download_archive import build_download_archive


def measure(name: str, callback, repeats: int = 3) -> dict:
    timings = []
    for _attempt in range(repeats):
        started = time.perf_counter()
        callback()
        timings.append(time.perf_counter() - started)
    return {
        "name": name,
        "runs": repeats,
        "minimumSeconds": min(timings),
        "medianSeconds": statistics.median(timings),
        "maximumSeconds": max(timings),
    }


def run(profile: str = "quick") -> dict:
    repeats = 2 if profile == "quick" else 5
    file_count = 20 if profile == "quick" else 100
    with tempfile.TemporaryDirectory(prefix="amiga-forge-benchmark-") as folder:
        root = Path(folder)
        service = DiskService(root / "work")
        drive = service.create_blank("ffs-hard", "BENCH", "20MB")
        # A drive opens on its partition table. Selecting the first partition
        # is what opening one in the interface does, and it is what gives the
        # benchmark a volume to write into.
        service.select_partition(drive, 0)
        adf = service.create_blank("adf", "BENCH")
        hardfile = service.create_blank("hardfile", "BENCHSCSI", "20MB", "hardfile")
        files = []
        for number in range(file_count):
            path = root / f"FILE{number:04d}"
            path.write_bytes((f"generated-{number}\n".encode("ascii")) * 16)
            group = number // 40 + 1
            files.append({"targetPath": f"PACK{group}/FILE{number:04d}", "hostPath": path})
        for number in range(min(20, file_count)):
            service.put(adf, f"F{number:02d}", Path(files[number]["hostPath"]))
        service.put_host_tree(drive, "", files, preserve_directories=True)
        service.make_directory(hardfile, "GAMES")

        results = [
            measure(
                "drive-list-partitions",
                lambda: service.list_partitions(drive),
                repeats,
            ),
            measure("floppy-list-20-files", lambda: service.browse_directory(adf, ""), repeats),
            measure(
                "drive-list-generated-tree",
                lambda: service.browse_directory(drive, "PACK1"),
                repeats,
            ),
            measure(
                f"drive-bulk-import-{file_count}-files",
                lambda: _bulk_import(service, files),
                repeats,
            ),
            measure("hardfile-root-list", lambda: service.browse_directory(hardfile, ""), repeats),
            measure("hardfile-checkpoint", lambda: _checkpoint_round_trip(service, hardfile), repeats),
            measure(
                "hardfile-save-archive",
                lambda: build_download_archive(service, hardfile),
                1 if profile == "quick" else 2,
            ),
        ]
        return {
            "schema": 1,
            "profile": profile,
            "generatedFiles": file_count,
            "benchmarks": results,
        }


def _checkpoint_round_trip(service: DiskService, session) -> None:
    token = service.begin_automatic_checkpoint(session, "benchmark")
    service.finish_automatic_checkpoint(session, token)


def _bulk_import(service: DiskService, files: list[dict]) -> None:
    target = service.create_blank("ffs-hard", "IMPORT", "20MB")
    service.select_partition(target, 0)
    try:
        service.put_host_tree(target, "", files, preserve_directories=True)
    finally:
        service.discard_session(target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("quick", "full"), default="quick")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    result = run(arguments.profile)
    rendered = json.dumps(result, indent=2)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
