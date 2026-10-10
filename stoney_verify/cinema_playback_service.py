from __future__ import annotations

"""Backend-neutral Dank Cinema room playback orchestration.

Discord controls and the web Theater both use this module so source selection,
torrent lifecycle, room authority, and canonical media identity cannot diverge.
"""

import asyncio
from dataclasses import dataclass
import time

from aiohttp import ClientError
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


_MIB = 1024 * 1024
_MAX_AUTO_START_ATTEMPTS = 3


def _safe_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _auto_video_height(item: Any) -> int:
    """Use actual probed resolution, otherwise unverified release-name clues."""
    meta = getattr(item, "metadata", {}) or {}
    meta = meta if isinstance(meta, Mapping) else {}
    verified = meta.get("verified")
    video = verified.get("video") if isinstance(verified, Mapping) else None
    if isinstance(video, Mapping) and _safe_int(video.get("height")):
        return _safe_int(video.get("height"))
    release = meta.get("release_name")
    release = release if isinstance(release, Mapping) else {}
    resolution = str(release.get("resolution") or "").strip().casefold()
    if resolution.endswith(("p", "i")):
        return _safe_int(resolution[:-1])
    return 0


_AUDIO_LANGUAGE_ALIASES = {
    "eng": "en", "english": "en", "spa": "es", "spanish": "es",
    "por": "pt", "portuguese": "pt", "fre": "fr", "fra": "fr",
    "french": "fr", "deu": "de", "ger": "de", "german": "de",
    "jpn": "ja", "japanese": "ja", "kor": "ko", "korean": "ko",
    "ita": "it", "italian": "it", "hin": "hi", "hindi": "hi",
    "zho": "zh", "chi": "zh", "chinese": "zh",
}


def _audio_language_key(value: Any) -> str:
    """Mirror the Theater's primary audio language aliases for source scoring."""
    text = str(value or "").strip().casefold().replace("_", "-")
    primary = text.split("-", 1)[0].split(" ", 1)[0]
    if primary in {"", "auto", "original", "und", "unknown"}:
        return ""
    return _AUDIO_LANGUAGE_ALIASES.get(
        primary, primary if len(primary) == 2 and primary.isalpha() else "",
    )


def _guild_audio_preference(preferences: Mapping[str, Any], guild_id: int) -> str:
    scoped = preferences.get("audio_language_by_guild")
    scoped = scoped if isinstance(scoped, Mapping) else {}
    key = str(max(0, int(guild_id or 0)))
    if key in scoped and int(guild_id or 0) > 0:
        # Explicit "auto" disables the global default *for this guild*.
        return _audio_language_key(scoped[key])
    return _audio_language_key(preferences.get("default_audio_language"))


def _preferred_audio_language_key(item: Any, language: str) -> int:
    """0=verified match, 1=unverified, 2=verified no matching audio.

    Never treat an index label or filename hint as verified audio tracks.
    """
    wanted = _audio_language_key(language)
    if not wanted:
        return 0
    metadata = getattr(item, "metadata", {}) or {}
    verified = metadata.get("verified") if isinstance(metadata, Mapping) else None
    if not isinstance(verified, Mapping) or not verified.get("available"):
        return 1
    names = verified.get("audio_languages")
    names = names if isinstance(names, (list, tuple)) else ()
    rows = verified.get("audio_tracks")
    rows = rows if isinstance(rows, (list, tuple)) else ()
    known = {
        _audio_language_key(value)
        for value in names
    }
    known.update(
        _audio_language_key(track.get("language"))
        for track in rows if isinstance(track, Mapping)
    )
    known.discard("")
    if not known:
        return 1
    return 0 if wanted in known else 2


async def _user_auto_preferences(user_id: int, guild_id: int) -> tuple[str, str]:
    try:
        profile = await get_cinema_user(int(user_id))
        preferences = (
            profile.get("preferences")
            if isinstance(profile, Mapping)
            and isinstance(profile.get("preferences"), Mapping)
            else {}
        )
        source = str(preferences.get("preferred_source") or "").strip().casefold()
        language = _guild_audio_preference(preferences, guild_id)
        return source, language
    except (CinemaStorageUnavailable, TypeError, ValueError):
        return "", ""


