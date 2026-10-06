from __future__ import annotations

"""Shared Cinema Feed Center state/mutation owner.

Both the signed Watch player and the full Cinema site use this service so feed
status, permissions, refresh behavior, categorization, and CAS persistence never
fork into two implementations.
"""

import time
from typing import Any, Mapping

from .media_source_registry import (
    MEDIA_CATEGORY_ANIME,
    MEDIA_CATEGORY_CUSTOM,
    MEDIA_CATEGORY_DOCUMENTARIES,
    MEDIA_CATEGORY_MOVIES,
    MEDIA_CATEGORY_TV,
    PROVIDER_TYPE_EXTERNAL,
    PROVIDER_TYPE_FEED,
    PROVIDER_TYPE_JSON,
    add_custom_source,
    load_media_source_registry,
    prepare_example_search_url,
    prepare_feed_url,
    remove_custom_source,
    save_media_source_registry,
    set_custom_source_category,
    set_custom_source_enabled,
)
from .media_source_resolver import preview_custom_media_source
from .cinema_discovery_service import enrich_discovery_rows, page_discoveries, record_feed_discoveries
from .cinema_storage import CinemaStorageUnavailable

CATEGORIES = (
    MEDIA_CATEGORY_MOVIES,
    MEDIA_CATEGORY_TV,
    MEDIA_CATEGORY_ANIME,
    MEDIA_CATEGORY_DOCUMENTARIES,
    MEDIA_CATEGORY_CUSTOM,
)
_PROVIDER_TYPES = {
    PROVIDER_TYPE_JSON,
    PROVIDER_TYPE_FEED,
    PROVIDER_TYPE_EXTERNAL,
}
_RUNTIME_STATE: dict[tuple[int, str], dict[str, Any]] = {}


class CinemaFeedConflict(RuntimeError):
    pass


def _default_refresh_query(category: str) -> str:
    clean = str(category or MEDIA_CATEGORY_CUSTOM).strip().lower()
    return {
        MEDIA_CATEGORY_MOVIES: "movie",
        MEDIA_CATEGORY_TV: "tv",
        MEDIA_CATEGORY_ANIME: "anime",
        MEDIA_CATEGORY_DOCUMENTARIES: "documentary",
        MEDIA_CATEGORY_CUSTOM: "movie",
    }.get(clean, "movie")


def _result_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    metadata = (
        dict(row.get("metadata") or {})
        if isinstance(row.get("metadata"), Mapping)
        else {}
    )
    return {
        "result_kind": "feed_discovery",
        "source_id": str(row.get("source_id") or "")[:100],
        "source_label": str(
            row.get("source_label")
            or metadata.get("source_label")
            or "Cinema source"
        )[:80],
        "category": str(
            row.get("category")
            or metadata.get("category")
            or MEDIA_CATEGORY_CUSTOM
        )[:40],
        "title": str(row.get("title") or metadata.get("release_title") or "Untitled")[:180],
        "release_title": str(metadata.get("release_title") or row.get("title") or "")[:240],
        "media_type": (
            str(row.get("media_type") or "")[:16]
            if row.get("media_type")
            else None
        ),
        "tmdb_id": int(row.get("tmdb_id") or 0) or None,
        "poster_url": str(metadata.get("poster_url") or ""),
        "backdrop_url": str(metadata.get("backdrop_url") or ""),
        "year": int(metadata.get("year") or 0),
        "rating": float(metadata.get("rating") or 0.0),
        "overview": str(metadata.get("overview") or "")[:900],
        "playable": bool(row.get("playable", True)),
        "first_seen_at": str(row.get("first_seen_at") or ""),
        "last_seen_at": str(row.get("last_seen_at") or ""),
    }


