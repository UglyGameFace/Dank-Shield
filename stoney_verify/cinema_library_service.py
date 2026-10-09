from __future__ import annotations

"""Durable per-user Dank Cinema library/progress/profile service.

This module owns Cinema persistence. UI and Discord surfaces call it; they do not
write Supabase directly. Missing storage fails safely instead of silently
pretending progress/watchlist actions succeeded.
"""

import asyncio
import time
from typing import Any, Mapping, Optional

from .cinema_storage import (
    CinemaStorageUnavailable,
    execute as _execute,
    rows as _rows,
    utc_now as _now,
)

USER_TABLE = "dank_cinema_users"
MEDIA_TABLE = "dank_cinema_user_media"
NOTIFICATION_TABLE = "dank_cinema_notifications"
SESSION_TABLE = "dank_cinema_watch_sessions"
LIST_TABLE = "dank_cinema_lists"
LIST_ITEM_TABLE = "dank_cinema_list_items"

DEFAULT_PREFERENCES: dict[str, Any] = {
    "autoplay_next": True,
    "playback_speed": 1.0,
    "preferred_source": "",
    "default_audio_language": "",
    # Scoped by the authenticated room's guild, not a user-submitted guild ID.
    "audio_language_by_guild": {},
    "default_subtitle_language": "",
    "visual_quality": "auto",
    "feed_notification_mode": "instant",
    "feed_playable_only": True,
    "feed_min_seeds": 0,
    "feed_preferred_resolutions": [],
    "feed_preferred_codecs": [],
    "feed_preferred_languages": [],
    "feed_queue_suggestions": True,
}

_CACHE_TTL = 20.0
_USER_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
_MEDIA_CACHE: dict[int, tuple[float, list[dict[str, Any]]]] = {}
_LOCKS: dict[int, asyncio.Lock] = {}
_SESSION_WRITE_INTERVAL = 30.0
_SESSION_WRITE_CACHE: dict[
    tuple[int, str],
    tuple[float, float, bool, float, str, Optional[str]],
] = {}


class InvalidCinemaState(ValueError):
    """Raised when a Cinema library operation receives invalid media state."""


def _normalize_preferences(value: Any) -> dict[str, Any]:
    raw = dict(value) if isinstance(value, Mapping) else {}
    result = dict(DEFAULT_PREFERENCES)
    result["autoplay_next"] = bool(raw.get("autoplay_next", result["autoplay_next"]))

    quality = str(raw.get("visual_quality") or "auto").strip().lower()
    result["visual_quality"] = (
        quality if quality in {"auto", "high", "standard", "lite"} else "auto"
    )

    try:
        speed = float(raw.get("playback_speed", 1.0))
    except Exception:
        speed = 1.0
    allowed = (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0)
    result["playback_speed"] = min(allowed, key=lambda item: abs(item - speed))

    result["preferred_source"] = " ".join(
        str(raw.get("preferred_source") or "").split()
    )[:80]
    result["default_audio_language"] = " ".join(
        str(raw.get("default_audio_language") or "").split()
    )[:40]
    scoped = raw.get("audio_language_by_guild")
    safe_scoped: dict[str, str] = {}
    if isinstance(scoped, Mapping):
        for key, value in list(scoped.items())[:100]:
            guild_key = str(key).strip()
            language = str(value or "").strip().lower()
            if (
                guild_key.isascii() and guild_key.isdigit()
                and 0 < int(guild_key) < 2**64
                and language.isascii() and 2 <= len(language) <= 24
                and language.replace("-", "").isalnum()
            ):
                safe_scoped[guild_key] = language
    result["audio_language_by_guild"] = safe_scoped
    result["default_subtitle_language"] = " ".join(
        str(raw.get("default_subtitle_language") or "").split()
    )[:40]

    notification_mode = str(
        raw.get("feed_notification_mode") or "instant"
    ).strip().lower()
    result["feed_notification_mode"] = (
        notification_mode
        if notification_mode in {"off", "instant", "daily"}
        else "instant"
    )
    result["feed_playable_only"] = bool(
        raw.get("feed_playable_only", True)
    )
    try:
        result["feed_min_seeds"] = max(
            0,
            min(int(raw.get("feed_min_seeds") or 0), 100000),
        )
    except Exception:
        result["feed_min_seeds"] = 0

    def clean_list(key: str, *, limit: int = 10, item_limit: int = 24) -> list[str]:
        value = raw.get(key)
        if not isinstance(value, (list, tuple, set)):
            return []
        output: list[str] = []
        seen: set[str] = set()
        for item in value:
            clean = " ".join(str(item or "").split()).lower()[:item_limit]
            if not clean or clean in seen:
                continue
            seen.add(clean)
            output.append(clean)
            if len(output) >= limit:
                break
        return output

    result["feed_preferred_resolutions"] = clean_list(
        "feed_preferred_resolutions",
        limit=8,
        item_limit=16,
    )
    result["feed_preferred_codecs"] = clean_list(
        "feed_preferred_codecs",
        limit=8,
        item_limit=24,
    )
    result["feed_preferred_languages"] = clean_list(
        "feed_preferred_languages",
        limit=12,
        item_limit=24,
    )
    result["feed_queue_suggestions"] = bool(
        raw.get("feed_queue_suggestions", True)
    )
    return result


