from __future__ import annotations

"""Durable per-user Dank Cinema library/progress/profile service.

This module owns Cinema persistence. UI and Discord surfaces call it; they do not
write Supabase directly. Missing storage fails safely instead of silently
pretending progress/watchlist actions succeeded.
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional

from .globals import get_supabase, reset_supabase

USER_TABLE = "dank_cinema_users"
MEDIA_TABLE = "dank_cinema_user_media"
NOTIFICATION_TABLE = "dank_cinema_notifications"

_CACHE_TTL = 20.0
_DB_ATTEMPTS = 3
_USER_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
_MEDIA_CACHE: dict[int, tuple[float, list[dict[str, Any]]]] = {}
_LOCKS: dict[int, asyncio.Lock] = {}

DEFAULT_PREFERENCES: dict[str, Any] = {
    "autoplay_next": True,
    "visual_quality": "auto",
    "playback_speed": 1.0,
    "preferred_source": "",
    "default_audio_language": "",
    "default_subtitle_language": "",
}


class CinemaStorageUnavailable(RuntimeError):
    pass


class InvalidCinemaState(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rows(response: Any) -> list[dict[str, Any]]:
    raw = getattr(response, "data", None) or []
    return [dict(row) for row in raw if isinstance(row, Mapping)]


def _is_retryable(exc: Exception) -> bool:
    text = repr(exc).casefold()
    return any(
        marker in text
        for marker in (
            "timeout",
            "timed out",
            "connection reset",
            "connection aborted",
            "temporarily unavailable",
            "remoteprotocolerror",
            "broken pipe",
            "eof",
        )
    )


def _execute_sync(label: str, operation: Callable[[Any], Any]) -> Any:
    last: Optional[Exception] = None
    for attempt in range(1, _DB_ATTEMPTS + 1):
        try:
            client = get_supabase()
            if client is None:
                raise CinemaStorageUnavailable(
                    "Dank Cinema storage is unavailable."
                )
            return operation(client)
        except CinemaStorageUnavailable:
            raise
        except Exception as exc:
            last = exc
            if _is_retryable(exc) and attempt < _DB_ATTEMPTS:
                reset_supabase()
                time.sleep(0.12 * attempt)
                continue
            break
    raise CinemaStorageUnavailable(
        f"{label} failed safely: {type(last).__name__ if last else 'unknown error'}"
    )


async def _execute(label: str, operation: Callable[[Any], Any]) -> Any:
    return await asyncio.to_thread(_execute_sync, label, operation)


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
    result["default_subtitle_language"] = " ".join(
        str(raw.get("default_subtitle_language") or "").split()
    )[:40]
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
) -> dict[str, Any]:
    uid = int(user_id)
    lock = _LOCKS.setdefault(uid, asyncio.Lock())
    async with lock:
        current = await get_cinema_user(uid, refresh=True)
        merged = dict(current.get("preferences") or {})
        for key in DEFAULT_PREFERENCES:
            if key in updates:
                merged[key] = updates.get(key)
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
    payload = {
        **key,
        "title": " ".join(str(title or "").split())[:180],
        "metadata": dict(metadata or {}),
        "progress_seconds": 0.0 if resolved_completed else progress,
        "duration_seconds": duration,
        "completed": resolved_completed,
        "last_watched_at": _now(),
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

    await _execute(f"write Cinema progress {user_id}", write)
    invalidate_cinema_user_cache(int(user_id))
    return payload


def _sort_iso(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: str(row.get(key) or ""),
        reverse=True,
    )


async def library_snapshot(user_id: int) -> dict[str, Any]:
    rows = await list_user_media(int(user_id))
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
        "series_progress": list(latest_episode_by_series.values()),
    }


async def create_notification(
    user_id: int,
    *,
    guild_id: Optional[int],
    kind: str,
    title: str,
    body: str = "",
    action: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    payload = {
        "user_id": int(user_id),
        "guild_id": int(guild_id) if guild_id else None,
        "kind": " ".join(str(kind or "info").split())[:40] or "info",
        "title": " ".join(str(title or "").split())[:160],
        "body": " ".join(str(body or "").split())[:500],
        "action": dict(action or {}),
        "created_at": _now(),
    }
    if not payload["title"]:
        raise InvalidCinemaState("Cinema notification title is required.")

    def write(client: Any):
        return client.table(NOTIFICATION_TABLE).insert(payload).execute()

    response = await _execute(f"create Cinema notification {user_id}", write)
    rows = _rows(response)
    return rows[0] if rows else payload


async def list_notifications(
    user_id: int,
    *,
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
    "get_cinema_user",
    "invalidate_cinema_user_cache",
    "library_snapshot",
    "list_notifications",
    "list_user_media",
    "mark_notification_read",
    "record_progress",
    "set_watchlist",
    "update_cinema_preferences",
]