def _automatic_rank_key(
    item: Any, *, preferred_source: str = "", preferred_language: str = "",
) -> tuple[Any, ...]:
    """Compare only defensible evidence; unknown codecs/peer rates are not verified."""
    risk = int(item.browser_video_risk_key())
    audio = (
        int(item.browser_audio_risk_key())
        if callable(getattr(item, "browser_audio_risk_key", None))
        else 1
    )
    meta = getattr(item, "metadata", {}) or {}
    meta = meta if isinstance(meta, Mapping) else {}
    height = _auto_video_height(item)
    # A large 4K remux is often a poor streaming choice; 1080p is the
    # default browser target, then 720p. Neither is certified by a label.
    resolution_penalty = (
        0 if 900 <= height <= 1200
        else 1 if 650 <= height < 900
        else 2 if height == 0
        else 3 if height > 1200
        else 4
    )
    size = _safe_int(getattr(item, "file_size", 0))
    # File size is unknown for many provider feeds. It should not be treated
    # as zero-byte proof of efficiency. Strongly penalize huge files, but
    # avoid rejecting valid movie sources solely on reported sizes.
    size_penalty = (
        1 if size <= 0
        else 0 if size <= (3 * 1024 ** 3 if height <= 900 and height else 6 * 1024 ** 3)
        else 2 if size <= 10 * 1024 ** 3
        else 3
    )
    seeds = _safe_int(getattr(item, "seeds", 0))
    leechers = _safe_int(getattr(item, "leechers", 0))
    reported_band = 0 if seeds >= 20 else 1 if seeds >= 5 else 2 if seeds else 3
    ratio = min(10.0, seeds / max(1, leechers))

    # Only a previously started torrent can have measured live throughput.
    # Completed torrents do not need a positive current download rate.
    observed = meta.get("observed_swarm")
    observed = observed if isinstance(observed, Mapping) else {}
    try:
        age = time.monotonic() - float(observed.get("at") or 0)
    except (ValueError, TypeError, OverflowError):
        age = float("inf")
    if 0 <= age <= 180 and observed:
        progress = float(observed.get("progress") or 0)
        rate = _safe_int(observed.get("download_rate"))
        connected = _safe_int(observed.get("connected_peers"))
        measured_band = (
            0 if progress >= 0.995
            else 1 if rate >= 2 * _MIB
            else 2 if rate >= _MIB // 2
            else 3 if rate > 0 and connected > 0
            else 5
        )
    else:
        measured_band = 4  # Unknown != fast and unknown != failed.

    preferred = str(preferred_source or "").strip().casefold()
    source_id = str(getattr(item, "source_id", "") or "").casefold()
    source_label = str(getattr(item, "source_label", "") or "").casefold()
    is_preferred = bool(preferred and (preferred == source_id or preferred in source_label))
    # An unseeded and unmeasured source should not outrank a viable
    # alternative solely for advertising 1080p/4K. Completion or observed
    # live throughput is stronger evidence than provider seed counts.
    availability = (
        0 if measured_band <= 3
        else 1 if seeds > 0
        else 2
    )
    return (
        availability, risk, size_penalty, measured_band,
        _preferred_audio_language_key(item, preferred_language),
        reported_band, resolution_penalty, audio,
        0 if is_preferred else 1, -min(seeds, 80), -ratio,
    )


def ranked_automatic_variants(
    variants: Any,
    *,
    preferred_source: str = "",
    preferred_language: str = "",
    max_file_bytes: int = 0,
    active_voters: Any = None,
) -> list[Any]:
    """Bounded, stable ranking with known format and configured file limits."""
    limit = _safe_int(max_file_bytes)
    rows = [
        item for item in list(variants or ())[:100]
        if callable(getattr(item, "browser_video_risk_key", None))
        and int(item.browser_video_risk_key()) < 2
        and (not limit or not _safe_int(getattr(item, "file_size", 0))
             or _safe_int(getattr(item, "file_size", 0)) <= limit)
    ]
    active = set(active_voters or ())
    return sorted(
        rows,
        key=lambda item: (
            -len(set(getattr(item, "votes", ()) or ()) & active),
            _automatic_rank_key(
                item, preferred_source=preferred_source,
                preferred_language=preferred_language,
            ),
        ),
    )


async def select_preferred_variant(
    user_id: int, variants: Any, *,
    guild_id: int = 0, active_voters: Any = None,
) -> Any:
    """Rank for this member in this authenticated guild, never a viewer list."""
    preferred_source, preferred_language = await _user_auto_preferences(
        int(user_id), int(guild_id),
    )
    rows = ranked_automatic_variants(
        variants, preferred_source=preferred_source,
        preferred_language=preferred_language, active_voters=active_voters,
    )
    return rows[0] if rows else None

