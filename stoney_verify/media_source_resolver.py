from __future__ import annotations

"""Safe resolver for guild-configured Movie Night HTTPS media feeds."""

import asyncio
import base64
import ipaddress
import json
import re
from decimal import Decimal, InvalidOperation
import socket
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Mapping, Optional
from urllib.parse import parse_qsl, quote, quote_plus, urlencode, urljoin, urlsplit, urlunsplit

import aiohttp

from stoney_verify.media_metadata import parse_release_name
from stoney_verify.media_source_registry import (
    PROVIDER_TYPE_FEED,
    CustomMediaSource,
    enabled_structured_sources,
    load_media_source_registry,
)

_MAX_SOURCE_RESULTS = 25
_MAX_TOTAL_RESULTS = 100
_MAX_RESPONSE_BYTES = 1024 * 1024
_MAX_FEED_RESPONSE_BYTES = 8 * 1024 * 1024
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
    if (
        str(parsed.scheme or "").lower() != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
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


def _looks_like_static_feed_endpoint(endpoint: str) -> bool:
    parsed = urlsplit(str(endpoint or "").strip())
    path = str(parsed.path or "").casefold().rstrip("/")
    if path.endswith((".xml", ".rss", ".atom")):
        return True
    leaf = path.rsplit("/", 1)[-1] if path else ""
    if leaf in {"feed", "feeds", "rss", "atom"}:
        return True

    query = {
        str(key or "").casefold(): str(value or "").casefold()
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    }
    return query.get("format") in {"rss", "atom", "xml"} or query.get("output") in {
        "rss",
        "atom",
        "xml",
    }


_PROVIDER_TEMPLATE_FIELDS = {
    "{query}": "query",
    "{imdb_id}": "imdb_id",
    "{imdb_numeric}": "imdb_numeric",
    "{tmdb_id}": "tmdb_id",
    "{series_tmdb_id}": "series_tmdb_id",
    "{tvdb_id}": "tvdb_id",
    "{year}": "year",
    "{media_type}": "media_type",
    "{season}": "season",
    "{episode}": "episode",
    "{season_episode}": "season_episode",
}
_IMDB_ID_RE = re.compile(r"^tt(\d{5,12})$", re.IGNORECASE)


def _imdb_numeric(value: str) -> str:
    """Convert a canonical tt-prefixed IMDb id to the numeric API form."""

    match = _IMDB_ID_RE.fullmatch(str(value or "").strip().lower())
    if not match:
        return ""
    return str(int(match.group(1)))


def _provider_lookup_context(
    query: str,
    catalog_metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, str]:
    clean_query = " ".join(str(query or "").split())[:180]
    metadata = catalog_metadata if isinstance(catalog_metadata, Mapping) else {}

    context: dict[str, str] = {"query": clean_query}
    media_type = str(metadata.get("media_type") or "").strip().casefold()
    if media_type:
        context["media_type"] = media_type
    year = _safe_int(metadata.get("year"))
    if year > 0:
        context["year"] = str(year)

    tmdb_id = _safe_int(metadata.get("tmdb_id"))
    series_tmdb_id = _safe_int(metadata.get("series_id"))
    if tmdb_id > 0:
        context["tmdb_id"] = str(tmdb_id)
    if series_tmdb_id > 0:
        context["series_tmdb_id"] = str(series_tmdb_id)
    elif media_type == "tv" and tmdb_id > 0:
        context["series_tmdb_id"] = str(tmdb_id)

    tvdb_id = _safe_int(metadata.get("tvdb_id") or metadata.get("tvdb"))
    if tvdb_id > 0:
        context["tvdb_id"] = str(tvdb_id)

    imdb_id = str(
        metadata.get("imdb_id")
        or metadata.get("imdb")
        or ""
    ).strip().lower()
    imdb_numeric = _imdb_numeric(imdb_id)
    if imdb_numeric:
        context["imdb_id"] = imdb_id
        context["imdb_numeric"] = imdb_numeric

    season_raw = metadata.get("season_number")
    episode_raw = metadata.get("episode_number")
    has_season = season_raw is not None and str(season_raw).strip() != ""
    has_episode = episode_raw is not None and str(episode_raw).strip() != ""
    season = _safe_int(season_raw)
    episode = _safe_int(episode_raw)
    if has_season:
        context["season"] = str(season)
    if has_episode and episode > 0:
        context["episode"] = str(episode)
    if has_season and has_episode and episode > 0:
        context["season_episode"] = f"S{season:02d}E{episode:02d}"
    return context


async def _enrich_provider_lookup_context(
    query: str,
    catalog_metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, str]:
    context = _provider_lookup_context(query, catalog_metadata)
    if not isinstance(catalog_metadata, Mapping):
        return context

    media_type = str(catalog_metadata.get("media_type") or "").strip().casefold()
    if media_type == "episode":
        lookup_type = "tv"
        lookup_id = _safe_int(catalog_metadata.get("series_id"))
    elif media_type in {"movie", "tv"}:
        lookup_type = media_type
        lookup_id = _safe_int(catalog_metadata.get("tmdb_id"))
    else:
        return context
    if lookup_id <= 0:
        return context

    try:
        from stoney_verify.cinema_catalog import get_external_ids

        external_ids = await get_external_ids(lookup_type, lookup_id)
    except Exception:
        # Identifier-only providers can report their own missing-identity error,
        # while ordinary title-query providers continue working normally.
        return context

    imdb_id = str(external_ids.get("imdb_id") or "").strip().lower()
    imdb_numeric = _imdb_numeric(imdb_id)
    if imdb_numeric:
        context["imdb_id"] = imdb_id
        context["imdb_numeric"] = imdb_numeric
    tvdb_id = _safe_int(external_ids.get("tvdb_id"))
    if tvdb_id > 0:
        context["tvdb_id"] = str(tvdb_id)
    return context


def _search_url(
    endpoint: str,
    query: str,
    *,
    lookup_context: Optional[Mapping[str, Any]] = None,
    static_feed: bool = False,
) -> str:
    clean_query = " ".join(str(query or "").split())[:180]
    if static_feed or _looks_like_static_feed_endpoint(endpoint):
        return endpoint

    context = {
        str(key): " ".join(str(value or "").split())[:180]
        for key, value in dict(lookup_context or {}).items()
        if str(value or "").strip()
    }
    context["query"] = clean_query

    parsed = urlsplit(endpoint)
    template_tokens = [
        token for token in _PROVIDER_TEMPLATE_FIELDS
        if token in endpoint
    ]
    if template_tokens:
        path = str(parsed.path or "")
        request_query = str(parsed.query or "")
        for token in template_tokens:
            field = _PROVIDER_TEMPLATE_FIELDS[token]
            value = str(context.get(field) or "").strip()
            if not value:
                friendly = {
                    "imdb_id": "IMDb identity",
                    "imdb_numeric": "IMDb identity",
                    "tmdb_id": "TMDB identity",
                    "series_tmdb_id": "series TMDB identity",
                    "tvdb_id": "TVDB identity",
                    "year": "release year",
                    "media_type": "media type",
                    "season": "season number",
                    "episode": "episode number",
                    "season_episode": "season/episode identity",
                    "query": "title query",
                }.get(field, field)
                raise ValueError(f"source requires {friendly} for this search")
            if token in path:
                path = path.replace(token, quote(value, safe=""))
            if token in request_query:
                request_query = request_query.replace(token, quote_plus(value))
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                path,
                request_query,
                "",
            )
        )

    if not clean_query:
        raise ValueError("Movie search query is empty.")

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
    "searchResults",
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
    "sourceRef",
    "magnet",
    "magnet_uri",
    "magnetUri",
    "magnet_url",
    "magnetUrl",
    "magnetLink",
    "torrent",
    "torrent_url",
    "torrentUrl",
    "download_url",
    "downloadUrl",
    "url",
)
_INFO_HASH_KEYS = (
    "info_hash",
    "infohash",
    "infoHash",
    "btih",
    "hash",
)
_INFO_HASH_V2_KEYS = (
    "info_hash_v2",
    "infohash_v2",
    "infoHashV2",
    "btmh",
)
_BTIH_HEX_RE = re.compile(r"^[A-Fa-f0-9]{40}$")
_BTIH_BASE32_RE = re.compile(r"^[A-Za-z2-7]{32}$")
_BTMH_SHA256_RE = re.compile(r"^[A-Fa-f0-9]{64}$")
_BTMH_MULTIHASH_RE = re.compile(r"^1220[A-Fa-f0-9]{64}$")
_SOURCE_METADATA_KEYS = (
    "quality",
    "resolution",
    "codec",
    "video_codec",
    "videoCodec",
    "audio_codec",
    "audioCodec",
    "language",
    "lang",
    "group",
    "provider",
    "indexer",
    "category",
    "year",
    "tmdb",
    "tmdb_id",
    "tmdbId",
    "tmdbid",
    "imdb",
    "imdb_id",
    "imdbId",
    "imdbid",
    "season",
    "season_number",
    "seasonNumber",
    "episode",
    "episode_number",
    "episodeNumber",
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

    title_keys = (
        "title",
        "name",
        "movie",
        "display_name",
        "displayName",
        "filename",
        "fileName",
    )
    if any(payload.get(key) for key in title_keys) and (
        any(payload.get(key) for key in _PLAYABLE_REF_KEYS)
        or any(payload.get(key) for key in _INFO_HASH_KEYS)
        or any(payload.get(key) for key in _INFO_HASH_V2_KEYS)
    ):
        return [payload]

    mapped_rows = [
        value
        for value in list(payload.values())[:_MAX_SOURCE_RESULTS]
        if isinstance(value, Mapping)
        and any(value.get(key) for key in title_keys)
        and (
            any(value.get(key) for key in _PLAYABLE_REF_KEYS)
            or any(value.get(key) for key in _INFO_HASH_KEYS)
            or any(value.get(key) for key in _INFO_HASH_V2_KEYS)
        )
    ]
    return mapped_rows


def _magnet_from_info_hash(value: Any) -> str:
    raw = str(value or "").strip()
    if _BTIH_HEX_RE.fullmatch(raw):
        btih = raw.lower()
        if btih == "0" * 40:
            return ""
    elif _BTIH_BASE32_RE.fullmatch(raw):
        btih = raw.upper()
        try:
            decoded = base64.b32decode(btih)
        except Exception:
            return ""
        if decoded == b"\x00" * 20:
            return ""
    else:
        return ""
    return f"magnet:?xt=urn:btih:{btih}"


def _magnet_from_info_hash_v2(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if _BTMH_SHA256_RE.fullmatch(raw):
        multihash = f"1220{raw}"
    elif _BTMH_MULTIHASH_RE.fullmatch(raw):
        multihash = raw
    else:
        return ""
    if multihash[4:] == "0" * 64:
        return ""
    return f"magnet:?xt=urn:btmh:{multihash}"


def _item_source_ref(
    item: Mapping[str, Any],
    *,
    allow_generic_url: bool = True,
) -> str:
    # Prefer fields that explicitly claim to be playable media. Generic "url"
    # is intentionally last because many search APIs use it for a detail page.
    for key in tuple(key for key in _PLAYABLE_REF_KEYS if key != "url"):
        value = item.get(key)
        if value:
            ref = _safe_source_ref(value)
            if ref:
                return ref

    for key in _INFO_HASH_KEYS:
        raw_hash = item.get(key)
        magnet = _magnet_from_info_hash(raw_hash)
        if magnet:
            return magnet
        magnet = _magnet_from_info_hash_v2(raw_hash)
        if magnet:
            return magnet

    for key in _INFO_HASH_V2_KEYS:
        magnet = _magnet_from_info_hash_v2(item.get(key))
        if magnet:
            return magnet

    if allow_generic_url:
        generic_url = item.get("url")
        if generic_url:
            ref = _safe_source_ref(generic_url)
            if ref:
                return ref
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
        torrents = item.get("torrents")
        has_nested_torrents = isinstance(torrents, (Mapping, list))

        # A movie-level "url" is commonly a catalog/detail page (for example
        # APIs that also expose a nested torrents array). Do not turn that page
        # into a fake release when real nested torrent variants are present.
        if _item_source_ref(
            item,
            allow_generic_url=not has_nested_torrents,
        ):
            rows.append(item)

        if has_nested_torrents:
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
        or item.get("displayName")
        or item.get("filename")
        or item.get("fileName")
    )
    source_ref = _item_source_ref(item)
    if not title or not source_ref:
        return None

    release_name = _clean_title(
        item.get("release_name")
        or item.get("releaseName")
        or item.get("filename")
        or item.get("file_name")
        or item.get("fileName")
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
        or item.get("seedCount")
    )
    leechers = _safe_int(
        item.get("leechers")
        or item.get("leeches")
        or item.get("leechers_count")
        or item.get("leech")
        or item.get("leech_count")
        or item.get("leechCount")
        or item.get("leecherCount")
    )
    peers = max(
        seeds + leechers,
        _safe_int(
            item.get("peers")
            or item.get("peer_count")
            or item.get("peerCount")
            or item.get("peer")
            or item.get("total_peers")
            or item.get("totalPeers")
        ),
    )

    return ResolvedMediaVariant(
        title=title,
        source_id=source.source_id,
        source_label=source.label,
        source_ref=source_ref,
        file_size=_safe_int(
            item.get("file_size")
            or item.get("fileSize")
            or item.get("size_bytes")
            or item.get("sizeBytes")
            or item.get("size")
            or item.get("filesize")
            or item.get("contentLength")
            or item.get("length")
            or item.get("bytes")
        ),
        seeds=seeds,
        leechers=leechers,
        peers=peers,
        metadata=metadata,
    )


async def _read_limited_body(
    response: aiohttp.ClientResponse,
    *,
    max_bytes: int = _MAX_RESPONSE_BYTES,
) -> bytes:
    limit = max(64 * 1024, min(int(max_bytes), 8 * 1024 * 1024))
    length = _safe_int(response.headers.get("Content-Length"))
    if length > limit:
        raise ValueError(f"source response exceeds the {limit} byte limit")

    payload = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        payload.extend(chunk)
        if len(payload) > limit:
            raise ValueError(f"source response exceeds the {limit} byte limit")
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


_FEED_SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([KMGTPE]?i?B|bytes?)?$", re.IGNORECASE)


def _feed_size_bytes(value: Any) -> int:
    """Normalize byte counts and human-readable sizes from RSS extensions.

    Nyaa uses values such as "1.1 GiB" while Torznab commonly reports raw
    byte counts. Unrecognized units stay unknown rather than guessing a size.
    """
    match = _FEED_SIZE_RE.fullmatch(str(value or "").strip())
    if match is None:
        return 0
    unit = (match.group(2) or "B").casefold()
    if unit in {"b", "byte", "bytes"}:
        multiplier = 1
    else:
        power = "kmgtpe".find(unit[0]) + 1
        if power <= 0:
            return 0
        multiplier = (1024 if "i" in unit else 1000) ** power
    try:
        return max(0, min(int(Decimal(match.group(1)) * multiplier), 2**63 - 1))
    except (InvalidOperation, OverflowError, ValueError):
        return 0


def _feed_entry_to_item(entry: ET.Element) -> Mapping[str, Any]:
    item: dict[str, Any] = {
        "title": "",
        "source_ref": "",
        "file_size": 0,
        "seeds": 0,
        "leechers": 0,
        "peers": 0,
    }
    metadata: dict[str, Any] = {}

    def set_source(value: Any, *, media_type: str = "") -> None:
        if item["source_ref"]:
            return
        ref = _feed_playable_ref(value, media_type=media_type)
        if ref:
            item["source_ref"] = ref

    def apply_named_value(raw_name: Any, raw_value: Any) -> None:
        name = re.sub(r"[^a-z0-9]+", "", str(raw_name or "").casefold())
        value = str(raw_value or "").strip()
        if not name or not value:
            return
        if name in {"infohash", "btih", "hash"} and not item.get("info_hash"):
            item["info_hash"] = value[:80]
        elif name in {"infohashv2", "btmh"} and not item.get("info_hash_v2"):
            item["info_hash_v2"] = value[:96]
        elif name in {"magnet", "magneturi", "magneturl"}:
            set_source(value)
        elif name in {"size", "filesize", "contentlength", "length"}:
            if not item["file_size"]:
                item["file_size"] = _feed_size_bytes(value)
        elif name in {"seed", "seeds", "seeders"}:
            item["seeds"] = max(int(item["seeds"]), _safe_int(value))
        elif name in {"leech", "leeches", "leechers"}:
            item["leechers"] = max(int(item["leechers"]), _safe_int(value))
        elif name in {"peer", "peers", "peercount"}:
            item["peers"] = max(int(item["peers"]), _safe_int(value))
        elif name in {
            "category",
            "imdb",
            "imdbid",
            "tmdb",
            "tmdbid",
            "language",
            "lang",
            "quality",
            "resolution",
            "codec",
            "group",
            "indexer",
        }:
            metadata.setdefault(name, value[:180])

    for child in entry.iter():
        name = _xml_local_name(child.tag)
        text_value = " ".join(str(child.text or "").split())

        if name == "title" and text_value and not item["title"]:
            item["title"] = text_value[:180]
            continue

        if name == "attr":
            attr_name = child.attrib.get("name") or child.attrib.get("key")
            attr_value = child.attrib.get("value") or text_value
            normalized_attr = re.sub(
                r"[^a-z0-9]+",
                "",
                str(attr_name or "").casefold(),
            )
            if normalized_attr == "peers":
                item["leechers"] = max(
                    int(item["leechers"]),
                    _safe_int(attr_value),
                )
            else:
                apply_named_value(attr_name, attr_value)
            continue

        if name in {
            "infohash",
            "info_hash",
            "infohashv2",
            "info_hash_v2",
            "btih",
            "btmh",
            "hash",
            "magneturi",
            "magnet_uri",
            "magneturl",
            "magnet",
            "size",
            "filesize",
            "contentlength",
            "length",
            "seed",
            "seeds",
            "seeders",
            "leech",
            "leeches",
            "leechers",
            "peer",
            "peers",
            "peercount",
        }:
            apply_named_value(name, text_value)
            continue

        if name == "enclosure":
            candidate = str(child.attrib.get("url") or child.attrib.get("href") or "").strip()
            media_type = str(child.attrib.get("type") or "")
            set_source(candidate, media_type=media_type)
            if not item["file_size"]:
                item["file_size"] = _safe_int(
                    child.attrib.get("length") or child.attrib.get("size")
                )
            continue

        if name == "link":
            candidate = str(child.attrib.get("href") or text_value or "").strip()
            rel = str(child.attrib.get("rel") or "").casefold()
            media_type = str(child.attrib.get("type") or "")
            playable = _feed_playable_ref(candidate, media_type=media_type)
            if playable and (
                rel in {"", "enclosure"}
                or candidate.casefold().startswith("magnet:?")
                or "bittorrent" in media_type.casefold()
                or str(urlsplit(candidate).path or "").casefold().endswith(".torrent")
            ):
                set_source(playable, media_type=media_type)
            continue

        if name == "guid" and text_value:
            set_source(text_value)
            continue

        if name in {"category", "author", "creator", "pubdate", "published", "updated"}:
            if text_value and name not in metadata:
                metadata[name] = text_value[:180]

    if metadata:
        item["metadata"] = metadata
    return item


def _feed_query_matches(title: Any, query: str) -> bool:
    clean_title = " ".join(str(title or "").casefold().split())
    clean_query = " ".join(str(query or "").casefold().split())
    if not clean_title:
        return False
    if not clean_query:
        return True
    if clean_query in clean_title:
        return True
    terms = re.findall(r"[a-z0-9]+", clean_query)
    return bool(terms) and all(term in clean_title for term in terms)


def _extract_feed_items(payload: bytes, query: str) -> list[Mapping[str, Any]]:
    lowered = payload.lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ValueError("source XML declarations are not allowed")

    stripped = payload.lstrip().lower()
    if stripped.startswith(b"<html") or stripped.startswith(b"<!doctype html"):
        raise ValueError("source returned HTML instead of a structured RSS/Atom feed")

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
    content_type = str(response.headers.get("Content-Type") or "").casefold()
    response_url = str(getattr(response, "url", "") or "")
    feed_hint = (
        "xml" in content_type
        or "rss" in content_type
        or "atom" in content_type
        or _looks_like_static_feed_endpoint(response_url)
    )
    payload = await _read_limited_body(
        response,
        max_bytes=(
            _MAX_FEED_RESPONSE_BYTES
            if feed_hint
            else _MAX_RESPONSE_BYTES
        ),
    )
    if not payload:
        raise ValueError("source response was empty")

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
    *,
    lookup_context: Optional[Mapping[str, Any]] = None,
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
    current = _validate_request_url(
        _search_url(
            source.endpoint_url,
            query,
            lookup_context=lookup_context,
            static_feed=source.provider_type == PROVIDER_TYPE_FEED,
        )
    )

    try:
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={
                "Accept": (
                    "application/json, application/rss+xml, application/atom+xml, "
                    "application/xml, text/xml;q=0.9"
                ),
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
                    items = await _read_structured_items_limited(response, query)
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
    query: str = "breaking bad",
) -> MediaSourceProbeOutcome:
    probe_context = {
        "query": query,
        "imdb_id": "tt0903747",
        "imdb_numeric": "903747",
        "tmdb_id": "1396",
        "series_tmdb_id": "1396",
        "tvdb_id": "81189",
        "year": "2008",
        "media_type": "tv",
        "season": "1",
        "episode": "1",
        "season_episode": "S01E01",
    }
    variants, error = await _search_one(
        source,
        query,
        lookup_context=probe_context,
    )
    if error:
        return MediaSourceProbeOutcome(reachable=False, error=error)
    return MediaSourceProbeOutcome(
        reachable=True,
        playable_results=len(variants),
    )


async def preview_custom_media_source(
    source: CustomMediaSource,
    *,
    query: str = "movie",
    limit: int = 8,
) -> MediaSourceSearchOutcome:
    """Refresh one configured structured source for the Cinema Feed Center.

    This reuses the same SSRF-safe resolver/parser as playback discovery rather
    than creating a second RSS/JSON fetch path.
    """

    if source.provider_type not in {PROVIDER_TYPE_FEED, "json"}:
        return MediaSourceSearchOutcome(
            variants=(),
            errors=(f"{source.label}: this source is a reference link, not a structured feed.",),
        )
    rows, error = await _search_one(source, query)
    if error:
        return MediaSourceSearchOutcome(variants=(), errors=(error,))
    safe_limit = max(1, min(int(limit), _MAX_SOURCE_RESULTS))
    return MediaSourceSearchOutcome(variants=tuple(rows[:safe_limit]))

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
    *,
    lookup_context: Optional[Mapping[str, Any]] = None,
) -> MediaSourceSearchOutcome:
    _raw, registry = await load_media_source_registry(int(guild_id), refresh=True)
    sources = enabled_structured_sources(registry)
    if not sources:
        return MediaSourceSearchOutcome(variants=(), errors=("No structured custom sources are enabled.",))

    semaphore = asyncio.Semaphore(_MAX_CONCURRENCY)

    async def run(source: CustomMediaSource):
        async with semaphore:
            if lookup_context is None:
                return await _search_one(source, query)
            return await _search_one(
                source,
                query,
                lookup_context=lookup_context,
            )

    results = await asyncio.gather(*(run(source) for source in sources))

    variants: list[ResolvedMediaVariant] = []
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()
    for source, (rows, error) in zip(sources, results):
        if error:
            errors.append(error)
        elif not rows:
            errors.append(f"{source.label}: no playable results for this search.")
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
    *,
    catalog_metadata: Optional[Mapping[str, Any]] = None,
) -> MediaSourceSearchOutcome:
    lookup_context: Optional[Mapping[str, Any]] = None
    if isinstance(catalog_metadata, Mapping) and catalog_metadata:
        lookup_context = await _enrich_provider_lookup_context(
            query,
            catalog_metadata,
        )
    builtin_result, custom = await asyncio.gather(
        _search_builtin_internet_archive(query),
        search_custom_media_sources(
            int(guild_id),
            query,
            lookup_context=lookup_context,
        ),
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
    "preview_custom_media_source",
    "MediaSourceProbeOutcome",
    "INTERNET_ARCHIVE_SOURCE_LABEL",
    "INTERNET_ARCHIVE_SOURCE_ID",
    "MediaSourceSearchOutcome",
    "PublicOnlyResolver",
    "fetch_torrent_metadata",
    "ResolvedMediaVariant",
    "search_custom_media_sources",
]
