from __future__ import annotations

"""Canonical Dank Cinema media identity and provider-release matching.

TMDB owns title/series/episode identity. Provider results may carry release
metadata, but they never redefine the selected catalog item.
"""

import re
from typing import Any, Mapping, Optional

from .cinema_catalog import CinemaEpisode, CinemaMedia
from .media_source_resolver import MediaSourceSearchOutcome


def _clean(value: Any, limit: int = 180) -> str:
    return " ".join(str(value or "").split())[:limit]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def normalized_title_tokens(value: Any) -> tuple[str, ...]:
    return tuple(re.findall(r"[a-z0-9]+", str(value or "").casefold()))


_EXPLICIT_ADULT_RE = re.compile(
    r"(?:^|[^a-z0-9])(?:xxx|porn|pornographic|adult[ _-]?video)(?:$|[^a-z0-9])",
    re.IGNORECASE,
)


def variant_is_explicit_adult(variant: Any) -> bool:
    if _EXPLICIT_ADULT_RE.search(str(getattr(variant, "title", "") or "")):
        return True
    metadata = getattr(variant, "metadata", None)
    metadata = metadata if isinstance(metadata, Mapping) else {}
    values: list[Any] = [metadata.get("category"), metadata.get("type")]
    source_reported = (
        metadata.get("source_reported")
        if isinstance(metadata.get("source_reported"), Mapping)
        else {}
    )
    values.extend(
        source_reported.get(key)
        for key in ("category", "type", "tags", "classification")
    )
    explicit_labels = {"adult", "xxx", "porn", "pornographic", "adult video"}
    for value in values:
        if not value:
            continue
        clean = " ".join(
            str(value).casefold().replace("_", " ").replace("-", " ").split()
        )
        if clean in explicit_labels or _EXPLICIT_ADULT_RE.search(clean):
            return True
    return False


def filter_adult_provider_results(
    outcome: MediaSourceSearchOutcome,
    *,
    enabled: bool,
) -> MediaSourceSearchOutcome:
    if enabled:
        return outcome
    kept = tuple(
        variant
        for variant in outcome.variants
        if not variant_is_explicit_adult(variant)
    )
    removed = len(outcome.variants) - len(kept)
    if removed <= 0:
        return outcome
    errors = list(outcome.errors)
    errors.append(
        f"Filtered {removed} explicit adult provider release(s) by server Cinema setting."
    )
    return MediaSourceSearchOutcome(
        variants=kept,
        errors=tuple(errors[:20]),
    )


def parse_episode_query(value: Any) -> Optional[tuple[str, int, int]]:
    """Parse the episode notation shared by website, Theater, and providers."""

    clean = " ".join(str(value or "").split())
    patterns = (
        re.compile(r"^(.+?)\s+s(\d{1,2})\s*e(\d{1,3})(?:\b|$)", re.IGNORECASE),
        re.compile(r"^(.+?)\s+(\d{1,2})x(\d{1,3})(?:\b|$)", re.IGNORECASE),
        re.compile(
            r"^(.+?)\s+season\s+(\d{1,2})\s+episode\s+(\d{1,3})(?:\b|$)",
            re.IGNORECASE,
        ),
    )
    for pattern in patterns:
        match = pattern.search(clean)
        if not match:
            continue
        title = " ".join(match.group(1).split())[:160]
        season = _safe_int(match.group(2), -1)
        episode = _safe_int(match.group(3), -1)
        if title and season >= 0 and episode > 0:
            return title, season, episode
    return None


def catalog_metadata(media: CinemaMedia) -> dict[str, Any]:
    return {
        "catalog_provider": "tmdb",
        "catalog_id": str(int(media.tmdb_id)),
        "tmdb_id": int(media.tmdb_id),
        "media_type": str(media.media_type),
        "title": str(media.title),
        "original_title": str(media.original_title or ""),
        "year": int(media.year or 0),
        "overview": str(media.overview or ""),
        "poster_url": str(media.poster_url or ""),
        "backdrop_url": str(media.backdrop_url or ""),
        "popularity": float(media.popularity or 0.0),
        "rating": float(media.rating or 0.0),
        "adult": bool(media.adult),
    }


