from __future__ import annotations

"""Full Dank Cinema website routes.

This is the product shell around the existing Watch player. It owns discovery,
library/search/details/profile/notification web APIs, while MovieNightRoom and
movie_night_web remain the playback/session authority.
"""

import asyncio
import json
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional

from aiohttp import web

from .cinema_catalog import (
    CinemaEpisode,
    CinemaMedia,
    catalog_home,
    get_details,
    get_season,
    recommendations_for_history,
    search_catalog,
)
from .cinema_library_service import (
    CinemaStorageUnavailable,
    create_notification,
    get_cinema_user,
    library_snapshot,
    list_notifications,
    list_user_media,
    mark_notification_read,
    record_progress,
    set_watchlist,
    update_cinema_preferences,
)
from .cinema_site_auth import cinema_site_url, validate_cinema_site_access
from .cinema_discovery_service import (
    list_recent_discoveries,
    search_discoveries,
)
from .cinema_feed_service import (
    CinemaFeedConflict,
    feed_state as cinema_feed_state,
    mutate_feed as mutate_cinema_feed,
)
from .media_source_registry import enabled_structured_sources, load_media_source_registry
from .media_source_resolver import (
    preview_custom_media_source,
    search_custom_media_sources,
)
from .movie_night import get_movie_night_manager
from .movie_night_web import movie_night_watch_url

_ASSET_DIR = Path(__file__).with_name("assets")
_CSS_PATH = _ASSET_DIR / "cinema_site.css"
_JS_PATH = _ASSET_DIR / "cinema_site.js"


def _site_identity(request: web.Request) -> tuple[int, int]:
    try:
        guild_id = int(request.match_info.get("guild_id") or 0)
    except Exception:
        guild_id = 0
    uid = validate_cinema_site_access(
        guild_id,
        str(request.query.get("uid", "") or ""),
        str(request.query.get("exp", "") or ""),
        str(request.query.get("sig", "") or ""),
    )
    if guild_id <= 0 or uid is None:
        raise web.HTTPUnauthorized(text="Invalid or expired Dank Cinema link.")
    return guild_id, int(uid)


def _can_manage_cinema(guild_id: int, user_id: int) -> bool:
    try:
        from .globals import bot
    except Exception:
        bot = None
    if bot is None:
        return False
    try:
        guild = bot.get_guild(int(guild_id))
    except Exception:
        guild = None
    if guild is None:
        return False
    if int(getattr(guild, "owner_id", 0) or 0) == int(user_id):
        return True
    try:
        member = guild.get_member(int(user_id))
    except Exception:
        member = None
    if member is None:
        return False
    permissions = getattr(member, "guild_permissions", None)
    return bool(
        getattr(permissions, "administrator", False)
        or getattr(permissions, "manage_guild", False)
    )


def _safe_discord_context(guild_id: int, user_id: int) -> dict[str, Any]:
    try:
        from .globals import bot
    except Exception:
        bot = None

    guild = None
    member = None
    if bot is not None:
        try:
            guild = bot.get_guild(int(guild_id))
        except Exception:
            guild = None
    if guild is not None:
        try:
            member = guild.get_member(int(user_id))
        except Exception:
            member = None
    if member is None and bot is not None:
        try:
            member = bot.get_user(int(user_id))
        except Exception:
            member = None

    avatar_url = ""
    if member is not None:
        try:
            raw = str(getattr(getattr(member, "display_avatar", None), "url", "") or "")
            if raw.startswith(
                ("https://cdn.discordapp.com/", "https://media.discordapp.net/")
            ):
                avatar_url = raw
        except Exception:
            avatar_url = ""

    return {
        "guild_id": int(guild_id),
        "guild_name": str(getattr(guild, "name", "") or "")[:100],
        "user_id": int(user_id),
        "user_name": str(
            getattr(member, "display_name", "")
            or getattr(member, "global_name", "")
            or getattr(member, "name", "")
            or user_id
        )[:80],
        "avatar_url": avatar_url,
        "discord_url": (
            f"https://discord.com/channels/{int(guild_id)}"
            if int(guild_id) > 0
            else ""
        ),
    }


