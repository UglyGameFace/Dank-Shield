from __future__ import annotations

"""Safe resolver for guild-configured Movie Night HTTPS media feeds."""

import asyncio
import ipaddress
import json
import socket
from dataclasses import dataclass
from typing import Any, Mapping, Optional
from urllib.parse import parse_qsl, quote_plus, urlencode, urljoin, urlsplit, urlunsplit

import aiohttp

from stoney_verify.media_metadata import parse_release_name
from stoney_verify.media_source_registry import (
    CustomMediaSource,
    enabled_custom_sources,
    load_media_source_registry,
)

_MAX_SOURCE_RESULTS = 25
_MAX_TOTAL_RESULTS = 100
_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_CONCURRENCY = 4
_TIMEOUT_SECONDS = 8.0


def _public_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(str(value or "").split("%", 1)[0])
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


class PublicOnlyResolver(aiohttp.abc.AbstractResolver):
    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_UNSPEC,
    ) -> list[dict[str, Any]]:
        infos = await asyncio.to_thread(
            socket.getaddrinfo,
            host,
            port,
            family,
            socket.SOCK_STREAM,
        )
        resolved: list[dict[str, Any]] = []
        seen: set[tuple[str, int]] = set()
        for fam, socktype, proto, _canonname, sockaddr in infos:
            address = str(sockaddr[0])
            actual_port = int(sockaddr[1])
            if not _public_ip(address):
                raise OSError("Custom media source resolved to a private/reserved address.")
            key = (address, actual_port)
            if key in seen:
                continue
            seen.add(key)
            resolved.append(
                {
                    "hostname": host,
                    "host": address,
                    "port": actual_port,
                    "family": fam,
                    "proto": proto,
                    "flags": 0,
                }
            )
        if not resolved:
            raise OSError("Custom media source did not resolve to a public address.")
        return resolved

    async def close(self) -> None:
        return None


@dataclass(frozen=True)
class ResolvedMediaVariant:
    title: str
    source_id: str
    source_label: str
    source_ref: str
    file_size: int
    seeds: int
    leechers: int
    peers: int
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class MediaSourceSearchOutcome:
    variants: tuple[ResolvedMediaVariant, ...]
    errors: tuple[str, ...] = ()


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except Exception:
        return 0


def _clean_title(value: Any) -> str:
    return " ".join(str(value or "").split())[:180]


def _safe_source_ref(value: Any) -> str:
    raw = str(value or "").strip().strip("<>")
    if not raw:
        return ""
    lowered = raw.lower()
    if lowered.startswith("magnet:?"):
        return raw[:4096]
    try:
        parsed = urlsplit(raw)
    except Exception:
        return ""
    if str(parsed.scheme or "").lower() != "https" or not parsed.hostname:
        return ""
    host = str(parsed.hostname).lower().strip(".")
    if host in {"localhost"} or host.endswith(".localhost") or host.endswith(".local"):
        return ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and not _public_ip(host):
        return ""
    return raw[:4096]


def _search_url(endpoint: str, query: str) -> str:
    clean_query = " ".join(str(query or "").split())[:180]
    if not clean_query:
        raise ValueError("Movie search query is empty.")

    if "{query}" in endpoint:
        return endpoint.replace("{query}", quote_plus(clean_query))

    parsed = urlsplit(endpoint)
    pairs = list(parse_qsl(parsed.query, keep_blank_values=True))
    pairs.append(("q", clean_query))
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(pairs),
            "",
        )
    )