def _cache_user(user_id: int, payload: Mapping[str, Any]) -> None:
    _USER_CACHE[int(user_id)] = (time.monotonic(), dict(payload))


def _cache_media(user_id: int, rows: list[dict[str, Any]]) -> None:
    _MEDIA_CACHE[int(user_id)] = (time.monotonic(), [dict(row) for row in rows])


def invalidate_cinema_user_cache(user_id: int) -> None:
    uid = int(user_id)
    _USER_CACHE.pop(uid, None)
    _MEDIA_CACHE.pop(uid, None)


def _cached(
    cache: dict[int, tuple[float, Any]],
    user_id: int,
) -> Any:
    found = cache.get(int(user_id))
    if not found:
        return None
    ts, value = found
    if time.monotonic() - ts > _CACHE_TTL:
        cache.pop(int(user_id), None)
        return None
    if isinstance(value, list):
        return [dict(row) for row in value]
    return dict(value)


async def get_cinema_user(user_id: int, *, refresh: bool = False) -> dict[str, Any]:
    uid = int(user_id)
    if uid <= 0:
        raise InvalidCinemaState("Invalid Cinema user.")
    if not refresh:
        cached = _cached(_USER_CACHE, uid)
        if cached is not None:
            return cached

    def read(client: Any):
        return (
            client.table(USER_TABLE)
            .select("*")
            .eq("user_id", uid)
            .limit(1)
            .execute()
        )

    rows = _rows(await _execute(f"read Cinema user {uid}", read))
    payload = {
        "user_id": uid,
        "preferences": dict(DEFAULT_PREFERENCES),
    }
    if rows:
        payload.update(rows[0])
    payload["preferences"] = _normalize_preferences(payload.get("preferences"))
    _cache_user(uid, payload)
    return dict(payload)


async def update_cinema_preferences(
    user_id: int,
    updates: Mapping[str, Any],
    *,
    guild_id: int | None = None,
) -> dict[str, Any]:
    uid = int(user_id)
    lock = _LOCKS.setdefault(uid, asyncio.Lock())
    async with lock:
        current = await get_cinema_user(uid, refresh=True)
        merged = dict(current.get("preferences") or {})
        for key in DEFAULT_PREFERENCES:
            if key != "audio_language_by_guild" and key in updates:
                merged[key] = updates.get(key)
        if "guild_audio_language" in updates:
            guild = int(guild_id or 0)
            if guild <= 0:
                raise InvalidCinemaState("A guild-linked Cinema session is required.")
            language = str(updates.get("guild_audio_language") or "").strip().lower()
            if language and (
                not language.isascii() or len(language) > 24
                or len(language) < 2 or not language.replace("-", "").isalnum()
            ):
                raise InvalidCinemaState("Choose a valid Cinema audio language.")
            per_guild = dict(merged.get("audio_language_by_guild") or {})
            if language:
                per_guild[str(guild)] = language
            else:
                per_guild.pop(str(guild), None)
            merged["audio_language_by_guild"] = per_guild
        preferences = _normalize_preferences(merged)
        payload = {
            "user_id": uid,
            "preferences": preferences,
            "updated_at": _now(),
        }

        def write(client: Any):
            try:
                return (
                    client.table(USER_TABLE)
                    .upsert(payload, on_conflict="user_id")
                    .execute()
                )
            except TypeError:
                return client.table(USER_TABLE).upsert(payload).execute()

        await _execute(f"write Cinema preferences {uid}", write)
        invalidate_cinema_user_cache(uid)
        return await get_cinema_user(uid, refresh=True)


def _normalize_media_type(value: Any) -> str:
    kind = str(value or "").strip().lower()
    if kind not in {"movie", "tv", "episode"}:
        raise InvalidCinemaState("Unsupported Cinema media type.")
    return kind


def _media_key_payload(
    *,
    user_id: int,
    media_type: str,
    tmdb_id: int,
    season_number: int = 0,
    episode_number: int = 0,
) -> dict[str, Any]:
    uid = int(user_id)
    kind = _normalize_media_type(media_type)
    tmdb = int(tmdb_id)
    season = max(0, int(season_number or 0))
    episode = max(0, int(episode_number or 0))
    if uid <= 0 or tmdb <= 0:
        raise InvalidCinemaState("Invalid Cinema media identity.")
    if kind != "episode":
        season = 0
        episode = 0
    return {
        "user_id": uid,
        "media_type": kind,
        "tmdb_id": tmdb,
        "season_number": season,
        "episode_number": episode,
    }


