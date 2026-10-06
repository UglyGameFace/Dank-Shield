from __future__ import annotations

"""Backend-neutral Dank Cinema room playback orchestration.

Discord controls and the web Theater both use this module so source selection,
torrent lifecycle, room authority, and canonical media identity cannot diverge.
"""

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .cinema_catalog import CinemaEpisode, CinemaMedia, get_details
from .cinema_library_service import CinemaStorageUnavailable, get_cinema_user
from .cinema_media_identity import (
    catalog_metadata,
    episode_catalog_metadata,
    episode_search_query,
    filter_adult_provider_results,
    filter_outcome_for_catalog,
    release_matches_catalog,
)
from .media_source_resolver import (
    MediaSourceSearchOutcome,
    fetch_torrent_metadata,
    search_movie_sources,
)
from .movie_night import MovieNightRoom, get_movie_night_manager, movie_room_lease_key
from .movie_night_preferences import load_movie_night_preferences
from .torrent_streaming import find_magnet, get_torrent_manager


class CinemaPlaybackError(RuntimeError):
    """Raised when canonical Cinema room media cannot be started safely."""


@dataclass(frozen=True)
class CinemaPlaybackResult:
    room: MovieNightRoom
    candidate: Any
    variant: Any
    stream_url: str
    session: Any = None


def _clean(value: Any, limit: int = 180) -> str:
    return " ".join(str(value or "").split())[:limit]


def _catalog_identity(value: Optional[Mapping[str, Any]]) -> tuple[str, int, int, int]:
    if not isinstance(value, Mapping):
        return "", 0, 0, 0
    kind = str(value.get("media_type") or "").strip().lower()
    tmdb_id = int(value.get("tmdb_id") or value.get("catalog_id") or 0)
    season = int(value.get("season_number") or 0)
    episode = int(value.get("episode_number") or 0)
    if kind not in {"movie", "tv", "episode"} or tmdb_id <= 0:
        return "", 0, 0, 0
    return kind, tmdb_id, season, episode


def find_catalog_candidate(
    room: MovieNightRoom,
    metadata: Optional[Mapping[str, Any]],
) -> Any:
    """Find a room candidate by canonical Cinema identity, never title alone."""

    identity = _catalog_identity(metadata)
    if not identity[0]:
        return None
    for candidate in room.candidates.values():
        candidate_meta = (
            candidate.metadata.get("catalog")
            if isinstance(getattr(candidate, "metadata", None), Mapping)
            and isinstance(candidate.metadata.get("catalog"), Mapping)
            else {}
        )
        if _catalog_identity(candidate_meta) == identity:
            return candidate
    return None


def materialize_search_results(
    room: MovieNightRoom,
    outcome: MediaSourceSearchOutcome,
    *,
    proposer_id: int,
    query: str,
    catalog_metadata: Optional[Mapping[str, Any]] = None,
    manager: Any = None,
) -> tuple[int, int]:
    """Attach normalized provider releases to canonical room candidates."""

    manager = manager or get_movie_night_manager()
    candidate_ids: set[str] = set()
    release_count = 0

    for result in outcome.variants:
        catalog = (
            dict(catalog_metadata)
            if release_matches_catalog(
                result.title,
                catalog_metadata,
                result.metadata,
            )
            else {}
        )
        candidate_title = _clean(catalog.get("title")) if catalog else result.title
        if catalog:
            candidate = find_catalog_candidate(room, catalog)
            if candidate is None and not _catalog_identity(catalog)[0]:
                candidate = manager.find_candidate_by_title(
                    room.room_id,
                    candidate_title,
                )
        else:
            candidate = manager.find_candidate_by_title(
                room.room_id,
                candidate_title,
            )
        candidate_metadata: dict[str, Any] = {"search_query": _clean(query)}
        if catalog:
            candidate_metadata["catalog"] = catalog

        if candidate is None:
            candidate = manager.nominate(
                room.room_id,
                user_id=int(proposer_id),
                title=candidate_title,
                metadata=candidate_metadata,
                auto_vote=False,
            )
        elif catalog:
            candidate.metadata.update(candidate_metadata)

        candidate_ids.add(candidate.candidate_id)
        manager.add_variant(
            room.room_id,
            candidate.candidate_id,
            user_id=int(proposer_id),
            source_ref=result.source_ref,
            source_id=result.source_id,
            source_label=result.source_label,
            file_size=result.file_size,
            peers=result.peers,
            seeds=result.seeds,
            leechers=result.leechers,
            metadata=result.metadata,
            auto_vote=False,
        )
        release_count += 1

    return len(candidate_ids), release_count


