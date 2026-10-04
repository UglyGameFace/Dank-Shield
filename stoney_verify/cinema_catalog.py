from __future__ import annotations

"""Canonical metadata/discovery layer for the full Dank Cinema website.

TMDB provides identity and metadata only. Playback remains owned by the existing
Cinema media-source/torrent pipeline. This module intentionally keeps those
responsibilities separate.
"""

import asyncio
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import urlencode

import aiohttp

from .movie_catalog import tmdb_read_token

_TMDB_BASE = "https://api.themoviedb.org/3"
_TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"
_TIMEOUT = aiohttp.ClientTimeout(total=8.0, connect=3.0, sock_read=6.0)
_MAX_BYTES = 768 * 1024
_CACHE_TTL = 300.0
_CACHE: dict[str, tuple[float, Any]] = {}


@dataclass(frozen=True)
class CinemaMedia:
    media_type: str
    tmdb_id: int
    title: str
    original_title: str = ""
    year: int = 0
    overview: str = ""
    poster_url: str = ""
    backdrop_url: str = ""
    rating: float = 0.0
    popularity: float = 0.0
    adult: bool = False

    @property
    def key(self) -> str:
        return f"{self.media_type}:{self.tmdb_id}"

    def to_payload(self) -> dict[str, Any]:
        return {
            "media_type": self.media_type,
            "tmdb_id": self.tmdb_id,
            "key": self.key,
            "title": self.title,
            "original_title": self.original_title,
            "year": self.year,
            "overview": self.overview,
            "poster_url": self.poster_url,
            "backdrop_url": self.backdrop_url,
            "rating": self.rating,
            "popularity": self.popularity,
            "adult": self.adult,
        }


@dataclass(frozen=True)
class CinemaEpisode:
    series_id: int
    season_number: int
    episode_number: int
    tmdb_id: int
    title: str
    overview: str = ""
    runtime: int = 0
    air_date: str = ""
    still_url: str = ""
    rating: float = 0.0

    @property
    def key(self) -> str:
        return (
            f"tv:{self.series_id}:s{self.season_number}:e{self.episode_number}"
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "media_type": "episode",
            "series_id": self.series_id,
            "season_number": self.season_number,
            "episode_number": self.episode_number,
            "tmdb_id": self.tmdb_id,
            "key": self.key,
            "title": self.title,
            "overview": self.overview,
            "runtime": self.runtime,
            "air_date": self.air_date,
            "still_url": self.still_url,
            "rating": self.rating,
        }


@dataclass(frozen=True)
class CinemaDetails:
    media: CinemaMedia
    tagline: str = ""
    runtime: int = 0
    genres: tuple[str, ...] = ()
    status: str = ""
    cast: tuple[dict[str, Any], ...] = ()
    directors: tuple[str, ...] = ()
    creators: tuple[str, ...] = ()
    trailer_key: str = ""
    seasons: tuple[dict[str, Any], ...] = ()
    recommendations: tuple[CinemaMedia, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            **self.media.to_payload(),
            "tagline": self.tagline,
            "runtime": self.runtime,
            "genres": list(self.genres),
            "status": self.status,
            "cast": [dict(row) for row in self.cast],
            "directors": list(self.directors),
            "creators": list(self.creators),
            "trailer_key": self.trailer_key,
            "trailer_url": (
                f"https://www.youtube.com/watch?v={self.trailer_key}"
                if self.trailer_key
                else ""
            ),
            "seasons": [dict(row) for row in self.seasons],
            "recommendations": [row.to_payload() for row in self.recommendations],
        }


def _clean_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_float(value: Any) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except Exception:
        return 0.0


def _year(value: Any) -> int:
    raw = str(value or "").strip()
    return int(raw[:4]) if len(raw) >= 4 and raw[:4].isdigit() else 0


def _image(path: Any, size: str) -> str:
    raw = str(path or "").strip()
    if not raw.startswith("/") or len(raw) > 220:
        return ""
    if not re.fullmatch(r"(?:w\d+|original)", size):
        return ""
    return f"{_TMDB_IMAGE_BASE}/{size}{raw}"


