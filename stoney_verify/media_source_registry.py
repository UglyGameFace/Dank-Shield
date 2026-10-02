from __future__ import annotations

"""Per-guild custom media source registry for Movie Night.

This stores source definitions only. Fetching/querying sources belongs to the
universal media resolver. The registry is intentionally generic and only accepts
HTTPS catalog/feed endpoints; it does not embed provider-specific scraping.
"""

import ipaddress
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Mapping, Optional
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

MEDIA_SOURCE_REGISTRY_KEY = "movie_night_media_sources_v1"
MEDIA_SOURCE_REGISTRY_VERSION = 2
MAX_CUSTOM_MEDIA_SOURCES = 20

PROVIDER_TYPE_JSON = "json"
PROVIDER_TYPE_EXTERNAL = "external"
_PROVIDER_TYPES = {PROVIDER_TYPE_JSON, PROVIDER_TYPE_EXTERNAL}
_SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")


@dataclass(frozen=True)
class CustomMediaSource:
    source_id: str
    label: str
    endpoint_url: str
    provider_type: str = PROVIDER_TYPE_JSON
    enabled: bool = True
    added_by: int = 0
    created_at: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "label": self.label,
            "endpoint_url": self.endpoint_url,
            "provider_type": self.provider_type,
            "enabled": bool(self.enabled),
            "added_by": int(self.added_by),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class MediaSourceRegistry:
    revision: int = 0
    sources: tuple[CustomMediaSource, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": MEDIA_SOURCE_REGISTRY_VERSION,
            "revision": int(self.revision),
            "sources": [item.to_payload() for item in self.sources],
        }


def _safe_id(value: Any) -> str:
    text = str(value or "").strip().lower().replace(" ", "-")
    text = re.sub(r"[^a-z0-9_-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-_")
    if not text:
        return ""
    text = text[:48]
    return text if _SOURCE_ID_RE.fullmatch(text) else ""


def _safe_label(value: Any) -> str:
    return " ".join(str(value or "").split())[:80]


def _safe_provider_type(value: Any) -> str:
    clean = str(value or PROVIDER_TYPE_JSON).strip().casefold()
    return clean if clean in _PROVIDER_TYPES else PROVIDER_TYPE_JSON


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except Exception:
        return 0


def _normalize_endpoint_url(value: Any) -> str:
    raw = str(value or "").strip().strip("<>")
    if not raw or len(raw) > 1000:
        raise ValueError("Custom media source URL is missing or too long.")

    try:
        parsed = urlsplit(raw)
    except Exception as exc:
        raise ValueError("Custom media source URL is invalid.") from exc

    if str(parsed.scheme or "").lower() != "https":
        raise ValueError("Custom media sources must use HTTPS.")
    if parsed.username or parsed.password:
        raise ValueError("Do not put credentials inside the custom source URL.")

    host = str(parsed.hostname or "").lower().strip(".")
    if not host:
        raise ValueError("Custom media source URL has no hostname.")
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise ValueError("Local/private custom source hosts are not allowed.")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        raise ValueError("Private/reserved custom source addresses are not allowed.")

    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Custom media source URL has an invalid port.") from exc

    netloc = host
    if port:
        netloc = f"{host}:{port}"

    return urlunsplit(
        (
            "https",
            netloc,
            str(parsed.path or "/"),
            str(parsed.query or ""),
            "",
        )
    )


def prepare_example_search_url(value: Any) -> str:
    """Turn a pasted working search URL into a reusable Movie Night template.

    Admins should not have to hand-write the query placeholder. Common search
    parameters are detected automatically. A bare endpoint with no query string
    remains valid because the resolver already appends q= at search time.
    """

    clean = _normalize_endpoint_url(value)
    if "{query}" in clean:
        return clean

    parsed = urlsplit(clean)
    pairs = list(parse_qsl(parsed.query, keep_blank_values=True))
    if not pairs:
        return clean

    common_keys = {"q", "query", "search", "term", "keyword", "keywords", "s"}
    updated: list[tuple[str, str]] = []
    replaced = False
    for key, raw_value in pairs:
        if not replaced and str(key or "").strip().casefold() in common_keys:
            updated.append((key, "{query}"))
            replaced = True
        else:
            updated.append((key, raw_value))

    if not replaced:
        raise ValueError(
            "Dank Shield could not find the movie-search part of that URL. "
            "Paste a search URL that uses q=, query=, search=, term=, keyword=, keywords=, or s=."
        )

    query = urlencode(updated, doseq=True).replace("%7Bquery%7D", "{query}")
    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            query,
            "",
        )
    )