def _media_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    payload = {
        "media_type": str(row.get("media_type") or ""),
        "tmdb_id": int(row.get("tmdb_id") or 0),
        "season_number": int(row.get("season_number") or 0),
        "episode_number": int(row.get("episode_number") or 0),
        "title": str(row.get("title") or "")[:180],
        "progress_seconds": float(row.get("progress_seconds") or 0.0),
        "duration_seconds": float(row.get("duration_seconds") or 0.0),
        "completed": bool(row.get("completed")),
        "watchlisted": bool(row.get("watchlisted")),
        "last_watched_at": str(row.get("last_watched_at") or ""),
        "watchlisted_at": str(row.get("watchlisted_at") or ""),
        "metadata": dict(metadata),
    }
    if payload["duration_seconds"] > 0:
        payload["progress_ratio"] = min(
            1.0,
            payload["progress_seconds"] / payload["duration_seconds"],
        )
    else:
        payload["progress_ratio"] = 0.0
    return payload


def _discovery_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    metadata = (
        dict(row.get("metadata") or {})
        if isinstance(row.get("metadata"), Mapping)
        else {}
    )
    media_type = str(row.get("media_type") or "").strip().lower()
    try:
        tmdb_id = int(row.get("tmdb_id") or 0)
    except Exception:
        tmdb_id = 0
    return {
        "result_kind": "feed_discovery",
        "title": str(row.get("title") or "")[:180],
        "media_type": media_type,
        "tmdb_id": tmdb_id,
        "poster_url": str(metadata.get("poster_url") or ""),
        "backdrop_url": str(metadata.get("backdrop_url") or ""),
        "overview": str(metadata.get("overview") or "")[:900],
        "year": int(metadata.get("year") or 0),
        "rating": float(metadata.get("rating") or 0.0),
        "source_id": str(row.get("source_id") or ""),
        "source_label": str(metadata.get("source_label") or ""),
        "category": str(metadata.get("category") or "custom"),
        "playable": bool(row.get("playable", True)),
        "first_seen_at": str(row.get("first_seen_at") or ""),
        "metadata": metadata,
    }


def _active_rooms_payload(guild_id: int, user_id: int) -> list[dict[str, Any]]:
    manager = get_movie_night_manager()
    rows: list[dict[str, Any]] = []
    for room in manager.active_rooms_for_guild(int(guild_id)):
        if not manager.user_can_access(room, int(user_id)):
            continue
        candidate = room.current_candidate
        metadata: Mapping[str, Any] = {}
        if candidate is not None and isinstance(candidate.metadata, Mapping):
            catalog = candidate.metadata.get("catalog")
            metadata = catalog if isinstance(catalog, Mapping) else candidate.metadata
        rows.append(
            {
                "room_id": room.room_id,
                "mode": str(room.mode or "watch_party"),
                "is_host": int(room.host_id) == int(user_id),
                "viewer_count": len(manager.active_viewers(room)),
                "playback_state": str(room.playback_state or "paused"),
                "position_seconds": room.current_position(),
                "title": str(
                    metadata.get("title")
                    or getattr(candidate, "title", "")
                    or "Cinema session"
                )[:180],
                "media_type": str(
                    metadata.get("media_type")
                    or ("movie" if metadata.get("catalog_provider") == "tmdb" else "")
                )[:20],
                "tmdb_id": int(
                    metadata.get("tmdb_id")
                    or metadata.get("catalog_id")
                    or 0
                ),
                "poster_url": str(metadata.get("poster_url") or ""),
                "backdrop_url": str(metadata.get("backdrop_url") or ""),
                "watch_url": movie_night_watch_url(room.room_id, int(user_id)),
            }
        )
    return rows


