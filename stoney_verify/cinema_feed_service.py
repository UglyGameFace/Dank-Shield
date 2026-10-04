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


def _payload(source: Any, *, guild_id: int, include_endpoint: bool) -> dict[str, Any]:
    runtime = _RUNTIME_STATE.get((int(guild_id), str(source.source_id)), {})
    provider_type = str(source.provider_type or PROVIDER_TYPE_JSON)
    category = str(
        getattr(source, "category", MEDIA_CATEGORY_CUSTOM) or MEDIA_CATEGORY_CUSTOM
    )
    payload: dict[str, Any] = {
        "source_id": str(source.source_id),
        "label": str(source.label),
        "provider_type": provider_type,
        "category": category if category in CATEGORIES else MEDIA_CATEGORY_CUSTOM,
        "enabled": bool(source.enabled),
        "search_capable": provider_type
        in {PROVIDER_TYPE_JSON, PROVIDER_TYPE_EXTERNAL},
        "discovery_capable": provider_type
        in {PROVIDER_TYPE_JSON, PROVIDER_TYPE_FEED},
        "playback_capable": provider_type
        in {PROVIDER_TYPE_JSON, PROVIDER_TYPE_FEED},
        "last_refresh_at": int(runtime.get("refreshed_at") or 0),
        "last_refresh_ok": runtime.get("ok"),
        "last_refresh_error": str(runtime.get("error") or "")[:240],
        "newly_discovered": list(runtime.get("titles") or [])[:8],
    }
    if include_endpoint:
        payload["endpoint_url"] = str(source.endpoint_url)
    return payload


async def feed_state(
    guild_id: int,
    *,
    can_manage: bool,
    refresh: bool = False,
) -> dict[str, Any]:
    _raw, registry = await load_media_source_registry(
        int(guild_id), refresh=bool(refresh)
    )
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
        "categories": list(CATEGORIES),
    }


async def refresh_feed(
    guild_id: int,
    *,
    source_id: str,
    query: str = "movie",
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

    outcome = await preview_custom_media_source(
        source,
        query=str(query or "movie")[:180],
        limit=8,
    )
    error = str(outcome.errors[0]) if outcome.errors else ""
    _RUNTIME_STATE[(int(guild_id), source.source_id)] = {
        "refreshed_at": int(time.time()),
        "ok": not bool(error),
        "error": error,
        "titles": [str(item.title)[:180] for item in outcome.variants[:8]],
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
            query=str(payload.get("query") or "movie"),
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
    "feed_state",
    "mutate_feed",
    "refresh_feed",
    "runtime_state",
]
