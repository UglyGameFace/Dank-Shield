from __future__ import annotations

"""Bot-native Dank Cinema Library intelligence.

This module composes the existing owners rather than replacing them:
Library supplies user state, TMDB supplies canonical metadata/recommendations,
Feed Center supplies availability, and Theater remains playback authority.
"""

import asyncio
from collections import defaultdict
from datetime import date
from typing import Any, Mapping, Optional, Sequence

from .cinema_catalog import (
    CinemaEpisode,
    CinemaMedia,
    get_details,
    get_next_episode,
    get_season,
)
from .cinema_discovery_service import list_recent_discoveries
from .cinema_feed_personalization import group_feed_results
from .cinema_library_service import (
    library_snapshot_from_rows,
    library_stats,
    list_custom_lists,
    list_user_media,
    list_watch_sessions,
)


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


def _clean(value: Any, limit: int = 180) -> str:
    return " ".join(str(value or "").split())[:limit]


def _metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = row.get("metadata")
    return dict(raw) if isinstance(raw, Mapping) else {}


def _canonical_key(row: Mapping[str, Any]) -> Optional[tuple[str, int]]:
    kind = str(row.get("media_type") or "").strip().lower()
    tmdb_id = _safe_int(row.get("tmdb_id"))
    if kind in {"movie", "tv"} and tmdb_id > 0:
        return kind, tmdb_id
    if kind == "episode":
        series_id = _safe_int(_metadata(row).get("series_id"))
        if series_id > 0:
            return "tv", series_id
    return None


def _media_state_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    metadata = _metadata(row)
    duration = max(0.0, _safe_float(row.get("duration_seconds")))
    progress = max(0.0, _safe_float(row.get("progress_seconds")))
    return {
        "media_type": str(row.get("media_type") or ""),
        "tmdb_id": _safe_int(row.get("tmdb_id")),
        "season_number": _safe_int(row.get("season_number")),
        "episode_number": _safe_int(row.get("episode_number")),
        "series_id": _safe_int(metadata.get("series_id")),
        "series_title": _clean(metadata.get("series_title"), 180),
        "title": _clean(row.get("title"), 180),
        "poster_url": str(
            metadata.get("poster_url")
            or metadata.get("series_poster_url")
            or ""
        ),
        "backdrop_url": str(
            metadata.get("backdrop_url")
            or metadata.get("still_url")
            or ""
        ),
        "year": _safe_int(metadata.get("year")),
        "adult": bool(metadata.get("adult", False)),
        "progress_seconds": progress,
        "duration_seconds": duration,
        "progress_ratio": min(1.0, progress / duration) if duration > 0 else 0.0,
        "completed": bool(row.get("completed")),
        "watchlisted": bool(row.get("watchlisted")),
        "favorite": bool(row.get("favorite")),
        "user_rating": _safe_int(row.get("rating")),
        "play_count": max(0, _safe_int(row.get("play_count"))),
        "first_watched_at": str(row.get("first_watched_at") or ""),
        "last_watched_at": str(row.get("last_watched_at") or ""),
        "last_completed_at": str(row.get("last_completed_at") or ""),
        "watchlisted_at": str(row.get("watchlisted_at") or ""),
        "favorite_at": str(row.get("favorite_at") or ""),
        "rated_at": str(row.get("rated_at") or ""),
        "list_position": max(0, _safe_int(row.get("position"))),
        "metadata": {
            key: metadata.get(key)
            for key in (
                "series_id",
                "series_title",
                "episode_title",
                "poster_url",
                "series_poster_url",
                "backdrop_url",
                "still_url",
                "year",
                "adult",
                "genres",
                "studios",
                "franchises",
            )
            if key in metadata
        },
    }


