from __future__ import annotations

"""Canonical movie-title catalog lookup for Movie Night.

Catalog providers identify a movie and provide metadata. They never provide or
resolve playback media. Playable releases still come from the Movie Night media
provider pipeline or from a host-supplied magnet/.torrent file.
"""

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlencode

import aiohttp

_TMDB_SEARCH_URL = "https://api.themoviedb.org/3/search/movie"
_TMDB_WATCH_URL = "https://api.themoviedb.org/3/movie/{movie_id}/watch/providers"
_TMDB_POSTER_BASE = "https://image.tmdb.org/t/p/w342"
_TMDB_TIMEOUT_SECONDS = 7.0
_TMDB_MAX_RESPONSE_BYTES = 512 * 1024


@dataclass(frozen=True)
class CatalogMovie:
    provider: str
    provider_id: str
    title: str
    original_title: str = ""
    year: int = 0
    overview: str = ""
    poster_url: str = ""
    popularity: float = 0.0
    adult: bool = False

    def to_metadata(self) -> dict[str, Any]:
        return {
            "catalog_provider": self.provider,
            "catalog_id": self.provider_id,
            "title": self.title,
            "original_title": self.original_title,
            "year": int(self.year),
            "overview": self.overview,
            "poster_url": self.poster_url,
            "popularity": float(self.popularity),
            "adult": bool(self.adult),
        }


@dataclass(frozen=True)
class CatalogSearchOutcome:
    movies: tuple[CatalogMovie, ...]
    error: str = ""

@dataclass(frozen=True)
class CatalogWatchAvailability:
    region: str
    link: str = ""
    free: tuple[str, ...] = ()
    ads: tuple[str, ...] = ()
    flatrate: tuple[str, ...] = ()
    rent: tuple[str, ...] = ()
    buy: tuple[str, ...] = ()

    def to_metadata(self) -> dict[str, Any]:
        return {
            "region": self.region,
            "link": self.link,
            "free": list(self.free),
            "ads": list(self.ads),
            "flatrate": list(self.flatrate),
            "rent": list(self.rent),
            "buy": list(self.buy),
            "attribution": "JustWatch via TMDB",
        }


def tmdb_read_token() -> str:
    return str(os.getenv("DANK_TMDB_READ_TOKEN", "") or "").strip()


def tmdb_catalog_ready() -> bool:
    return bool(tmdb_read_token())

def tmdb_watch_region() -> str:
    region = str(os.getenv("DANK_TMDB_WATCH_REGION", "US") or "US").strip().upper()
    return region if re.fullmatch(r"[A-Z]{2}", region) else "US"


def _safe_year(value: Any) -> int:
    text = str(value or "").strip()
    if len(text) < 4 or not text[:4].isdigit():
        return 0
    year = int(text[:4])
    return year if 1880 <= year <= 2200 else 0


def _clean_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _tmdb_movie_from_item(item: Mapping[str, Any]) -> CatalogMovie | None:
    raw_id = item.get("id")
    try:
        provider_id = str(int(raw_id))
    except Exception:
        return None

    title = _clean_text(item.get("title") or item.get("original_title"), 180)
    if not title:
        return None

    poster_path = str(item.get("poster_path") or "").strip()
    poster_url = ""
    if poster_path.startswith("/") and len(poster_path) <= 200:
        poster_url = f"{_TMDB_POSTER_BASE}{poster_path}"

    try:
        popularity = max(0.0, float(item.get("popularity") or 0.0))
    except Exception:
        popularity = 0.0

    return CatalogMovie(
        provider="tmdb",
        provider_id=provider_id,
        title=title,
        original_title=_clean_text(item.get("original_title"), 180),
        year=_safe_year(item.get("release_date")),
        overview=_clean_text(item.get("overview"), 900),
        poster_url=poster_url,
        popularity=popularity,
        adult=bool(item.get("adult", False)),
    )


def _provider_names(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    names: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, Mapping):
            continue
        name = _clean_text(item.get("provider_name"), 80)
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        names.append(name)
    return tuple(names[:20])