def _runtime_result_payload(
    variant: Any,
    *,
    category: str,
) -> dict[str, Any]:
    metadata = dict(getattr(variant, "metadata", {}) or {})
    source_reported = (
        dict(metadata.get("source_reported") or {})
        if isinstance(metadata.get("source_reported"), Mapping)
        else {}
    )
    release = (
        dict(metadata.get("release_name") or {})
        if isinstance(metadata.get("release_name"), Mapping)
        else {}
    )
    return {
        "result_kind": "feed_discovery",
        "source_id": str(getattr(variant, "source_id", "") or "")[:100],
        "source_label": str(getattr(variant, "source_label", "") or "Cinema source")[:80],
        "category": str(category or MEDIA_CATEGORY_CUSTOM)[:40],
        "title": str(getattr(variant, "title", "") or "Untitled")[:180],
        "release_title": str(getattr(variant, "title", "") or "")[:240],
        "media_type": None,
        "tmdb_id": None,
        "poster_url": "",
        "backdrop_url": "",
        "year": int(release.get("year") or source_reported.get("year") or 0),
        "rating": 0.0,
        "overview": "",
        "playable": True,
        "first_seen_at": "",
        "last_seen_at": "",
        "seeds": int(getattr(variant, "seeds", 0) or 0),
        "leechers": int(getattr(variant, "leechers", 0) or 0),
        "peers": int(getattr(variant, "peers", 0) or 0),
        "file_size": int(getattr(variant, "file_size", 0) or 0),
    }


def _payload(source: Any, *, guild_id: int, include_endpoint: bool) -> dict[str, Any]:
    runtime = _RUNTIME_STATE.get((int(guild_id), str(source.source_id)), {})
    provider_type = str(source.provider_type or PROVIDER_TYPE_JSON)
    category = str(
        getattr(source, "category", MEDIA_CATEGORY_CUSTOM) or MEDIA_CATEGORY_CUSTOM
    )
    last_refresh_ok = runtime.get("ok")
    if not bool(source.enabled):
        health_state = "disabled"
    elif provider_type == PROVIDER_TYPE_EXTERNAL:
        health_state = "reference"
    elif last_refresh_ok is True:
        health_state = "online"
    elif last_refresh_ok is False:
        health_state = "offline"
    else:
        health_state = "unchecked"

    payload: dict[str, Any] = {
        "source_id": str(source.source_id),
        "label": str(source.label),
        "provider_type": provider_type,
        "category": category if category in CATEGORIES else MEDIA_CATEGORY_CUSTOM,
        "enabled": bool(source.enabled),
        "search_capable": provider_type == PROVIDER_TYPE_JSON,
        "discovery_capable": provider_type
        in {PROVIDER_TYPE_JSON, PROVIDER_TYPE_FEED},
        "playback_capable": provider_type
        in {PROVIDER_TYPE_JSON, PROVIDER_TYPE_FEED},
        "supported_media_types": [
            category if category in CATEGORIES else MEDIA_CATEGORY_CUSTOM
        ],
        "health_state": health_state,
        "last_refresh_at": int(runtime.get("refreshed_at") or 0),
        "last_refresh_ok": last_refresh_ok,
        "last_refresh_error": str(runtime.get("error") or "")[:240],
        "discovery_warning": str(runtime.get("discovery_warning") or "")[:240],
        "newly_discovered": list(runtime.get("titles") or [])[:8],
        "last_refresh_result_count": int(runtime.get("result_count") or 0),
    }
    if include_endpoint:
        payload["endpoint_url"] = str(source.endpoint_url)
    return payload