def render_provider_search_url(endpoint_url: Any, query: Any) -> str:
    """Render a safe provider search URL without fetching or scraping the page."""

    endpoint = _normalize_endpoint_url(endpoint_url)
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


def _source_from_raw(raw: Any) -> Optional[CustomMediaSource]:
    if not isinstance(raw, Mapping):
        return None
    source_id = _safe_id(raw.get("source_id"))
    label = _safe_label(raw.get("label"))
    try:
        endpoint = _normalize_endpoint_url(raw.get("endpoint_url"))
    except ValueError:
        return None
    if not source_id or not label:
        return None
    return CustomMediaSource(
        source_id=source_id,
        label=label,
        endpoint_url=endpoint,
        provider_type=_safe_provider_type(raw.get("provider_type")),
        enabled=bool(raw.get("enabled", True)),
        added_by=_safe_int(raw.get("added_by")),
        created_at=str(raw.get("created_at") or "")[:64],
    )


def parse_media_source_registry(raw_config: Mapping[str, Any]) -> MediaSourceRegistry:
    blob = raw_config.get(MEDIA_SOURCE_REGISTRY_KEY)
    if not isinstance(blob, Mapping):
        return MediaSourceRegistry()

    try:
        revision = max(0, int(blob.get("revision") or 0))
    except Exception:
        revision = 0

    sources: list[CustomMediaSource] = []
    seen: set[str] = set()
    for item in list(blob.get("sources") or [])[:MAX_CUSTOM_MEDIA_SOURCES]:
        source = _source_from_raw(item)
        if source is None or source.source_id in seen:
            continue
        seen.add(source.source_id)
        sources.append(source)

    return MediaSourceRegistry(revision=revision, sources=tuple(sources))


def _generated_source_id(
    registry: MediaSourceRegistry,
    *,
    label: str,
    endpoint_url: str,
) -> str:
    base = _safe_id(label)
    if not base:
        try:
            host = str(urlsplit(endpoint_url).hostname or "").split(".", 1)[0]
        except Exception:
            host = ""
        base = _safe_id(host) or "source"

    existing = {item.source_id for item in registry.sources}
    if base not in existing:
        return base

    for suffix in range(2, MAX_CUSTOM_MEDIA_SOURCES + 2):
        tail = f"-{suffix}"
        candidate = f"{base[: max(1, 48 - len(tail))]}{tail}"
        if candidate not in existing and _SOURCE_ID_RE.fullmatch(candidate):
            return candidate
    raise ValueError("Could not generate a unique custom media source ID.")


def add_custom_source(
    registry: MediaSourceRegistry,
    *,
    source_id: str = "",
    label: str,
    endpoint_url: str,
    added_by: int,
    provider_type: str = PROVIDER_TYPE_JSON,
) -> MediaSourceRegistry:
    clean_label = _safe_label(label)
    clean_url = _normalize_endpoint_url(endpoint_url)
    clean_type = _safe_provider_type(provider_type)
    clean_id = _safe_id(source_id)
    if not clean_label:
        raise ValueError("Custom media source name is required.")
    if not str(source_id or "").strip():
        clean_id = _generated_source_id(
            registry,
            label=clean_label,
            endpoint_url=clean_url,
        )
    elif not clean_id:
        raise ValueError("Custom media source ID is invalid.")

    existing = {item.source_id: item for item in registry.sources}
    if clean_id not in existing and len(existing) >= MAX_CUSTOM_MEDIA_SOURCES:
        raise ValueError(
            f"This server already has the maximum {MAX_CUSTOM_MEDIA_SOURCES} custom media sources."
        )

    previous = existing.get(clean_id)
    created_at = (
        previous.created_at
        if previous is not None and previous.created_at
        else datetime.now(timezone.utc).isoformat()
    )
    existing[clean_id] = CustomMediaSource(
        source_id=clean_id,
        label=clean_label,
        endpoint_url=clean_url,
        provider_type=clean_type,
        enabled=True if previous is None else bool(previous.enabled),
        added_by=_safe_int(added_by),
        created_at=created_at,
    )
    return MediaSourceRegistry(
        revision=int(registry.revision) + 1,
        sources=tuple(existing.values()),
    )


