from __future__ import annotations

"""Durable, conservatively normalized Cinema feed discoveries."""

import asyncio
import hashlib
import re
import time
from typing import Any, Mapping, Sequence

from .cinema_catalog import CinemaDetails, CinemaMedia, get_details, search_catalog
from .cinema_storage import execute, rows, utc_now
from .media_metadata import parse_release_name

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


def _looks_like_episode_release(value: Any) -> bool:
    text = " ".join(_tokens(value))
    return bool(
        re.search(r"\bs\d{1,2}e\d{1,3}\b", text)
        or re.search(r"\b\d{1,2}x\d{1,3}\b", text)
    )


def _discovery_search_queries(title: str) -> tuple[str, ...]:
    primary = _clean_search_title(title)
    if not primary:
        return ()
    queries: list[str] = [primary]
    if _looks_like_episode_release(title):
        stripped = re.sub(
            r"\b(?:19|20)\d{2}\b",
            " ",
            primary,
        )
        stripped = " ".join(stripped.split())
        if stripped and stripped not in queries:
            queries.insert(0, stripped)
    return tuple(queries)


async def _resolve_media(title: str, category: str) -> CinemaMedia | None:
    queries = _discovery_search_queries(title)
    if not queries:
        return None

    wanted_type = (
        "tv"
        if category in {"tv", "anime"} or _looks_like_episode_release(title)
        else ""
    )
    seen: set[str] = set()
    for query in queries:
        try:
            candidates = await search_catalog(query, limit=8, include_adult=False)
        except Exception:
            continue

        for media in candidates:
            if media.key in seen:
                continue
            seen.add(media.key)
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


async def enrich_discovery_rows(
    guild_id: int,
    discovery_rows: Sequence[Mapping[str, Any]],
    *,
    max_items: int = 4,
    retry_seconds: int = 1800,
) -> list[dict[str, Any]]:
    gid = int(guild_id)
    now_epoch = int(time.time())
    output = [dict(row) for row in discovery_rows if isinstance(row, Mapping)]
    candidates: list[tuple[int, dict[str, Any], str, str]] = []

    for index, row in enumerate(output):
        if int(row.get("tmdb_id") or 0) > 0:
            continue
        metadata = (
            dict(row.get("metadata") or {})
            if isinstance(row.get("metadata"), Mapping)
            else {}
        )
        attempted_at = int(metadata.get("enrichment_attempted_at") or 0)
        if attempted_at and now_epoch - attempted_at < max(60, int(retry_seconds)):
            continue
        release_title = str(
            metadata.get("release_title")
            or row.get("title")
            or ""
        ).strip()
        if not release_title:
            continue
        category = str(metadata.get("category") or "custom")
        candidates.append((index, row, release_title, category))
        if len(candidates) >= max(1, min(int(max_items), 8)):
            break

    if not candidates:
        return output

    semaphore = asyncio.Semaphore(2)

    async def resolve(
        index: int,
        row: dict[str, Any],
        release_title: str,
        category: str,
    ) -> tuple[int, dict[str, Any], CinemaMedia | None]:
        async with semaphore:
            return index, row, await _resolve_media(release_title, category)

    resolved = await asyncio.gather(
        *(resolve(*candidate) for candidate in candidates)
    )
    updates: list[dict[str, Any]] = []

    for index, row, media in resolved:
        metadata = (
            dict(row.get("metadata") or {})
            if isinstance(row.get("metadata"), Mapping)
            else {}
        )
        metadata["enrichment_attempted_at"] = now_epoch
        updated = dict(row)
        updated["metadata"] = metadata
        if media is not None:
            updated["title"] = media.title[:180]
            updated["media_type"] = media.media_type
            updated["tmdb_id"] = int(media.tmdb_id)
            metadata.update(
                {
                    "poster_url": media.poster_url,
                    "backdrop_url": media.backdrop_url,
                    "year": media.year,
                    "overview": media.overview,
                    "rating": media.rating,
                }
            )
        output[index] = updated
        updates.append(
            {
                "guild_id": gid,
                "source_id": str(updated.get("source_id") or "")[:100],
                "discovery_key": str(updated.get("discovery_key") or "")[:32],
                "title": str(updated.get("title") or "")[:180],
                "media_type": updated.get("media_type"),
                "tmdb_id": int(updated.get("tmdb_id") or 0) or None,
                "metadata": metadata,
                "playable": bool(updated.get("playable", True)),
                "last_seen_at": str(updated.get("last_seen_at") or utc_now()),
            }
        )

    if updates:
        def write(client: Any):
            try:
                return (
                    client.table(TABLE)
                    .upsert(
                        updates,
                        on_conflict="guild_id,source_id,discovery_key",
                        ignore_duplicates=False,
                    )
                    .execute()
                )
            except TypeError:
                return client.table(TABLE).upsert(updates).execute()

        try:
            await execute(f"enrich Cinema feed discoveries {gid}", write)
        except Exception:
            pass

    return output