async def feed_state(
    guild_id: int,
    *,
    can_manage: bool,
    refresh: bool = False,
    query: str = "",
    page: int = 1,
    page_size: int = 8,
) -> dict[str, Any]:
    _raw, registry = await load_media_source_registry(
        int(guild_id), refresh=bool(refresh)
    )
    results_warning = ""
    clean_query = " ".join(str(query or "").split())[:120]
    current_page = max(1, int(page))
    current_page_size = max(1, min(int(page_size), 24))
    page_data: dict[str, Any] = {
        "rows": [],
        "query": clean_query,
        "page": current_page,
        "page_size": current_page_size,
        "total": 0,
        "total_pages": 1,
        "has_previous": current_page > 1,
        "has_next": False,
    }
    try:
        page_data = await page_discoveries(
            int(guild_id),
            query=clean_query,
            page=current_page,
            page_size=current_page_size,
        )
        enriched_rows = await enrich_discovery_rows(
            int(guild_id),
            page_data.get("rows") or [],
            max_items=4,
        )
        page_data["rows"] = enriched_rows
    except CinemaStorageUnavailable:
        results_warning = "Saved feed results are temporarily unavailable."
    except Exception:
        results_warning = "Saved feed results could not be loaded."

    merged_results = [
        _result_payload(row)
        for row in page_data.get("rows") or []
        if isinstance(row, Mapping)
    ]

    if results_warning and int(page_data.get("page") or 1) == 1:
        runtime_results: list[dict[str, Any]] = []
        for source in registry.sources:
            if not (can_manage or source.enabled):
                continue
            runtime = _RUNTIME_STATE.get((int(guild_id), str(source.source_id)), {})
            rows = runtime.get("results")
            if isinstance(rows, list):
                runtime_results.extend(
                    dict(row)
                    for row in rows[:8]
                    if isinstance(row, Mapping)
                )
        if clean_query:
            needle = clean_query.casefold()
            runtime_results = [
                row
                for row in runtime_results
                if needle in str(row.get("title") or "").casefold()
                or needle in str(row.get("release_title") or "").casefold()
            ]
        merged_results = runtime_results[:current_page_size]

    return {
        "revision": int(registry.revision),
        "can_manage": bool(can_manage),
        "sources": [
            _payload(
                source,
                guild_id=int(guild_id),
                include_endpoint=bool(can_manage),
            )
            for source in registry.sources
            if can_manage or source.enabled
        ],
        "results": merged_results,
        "results_warning": results_warning,
        "pagination": {
            "query": str(page_data.get("query") or clean_query),
            "page": int(page_data.get("page") or current_page),
            "page_size": int(page_data.get("page_size") or current_page_size),
            "total": int(page_data.get("total") or 0),
            "total_pages": int(page_data.get("total_pages") or 1),
            "has_previous": bool(page_data.get("has_previous")),
            "has_next": bool(page_data.get("has_next")),
        },
        "categories": list(CATEGORIES),
    }


async def refresh_feed(
    guild_id: int,
    *,
    source_id: str,
    query: str = "",
) -> None:
    _raw, registry = await load_media_source_registry(int(guild_id), refresh=True)
    source = next(
        (item for item in registry.sources if item.source_id == str(source_id)),
        None,
    )
    if source is None:
        raise LookupError("Media source not found.")
    if not source.enabled:
        raise ValueError("Enable this source before refreshing it.")

    provider_type = str(getattr(source, "provider_type", "") or "")
    if provider_type == PROVIDER_TYPE_FEED:
        refresh_query = " ".join(str(query or "").split())[:180]
    else:
        refresh_query = " ".join(
            str(
                query
                or _default_refresh_query(
                    getattr(source, "category", MEDIA_CATEGORY_CUSTOM)
                )
            ).split()
        )[:180]
    outcome = await preview_custom_media_source(
        source,
        query=refresh_query,
        limit=8,
    )
    error = str(outcome.errors[0]) if outcome.errors else ""
    titles = [str(item.title)[:180] for item in outcome.variants[:8]]
    category = str(
        getattr(source, "category", MEDIA_CATEGORY_CUSTOM) or MEDIA_CATEGORY_CUSTOM
    )
    runtime_results = [
        _runtime_result_payload(
            variant,
            category=category,
        )
        for variant in outcome.variants[:8]
    ]
    discovery_warning = ""
    if provider_type == PROVIDER_TYPE_FEED and not titles and not error:
        discovery_warning = (
            "Feed is reachable, but it returned no playable magnet or .torrent "
            "items during this refresh."
        )
    if titles:
        try:
            recorded = await record_feed_discoveries(
                int(guild_id),
                source_id=str(source.source_id),
                source_label=str(source.label),
                category=category,
                titles=titles,
            )
            if recorded:
                canonical_by_release = {
                    str(
                        (
                            dict(item.get("metadata") or {})
                            if isinstance(item.get("metadata"), Mapping)
                            else {}
                        ).get("release_title")
                        or item.get("title")
                        or ""
                    ).casefold(): _result_payload(item)
                    for item in recorded
                    if isinstance(item, Mapping)
                }
                runtime_results = [
                    canonical_by_release.get(
                        str(row.get("release_title") or row.get("title") or "").casefold(),
                        row,
                    )
                    for row in runtime_results
                ]
        except CinemaStorageUnavailable:
            discovery_warning = (
                "Source refreshed, but Recently Added storage is temporarily unavailable."
            )
        except Exception:
            discovery_warning = (
                "Source refreshed, but new-title metadata could not be indexed."
            )

    _RUNTIME_STATE[(int(guild_id), source.source_id)] = {
        "refreshed_at": int(time.time()),
        "ok": not bool(error),
        "error": error,
        "discovery_warning": discovery_warning,
        "titles": titles,
        "results": runtime_results,
        "result_count": len(runtime_results),
        "refresh_query": refresh_query,
    }


