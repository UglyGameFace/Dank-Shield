from __future__ import annotations

"""Dank Cinema Feed Center personalization and curation.

Shared guild sources stay in the existing media source registry/resolver. This
module owns the layer above ingestion: personal subscriptions, saved searches,
filters, collections, private feed definitions, grouping, source trust, and
notification matching.
"""

import asyncio
import hashlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence

from .cinema_catalog import CinemaDetails, CinemaMedia, get_details, search_catalog
from .cinema_library_service import (
    create_notification,
    get_cinema_user,
    library_snapshot,
)
from .cinema_storage import CinemaStorageUnavailable, execute, rows, utc_now
from .media_metadata import parse_release_name
from .media_source_registry import (
    MEDIA_CATEGORY_CUSTOM,
    PROVIDER_TYPE_FEED,
    PROVIDER_TYPE_JSON,
    CustomMediaSource,
    prepare_example_search_url,
    prepare_feed_url,
)
from .media_source_resolver import preview_custom_media_source

RULE_TABLE = "dank_cinema_feed_rules"
PRIVATE_DISCOVERY_TABLE = "dank_cinema_user_feed_discoveries"
HEALTH_TABLE = "dank_cinema_feed_source_health"
MEDIA_TABLE = "dank_cinema_user_media"

_RULE_TYPES = {
    "follow",
    "saved_search",
    "filter",
    "collection",
    "routing",
    "person",
    "genre",
    "studio",
    "franchise",
    "private_source",
}
_SCOPES = {"user", "guild"}
_MEDIA_TYPES = {"movie", "tv"}
_NOTIFY_MODES = {"off", "instant", "daily"}
_PROVIDER_TYPES = {PROVIDER_TYPE_FEED, PROVIDER_TYPE_JSON}
_RESOLUTION_RANK = {
    "2160p": 5,
    "4k": 5,
    "1080p": 4,
    "720p": 3,
    "576p": 2,
    "480p": 1,
}


class CinemaFeedRuleError(ValueError):
    pass


def _clean(value: Any, limit: int = 180) -> str:
    return " ".join(str(value or "").split())[:limit]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _string_list(value: Any, *, limit: int = 12, item_limit: int = 80) -> list[str]:
    raw = value if isinstance(value, (list, tuple, set)) else []
    output: list[str] = []
    seen: set[str] = set()
    for item in raw:
        clean = _clean(item, item_limit)
        key = clean.casefold()
        if not clean or key in seen:
            continue
        seen.add(key)
        output.append(clean)
        if len(output) >= limit:
            break
    return output


def _normalize_filters(value: Any) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, Mapping) else {}
    resolutions = [
        item.casefold()
        for item in _string_list(raw.get("resolutions"), limit=8, item_limit=16)
    ]
    codecs = [
        item.casefold()
        for item in _string_list(raw.get("codecs"), limit=8, item_limit=24)
    ]
    languages = [
        item.casefold()
        for item in _string_list(raw.get("languages"), limit=12, item_limit=24)
    ]
    excluded_terms = [
        item.casefold()
        for item in _string_list(raw.get("excluded_terms"), limit=16, item_limit=60)
    ]
    source_ids = _string_list(raw.get("source_ids"), limit=20, item_limit=100)
    categories = [
        item.casefold()
        for item in _string_list(raw.get("categories"), limit=10, item_limit=40)
    ]
    result: dict[str, Any] = {
        "playable_only": bool(raw.get("playable_only", True)),
        "min_seeds": max(0, _safe_int(raw.get("min_seeds"))),
        "min_peers": max(0, _safe_int(raw.get("min_peers"))),
        "min_size_bytes": max(0, _safe_int(raw.get("min_size_bytes"))),
        "max_size_bytes": max(0, _safe_int(raw.get("max_size_bytes"))),
        "resolutions": resolutions,
        "codecs": codecs,
        "languages": languages,
        "excluded_terms": excluded_terms,
        "source_ids": source_ids,
        "categories": categories,
        "hdr_only": bool(raw.get("hdr_only", False)),
        "subtitles_only": bool(raw.get("subtitles_only", False)),
    }
    # Private source configuration is kept service-role-only inside the rule.
    endpoint = _clean(raw.get("endpoint_url"), 1000)
    provider_type = _clean(raw.get("provider_type"), 20).casefold()
    category = _clean(raw.get("category"), 40).casefold()
    if endpoint:
        result["endpoint_url"] = endpoint
    if provider_type:
        result["provider_type"] = provider_type
    if category:
        result["category"] = category
    return result


def _normalize_actions(value: Any) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, Mapping) else {}
    notify = _clean(raw.get("notify") or "instant", 16).casefold()
    if notify not in _NOTIFY_MODES:
        notify = "instant"
    return {
        "notify": notify,
        "collection": _clean(raw.get("collection"), 80),
        "route": _clean(raw.get("route"), 80),
        "queue_suggest": bool(raw.get("queue_suggest", False)),
    }


def _normalize_rule(row: Mapping[str, Any]) -> dict[str, Any]:
    scope = _clean(row.get("scope") or "user", 16).casefold()
    rule_type = _clean(row.get("rule_type") or "saved_search", 24).casefold()
    media_type = _clean(row.get("media_type"), 16).casefold()
    return {
        "id": _clean(row.get("id"), 80),
        "guild_id": _safe_int(row.get("guild_id")),
        "owner_user_id": _safe_int(row.get("owner_user_id")) or None,
        "scope": scope if scope in _SCOPES else "user",
        "rule_type": rule_type if rule_type in _RULE_TYPES else "saved_search",
        "name": _clean(row.get("name"), 100),
        "query": _clean(row.get("query"), 180),
        "media_type": media_type if media_type in _MEDIA_TYPES else None,
        "tmdb_id": _safe_int(row.get("tmdb_id")) or None,
        "filters": _normalize_filters(row.get("filters")),
        "actions": _normalize_actions(row.get("actions")),
        "enabled": bool(row.get("enabled", True)),
        "created_by": _safe_int(row.get("created_by")),
        "created_at": str(row.get("created_at") or ""),
        "updated_at": str(row.get("updated_at") or ""),
    }