async def list_user_media(user_id: int, *, refresh: bool = False) -> list[dict[str, Any]]:
    uid = int(user_id)
    if not refresh:
        cached = _cached(_MEDIA_CACHE, uid)
        if cached is not None:
            return cached

    def read(client: Any):
        return (
            client.table(MEDIA_TABLE)
            .select("*")
            .eq("user_id", uid)
            .order("updated_at", desc=True)
            .limit(500)
            .execute()
        )

    rows = _rows(await _execute(f"read Cinema library {uid}", read))
    _cache_media(uid, rows)
    return [dict(row) for row in rows]


async def get_media_state(
    user_id: int,
    *,
    media_type: str,
    tmdb_id: int,
    season_number: int = 0,
    episode_number: int = 0,
    refresh: bool = False,
) -> Optional[dict[str, Any]]:
    """Return one canonical per-user media row without bypassing service caching."""

    key = _media_key_payload(
        user_id=user_id,
        media_type=media_type,
        tmdb_id=tmdb_id,
        season_number=season_number,
        episode_number=episode_number,
    )
    rows = await list_user_media(int(user_id), refresh=refresh)
    for row in rows:
        if (
            str(row.get("media_type") or "") == key["media_type"]
            and int(row.get("tmdb_id") or 0) == key["tmdb_id"]
            and int(row.get("season_number") or 0) == key["season_number"]
            and int(row.get("episode_number") or 0) == key["episode_number"]
        ):
            return dict(row)
    return None


async def _read_media_row(
    *,
    user_id: int,
    media_type: str,
    tmdb_id: int,
    season_number: int = 0,
    episode_number: int = 0,
) -> Optional[dict[str, Any]]:
    key = _media_key_payload(
        user_id=user_id,
        media_type=media_type,
        tmdb_id=tmdb_id,
        season_number=season_number,
        episode_number=episode_number,
    )

    def read(client: Any):
        return (
            client.table(MEDIA_TABLE)
            .select("*")
            .eq("user_id", key["user_id"])
            .eq("media_type", key["media_type"])
            .eq("tmdb_id", key["tmdb_id"])
            .eq("season_number", key["season_number"])
            .eq("episode_number", key["episode_number"])
            .limit(1)
            .execute()
        )

    found = _rows(await _execute(
        f"read Cinema media state {key['user_id']}:{key['media_type']}:{key['tmdb_id']}",
        read,
    ))
    return dict(found[0]) if found else None


async def _write_media_patch(
    user_id: int,
    *,
    media_type: str,
    tmdb_id: int,
    season_number: int = 0,
    episode_number: int = 0,
    patch: Mapping[str, Any],
) -> dict[str, Any]:
    key = _media_key_payload(
        user_id=user_id,
        media_type=media_type,
        tmdb_id=tmdb_id,
        season_number=season_number,
        episode_number=episode_number,
    )
    payload = {**key, **dict(patch), "updated_at": _now()}

    def write(client: Any):
        try:
            return (
                client.table(MEDIA_TABLE)
                .upsert(
                    payload,
                    on_conflict=(
                        "user_id,media_type,tmdb_id,season_number,episode_number"
                    ),
                )
                .execute()
            )
        except TypeError:
            return client.table(MEDIA_TABLE).upsert(payload).execute()

    response = await _execute(f"write Cinema media patch {user_id}", write)
    invalidate_cinema_user_cache(int(user_id))
    stored = _rows(response)
    return dict(stored[0]) if stored else payload


async def set_favorite(
    user_id: int,
    *,
    media_type: str,
    tmdb_id: int,
    title: str,
    metadata: Optional[Mapping[str, Any]] = None,
    season_number: int = 0,
    episode_number: int = 0,
    enabled: bool = True,
) -> dict[str, Any]:
    return await _write_media_patch(
        int(user_id),
        media_type=media_type,
        tmdb_id=tmdb_id,
        season_number=season_number,
        episode_number=episode_number,
        patch={
            "title": " ".join(str(title or "").split())[:180],
            "metadata": dict(metadata or {}),
            "favorite": bool(enabled),
            "favorite_at": _now() if enabled else None,
        },
    )


async def set_rating(
    user_id: int,
    *,
    media_type: str,
    tmdb_id: int,
    title: str,
    rating: Optional[int],
    metadata: Optional[Mapping[str, Any]] = None,
    season_number: int = 0,
    episode_number: int = 0,
) -> dict[str, Any]:
    numeric: Optional[int]
    if rating is None or int(rating or 0) <= 0:
        numeric = None
    else:
        numeric = int(rating)
        if numeric < 1 or numeric > 10:
            raise InvalidCinemaState("Cinema ratings must be between 1 and 10.")
    return await _write_media_patch(
        int(user_id),
        media_type=media_type,
        tmdb_id=tmdb_id,
        season_number=season_number,
        episode_number=episode_number,
        patch={
            "title": " ".join(str(title or "").split())[:180],
            "metadata": dict(metadata or {}),
            "rating": numeric,
            "rated_at": _now() if numeric is not None else None,
        },
    )


