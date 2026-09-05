"""One policy for every request this application makes to the network.

The online library and the metadata lookup both fetch URLs, and the catalogue
sources are editable through the API. Without a check, anyone who can reach the
service could point it at an address only the server can see: another container,
a router's admin page, or a cloud instance-metadata endpoint. The Compose file
publishes port 8666 on every interface, so that is not purely theoretical on a
shared network.

Requests are therefore restricted to ``http`` and ``https`` and to addresses
that are publicly routable. A host that resolves to a loopback, private,
link-local, reserved or multicast address is refused before any connection is
made.

A local mirror of an archive is a legitimate setup, so the restriction can be
lifted deliberately with ``AMIGA_ALLOW_PRIVATE_SOURCES=1``. It is opt-in
because the safe default should not depend on the operator noticing.

This narrows the target set; it is not a complete defence. A name that resolves
to a public address at validation and a private one at connection time would
still pass, and defeating that needs the resolved address to be pinned through
to the socket. That is a deliberate limit, recorded so nobody assumes more
protection than exists.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import urllib.parse

from .errors import DiskError


ALLOWED_SCHEMES = frozenset({"http", "https"})

_PRIVATE_OVERRIDE = "AMIGA_ALLOW_PRIVATE_SOURCES"


def private_addresses_allowed() -> bool:
    """Whether the operator has opted in to non-public sources."""
    return os.environ.get(_PRIVATE_OVERRIDE, "").strip() in {"1", "true", "yes"}


def _is_public(address: str) -> bool:
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return not (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_reserved
        or parsed.is_multicast
        or parsed.is_unspecified
    )


def http_url(url: object, message: str = "The catalogue supplied an invalid URL.") -> str:
    """Check a URL's shape only, without touching the network.

    Saving a source must not depend on DNS or on being online, so configuration
    is validated here and the destination is checked separately when the
    request is actually about to be made.
    """
    value = str(url or "").strip()
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.netloc:
        raise DiskError(message)
    try:
        if not parsed.hostname:
            raise DiskError(message)
    except ValueError as exc:
        raise DiskError(message) from exc
    return value


def checked_url(url: object, message: str = "The catalogue supplied an invalid URL.") -> str:
    """Return a URL that is safe to request right now.

    Called immediately before a request, because a destination that was
    acceptable when it was saved may not be acceptable when it is used.
    """
    value = http_url(url, message)
    parsed = urllib.parse.urlparse(value)
    host = parsed.hostname or ""
    if private_addresses_allowed():
        return value
    try:
        resolved = socket.getaddrinfo(host, parsed.port or None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError) as exc:
        raise DiskError(f"Could not resolve {host}.") from exc
    addresses = {info[4][0] for info in resolved}
    if not addresses or not all(_is_public(address) for address in addresses):
        raise DiskError(
            f"{host} resolves to an address on this machine or a private network. "
            "Online sources must be publicly reachable. Set "
            f"{_PRIVATE_OVERRIDE}=1 to allow a local mirror on a trusted network."
        )
    return value


__all__ = ["ALLOWED_SCHEMES", "http_url", "private_addresses_allowed"]