async def mutate_feed(
    guild_id: int,
    *,
    actor_id: int,
    action: str,
    payload: Mapping[str, Any],
) -> None:
    clean_action = str(action or "").strip().lower()
    source_id = str(payload.get("source_id") or "").strip()
    raw_config, registry = await load_media_source_registry(
        int(guild_id),
        refresh=True,
    )

    if clean_action == "refresh":
        await refresh_feed(
            int(guild_id),
            source_id=source_id,
            query=str(payload.get("query") or ""),
        )
        return

    if clean_action == "save":
        label = " ".join(str(payload.get("label") or "").split())[:80]
        provider_type = str(
            payload.get("provider_type") or PROVIDER_TYPE_FEED
        ).strip().lower()
        if provider_type not in _PROVIDER_TYPES:
            raise ValueError("Unsupported media source type.")
        category = str(
            payload.get("category") or MEDIA_CATEGORY_CUSTOM
        ).strip().lower()
        if category not in CATEGORIES:
            category = MEDIA_CATEGORY_CUSTOM
        raw_url = str(payload.get("endpoint_url") or "").strip()
        endpoint_url = (
            prepare_feed_url(raw_url)
            if provider_type == PROVIDER_TYPE_FEED
            else prepare_example_search_url(raw_url)
        )
        updated = add_custom_source(
            registry,
            source_id=source_id,
            label=label,
            endpoint_url=endpoint_url,
            added_by=int(actor_id),
            provider_type=provider_type,
            category=category,
        )
        actual_id = source_id
        if not actual_id:
            before = {item.source_id for item in registry.sources}
            created = [
                item for item in updated.sources if item.source_id not in before
            ]
            actual_id = created[0].source_id if created else ""
        if actual_id:
            updated = set_custom_source_category(updated, actual_id, category)
    elif clean_action == "toggle":
        source = next(
            (item for item in registry.sources if item.source_id == source_id),
            None,
        )
        if source is None:
            raise LookupError("Media source not found.")
        updated = set_custom_source_enabled(
            registry,
            source_id,
            not bool(source.enabled),
        )
    elif clean_action == "remove":
        updated = remove_custom_source(registry, source_id)
        _RUNTIME_STATE.pop((int(guild_id), source_id), None)
    elif clean_action == "category":
        category = str(
            payload.get("category") or MEDIA_CATEGORY_CUSTOM
        ).strip().lower()
        if category not in CATEGORIES:
            raise ValueError("Unsupported media category.")
        updated = set_custom_source_category(
            registry,
            source_id,
            category,
        )
    else:
        raise ValueError("Unsupported media source action.")

    applied, _saved = await save_media_source_registry(
        int(guild_id),
        expected_config=raw_config,
        updated=updated,
    )
    if not applied:
        raise CinemaFeedConflict(
            "Cinema sources changed elsewhere. Refresh the Feed Center and try again."
        )


def runtime_state() -> dict[tuple[int, str], dict[str, Any]]:
    return _RUNTIME_STATE


__all__ = [
    "CATEGORIES",
    "CinemaFeedConflict",
    "_default_refresh_query",
    "feed_state",
    "mutate_feed",
    "refresh_feed",
    "runtime_state",
]