async def start_automatic_variant(
    room_id: str,
    *,
    actor_id: int,
    candidate_id: str,
    selected: Any,
    ranked: Any,
    start_variant: Any = None,
    max_file_bytes: int = 0,
    manager: Any = None,
    guild_id: int = 0,
    active_voters: Any = None,
) -> tuple[CinemaPlaybackResult, Any, int]:
    """Retry only startup failures, never swap a playing room behind viewers.

    The first selection retains the user's soft preference. The remaining
    sources are ranked by the same safety/efficiency policy, and each is
    started at most once. No retries after a room-media state change.
    """
    manager = manager or get_movie_night_manager()
    original = manager.get(room_id)
    if original is None or original.ended or original.host_id != int(actor_id):
        raise PermissionError("Only the active host can change Cinema media.")
    def state_key(room: Any) -> tuple[str, str, str, str]:
        options = getattr(room, "candidates", None)
        candidate = options.get(candidate_id) if isinstance(options, Mapping) else None
        return (
            str(room.stream_token or ""),
            str(room.current_candidate_id or ""),
            str(room.current_variant_id or ""),
            str(getattr(candidate, "selected_variant_id", "") or ""),
        )

    baseline = state_key(original)
    limit = _safe_int(max_file_bytes)
    if not limit and (start_variant is None or start_variant is start_room_variant):
        # Read the existing configured torrent file cap once for the real
        # production launcher. Injected test/other backends need no torrent
        # engine initialization to rank candidates.
        limit = _safe_int(getattr(get_torrent_manager(), "max_file_bytes", 0))
    preferred_source, preferred_language = await _user_auto_preferences(
        int(actor_id), int(guild_id),
    )
    remaining = [
        row for row in ranked_automatic_variants(
            ranked, max_file_bytes=limit, active_voters=active_voters,
            preferred_source=preferred_source, preferred_language=preferred_language,
        )
        if selected is None or row.variant_id != selected.variant_id
    ]
    choices = ([selected] if selected is not None
               and selected.browser_video_risk_key() < 2
               and (not limit or not _safe_int(selected.file_size)
                    or _safe_int(selected.file_size) <= limit) else []) + remaining
    if not choices:
        raise CinemaPlaybackError(
            "No automatic source passed the current compatibility and file-size checks."
        )

    start = start_variant or start_room_variant
    failures = 0
    for item in choices[:_MAX_AUTO_START_ATTEMPTS]:
        latest = manager.get(room_id)
        if (
            latest is None or latest.ended
            or int(latest.host_id) != int(actor_id)
            or state_key(latest) != baseline
        ):
            raise CinemaPlaybackError("Cinema changed during automatic source selection.")
        try:
            result = await start(
                room_id, actor_id=int(actor_id),
                candidate_id=candidate_id, variant_id=item.variant_id,
                automatic=True,
            )
            return result, item, failures
        except PermissionError:
            # PermissionError is an OSError subclass. Host/lease authority
            # failures are never recoverable by trying another source.
            raise
        except (CinemaPlaybackError, ClientError, RuntimeError, ValueError, OSError, TimeoutError) as exc:
            print(
                "⚠️ cinema_auto_source startup_failed "
                f"attempt={failures + 1}/{min(_MAX_AUTO_START_ATTEMPTS, len(choices))} "
                f"reason={type(exc).__name__}"
            )
            # State must remain unchanged for a safe retry; e.g. a failure
            # after a successful room commit must never trigger a second launch.
            if (
                (latest := manager.get(room_id)) is None or latest.ended
                or int(latest.host_id) != int(actor_id)
                or state_key(latest) != baseline
            ):
                raise
            failures += 1
    raise CinemaPlaybackError(
        f"Automatic playback tried {failures} compatible candidates without a successful startup. "
        "Choose another release or retry when providers are available."
    )


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