def _media_from_item(item: Mapping[str, Any], forced_type: str = "") -> Optional[CinemaMedia]:
    media_type = str(forced_type or item.get("media_type") or "").strip().lower()
    if media_type not in {"movie", "tv"}:
        return None
    tmdb_id = _safe_int(item.get("id"))
    if tmdb_id <= 0:
        return None
    title = _clean_text(
        item.get("title")
        or item.get("name")
        or item.get("original_title")
        or item.get("original_name"),
        180,
    )
    if not title:
        return None
    return CinemaMedia(
        media_type=media_type,
        tmdb_id=tmdb_id,
        title=title,
        original_title=_clean_text(
            item.get("original_title") or item.get("original_name"), 180
        ),
        year=_year(item.get("release_date") or item.get("first_air_date")),
        overview=_clean_text(item.get("overview"), 1200),
        poster_url=_image(item.get("poster_path"), "w500"),
        backdrop_url=_image(item.get("backdrop_path"), "w1280"),
        rating=_safe_float(item.get("vote_average")),
        popularity=_safe_float(item.get("popularity")),
        adult=bool(item.get("adult", False)),
    )


async def _read_json(response: aiohttp.ClientResponse) -> Any:
    raw_length = str(response.headers.get("Content-Length") or "").strip()
    if raw_length.isdigit() and int(raw_length) > _MAX_BYTES:
        raise ValueError("TMDB response exceeded the configured size limit.")
    body = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        body.extend(chunk)
        if len(body) > _MAX_BYTES:
            raise ValueError("TMDB response exceeded the configured size limit.")
    return json.loads(body.decode("utf-8"))


async def _request(path: str, *, params: Optional[Mapping[str, Any]] = None, cache_ttl: float = _CACHE_TTL) -> Any:
    token = tmdb_read_token()
    if not token:
        raise RuntimeError("TMDB catalog is not configured.")

    query = {
        str(key): str(value)
        for key, value in dict(params or {}).items()
        if value is not None and str(value) != ""
    }
    key = f"{path}?{urlencode(sorted(query.items()))}"
    cached = _CACHE.get(key)
    if cached and time.monotonic() - cached[0] <= max(0.0, cache_ttl):
        return cached[1]

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "DankShield-Cinema/1.0",
    }
    async with aiohttp.ClientSession(timeout=_TIMEOUT, headers=headers) as session:
        async with session.get(
            f"{_TMDB_BASE}{path}",
            params=query,
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                raise RuntimeError(f"TMDB returned HTTP {response.status}.")
            payload = await _read_json(response)

    if len(_CACHE) > 512:
        stale = sorted(_CACHE.items(), key=lambda item: item[1][0])[:128]
        for stale_key, _value in stale:
            _CACHE.pop(stale_key, None)
    _CACHE[key] = (time.monotonic(), payload)
    return payload


def _media_rows(
    value: Any,
    *,
    forced_type: str = "",
    limit: int = 24,
    include_adult: bool = False,
) -> tuple[CinemaMedia, ...]:
    rows = value if isinstance(value, list) else []
    output: list[CinemaMedia] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        item = _media_from_item(raw, forced_type)
        if item is None or item.key in seen or (item.adult and not include_adult):
            continue
        seen.add(item.key)
        output.append(item)
        if len(output) >= max(1, min(int(limit), 40)):
            break
    return tuple(output)


async def search_catalog(
    query: str,
    *,
    limit: int = 30,
    include_adult: bool = False,
) -> tuple[CinemaMedia, ...]:
    clean = _clean_text(query, 180)
    if not clean:
        return ()
    payload = await _request(
        "/search/multi",
        params={
            "query": clean,
            "include_adult": "true" if include_adult else "false",
            "language": "en-US",
            "page": 1,
        },
        cache_ttl=90.0,
    )
    return _media_rows(
        payload.get("results") if isinstance(payload, Mapping) else [],
        limit=limit,
        include_adult=include_adult,
    )


async def catalog_home() -> dict[str, tuple[CinemaMedia, ...]]:
    async def rows(path: str, forced_type: str = "") -> tuple[CinemaMedia, ...]:
        payload = await _request(
            path,
            params={"language": "en-US", "page": 1},
        )
        return _media_rows(
            payload.get("results") if isinstance(payload, Mapping) else [],
            forced_type=forced_type,
            limit=20,
        )

    trending, popular_movies, popular_tv, top_movies, top_tv = await asyncio.gather(
        rows("/trending/all/day"),
        rows("/movie/popular", "movie"),
        rows("/tv/popular", "tv"),
        rows("/movie/top_rated", "movie"),
        rows("/tv/top_rated", "tv"),
    )
    return {
        "trending": trending,
        "popular_movies": popular_movies,
        "popular_tv": popular_tv,
        "top_movies": top_movies,
        "top_tv": top_tv,
    }


def _credits(payload: Mapping[str, Any]) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...], tuple[str, ...]]:
    cast_rows = payload.get("cast") if isinstance(payload.get("cast"), list) else []
    cast: list[dict[str, Any]] = []
    for raw in cast_rows[:20]:
        if not isinstance(raw, Mapping):
            continue
        name = _clean_text(raw.get("name"), 100)
        if not name:
            continue
        cast.append(
            {
                "name": name,
                "character": _clean_text(raw.get("character"), 100),
                "profile_url": _image(raw.get("profile_path"), "w185"),
            }
        )

    crew_rows = payload.get("crew") if isinstance(payload.get("crew"), list) else []
    directors: list[str] = []
    for raw in crew_rows:
        if not isinstance(raw, Mapping) or str(raw.get("job") or "") != "Director":
            continue
        name = _clean_text(raw.get("name"), 100)
        if name and name not in directors:
            directors.append(name)
        if len(directors) >= 5:
            break

    creators: list[str] = []
    return tuple(cast), tuple(directors), tuple(creators)


