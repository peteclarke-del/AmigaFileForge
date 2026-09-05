"""The single place Amiga File Forge reaches into Amiganut's private API.

The workbench needs bulk-copy behaviour that ``amiganut.disc.cli`` implements
but does not export: preserving protection bits, comments and datestamps
across a directory tree, ordering writes so the destination's block
allocation stays compact, and walking a tree post-order so directories are
removed after their contents. Re-deriving that from the public API would mean
re-deriving AmigaDOS allocation policy, which is exactly the sort of
duplication this project avoids.

Borrowing underscore-prefixed names from a dependency is a real risk, so it is
confined here rather than scattered across the service modules:

* ``amiganut`` ships in this repository at a known version. These names are
  verified against that version by ``tests/test_amiganut_internals.py``, so a
  change fails loudly in CI instead of at runtime in front of a user.
* ``tests/test_component_boundaries.py`` asserts that no other module imports a
  private Amiganut symbol, so the surface cannot grow by accident.
* Every symbol is re-exported under a descriptive public name, so calling code
  reads as ordinary domain vocabulary and a future public API can be swapped in
  by editing this module alone.

Import failures are reported once, in the caller's language, instead of being
re-wrapped with a slightly different message at each of the previous call sites.
"""

from __future__ import annotations

from .errors import DiskError


_UNAVAILABLE = (
    "The Amiganut bulk-copy API is unavailable. This build of Amiga File Forge "
    "expects the bundled Amiganut engine; reinstall the application package."
)


try:  # pragma: no cover - exercised by the import-failure test below
    from amiganut.disc.cli import (
        _collect_copy_items,
        _ensure_dir_chain,
        _file_item,
        _in_global_storage_order,
        _natural_name_key,
        _walk_post_order_mount,
        _write_copy_item,
    )
except ImportError as exc:  # pragma: no cover - defensive
    raise DiskError(_UNAVAILABLE) from exc


# Public vocabulary for the rest of the application. The right-hand side is the
# only place an Amiganut private name appears.

#: Build one copy descriptor for a file, carrying its Amiga metadata.
file_copy_item = _file_item

#: Collect copy descriptors for a directory tree.
collect_copy_items = _collect_copy_items

#: Order copy descriptors so AmigaDOS allocation stays compact.
in_storage_order = _in_global_storage_order

#: Create any missing parent directories for a destination path.
ensure_directory_chain = _ensure_dir_chain

#: Write one copy descriptor into a mounted filesystem.
write_copy_item = _write_copy_item

#: Walk a directory tree children-first, so directories can be removed safely.
walk_post_order = _walk_post_order_mount

#: Sort key matching the catalogue order the AmigaDOS shell presents.
natural_name_key = _natural_name_key


BORROWED_SYMBOLS = (
    "_collect_copy_items",
    "_ensure_dir_chain",
    "_file_item",
    "_in_global_storage_order",
    "_natural_name_key",
    "_walk_post_order_mount",
    "_write_copy_item",
)


__all__ = [
    "BORROWED_SYMBOLS",
    "collect_copy_items",
    "ensure_directory_chain",
    "file_copy_item",
    "in_storage_order",
    "natural_name_key",
    "walk_post_order",
    "write_copy_item",
]