async def _feed_discovery(guild_id: int, *, limit: int = 14) -> list[dict[str, Any]]:
    try:
        _raw, registry = await load_media_source_registry(int(guild_id), refresh=False)
    except Exception:
        return []
    sources = enabled_structured_sources(registry)[:5]
    if not sources:
        return []

    semaphore = asyncio.Semaphore(3)

    async def one(source: Any) -> list[dict[str, Any]]:
        async with semaphore:
            try:
                result = await preview_custom_media_source(source, query="movie", limit=4)
            except Exception:
                return []
        rows: list[dict[str, Any]] = []
        for variant in result.variants:
            rows.append(
                {
                    "title": variant.title,
                    "source_id": variant.source_id,
                    "source_label": variant.source_label,
                    "category": source.category,
                    "playable": True,
                    "metadata": dict(variant.metadata or {}),
                }
            )
        return rows

    groups = await asyncio.gather(*(one(source) for source in sources))
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for group in groups:
        for row in group:
            key = (str(row["title"]).casefold(), str(row["source_id"]))
            if key in seen:
                continue
            seen.add(key)
            output.append(row)
            if len(output) >= limit:
                return output
    return output


async def _next_episode_rows(library: Mapping[str, Any]) -> list[dict[str, Any]]:
    progress_rows = list(library.get("series_progress") or [])[:8]
    if not progress_rows:
        return []

    async def one(row: Mapping[str, Any]) -> Optional[dict[str, Any]]:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
        series_id = int(metadata.get("series_id") or 0)
        current_season = int(row.get("season_number") or 0)
        current_episode = int(row.get("episode_number") or 0)
        if series_id <= 0:
            return None
        try:
            details = await get_details("tv", series_id)
            season_numbers = [
                int(item.get("season_number") or 0)
                for item in details.seasons
                if int(item.get("season_number") or 0) > 0
            ]
            candidates: list[CinemaEpisode] = []
            for season_number in sorted(set(season_numbers)):
                if season_number < current_season:
                    continue
                episodes = await get_season(series_id, season_number)
                for episode in episodes:
                    if season_number == current_season and episode.episode_number <= current_episode:
                        continue
                    candidates.append(episode)
                if candidates:
                    break
            if not candidates:
                return None
            episode = candidates[0]
            if episode.air_date:
                try:
                    if date.fromisoformat(episode.air_date) > date.today():
                        return None
                except Exception:
                    pass
            return {
                **episode.to_payload(),
                "series_title": details.media.title,
                "series_poster_url": details.media.poster_url,
            }
        except Exception:
            return None

    results = await asyncio.gather(*(one(row) for row in progress_rows))
    return [row for row in results if row is not None]