async def record_feed_discoveries(
    guild_id: int,
    *,
    source_id: str,
    source_label: str,
    category: str,
    titles: Sequence[str],
    release_metadata: Mapping[str, Mapping[str, Any]] | None = None,
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

    async def resolve(
        title: str,
    ) -> tuple[str, CinemaMedia | None, CinemaDetails | None]:
        async with semaphore:
            media = await _resolve_media(title, str(category or "custom"))
            details = None
            if media is not None:
                try:
                    details = await get_details(media.media_type, media.tmdb_id)
                except Exception:
                    details = None
            return title, media, details

    resolved = await asyncio.gather(*(resolve(title) for title in clean_titles))
    now = utc_now()
    payloads: list[dict[str, Any]] = []
    for title, media, details in resolved:
        extra = {}
        if isinstance(release_metadata, Mapping):
            candidate = release_metadata.get(title.casefold())
            if isinstance(candidate, Mapping):
                extra = dict(candidate)
        metadata: dict[str, Any] = {
            "source_label": str(source_label or "")[:80],
            "category": str(category or "custom")[:40],
            "release_title": title,
            "release_name": parse_release_name(title),
            "seeds": max(0, int(extra.get("seeds") or 0)),
            "leechers": max(0, int(extra.get("leechers") or 0)),
            "peers": max(0, int(extra.get("peers") or 0)),
            "file_size": max(0, int(extra.get("file_size") or 0)),
            "languages": [
                " ".join(str(item or "").split()).lower()[:24]
                for item in list(extra.get("languages") or [])[:12]
                if str(item or "").strip()
            ],
        }
        source_reported = extra.get("source_reported")
        if isinstance(source_reported, Mapping):
            metadata["source_reported"] = {
                str(key)[:80]: value
                for key, value in list(source_reported.items())[:32]
                if isinstance(value, (str, int, float, bool)) or value is None
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
            if details is not None:
                metadata.update(
                    {
                        "genres": list(details.genres)[:12],
                        "studios": list(details.studios)[:16],
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


async def page_discoveries(
    guild_id: int,
    *,
    query: str = "",
    page: int = 1,
    page_size: int = 8,
) -> dict[str, Any]:
    gid = int(guild_id)
    clean_query = " ".join(str(query or "").split())[:120]
    filter_query = " ".join(
        re.sub(r"[^A-Za-z0-9 _.'-]+", " ", clean_query).split()
    )[:120]
    size = max(1, min(int(page_size), 24))
    current_page = max(1, int(page))
    offset = (current_page - 1) * size

    def read(client: Any):
        request = (
            client.table(TABLE)
            .select("*", count="exact")
            .eq("guild_id", gid)
        )
        if filter_query:
            request = request.or_(
                ",".join(
                    (
                        f"title.ilike.%{filter_query}%",
                        f"metadata->>release_title.ilike.%{filter_query}%",
                    )
                )
            )
        return (
            request
            .order("first_seen_at", desc=True)
            .range(offset, offset + size - 1)
            .execute()
        )

    response = await execute(
        f"page Cinema feed discoveries {gid}",
        read,
    )
    page_rows = rows(response)
    raw_count = getattr(response, "count", None)
    total = int(raw_count) if raw_count is not None else offset + len(page_rows)
    total_pages = max(1, (total + size - 1) // size) if total else 1
    if current_page > total_pages and total:
        current_page = total_pages

    return {
        "rows": page_rows,
        "query": clean_query,
        "page": current_page,
        "page_size": size,
        "total": total,
        "total_pages": total_pages,
        "has_previous": current_page > 1,
        "has_next": current_page < total_pages,
    }


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
    "enrich_discovery_rows",
    "list_recent_discoveries",
    "page_discoveries",
    "record_feed_discoveries",
    "search_discoveries",
]