async def select_preferred_variant(
    user_id: int,
    variants: Any,
) -> Any:
    """Choose the user's preferred real source when available, otherwise best-ranked first."""

    rows = list(variants or ())
    if not rows:
        return None
    try:
        profile = await get_cinema_user(int(user_id))
        preferences = (
            profile.get("preferences")
            if isinstance(profile.get("preferences"), Mapping)
            else {}
        )
        preferred = str(preferences.get("preferred_source") or "").strip().casefold()
    except (CinemaStorageUnavailable, TypeError, ValueError):
        preferred = ""

    if preferred:
        match = next(
            (
                item
                for item in rows
                if preferred == str(getattr(item, "source_id", "") or "").casefold()
                or preferred in str(getattr(item, "source_label", "") or "").casefold()
            ),
            None,
        )
        if match is not None:
            return match
    return rows[0]


async def search_exact_movie_sources(
    guild_id: int,
    *,
    media: CinemaMedia,
) -> tuple[dict[str, Any], str, MediaSourceSearchOutcome]:
    """Search configured providers for one exact canonical movie."""

    if str(media.media_type or "").strip().lower() != "movie":
        raise ValueError("Exact movie source search requires a movie catalog item.")
    metadata = catalog_metadata(media)
    try:
        details = await get_details("movie", int(media.tmdb_id))
        metadata.update(
            {
                "genres": list(details.genres)[:12],
                "studios": list(details.studios)[:16],
                "franchises": list(details.franchises)[:8],
            }
        )
    except Exception:
        pass
    query = _clean(media.title)
    outcome = await search_movie_sources(
        int(guild_id),
        query,
        catalog_metadata=metadata,
    )
    try:
        _raw, preferences = await load_movie_night_preferences(int(guild_id), refresh=False)
        adult_enabled = bool(preferences.adult_content_enabled)
    except Exception:
        adult_enabled = False
    outcome = filter_adult_provider_results(outcome, enabled=adult_enabled)
    return metadata, query, filter_outcome_for_catalog(outcome, metadata)


async def search_exact_episode_sources(
    guild_id: int,
    *,
    series: CinemaMedia,
    episode: CinemaEpisode,
) -> tuple[dict[str, Any], str, MediaSourceSearchOutcome]:
    """Search configured providers for one exact canonical TV episode."""

    metadata = episode_catalog_metadata(series=series, episode=episode)
    try:
        details = await get_details("tv", int(series.tmdb_id))
        metadata.update(
            {
                "genres": list(details.genres)[:12],
                "studios": list(details.studios)[:16],
                "franchises": list(details.franchises)[:8],
            }
        )
    except Exception:
        pass
    query = episode_search_query(
        series.title,
        episode.season_number,
        episode.episode_number,
        series.year,
    )
    outcome = await search_movie_sources(
        int(guild_id),
        query,
        catalog_metadata=metadata,
    )
    try:
        _raw, preferences = await load_movie_night_preferences(int(guild_id), refresh=False)
        adult_enabled = bool(preferences.adult_content_enabled)
    except Exception:
        adult_enabled = False
    outcome = filter_adult_provider_results(outcome, enabled=adult_enabled)
    return metadata, query, filter_outcome_for_catalog(outcome, metadata)