def choose_automatic_torrent_file(session: Any, catalog: Any) -> Any:
    """Select the actual playable file, not blindly the largest torrent entry.

    Single torrents and multi-episode packs share one metadata path. Reject
    explicit wrong-episode entries, then prefer the exact episode and a
    container without known browser risks. Never infer codec verification
    from a filename.
    """
    from .media_metadata import browser_video_risk_key, parse_release_name

    metadata = catalog if isinstance(catalog, Mapping) else {}
    episode = str(metadata.get("media_type") or "").casefold() == "episode"
    season = _safe_int(metadata.get("season_number"))
    number = _safe_int(metadata.get("episode_number"))
    possible = []
    for item in tuple(getattr(session, "candidates", ()) or ())[:100]:
        name = str(getattr(item, "path", "") or "")
        tags = parse_release_name(name)
        item_season = _safe_int(tags.get("season"))
        item_number = _safe_int(tags.get("episode"))
        known_episode = bool(item_season and item_number)
        if episode and known_episode and (item_season, item_number) != (season, number):
            continue
        risk = browser_video_risk_key({
            "release_name": tags,
            "source_reported": {"filename": name},
        })
        # Exact episode identity beats an unmarked extra file. For movies,
        # choose a format without explicit browser codec/container red flags.
        exactness = 0 if episode and known_episode else 1
        size = _safe_int(getattr(item, "size", 0))
        small_extra = 1 if size < 25 * _MIB else 0
        possible.append(((exactness if episode else 0, risk, small_extra, -size), item))
    return min(possible, key=lambda entry: entry[0])[1] if possible else None


async def start_room_variant(
    room_id: str,
    *,
    actor_id: int,
    candidate_id: str,
    variant_id: str,
    authorized_by_vote: bool = False,
    automatic: bool = False,
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

    if automatic:
        catalog = (
            candidate.metadata.get("catalog")
            if isinstance(getattr(candidate, "metadata", None), Mapping)
            else {}
        )
        chosen_file = choose_automatic_torrent_file(session, catalog)
        if chosen_file is None:
            if str(session.token) != previous:
                await torrent_manager.release_lease(
                    session.token, lease_key, remove_if_unused=True,
                )
            raise CinemaPlaybackError(
                "Torrent metadata contains no file matching the requested episode."
            )
        if int(chosen_file.index) != int(session.file_index):
            if str(session.token) == previous:
                raise CinemaPlaybackError(
                    "Cinema cannot silently change an existing shared torrent file."
                )
            try:
                session = await torrent_manager.select_file(
                    session.token, int(chosen_file.index), owner_id=initial_host_id,
                )
            except Exception:
                await torrent_manager.release_lease(
                    session.token, lease_key, remove_if_unused=True,
                )
                raise
        # If the existing bounded probe can run immediately from downloaded
        # head/tail pieces, wait briefly for *actual* video codec evidence
        # before committing the stream. Never wait for an entire torrent.
        try:
            torrent_manager.schedule_metadata_probe(session)
            for _ in range(12):
                if not bool(getattr(session, "metadata_probe_running", False)):
                    break
                await asyncio.sleep(0.15)
        except (RuntimeError, AttributeError):
            pass

        # The downloaded torrent metadata reveals the selected file name
        # before the browser player starts. It is stronger negative evidence
        # than a provider label, but still cannot certify playable codecs.
        from .media_metadata import browser_video_risk_key
        file_meta = {
            "release_name": dict(session.release_metadata or {}),
            "source_reported": {"filename": str(session.file_name or "")},
            "verified": dict(session.verified_metadata or {}),
        }
        if browser_video_risk_key(file_meta) >= 2:
            if str(session.token) != previous:
                await torrent_manager.release_lease(
                    session.token, lease_key, remove_if_unused=True,
                )
            raise CinemaPlaybackError(
                "Torrent metadata selected a video format unsuitable for automatic browser playback."
            )

    try:
        stream_url = torrent_manager.stream_url(session)
    except Exception:
        if str(session.token) != previous:
            await torrent_manager.release_lease(
                session.token, lease_key, remove_if_unused=True,
            )
        raise
    if not stream_url:
        if str(session.token) != previous:
            await torrent_manager.release_lease(
                session.token, lease_key, remove_if_unused=True,
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

    try:
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
    except Exception:
        current = manager.get(room.room_id)
        # Never discard a lease already referenced by committed room state,
        # even if a later viewer-state update failed after the token changed.
        if (
            str(session.token) != previous
            and (current is None or str(current.stream_token or "") != session.token)
        ):
            await torrent_manager.release_lease(
                session.token, lease_key, remove_if_unused=True,
            )
        raise

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
    "choose_automatic_torrent_file",
    "ranked_automatic_variants",
    "select_preferred_variant",
    "start_automatic_variant",
    "start_room_variant",
]
