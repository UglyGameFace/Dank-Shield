from __future__ import annotations

"""Safe resolver for guild-configured Movie Night HTTPS media feeds."""

import asyncio
import ipaddress
import json
import re
import socket
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Mapping, Optional
from urllib.parse import parse_qsl, quote_plus, urlencode, urljoin, urlsplit, urlunsplit

import aiohttp

from stoney_verify.media_metadata import parse_release_name
from stoney_verify.media_source_registry import (
    CustomMediaSource,
    enabled_structured_sources,
    load_media_source_registry,
)

_MAX_SOURCE_RESULTS = 25
_MAX_TOTAL_RESULTS = 100
_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_CONCURRENCY = 4
_TIMEOUT_SECONDS = 8.0

INTERNET_ARCHIVE_SOURCE_ID = "internet-archive-feature-films"
INTERNET_ARCHIVE_SOURCE_LABEL = "Internet Archive Feature Films"
_INTERNET_ARCHIVE_SEARCH_URL = "https://archive.org/advancedsearch.php"
_ARCHIVE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,180}$")


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

@dataclass(frozen=True)
class MediaSourceProbeOutcome:
    reachable: bool
    playable_results: int = 0
    error: str = ""


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except Exception:
        return 0


def _internet_archive_search_url(query: str) -> str:
    clean_query = " ".join(str(query or "").split())[:180]
    if not clean_query:
        raise ValueError("Movie search query is empty.")

    escaped_query = clean_query.replace("\\", "\\\\").replace('"', '\\"')
    params = [
        ("q", f'collection:feature_films AND title:("{escaped_query}")'),
        ("fl[]", "identifier"),
        ("fl[]", "title"),
        ("fl[]", "date"),
        ("fl[]", "downloads"),
        ("rows", "12"),
        ("page", "1"),
        ("output", "json"),
        ("sort[]", "downloads desc"),
    ]
    return f"{_INTERNET_ARCHIVE_SEARCH_URL}?{urlencode(params)}"


def _archive_variant_from_doc(item: Mapping[str, Any]) -> Optional[ResolvedMediaVariant]:
    identifier = str(item.get("identifier") or "").strip()
    if (
        not identifier
        or identifier in {".", ".."}
        or not _ARCHIVE_ID_RE.fullmatch(identifier)
    ):
        return None
    title = _clean_title(item.get("title") or identifier)
    if not title:
        return None

    torrent_url = f"https://archive.org/download/{identifier}/{identifier}_archive.torrent"
    release = parse_release_name(title)
    metadata: dict[str, Any] = {
        "release_name": release,
        "source_reported_verified": False,
        "source_reported": {
            "archive_identifier": identifier,
            "archive_date": _clean_title(item.get("date"))[:40],
            "archive_downloads": _safe_int(item.get("downloads")),
        },
        "builtin_source": INTERNET_ARCHIVE_SOURCE_ID,
    }
    return ResolvedMediaVariant(
        title=title,
        source_id=INTERNET_ARCHIVE_SOURCE_ID,
        source_label=INTERNET_ARCHIVE_SOURCE_LABEL,
        source_ref=torrent_url,
        file_size=0,
        seeds=0,
        leechers=0,
        peers=0,
        metadata=metadata,
    )

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
    path = str(parsed.path or "").casefold()
    if path.endswith((".xml", ".rss", ".atom")):
        return endpoint

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


_RESULT_LIST_KEYS = (
    "results",
    "items",
    "releases",
    "variants",
    "torrents",
    "movies",
    "entries",
)
_RESULT_WRAPPER_KEYS = (
    "data",
    "response",
    "payload",
)
_PLAYABLE_REF_KEYS = (
    "source_ref",
    "magnet",
    "magnet_uri",
    "magnet_url",
    "torrent",
    "torrent_url",
    "download_url",
    "url",
)
_INFO_HASH_KEYS = (
    "info_hash",
    "infohash",
    "hash",
)
_BTIH_HEX_RE = re.compile(r"^[A-Fa-f0-9]{40}$")
_BTIH_BASE32_RE = re.compile(r"^[A-Za-z2-7]{32}$")
_SOURCE_METADATA_KEYS = (
    "quality",
    "resolution",
    "codec",
    "video_codec",
    "audio_codec",
    "language",
    "lang",
    "group",
    "provider",
    "indexer",
    "category",
    "year",
)