def _session_payload(
    row: Mapping[str, Any],
    *,
    current_state: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    metadata = _metadata(row)
    current = dict(current_state or {})
    return {
        "id": str(row.get("id") or ""),
        "media_type": str(row.get("media_type") or ""),
        "tmdb_id": _safe_int(row.get("tmdb_id")),
        "series_id": _safe_int(row.get("series_id")),
        "season_number": _safe_int(row.get("season_number")),
        "episode_number": _safe_int(row.get("episode_number")),
        "title": _clean(row.get("title"), 180),
        "guild_id": _safe_int(row.get("guild_id")),
        "room_id": _clean(row.get("room_id"), 120),
        "session_mode": (
            str(row.get("session_mode") or "private")
            if str(row.get("session_mode") or "private") in {"private", "watch_party"}
            else "private"
        ),
        "is_host": bool(row.get("is_host")),
        "max_progress_seconds": max(0.0, _safe_float(row.get("max_progress_seconds"))),
        "duration_seconds": max(0.0, _safe_float(row.get("duration_seconds"))),
        "completed": bool(row.get("completed")),
        "favorite": bool(current.get("favorite")),
        "user_rating": _safe_int(current.get("rating")),
        "play_count": max(0, _safe_int(current.get("play_count"))),
        "watchlisted": bool(current.get("watchlisted")),
        "started_at": str(row.get("started_at") or ""),
        "last_seen_at": str(row.get("last_seen_at") or ""),
        "completed_at": str(row.get("completed_at") or ""),
        "poster_url": str(
            metadata.get("poster_url")
            or metadata.get("series_poster_url")
            or ""
        ),
        "backdrop_url": str(
            metadata.get("backdrop_url")
            or metadata.get("still_url")
            or ""
        ),
        "year": _safe_int(metadata.get("year")),
        "adult": bool(metadata.get("adult", False)),
    }


async def _availability_groups(guild_id: int) -> list[dict[str, Any]]:
    try:
        rows = await list_recent_discoveries(int(guild_id), limit=200)
    except Exception:
        return []
    return group_feed_results(
        [dict(row) for row in rows if isinstance(row, Mapping)]
    )


def _availability_index(
    groups: Sequence[Mapping[str, Any]],
) -> tuple[dict[tuple[str, int], dict[str, Any]], dict[tuple[int, int, int], dict[str, Any]]]:
    titles: dict[tuple[str, int], dict[str, Any]] = {}
    episodes: dict[tuple[int, int, int], dict[str, Any]] = {}
    for raw in groups:
        group = dict(raw)
        kind = str(group.get("media_type") or "").strip().lower()
        tmdb_id = _safe_int(group.get("tmdb_id"))
        if kind in {"movie", "tv"} and tmdb_id > 0:
            current = titles.get((kind, tmdb_id))
            if current is None or _safe_int(group.get("release_count")) > _safe_int(current.get("release_count")):
                titles[(kind, tmdb_id)] = group
        season = _safe_int(group.get("season_number"), -1)
        episode = _safe_int(group.get("episode_number"), -1)
        if kind == "tv" and tmdb_id > 0 and season >= 0 and episode > 0:
            episodes[(tmdb_id, season, episode)] = group
    return titles, episodes


def _decorate_availability(
    payload: Mapping[str, Any],
    *,
    title_index: Mapping[tuple[str, int], Mapping[str, Any]],
    episode_index: Mapping[tuple[int, int, int], Mapping[str, Any]],
) -> dict[str, Any]:
    result = dict(payload)
    kind = str(result.get("media_type") or "").strip().lower()
    if kind == "episode":
        series_id = _safe_int(result.get("series_id"))
        season = _safe_int(result.get("season_number"))
        episode = _safe_int(result.get("episode_number"))
        match = episode_index.get((series_id, season, episode))
    else:
        match = title_index.get((kind, _safe_int(result.get("tmdb_id"))))
    if match:
        result["available_now"] = True
        result["available_release_count"] = max(
            1,
            _safe_int(match.get("release_count"), 1),
        )
        result["best_quality"] = str(match.get("best_quality") or "")
    else:
        result["available_now"] = False
        result["available_release_count"] = 0
        result["best_quality"] = ""
    return result


def _seed_score(row: Mapping[str, Any]) -> float:
    score = 0.0
    play_count = max(0, _safe_int(row.get("play_count")))
    rating = max(0, min(10, _safe_int(row.get("rating"))))
    if play_count:
        score += 4.0 + min(5.0, play_count)
    if bool(row.get("favorite")):
        score += 7.0
    if rating:
        score += max(0.0, rating - 4.0)
    if bool(row.get("watchlisted")):
        score += 2.0
    if row.get("last_watched_at"):
        score += 2.0
    return score


def _seed_rows(rows: Sequence[Mapping[str, Any]], *, limit: int = 5) -> list[tuple[tuple[str, int], dict[str, Any], float]]:
    by_key: dict[tuple[str, int], tuple[dict[str, Any], float]] = {}
    for raw in rows:
        row = dict(raw)
        key = _canonical_key(row)
        if key is None:
            continue
        score = _seed_score(row)
        if score <= 0:
            continue
        current = by_key.get(key)
        if current is None or score > current[1]:
            by_key[key] = (row, score)
    ranked = sorted(
        ((key, row, score) for key, (row, score) in by_key.items()),
        key=lambda item: (
            -item[2],
            str(item[1].get("last_watched_at") or ""),
        ),
    )
    return ranked[: max(1, min(int(limit), 8))]


async def recommendation_rows(
    guild_id: int,
    user_id: int,
    *,
    include_adult: bool,
    limit: int = 20,
    media_rows: Optional[list[dict[str, Any]]] = None,
    availability_groups: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    if media_rows is None and availability_groups is None:
        rows, groups = await asyncio.gather(
            list_user_media(int(user_id)),
            _availability_groups(int(guild_id)),
        )
    elif media_rows is None:
        rows = await list_user_media(int(user_id))
        groups = list(availability_groups or [])
    elif availability_groups is None:
        rows = list(media_rows)
        groups = (
        list(availability_groups)
        if availability_groups is not None
        else await _availability_groups(int(guild_id))
    )
    else:
        rows = list(media_rows)
        groups = list(availability_groups)
    seeds = _seed_rows(rows, limit=5)
    if not seeds:
        return {
            "because_you_watched": [],
            "recommended": [],
        }

    async def details_for(
        key: tuple[str, int],
        row: Mapping[str, Any],
        score: float,
    ) -> tuple[tuple[str, int], dict[str, Any], float, Any]:
        details = await get_details(key[0], key[1])
        return key, dict(row), score, details

    resolved = await asyncio.gather(
        *(details_for(key, row, score) for key, row, score in seeds),
        return_exceptions=True,
    )

    blocked = {
        key
        for raw in rows
        for key in [_canonical_key(raw)]
        if key is not None and (
            max(0, _safe_int(raw.get("play_count"))) > 0
            or bool(raw.get("completed"))
        )
    }
    title_index, episode_index = _availability_index(groups)

    candidate_scores: dict[str, float] = defaultdict(float)
    candidates: dict[str, CinemaMedia] = {}
    reasons: dict[str, str] = {}
    because: list[dict[str, Any]] = []

    for resolved_row in resolved:
        if isinstance(resolved_row, Exception):
            continue
        key, seed_row, seed_score, details = resolved_row
        seed_title = _clean(
            seed_row.get("title")
            or details.media.title,
            180,
        )
        for index, media in enumerate(details.recommendations):
            if media.adult and not include_adult:
                continue
            media_key = (media.media_type, int(media.tmdb_id))
            if media_key in blocked:
                continue
            candidates[media.key] = media
            candidate_scores[media.key] += (
                seed_score
                + max(0.0, 4.0 - index * 0.15)
                + min(2.0, float(media.rating or 0.0) / 5.0)
            )
            reasons.setdefault(media.key, f"Because you watched {seed_title}")

        if not because:
            for media in details.recommendations:
                if media.adult and not include_adult:
                    continue
                if (media.media_type, int(media.tmdb_id)) in blocked:
                    continue
                payload = media.to_payload()
                payload["reason"] = f"Because you watched {seed_title}"
                because.append(
                    _decorate_availability(
                        payload,
                        title_index=title_index,
                        episode_index=episode_index,
                    )
                )
                if len(because) >= 14:
                    break

    ranked = sorted(
        candidates.values(),
        key=lambda media: (
            -candidate_scores.get(media.key, 0.0),
            -float(media.rating or 0.0),
            -float(media.popularity or 0.0),
        ),
    )
    recommended: list[dict[str, Any]] = []
    for media in ranked[: max(1, min(int(limit), 30))]:
        payload = media.to_payload()
        payload["reason"] = reasons.get(media.key, "Recommended from your Dank Cinema activity")
        payload["recommendation_score"] = round(
            candidate_scores.get(media.key, 0.0),
            2,
        )
        recommended.append(
            _decorate_availability(
                payload,
                title_index=title_index,
                episode_index=episode_index,
            )
        )

    return {
        "because_you_watched": because,
        "recommended": recommended,
    }


def _latest_episode_rows_by_series(
    rows: Sequence[Mapping[str, Any]],
) -> dict[int, dict[str, Any]]:
    output: dict[int, dict[str, Any]] = {}
    episodes = [
        dict(row)
        for row in rows
        if str(row.get("media_type") or "") == "episode"
        and _safe_int(_metadata(row).get("series_id")) > 0
    ]
    episodes.sort(
        key=lambda row: (
            str(row.get("last_watched_at") or ""),
            _safe_int(row.get("season_number")),
            _safe_int(row.get("episode_number")),
        ),
        reverse=True,
    )
    for row in episodes:
        series_id = _safe_int(_metadata(row).get("series_id"))
        output.setdefault(series_id, row)
    return output


async def _watchlisted_series_candidate(
    series_id: int,
) -> tuple[Any, Optional[CinemaEpisode]]:
    details = await get_details("tv", int(series_id))
    today = date.today()
    seasons = sorted(
        (
            int(row.get("season_number") or 0)
            for row in details.seasons
            if int(row.get("season_number") or 0) > 0
        ),
        reverse=True,
    )
    future: list[CinemaEpisode] = []
    recent: list[CinemaEpisode] = []
    for season_number in seasons[:3]:
        try:
            episodes = await get_season(int(series_id), season_number)
        except Exception:
            continue
        for episode in episodes:
            if not episode.air_date:
                continue
            try:
                air = date.fromisoformat(episode.air_date)
            except Exception:
                continue
            if air >= today:
                future.append(episode)
            else:
                recent.append(episode)
    if future:
        future.sort(key=lambda row: (row.air_date, row.season_number, row.episode_number))
        return details, future[0]
    if recent:
        recent.sort(
            key=lambda row: (row.air_date, row.season_number, row.episode_number),
            reverse=True,
        )
        return details, recent[0]
    return details, None


async def upcoming_episode_rows(
    guild_id: int,
    user_id: int,
    *,
    include_adult: bool,
    limit: int = 16,
    media_rows: Optional[list[dict[str, Any]]] = None,
    availability_groups: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    if media_rows is None and availability_groups is None:
        rows, groups = await asyncio.gather(
            list_user_media(int(user_id)),
            _availability_groups(int(guild_id)),
        )
    elif media_rows is None:
        rows = await list_user_media(int(user_id))
        groups = list(availability_groups or [])
    elif availability_groups is None:
        rows = list(media_rows)
        groups = await _availability_groups(int(guild_id))
    else:
        rows = list(media_rows)
        groups = list(availability_groups)
    title_index, episode_index = _availability_index(groups)
    latest_by_series = _latest_episode_rows_by_series(rows)

    watchlisted_series: dict[int, dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw)
        if (
            str(row.get("media_type") or "") == "tv"
            and bool(row.get("watchlisted"))
            and _safe_int(row.get("tmdb_id")) > 0
        ):
            watchlisted_series[_safe_int(row.get("tmdb_id"))] = row
    series_ids = list(
        dict.fromkeys(
            [*latest_by_series.keys(), *watchlisted_series.keys()]
        )
    )[:8]

    async def one(series_id: int) -> Optional[dict[str, Any]]:
        latest = latest_by_series.get(series_id)
        try:
            if latest is not None:
                details = await get_details("tv", series_id)
                episode = await get_next_episode(
                    series_id,
                    _safe_int(latest.get("season_number")),
                    _safe_int(latest.get("episode_number")),
                )
            else:
                details, episode = await _watchlisted_series_candidate(series_id)
        except Exception:
            return None
        if details.media.adult and not include_adult:
            return None
        if episode is None:
            return None

        payload = {
            **episode.to_payload(),
            "series_title": details.media.title,
            "series_poster_url": details.media.poster_url,
            "poster_url": details.media.poster_url,
            "backdrop_url": episode.still_url or details.media.backdrop_url,
        }
        payload = _decorate_availability(
            payload,
            title_index=title_index,
            episode_index=episode_index,
        )
        air_date = str(episode.air_date or "")
        today = date.today()
        future = False
        if air_date:
            try:
                future = date.fromisoformat(air_date) > today
            except Exception:
                future = False
        if future:
            payload["availability_status"] = "upcoming"
            payload["availability_label"] = f"Airs {air_date}"
        elif payload["available_now"]:
            payload["availability_status"] = "available"
            payload["availability_label"] = "Available now"
        else:
            payload["availability_status"] = "waiting"
            payload["availability_label"] = "Aired • waiting for source"
        return payload

    results = await asyncio.gather(*(one(series_id) for series_id in series_ids))
    output = [row for row in results if row is not None]
    output.sort(
        key=lambda row: (
            0 if row.get("availability_status") == "available" else 1,
            str(row.get("air_date") or "9999-99-99"),
        )
    )
    return output[: max(1, min(int(limit), 30))]


def _list_payload(
    row: Mapping[str, Any],
    *,
    include_adult: bool,
    media_state_index: Optional[Mapping[tuple[str, int, int, int], Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for raw in list(row.get("items") or []):
        if not isinstance(raw, Mapping):
            continue
        metadata = _metadata(raw)
        if not include_adult and bool(metadata.get("adult", False)):
            continue
        key = (
            str(raw.get("media_type") or ""),
            _safe_int(raw.get("tmdb_id")),
            _safe_int(raw.get("season_number")),
            _safe_int(raw.get("episode_number")),
        )
        current = (
            dict(media_state_index.get(key) or {})
            if media_state_index is not None
            else {}
        )
        merged = dict(raw)
        for field in (
            "favorite",
            "rating",
            "play_count",
            "watchlisted",
            "completed",
            "last_watched_at",
            "last_completed_at",
        ):
            if field in current:
                merged[field] = current.get(field)
        items.append(_media_state_payload(merged))
    return {
        "id": str(row.get("id") or ""),
        "name": _clean(row.get("name"), 80),
        "description": _clean(row.get("description"), 300),
        "position": max(0, _safe_int(row.get("position"))),
        "created_at": str(row.get("created_at") or ""),
        "updated_at": str(row.get("updated_at") or ""),
        "items": items,
    }


async def library_intelligence_snapshot(
    guild_id: int,
    user_id: int,
    *,
    include_adult: bool,
) -> dict[str, Any]:
    media_task = asyncio.create_task(list_user_media(int(user_id)))
    lists_task = asyncio.create_task(list_custom_lists(int(user_id)))
    sessions_task = asyncio.create_task(list_watch_sessions(int(user_id), limit=1000))
    availability_task = asyncio.create_task(_availability_groups(int(guild_id)))
    media_rows, lists, sessions, availability_groups = await asyncio.gather(
        media_task,
        lists_task,
        sessions_task,
        availability_task,
    )
    library = library_snapshot_from_rows(media_rows)
    media_state_index = {
        (
            str(row.get("media_type") or ""),
            _safe_int(row.get("tmdb_id")),
            _safe_int(row.get("season_number")),
            _safe_int(row.get("episode_number")),
        ): row
        for row in media_rows
    }
    stats_task = asyncio.create_task(
        library_stats(
            int(user_id),
            media_rows=media_rows,
            sessions=sessions,
        )
    )
    recommendations_task = asyncio.create_task(
        recommendation_rows(
            int(guild_id),
            int(user_id),
            include_adult=include_adult,
            limit=24,
            media_rows=media_rows,
            availability_groups=availability_groups,
        )
    )
    upcoming_task = asyncio.create_task(
        upcoming_episode_rows(
            int(guild_id),
            int(user_id),
            include_adult=include_adult,
            limit=16,
            media_rows=media_rows,
            availability_groups=availability_groups,
        )
    )
    stats, recommendations, upcoming = await asyncio.gather(
        stats_task,
        recommendations_task,
        upcoming_task,
    )

    return {
        "continue_watching": [
            _media_state_payload(row)
            for row in library.get("continue_watching") or []
            if include_adult or not bool(_metadata(row).get("adult", False))
        ],
        "watchlist": [
            _media_state_payload(row)
            for row in library.get("watchlist") or []
            if include_adult or not bool(_metadata(row).get("adult", False))
        ],
        "favorites": [
            _media_state_payload(row)
            for row in library.get("favorites") or []
            if include_adult or not bool(_metadata(row).get("adult", False))
        ],
        "recently_watched": [
            _media_state_payload(row)
            for row in library.get("recently_watched") or []
            if include_adult or not bool(_metadata(row).get("adult", False))
        ],
        "watch_again": [
            _media_state_payload(row)
            for row in library.get("watch_again") or []
            if include_adult or not bool(_metadata(row).get("adult", False))
        ],
        "rated": [
            _media_state_payload(row)
            for row in library.get("rated") or []
            if include_adult or not bool(_metadata(row).get("adult", False))
        ],
        "history_sessions": [
            _session_payload(
                row,
                current_state=media_state_index.get(
                    (
                        str(row.get("media_type") or ""),
                        _safe_int(row.get("tmdb_id")),
                        _safe_int(row.get("season_number")),
                        _safe_int(row.get("episode_number")),
                    )
                ),
            )
            for row in sessions
            if include_adult or not bool(_metadata(row).get("adult", False))
        ],
        "lists": [
            _list_payload(
                row,
                include_adult=include_adult,
                media_state_index=media_state_index,
            )
            for row in lists
            if isinstance(row, Mapping)
        ],
        "stats": stats,
        "because_you_watched": recommendations["because_you_watched"],
        "recommended": recommendations["recommended"],
        "upcoming": upcoming,
    }


async def group_recommendations(
    guild_id: int,
    user_ids: Sequence[int],
    *,
    include_adult: bool,
    limit: int = 14,
    availability_groups: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    users = list(dict.fromkeys(int(uid) for uid in user_ids if int(uid) > 0))[:20]
    if len(users) < 2:
        return []

    all_rows = await asyncio.gather(
        *(list_user_media(uid) for uid in users),
        return_exceptions=True,
    )
    seed_users: dict[tuple[str, int], set[int]] = defaultdict(set)
    seed_weights: dict[tuple[str, int], float] = defaultdict(float)
    for uid, rows in zip(users, all_rows):
        if isinstance(rows, Exception):
            continue
        for key, _row, score in _seed_rows(rows, limit=3):
            seed_users[key].add(uid)
            seed_weights[key] += score

    ranked_seeds = sorted(
        seed_users,
        key=lambda key: (
            -len(seed_users[key]),
            -seed_weights[key],
            key[0],
            key[1],
        ),
    )[:8]
    if not ranked_seeds:
        return []

    async def resolve(key: tuple[str, int]):
        return key, await get_details(key[0], key[1])

    resolved = await asyncio.gather(
        *(resolve(key) for key in ranked_seeds),
        return_exceptions=True,
    )
    candidate_users: dict[str, set[int]] = defaultdict(set)
    candidate_scores: dict[str, float] = defaultdict(float)
    candidates: dict[str, CinemaMedia] = {}
    blocked = {f"{key[0]}:{key[1]}" for key in ranked_seeds}
    for value in resolved:
        if isinstance(value, Exception):
            continue
        key, details = value
        viewers = seed_users.get(key, set())
        weight = seed_weights.get(key, 0.0)
        for index, media in enumerate(details.recommendations):
            if media.adult and not include_adult:
                continue
            if media.key in blocked:
                continue
            candidates[media.key] = media
            candidate_users[media.key].update(viewers)
            candidate_scores[media.key] += weight + max(0.0, 3.0 - index * 0.1)

    groups = await _availability_groups(int(guild_id))
    title_index, episode_index = _availability_index(groups)
    ranked = sorted(
        candidates.values(),
        key=lambda media: (
            -len(candidate_users.get(media.key, set())),
            -candidate_scores.get(media.key, 0.0),
            -float(media.rating or 0.0),
        ),
    )

    output: list[dict[str, Any]] = []
    for media in ranked[: max(1, min(int(limit), 24))]:
        fit = len(candidate_users.get(media.key, set()))
        payload = media.to_payload()
        payload.update(
            {
                "viewer_fit": fit,
                "viewer_total": len(users),
                "reason": f"Fits {fit} of {len(users)} viewers",
            }
        )
        output.append(
            _decorate_availability(
                payload,
                title_index=title_index,
                episode_index=episode_index,
            )
        )
    return output


async def home_intelligence(
    guild_id: int,
    user_id: int,
    *,
    include_adult: bool,
    group_user_ids: Sequence[int] = (),
) -> dict[str, Any]:
    media_rows, availability_groups = await asyncio.gather(
        list_user_media(int(user_id)),
        _availability_groups(int(guild_id)),
    )
    recommendations_task = asyncio.create_task(
        recommendation_rows(
            int(guild_id),
            int(user_id),
            include_adult=include_adult,
            limit=24,
            media_rows=media_rows,
            availability_groups=availability_groups,
        )
    )
    upcoming_task = asyncio.create_task(
        upcoming_episode_rows(
            int(guild_id),
            int(user_id),
            include_adult=include_adult,
            limit=16,
            media_rows=media_rows,
            availability_groups=availability_groups,
        )
    )
    if group_user_ids:
        group_task = asyncio.create_task(
            group_recommendations(
                int(guild_id),
                group_user_ids,
                include_adult=include_adult,
                limit=14,
                availability_groups=availability_groups,
            )
        )
    else:
        group_task = None

    recommendations, upcoming = await asyncio.gather(
        recommendations_task,
        upcoming_task,
    )
    group_rows = await group_task if group_task is not None else []
    return {
        "because_you_watched": recommendations["because_you_watched"],
        "recommended": recommendations["recommended"],
        "upcoming": upcoming,
        "group_recommendations": group_rows,
    }


__all__ = [
    "group_recommendations",
    "home_intelligence",
    "library_intelligence_snapshot",
    "recommendation_rows",
    "upcoming_episode_rows",
]