async def mark_watched(
    user_id: int,
    *,
    media_type: str,
    tmdb_id: int,
    title: str,
    metadata: Optional[Mapping[str, Any]] = None,
    season_number: int = 0,
    episode_number: int = 0,
    watched: bool = True,
) -> dict[str, Any]:
    key = _media_key_payload(
        user_id=user_id,
        media_type=media_type,
        tmdb_id=tmdb_id,
        season_number=season_number,
        episode_number=episode_number,
    )
    existing = await _read_media_row(**key)
    now = _now()
    patch: dict[str, Any] = {
        "title": " ".join(str(title or "").split())[:180],
        "metadata": dict(metadata or (existing or {}).get("metadata") or {}),
        "progress_seconds": 0.0,
        "completed": bool(watched),
        "last_watched_at": now if watched else (existing or {}).get("last_watched_at"),
    }
    if watched:
        already_completed = bool((existing or {}).get("completed"))
        previous_count = max(0, int((existing or {}).get("play_count") or 0))
        patch.update(
            {
                "play_count": previous_count if already_completed else previous_count + 1,
                "first_watched_at": (
                    (existing or {}).get("first_watched_at") or now
                ),
                "last_completed_at": (
                    (existing or {}).get("last_completed_at")
                    if already_completed
                    else now
                ),
            }
        )
    else:
        patch.update(
            {
                "play_count": 0,
                "first_watched_at": None,
                "last_completed_at": None,
            }
        )
    return await _write_media_patch(
        int(user_id),
        media_type=key["media_type"],
        tmdb_id=key["tmdb_id"],
        season_number=key["season_number"],
        episode_number=key["episode_number"],
        patch=patch,
    )


def _session_key(value: Any) -> str:
    return " ".join(str(value or "").split())[:180]


async def record_watch_session(
    user_id: int,
    *,
    media_type: str,
    tmdb_id: int,
    title: str,
    progress_seconds: float,
    duration_seconds: float,
    completed: bool,
    season_number: int = 0,
    episode_number: int = 0,
    metadata: Optional[Mapping[str, Any]] = None,
    context: Optional[Mapping[str, Any]] = None,
) -> None:
    ctx = dict(context or {})
    session_key = _session_key(ctx.get("session_key"))
    if not session_key:
        return
    uid = int(user_id)
    progress = max(0.0, float(progress_seconds or 0.0))
    duration = max(0.0, float(duration_seconds or 0.0))
    cache_key = (uid, session_key)
    previous_cache = _SESSION_WRITE_CACHE.get(cache_key)
    now_mono = time.monotonic()
    if previous_cache is not None:
        last_write, last_progress, _last_completed, _duration, _started_at, _completed_at = previous_cache
        if (
            not completed
            and now_mono - last_write < _SESSION_WRITE_INTERVAL
            and abs(progress - last_progress) < 60.0
        ):
            return

    key = _media_key_payload(
        user_id=uid,
        media_type=media_type,
        tmdb_id=tmdb_id,
        season_number=season_number,
        episode_number=episode_number,
    )

    if previous_cache is None:
        def read(client: Any):
            return (
                client.table(SESSION_TABLE)
                .select("*")
                .eq("user_id", uid)
                .eq("session_key", session_key)
                .limit(1)
                .execute()
            )

        found = _rows(await _execute(f"read Cinema watch session {uid}", read))
        existing = dict(found[0]) if found else {}
    else:
        (
            _last_write,
            cached_progress,
            cached_completed,
            cached_duration,
            cached_started_at,
            cached_completed_at,
        ) = previous_cache
        existing = {
            "max_progress_seconds": cached_progress,
            "completed": cached_completed,
            "duration_seconds": cached_duration,
            "started_at": cached_started_at,
            "completed_at": cached_completed_at,
        }
    now = _now()
    payload = {
        "user_id": uid,
        "session_key": session_key,
        "guild_id": int(ctx.get("guild_id") or 0) or None,
        "room_id": _session_key(ctx.get("room_id"))[:120],
        "session_mode": (
            str(ctx.get("session_mode") or "private")
            if str(ctx.get("session_mode") or "private")
            in {"private", "watch_party", "standalone"}
            else "private"
        ),
        "candidate_id": _session_key(ctx.get("candidate_id"))[:120],
        "media_type": key["media_type"],
        "tmdb_id": key["tmdb_id"],
        "series_id": int((metadata or {}).get("series_id") or 0) or None,
        "season_number": key["season_number"],
        "episode_number": key["episode_number"],
        "title": " ".join(str(title or "").split())[:180],
        "metadata": dict(metadata or {}),
        "is_host": bool(ctx.get("is_host", False)),
        "max_progress_seconds": max(
            progress,
            float(existing.get("max_progress_seconds") or 0.0),
        ),
        "duration_seconds": max(
            duration,
            float(existing.get("duration_seconds") or 0.0),
        ),
        "completed": bool(existing.get("completed")) or bool(completed),
        "started_at": existing.get("started_at") or now,
        "last_seen_at": now,
        "completed_at": (
            existing.get("completed_at")
            or (now if completed else None)
        ),
    }

    def write(client: Any):
        try:
            return (
                client.table(SESSION_TABLE)
                .upsert(payload, on_conflict="user_id,session_key")
                .execute()
            )
        except TypeError:
            return client.table(SESSION_TABLE).upsert(payload).execute()

    await _execute(f"write Cinema watch session {uid}", write)
    _SESSION_WRITE_CACHE[cache_key] = (
        now_mono,
        float(payload["max_progress_seconds"]),
        bool(payload["completed"]),
        float(payload["duration_seconds"]),
        str(payload["started_at"] or now),
        str(payload["completed_at"]) if payload.get("completed_at") else None,
    )
    if len(_SESSION_WRITE_CACHE) > 2048:
        oldest = sorted(
            _SESSION_WRITE_CACHE.items(),
            key=lambda item: item[1][0],
        )[:256]
        for stale_key, _value in oldest:
            _SESSION_WRITE_CACHE.pop(stale_key, None)


