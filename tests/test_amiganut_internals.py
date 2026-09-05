from __future__ import annotations

import ast
import unittest
from pathlib import Path

from app import amiganut_internals


APP_ROOT = Path(__file__).resolve().parent.parent / "app"
ADAPTER = APP_ROOT / "amiganut_internals.py"


def _private_amiganut_imports(source: Path) -> list[str]:
    """Return private Amiganut names imported by one module."""
    tree = ast.parse(source.read_text(encoding="utf-8"))
    borrowed: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        if node.module != "amiganut" and not node.module.startswith("amiganut."):
            continue
        borrowed.extend(
            alias.name for alias in node.names if alias.name.startswith("_")
        )
    return borrowed


class AmiganutAdapterTests(unittest.TestCase):
    """The borrowed private API must stay real, named, and contained."""

    def test_every_borrowed_symbol_exists_in_the_pinned_release(self) -> None:
        """An Amiganut upgrade that drops a private name fails here, not in front of a user."""
        from amiganut.disc import cli

        for name in amiganut_internals.BORROWED_SYMBOLS:
            with self.subTest(symbol=name):
                self.assertTrue(
                    hasattr(cli, name),
                    f"amiganut.disc.cli no longer provides {name}; the pinned "
                    "version has changed and app/amiganut_internals.py needs review",
                )

    def test_every_borrowed_symbol_is_callable(self) -> None:
        from amiganut.disc import cli

        for name in amiganut_internals.BORROWED_SYMBOLS:
            with self.subTest(symbol=name):
                self.assertTrue(callable(getattr(cli, name)))

    def test_public_names_are_bound_to_the_borrowed_symbols(self) -> None:
        from amiganut.disc import cli

        expected = {
            "file_copy_item": "_file_item",
            "collect_copy_items": "_collect_copy_items",
            "in_storage_order": "_in_global_storage_order",
            "ensure_directory_chain": "_ensure_dir_chain",
            "write_copy_item": "_write_copy_item",
            "walk_post_order": "_walk_post_order_mount",
            "natural_name_key": "_natural_name_key",
        }
        for public, private in expected.items():
            with self.subTest(name=public):
                self.assertIs(
                    getattr(amiganut_internals, public),
                    getattr(cli, private),
                )

    def test_the_declared_symbol_list_matches_what_is_re_exported(self) -> None:
        """BORROWED_SYMBOLS is the contract the upgrade check reads; keep it honest."""
        self.assertEqual(
            sorted(amiganut_internals.BORROWED_SYMBOLS),
            sorted(_private_amiganut_imports(ADAPTER)),
        )

    def test_no_other_module_reaches_into_the_private_amiganut_api(self) -> None:
        """Borrowing private names is contained to one reviewed module."""
        offenders: dict[str, list[str]] = {}
        for source in sorted(APP_ROOT.rglob("*.py")):
            if source == ADAPTER:
                continue
            borrowed = _private_amiganut_imports(source)
            if borrowed:
                offenders[str(source.relative_to(APP_ROOT.parent))] = borrowed
        self.assertEqual(
            offenders,
            {},
            "private Amiganut symbols must be imported through "
            "app/amiganut_internals.py so an upgrade has one place to review",
        )

    def test_the_adapter_exports_only_public_names(self) -> None:
        for name in amiganut_internals.__all__:
            with self.subTest(name=name):
                self.assertFalse(name.startswith("_"))
                self.assertTrue(hasattr(amiganut_internals, name))


if __name__ == "__main__":
    unittest.main()