def _trailer(payload: Mapping[str, Any]) -> str:
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    ranked: list[tuple[int, str]] = []
    for raw in results:
        if not isinstance(raw, Mapping):
            continue
        if str(raw.get("site") or "") != "YouTube":
            continue
        key = str(raw.get("key") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{6,32}", key):
            continue
        kind = str(raw.get("type") or "")
        official = bool(raw.get("official", False))
        score = (4 if official else 0) + (3 if kind == "Trailer" else 2 if kind == "Teaser" else 0)
        ranked.append((score, key))
    ranked.sort(reverse=True)
    return ranked[0][1] if ranked else ""


async def get_details(media_type: str, tmdb_id: int) -> CinemaDetails:
    kind = str(media_type or "").strip().lower()
    if kind not in {"movie", "tv"}:
        raise ValueError("Unsupported Cinema media type.")
    numeric_id = int(tmdb_id)
    if numeric_id <= 0:
        raise ValueError("Invalid TMDB id.")

    payload = await _request(
        f"/{kind}/{numeric_id}",
        params={
            "language": "en-US",
            "append_to_response": "credits,videos,recommendations",
        },
    )
    if not isinstance(payload, Mapping):
        raise RuntimeError("TMDB returned an unexpected details response.")

    media = _media_from_item(payload, kind)
    if media is None:
        raise RuntimeError("TMDB details could not be normalized.")

    credits_payload = payload.get("credits") if isinstance(payload.get("credits"), Mapping) else {}
    cast, directors, _unused = _credits(credits_payload)
    creators = tuple(
        _clean_text(row.get("name"), 100)
        for row in list(payload.get("created_by") or [])[:8]
        if isinstance(row, Mapping) and _clean_text(row.get("name"), 100)
    )

    runtime = _safe_int(payload.get("runtime"))
    if kind == "tv" and runtime <= 0:
        runtimes = payload.get("episode_run_time")
        if isinstance(runtimes, list) and runtimes:
            runtime = max(0, _safe_int(runtimes[0]))

    genres = tuple(
        _clean_text(row.get("name"), 60)
        for row in list(payload.get("genres") or [])[:12]
        if isinstance(row, Mapping) and _clean_text(row.get("name"), 60)
    )

    seasons: list[dict[str, Any]] = []
    if kind == "tv":
        for row in list(payload.get("seasons") or [])[:80]:
            if not isinstance(row, Mapping):
                continue
            number = _safe_int(row.get("season_number"), -1)
            if number < 0:
                continue
            seasons.append(
                {
                    "season_number": number,
                    "name": _clean_text(row.get("name"), 120) or f"Season {number}",
                    "episode_count": max(0, _safe_int(row.get("episode_count"))),
                    "air_date": str(row.get("air_date") or "")[:10],
                    "poster_url": _image(row.get("poster_path"), "w342"),
                }
            )

    rec_payload = payload.get("recommendations") if isinstance(payload.get("recommendations"), Mapping) else {}
    recommendations = _media_rows(
        rec_payload.get("results") if isinstance(rec_payload, Mapping) else [],
        forced_type=kind,
        limit=18,
    )
    videos = payload.get("videos") if isinstance(payload.get("videos"), Mapping) else {}

    return CinemaDetails(
        media=media,
        tagline=_clean_text(payload.get("tagline"), 280),
        runtime=runtime,
        genres=genres,
        status=_clean_text(payload.get("status"), 80),
        cast=cast,
        directors=directors,
        creators=creators,
        trailer_key=_trailer(videos),
        seasons=tuple(seasons),
        recommendations=recommendations,
    )


async def get_season(series_id: int, season_number: int) -> tuple[CinemaEpisode, ...]:
    series = int(series_id)
    season = int(season_number)
    if series <= 0 or season < 0:
        raise ValueError("Invalid series or season.")
    payload = await _request(
        f"/tv/{series}/season/{season}",
        params={"language": "en-US"},
    )
    rows = payload.get("episodes") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        return ()
    episodes: list[CinemaEpisode] = []
    for row in rows[:100]:
        if not isinstance(row, Mapping):
            continue
        episode_number = _safe_int(row.get("episode_number"), -1)
        tmdb_id = _safe_int(row.get("id"))
        if episode_number < 0 or tmdb_id <= 0:
            continue
        title = _clean_text(row.get("name"), 180) or f"Episode {episode_number}"
        episodes.append(
            CinemaEpisode(
                series_id=series,
                season_number=season,
                episode_number=episode_number,
                tmdb_id=tmdb_id,
                title=title,
                overview=_clean_text(row.get("overview"), 900),
                runtime=max(0, _safe_int(row.get("runtime"))),
                air_date=str(row.get("air_date") or "")[:10],
                still_url=_image(row.get("still_path"), "w780"),
                rating=_safe_float(row.get("vote_average")),
            )
        )
    return tuple(episodes)


async def get_next_episode(
    series_id: int,
    season_number: int,
    episode_number: int,
) -> Optional[CinemaEpisode]:
    """Resolve the canonical episode immediately after the current TV episode."""

    series = int(series_id)
    season = max(0, int(season_number))
    episode = max(0, int(episode_number))
    if series <= 0:
        raise ValueError("Invalid series id.")

    current_season = await get_season(series, season)
    later = sorted(
        (
            row
            for row in current_season
            if int(row.episode_number) > episode
        ),
        key=lambda row: int(row.episode_number),
    )
    if later:
        return later[0]

    details = await get_details("tv", series)
    later_seasons = sorted(
        (
            int(row.get("season_number") or 0)
            for row in details.seasons
            if int(row.get("season_number") or 0) > season
            and int(row.get("episode_count") or 0) > 0
        )
    )
    for next_season in later_seasons:
        episodes = sorted(
            await get_season(series, next_season),
            key=lambda row: int(row.episode_number),
        )
        if episodes:
            return episodes[0]
    return None


async def recommendations_for_history(
    history: Sequence[Mapping[str, Any]],
    *,
    limit: int = 20,
) -> tuple[CinemaMedia, ...]:
    seeds: list[tuple[str, int]] = []
    seen_seed: set[tuple[str, int]] = set()
    for row in history:
        kind = str(row.get("media_type") or "").strip().lower()
        tmdb_id = _safe_int(row.get("tmdb_id"))
        key = (kind, tmdb_id)
        if kind not in {"movie", "tv"} or tmdb_id <= 0 or key in seen_seed:
            continue
        seen_seed.add(key)
        seeds.append(key)
        if len(seeds) >= 3:
            break

    if not seeds:
        return ()

    async def one(kind: str, tmdb_id: int) -> tuple[CinemaMedia, ...]:
        payload = await _request(
            f"/{kind}/{tmdb_id}/recommendations",
            params={"language": "en-US", "page": 1},
            cache_ttl=600.0,
        )
        return _media_rows(
            payload.get("results") if isinstance(payload, Mapping) else [],
            forced_type=kind,
            limit=12,
        )

    groups = await asyncio.gather(*(one(kind, tmdb_id) for kind, tmdb_id in seeds), return_exceptions=True)
    output: list[CinemaMedia] = []
    seen: set[str] = set()
    blocked = {f"{kind}:{tmdb_id}" for kind, tmdb_id in seeds}
    for group in groups:
        if isinstance(group, Exception):
            continue
        for media in group:
            if media.key in seen or media.key in blocked:
                continue
            seen.add(media.key)
            output.append(media)
            if len(output) >= max(1, min(int(limit), 30)):
                return tuple(output)
    return tuple(output)


__all__ = [
    "CinemaDetails",
    "CinemaEpisode",
    "CinemaMedia",
    "catalog_home",
    "get_details",
    "get_next_episode",
    "get_season",
    "recommendations_for_history",
    "search_catalog",
]
