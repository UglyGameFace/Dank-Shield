from __future__ import annotations

"""Canonical public-network safety helpers for Share Router media."""

import ipaddress
from typing import Any, Mapping
from urllib.parse import urljoin, urlsplit

import aiohttp


_LOCAL_HOST_SUFFIXES = (
    ".local",
    ".localhost",
    ".internal",
    ".lan",
    ".home",
    ".home.arpa",
)
_SAFE_REQUEST_HEADERS = {
    "accept",
    "accept-language",
    "origin",
    "referer",
    "user-agent",
}


class UnsafeMediaURL(ValueError):
    pass


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def is_public_address(value: str) -> bool:
    try:
        return bool(ipaddress.ip_address(str(value or "").strip()).is_global)
    except ValueError:
        return False


def is_safe_media_download_url(value: str) -> bool:
    raw = _safe_str(value)
    if not raw:
        return False
    try:
        parsed = urlsplit(raw)
    except Exception:
        return False
    if str(parsed.scheme or "").lower() not in {"http", "https"}:
        return False
    if parsed.username or parsed.password:
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    if port not in {None, 80, 443}:
        return False

    host = str(parsed.hostname or "").lower().strip(".")
    if not host or host == "localhost" or host.endswith(_LOCAL_HOST_SUFFIXES):
        return False

    try:
        ipaddress.ip_address(host)
    except ValueError:
        return "." in host
    return is_public_address(host)


def safe_media_headers(
    headers: Mapping[str, Any] | tuple[tuple[str, str], ...] | None,
) -> dict[str, str]:
    clean: dict[str, str] = {}
    for key, value in dict(headers or {}).items():
        name = _safe_str(key)
        text = _safe_str(value).replace("\r", " ").replace("\n", " ").strip()
        if not name or not text or name.lower() not in _SAFE_REQUEST_HEADERS:
            continue
        clean[name] = text[:1000]
    return clean


class PublicOnlyDNSResolver(aiohttp.abc.AbstractResolver):
    """Reject DNS answers that would route media traffic to non-public IPs."""

    def __init__(self) -> None:
        self._resolver = aiohttp.DefaultResolver()

    async def resolve(self, host: str, port: int = 0, family: int = 0):
        records = await self._resolver.resolve(host, port, family)
        if not records:
            raise OSError("media host did not resolve")
        for record in records:
            try:
                address = str(record["host"])
            except Exception as exc:
                raise OSError("media DNS answer was malformed") from exc
            if not is_public_address(address):
                raise OSError("media host resolved to a non-public address")
        return records

    async def close(self) -> None:
        await self._resolver.close()


def public_tcp_connector(*, ttl_dns_cache: int = 60) -> aiohttp.TCPConnector:
    return aiohttp.TCPConnector(
        resolver=PublicOnlyDNSResolver(),
        ttl_dns_cache=max(0, int(ttl_dns_cache)),
    )


async def public_get(
    session: aiohttp.ClientSession,
    url: str,
    *,
    headers: Mapping[str, Any] | tuple[tuple[str, str], ...] | None = None,
    max_redirects: int = 4,
) -> tuple[aiohttp.ClientResponse, str]:
    """GET a public URL and validate every redirect before the next request."""

    current = _safe_str(url)
    safe_headers = safe_media_headers(headers)

    for _ in range(max(0, int(max_redirects)) + 1):
        if not is_safe_media_download_url(current):
            raise UnsafeMediaURL("media URL is not a safe public HTTP(S) target")

        response = await session.get(
            current,
            allow_redirects=False,
            headers=safe_headers or None,
        )
        if response.status not in {301, 302, 303, 307, 308}:
            return response, str(response.url)

        location = _safe_str(response.headers.get("Location"))
        response.release()
        if not location:
            raise UnsafeMediaURL("redirect omitted Location")
        current = urljoin(current, location)

    raise UnsafeMediaURL("too many media redirects")


__all__ = [
    "PublicOnlyDNSResolver",
    "UnsafeMediaURL",
    "is_public_address",
    "is_safe_media_download_url",
    "public_get",
    "public_tcp_connector",
    "safe_media_headers",
]