def _extract_items(payload: Any, *, _depth: int = 0) -> list[Mapping[str, Any]]:
    if _depth > 3:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []

    for key in _RESULT_LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]

    for key in _RESULT_WRAPPER_KEYS:
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
        if isinstance(value, Mapping):
            nested = _extract_items(value, _depth=_depth + 1)
            if nested:
                return nested
    return []


def _magnet_from_info_hash(value: Any) -> str:
    raw = str(value or "").strip()
    if _BTIH_HEX_RE.fullmatch(raw):
        btih = raw.lower()
        if btih == "0" * 40:
            return ""
    elif _BTIH_BASE32_RE.fullmatch(raw):
        btih = raw.upper()
    else:
        return ""
    return f"magnet:?xt=urn:btih:{btih}"


def _item_source_ref(item: Mapping[str, Any]) -> str:
    for key in _PLAYABLE_REF_KEYS:
        value = item.get(key)
        if value:
            ref = _safe_source_ref(value)
            if ref:
                return ref

    for key in _INFO_HASH_KEYS:
        magnet = _magnet_from_info_hash(item.get(key))
        if magnet:
            return magnet
    return ""


def _nested_torrent_items(
    parent: Mapping[str, Any],
    value: Any,
    *,
    path: tuple[str, ...] = (),
    _depth: int = 0,
) -> list[Mapping[str, Any]]:
    if _depth > 4:
        return []

    rows: list[Mapping[str, Any]] = []
    if isinstance(value, list):
        for index, child in enumerate(value[:_MAX_SOURCE_RESULTS]):
            if not isinstance(child, Mapping):
                continue
            merged = dict(parent)
            merged.update(child)
            merged.setdefault("variant_path", "/".join((*path, str(index))))
            rows.append(merged)
        return rows

    if not isinstance(value, Mapping):
        return rows

    if _item_source_ref(value):
        merged = dict(parent)
        merged.update(value)
        if path:
            merged.setdefault("variant_path", "/".join(path))
            if not any(merged.get(key) for key in ("release_name", "filename")):
                merged["release_name"] = " ".join(
                    part for part in (str(parent.get("title") or parent.get("name") or ""), *path)
                    if part
                )
        rows.append(merged)
        return rows

    for key, child in list(value.items())[:64]:
        if isinstance(child, (Mapping, list)):
            rows.extend(
                _nested_torrent_items(
                    parent,
                    child,
                    path=(*path, str(key)[:40]),
                    _depth=_depth + 1,
                )
            )
            if len(rows) >= _MAX_SOURCE_RESULTS:
                break
    return rows[:_MAX_SOURCE_RESULTS]