def _hero_from_sections(
    active_rooms: list[dict[str, Any]],
    continue_rows: list[Mapping[str, Any]],
    trending: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    if active_rooms:
        room = dict(active_rooms[0])
        return {
            "kind": "session",
            "title": room.get("title") or "Continue your Cinema session",
            "subtitle": "Return to the live session without losing synchronization.",
            "backdrop_url": room.get("backdrop_url") or "",
            "poster_url": room.get("poster_url") or "",
            "action": {"kind": "watch_url", "url": room.get("watch_url") or "", "label": "Return to Theater"},
            "secondary": {"kind": "session", "room_id": room.get("room_id")},
        }
    if continue_rows:
        row = dict(continue_rows[0])
        metadata = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
        return {
            "kind": "resume",
            "title": row.get("title") or "Continue Watching",
            "subtitle": "Pick up where you left off.",
            "backdrop_url": str(metadata.get("backdrop_url") or ""),
            "poster_url": str(metadata.get("poster_url") or ""),
            "action": {
                "kind": "details",
                "media_type": row.get("media_type"),
                "tmdb_id": row.get("tmdb_id"),
                "label": "Resume",
            },
        }
    if trending:
        row = dict(trending[0])
        return {
            "kind": "discover",
            "title": row.get("title") or "Trending now",
            "subtitle": row.get("overview") or "Explore what people are watching.",
            "backdrop_url": row.get("backdrop_url") or "",
            "poster_url": row.get("poster_url") or "",
            "action": {
                "kind": "details",
                "media_type": row.get("media_type"),
                "tmdb_id": row.get("tmdb_id"),
                "label": "More Info",
            },
        }
    return None


async def cinema_home_api(request: web.Request) -> web.Response:
    guild_id, user_id = _site_identity(request)
    try:
        library_task = asyncio.create_task(library_snapshot(user_id))
        profile_task = asyncio.create_task(get_cinema_user(user_id))
        catalog_task = asyncio.create_task(catalog_home())
        feeds_task = asyncio.create_task(_feed_discovery(guild_id))
        recent_added_task = asyncio.create_task(
            list_recent_discoveries(guild_id, limit=30)
        )
        notifications_task = asyncio.create_task(
            list_notifications(user_id, unread_only=True, limit=20)
        )
        library, profile, catalog, feeds, recent_added, notifications = await asyncio.gather(
            library_task,
            profile_task,
            catalog_task,
            feeds_task,
            recent_added_task,
            notifications_task,
        )
    except CinemaStorageUnavailable as exc:
        raise web.HTTPServiceUnavailable(
            text="Dank Cinema library storage is unavailable. Your theater session still works."
        ) from exc
    except RuntimeError as exc:
        raise web.HTTPServiceUnavailable(text=str(exc)) from exc

    recent = list(library.get("recently_watched") or [])
    try:
        recommended = await recommendations_for_history(recent, limit=20)
    except Exception:
        recommended = ()
    new_episodes = await _next_episode_rows(library)
    active_rooms = _active_rooms_payload(guild_id, user_id)

    sections: list[dict[str, Any]] = []

    def add(key: str, title: str, rows: Any) -> None:
        clean = list(rows or [])
        if clean:
            sections.append({"key": key, "title": title, "items": clean})

    add(
        "continue",
        "Continue Watching",
        [_media_payload(row) for row in library.get("continue_watching") or []],
    )
    add("new_episodes", "New Episodes", new_episodes)
    add(
        "trending",
        "Trending",
        [item.to_payload() for item in catalog.get("trending", ())],
    )
    add(
        "popular_movies",
        "Popular Movies",
        [item.to_payload() for item in catalog.get("popular_movies", ())],
    )
    add(
        "popular_tv",
        "Popular TV",
        [item.to_payload() for item in catalog.get("popular_tv", ())],
    )
    add(
        "recently_added",
        "Recently Added",
        [_discovery_payload(row) for row in recent_added],
    )
    add(
        "watchlist",
        "Your Watchlist",
        [_media_payload(row) for row in library.get("watchlist") or []],
    )
    add(
        "watch_again",
        "Watch Again",
        [_media_payload(row) for row in library.get("watch_again") or []],
    )
    add(
        "watch_party_picks",
        "Watch Party Picks",
        [item.to_payload() for item in catalog.get("top_movies", ())[:14]],
    )
    add("feeds", "From Your Feeds", feeds)
    add(
        "recommended",
        "Recommended For You",
        [item.to_payload() for item in recommended],
    )
    add(
        "recently_watched",
        "Recently Watched",
        [_media_payload(row) for row in library.get("recently_watched") or []],
    )

    trending_payload = [
        item.to_payload() for item in catalog.get("trending", ())
    ]
    hero = _hero_from_sections(
        active_rooms,
        list(library.get("continue_watching") or []),
        trending_payload,
    )
    return web.json_response(
        {
            "discord": _safe_discord_context(guild_id, user_id),
            "can_manage_cinema": _can_manage_cinema(guild_id, user_id),
            "profile": profile,
            "notifications_unread": len(notifications),
            "active_sessions": active_rooms,
            "hero": hero,
            "sections": sections,
        }
    )


async def cinema_search_api(request: web.Request) -> web.Response:
    guild_id, user_id = _site_identity(request)
    query = " ".join(str(request.query.get("q", "") or "").split())[:180]
    if not query:
        return web.json_response({"query": "", "results": []})

    catalog_task = asyncio.create_task(search_catalog(query, limit=30))
    media_task = asyncio.create_task(list_user_media(user_id))
    source_task = asyncio.create_task(search_custom_media_sources(guild_id, query))
    discovery_task = asyncio.create_task(
        search_discoveries(guild_id, query, limit=20)
    )
    catalog_rows, user_rows, source_result, discovery_rows = await asyncio.gather(
        catalog_task,
        media_task,
        source_task,
        discovery_task,
        return_exceptions=True,
    )

    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    if not isinstance(catalog_rows, Exception):
        for media in catalog_rows:
            payload = media.to_payload()
            payload["result_kind"] = "catalog"
            results.append(payload)
            seen.add(media.key)

    needle = query.casefold()
    if not isinstance(user_rows, Exception):
        for row in user_rows:
            title = str(row.get("title") or "")
            if needle not in title.casefold():
                continue
            payload = _media_payload(row)
            key = (
                f"{payload['media_type']}:{payload['tmdb_id']}:"
                f"{payload['season_number']}:{payload['episode_number']}"
            )
            if key in seen:
                continue
            payload["result_kind"] = (
                "episode" if payload["media_type"] == "episode" else "library"
            )
            results.append(payload)
            seen.add(key)

    if not isinstance(source_result, Exception):
        for variant in source_result.variants[:20]:
            key = f"source:{variant.source_id}:{variant.title.casefold()}"
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "result_kind": "playable_source",
                    "title": variant.title,
                    "source_id": variant.source_id,
                    "source_label": variant.source_label,
                    "playable": True,
                    "metadata": dict(variant.metadata or {}),
                }
            )

    if not isinstance(discovery_rows, Exception):
        for row in discovery_rows:
            payload = _discovery_payload(row)
            key = (
                f"{payload.get('media_type')}:{payload.get('tmdb_id')}"
                if payload.get("tmdb_id")
                else f"feed:{payload.get('source_id')}:{str(payload.get('title') or '').casefold()}"
            )
            if key in seen:
                continue
            seen.add(key)
            results.append(payload)

    return web.json_response({"query": query, "results": results[:60]})


