from __future__ import annotations

"""Shared helpers for Dank Shield Community Tools.

This module used to own the old Community Tools sticky store
(``dank_stickies`` / ``dank_sticky_polls``). That system was removed —
polls and stickies live in Post Builder now — and its tables are left
inert in the database as an archive (a JSON backup was captured before
removal). What survives here is only the shared floor other Community
Tools features still use: the storage exceptions, the PostgREST page
size, ``utc_now``, and the SSRF-safe HTTPS URL normalizer (Quiet Server
Notices validate partner links with it).
"""

import ipaddress
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse, urlunparse

POSTGREST_PAGE_SIZE = 500


class CommunityStorageUnavailable(RuntimeError):
    """Raised when persistent Community Tools storage is unavailable."""


class InvalidCommunityToolValue(ValueError):
    """Raised when a Community Tools value cannot be used safely."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_https_url(value: Any, *, allow_empty: bool = True) -> str:
    raw = str(value or "").strip()
    if not raw:
        if allow_empty:
            return ""
        raise InvalidCommunityToolValue("Enter a valid HTTPS URL.")
    if len(raw) > 1000:
        raise InvalidCommunityToolValue("That URL is too long.")

    parsed = urlparse(raw)
    if parsed.scheme.lower() != "https":
        raise InvalidCommunityToolValue("Only HTTPS URLs are allowed.")
    if parsed.username or parsed.password:
        raise InvalidCommunityToolValue("URLs containing credentials are not allowed.")
    try:
        if parsed.port is not None:
            raise InvalidCommunityToolValue("Custom URL ports are not allowed.")
    except ValueError as exc:
        raise InvalidCommunityToolValue("That URL has an invalid port.") from exc

    host = str(parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise InvalidCommunityToolValue("That URL does not contain a valid hostname.")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise InvalidCommunityToolValue("Local/private URLs are not allowed.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
    ):
        raise InvalidCommunityToolValue("Local/private IP addresses are not allowed.")

    return urlunparse(("https", host, parsed.path or "", "", parsed.query or "", parsed.fragment or ""))


__all__ = [
    "CommunityStorageUnavailable",
    "InvalidCommunityToolValue",
    "POSTGREST_PAGE_SIZE",
    "normalize_https_url",
    "utc_now",
]
