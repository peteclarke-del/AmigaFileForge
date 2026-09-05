"""Pure helpers for constructing logical ROM bytes from physical chip files."""

from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path


MAX_COMBINED_ROM_SIZE = 64 * 1024 * 1024
MAX_ROM_COMPONENTS = 256
COPY_BLOCK_SIZE = 1024 * 1024
ROM_COMPONENT_LAYOUTS = {"linear", "byte-interleaved-2", "byte-interleaved-4"}


def write_combined_rom(
    component_paths: list[Path], output_path: Path, layout: str = "linear"
) -> None:
    components = [Path(path) for path in component_paths]
    if not components:
        raise ValueError("Choose at least one ROM component.")
    if len(components) > MAX_ROM_COMPONENTS:
        raise ValueError(
            f"A ROM set cannot contain more than {MAX_ROM_COMPONENTS} components."
        )
    layout = str(layout or "linear")
    if layout not in ROM_COMPONENT_LAYOUTS:
        raise ValueError("Choose a linear, two-chip or four-chip ROM byte layout.")
    sizes = [path.stat().st_size for path in components]
    if sum(sizes) > MAX_COMBINED_ROM_SIZE:
        raise ValueError("That ROM set is larger than the 64 MiB workbench safety limit.")
    interleaved = layout.startswith("byte-interleaved")
    expected_components = int(layout.rsplit("-", 1)[-1]) if interleaved else None
    if interleaved and len(components) != expected_components:
        raise ValueError(
            f"The {layout} layout requires exactly {expected_components} components."
        )
    if interleaved and len(set(sizes)) != 1:
        raise ValueError("Byte-interleaved ROM components must have exactly equal sizes.")
    output_path = Path(output_path)
    if interleaved:
        with ExitStack() as stack:
            sources = [stack.enter_context(path.open("rb")) for path in components]
            output = stack.enter_context(output_path.open("wb"))
            remaining = sizes[0]
            while remaining:
                amount = min(COPY_BLOCK_SIZE, remaining)
                rows = [source.read(amount) for source in sources]
                if any(len(row) != amount for row in rows):
                    raise ValueError("A ROM component changed while it was being read.")
                combined = bytearray(amount * len(rows))
                for index, row in enumerate(rows):
                    combined[index::len(rows)] = row
                output.write(combined)
                remaining -= amount
        return
    with output_path.open("wb") as output:
        for path, size in zip(components, sizes, strict=True):
            with path.open("rb") as source:
                remaining = size
                while remaining:
                    chunk = source.read(min(COPY_BLOCK_SIZE, remaining))
                    if not chunk:
                        raise ValueError("A ROM component changed while it was being read.")
                    output.write(chunk)
                    remaining -= len(chunk)


__all__ = [
    "COPY_BLOCK_SIZE",
    "MAX_COMBINED_ROM_SIZE",
    "MAX_ROM_COMPONENTS",
    "ROM_COMPONENT_LAYOUTS",
    "write_combined_rom",
]