def _expand_provider_items(items: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    for item in items[:_MAX_SOURCE_RESULTS]:
        if _item_source_ref(item):
            rows.append(item)
        torrents = item.get("torrents")
        if isinstance(torrents, (Mapping, list)):
            rows.extend(_nested_torrent_items(item, torrents))
        if len(rows) >= _MAX_SOURCE_RESULTS:
            break
    return rows[:_MAX_SOURCE_RESULTS]


def _variant_from_item(
    source: CustomMediaSource,
    item: Mapping[str, Any],
) -> Optional[ResolvedMediaVariant]:
    title = _clean_title(
        item.get("title")
        or item.get("name")
        or item.get("movie")
        or item.get("display_name")
        or item.get("filename")
    )
    source_ref = _item_source_ref(item)
    if not title or not source_ref:
        return None

    release_name = _clean_title(
        item.get("release_name")
        or item.get("filename")
        or item.get("file_name")
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

    for key in _SOURCE_METADATA_KEYS:
        value = item.get(key)
        if isinstance(value, (str, int, float, bool)) and str(value).strip():
            source_reported.setdefault(key, value)
    variant_path = _clean_title(item.get("variant_path"))[:120]
    if variant_path:
        source_reported.setdefault("variant_path", variant_path)

    metadata: dict[str, Any] = {
        "release_name": inferred,
        "source_reported": source_reported,
        "source_reported_verified": False,
    }

    seeds = _safe_int(
        item.get("seeds")
        or item.get("seeders")
        or item.get("seed")
        or item.get("seed_count")
    )
    leechers = _safe_int(
        item.get("leechers")
        or item.get("leeches")
        or item.get("leechers_count")
        or item.get("leech")
        or item.get("leech_count")
    )
    peers = max(
        seeds + leechers,
        _safe_int(
            item.get("peers")
            or item.get("peer_count")
            or item.get("peer")
            or item.get("total_peers")
        ),
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
            or item.get("filesize")
            or item.get("length")
            or item.get("bytes")
        ),
        seeds=seeds,
        leechers=leechers,
        peers=peers,
        metadata=metadata,
    )


async def _read_limited_body(response: aiohttp.ClientResponse) -> bytes:
    length = _safe_int(response.headers.get("Content-Length"))
    if length > _MAX_RESPONSE_BYTES:
        raise ValueError("source response exceeds the 1 MiB limit")

    payload = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        payload.extend(chunk)
        if len(payload) > _MAX_RESPONSE_BYTES:
            raise ValueError("source response exceeds the 1 MiB limit")
    return bytes(payload)


def _xml_local_name(tag: Any) -> str:
    raw = str(tag or "")
    if "}" in raw:
        raw = raw.rsplit("}", 1)[-1]
    if ":" in raw:
        raw = raw.rsplit(":", 1)[-1]
    return raw.casefold()


def _feed_playable_ref(value: Any, *, media_type: str = "") -> str:
    raw = str(value or "").strip().strip("<>")
    if not raw:
        return ""
    if raw.casefold().startswith("magnet:?"):
        return _safe_source_ref(raw)

    try:
        parsed = urlsplit(raw)
    except Exception:
        return ""
    path = str(parsed.path or "").casefold()
    declared_type = str(media_type or "").casefold()
    if not path.endswith(".torrent") and "bittorrent" not in declared_type:
        return ""
    return _safe_source_ref(raw)


def _feed_entry_to_item(entry: ET.Element) -> Mapping[str, Any]:
    title = ""
    source_ref = ""
    info_hash = ""
    file_size = 0
    metadata: dict[str, Any] = {}

    for child in entry.iter():
        name = _xml_local_name(child.tag)
        text_value = " ".join(str(child.text or "").split())

        if name == "title" and text_value and not title:
            title = text_value[:180]
            continue

        if name in {"infohash", "info_hash"} and text_value and not info_hash:
            info_hash = text_value[:80]
            continue

        if name in {"magneturi", "magnet_uri", "magnet"} and text_value and not source_ref:
            source_ref = _feed_playable_ref(text_value)
            continue

        if name == "enclosure":
            candidate = str(child.attrib.get("url") or child.attrib.get("href") or "").strip()
            media_type = str(child.attrib.get("type") or "")
            if not source_ref:
                source_ref = _feed_playable_ref(candidate, media_type=media_type)
            if not file_size:
                file_size = _safe_int(child.attrib.get("length") or child.attrib.get("size"))
            continue

        if name == "link":
            candidate = str(child.attrib.get("href") or text_value or "").strip()
            rel = str(child.attrib.get("rel") or "").casefold()
            media_type = str(child.attrib.get("type") or "")
            if not source_ref and (
                rel == "enclosure"
                or candidate.casefold().startswith("magnet:?")
                or _feed_playable_ref(candidate, media_type=media_type)
            ):
                source_ref = _feed_playable_ref(candidate, media_type=media_type)
            continue

        if name == "guid" and text_value and not source_ref:
            source_ref = _feed_playable_ref(text_value)
            continue

        if name in {"category", "author", "creator", "pubdate", "published", "updated"}:
            if text_value and name not in metadata:
                metadata[name] = text_value[:180]

    item: dict[str, Any] = {
        "title": title,
        "source_ref": source_ref,
        "file_size": file_size,
    }
    if info_hash:
        item["info_hash"] = info_hash
    if metadata:
        item["metadata"] = metadata
    return item


def _feed_query_matches(title: Any, query: str) -> bool:
    clean_title = " ".join(str(title or "").casefold().split())
    clean_query = " ".join(str(query or "").casefold().split())
    if not clean_title or not clean_query:
        return False
    if clean_query in clean_title:
        return True
    terms = re.findall(r"[a-z0-9]+", clean_query)
    return bool(terms) and all(term in clean_title for term in terms)


def _extract_feed_items(payload: bytes, query: str) -> list[Mapping[str, Any]]:
    lowered = payload[:4096].casefold()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ValueError("source XML declarations are not allowed")

    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ValueError("source did not return valid RSS/Atom XML") from exc

    rows: list[Mapping[str, Any]] = []
    for entry in root.iter():
        if _xml_local_name(entry.tag) not in {"item", "entry"}:
            continue
        item = _feed_entry_to_item(entry)
        if not _feed_query_matches(item.get("title"), query):
            continue
        if not _item_source_ref(item):
            continue
        rows.append(item)
        if len(rows) >= _MAX_SOURCE_RESULTS:
            break
    return rows


async def _read_structured_items_limited(
    response: aiohttp.ClientResponse,
    query: str,
) -> list[Mapping[str, Any]]:
    payload = await _read_limited_body(response)
    if not payload:
        raise ValueError("source response was empty")

    content_type = str(response.headers.get("Content-Type") or "").casefold()
    stripped = payload.lstrip()

    if "json" in content_type or stripped.startswith((b"{", b"[")):
        try:
            decoded = json.loads(payload.decode("utf-8"))
        except Exception as exc:
            raise ValueError("source did not return valid JSON") from exc
        return _expand_provider_items(_extract_items(decoded))

    if (
        "xml" in content_type
        or "rss" in content_type
        or "atom" in content_type
        or stripped.startswith(b"<")
    ):
        return _extract_feed_items(payload, query)

    raise ValueError("source must return structured JSON, RSS, or Atom data")


async def _read_json_limited(response: aiohttp.ClientResponse) -> Any:
    payload = await _read_limited_body(response)
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
                    items = _expand_provider_items(_extract_items(payload))
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


async def _search_builtin_internet_archive(
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
    current = _validate_request_url(_internet_archive_search_url(query))

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
                            raise ValueError("built-in source redirect had no location")
                        current = _validate_request_url(urljoin(current, location))
                        continue
                    if response.status != 200:
                        return [], f"{INTERNET_ARCHIVE_SOURCE_LABEL}: HTTP {response.status}"
                    payload = await _read_json_limited(response)
                    response_blob = payload.get("response") if isinstance(payload, Mapping) else None
                    docs = response_blob.get("docs") if isinstance(response_blob, Mapping) else None
                    if not isinstance(docs, list):
                        return [], f"{INTERNET_ARCHIVE_SOURCE_LABEL}: unexpected search response"
                    variants = [
                        variant
                        for item in docs[:12]
                        if isinstance(item, Mapping)
                        if (variant := _archive_variant_from_doc(item)) is not None
                    ]
                    return variants, ""
            return [], f"{INTERNET_ARCHIVE_SOURCE_LABEL}: too many redirects"
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError) as exc:
        return [], f"{INTERNET_ARCHIVE_SOURCE_LABEL}: {type(exc).__name__}: {exc}"
    finally:
        await resolver.close()


async def probe_custom_media_source(
    source: CustomMediaSource,
    *,
    query: str = "batman",
) -> MediaSourceProbeOutcome:
    variants, error = await _search_one(source, query)
    if error:
        return MediaSourceProbeOutcome(reachable=False, error=error)
    return MediaSourceProbeOutcome(
        reachable=True,
        playable_results=len(variants),
    )

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
    sources = enabled_structured_sources(registry)
    if not sources:
        return MediaSourceSearchOutcome(variants=(), errors=("No structured custom sources are enabled.",))

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


def _merge_media_outcomes(
    outcomes: tuple[MediaSourceSearchOutcome, ...],
) -> MediaSourceSearchOutcome:
    variants: list[ResolvedMediaVariant] = []
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for outcome in outcomes:
        errors.extend(outcome.errors)
        for row in outcome.variants:
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


async def search_movie_sources(
    guild_id: int,
    query: str,
) -> MediaSourceSearchOutcome:
    builtin_result, custom = await asyncio.gather(
        _search_builtin_internet_archive(query),
        search_custom_media_sources(int(guild_id), query),
    )
    builtin_rows, builtin_error = builtin_result
    builtin = MediaSourceSearchOutcome(
        variants=tuple(builtin_rows),
        errors=(builtin_error,) if builtin_error else (),
    )
    if custom.errors == ("No structured custom sources are enabled.",):
        custom = MediaSourceSearchOutcome(variants=custom.variants)
    return _merge_media_outcomes((builtin, custom))


__all__ = [
    "search_movie_sources",
    "probe_custom_media_source",
    "MediaSourceProbeOutcome",
    "INTERNET_ARCHIVE_SOURCE_LABEL",
    "INTERNET_ARCHIVE_SOURCE_ID",
    "MediaSourceSearchOutcome",
    "PublicOnlyResolver",
    "fetch_torrent_metadata",
    "ResolvedMediaVariant",
    "search_custom_media_sources",
]