async def list_watch_sessions(
    user_id: int,
    *,
    limit: int = 200,
) -> list[dict[str, Any]]:
    uid = int(user_id)

    def read(client: Any):
        return (
            client.table(SESSION_TABLE)
            .select("*")
            .eq("user_id", uid)
            .order("last_seen_at", desc=True)
            .limit(max(1, min(int(limit), 1000)))
            .execute()
        )

    return _rows(await _execute(f"read Cinema watch sessions {uid}", read))


async def list_custom_lists(user_id: int) -> list[dict[str, Any]]:
    uid = int(user_id)

    def read_lists(client: Any):
        return (
            client.table(LIST_TABLE)
            .select("*")
            .eq("user_id", uid)
            .order("position")
            .order("created_at")
            .limit(100)
            .execute()
        )

    def read_items(client: Any):
        return (
            client.table(LIST_ITEM_TABLE)
            .select("*")
            .eq("user_id", uid)
            .order("position")
            .order("added_at")
            .limit(1000)
            .execute()
        )

    list_response, item_response = await asyncio.gather(
        _execute(f"read Cinema lists {uid}", read_lists),
        _execute(f"read Cinema list items {uid}", read_items),
    )
    list_rows = [dict(row) for row in _rows(list_response)]
    items_by_list: dict[str, list[dict[str, Any]]] = {}
    for raw in _rows(item_response):
        row = dict(raw)
        items_by_list.setdefault(str(row.get("list_id") or ""), []).append(row)
    for row in list_rows:
        row["items"] = items_by_list.get(str(row.get("id") or ""), [])
    return list_rows


async def save_custom_list(
    user_id: int,
    *,
    name: str,
    description: str = "",
    list_id: str = "",
    position: int = 0,
) -> dict[str, Any]:
    uid = int(user_id)
    clean_name = " ".join(str(name or "").split())[:80]
    if not clean_name:
        raise InvalidCinemaState("Cinema list name is required.")
    clean_id = str(list_id or "").strip()
    payload = {
        "user_id": uid,
        "name": clean_name,
        "description": " ".join(str(description or "").split())[:300],
        "position": max(0, int(position or 0)),
        "updated_at": _now(),
    }

    if clean_id:
        def update(client: Any):
            return (
                client.table(LIST_TABLE)
                .update(payload)
                .eq("id", clean_id)
                .eq("user_id", uid)
                .execute()
            )
        response = await _execute(f"update Cinema list {uid}", update)
        rows = _rows(response)
        if not rows:
            raise InvalidCinemaState("Cinema list not found.")
        return dict(rows[0])

    payload["created_at"] = _now()

    def insert(client: Any):
        return client.table(LIST_TABLE).insert(payload).execute()

    rows = _rows(await _execute(f"create Cinema list {uid}", insert))
    return dict(rows[0]) if rows else payload


async def delete_custom_list(user_id: int, list_id: str) -> None:
    uid = int(user_id)
    clean_id = str(list_id or "").strip()
    if not clean_id:
        raise InvalidCinemaState("Cinema list id is required.")

    def remove(client: Any):
        return (
            client.table(LIST_TABLE)
            .delete()
            .eq("id", clean_id)
            .eq("user_id", uid)
            .execute()
        )

    await _execute(f"delete Cinema list {uid}", remove)