def _watch_availability_from_payload(
    payload: Any,
    *,
    region: str,
) -> CatalogWatchAvailability:
    results = payload.get("results") if isinstance(payload, Mapping) else None
    row = results.get(region) if isinstance(results, Mapping) else None
    if not isinstance(row, Mapping):
        return CatalogWatchAvailability(region=region)

    raw_link = str(row.get("link") or "").strip()
    link = raw_link if raw_link.startswith("https://www.themoviedb.org/") else ""
    return CatalogWatchAvailability(
        region=region,
        link=link,
        free=_provider_names(row.get("free")),
        ads=_provider_names(row.get("ads")),
        flatrate=_provider_names(row.get("flatrate")),
        rent=_provider_names(row.get("rent")),
        buy=_provider_names(row.get("buy")),
    )

async def _read_json_limited(response: aiohttp.ClientResponse) -> Any:
    length_text = str(response.headers.get("Content-Length") or "").strip()
    if length_text.isdigit() and int(length_text) > _TMDB_MAX_RESPONSE_BYTES:
        raise ValueError("TMDB response exceeded the configured size limit.")

    payload = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        payload.extend(chunk)
        if len(payload) > _TMDB_MAX_RESPONSE_BYTES:
            raise ValueError("TMDB response exceeded the configured size limit.")
    try:
        return json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise ValueError("TMDB did not return valid JSON.") from exc


async def search_tmdb_movies(
    query: str,
    *,
    limit: int = 8,
    include_adult: bool = False,
) -> CatalogSearchOutcome:
    token = tmdb_read_token()
    if not token:
        return CatalogSearchOutcome(
            movies=(),
            error="TMDB catalog search is not configured by the bot owner.",
        )

    clean_query = _clean_text(query, 180)
    if not clean_query:
        return CatalogSearchOutcome(movies=(), error="Movie search query is empty.")

    params = {
        "query": clean_query,
        "include_adult": "true" if include_adult else "false",
        "language": "en-US",
        "page": "1",
    }
    timeout = aiohttp.ClientTimeout(
        total=_TMDB_TIMEOUT_SECONDS,
        connect=3.0,
        sock_read=5.0,
    )
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "DankShield-MovieNight/1.0",
    }

    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(
                f"{_TMDB_SEARCH_URL}?{urlencode(params)}",
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    return CatalogSearchOutcome(
                        movies=(),
                        error=f"TMDB catalog returned HTTP {response.status}.",
                    )
                payload = await _read_json_limited(response)
    except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
        return CatalogSearchOutcome(
            movies=(),
            error=f"TMDB catalog: {type(exc).__name__}: {exc}",
        )

    raw_results = payload.get("results") if isinstance(payload, Mapping) else None
    if not isinstance(raw_results, list):
        return CatalogSearchOutcome(
            movies=(),
            error="TMDB catalog returned an unexpected response.",
        )

    movies: list[CatalogMovie] = []
    seen: set[str] = set()
    for raw in raw_results:
        if not isinstance(raw, Mapping):
            continue
        movie = _tmdb_movie_from_item(raw)
        if movie is None or movie.provider_id in seen:
            continue
        seen.add(movie.provider_id)
        movies.append(movie)
        if len(movies) >= max(1, min(int(limit), 12)):
            break
    return CatalogSearchOutcome(movies=tuple(movies))


async def get_tmdb_watch_availability(
    movie_id: str | int,
    *,
    region: str | None = None,
) -> CatalogWatchAvailability:
    selected_region = str(region or tmdb_watch_region()).strip().upper()
    if not re.fullmatch(r"[A-Z]{2}", selected_region):
        selected_region = tmdb_watch_region()

    token = tmdb_read_token()
    if not token:
        return CatalogWatchAvailability(region=selected_region)
    try:
        numeric_id = int(movie_id)
    except Exception:
        return CatalogWatchAvailability(region=selected_region)
    if numeric_id <= 0:
        return CatalogWatchAvailability(region=selected_region)

    timeout = aiohttp.ClientTimeout(
        total=_TMDB_TIMEOUT_SECONDS,
        connect=3.0,
        sock_read=5.0,
    )
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "DankShield-MovieNight/1.0",
    }

    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(
                _TMDB_WATCH_URL.format(movie_id=numeric_id),
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    return CatalogWatchAvailability(region=selected_region)
                payload = await _read_json_limited(response)
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return CatalogWatchAvailability(region=selected_region)

    return _watch_availability_from_payload(payload, region=selected_region)

__all__ = [
    "tmdb_watch_region",
    "get_tmdb_watch_availability",
    "CatalogWatchAvailability",
    "CatalogMovie",
    "CatalogSearchOutcome",
    "search_tmdb_movies",
    "tmdb_catalog_ready",
    "tmdb_read_token",
]