async def list_feed_rules(
    guild_id: int,
    user_id: int,
    *,
    include_disabled: bool = True,
) -> list[dict[str, Any]]:
    gid = int(guild_id)
    uid = int(user_id)

    def read_user(client: Any):
        query = (
            client.table(RULE_TABLE)
            .select("*")
            .eq("guild_id", gid)
            .eq("scope", "user")
            .eq("owner_user_id", uid)
            .order("updated_at", desc=True)
            .limit(100)
        )
        if not include_disabled:
            query = query.eq("enabled", True)
        return query.execute()

    def read_guild(client: Any):
        query = (
            client.table(RULE_TABLE)
            .select("*")
            .eq("guild_id", gid)
            .eq("scope", "guild")
            .order("updated_at", desc=True)
            .limit(100)
        )
        if not include_disabled:
            query = query.eq("enabled", True)
        return query.execute()

    user_rows, guild_rows = await asyncio.gather(
        execute(f"read personal Cinema feed rules {gid}:{uid}", read_user),
        execute(f"read guild Cinema feed rules {gid}", read_guild),
    )
    return [
        _normalize_rule(row)
        for row in (*rows(user_rows), *rows(guild_rows))
    ]


async def _get_rule(rule_id: str) -> Optional[dict[str, Any]]:
    clean_id = _clean(rule_id, 80)
    if not clean_id:
        return None

    def read(client: Any):
        return client.table(RULE_TABLE).select("*").eq("id", clean_id).limit(1).execute()

    found = rows(await execute(f"read Cinema feed rule {clean_id}", read))
    return _normalize_rule(found[0]) if found else None


def _rule_write_payload(
    guild_id: int,
    user_id: int,
    payload: Mapping[str, Any],
    *,
    can_manage: bool,
) -> dict[str, Any]:
    gid = int(guild_id)
    uid = int(user_id)
    scope = _clean(payload.get("scope") or "user", 16).casefold()
    rule_type = _clean(payload.get("rule_type") or "saved_search", 24).casefold()
    if scope not in _SCOPES:
        raise CinemaFeedRuleError("Unsupported Feed Rule scope.")
    if rule_type not in _RULE_TYPES:
        raise CinemaFeedRuleError("Unsupported Feed Rule type.")
    if scope == "guild" and not can_manage:
        raise PermissionError("Manage Server permission is required for server Feed Rules.")
    if rule_type == "private_source" and scope != "user":
        raise CinemaFeedRuleError("Private feed sources must belong to one user.")

    query = _clean(payload.get("query"), 180)
    name = _clean(payload.get("name"), 100)
    media_type = _clean(payload.get("media_type"), 16).casefold()
    if media_type not in _MEDIA_TYPES:
        media_type = ""
    tmdb_id = max(0, _safe_int(payload.get("tmdb_id")))
    filters = _normalize_filters(payload.get("filters"))
    actions = _normalize_actions(payload.get("actions"))

    if rule_type in {
        "follow",
        "saved_search",
        "collection",
        "routing",
        "person",
        "genre",
        "studio",
        "franchise",
    } and not (query or tmdb_id):
        raise CinemaFeedRuleError("This Feed Rule needs a title, search, or TMDB identity.")

    if rule_type == "private_source":
        endpoint = str(filters.get("endpoint_url") or "")
        provider_type = str(filters.get("provider_type") or PROVIDER_TYPE_FEED).casefold()
        if provider_type not in _PROVIDER_TYPES:
            raise CinemaFeedRuleError("Private sources support RSS/Atom or structured JSON APIs.")
        filters["provider_type"] = provider_type
        filters["endpoint_url"] = (
            prepare_feed_url(endpoint)
            if provider_type == PROVIDER_TYPE_FEED
            else prepare_example_search_url(endpoint)
        )
        filters["category"] = str(
            filters.get("category") or MEDIA_CATEGORY_CUSTOM
        ).casefold()[:40]
        if not name:
            name = "My Private Feed"

    if not name:
        name = query or "Feed Rule"

    return {
        "guild_id": gid,
        "owner_user_id": uid if scope == "user" else None,
        "scope": scope,
        "rule_type": rule_type,
        "name": name,
        "query": query,
        "media_type": media_type or None,
        "tmdb_id": tmdb_id or None,
        "filters": filters,
        "actions": actions,
        "enabled": bool(payload.get("enabled", True)),
        "created_by": uid,
        "updated_at": utc_now(),
    }


async def save_feed_rule(
    guild_id: int,
    user_id: int,
    payload: Mapping[str, Any],
    *,
    can_manage: bool,
) -> dict[str, Any]:
    clean_id = _clean(payload.get("id"), 80)
    write_payload = _rule_write_payload(
        guild_id,
        user_id,
        payload,
        can_manage=can_manage,
    )

    if clean_id:
        existing = await _get_rule(clean_id)
        if existing is None or int(existing.get("guild_id") or 0) != int(guild_id):
            raise LookupError("Feed Rule not found.")
        if existing.get("scope") == "guild":
            if not can_manage:
                raise PermissionError("Manage Server permission is required for server Feed Rules.")
        elif int(existing.get("owner_user_id") or 0) != int(user_id):
            raise PermissionError("That personal Feed Rule belongs to another user.")

        def update(client: Any):
            return (
                client.table(RULE_TABLE)
                .update(write_payload)
                .eq("id", clean_id)
                .eq("guild_id", int(guild_id))
                .execute()
            )

        response = await execute(f"update Cinema feed rule {clean_id}", update)
    else:
        write_payload["created_at"] = utc_now()

        def insert(client: Any):
            return client.table(RULE_TABLE).insert(write_payload).execute()

        response = await execute(
            f"create Cinema feed rule {guild_id}:{user_id}",
            insert,
        )

    saved = rows(response)
    return _normalize_rule(saved[0] if saved else {**write_payload, "id": clean_id})