async def set_custom_list_item(
    user_id: int,
    *,
    list_id: str,
    media_type: str,
    tmdb_id: int,
    title: str,
    metadata: Optional[Mapping[str, Any]] = None,
    season_number: int = 0,
    episode_number: int = 0,
    enabled: bool = True,
    position: int = 0,
) -> Optional[dict[str, Any]]:
    uid = int(user_id)
    clean_id = str(list_id or "").strip()
    if not clean_id:
        raise InvalidCinemaState("Choose a Cinema list.")

    def verify(client: Any):
        return (
            client.table(LIST_TABLE)
            .select("id")
            .eq("id", clean_id)
            .eq("user_id", uid)
            .limit(1)
            .execute()
        )

    if not _rows(await _execute(f"verify Cinema list {uid}", verify)):
        raise InvalidCinemaState("Cinema list not found.")

    key = _media_key_payload(
        user_id=uid,
        media_type=media_type,
        tmdb_id=tmdb_id,
        season_number=season_number,
        episode_number=episode_number,
    )
    if not enabled:
        def remove(client: Any):
            return (
                client.table(LIST_ITEM_TABLE)
                .delete()
                .eq("list_id", clean_id)
                .eq("user_id", uid)
                .eq("media_type", key["media_type"])
                .eq("tmdb_id", key["tmdb_id"])
                .eq("season_number", key["season_number"])
                .eq("episode_number", key["episode_number"])
                .execute()
            )
        await _execute(f"remove Cinema list item {uid}", remove)
        return None

    payload = {
        "list_id": clean_id,
        "user_id": uid,
        "media_type": key["media_type"],
        "tmdb_id": key["tmdb_id"],
        "season_number": key["season_number"],
        "episode_number": key["episode_number"],
        "title": " ".join(str(title or "").split())[:180],
        "metadata": dict(metadata or {}),
        "position": max(0, int(position or 0)),
        "added_at": _now(),
    }

    def write(client: Any):
        try:
            return (
                client.table(LIST_ITEM_TABLE)
                .upsert(
                    payload,
                    on_conflict=(
                        "list_id,media_type,tmdb_id,season_number,episode_number"
                    ),
                )
                .execute()
            )
        except TypeError:
            return client.table(LIST_ITEM_TABLE).upsert(payload).execute()

    rows = _rows(await _execute(f"write Cinema list item {uid}", write))
    return dict(rows[0]) if rows else payload