async def cinema_details_api(request: web.Request) -> web.Response:
    _guild_id, user_id = _site_identity(request)
    media_type = str(request.match_info.get("media_type") or "").strip().lower()
    try:
        tmdb_id = int(request.match_info.get("tmdb_id") or 0)
    except Exception:
        tmdb_id = 0
    if media_type not in {"movie", "tv"} or tmdb_id <= 0:
        raise web.HTTPBadRequest(text="Invalid Cinema title.")

    details_task = asyncio.create_task(get_details(media_type, tmdb_id))
    library_task = asyncio.create_task(list_user_media(user_id))
    details, rows = await asyncio.gather(details_task, library_task)

    matching = [
        _media_payload(row)
        for row in rows
        if str(row.get("media_type") or "") == media_type
        and int(row.get("tmdb_id") or 0) == tmdb_id
        and int(row.get("season_number") or 0) == 0
        and int(row.get("episode_number") or 0) == 0
    ]
    episode_progress = [
        _media_payload(row)
        for row in rows
        if media_type == "tv"
        and str(row.get("media_type") or "") == "episode"
        and int(
            (
                row.get("metadata")
                if isinstance(row.get("metadata"), Mapping)
                else {}
            ).get("series_id")
            or 0
        )
        == tmdb_id
    ]
    active_sessions = _active_rooms_payload(_guild_id, user_id)
    active_session = next(
        (
            room
            for room in active_sessions
            if str(room.get("media_type") or "") == media_type
            and int(room.get("tmdb_id") or 0) == tmdb_id
        ),
        None,
    )
    return web.json_response(
        {
            "details": details.to_payload(),
            "library": matching[0] if matching else None,
            "episode_progress": episode_progress,
            "active_session": active_session,
            "discord": _safe_discord_context(_guild_id, user_id),
        }
    )