async def delete_feed_rule(
    guild_id: int,
    user_id: int,
    rule_id: str,
    *,
    can_manage: bool,
) -> None:
    existing = await _get_rule(rule_id)
    if existing is None or int(existing.get("guild_id") or 0) != int(guild_id):
        raise LookupError("Feed Rule not found.")
    if existing.get("scope") == "guild":
        if not can_manage:
            raise PermissionError("Manage Server permission is required for server Feed Rules.")
    elif int(existing.get("owner_user_id") or 0) != int(user_id):
        raise PermissionError("That personal Feed Rule belongs to another user.")

    def remove(client: Any):
        return (
            client.table(RULE_TABLE)
            .delete()
            .eq("id", str(existing["id"]))
            .eq("guild_id", int(guild_id))
            .execute()
        )

    await execute(f"delete Cinema feed rule {existing['id']}", remove)


def _release_details(row: Mapping[str, Any]) -> dict[str, Any]:
    metadata = (
        dict(row.get("metadata") or {})
        if isinstance(row.get("metadata"), Mapping)
        else {}
    )
    release_title = _clean(
        row.get("release_title")
        or metadata.get("release_title")
        or row.get("title"),
        300,
    )
    parsed = parse_release_name(release_title)
    video_tags = [
        str(item).casefold()
        for item in parsed.get("video_tags") or []
        if str(item).strip()
    ]
    audio_tags = [
        str(item).casefold()
        for item in parsed.get("audio_tags") or []
        if str(item).strip()
    ]
    hdr_tags = [
        str(item).casefold()
        for item in parsed.get("hdr_tags") or []
        if str(item).strip()
    ]
    resolution = str(parsed.get("resolution") or "").casefold()
    quality_rank = _RESOLUTION_RANK.get(resolution, 0)
    if any("av1" in item for item in video_tags):
        codec = "av1"
    elif any("265" in item or "hevc" in item for item in video_tags):
        codec = "x265"
    elif any("264" in item for item in video_tags):
        codec = "x264"
    else:
        codec = video_tags[0] if video_tags else ""

    languages = _string_list(
        metadata.get("languages")
        or metadata.get("language_tags")
        or [],
        limit=12,
        item_limit=24,
    )
    subtitle_languages = _string_list(
        metadata.get("subtitle_languages")
        or metadata.get("subtitles")
        or [],
        limit=12,
        item_limit=24,
    )
    release_has_subtitle_tag = bool(
        re.search(
            r"(?i)(?:^|[ ._\-])(?:sub|subs|subbed|subtitle|subtitles|multi[-_. ]?sub)(?:$|[ ._\-])",
            release_title,
        )
    )
    return {
        "release_title": release_title,
        "season": _safe_int(parsed.get("season")) or None,
        "episode": _safe_int(parsed.get("episode")) or None,
        "resolution": resolution,
        "quality_rank": quality_rank,
        "codec": codec,
        "video_tags": video_tags,
        "audio_tags": audio_tags,
        "hdr_tags": hdr_tags,
        "languages": [item.casefold() for item in languages],
        "subtitle_languages": [item.casefold() for item in subtitle_languages],
        "has_subtitles": bool(subtitle_languages) or release_has_subtitle_tag,
        "release_group": _clean(parsed.get("release_group"), 80),
        "seeds": max(0, _safe_int(row.get("seeds") or metadata.get("seeds"))),
        "leechers": max(0, _safe_int(row.get("leechers") or metadata.get("leechers"))),
        "peers": max(0, _safe_int(row.get("peers") or metadata.get("peers"))),
        "file_size": max(0, _safe_int(row.get("file_size") or metadata.get("file_size"))),
        "source_id": _clean(row.get("source_id"), 100),
        "source_label": _clean(row.get("source_label") or metadata.get("source_label"), 80),
        "category": _clean(row.get("category") or metadata.get("category"), 40),
        "playable": bool(row.get("playable", True)),
        "first_seen_at": str(row.get("first_seen_at") or ""),
        "last_seen_at": str(row.get("last_seen_at") or ""),
        "discovery_key": _clean(row.get("discovery_key"), 80),
    }


def _group_key(row: Mapping[str, Any], release: Mapping[str, Any]) -> str:
    media_type = _clean(row.get("media_type"), 16).casefold()
    tmdb_id = _safe_int(row.get("tmdb_id"))
    season = _safe_int(release.get("season"), -1)
    episode = _safe_int(release.get("episode"), -1)
    if tmdb_id > 0 and media_type in _MEDIA_TYPES:
        suffix = (
            f":s{season:02d}:e{episode:03d}"
            if season >= 0 and episode > 0
            else ""
        )
        return f"{media_type}:{tmdb_id}{suffix}"
    normalized = re.sub(
        r"[^a-z0-9]+",
        " ",
        _clean(row.get("title") or release.get("release_title"), 240).casefold(),
    )
    normalized = " ".join(normalized.split())[:180]
    if season >= 0 and episode > 0:
        normalized = f"{normalized}:s{season:02d}:e{episode:03d}"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]
    return f"title:{digest}"