def episode_catalog_metadata(
    *,
    series: CinemaMedia,
    episode: CinemaEpisode,
) -> dict[str, Any]:
    season = max(0, int(episode.season_number))
    number = max(0, int(episode.episode_number))
    display_title = (
        f"{series.title} S{season:02d}E{number:02d}"
        + (f" • {episode.title}" if episode.title else "")
    )
    return {
        "catalog_provider": "tmdb",
        "catalog_id": str(int(episode.tmdb_id)),
        "tmdb_id": int(episode.tmdb_id),
        "media_type": "episode",
        "title": display_title[:180],
        "episode_title": str(episode.title or "")[:180],
        "series_id": int(series.tmdb_id),
        "series_title": str(series.title)[:180],
        "season_number": season,
        "episode_number": number,
        "year": int(series.year or 0),
        "overview": str(episode.overview or "")[:900],
        "poster_url": str(series.poster_url or ""),
        "backdrop_url": str(episode.still_url or series.backdrop_url or ""),
        "still_url": str(episode.still_url or ""),
        "runtime": int(episode.runtime or 0),
        "rating": float(episode.rating or 0.0),
        "adult": False,
    }


def episode_search_query(series_title: Any, season_number: Any, episode_number: Any) -> str:
    title = _clean(series_title)
    season = max(0, _safe_int(season_number))
    episode = max(0, _safe_int(episode_number))
    return f"{title} S{season:02d}E{episode:02d}".strip()


def episode_release_matches(
    release_title: Any,
    metadata: Mapping[str, Any],
) -> bool:
    series_title = _clean(metadata.get("series_title"))
    season = _safe_int(metadata.get("season_number"), -1)
    episode = _safe_int(metadata.get("episode_number"), -1)
    if not series_title or season < 0 or episode < 0:
        return False

    release_text = str(release_title or "").casefold()
    release_tokens = normalized_title_tokens(release_text)
    series_tokens = normalized_title_tokens(series_title)
    if not series_tokens or not all(token in release_tokens for token in series_tokens):
        return False

    compact = re.sub(r"[^a-z0-9]+", "", release_text)
    patterns = (
        f"s{season:02d}e{episode:02d}",
        f"s{season}e{episode}",
        f"{season}x{episode:02d}",
        f"{season}x{episode}",
        f"season{season}episode{episode}",
    )
    return any(pattern in compact for pattern in patterns)


def release_matches_catalog(
    release_title: Any,
    metadata: Optional[Mapping[str, Any]],
    release_metadata: Optional[Mapping[str, Any]] = None,
) -> bool:
    if not isinstance(metadata, Mapping):
        return False

    if str(metadata.get("media_type") or "").strip().lower() == "episode":
        return episode_release_matches(release_title, metadata)

    catalog_id = _clean(metadata.get("catalog_id"), 40)
    if catalog_id and isinstance(release_metadata, Mapping):
        reported = (
            release_metadata.get("source_reported")
            if isinstance(release_metadata.get("source_reported"), Mapping)
            else {}
        )
        reported_tmdb = _clean(
            reported.get("tmdb")
            or reported.get("tmdb_id")
            or reported.get("tmdbId")
            or reported.get("tmdbid"),
            40,
        )
        if reported_tmdb:
            return reported_tmdb == catalog_id

    catalog_title = _clean(metadata.get("title"))
    if not catalog_title:
        return False

    catalog_tokens = normalized_title_tokens(catalog_title)
    release_tokens = normalized_title_tokens(release_title)
    if not catalog_tokens or not release_tokens:
        return False
    if not all(token in release_tokens for token in catalog_tokens):
        return False

    year = _safe_int(metadata.get("year"), 0)
    if year:
        release_years = {
            int(token)
            for token in release_tokens
            if len(token) == 4 and token.isdigit() and 1900 <= int(token) <= 2100
        }
        if release_years and year not in release_years:
            return False
    return True


def filter_outcome_for_catalog(
    outcome: MediaSourceSearchOutcome,
    metadata: Optional[Mapping[str, Any]],
) -> MediaSourceSearchOutcome:
    if not isinstance(metadata, Mapping) or not metadata:
        return outcome

    matched = tuple(
        variant
        for variant in outcome.variants
        if release_matches_catalog(
            variant.title,
            metadata,
            variant.metadata,
        )
    )
    if len(matched) == len(outcome.variants):
        return outcome

    errors = list(outcome.errors)
    if outcome.variants and not matched:
        errors.append(
            "Connected providers returned releases, but none matched the selected catalog title."
        )
    elif len(matched) < len(outcome.variants):
        errors.append(
            f"Ignored {len(outcome.variants) - len(matched)} provider release(s) "
            "that did not match the selected catalog title."
        )
    return MediaSourceSearchOutcome(
        variants=matched,
        errors=tuple(errors[:20]),
    )


__all__ = [
    "catalog_metadata",
    "episode_catalog_metadata",
    "episode_release_matches",
    "episode_search_query",
    "filter_adult_provider_results",
    "filter_outcome_for_catalog",
    "normalized_title_tokens",
    "parse_episode_query",
    "release_matches_catalog",
    "variant_is_explicit_adult",
]