async def cinema_season_api(request: web.Request) -> web.Response:
    _guild_id, user_id = _site_identity(request)
    try:
        series_id = int(request.match_info.get("series_id") or 0)
        season_number = int(request.match_info.get("season_number") or 0)
    except Exception:
        raise web.HTTPBadRequest(text="Invalid season.")
    episodes_task = asyncio.create_task(get_season(series_id, season_number))
    media_task = asyncio.create_task(list_user_media(user_id))
    episodes, rows = await asyncio.gather(episodes_task, media_task)

    progress = {
        (
            int(row.get("season_number") or 0),
            int(row.get("episode_number") or 0),
        ): _media_payload(row)
        for row in rows
        if str(row.get("media_type") or "") == "episode"
        and int(
            (
                row.get("metadata")
                if isinstance(row.get("metadata"), Mapping)
                else {}
            ).get("series_id")
            or 0
        )
        == series_id
    }
    output: list[dict[str, Any]] = []
    for episode in episodes:
        payload = episode.to_payload()
        payload["progress"] = progress.get(
            (episode.season_number, episode.episode_number)
        )
        output.append(payload)
    return web.json_response({"episodes": output})


async def cinema_library_api(request: web.Request) -> web.Response:
    _guild_id, user_id = _site_identity(request)
    if request.method == "GET":
        return web.json_response(await library_snapshot(user_id))

    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, Mapping):
        payload = {}
    action = str(payload.get("action") or "").strip().lower()

    if action == "watchlist":
        row = await set_watchlist(
            user_id,
            media_type=str(payload.get("media_type") or ""),
            tmdb_id=int(payload.get("tmdb_id") or 0),
            title=str(payload.get("title") or ""),
            metadata=(
                payload.get("metadata")
                if isinstance(payload.get("metadata"), Mapping)
                else {}
            ),
            enabled=bool(payload.get("enabled", True)),
        )
        return web.json_response({"ok": True, "item": row})

    if action == "progress":
        row = await record_progress(
            user_id,
            media_type=str(payload.get("media_type") or ""),
            tmdb_id=int(payload.get("tmdb_id") or 0),
            title=str(payload.get("title") or ""),
            progress_seconds=float(payload.get("progress_seconds") or 0.0),
            duration_seconds=float(payload.get("duration_seconds") or 0.0),
            season_number=int(payload.get("season_number") or 0),
            episode_number=int(payload.get("episode_number") or 0),
            metadata=(
                payload.get("metadata")
                if isinstance(payload.get("metadata"), Mapping)
                else {}
            ),
            completed=(
                bool(payload.get("completed"))
                if "completed" in payload
                else None
            ),
        )
        return web.json_response({"ok": True, "item": row})

    raise web.HTTPBadRequest(text="Unsupported Cinema library action.")


async def cinema_profile_api(request: web.Request) -> web.Response:
    guild_id, user_id = _site_identity(request)
    if request.method == "GET":
        row = await get_cinema_user(user_id)
        return web.json_response(
            {
                "discord": _safe_discord_context(guild_id, user_id),
                "preferences": row.get("preferences") or {},
            }
        )
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, Mapping):
        payload = {}
    row = await update_cinema_preferences(user_id, payload)
    return web.json_response(
        {
            "ok": True,
            "preferences": row.get("preferences") or {},
        }
    )


async def cinema_feeds_api(request: web.Request) -> web.Response:
    guild_id, user_id = _site_identity(request)
    can_manage = _can_manage_cinema(guild_id, user_id)

    if request.method == "GET":
        return web.json_response(
            await cinema_feed_state(
                guild_id,
                can_manage=can_manage,
                refresh=False,
            )
        )

    if not can_manage:
        raise web.HTTPForbidden(
            text="Manage Server permission is required to change Cinema sources."
        )
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, Mapping):
        payload = {}
    try:
        await mutate_cinema_feed(
            guild_id,
            actor_id=user_id,
            action=str(payload.get("action") or ""),
            payload=payload,
        )
    except LookupError as exc:
        raise web.HTTPNotFound(text=str(exc))
    except CinemaFeedConflict as exc:
        raise web.HTTPConflict(text=str(exc))
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc))

    return web.json_response(
        await cinema_feed_state(
            guild_id,
            can_manage=True,
            refresh=False,
        )
    )