def group_feed_results(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for raw in results:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        release = _release_details(row)
        key = _group_key(row, release)
        group = grouped.get(key)
        if group is None:
            group = dict(row)
            group["group_key"] = key
            group["releases"] = []
            group["match_reasons"] = []
            grouped[key] = group
        group["releases"].append(release)

        if not group.get("poster_url") and row.get("poster_url"):
            group["poster_url"] = row.get("poster_url")
        if not group.get("backdrop_url") and row.get("backdrop_url"):
            group["backdrop_url"] = row.get("backdrop_url")
        if not group.get("overview") and row.get("overview"):
            group["overview"] = row.get("overview")

    output: list[dict[str, Any]] = []
    for group in grouped.values():
        releases = list(group.get("releases") or [])
        releases.sort(
            key=lambda item: (
                int(item.get("quality_rank") or 0),
                int(item.get("seeds") or 0),
                int(item.get("peers") or 0),
            ),
            reverse=True,
        )
        group["releases"] = releases
        group["release_count"] = len(releases)
        group["source_count"] = len(
            {str(item.get("source_id") or "") for item in releases if item.get("source_id")}
        )
        group["best_release"] = dict(releases[0]) if releases else {}
        ranks = sorted(
            {int(item.get("quality_rank") or 0) for item in releases if int(item.get("quality_rank") or 0) > 0}
        )
        group["upgrade_available"] = len(ranks) > 1
        group["best_quality"] = (
            str((releases[0] if releases else {}).get("resolution") or "")
        )
        seasons = {
            (int(item.get("season") or -1), int(item.get("episode") or -1))
            for item in releases
            if item.get("season") is not None and item.get("episode") is not None
        }
        if seasons:
            season, episode = sorted(seasons)[-1]
            group["season_number"] = season
            group["episode_number"] = episode
            group["new_episode"] = True
        else:
            group["new_episode"] = False
        output.append(group)

    output.sort(
        key=lambda row: str(
            (row.get("best_release") or {}).get("first_seen_at")
            or row.get("first_seen_at")
            or ""
        ),
        reverse=True,
    )
    return output


def _result_text(group: Mapping[str, Any]) -> str:
    releases = group.get("releases") if isinstance(group.get("releases"), list) else []
    bits = [
        str(group.get("title") or ""),
        str(group.get("release_title") or ""),
        " ".join(str(item.get("release_title") or "") for item in releases[:12]),
        " ".join(str(item) for item in group.get("genres") or []),
        " ".join(str(item) for item in group.get("studios") or []),
        " ".join(str(item) for item in group.get("franchises") or []),
        " ".join(str(item) for item in group.get("people") or []),
        " ".join(str(item) for item in group.get("directors") or []),
        " ".join(str(item) for item in group.get("creators") or []),
    ]
    return " ".join(bits).casefold()


def _matches_filters(group: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
    releases = (
        [dict(item) for item in group.get("releases") or [] if isinstance(item, Mapping)]
        if isinstance(group.get("releases"), list)
        else []
    )
    if bool(filters.get("playable_only", True)) and not any(
        bool(item.get("playable", True)) for item in releases
    ):
        return False

    excluded = [str(item).casefold() for item in filters.get("excluded_terms") or []]
    text = _result_text(group)
    if any(term and term in text for term in excluded):
        return False

    source_ids = {str(item) for item in filters.get("source_ids") or [] if str(item)}
    categories = {str(item).casefold() for item in filters.get("categories") or [] if str(item)}
    resolutions = {str(item).casefold() for item in filters.get("resolutions") or [] if str(item)}
    codecs = {str(item).casefold() for item in filters.get("codecs") or [] if str(item)}
    languages = {str(item).casefold() for item in filters.get("languages") or [] if str(item)}

    def release_ok(release: Mapping[str, Any]) -> bool:
        if source_ids and str(release.get("source_id") or "") not in source_ids:
            return False
        if categories and str(release.get("category") or "").casefold() not in categories:
            return False
        if resolutions and str(release.get("resolution") or "").casefold() not in resolutions:
            return False
        if codecs and str(release.get("codec") or "").casefold() not in codecs:
            return False
        release_languages = {
            str(item).casefold() for item in release.get("languages") or [] if str(item)
        }
        if languages and not (languages & release_languages):
            return False
        if bool(filters.get("hdr_only")) and not list(release.get("hdr_tags") or []):
            return False
        if bool(filters.get("subtitles_only")) and not bool(release.get("has_subtitles")):
            return False
        if int(release.get("seeds") or 0) < int(filters.get("min_seeds") or 0):
            return False
        if int(release.get("peers") or 0) < int(filters.get("min_peers") or 0):
            return False
        size = int(release.get("file_size") or 0)
        min_size = int(filters.get("min_size_bytes") or 0)
        max_size = int(filters.get("max_size_bytes") or 0)
        if min_size and size and size < min_size:
            return False
        if max_size and size and size > max_size:
            return False
        return True

    return any(release_ok(release) for release in releases) if releases else True


def _rule_matches(group: Mapping[str, Any], rule: Mapping[str, Any]) -> bool:
    if not bool(rule.get("enabled", True)):
        return False
    media_type = str(rule.get("media_type") or "")
    if media_type and str(group.get("media_type") or "") != media_type:
        return False

    tmdb_id = int(rule.get("tmdb_id") or 0)
    if tmdb_id:
        if int(group.get("tmdb_id") or 0) != tmdb_id:
            return False
        # Canonical identity is stronger than display-title text.
        query = ""
    else:
        query = str(rule.get("query") or "").strip().casefold()

    if query:
        rule_type = str(rule.get("rule_type") or "")
        if rule_type == "person":
            haystack = " ".join(
                str(item)
                for key in ("people", "directors", "creators")
                for item in group.get(key) or []
            ).casefold()
        elif rule_type == "genre":
            haystack = " ".join(str(item) for item in group.get("genres") or []).casefold()
        elif rule_type == "studio":
            haystack = " ".join(str(item) for item in group.get("studios") or []).casefold()
        elif rule_type == "franchise":
            haystack = " ".join(str(item) for item in group.get("franchises") or []).casefold()
        else:
            haystack = _result_text(group)
        if query not in haystack:
            return False
    return _matches_filters(group, rule.get("filters") or {})


async def build_personalized_feed(
    guild_id: int,
    user_id: int,
    server_results: Sequence[Mapping[str, Any]],
    *,
    private_results: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    gid = int(guild_id)
    uid = int(user_id)
    rules_task = asyncio.create_task(list_feed_rules(gid, uid, include_disabled=True))
    library_task = asyncio.create_task(library_snapshot(uid))
    profile_task = asyncio.create_task(get_cinema_user(uid))
    rules, library, profile = await asyncio.gather(rules_task, library_task, profile_task)

    combined = [
        dict(row)
        for row in (*server_results, *private_results)
        if isinstance(row, Mapping)
    ]
    groups = group_feed_results(combined)
    watchlist_keys = {
        (str(row.get("media_type") or ""), int(row.get("tmdb_id") or 0))
        for row in library.get("watchlist") or []
        if int(row.get("tmdb_id") or 0) > 0
    }
    user_rules = [
        rule for rule in rules
        if rule.get("scope") == "user" and rule.get("rule_type") != "private_source"
    ]
    guild_rules = [
        rule for rule in rules
        if rule.get("scope") == "guild"
    ]

    prefs = (
        dict(profile.get("preferences") or {})
        if isinstance(profile.get("preferences"), Mapping)
        else {}
    )
    default_filters = _normalize_filters(
        {
            "playable_only": prefs.get("feed_playable_only", True),
            "min_seeds": prefs.get("feed_min_seeds", 0),
            "resolutions": prefs.get("feed_preferred_resolutions", []),
            "codecs": prefs.get("feed_preferred_codecs", []),
            "languages": prefs.get("feed_preferred_languages", []),
        }
    )

    my_feed: list[dict[str, Any]] = []
    collections: dict[str, list[dict[str, Any]]] = {}

    for raw_group in groups:
        group = dict(raw_group)
        reasons: list[str] = []
        if bool(group.get("private")):
            reasons.append("Private feed")
        key = (
            str(group.get("media_type") or ""),
            int(group.get("tmdb_id") or 0),
        )
        if key in watchlist_keys:
            reasons.append("Watchlist")

        matched_user_rules = [
            rule for rule in user_rules if _rule_matches(group, rule)
        ]
        for rule in matched_user_rules:
            label = str(rule.get("name") or rule.get("rule_type") or "Feed Rule")
            if label not in reasons:
                reasons.append(label)

        if reasons and _matches_filters(group, default_filters):
            group["match_reasons"] = reasons
            group["queue_suggested"] = bool(
                prefs.get("feed_queue_suggestions", True)
                and (
                    key in watchlist_keys
                    or any(
                    bool((rule.get("actions") or {}).get("queue_suggest"))
                    or rule.get("rule_type") == "follow"
                    for rule in matched_user_rules
                    )
                )
            )
            group["watchlist_match"] = key in watchlist_keys
            group["followed_match"] = any(
                rule.get("rule_type") == "follow" for rule in matched_user_rules
            )
            my_feed.append(group)

        for rule in guild_rules:
            if rule.get("rule_type") not in {"collection", "routing"}:
                continue
            if not _rule_matches(group, rule):
                continue
            label = str(
                (rule.get("actions") or {}).get("collection")
                or rule.get("name")
                or "Featured"
            )
            collections.setdefault(label, []).append(dict(group))

        for rule in matched_user_rules:
            collection = str((rule.get("actions") or {}).get("collection") or "")
            if collection:
                collections.setdefault(collection, []).append(dict(group))

    return {
        "rules": rules,
        "my_feed": my_feed[:60],
        "collections": [
            {"name": name, "items": items[:40]}
            for name, items in collections.items()
        ],
        "grouped_results": groups,
        "watchlist_match_count": sum(
            1 for item in my_feed if item.get("watchlist_match")
        ),
        "queue_suggestion_count": sum(
            1 for item in my_feed if item.get("queue_suggested")
        ),
    }


def source_trust_payload(row: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    if not isinstance(row, Mapping):
        return {
            "trust_score": None,
            "trust_label": "Unrated",
            "refresh_count": 0,
            "success_rate": None,
            "total_results": 0,
        }
    refresh_count = max(0, _safe_int(row.get("refresh_count")))
    success_count = max(0, _safe_int(row.get("success_count")))
    failure_count = max(0, _safe_int(row.get("failure_count")))
    total_results = max(0, _safe_int(row.get("total_results")))
    if refresh_count <= 0:
        score = None
    else:
        success_rate = min(1.0, success_count / max(1, refresh_count))
        productivity = min(1.0, total_results / max(1, success_count * 8))
        score = round((success_rate * 82.0) + (productivity * 18.0))
    if score is None:
        label = "Unrated"
    elif score >= 90:
        label = "Excellent"
    elif score >= 75:
        label = "Good"
    elif score >= 55:
        label = "Fair"
    else:
        label = "Needs attention"
    return {
        "trust_score": score,
        "trust_label": label,
        "refresh_count": refresh_count,
        "success_count": success_count,
        "failure_count": failure_count,
        "success_rate": (
            round(success_count / max(1, refresh_count), 3)
            if refresh_count
            else None
        ),
        "total_results": total_results,
        "last_result_count": max(0, _safe_int(row.get("last_result_count"))),
        "last_error": _clean(row.get("last_error"), 240),
        "last_refreshed_at": str(row.get("last_refreshed_at") or ""),
        "last_success_at": str(row.get("last_success_at") or ""),
    }


async def ensure_source_health(guild_id: int, source_id: str) -> None:
    gid = int(guild_id)
    sid = _clean(source_id, 100)
    if not sid:
        return

    def read(client: Any):
        return (
            client.table(HEALTH_TABLE)
            .select("guild_id,source_id")
            .eq("guild_id", gid)
            .eq("source_id", sid)
            .limit(1)
            .execute()
        )

    if rows(await execute(f"read Cinema source enrollment {gid}:{sid}", read)):
        return

    payload = {
        "guild_id": gid,
        "source_id": sid,
        "refresh_count": 0,
        "success_count": 0,
        "failure_count": 0,
        "total_results": 0,
        "last_result_count": 0,
        "last_error": "",
        "updated_at": utc_now(),
    }

    def insert(client: Any):
        return client.table(HEALTH_TABLE).insert(payload).execute()

    try:
        await execute(f"enroll Cinema source health {gid}:{sid}", insert)
    except Exception:
        # Another request/worker may have enrolled it first.
        pass


async def list_due_source_targets(
    *,
    refresh_seconds: int,
    limit: int = 24,
) -> list[tuple[int, str]]:
    safe_seconds = max(300, min(int(refresh_seconds), 86400))
    safe_limit = max(1, min(int(limit), 100))
    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=safe_seconds)
    ).isoformat()

    def read(client: Any):
        return (
            client.table(HEALTH_TABLE)
            .select("guild_id,source_id,last_refreshed_at")
            .or_(f"last_refreshed_at.is.null,last_refreshed_at.lt.{cutoff}")
            .order("last_refreshed_at")
            .limit(safe_limit * 2)
            .execute()
        )

    found = rows(await execute("read due Cinema feed refresh targets", read))
    targets: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for row in found:
        gid = _safe_int(row.get("guild_id"))
        sid = _clean(row.get("source_id"), 100)
        key = (gid, sid)
        if gid <= 0 or not sid or sid.startswith("private-") or key in seen:
            continue
        seen.add(key)
        targets.append(key)
        if len(targets) >= safe_limit:
            break
    return targets


async def list_source_health(guild_id: int) -> dict[str, dict[str, Any]]:
    gid = int(guild_id)

    def read(client: Any):
        return client.table(HEALTH_TABLE).select("*").eq("guild_id", gid).limit(300).execute()

    health_rows = rows(await execute(f"read Cinema source health {gid}", read))
    return {
        str(row.get("source_id") or ""): source_trust_payload(row)
        for row in health_rows
        if str(row.get("source_id") or "")
    }


async def record_source_health(
    guild_id: int,
    source_id: str,
    *,
    ok: bool,
    result_count: int,
    error: str = "",
) -> dict[str, Any]:
    gid = int(guild_id)
    sid = _clean(source_id, 100)
    if not sid:
        raise CinemaFeedRuleError("Cinema source id is required.")

    def read(client: Any):
        return (
            client.table(HEALTH_TABLE)
            .select("*")
            .eq("guild_id", gid)
            .eq("source_id", sid)
            .limit(1)
            .execute()
        )

    existing = rows(await execute(f"read Cinema source health {gid}:{sid}", read))
    current = existing[0] if existing else {}
    now = utc_now()
    payload = {
        "guild_id": gid,
        "source_id": sid,
        "refresh_count": max(0, _safe_int(current.get("refresh_count"))) + 1,
        "success_count": max(0, _safe_int(current.get("success_count"))) + (1 if ok else 0),
        "failure_count": max(0, _safe_int(current.get("failure_count"))) + (0 if ok else 1),
        "total_results": max(0, _safe_int(current.get("total_results")))
        + max(0, int(result_count)),
        "last_result_count": max(0, int(result_count)),
        "last_error": "" if ok else _clean(error, 240),
        "last_refreshed_at": now,
        "last_success_at": now if ok else current.get("last_success_at"),
        "updated_at": now,
    }

    def write(client: Any):
        try:
            return (
                client.table(HEALTH_TABLE)
                .upsert(payload, on_conflict="guild_id,source_id")
                .execute()
            )
        except TypeError:
            return client.table(HEALTH_TABLE).upsert(payload).execute()

    await execute(f"write Cinema source health {gid}:{sid}", write)
    return source_trust_payload(payload)


def _private_discovery_key(title: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(title or "").casefold())
    normalized = " ".join(normalized.split())[:500]
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


async def _resolve_media(title: str, category: str) -> Optional[CinemaMedia]:
    parsed = parse_release_name(title)
    query = _clean(parsed.get("title") or title, 160)
    if not query:
        return None
    wanted_type = "tv" if (
        str(category or "").casefold() in {"tv", "anime"}
        or parsed.get("season") is not None
        or parsed.get("episode") is not None
    ) else ""
    try:
        candidates = await search_catalog(query, limit=8, include_adult=False)
    except Exception:
        return None
    lowered = query.casefold()
    for media in candidates:
        if wanted_type and media.media_type != wanted_type:
            continue
        if media.title.casefold() == lowered:
            return media
    for media in candidates:
        if wanted_type and media.media_type != wanted_type:
            continue
        if all(token in title.casefold() for token in media.title.casefold().split()):
            return media
    return next(
        (
            media for media in candidates
            if not wanted_type or media.media_type == wanted_type
        ),
        None,
    )


async def record_private_discoveries(
    guild_id: int,
    user_id: int,
    *,
    source_id: str,
    source_label: str,
    category: str,
    variants: Sequence[Any],
) -> list[dict[str, Any]]:
    gid = int(guild_id)
    uid = int(user_id)
    semaphore = asyncio.Semaphore(3)

    async def one(variant: Any) -> dict[str, Any]:
        title = _clean(getattr(variant, "title", ""), 240)
        async with semaphore:
            media = await _resolve_media(title, category)
            details: CinemaDetails | None = None
            if media is not None:
                try:
                    details = await get_details(media.media_type, media.tmdb_id)
                except Exception:
                    details = None
        raw_metadata = (
            dict(getattr(variant, "metadata", {}) or {})
            if isinstance(getattr(variant, "metadata", {}), Mapping)
            else {}
        )
        parsed = parse_release_name(title)
        source_reported = (
            dict(raw_metadata.get("source_reported") or {})
            if isinstance(raw_metadata.get("source_reported"), Mapping)
            else {}
        )
        subtitle_languages: list[str] = []
        for key in ("subtitle_language", "subtitle_languages"):
            value = source_reported.get(key)
            if isinstance(value, str):
                subtitle_languages.extend(
                    item.strip()
                    for item in value.replace(",", " ").split()
                    if item.strip()
                )
            elif isinstance(value, (list, tuple, set)):
                subtitle_languages.extend(str(item) for item in value)
        metadata: dict[str, Any] = {
            "source_label": _clean(source_label, 80),
            "category": _clean(category, 40),
            "release_title": title,
            "seeds": max(0, _safe_int(getattr(variant, "seeds", 0))),
            "leechers": max(0, _safe_int(getattr(variant, "leechers", 0))),
            "peers": max(0, _safe_int(getattr(variant, "peers", 0))),
            "file_size": max(0, _safe_int(getattr(variant, "file_size", 0))),
            "release_name": parsed,
            "source_reported": source_reported,
            "subtitle_languages": _string_list(
                subtitle_languages,
                limit=12,
                item_limit=24,
            ),
        }
        display_title = title
        media_type = None
        tmdb_id = None
        if media is not None:
            display_title = media.title
            media_type = media.media_type
            tmdb_id = int(media.tmdb_id)
            metadata.update(
                {
                    "poster_url": media.poster_url,
                    "backdrop_url": media.backdrop_url,
                    "year": media.year,
                    "overview": media.overview,
                    "rating": media.rating,
                }
            )
            if details is not None:
                metadata.update(
                    {
                        "genres": list(details.genres)[:12],
                        "studios": list(details.studios)[:16],
                        "franchises": list(details.franchises)[:8],
                        "people": [
                            str(row.get("name") or "")[:100]
                            for row in list(details.cast)[:20]
                            if isinstance(row, Mapping)
                            and str(row.get("name") or "").strip()
                        ],
                        "directors": list(details.directors)[:8],
                        "creators": list(details.creators)[:8],
                    }
                )
        return {
            "user_id": uid,
            "guild_id": gid,
            "source_id": _clean(source_id, 100),
            "discovery_key": _private_discovery_key(title),
            "title": display_title[:180],
            "media_type": media_type,
            "tmdb_id": tmdb_id,
            "metadata": metadata,
            "playable": True,
            "last_seen_at": utc_now(),
        }

    payloads = await asyncio.gather(
        *(one(variant) for variant in list(variants)[:24])
    )
    if not payloads:
        return []

    def write(client: Any):
        try:
            return (
                client.table(PRIVATE_DISCOVERY_TABLE)
                .upsert(
                    payloads,
                    on_conflict="user_id,guild_id,source_id,discovery_key",
                    ignore_duplicates=False,
                )
                .execute()
            )
        except TypeError:
            return client.table(PRIVATE_DISCOVERY_TABLE).upsert(payloads).execute()

    await execute(f"write private Cinema discoveries {gid}:{uid}", write)
    return payloads


async def list_private_discoveries(
    guild_id: int,
    user_id: int,
    *,
    limit: int = 60,
) -> list[dict[str, Any]]:
    gid = int(guild_id)
    uid = int(user_id)

    def read(client: Any):
        return (
            client.table(PRIVATE_DISCOVERY_TABLE)
            .select("*")
            .eq("guild_id", gid)
            .eq("user_id", uid)
            .order("first_seen_at", desc=True)
            .limit(max(1, min(int(limit), 100)))
            .execute()
        )

    found = rows(await execute(f"read private Cinema discoveries {gid}:{uid}", read))
    output: list[dict[str, Any]] = []
    for row in found:
        metadata = (
            dict(row.get("metadata") or {})
            if isinstance(row.get("metadata"), Mapping)
            else {}
        )
        output.append(
            {
                "result_kind": "feed_discovery",
                "private": True,
                "source_id": _clean(row.get("source_id"), 100),
                "source_label": _clean(metadata.get("source_label") or "Private feed", 80),
                "category": _clean(metadata.get("category") or MEDIA_CATEGORY_CUSTOM, 40),
                "title": _clean(row.get("title"), 180),
                "release_title": _clean(metadata.get("release_title") or row.get("title"), 240),
                "media_type": row.get("media_type"),
                "tmdb_id": _safe_int(row.get("tmdb_id")) or None,
                "poster_url": str(metadata.get("poster_url") or ""),
                "backdrop_url": str(metadata.get("backdrop_url") or ""),
                "year": _safe_int(metadata.get("year")),
                "rating": _safe_float(metadata.get("rating")),
                "overview": _clean(metadata.get("overview"), 900),
                "playable": bool(row.get("playable", True)),
                "first_seen_at": str(row.get("first_seen_at") or ""),
                "last_seen_at": str(row.get("last_seen_at") or ""),
                "discovery_key": _clean(row.get("discovery_key"), 80),
                "seeds": max(0, _safe_int(metadata.get("seeds"))),
                "leechers": max(0, _safe_int(metadata.get("leechers"))),
                "peers": max(0, _safe_int(metadata.get("peers"))),
                "file_size": max(0, _safe_int(metadata.get("file_size"))),
                "genres": list(metadata.get("genres") or [])[:12],
                "studios": list(metadata.get("studios") or [])[:16],
                "franchises": list(metadata.get("franchises") or [])[:8],
                "subtitle_languages": list(metadata.get("subtitle_languages") or [])[:12],
                "people": list(metadata.get("people") or [])[:20],
                "directors": list(metadata.get("directors") or [])[:8],
                "creators": list(metadata.get("creators") or [])[:8],
            }
        )
    return output


async def refresh_private_source(
    guild_id: int,
    user_id: int,
    rule_id: str,
) -> dict[str, Any]:
    rule = await _get_rule(rule_id)
    if (
        rule is None
        or int(rule.get("guild_id") or 0) != int(guild_id)
        or int(rule.get("owner_user_id") or 0) != int(user_id)
        or rule.get("rule_type") != "private_source"
    ):
        raise LookupError("Private Feed Rule not found.")
    filters = dict(rule.get("filters") or {})
    endpoint = str(filters.get("endpoint_url") or "")
    provider_type = str(filters.get("provider_type") or PROVIDER_TYPE_FEED)
    category = str(filters.get("category") or MEDIA_CATEGORY_CUSTOM)
    source_id = f"private-{str(rule.get('id') or '')[:36]}"
    source = CustomMediaSource(
        source_id=source_id[:48],
        label=str(rule.get("name") or "My Private Feed")[:80],
        endpoint_url=endpoint,
        provider_type=provider_type,
        enabled=True,
        category=category,
        added_by=int(user_id),
        created_at=str(rule.get("created_at") or ""),
    )
    query = "" if provider_type == PROVIDER_TYPE_FEED else str(rule.get("query") or "movie")
    outcome = await preview_custom_media_source(source, query=query, limit=24)
    error = str(outcome.errors[0]) if outcome.errors else ""
    variants = list(outcome.variants)
    await record_source_health(
        int(guild_id),
        source_id,
        ok=not bool(error),
        result_count=len(variants),
        error=error,
    )
    if error and not variants:
        raise CinemaFeedRuleError(error)
    recorded = await record_private_discoveries(
        int(guild_id),
        int(user_id),
        source_id=source_id,
        source_label=source.label,
        category=category,
        variants=variants,
    )
    if recorded:
        try:
            private_results = [
                row
                for row in await list_private_discoveries(
                    int(guild_id),
                    int(user_id),
                    limit=60,
                )
                if str(row.get("source_id") or "") == source_id
            ]
            await process_feed_notifications(
                int(guild_id),
                private_results,
                target_user_id=int(user_id),
            )
        except Exception:
            pass
    return {
        "rule": rule,
        "result_count": len(recorded),
        "error": error,
    }


async def process_feed_notifications(
    guild_id: int,
    discoveries: Sequence[Mapping[str, Any]],
    *,
    target_user_id: int = 0,
) -> int:
    """Create bounded Cinema inbox alerts for watchlist/rule matches.

    This deliberately writes to the existing Cinema inbox instead of sending
    unsolicited DMs. Daily mode collapses matches into one per-day inbox card.
    """

    gid = int(guild_id)
    groups = group_feed_results(discoveries)
    if not groups:
        return 0

    def read_rules(client: Any):
        return (
            client.table(RULE_TABLE)
            .select("*")
            .eq("guild_id", gid)
            .eq("scope", "user")
            .eq("enabled", True)
            .limit(500)
            .execute()
        )

    rule_rows = rows(await execute(f"read notification Feed Rules {gid}", read_rules))
    rules = [_normalize_rule(row) for row in rule_rows]
    target_uid = max(0, int(target_user_id or 0))
    if target_uid:
        rules = [
            rule for rule in rules
            if int(rule.get("owner_user_id") or 0) == target_uid
        ]
    eligible_users = {
        int(rule.get("owner_user_id") or 0)
        for rule in rules
        if int(rule.get("owner_user_id") or 0) > 0
    }
    matching_rules = [
        rule for rule in rules
        if rule.get("rule_type") != "private_source"
    ]

    matched: dict[int, list[tuple[dict[str, Any], str, str]]] = {}
    for group in groups:
        for rule in matching_rules:
            if not _rule_matches(group, rule):
                continue
            uid = int(rule.get("owner_user_id") or 0)
            if uid <= 0:
                continue
            notify_mode = str((rule.get("actions") or {}).get("notify") or "instant")
            if notify_mode == "off":
                continue
            matched.setdefault(uid, []).append((dict(group), str(rule.get("name") or "Feed Rule"), notify_mode))

    # Watchlist matching uses canonical identities and requires no explicit rule.
    for group in groups:
        media_type = str(group.get("media_type") or "")
        tmdb_id = int(group.get("tmdb_id") or 0)
        if media_type not in _MEDIA_TYPES or tmdb_id <= 0:
            continue

        def read_watchlist(client: Any):
            return (
                client.table(MEDIA_TABLE)
                .select("user_id")
                .eq("media_type", media_type)
                .eq("tmdb_id", tmdb_id)
                .eq("watchlisted", True)
                .limit(500)
                .execute()
            )

        try:
            watched_rows = rows(
                await execute(
                    f"read watchlist feed matches {gid}:{media_type}:{tmdb_id}",
                    read_watchlist,
                )
            )
        except CinemaStorageUnavailable:
            watched_rows = []
        for row in watched_rows:
            uid = int(row.get("user_id") or 0)
            if target_uid and uid != target_uid:
                continue
            if uid <= 0 or uid not in eligible_users:
                continue
            matched.setdefault(uid, []).append((dict(group), "Watchlist", "instant"))

    created = 0
    today = datetime.now(timezone.utc).date().isoformat()
    for uid, matches in list(matched.items())[:100]:
        try:
            profile = await get_cinema_user(uid)
            preferences = (
                dict(profile.get("preferences") or {})
                if isinstance(profile.get("preferences"), Mapping)
                else {}
            )
        except Exception:
            preferences = {}
        preferred_mode = str(
            preferences.get("feed_notification_mode") or "instant"
        ).casefold()
        if preferred_mode not in _NOTIFY_MODES:
            preferred_mode = "instant"
        if preferred_mode == "off":
            continue

        # De-duplicate same title matched by multiple rules while retaining
        # their requested notification modes.
        unique: dict[str, tuple[dict[str, Any], set[str], set[str]]] = {}
        for group, reason, rule_mode in matches:
            key = str(group.get("group_key") or "")
            if not key:
                continue
            current = unique.setdefault(key, (group, set(), set()))
            current[1].add(reason)
            current[2].add(str(rule_mode or "instant"))

        effective_mode = preferred_mode
        if preferred_mode == "instant" and unique and all(
            modes and modes <= {"daily"}
            for _group, _reasons, modes in unique.values()
        ):
            effective_mode = "daily"

        if effective_mode == "daily":
            sample = next(iter(unique.values()), None)
            if sample is None:
                continue
            group, reasons, _modes = sample
            count = len(unique)
            await create_notification(
                uid,
                guild_id=gid,
                kind="feed_digest",
                title=f"{count} new Cinema feed match{'es' if count != 1 else ''} today",
                body=(
                    f"Latest: {str(group.get('title') or 'New release')}. "
                    f"Matched {', '.join(sorted(reasons))[:180]}."
                ),
                action={"kind": "feeds", "filter": "my-feed"},
                dedupe_key=f"feed-digest:{gid}:{today}",
            )
            created += 1
            continue

        for key, (group, reasons, _modes) in list(unique.items())[:12]:
            best = (
                dict(group.get("best_release") or {})
                if isinstance(group.get("best_release"), Mapping)
                else {}
            )
            quality = str(best.get("resolution") or "")
            episode = ""
            if group.get("new_episode"):
                episode = (
                    f" S{int(group.get('season_number') or 0):02d}"
                    f"E{int(group.get('episode_number') or 0):02d}"
                )
            upgrade = " • quality upgrade available" if group.get("upgrade_available") else ""
            await create_notification(
                uid,
                guild_id=gid,
                kind="feed_match",
                title=(
                    f"New episode available: {str(group.get('title') or 'Cinema release')}{episode}"
                    if group.get("new_episode")
                    else f"New feed match: {str(group.get('title') or 'Cinema release')}"
                ),
                body=(
                    f"Matched {', '.join(sorted(reasons))[:180]}"
                    f"{f' • {quality}' if quality else ''}{upgrade}"
                ),
                action={
                    "kind": "feed_result",
                    "media_type": group.get("media_type"),
                    "tmdb_id": group.get("tmdb_id"),
                    "group_key": key,
                },
                dedupe_key=f"feed-match:{gid}:{uid}:{key}:{quality or 'any'}",
            )
            created += 1
            if created >= 80:
                return created
    return created


__all__ = [
    "CinemaFeedRuleError",
    "build_personalized_feed",
    "delete_feed_rule",
    "ensure_source_health",
    "group_feed_results",
    "list_feed_rules",
    "list_due_source_targets",
    "list_private_discoveries",
    "list_source_health",
    "process_feed_notifications",
    "record_source_health",
    "refresh_private_source",
    "save_feed_rule",
    "source_trust_payload",
]