async def library_stats(
    user_id: int,
    *,
    media_rows: Optional[list[dict[str, Any]]] = None,
    sessions: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    uid = int(user_id)
    if media_rows is None and sessions is None:
        media_rows, sessions = await asyncio.gather(
            list_user_media(uid),
            list_watch_sessions(uid, limit=1000),
        )
    elif media_rows is None:
        media_rows = await list_user_media(uid)
    elif sessions is None:
        sessions = await list_watch_sessions(uid, limit=1000)
    media_rows = list(media_rows or [])
    sessions = list(sessions or [])
    completed_movies = [
        row for row in media_rows
        if str(row.get("media_type") or "") == "movie"
        and int(row.get("play_count") or 0) > 0
    ]
    completed_episodes = [
        row for row in media_rows
        if str(row.get("media_type") or "") == "episode"
        and int(row.get("play_count") or 0) > 0
    ]
    play_count = sum(max(0, int(row.get("play_count") or 0)) for row in media_rows)
    distinct_completed = len(completed_movies) + len(completed_episodes)
    rewatches = max(0, play_count - distinct_completed)
    watched_seconds = 0.0
    for row in sessions:
        progress = max(0.0, float(row.get("max_progress_seconds") or 0.0))
        duration = max(0.0, float(row.get("duration_seconds") or 0.0))
        watched_seconds += min(progress, duration) if duration > 0 else progress

    ratings = [
        int(row.get("rating") or 0)
        for row in media_rows
        if int(row.get("rating") or 0) > 0
    ]
    genre_scores: dict[str, int] = {}
    for row in media_rows:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
        weight = max(
            1,
            int(row.get("play_count") or 0)
            + (2 if bool(row.get("favorite")) else 0)
            + (1 if int(row.get("rating") or 0) >= 8 else 0),
        )
        for genre in list(metadata.get("genres") or [])[:12]:
            clean = " ".join(str(genre or "").split())[:60]
            if clean:
                genre_scores[clean] = genre_scores.get(clean, 0) + weight

    return {
        "movies_watched": len(completed_movies),
        "episodes_watched": len(completed_episodes),
        "total_completions": play_count,
        "rewatches": rewatches,
        "watch_hours": round(watched_seconds / 3600.0, 1),
        "favorites": sum(1 for row in media_rows if bool(row.get("favorite"))),
        "ratings": len(ratings),
        "average_rating": round(sum(ratings) / len(ratings), 1) if ratings else 0.0,
        "watch_party_sessions": sum(
            1 for row in sessions if str(row.get("session_mode") or "") == "watch_party"
        ),
        "private_sessions": sum(
            1 for row in sessions if str(row.get("session_mode") or "") == "private"
        ),
        "standalone_sessions": sum(
            1 for row in sessions if str(row.get("session_mode") or "") == "standalone"
        ),
        "top_genres": [
            {"name": name, "score": score}
            for name, score in sorted(
                genre_scores.items(),
                key=lambda item: (-item[1], item[0].casefold()),
            )[:8]
        ],
    }


async def set_watchlist(
    user_id: int,
    *,
    media_type: str,
    tmdb_id: int,
    title: str,
    metadata: Optional[Mapping[str, Any]] = None,
    enabled: bool = True,
) -> dict[str, Any]:
    key = _media_key_payload(
        user_id=user_id,
        media_type=media_type,
        tmdb_id=tmdb_id,
    )
    if key["media_type"] == "episode":
        raise InvalidCinemaState("Add the TV series, not an episode, to Watchlist.")
    payload = {
        **key,
        "title": " ".join(str(title or "").split())[:180],
        "metadata": dict(metadata or {}),
        "watchlisted": bool(enabled),
        "watchlisted_at": _now() if enabled else None,
        "updated_at": _now(),
    }

    def write(client: Any):
        try:
            return (
                client.table(MEDIA_TABLE)
                .upsert(
                    payload,
                    on_conflict=(
                        "user_id,media_type,tmdb_id,season_number,episode_number"
                    ),
                )
                .execute()
            )
        except TypeError:
            return client.table(MEDIA_TABLE).upsert(payload).execute()

    await _execute(f"write Cinema watchlist {user_id}", write)
    invalidate_cinema_user_cache(int(user_id))
    return payload


async def record_progress(
    user_id: int,
    *,
    media_type: str,
    tmdb_id: int,
    title: str,
    progress_seconds: float,
    duration_seconds: float,
    season_number: int = 0,
    episode_number: int = 0,
    metadata: Optional[Mapping[str, Any]] = None,
    completed: Optional[bool] = None,
    activity_context: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    key = _media_key_payload(
        user_id=user_id,
        media_type=media_type,
        tmdb_id=tmdb_id,
        season_number=season_number,
        episode_number=episode_number,
    )
    progress = max(0.0, float(progress_seconds or 0.0))
    duration = max(0.0, float(duration_seconds or 0.0))
    if duration > 0:
        progress = min(progress, duration)
    resolved_completed = (
        bool(completed)
        if completed is not None
        else bool(duration > 0 and progress >= max(30.0, duration * 0.92))
    )
    now = _now()
    payload: dict[str, Any] = {
        **key,
        "title": " ".join(str(title or "").split())[:180],
        "metadata": dict(metadata or {}),
        "progress_seconds": 0.0 if resolved_completed else progress,
        "duration_seconds": duration,
        "completed": resolved_completed,
        "last_watched_at": now,
        "updated_at": now,
    }

    if resolved_completed:
        existing = await _read_media_row(**key)
        already_completed = bool((existing or {}).get("completed"))
        previous_count = max(0, int((existing or {}).get("play_count") or 0))
        payload.update(
            {
                "play_count": previous_count if already_completed else previous_count + 1,
                "first_watched_at": (
                    (existing or {}).get("first_watched_at") or now
                ),
                "last_completed_at": (
                    (existing or {}).get("last_completed_at")
                    if already_completed
                    else now
                ),
            }
        )

    def write(client: Any):
        try:
            return (
                client.table(MEDIA_TABLE)
                .upsert(
                    payload,
                    on_conflict=(
                        "user_id,media_type,tmdb_id,season_number,episode_number"
                    ),
                )
                .execute()
            )
        except TypeError:
            return client.table(MEDIA_TABLE).upsert(payload).execute()

    await _execute(f"write Cinema progress {user_id}", write)
    invalidate_cinema_user_cache(int(user_id))

    if activity_context:
        try:
            await record_watch_session(
                int(user_id),
                media_type=key["media_type"],
                tmdb_id=key["tmdb_id"],
                title=str(payload["title"]),
                progress_seconds=progress,
                duration_seconds=duration,
                completed=resolved_completed,
                season_number=key["season_number"],
                episode_number=key["episode_number"],
                metadata=payload["metadata"],
                context=activity_context,
            )
        except Exception:
            # Session analytics are secondary to durable resume/progress state.
            pass

    return payload

def _sort_iso(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: str(row.get(key) or ""),
        reverse=True,
    )


def library_snapshot_from_rows(
    rows: list[dict[str, Any]],
) -> dict[str, Any]:
    watched = [row for row in rows if row.get("last_watched_at")]
    watchlist = _sort_iso(
        [row for row in rows if bool(row.get("watchlisted"))],
        "watchlisted_at",
    )[:60]
    continue_watching = _sort_iso(
        [
            row
            for row in watched
            if not bool(row.get("completed"))
            and float(row.get("progress_seconds") or 0.0) > 0
        ],
        "last_watched_at",
    )[:30]
    recently_watched = _sort_iso(watched, "last_watched_at")[:30]
    watch_again = _sort_iso(
        [row for row in watched if bool(row.get("completed"))],
        "last_watched_at",
    )[:30]
    favorites = _sort_iso(
        [row for row in rows if bool(row.get("favorite"))],
        "favorite_at",
    )[:60]
    rated = _sort_iso(
        [row for row in rows if int(row.get("rating") or 0) > 0],
        "rated_at",
    )[:60]

    latest_episode_by_series: dict[int, dict[str, Any]] = {}
    for row in recently_watched:
        if str(row.get("media_type") or "") != "episode":
            continue
        metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
        series_id = int(metadata.get("series_id") or 0)
        if series_id > 0 and series_id not in latest_episode_by_series:
            latest_episode_by_series[series_id] = dict(row)

    return {
        "watchlist": watchlist,
        "continue_watching": continue_watching,
        "recently_watched": recently_watched,
        "watch_again": watch_again,
        "favorites": favorites,
        "rated": rated,
        "series_progress": list(latest_episode_by_series.values()),
    }


async def library_snapshot(user_id: int) -> dict[str, Any]:
    rows = await list_user_media(int(user_id))
    return library_snapshot_from_rows(rows)


async def create_notification(
    user_id: int,
    *,
    guild_id: Optional[int],
    kind: str,
    title: str,
    body: str = "",
    action: Optional[Mapping[str, Any]] = None,
    dedupe_key: str = "",
) -> dict[str, Any]:
    payload = {
        "user_id": int(user_id),
        "guild_id": int(guild_id) if guild_id else None,
        "kind": " ".join(str(kind or "info").split())[:40] or "info",
        "title": " ".join(str(title or "").split())[:160],
        "body": " ".join(str(body or "").split())[:500],
        "action": dict(action or {}),
        "dedupe_key": (
            " ".join(str(dedupe_key or "").split())[:180]
            if dedupe_key
            else None
        ),
        "created_at": _now(),
    }
    if not payload["title"]:
        raise InvalidCinemaState("Cinema notification title is required.")

    def write(client: Any):
        if payload["dedupe_key"]:
            try:
                return (
                    client.table(NOTIFICATION_TABLE)
                    .upsert(
                        payload,
                        on_conflict="user_id,dedupe_key",
                    )
                    .execute()
                )
            except TypeError:
                pass
        return client.table(NOTIFICATION_TABLE).insert(payload).execute()

    response = await _execute(f"create Cinema notification {user_id}", write)
    rows = _rows(response)
    return rows[0] if rows else payload


async def notify_watch_party_invite(
    user_id: int,
    *,
    guild_id: int,
    host_name: str,
    room_id: str,
) -> dict[str, Any]:
    clean_host = " ".join(str(host_name or "Cinema host").split())[:80]
    clean_room = " ".join(str(room_id or "").split())[:120]
    return await create_notification(
        int(user_id),
        guild_id=int(guild_id),
        kind="watch_party_invite",
        title="Watch Party invite",
        body=f"{clean_host} invited you to a live Dank Cinema Watch Party.",
        action={"kind": "room", "room_id": clean_room},
        dedupe_key=f"watch-party:{clean_room}",
    )


async def list_notifications(
    user_id: int,
    *,
    guild_id: Optional[int] = None,
    unread_only: bool = False,
    limit: int = 30,
) -> list[dict[str, Any]]:
    uid = int(user_id)

    def read(client: Any):
        query = (
            client.table(NOTIFICATION_TABLE)
            .select("*")
            .eq("user_id", uid)
            .order("created_at", desc=True)
            .limit(max(1, min(int(limit), 100)))
        )
        if guild_id:
            query = query.eq("guild_id", int(guild_id))
        if unread_only:
            query = query.is_("read_at", "null")
        return query.execute()

    return _rows(await _execute(f"read Cinema notifications {uid}", read))


async def mark_notification_read(user_id: int, notification_id: str) -> None:
    uid = int(user_id)
    clean_id = str(notification_id or "").strip()
    if not clean_id:
        raise InvalidCinemaState("Cinema notification id is required.")

    def write(client: Any):
        return (
            client.table(NOTIFICATION_TABLE)
            .update({"read_at": _now()})
            .eq("id", clean_id)
            .eq("user_id", uid)
            .execute()
        )

    await _execute(f"mark Cinema notification {uid}", write)


__all__ = [
    "CinemaStorageUnavailable",
    "DEFAULT_PREFERENCES",
    "InvalidCinemaState",
    "create_notification",
    "delete_custom_list",
    "get_cinema_user",
    "get_media_state",
    "invalidate_cinema_user_cache",
    "library_snapshot",
    "library_snapshot_from_rows",
    "library_stats",
    "list_custom_lists",
    "list_notifications",
    "list_user_media",
    "list_watch_sessions",
    "mark_notification_read",
    "mark_watched",
    "notify_watch_party_invite",
    "record_progress",
    "record_watch_session",
    "save_custom_list",
    "set_custom_list_item",
    "set_favorite",
    "set_rating",
    "set_watchlist",
    "update_cinema_preferences",
]
