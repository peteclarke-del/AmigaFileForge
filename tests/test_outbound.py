from __future__ import annotations

import socket
import unittest
from unittest.mock import patch

from app.errors import DiskError
from app.outbound import checked_url, http_url, private_addresses_allowed


def _resolves_to(*addresses: str):
    """Pretend a host resolves to these addresses, without using DNS."""
    return patch(
        "app.outbound.socket.getaddrinfo",
        return_value=[
            (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, 80))
            for address in addresses
        ],
    )


class UrlShapeTests(unittest.TestCase):
    """Configuration is validated without touching the network."""

    def test_http_and_https_are_accepted(self) -> None:
        for url in ("http://example.com/list", "https://example.com/list"):
            with self.subTest(url=url):
                self.assertEqual(http_url(url), url)

    def test_other_schemes_are_refused(self) -> None:
        for url in (
            "file:///etc/passwd",
            "ftp://example.com/x",
            "gopher://example.com",
            "data:text/plain,hello",
            "javascript:alert(1)",
        ):
            with self.subTest(url=url):
                with self.assertRaises(DiskError):
                    http_url(url)

    def test_a_url_without_a_host_is_refused(self) -> None:
        for url in ("", "   ", "http://", "https:///path", None):
            with self.subTest(url=url):
                with self.assertRaises(DiskError):
                    http_url(url)

    def test_shape_validation_never_resolves_a_name(self) -> None:
        """Saving a source must work offline."""
        with patch("app.outbound.socket.getaddrinfo", side_effect=AssertionError("resolved")):
            self.assertEqual(http_url("https://example.test/x"), "https://example.test/x")


class DestinationTests(unittest.TestCase):
    """The destination is checked at the moment a request is about to be made."""

    def test_a_public_address_is_allowed(self) -> None:
        with _resolves_to("93.184.216.34"):
            self.assertEqual(checked_url("https://example.com/x"), "https://example.com/x")

    def test_loopback_and_private_networks_are_refused(self) -> None:
        for address in (
            "127.0.0.1",        # this machine
            "::1",              # this machine, IPv6
            "10.0.0.5",         # private
            "192.168.1.1",      # private, a home router
            "172.16.4.4",       # private
            "169.254.169.254",  # link-local, cloud instance metadata
            "0.0.0.0",          # unspecified
            "224.0.0.1",        # multicast
        ):
            with self.subTest(address=address):
                with _resolves_to(address):
                    with self.assertRaisesRegex(DiskError, "private network"):
                        checked_url("https://internal.example/x")

    def test_a_host_is_refused_when_any_address_is_private(self) -> None:
        """A name with a mixed answer must not slip through on one good record."""
        with _resolves_to("93.184.216.34", "127.0.0.1"):
            with self.assertRaisesRegex(DiskError, "private network"):
                checked_url("https://mixed.example/x")

    def test_a_name_that_does_not_resolve_is_reported(self) -> None:
        with patch("app.outbound.socket.getaddrinfo", side_effect=socket.gaierror("no such host")):
            with self.assertRaisesRegex(DiskError, "Could not resolve"):
                checked_url("https://nowhere.example/x")

    def test_the_scheme_is_still_enforced_before_any_lookup(self) -> None:
        with patch("app.outbound.socket.getaddrinfo", side_effect=AssertionError("resolved")):
            with self.assertRaises(DiskError):
                checked_url("file:///etc/passwd")


class PrivateOverrideTests(unittest.TestCase):
    """A local archive mirror is a real setup, so the rule can be lifted."""

    def test_the_override_is_off_by_default(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(private_addresses_allowed())

    def test_the_override_permits_a_private_mirror(self) -> None:
        with patch.dict("os.environ", {"AMIGA_ALLOW_PRIVATE_SOURCES": "1"}):
            with patch("app.outbound.socket.getaddrinfo", side_effect=AssertionError("resolved")):
                self.assertEqual(
                    checked_url("http://192.168.1.10/mirror"), "http://192.168.1.10/mirror"
                )

    def test_only_explicit_affirmatives_enable_the_override(self) -> None:
        for value in ("", "0", "no", "false", "maybe"):
            with self.subTest(value=value):
                with patch.dict("os.environ", {"AMIGA_ALLOW_PRIVATE_SOURCES": value}):
                    self.assertFalse(private_addresses_allowed())


class CatalogueIntegrationTests(unittest.TestCase):
    """The online library routes its requests through the shared policy."""

    def test_the_catalogue_checks_destinations_before_fetching(self) -> None:
        import tempfile
        from pathlib import Path

        from app.catalog_service import CatalogueService

        with tempfile.TemporaryDirectory() as folder:
            service = CatalogueService(Path(folder))
            with _resolves_to("127.0.0.1"):
                with self.assertRaisesRegex(DiskError, "private network"):
                    service._fetch("http://localhost:8666/admin")


if __name__ == "__main__":
    unittest.main()
