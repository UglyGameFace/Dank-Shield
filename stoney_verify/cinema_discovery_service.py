from __future__ import annotations

"""Durable, conservatively normalized Cinema feed discoveries."""

import asyncio
import hashlib
import re
from typing import Any, Mapping, Sequence

from .cinema_catalog import CinemaMedia, search_catalog
from .cinema_storage import execute, rows, utc_now

TABLE = "dank_cinema_feed_discoveries"

_TECHNICAL = {
    "1080p",
    "2160p",
    "720p",
    "480p",
    "4k",
    "uhd",
    "hdr",
    "hdr10",
    "dv",
    "dolby",
    "vision",
    "bluray",
    "blu",
    "ray",
    "bdrip",
    "brrip",
    "webrip",
    "web",
    "webdl",
    "dl",
    "hdtv",
    "x264",
    "x265",
    "h264",
    "h265",
    "hevc",
    "av1",
    "aac",
    "ac3",
    "eac3",
    "dts",
    "remux",
    "proper",
    "repack",
    "extended",
    "multi",
    "dubbed",
    "subbed",
}


def _tokens(value: Any) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(value or "").casefold())


def _year(tokens: Sequence[str]) -> int:
    for token in tokens:
        if len(token) == 4 and token.isdigit() and 1900 <= int(token) <= 2100:
            return int(token)
    return 0


def _clean_search_title(value: Any) -> str:
    tokens = _tokens(value)
    output: list[str] = []
    for token in tokens:
        if token in _TECHNICAL:
            break
        if re.fullmatch(r"s\d{1,2}e\d{1,3}", token):
            break
        if re.fullmatch(r"\d{1,2}x\d{1,3}", token):
            break
        if len(token) == 4 and token.isdigit() and 1900 <= int(token) <= 2100:
            output.append(token)
            break
        output.append(token)
    return " ".join(output[:18]).strip()


def _matches_release(media: CinemaMedia, release_title: str) -> bool:
    release_tokens = set(_tokens(release_title))
    title_tokens = [token for token in _tokens(media.title) if token not in _TECHNICAL]
    if not title_tokens or not all(token in release_tokens for token in title_tokens):
        return False
    release_year = _year(list(release_tokens))
    if media.year and release_year and int(media.year) != release_year:
        return False
    return True


async def _resolve_media(title: str, category: str) -> CinemaMedia | None:
    query = _clean_search_title(title)
    if not query:
        return None
    try:
        candidates = await search_catalog(query, limit=5, include_adult=False)
    except Exception:
        return None

    wanted_type = "tv" if category in {"tv", "anime"} else ""
    for media in candidates:
        if wanted_type and media.media_type != wanted_type:
            continue
        if _matches_release(media, title):
            return media
    for media in candidates:
        if wanted_type and media.media_type != wanted_type:
            continue
        normalized_query = set(_tokens(query))
        normalized_title = set(_tokens(media.title))
        if normalized_title and normalized_title == normalized_query:
            return media
    return None


def _discovery_key(title: str) -> str:
    normalized = " ".join(_tokens(title))[:500]
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


async def record_feed_discoveries(
    guild_id: int,
    *,
    source_id: str,
    source_label: str,
    category: str,
    titles: Sequence[str],
) -> list[dict[str, Any]]:
    gid = int(guild_id)
    clean_titles: list[str] = []
    seen: set[str] = set()
    for raw in titles:
        title = " ".join(str(raw or "").split())[:240]
        key = title.casefold()
        if not title or key in seen:
            continue
        seen.add(key)
        clean_titles.append(title)
        if len(clean_titles) >= 12:
            break

    semaphore = asyncio.Semaphore(3)

    async def resolve(title: str) -> tuple[str, CinemaMedia | None]:
        async with semaphore:
            return title, await _resolve_media(title, str(category or "custom"))

    resolved = await asyncio.gather(*(resolve(title) for title in clean_titles))
    now = utc_now()
    payloads: list[dict[str, Any]] = []
    for title, media in resolved:
        metadata: dict[str, Any] = {
            "source_label": str(source_label or "")[:80],
            "category": str(category or "custom")[:40],
            "release_title": title,
        }
        media_type = None
        tmdb_id = None
        display_title = title
        if media is not None:
            media_type = media.media_type
            tmdb_id = int(media.tmdb_id)
            display_title = media.title
            metadata.update(
                {
                    "poster_url": media.poster_url,
                    "backdrop_url": media.backdrop_url,
                    "year": media.year,
                    "overview": media.overview,
                    "rating": media.rating,
                }
            )
        payloads.append(
            {
                "guild_id": gid,
                "source_id": str(source_id or "")[:100],
                "discovery_key": _discovery_key(title),
                "title": display_title[:180],
                "media_type": media_type,
                "tmdb_id": tmdb_id,
                "metadata": metadata,
                "playable": True,
                "last_seen_at": now,
            }
        )

    if not payloads:
        return []

    def write(client: Any):
        try:
            return (
                client.table(TABLE)
                .upsert(
                    payloads,
                    on_conflict="guild_id,source_id,discovery_key",
                    ignore_duplicates=False,
                )
                .execute()
            )
        except TypeError:
            return client.table(TABLE).upsert(payloads).execute()

    await execute(f"write Cinema feed discoveries {gid}", write)
    return payloads


async def list_recent_discoveries(
    guild_id: int,
    *,
    limit: int = 30,
) -> list[dict[str, Any]]:
    gid = int(guild_id)

    def read(client: Any):
        return (
            client.table(TABLE)
            .select("*")
            .eq("guild_id", gid)
            .order("first_seen_at", desc=True)
            .limit(max(1, min(int(limit), 100)))
            .execute()
        )

    return rows(await execute(f"read Cinema feed discoveries {gid}", read))


async def search_discoveries(
    guild_id: int,
    query: str,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    needle = " ".join(str(query or "").split())[:120]
    if not needle:
        return []
    gid = int(guild_id)

    def read(client: Any):
        return (
            client.table(TABLE)
            .select("*")
            .eq("guild_id", gid)
            .ilike("title", f"%{needle}%")
            .order("last_seen_at", desc=True)
            .limit(max(1, min(int(limit), 50)))
            .execute()
        )

    return rows(await execute(f"search Cinema feed discoveries {gid}", read))


__all__ = [
    "list_recent_discoveries",
    "record_feed_discoveries",
    "search_discoveries",
]