def set_custom_source_enabled(
    registry: MediaSourceRegistry,
    source_id: str,
    enabled: bool,
) -> MediaSourceRegistry:
    clean_id = _safe_id(source_id)
    found = False
    updated: list[CustomMediaSource] = []
    for source in registry.sources:
        if source.source_id == clean_id:
            updated.append(replace(source, enabled=bool(enabled)))
            found = True
        else:
            updated.append(source)
    if not found:
        raise LookupError("Custom media source not found.")
    return MediaSourceRegistry(
        revision=int(registry.revision) + 1,
        sources=tuple(updated),
    )


def remove_custom_source(
    registry: MediaSourceRegistry,
    source_id: str,
) -> MediaSourceRegistry:
    clean_id = _safe_id(source_id)
    updated = tuple(
        source for source in registry.sources if source.source_id != clean_id
    )
    if len(updated) == len(registry.sources):
        raise LookupError("Custom media source not found.")
    return MediaSourceRegistry(
        revision=int(registry.revision) + 1,
        sources=updated,
    )


def enabled_custom_sources(registry: MediaSourceRegistry) -> tuple[CustomMediaSource, ...]:
    return tuple(item for item in registry.sources if item.enabled)


def enabled_structured_sources(registry: MediaSourceRegistry) -> tuple[CustomMediaSource, ...]:
    return tuple(
        item
        for item in registry.sources
        if item.enabled and item.provider_type == PROVIDER_TYPE_JSON
    )


def enabled_external_sources(registry: MediaSourceRegistry) -> tuple[CustomMediaSource, ...]:
    return tuple(
        item
        for item in registry.sources
        if item.enabled and item.provider_type == PROVIDER_TYPE_EXTERNAL
    )


async def load_media_source_registry(
    guild_id: int,
    *,
    refresh: bool = False,
) -> tuple[Mapping[str, Any], MediaSourceRegistry]:
    from stoney_verify.guild_config import get_guild_config

    raw = await get_guild_config(int(guild_id), refresh=bool(refresh))
    return raw, parse_media_source_registry(raw)


async def save_media_source_registry(
    guild_id: int,
    *,
    expected_config: Mapping[str, Any],
    updated: MediaSourceRegistry,
) -> tuple[bool, Mapping[str, Any]]:
    from stoney_verify.guild_config import compare_and_swap_guild_config_key

    expected = expected_config.get(MEDIA_SOURCE_REGISTRY_KEY)
    applied, saved = await compare_and_swap_guild_config_key(
        int(guild_id),
        MEDIA_SOURCE_REGISTRY_KEY,
        expected=expected,
        value=updated.to_payload(),
        source="movie_night_media_source_registry",
    )
    return bool(applied), saved


__all__ = [
    "render_provider_search_url",
    "enabled_external_sources",
    "enabled_structured_sources",
    "PROVIDER_TYPE_EXTERNAL",
    "PROVIDER_TYPE_JSON",
    "CustomMediaSource",
    "MEDIA_SOURCE_REGISTRY_KEY",
    "MAX_CUSTOM_MEDIA_SOURCES",
    "MediaSourceRegistry",
    "add_custom_source",
    "enabled_custom_sources",
    "load_media_source_registry",
    "parse_media_source_registry",
    "prepare_example_search_url",
    "remove_custom_source",
    "save_media_source_registry",
    "set_custom_source_enabled",
]