def _validate_request_url(value: str) -> str:
    parsed = urlsplit(str(value or "").strip())
    if str(parsed.scheme or "").lower() != "https":
        raise ValueError("Custom media source requests must stay on HTTPS.")
    if parsed.username or parsed.password or not parsed.hostname:
        raise ValueError("Custom media source request URL is unsafe.")
    host = str(parsed.hostname).lower().strip(".")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise ValueError("Custom media source request points to a local host.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and not _public_ip(host):
        raise ValueError("Custom media source request points to a private/reserved address.")
    return parsed.geturl()


def _extract_items(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("results", "items", "releases", "variants"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
    return []


def _variant_from_item(
    source: CustomMediaSource,
    item: Mapping[str, Any],
) -> Optional[ResolvedMediaVariant]:
    title = _clean_title(
        item.get("title")
        or item.get("name")
        or item.get("movie")
    )
    source_ref = _safe_source_ref(
        item.get("source_ref")
        or item.get("magnet")
        or item.get("url")
    )
    if not title or not source_ref:
        return None

    release_name = _clean_title(
        item.get("release_name")
        or item.get("filename")
        or item.get("name")
        or title
    )
    inferred = parse_release_name(release_name)

    source_reported: dict[str, Any] = {}
    raw_meta = item.get("metadata")
    if isinstance(raw_meta, Mapping):
        for key, value in list(raw_meta.items())[:64]:
            clean_key = _clean_title(key)[:80]
            if not clean_key:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                source_reported[clean_key] = value

    metadata: dict[str, Any] = {
        "release_name": inferred,
        "source_reported": source_reported,
        "source_reported_verified": False,
    }

    seeds = _safe_int(item.get("seeds") or item.get("seeders"))
    leechers = _safe_int(
        item.get("leechers")
        or item.get("leeches")
        or item.get("leechers_count")
    )
    peers = max(
        seeds + leechers,
        _safe_int(item.get("peers") or item.get("peer_count")),
    )

    return ResolvedMediaVariant(
        title=title,
        source_id=source.source_id,
        source_label=source.label,
        source_ref=source_ref,
        file_size=_safe_int(
            item.get("file_size")
            or item.get("size_bytes")
            or item.get("size")
        ),
        seeds=seeds,
        leechers=leechers,
        peers=peers,
        metadata=metadata,
    )


async def _read_json_limited(response: aiohttp.ClientResponse) -> Any:
    length = _safe_int(response.headers.get("Content-Length"))
    if length > _MAX_RESPONSE_BYTES:
        raise ValueError("source response exceeds the 1 MiB limit")

    payload = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        payload.extend(chunk)
        if len(payload) > _MAX_RESPONSE_BYTES:
            raise ValueError("source response exceeds the 1 MiB limit")
    try:
        return json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise ValueError("source did not return valid JSON") from exc


async def _search_one(
    source: CustomMediaSource,
    query: str,
) -> tuple[list[ResolvedMediaVariant], str]:
    resolver = PublicOnlyResolver()
    connector = aiohttp.TCPConnector(
        resolver=resolver,
        use_dns_cache=False,
        ttl_dns_cache=0,
        limit=2,
    )
    timeout = aiohttp.ClientTimeout(
        total=_TIMEOUT_SECONDS,
        connect=3.0,
        sock_read=5.0,
    )
    current = _validate_request_url(_search_url(source.endpoint_url, query))

    try:
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "User-Agent": "DankShield-MovieNight/1.0",
            },
        ) as session:
            for _ in range(4):
                async with session.get(current, allow_redirects=False) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        location = str(response.headers.get("Location") or "").strip()
                        if not location:
                            raise ValueError("source redirect had no location")
                        current = _validate_request_url(urljoin(current, location))
                        continue
                    if response.status != 200:
                        return [], f"{source.label}: HTTP {response.status}"
                    payload = await _read_json_limited(response)
                    items = _extract_items(payload)
                    variants = [
                        variant
                        for item in items[:_MAX_SOURCE_RESULTS]
                        if (variant := _variant_from_item(source, item)) is not None
                    ]
                    return variants, ""
            return [], f"{source.label}: too many redirects"
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as exc:
        return [], f"{source.label}: {type(exc).__name__}: {exc}"
    finally:
        await resolver.close()


async def fetch_torrent_metadata(
    source_ref: str,
    *,
    max_bytes: int = 4 * 1024 * 1024,
) -> bytes:
    """Fetch authorized .torrent metadata without permitting private-network access."""

    current = _validate_request_url(str(source_ref or "").strip())
    limit = max(64 * 1024, min(int(max_bytes), 16 * 1024 * 1024))
    resolver = PublicOnlyResolver()
    connector = aiohttp.TCPConnector(
        resolver=resolver,
        use_dns_cache=False,
        ttl_dns_cache=0,
        limit=1,
    )
    timeout = aiohttp.ClientTimeout(total=8.0, connect=3.0, sock_read=5.0)

    try:
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={
                "Accept": "application/x-bittorrent, application/octet-stream",
                "User-Agent": "DankShield-MovieNight/1.0",
            },
        ) as session:
            for _ in range(4):
                async with session.get(current, allow_redirects=False) as response:
                    if response.status in {301, 302, 303, 307, 308}:
                        location = str(response.headers.get("Location") or "").strip()
                        if not location:
                            raise ValueError("torrent redirect had no location")
                        current = _validate_request_url(urljoin(current, location))
                        continue
                    if response.status != 200:
                        raise ValueError(f"torrent source returned HTTP {response.status}")

                    length = _safe_int(response.headers.get("Content-Length"))
                    if length > limit:
                        raise ValueError("torrent metadata exceeds the configured limit")

                    payload = bytearray()
                    async for chunk in response.content.iter_chunked(64 * 1024):
                        payload.extend(chunk)
                        if len(payload) > limit:
                            raise ValueError("torrent metadata exceeds the configured limit")
                    if not payload:
                        raise ValueError("torrent metadata response was empty")
                    return bytes(payload)
            raise ValueError("torrent source redirected too many times")
    finally:
        await resolver.close()


async def search_custom_media_sources(
    guild_id: int,
    query: str,
) -> MediaSourceSearchOutcome:
    _raw, registry = await load_media_source_registry(int(guild_id), refresh=True)
    sources = enabled_custom_sources(registry)
    if not sources:
        return MediaSourceSearchOutcome(variants=(), errors=("No custom sources are enabled.",))

    semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)

    async def run(source: CustomMediaSource):
        async with semaphore:
            return await _search_one(source, query)

    results = await asyncio.gather(*(run(source) for source in sources))

    variants: list[ResolvedMediaVariant] = []
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for rows, error in results:
        if error:
            errors.append(error)
        for row in rows:
            key = (row.title.casefold(), row.source_ref)
            if key in seen:
                continue
            seen.add(key)
            variants.append(row)
            if len(variants) >= _MAX_TOTAL_RESULTS:
                break
        if len(variants) >= _MAX_TOTAL_RESULTS:
            break

    variants.sort(
        key=lambda item: (
            -int(item.seeds),
            -(float(item.seeds) / float(max(1, item.leechers))),
            int(item.leechers),
            -int(item.peers),
            int(item.file_size or 0),
            item.title.casefold(),
        )
    )
    return MediaSourceSearchOutcome(
        variants=tuple(variants),
        errors=tuple(errors[:20]),
    )


__all__ = [
    "MediaSourceSearchOutcome",
    "PublicOnlyResolver",
    "fetch_torrent_metadata",
    "ResolvedMediaVariant",
    "search_custom_media_sources",
]