async def start_room_variant(
    room_id: str,
    *,
    actor_id: int,
    candidate_id: str,
    variant_id: str,
    authorized_by_vote: bool = False,
) -> CinemaPlaybackResult:
    """Start one existing candidate variant under the room's canonical authority."""

    manager = get_movie_night_manager()
    room = manager.get(str(room_id or ""))
    if room is None or room.ended:
        raise LookupError("This Cinema room no longer exists.")
    actor = int(actor_id)
    if not manager.user_can_access(room, actor):
        raise PermissionError("This Cinema room is private.")

    candidate = room.candidates.get(str(candidate_id or ""))
    if candidate is None:
        raise LookupError("That Cinema title no longer exists.")
    variant = candidate.variants.get(str(variant_id or ""))
    if variant is None:
        raise LookupError("That Cinema release no longer exists.")

    if not authorized_by_vote and actor != int(room.host_id):
        raise PermissionError("Only the active host can replace Cinema media.")

    torrent_manager = get_torrent_manager()
    if variant.file_size and int(variant.file_size) > int(torrent_manager.max_file_bytes):
        raise CinemaPlaybackError(
            "This release is above the current per-file Cinema host limit."
        )

    previous = str(room.stream_token or "")
    initial_host_id = int(room.host_id)
    lease_key = movie_room_lease_key(int(room.guild_id), int(room.channel_id))
    source_ref = str(variant.source_ref or "").strip()

    if source_ref.lower().startswith("magnet:?"):
        clean_magnet = find_magnet(source_ref)
        if not clean_magnet:
            raise CinemaPlaybackError("This source returned an invalid magnet link.")
        session = await torrent_manager.start_magnet(
            clean_magnet,
            guild_id=int(room.guild_id),
            owner_id=initial_host_id,
            replace_token=previous,
            lease_key=lease_key,
        )
    elif source_ref.lower().startswith("https://"):
        payload = await fetch_torrent_metadata(
            source_ref,
            max_bytes=torrent_manager.max_metadata_bytes,
        )
        session = await torrent_manager.start_torrent_bytes(
            payload,
            guild_id=int(room.guild_id),
            owner_id=initial_host_id,
            replace_token=previous,
            lease_key=lease_key,
        )
    else:
        raise CinemaPlaybackError(
            "This release is not a supported magnet or HTTPS .torrent source."
        )

    stream_url = torrent_manager.stream_url(session)
    if not stream_url:
        await torrent_manager.release_lease(
            session.token,
            lease_key,
            remove_if_unused=True,
        )
        raise CinemaPlaybackError(
            "The media session started but no signed public stream URL could be created."
        )

    latest = manager.get(room.room_id)
    if (
        latest is None
        or latest.ended
        or int(latest.host_id) != initial_host_id
        or str(latest.stream_token or "") != previous
    ):
        await torrent_manager.release_lease(
            session.token,
            lease_key,
            remove_if_unused=True,
        )
        raise CinemaPlaybackError(
            "Cinema changed while this release was loading, so the stale media result was discarded."
        )

    manager.select_variant(
        latest.room_id,
        candidate.candidate_id,
        variant_id=variant.variant_id,
    )
    manager.set_room_media(
        latest.room_id,
        host_id=int(latest.host_id),
        stream_token=session.token,
        candidate_id=candidate.candidate_id,
        variant_id=variant.variant_id,
    )

    try:
        profile = await get_cinema_user(initial_host_id)
        preferences = (
            profile.get("preferences")
            if isinstance(profile.get("preferences"), Mapping)
            else {}
        )
        preferred_speed = float(preferences.get("playback_speed") or 1.0)
        if preferred_speed != 1.0:
            manager.apply_host_action(
                latest.room_id,
                host_id=initial_host_id,
                action="speed",
                payload={"rate": preferred_speed},
            )
    except (CinemaStorageUnavailable, TypeError, ValueError):
        # Personalization must never prevent otherwise valid Cinema playback.
        pass

    merged_meta = dict(variant.metadata or {})
    merged_meta["release_name"] = dict(
        session.release_metadata or merged_meta.get("release_name") or {}
    )
    if session.verified_metadata:
        merged_meta["verified"] = dict(session.verified_metadata)
    variant.metadata = merged_meta
    variant.file_size = int(session.file_size or variant.file_size)

    if previous and previous != session.token:
        await torrent_manager.release_lease(
            previous,
            lease_key,
            remove_if_unused=True,
        )

    return CinemaPlaybackResult(
        room=latest,
        candidate=candidate,
        variant=variant,
        stream_url=stream_url,
        session=session,
    )


__all__ = [
    "CinemaPlaybackError",
    "CinemaPlaybackResult",
    "find_catalog_candidate",
    "materialize_search_results",
    "search_exact_episode_sources",
    "search_exact_movie_sources",
    "select_preferred_variant",
    "start_room_variant",
]