async def cinema_notifications_api(request: web.Request) -> web.Response:
    _guild_id, user_id = _site_identity(request)
    if request.method == "GET":
        rows = await list_notifications(user_id, limit=50)
        return web.json_response({"notifications": rows})
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, Mapping):
        payload = {}
    notification_id = str(payload.get("notification_id") or "").strip()
    if not notification_id:
        raise web.HTTPBadRequest(text="Choose a notification.")
    await mark_notification_read(user_id, notification_id)
    return web.json_response({"ok": True})


def _site_html(guild_id: int, user_id: int) -> str:
    boot = json.dumps(
        {
            "guildId": int(guild_id),
            "userId": int(user_id),
        },
        separators=(",", ":"),
    ).replace("</", "<\/")
    return f"""<!doctype html>
<html lang="en" data-quality="standard">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
  <meta name="theme-color" content="#030806">
  <title>Dank Cinema</title>
  <link rel="stylesheet" href="/cinema/assets/site.css?v=1">
</head>
<body>
  <div id="app" class="app-shell" aria-live="polite"></div>
  <script>window.__DANK_CINEMA_BOOT__={boot};</script>
  <script src="/cinema/assets/site.js?v=1" defer></script>
</body>
</html>"""


async def cinema_site_page(request: web.Request) -> web.Response:
    guild_id, user_id = _site_identity(request)
    return web.Response(
        text=_site_html(guild_id, user_id),
        content_type="text/html",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline'; "
                "style-src 'self'; "
                "img-src 'self' data: https://image.tmdb.org https://cdn.discordapp.com https://media.discordapp.net; "
                "connect-src 'self'; "
                "frame-src https://www.youtube-nocookie.com; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def cinema_site_asset(request: web.Request) -> web.Response:
    name = str(request.match_info.get("name") or "")
    path: Optional[Path]
    content_type: str
    if name == "site.css":
        path = _CSS_PATH
        content_type = "text/css"
    elif name == "site.js":
        path = _JS_PATH
        content_type = "application/javascript"
    else:
        raise web.HTTPNotFound(text="Cinema asset not found.")
    try:
        payload = path.read_bytes()
    except OSError:
        raise web.HTTPNotFound(text="Cinema asset unavailable.")
    return web.Response(
        body=payload,
        content_type=content_type,
        headers={
            "Cache-Control": "public, max-age=300",
            "X-Content-Type-Options": "nosniff",
        },
    )


def register_cinema_site_routes(app: web.Application) -> None:
    app.router.add_get("/cinema/assets/{name}", cinema_site_asset)
    app.router.add_get("/cinema/{guild_id}", cinema_site_page)
    app.router.add_get("/cinema/{guild_id}/api/home", cinema_home_api)
    app.router.add_get("/cinema/{guild_id}/api/search", cinema_search_api)
    app.router.add_get(
        "/cinema/{guild_id}/api/details/{media_type}/{tmdb_id}",
        cinema_details_api,
    )
    app.router.add_get(
        "/cinema/{guild_id}/api/season/{series_id}/{season_number}",
        cinema_season_api,
    )
    app.router.add_get("/cinema/{guild_id}/api/library", cinema_library_api)
    app.router.add_post("/cinema/{guild_id}/api/library", cinema_library_api)
    app.router.add_get("/cinema/{guild_id}/api/profile", cinema_profile_api)
    app.router.add_post("/cinema/{guild_id}/api/profile", cinema_profile_api)
    app.router.add_get("/cinema/{guild_id}/api/feeds", cinema_feeds_api)
    app.router.add_post("/cinema/{guild_id}/api/feeds", cinema_feeds_api)
    app.router.add_get(
        "/cinema/{guild_id}/api/notifications",
        cinema_notifications_api,
    )
    app.router.add_post(
        "/cinema/{guild_id}/api/notifications",
        cinema_notifications_api,
    )


__all__ = [
    "cinema_feeds_api",
    "cinema_home_api",
    "cinema_library_api",
    "cinema_notifications_api",
    "cinema_profile_api",
    "cinema_search_api",
    "cinema_season_api",
    "cinema_site_page",
    "cinema_site_url",
    "register_cinema_site_routes",
]
