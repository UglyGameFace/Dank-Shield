from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord
from urllib.parse import parse_qs, urlsplit

from stoney_verify import (
    cinema_catalog,
    cinema_discovery_service,
    cinema_feed_service,
    cinema_site,
    cinema_site_auth,
    movie_night_web,
)
from stoney_verify.cinema_catalog import CinemaDetails, CinemaEpisode, CinemaMedia
from stoney_verify.cinema_media_identity import (
    episode_catalog_metadata,
    episode_search_query,
    filter_outcome_for_catalog,
    parse_episode_query,
    release_matches_catalog,
)
from stoney_verify import cinema_playback_service
from stoney_verify.cinema_storage import CinemaStorageUnavailable
from stoney_verify.media_source_resolver import (
    MediaSourceSearchOutcome,
    ResolvedMediaVariant,
)
from stoney_verify.movie_night import MovieNightManager


def _episode(
    *,
    series_id: int = 77,
    season: int = 3,
    number: int = 7,
    tmdb_id: int = 9001,
    title: str = "Episode",
) -> CinemaEpisode:
    return CinemaEpisode(
        series_id=series_id,
        season_number=season,
        episode_number=number,
        tmdb_id=tmdb_id,
        title=title,
    )


def _series() -> CinemaMedia:
    return CinemaMedia(
        media_type="tv",
        tmdb_id=77,
        title="Example Show",
        year=2026,
        poster_url="https://image.tmdb.org/t/p/w500/poster.jpg",
        backdrop_url="https://image.tmdb.org/t/p/w1280/backdrop.jpg",
    )


def test_cinema_home_movie_resume_hero_starts_direct_playback() -> None:
    hero = cinema_site._hero_from_sections(
        [],
        [
            {
                "media_type": "movie",
                "tmdb_id": 123,
                "title": "Example Movie",
                "progress_seconds": 321.0,
                "metadata": {"backdrop_url": "backdrop", "poster_url": "poster"},
            }
        ],
        [],
    )

    assert hero is not None
    assert hero["kind"] == "resume"
    assert hero["action"] == {
        "kind": "play",
        "media_type": "movie",
        "tmdb_id": 123,
        "label": "Resume",
    }


def test_cinema_home_episode_resume_hero_keeps_exact_episode_identity() -> None:
    hero = cinema_site._hero_from_sections(
        [],
        [
            {
                "media_type": "episode",
                "tmdb_id": 9002,
                "season_number": 1,
                "episode_number": 2,
                "title": "Diversity Day",
                "progress_seconds": 420.0,
                "metadata": {
                    "series_id": 2316,
                    "series_title": "The Office",
                    "backdrop_url": "backdrop",
                },
            }
        ],
        [],
    )

    assert hero is not None
    assert hero["kind"] == "resume"
    assert hero["action"] == {
        "kind": "play",
        "media_type": "episode",
        "tmdb_id": 9002,
        "series_id": 2316,
        "season_number": 1,
        "episode_number": 2,
        "label": "Resume",
    }


def test_cinema_home_episode_resume_never_builds_invalid_episode_details_route() -> None:
    hero = cinema_site._hero_from_sections(
        [],
        [
            {
                "media_type": "episode",
                "tmdb_id": 9002,
                "season_number": 1,
                "episode_number": 2,
                "title": "Diversity Day",
                "metadata": {},
            }
        ],
        [],
    )

    assert hero is not None
    assert hero["action"] == {}


def test_movie_identity_rejects_explicit_game_and_software_categories() -> None:
    metadata = {
        "media_type": "movie",
        "catalog_id": "123",
        "title": "Resident Evil",
        "year": 2026,
    }

    assert not release_matches_catalog(
        "Resident Evil (GOG)",
        metadata,
        {"source_reported": {"category": "Games"}},
    )
    assert not release_matches_catalog(
        "Resident Evil Requiem voices38",
        metadata,
        {"source_reported": {"category": "400"}},
    )
    assert not release_matches_catalog(
        "Resident Evil 2026 installer",
        metadata,
        {"source_reported": {"category": "Applications"}},
    )


def test_movie_identity_keeps_video_or_unknown_provider_categories() -> None:
    metadata = {
        "media_type": "movie",
        "catalog_id": "123",
        "title": "Resident Evil",
        "year": 2026,
    }

    assert release_matches_catalog(
        "Resident Evil 2026 1080p WEB-DL",
        metadata,
        {"source_reported": {"category": "Movies"}},
    )
    assert release_matches_catalog(
        "Resident Evil 2026 1080p WEB-DL",
        metadata,
        {"source_reported": {"category": "205"}},
    )
    assert release_matches_catalog(
        "Resident Evil 2026 1080p WEB-DL",
        metadata,
        {"source_reported": {"category": "custom-release"}},
    )


def test_episode_identity_requires_exact_series_and_episode_marker() -> None:
    metadata = episode_catalog_metadata(series=_series(), episode=_episode())

    assert release_matches_catalog(
        "Example.Show.S03E07.1080p.WEB-DL",
        metadata,
        {},
    )
    assert release_matches_catalog(
        "Example Show 3x07 720p",
        metadata,
        {},
    )
    assert not release_matches_catalog(
        "Example.Show.S03E08.1080p.WEB-DL",
        metadata,
        {},
    )
    assert not release_matches_catalog(
        "Different.Show.S03E07.1080p.WEB-DL",
        metadata,
        {},
    )


def test_episode_identity_uses_series_year_when_available() -> None:
    metadata = episode_catalog_metadata(series=_series(), episode=_episode())

    assert episode_search_query("Example Show", 3, 7, 2026) == "Example Show 2026 S03E07"
    assert release_matches_catalog(
        "Example.Show.2026.S03E07.1080p.WEB-DL",
        metadata,
        {},
    )
    assert not release_matches_catalog(
        "Example.Show.2005.S03E07.1080p.WEB-DL",
        metadata,
        {},
    )
    # Providers often omit a series year, so exact title + episode remains valid.
    assert release_matches_catalog(
        "Example.Show.S03E07.1080p.WEB-DL",
        metadata,
        {},
    )


def test_episode_provider_filter_keeps_only_canonical_episode() -> None:
    metadata = episode_catalog_metadata(series=_series(), episode=_episode())
    good = ResolvedMediaVariant(
        title="Example.Show.S03E07.1080p",
        source_id="good",
        source_label="Good",
        source_ref="magnet:?xt=urn:btih:" + "1" * 40,
        file_size=100,
        seeds=10,
        leechers=1,
        peers=11,
        metadata={},
    )
    wrong = ResolvedMediaVariant(
        title="Example.Show.S03E08.1080p",
        source_id="wrong",
        source_label="Wrong",
        source_ref="magnet:?xt=urn:btih:" + "2" * 40,
        file_size=100,
        seeds=20,
        leechers=1,
        peers=21,
        metadata={},
    )

    filtered = filter_outcome_for_catalog(
        MediaSourceSearchOutcome(variants=(wrong, good)),
        metadata,
    )

    assert filtered.variants == (good,)
    assert filtered.errors


def test_episode_source_search_recovers_yearless_release_without_wrong_episode(monkeypatch) -> None:
    series = _series()
    episode = _episode()
    requests = []
    good = ResolvedMediaVariant(
        title="Example.Show.S03E07.1080p.WEB-DL",
        source_id="sample",
        source_label="Sample",
        source_ref="magnet:?xt=urn:btih:" + "a" * 40,
        file_size=2_000_000_000,
        seeds=20,
        leechers=2,
        peers=22,
        metadata={},
    )
    wrong_episode = ResolvedMediaVariant(
        title="Example.Show.S03E08.1080p.WEB-DL",
        source_id="sample",
        source_label="Sample",
        source_ref="magnet:?xt=urn:btih:" + "b" * 40,
        file_size=2_000_000_000,
        seeds=100,
        leechers=2,
        peers=102,
        metadata={},
    )
    wrong_year = ResolvedMediaVariant(
        title="Example.Show.2005.S03E07.1080p.WEB-DL",
        source_id="sample",
        source_label="Sample",
        source_ref="magnet:?xt=urn:btih:" + "c" * 40,
        file_size=2_000_000_000,
        seeds=80,
        leechers=2,
        peers=82,
        metadata={},
    )

    async def catalog_details(_kind, _tmdb_id):
        return CinemaDetails(media=series)

    async def preferences(_guild_id, *, refresh=False):
        return {}, SimpleNamespace(adult_content_enabled=False)

    async def search(_guild_id, query, *, catalog_metadata):
        requests.append((query, catalog_metadata["series_id"]))
        if len(requests) == 1:
            return MediaSourceSearchOutcome(
                variants=(),
                errors=("Sample: no playable results for this search.",),
            )
        return MediaSourceSearchOutcome(variants=(wrong_episode, wrong_year, good))

    monkeypatch.setattr(cinema_playback_service, "get_details", catalog_details)
    monkeypatch.setattr(cinema_playback_service, "load_movie_night_preferences", preferences)
    monkeypatch.setattr(cinema_playback_service, "search_movie_sources", search)

    metadata, query, result = asyncio.run(
        cinema_playback_service.search_exact_episode_sources(
            123,
            series=series,
            episode=episode,
        )
    )
    assert metadata["tmdb_id"] == episode.tmdb_id
    assert requests == [
        ("Example Show 2026 S03E07", series.tmdb_id),
        ("Example Show S03E07", series.tmdb_id),
    ]
    assert query == "Example Show S03E07"
    assert result.variants == (good,)


def test_episode_source_search_skips_yearless_retry_when_primary_is_playable(monkeypatch) -> None:
    calls = []
    series = _series()
    good = ResolvedMediaVariant(
        title="Example.Show.2026.S03E07.1080p",
        source_id="sample",
        source_label="Sample",
        source_ref="magnet:?xt=urn:btih:" + "d" * 40,
        file_size=1_000_000_000,
        seeds=5,
        leechers=1,
        peers=6,
        metadata={},
    )

    async def search(_guild_id, query, *, catalog_metadata):
        calls.append(query)
        return MediaSourceSearchOutcome(variants=(good,))

    async def preferences(_guild_id, *, refresh=False):
        return {}, SimpleNamespace(adult_content_enabled=False)

    async def catalog_details(_kind, _tmdb_id):
        return CinemaDetails(media=series)

    monkeypatch.setattr(cinema_playback_service, "get_details", catalog_details)
    monkeypatch.setattr(cinema_playback_service, "load_movie_night_preferences", preferences)
    monkeypatch.setattr(cinema_playback_service, "search_movie_sources", search)

    _metadata, query, result = asyncio.run(
        cinema_playback_service.search_exact_episode_sources(
            123, series=series, episode=_episode(),
        )
    )
    assert calls == ["Example Show 2026 S03E07"]
    assert query == calls[0]
    assert result.variants == (good,)


def test_episode_source_search_yearless_retry_does_not_accept_wrong_episode(monkeypatch) -> None:
    calls = []
    series = _series()
    wrong = ResolvedMediaVariant(
        title="Example.Show.S03E08.1080p",
        source_id="sample",
        source_label="Sample",
        source_ref="magnet:?xt=urn:btih:" + "e" * 40,
        file_size=1_000_000_000,
        seeds=100,
        leechers=1,
        peers=101,
        metadata={},
    )

    async def search(_guild_id, query, *, catalog_metadata):
        calls.append(query)
        if len(calls) == 1:
            return MediaSourceSearchOutcome(variants=())
        return MediaSourceSearchOutcome(variants=(wrong,))

    async def preferences(_guild_id, *, refresh=False):
        return {}, SimpleNamespace(adult_content_enabled=False)

    async def catalog_details(_kind, _tmdb_id):
        return CinemaDetails(media=series)

    monkeypatch.setattr(cinema_playback_service, "get_details", catalog_details)
    monkeypatch.setattr(cinema_playback_service, "load_movie_night_preferences", preferences)
    monkeypatch.setattr(cinema_playback_service, "search_movie_sources", search)

    _metadata, _query, result = asyncio.run(
        cinema_playback_service.search_exact_episode_sources(
            123, series=series, episode=_episode(),
        )
    )
    assert len(calls) == 2
    assert result.variants == ()
    assert "none matched" in " ".join(result.errors).lower()


def test_catalog_search_ranks_exact_tv_title_before_fuzzy_variants(monkeypatch) -> None:
    async def request(_path, *, params=None, cache_ttl=0.0):
        _ = params, cache_ttl
        return {
            "results": [
                {
                    "media_type": "tv",
                    "id": 300,
                    "name": "The Office PL",
                    "first_air_date": "2021-01-01",
                    "popularity": 500.0,
                    "vote_average": 7.8,
                },
                {
                    "media_type": "tv",
                    "id": 2316,
                    "name": "The Office",
                    "first_air_date": "2005-03-24",
                    "popularity": 180.0,
                    "vote_average": 8.6,
                },
                {
                    "media_type": "tv",
                    "id": 2996,
                    "name": "The Office",
                    "first_air_date": "2001-07-09",
                    "popularity": 90.0,
                    "vote_average": 8.5,
                },
                {
                    "media_type": "movie",
                    "id": 999,
                    "title": "Office Party",
                    "release_date": "2026-01-01",
                    "popularity": 900.0,
                    "vote_average": 9.0,
                },
            ]
        }

    monkeypatch.setattr(cinema_catalog, "_request", request)

    results = asyncio.run(cinema_catalog.search_catalog("The Office", limit=4))
    assert [item.tmdb_id for item in results[:3]] == [2316, 2996, 300]

    year_results = asyncio.run(cinema_catalog.search_catalog("The Office (2001)", limit=4))
    assert year_results[0].tmdb_id == 2996


def test_next_episode_crosses_real_season_boundary(monkeypatch) -> None:
    async def fake_season(_series_id: int, season_number: int):
        if season_number == 3:
            return (_episode(season=3, number=7, tmdb_id=9001),)
        if season_number == 4:
            return (
                _episode(season=4, number=1, tmdb_id=9101, title="Season Four Premiere"),
                _episode(season=4, number=2, tmdb_id=9102),
            )
        return ()

    async def fake_details(_kind: str, _series_id: int):
        return CinemaDetails(
            media=_series(),
            seasons=(
                {"season_number": 3, "episode_count": 7},
                {"season_number": 4, "episode_count": 8},
            ),
        )

    monkeypatch.setattr(cinema_catalog, "get_season", fake_season)
    monkeypatch.setattr(cinema_catalog, "get_details", fake_details)

    found = asyncio.run(cinema_catalog.get_next_episode(77, 3, 7))

    assert found is not None
    assert found.season_number == 4
    assert found.episode_number == 1
    assert found.tmdb_id == 9101


def test_materialized_provider_result_keeps_catalog_identity(monkeypatch) -> None:
    manager = MovieNightManager()
    room = manager.create_room(
        guild_id=100,
        channel_id=200,
        host_id=42,
        stream_token="",
    )
    monkeypatch.setattr(cinema_playback_service, "get_movie_night_manager", lambda: manager)

    metadata = episode_catalog_metadata(series=_series(), episode=_episode())
    result = ResolvedMediaVariant(
        title="Example.Show.S03E07.1080p.WEB-DL",
        source_id="provider",
        source_label="Provider",
        source_ref="magnet:?xt=urn:btih:" + "3" * 40,
        file_size=1234,
        seeds=8,
        leechers=2,
        peers=10,
        metadata={"quality": "1080p"},
    )

    titles, releases = cinema_playback_service.materialize_search_results(
        room,
        MediaSourceSearchOutcome(variants=(result,)),
        proposer_id=42,
        query="Example Show S03E07",
        catalog_metadata=metadata,
    )

    assert titles == 1
    assert releases == 1
    candidate = manager.find_candidate_by_title(room.room_id, metadata["title"])
    assert candidate is not None
    assert candidate.metadata["catalog"]["tmdb_id"] == 9001
    assert candidate.metadata["catalog"]["series_id"] == 77
    assert len(candidate.variants) == 1


def test_watch_player_only_exposes_real_resolved_next_episode_control() -> None:
    html = movie_night_web._watch_html(
        "room-tv",
        42,
        "uid=42&exp=9999999999&sig=test",
    )

    assert 'id="nextEpisode"' in html
    assert 'hidden disabled' in html
    assert "async function loadNextEpisodeAvailability" in html
    assert 'jsonFetch("/movie/"+BOOT.roomId+"/next-episode")' in html
    assert "nextEpisodeState?.available" in html
    assert "lastState?.is_host" in html
    assert 'document.getElementById("nextEpisode").onclick=()=>playNextEpisode(true)' in html
    assert "cinemaPreferences.autoplay_next!==false" in html


def test_next_episode_get_hides_control_without_playable_source(monkeypatch) -> None:
    current = SimpleNamespace(
        title="Example Show S03E07",
        metadata={
            "catalog": {
                "media_type": "episode",
                "tmdb_id": 9001,
                "series_id": 77,
                "series_title": "Example Show",
                "season_number": 3,
                "episode_number": 7,
            }
        },
    )
    room = SimpleNamespace(
        room_id="room-tv",
        guild_id=100,
        host_id=42,
        current_candidate_id="current",
        candidates={"current": current},
    )

    async def room_and_user(_request):
        return room, 42

    next_episode = _episode(season=3, number=8, tmdb_id=9002, title="Next")
    metadata = episode_catalog_metadata(series=_series(), episode=next_episode)

    async def context(_room):
        return (
            current.metadata["catalog"],
            next_episode,
            metadata,
            "Example Show S03E08",
            MediaSourceSearchOutcome(variants=()),
        )

    monkeypatch.setattr(movie_night_web, "_room_and_user", room_and_user)
    monkeypatch.setattr(movie_night_web, "_next_episode_context", context)

    response = asyncio.run(
        movie_night_web.movie_night_next_episode(SimpleNamespace(method="GET"))
    )
    payload = __import__("json").loads(response.text)

    assert payload["available"] is False
    assert payload["reason"] == "source_unavailable"
    assert payload["next_episode"]["episode_number"] == 8


def test_watch_party_picks_only_use_real_accessible_room_media(monkeypatch) -> None:
    manager = MovieNightManager()
    room = manager.create_room(
        guild_id=100,
        channel_id=200,
        host_id=42,
        stream_token="",
    )
    candidate = manager.nominate(
        room.room_id,
        user_id=42,
        title="Example Show S03E07",
        metadata={
            "catalog": episode_catalog_metadata(
                series=_series(),
                episode=_episode(),
            )
        },
        auto_vote=False,
    )
    room.current_candidate_id = candidate.candidate_id
    room.queue.append(candidate.candidate_id)
    monkeypatch.setattr(cinema_site, "get_movie_night_manager", lambda: manager)

    rows = cinema_site._watch_party_picks(100, 42)

    assert len(rows) == 1
    assert rows[0]["result_kind"] == "watch_party_pick"
    assert rows[0]["media_type"] == "episode"
    assert rows[0]["series_id"] == 77
    assert rows[0]["season_number"] == 3
    assert rows[0]["episode_number"] == 7
    assert rows[0]["watch_party_active"] is True


def test_cinema_details_keeps_title_playable_when_library_storage_is_unavailable(monkeypatch) -> None:
    movie = CinemaMedia(
        media_type="movie",
        tmdb_id=123,
        title="Example Movie",
        year=2026,
    )
    details = CinemaDetails(media=movie)

    async def site_identity(_request):
        return (100, 42)

    async def get_details(_kind, _tmdb_id):
        return details

    async def list_media(_user_id):
        raise CinemaStorageUnavailable("test storage outage")

    async def adult_enabled(_guild_id):
        return False

    async def no_sources(_guild_id, *, media):
        assert media.tmdb_id == 123
        return (
            cinema_playback_service.catalog_metadata(media),
            "Example Movie",
            MediaSourceSearchOutcome(variants=()),
        )

    cinema_site._SOURCE_SNAPSHOT_CACHE.clear()
    monkeypatch.setattr(cinema_site, "_site_identity", site_identity)
    monkeypatch.setattr(cinema_site, "get_details", get_details)
    monkeypatch.setattr(cinema_site, "list_user_media", list_media)
    monkeypatch.setattr(cinema_site, "_guild_adult_content_enabled", adult_enabled)
    monkeypatch.setattr(cinema_site, "search_exact_movie_sources", no_sources)
    monkeypatch.setattr(cinema_site, "get_movie_night_manager", lambda: MovieNightManager())

    request = SimpleNamespace(match_info={"media_type": "movie", "tmdb_id": "123"})
    response = asyncio.run(cinema_site.cinema_details_api(request))
    payload = __import__("json").loads(response.text)

    assert response.status == 200
    assert payload["details"]["title"] == "Example Movie"
    assert payload["library"] is None
    assert payload["library_available"] is False
    assert "Playback and title details still work" in payload["library_notice"]
    assert cinema_site._SOURCE_SNAPSHOT_CACHE == {}


def test_cinema_details_catalog_failure_is_specific_503_not_default_500(monkeypatch) -> None:
    async def site_identity(_request):
        return (100, 42)

    async def broken_details(_kind, _tmdb_id):
        raise RuntimeError("tmdb unavailable")

    async def list_media(_user_id):
        return []

    monkeypatch.setattr(cinema_site, "_site_identity", site_identity)
    monkeypatch.setattr(cinema_site, "get_details", broken_details)
    monkeypatch.setattr(cinema_site, "list_user_media", list_media)

    request = SimpleNamespace(match_info={"media_type": "movie", "tmdb_id": "123"})
    try:
        asyncio.run(cinema_site.cinema_details_api(request))
    except Exception as exc:
        from aiohttp import web

        assert isinstance(exc, web.HTTPServiceUnavailable)
        assert exc.status == 503
        assert "title metadata is temporarily unavailable" in exc.text
    else:
        raise AssertionError("Catalog failure unexpectedly escaped as a successful details response.")


def test_cinema_season_keeps_episode_list_when_library_storage_is_unavailable(monkeypatch) -> None:
    async def site_identity(_request):
        return (100, 42)

    async def get_season(_series_id, _season_number):
        return (_episode(),)

    async def list_media(_user_id):
        raise CinemaStorageUnavailable("test storage outage")

    monkeypatch.setattr(cinema_site, "_site_identity", site_identity)
    monkeypatch.setattr(cinema_site, "get_season", get_season)
    monkeypatch.setattr(cinema_site, "list_user_media", list_media)

    request = SimpleNamespace(match_info={"series_id": "77", "season_number": "3"})
    response = asyncio.run(cinema_site.cinema_season_api(request))
    payload = __import__("json").loads(response.text)

    assert response.status == 200
    assert payload["library_available"] is False
    assert len(payload["episodes"]) == 1
    assert payload["episodes"][0]["episode_number"] == 7
    assert payload["episodes"][0]["progress"] is None


def test_cinema_site_play_rejects_nonhost_explicit_room(monkeypatch) -> None:
    manager = MovieNightManager()
    room = manager.create_room(
        guild_id=100,
        channel_id=200,
        host_id=42,
        stream_token="",
    )
    monkeypatch.setattr(cinema_site, "get_movie_night_manager", lambda: manager)

    async def site_identity(_request):
        return (100, 99)

    monkeypatch.setattr(cinema_site, "_site_identity", site_identity)

    class Request:
        async def json(self):
            return {
                "room_id": room.room_id,
                "media_type": "movie",
                "tmdb_id": 123,
            }

    try:
        asyncio.run(cinema_site.cinema_play_api(Request()))
    except Exception as exc:
        from aiohttp import web

        assert isinstance(exc, web.HTTPForbidden)
        assert "current Cinema host" in exc.text
    else:
        raise AssertionError("A non-host site identity must not replace Cinema media.")


def test_cinema_site_play_without_room_creates_host_only_standalone_room(monkeypatch) -> None:
    cinema_site._SOURCE_SNAPSHOT_CACHE.clear()
    monkeypatch.setenv("DANK_MEDIA_PUBLIC_BASE_URL", "https://cinema.example")
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "cinema-test-secret")
    manager = MovieNightManager()
    fake_session = SimpleNamespace(
        startup_started_at=0.0,
        created_at=0.0,
        launch_timing={},
    )
    movie = CinemaMedia(
        media_type="movie",
        tmdb_id=123,
        title="Example Movie",
        year=2026,
    )
    details = CinemaDetails(media=movie)
    metadata = cinema_playback_service.catalog_metadata(movie)
    outcome = MediaSourceSearchOutcome(
        variants=(
            ResolvedMediaVariant(
                title="Example.Movie.2026.1080p",
                source_id="provider",
                source_label="Provider",
                source_ref="magnet:?xt=urn:btih:" + "7" * 40,
                file_size=1000,
                seeds=20,
                leechers=2,
                peers=22,
                metadata={},
            ),
        )
    )

    async def site_identity(_request):
        return (100, 42)

    async def get_details(_kind, _tmdb_id):
        return details

    async def adult_enabled(_guild_id):
        return False

    async def exact_sources(_guild_id, *, media):
        assert media.tmdb_id == 123
        return metadata, "Example Movie", outcome

    async def preferred(_user_id, rows):
        return list(rows)[0]

    async def start_variant(room_id, *, actor_id, candidate_id, variant_id):
        room = manager.get(room_id)
        assert room is not None
        assert actor_id == 42
        assert candidate_id
        assert variant_id
        now = __import__("time").monotonic()
        fake_session.startup_started_at = now
        fake_session.created_at = now
        return SimpleNamespace(room=room, session=fake_session)

    monkeypatch.setattr(cinema_site, "_site_identity", site_identity)
    monkeypatch.setattr(cinema_site, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(cinema_playback_service, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(cinema_site, "get_details", get_details)
    monkeypatch.setattr(cinema_site, "_guild_adult_content_enabled", adult_enabled)
    monkeypatch.setattr(cinema_site, "search_exact_movie_sources", exact_sources)
    monkeypatch.setattr(cinema_site, "select_preferred_variant", preferred)
    monkeypatch.setattr(cinema_site, "start_room_variant", start_variant)

    class Request:
        async def json(self):
            return {"media_type": "movie", "tmdb_id": 123}

    response = asyncio.run(cinema_site.cinema_play_api(Request()))
    payload = __import__("json").loads(response.text)
    room = manager.get(payload["room_id"])

    assert room is not None
    assert room.mode == "standalone"
    assert room.guild_id == 100
    assert room.channel_id == -42
    assert room.host_id == 42
    assert manager.user_can_access(room, 42)
    assert not manager.user_can_access(room, 99)
    assert payload["mode"] == "standalone"
    assert "/movie/" in payload["watch_url"]
    assert set(fake_session.launch_timing) == {
        "site_source_ms",
        "site_torrent_start_ms",
        "site_session_ready_ms",
        "site_response_ready_ms",
    }
    assert fake_session.launch_timing["site_response_ready_ms"] >= fake_session.launch_timing["site_source_ms"]


def test_cinema_details_source_snapshot_is_reused_by_immediate_play(monkeypatch) -> None:
    monkeypatch.setenv("DANK_MEDIA_PUBLIC_BASE_URL", "https://cinema.example")
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "cinema-test-secret")
    cinema_site._SOURCE_SNAPSHOT_CACHE.clear()

    manager = MovieNightManager()
    movie = CinemaMedia(
        media_type="movie",
        tmdb_id=123,
        title="Example Movie",
        year=2026,
    )
    details = CinemaDetails(media=movie)
    metadata = cinema_playback_service.catalog_metadata(movie)
    source_ref = "magnet:?xt=urn:btih:" + "c" * 40
    outcome = MediaSourceSearchOutcome(
        variants=(
            ResolvedMediaVariant(
                title="Example.Movie.2026.1080p.WEB",
                source_id="provider",
                source_label="Provider",
                source_ref=source_ref,
                file_size=2000,
                seeds=30,
                leechers=2,
                peers=32,
                metadata={},
            ),
        )
    )
    search_calls = 0

    async def site_identity(_request):
        return (100, 42)

    async def get_details(_kind, _tmdb_id):
        return details

    async def list_media(_user_id):
        return []

    async def adult_enabled(_guild_id):
        return False

    async def exact_sources(_guild_id, *, media):
        nonlocal search_calls
        search_calls += 1
        if search_calls > 1:
            raise AssertionError("Play unexpectedly repeated the provider search after Details.")
        assert media.tmdb_id == 123
        return metadata, "Example Movie", outcome

    async def preferred(_user_id, rows):
        return list(rows)[0]

    async def start_variant(room_id, *, actor_id, candidate_id, variant_id):
        room = manager.get(room_id)
        assert room is not None
        chosen = room.candidates[candidate_id].variants[variant_id]
        assert chosen.source_ref == source_ref
        return SimpleNamespace(room=room)

    monkeypatch.setattr(cinema_site, "_site_identity", site_identity)
    monkeypatch.setattr(cinema_site, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(cinema_playback_service, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(cinema_site, "get_details", get_details)
    monkeypatch.setattr(cinema_site, "list_user_media", list_media)
    monkeypatch.setattr(cinema_site, "_guild_adult_content_enabled", adult_enabled)
    monkeypatch.setattr(cinema_site, "search_exact_movie_sources", exact_sources)
    monkeypatch.setattr(cinema_site, "select_preferred_variant", preferred)
    monkeypatch.setattr(cinema_site, "start_room_variant", start_variant)

    details_request = SimpleNamespace(
        match_info={"media_type": "movie", "tmdb_id": "123"},
    )
    details_response = asyncio.run(cinema_site.cinema_details_api(details_request))
    details_payload = __import__("json").loads(details_response.text)

    assert details_response.status == 200
    assert len(details_payload["sources"]) == 1
    assert details_payload["sources"][0]["source_choice"]
    assert source_ref not in details_response.text
    assert search_calls == 1

    class PlayRequest:
        async def json(self):
            return {"media_type": "movie", "tmdb_id": 123}

    play_response = asyncio.run(cinema_site.cinema_play_api(PlayRequest()))
    play_payload = __import__("json").loads(play_response.text)

    assert play_response.status == 200
    assert play_payload["source"]["source_id"] == "provider"
    assert play_payload["source"]["selection_mode"] == "automatic"
    assert search_calls == 1
    assert cinema_site._SOURCE_SNAPSHOT_CACHE == {}


def test_cinema_site_manual_source_choice_overrides_auto_rank(monkeypatch) -> None:
    cinema_site._SOURCE_SNAPSHOT_CACHE.clear()
    monkeypatch.setenv("DANK_MEDIA_PUBLIC_BASE_URL", "https://cinema.example")
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "cinema-test-secret")
    manager = MovieNightManager()
    movie = CinemaMedia(
        media_type="movie",
        tmdb_id=123,
        title="Example Movie",
        year=2026,
    )
    details = CinemaDetails(media=movie)
    metadata = cinema_playback_service.catalog_metadata(movie)
    automatic_ref = "magnet:?xt=urn:btih:" + "a" * 40
    manual_ref = "magnet:?xt=urn:btih:" + "b" * 40
    outcome = MediaSourceSearchOutcome(
        variants=(
            ResolvedMediaVariant(
                title="Example.Movie.2026.1080p.WEB",
                source_id="automatic",
                source_label="Automatic Provider",
                source_ref=automatic_ref,
                file_size=2000,
                seeds=100,
                leechers=1,
                peers=101,
                metadata={},
            ),
            ResolvedMediaVariant(
                title="Example.Movie.2026.720p.WEB",
                source_id="manual",
                source_label="Manual Provider",
                source_ref=manual_ref,
                file_size=1000,
                seeds=4,
                leechers=2,
                peers=6,
                metadata={},
            ),
        )
    )

    async def site_identity(_request):
        return (100, 42)

    async def get_details(_kind, _tmdb_id):
        return details

    async def adult_enabled(_guild_id):
        return False

    async def exact_sources(_guild_id, *, media):
        assert media.tmdb_id == 123
        return metadata, "Example Movie", outcome

    async def should_not_auto_select(_user_id, _rows):
        raise AssertionError("manual source choice unexpectedly fell back to automatic selection")

    async def start_variant(room_id, *, actor_id, candidate_id, variant_id):
        room = manager.get(room_id)
        assert room is not None
        assert actor_id == 42
        candidate = room.candidates[candidate_id]
        chosen = candidate.variants[variant_id]
        assert chosen.source_ref == manual_ref
        return SimpleNamespace(room=room)

    monkeypatch.setattr(cinema_site, "_site_identity", site_identity)
    monkeypatch.setattr(cinema_site, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(cinema_playback_service, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(cinema_site, "get_details", get_details)
    monkeypatch.setattr(cinema_site, "_guild_adult_content_enabled", adult_enabled)
    monkeypatch.setattr(cinema_site, "search_exact_movie_sources", exact_sources)
    monkeypatch.setattr(cinema_site, "select_preferred_variant", should_not_auto_select)
    monkeypatch.setattr(cinema_site, "start_room_variant", start_variant)

    choice = cinema_site._source_choice_id(manual_ref)

    class Request:
        async def json(self):
            return {
                "media_type": "movie",
                "tmdb_id": 123,
                "source_choice": choice,
            }

    response = asyncio.run(cinema_site.cinema_play_api(Request()))
    payload = __import__("json").loads(response.text)

    assert payload["source"]["source_id"] == "manual"
    assert payload["source"]["selection_mode"] == "manual"
    assert manual_ref not in response.text
    assert automatic_ref not in response.text


def test_cinema_identity_cookie_can_reopen_exact_guild_after_membership_recheck(monkeypatch) -> None:
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "cinema-test-secret")
    identity = cinema_site_auth.cinema_identity_value(42, ttl_seconds=3600)
    request = SimpleNamespace(
        match_info={"guild_id": "100"},
        query={},
        cookies={cinema_site_auth.CINEMA_IDENTITY_COOKIE: identity},
    )

    async def member_state(guild_id: int, user_id: int):
        return "present" if (int(guild_id), int(user_id)) == (100, 42) else "absent"

    monkeypatch.setattr(cinema_site, "_site_member_state", member_state)
    assert asyncio.run(cinema_site._site_identity(request)) == (100, 42)


def test_cinema_guild_cache_miss_falls_back_to_discord_rest(monkeypatch) -> None:
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "cinema-test-secret")
    cinema_site._SITE_GUILD_REST_CACHE.clear()
    cinema_site._SITE_GUILD_ABSENT_UNTIL.clear()
    cinema_site._SITE_GUILD_UNAVAILABLE_UNTIL.clear()
    cinema_site._SITE_MEMBER_REVOKED.discard((100, 42))

    class FakeGuild:
        id = 100

        def get_member(self, user_id: int):
            return object() if int(user_id) == 42 else None

        async def fetch_member(self, user_id: int):
            return object() if int(user_id) == 42 else None

    class FakeBot:
        def __init__(self):
            self.fetch_calls = 0

        def get_guild(self, _guild_id: int):
            return None

        async def fetch_guild(self, guild_id: int):
            self.fetch_calls += 1
            assert int(guild_id) == 100
            return FakeGuild()

    fake_bot = FakeBot()
    monkeypatch.setattr(cinema_site, "_bot_client", lambda: fake_bot)

    identity = cinema_site_auth.cinema_identity_value(42, ttl_seconds=3600)
    request = SimpleNamespace(
        match_info={"guild_id": "100"},
        query={},
        cookies={cinema_site_auth.CINEMA_IDENTITY_COOKIE: identity},
    )

    assert asyncio.run(cinema_site._site_identity(request)) == (100, 42)
    assert fake_bot.fetch_calls == 1

    state, guild = asyncio.run(cinema_site._resolve_bot_guild(100))
    assert state == "present"
    assert int(guild.id) == 100
    assert fake_bot.fetch_calls == 1


def test_cinema_rest_confirms_bot_is_not_installed(monkeypatch) -> None:
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "cinema-test-secret")
    cinema_site._SITE_GUILD_REST_CACHE.clear()
    cinema_site._SITE_GUILD_ABSENT_UNTIL.clear()
    cinema_site._SITE_GUILD_UNAVAILABLE_UNTIL.clear()
    cinema_site._SITE_MEMBER_REVOKED.discard((100, 42))

    class FakeBot:
        def get_guild(self, _guild_id: int):
            return None

        async def fetch_guild(self, _guild_id: int):
            raise discord.NotFound(
                SimpleNamespace(status=404, reason="missing"),
                "missing",
            )

    monkeypatch.setattr(cinema_site, "_bot_client", lambda: FakeBot())

    identity = cinema_site_auth.cinema_identity_value(42, ttl_seconds=3600)
    request = SimpleNamespace(
        match_info={"guild_id": "100"},
        query={},
        cookies={cinema_site_auth.CINEMA_IDENTITY_COOKIE: identity},
    )

    try:
        asyncio.run(cinema_site._site_identity(request))
    except Exception as exc:
        from aiohttp import web

        assert isinstance(exc, web.HTTPForbidden)
        assert "not installed in this Discord server" in exc.text
    else:
        raise AssertionError("Cinema must reject a guild Discord REST confirms the bot is not in.")


def test_standalone_oauth_guild_proof_allows_browsing_when_bot_member_rest_is_unavailable(monkeypatch) -> None:
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "cinema-test-secret")
    identity = cinema_site_auth.cinema_identity_value(42, ttl_seconds=3600)
    guilds = cinema_site_auth.cinema_guilds_value(42, [100], ttl_seconds=900)
    request = SimpleNamespace(
        match_info={"guild_id": "100"},
        query={},
        cookies={
            cinema_site_auth.CINEMA_IDENTITY_COOKIE: identity,
            cinema_site_auth.CINEMA_GUILDS_COOKIE: guilds,
        },
    )

    monkeypatch.setattr(
        cinema_site,
        "_bot_guild",
        lambda guild_id: object() if int(guild_id) == 100 else None,
    )

    async def should_not_recheck_member(_guild_id: int, _user_id: int):
        raise AssertionError("fresh OAuth guild proof must bypass member REST")

    monkeypatch.setattr(cinema_site, "_site_member_state", should_not_recheck_member)

    assert asyncio.run(cinema_site._site_identity(request)) == (100, 42)


def test_standalone_oauth_guild_proof_is_exact_guild_scoped(monkeypatch) -> None:
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "cinema-test-secret")
    identity = cinema_site_auth.cinema_identity_value(42, ttl_seconds=3600)
    guilds = cinema_site_auth.cinema_guilds_value(42, [200], ttl_seconds=900)
    request = SimpleNamespace(
        match_info={"guild_id": "100"},
        query={},
        cookies={
            cinema_site_auth.CINEMA_IDENTITY_COOKIE: identity,
            cinema_site_auth.CINEMA_GUILDS_COOKIE: guilds,
        },
    )
    monkeypatch.setattr(cinema_site, "_bot_guild", lambda _guild_id: object())

    async def departed_member(_guild_id: int, _user_id: int):
        return "absent"

    monkeypatch.setattr(cinema_site, "_site_member_state", departed_member)

    try:
        asyncio.run(cinema_site._site_identity(request))
    except Exception as exc:
        from aiohttp import web

        assert isinstance(exc, web.HTTPForbidden)
        assert "requires membership in this Discord server" in exc.text
    else:
        raise AssertionError("OAuth guild proof must never authorize a different guild.")


def test_discord_signed_cinema_link_establishes_browser_session_without_member_rest(monkeypatch) -> None:
    monkeypatch.setenv("DANK_MEDIA_PUBLIC_BASE_URL", "https://cinema.example")
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "cinema-test-secret")

    signed_url = cinema_site_auth.cinema_site_url(100, 42, ttl_seconds=900)
    parsed = urlsplit(signed_url)
    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    request = SimpleNamespace(
        match_info={"guild_id": "100"},
        query=query,
        cookies={},
    )

    monkeypatch.setattr(
        cinema_site,
        "_bot_guild",
        lambda guild_id: object() if int(guild_id) == 100 else None,
    )

    async def should_not_fetch_member(_guild_id: int, _user_id: int):
        raise AssertionError("valid Discord signed entry must not require member REST before cookie exchange")

    monkeypatch.setattr(cinema_site, "_site_member_state", should_not_fetch_member)

    response = asyncio.run(cinema_site.cinema_site_page(request))

    assert response.status == 200
    assert cinema_site_auth.CINEMA_SESSION_COOKIE in response.cookies
    assert cinema_site_auth.CINEMA_IDENTITY_COOKIE in response.cookies
    assert cinema_site_auth.CINEMA_GUILDS_COOKIE in response.cookies


def test_discord_signed_api_fallback_honors_member_remove_revocation(monkeypatch) -> None:
    monkeypatch.setenv("DANK_MEDIA_PUBLIC_BASE_URL", "https://cinema.example")
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "cinema-test-secret")

    signed_url = cinema_site_auth.cinema_site_url(100, 42, ttl_seconds=900)
    parsed = urlsplit(signed_url)
    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    request = SimpleNamespace(
        match_info={"guild_id": "100"},
        query=query,
        cookies={},
    )
    monkeypatch.setattr(
        cinema_site,
        "_bot_guild",
        lambda guild_id: object() if int(guild_id) == 100 else None,
    )

    cinema_site.note_cinema_member_remove(100, 42)
    try:
        asyncio.run(cinema_site._site_identity(request))
    except Exception as exc:
        from aiohttp import web

        assert isinstance(exc, web.HTTPForbidden)
        assert "requires membership in this Discord server" in exc.text
    else:
        raise AssertionError("Signed API fallback must not bypass member-remove revocation.")
    finally:
        cinema_site.note_cinema_member_join(100, 42)


def test_discord_signed_cinema_link_still_requires_bot_to_share_target_guild(monkeypatch) -> None:
    monkeypatch.setenv("DANK_MEDIA_PUBLIC_BASE_URL", "https://cinema.example")
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "cinema-test-secret")

    signed_url = cinema_site_auth.cinema_site_url(100, 42, ttl_seconds=900)
    parsed = urlsplit(signed_url)
    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    request = SimpleNamespace(
        match_info={"guild_id": "100"},
        query=query,
        cookies={},
    )
    async def absent_guild(_guild_id: int):
        return "absent", None

    monkeypatch.setattr(cinema_site, "_resolve_bot_guild", absent_guild)

    try:
        asyncio.run(cinema_site._site_identity(request))
    except Exception as exc:
        from aiohttp import web

        assert isinstance(exc, web.HTTPForbidden)
        assert "not installed in this Discord server" in exc.text
    else:
        raise AssertionError("A signed Cinema link must not work after Dank Shield leaves the guild.")


def test_cinema_guild_remove_invalidates_positive_rest_cache_and_join_restores(monkeypatch) -> None:
    cinema_site._SITE_GUILD_REST_CACHE.clear()
    cinema_site._SITE_GUILD_ABSENT_UNTIL.clear()
    cinema_site._SITE_GUILD_UNAVAILABLE_UNTIL.clear()

    guild = SimpleNamespace(id=100)
    cinema_site.note_cinema_guild_join(100, guild)

    assert 100 in cinema_site._SITE_GUILD_REST_CACHE
    assert 100 not in cinema_site._SITE_GUILD_ABSENT_UNTIL

    cinema_site.note_cinema_guild_remove(100)

    assert 100 not in cinema_site._SITE_GUILD_REST_CACHE
    assert cinema_site._SITE_GUILD_ABSENT_UNTIL.get(100, 0.0) > 0.0

    cinema_site.note_cinema_guild_join(100, guild)

    assert 100 in cinema_site._SITE_GUILD_REST_CACHE
    assert 100 not in cinema_site._SITE_GUILD_ABSENT_UNTIL


def test_cinema_session_is_bounded_to_signed_entry_window(monkeypatch) -> None:
    import time

    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "cinema-test-secret")
    value = cinema_site_auth.cinema_session_value(
        100,
        42,
        ttl_seconds=30 * 24 * 60 * 60,
    )
    parts = value.split(".")
    assert len(parts) == 4
    expires = int(parts[2])
    remaining = expires - int(time.time())

    assert cinema_site_auth.CINEMA_SESSION_TTL_SECONDS == 6 * 60 * 60
    assert 5 * 60 * 60 < remaining <= 6 * 60 * 60
    assert cinema_site_auth.validate_cinema_session(100, value) == 42


def test_cinema_browser_session_is_scoped_to_exact_discord_guild(monkeypatch) -> None:
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "cinema-test-secret")
    value = cinema_site_auth.cinema_session_value(100, 42, ttl_seconds=3600)

    assert value
    assert cinema_site_auth.validate_cinema_session(100, value) == 42
    assert cinema_site_auth.validate_cinema_session(200, value) is None


def test_verified_cinema_session_does_not_recheck_member_rest(monkeypatch) -> None:
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "cinema-test-secret")
    value = cinema_site_auth.cinema_session_value(100, 42, ttl_seconds=3600)
    request = SimpleNamespace(
        match_info={"guild_id": "100"},
        query={},
        cookies={cinema_site_auth.CINEMA_SESSION_COOKIE: value},
    )

    cinema_site._SITE_MEMBER_REVOKED.discard((100, 42))
    monkeypatch.setattr(
        cinema_site,
        "_bot_guild",
        lambda guild_id: object() if int(guild_id) == 100 else None,
    )

    async def must_not_run(_guild_id: int, _user_id: int):
        raise AssertionError("verified exact-guild Cinema session must not call member REST")

    monkeypatch.setattr(cinema_site, "_site_member_state", must_not_run)

    assert asyncio.run(cinema_site._site_identity(request)) == (100, 42)


def test_exact_guild_session_survives_temporary_membership_api_failure(monkeypatch) -> None:
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "cinema-test-secret")
    cinema_site._SITE_MEMBER_REVOKED.discard((100, 42))
    value = cinema_site_auth.cinema_session_value(100, 42, ttl_seconds=3600)
    request = SimpleNamespace(
        match_info={"guild_id": "100"},
        query={},
        cookies={cinema_site_auth.CINEMA_SESSION_COOKIE: value},
    )
    monkeypatch.setattr(
        cinema_site,
        "_bot_guild",
        lambda guild_id: object() if int(guild_id) == 100 else None,
    )

    async def unavailable_member(_guild_id: int, _user_id: int):
        return "unavailable"

    monkeypatch.setattr(cinema_site, "_site_member_state", unavailable_member)

    assert asyncio.run(cinema_site._site_identity(request)) == (100, 42)


def test_member_remove_revokes_existing_cinema_session_and_rejoin_restores_it(monkeypatch) -> None:
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "cinema-test-secret")
    value = cinema_site_auth.cinema_session_value(100, 42, ttl_seconds=3600)
    request = SimpleNamespace(
        match_info={"guild_id": "100"},
        query={},
        cookies={cinema_site_auth.CINEMA_SESSION_COOKIE: value},
    )
    monkeypatch.setattr(
        cinema_site,
        "_bot_guild",
        lambda guild_id: object() if int(guild_id) == 100 else None,
    )

    cinema_site.note_cinema_member_join(100, 42)

    async def unavailable_member(_guild_id: int, _user_id: int):
        return "unavailable"

    monkeypatch.setattr(cinema_site, "_site_member_state", unavailable_member)
    assert asyncio.run(cinema_site._site_identity(request)) == (100, 42)

    cinema_site.note_cinema_member_remove(100, 42)
    try:
        asyncio.run(cinema_site._site_identity(request))
    except Exception as exc:
        from aiohttp import web

        assert isinstance(exc, web.HTTPForbidden)
        assert "requires membership in this Discord server" in exc.text
    else:
        raise AssertionError("Member removal must revoke an existing Cinema session.")

    cinema_site.note_cinema_member_join(100, 42)
    assert asyncio.run(cinema_site._site_identity(request)) == (100, 42)


def test_standalone_cinema_login_and_signed_link_exchange_share_one_site_session() -> None:
    from pathlib import Path

    source = Path(cinema_site.__file__).read_text(encoding="utf-8")
    script = (
        Path(cinema_site.__file__).resolve().parent / "assets" / "cinema_site.js"
    ).read_text(encoding="utf-8")

    assert 'app.router.add_get("/cinema", cinema_entry_page)' in source
    assert 'app.router.add_get("/cinema/login", cinema_oauth_login)' in source
    assert 'app.router.add_get("/cinema/auth/callback", cinema_oauth_callback)' in source
    assert 'scope": "identify guilds"' in source
    assert "_issue_oauth_state(target_guild)" in source
    assert "_consume_oauth_state(returned_state)" in source
    assert "if target_guild not in user_guild_ids:" in source
    assert "guild_state, _guild = await _resolve_bot_guild(target_guild)" in source
    assert "await _fetch_site_member(target_guild, user_id)" not in source
    assert "def _recent_oauth_guild_proof(" in source
    assert "validate_cinema_guilds(" in source
    assert "def _site_member_state(" in source
    assert 'return "unavailable"' in source
    assert "_SITE_MEMBER_CACHE_SECONDS = 5 * 60.0" in source
    assert "cinema_session_value(guild_id, user_id)" in source
    assert 'path=f"/cinema/{int(guild_id)}"' in source
    assert 'const signedAuth = new URLSearchParams();' in script
    assert 'signedAuth.set(key, value)' in script
    assert 'const hadSignedEntry = ["uid", "exp", "sig"].every((key) => signedAuth.has(key));' in script
    assert 'const AUTH_QUERY = hadSignedEntry ? `?${signedAuth.toString()}` : "";' in script
    assert 'initialUrl.searchParams.delete("sig")' in script
    assert 'history.replaceState(' in script
    assert 'return `${path}${AUTH_QUERY ? join + AUTH_QUERY.slice(1) : ""}`;' in script
    assert 'src="/cinema/assets/site.js?v=17"' in source
    assert '"/cinema/{guild_id}/api/auth-debug"' in source
    assert "def _cinema_auth_debug_payload(" in source
    assert "signed-session-v8-snowflake-safe" in source
    assert "async function authDiagnostics()" in script
    assert 'API_BASE + "/auth-debug"' in script
    assert "function authDiagnosticText(data)" in script
    assert 'botGuildRest=${data.bot_guild_state || "unknown"}' in script
    assert 'botGuildCache=${data.bot_guild_cache_present ? "yes" : "no"}' in script
    assert 'return `Diagnostic: ${parts.join(" · ")}`;' in script


def test_cinema_auth_debug_reports_request_auth_state_without_secret_values(monkeypatch) -> None:
    monkeypatch.setenv("DANK_MEDIA_PUBLIC_BASE_URL", "https://cinema.example")
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "cinema-test-secret")

    signed_url = cinema_site_auth.cinema_site_url(100, 42, ttl_seconds=900)
    parsed = urlsplit(signed_url)
    query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
    session = cinema_site_auth.cinema_session_value(100, 42, ttl_seconds=900)
    identity = cinema_site_auth.cinema_identity_value(42, ttl_seconds=900)
    guilds = cinema_site_auth.cinema_guilds_value(42, [100], ttl_seconds=900)
    request = SimpleNamespace(
        match_info={"guild_id": "100"},
        query=query,
        cookies={
            cinema_site_auth.CINEMA_SESSION_COOKIE: session,
            cinema_site_auth.CINEMA_IDENTITY_COOKIE: identity,
            cinema_site_auth.CINEMA_GUILDS_COOKIE: guilds,
        },
        headers={"Cookie": "present", "X-Forwarded-Proto": "https"},
        secure=False,
    )
    monkeypatch.setattr(
        cinema_site,
        "_bot_guild",
        lambda guild_id: object() if int(guild_id) == 100 else None,
    )
    cinema_site._SITE_MEMBER_REVOKED.discard((100, 42))

    payload = cinema_site._cinema_auth_debug_payload(request)

    assert payload == {
        "contract": "signed-session-v8-snowflake-safe",
        "route_guild_valid": True,
        "signed_query": "valid",
        "signed_query_complete": True,
        "session_cookie": "valid",
        "identity_cookie": "valid",
        "guild_proof_cookie": "valid",
        "selected_source": "signed",
        "bot_guild_present": True,
        "member_revoked": False,
        "cookie_header_present": True,
        "request_secure": False,
        "forwarded_proto": "https",
    }
    rendered = repr(payload)
    assert query["sig"] not in rendered
    assert session not in rendered
    assert identity not in rendered
    assert guilds not in rendered


def test_cinema_auth_debug_distinguishes_missing_and_invalid_credentials(monkeypatch) -> None:
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "cinema-test-secret")
    request = SimpleNamespace(
        match_info={"guild_id": "100"},
        query={"uid": "42", "exp": "1", "sig": "bad"},
        cookies={
            cinema_site_auth.CINEMA_SESSION_COOKIE: "bad-session",
            cinema_site_auth.CINEMA_IDENTITY_COOKIE: "bad-identity",
        },
        headers={},
        secure=True,
    )
    monkeypatch.setattr(cinema_site, "_bot_guild", lambda _guild_id: object())

    payload = cinema_site._cinema_auth_debug_payload(request)

    assert payload["signed_query"] == "invalid"
    assert payload["signed_query_complete"] is True
    assert payload["session_cookie"] == "invalid"
    assert payload["identity_cookie"] == "invalid"
    assert payload["guild_proof_cookie"] == "missing"
    assert payload["selected_source"] == "none"
    assert payload["bot_guild_present"] is True
    assert payload["member_revoked"] is False
    assert payload["cookie_header_present"] is False


def test_cinema_oauth_state_survives_mobile_cookie_handoff_and_is_one_time() -> None:
    cinema_site._CINEMA_OAUTH_STATES.clear()
    state = cinema_site._issue_oauth_state(123)

    assert state
    assert cinema_site._consume_oauth_state(state) == 123
    assert cinema_site._consume_oauth_state(state) is None


def test_cinema_oauth_env_normalizes_quotes_and_supports_exact_redirect_override(monkeypatch) -> None:
    monkeypatch.setenv("DANK_CINEMA_DISCORD_CLIENT_ID", '"123456"')
    monkeypatch.setenv("DANK_CINEMA_DISCORD_CLIENT_SECRET", "'secret-value'")
    monkeypatch.setenv(
        "DANK_CINEMA_DISCORD_REDIRECT_URI",
        '"https://stoneyverify.discloud.dev/cinema/auth/callback"',
    )

    status = cinema_site.cinema_oauth_status()

    assert status["ready"] is True
    assert status["client_id_ready"] is True
    assert status["client_secret_ready"] is True
    assert status["redirect_uri_ready"] is True
    assert (
        status["redirect_uri"]
        == "https://stoneyverify.discloud.dev/cinema/auth/callback"
    )


def test_cinema_oauth_rejects_wrong_redirect_shape(monkeypatch) -> None:
    monkeypatch.setenv("DANK_CINEMA_DISCORD_CLIENT_ID", "123456")
    monkeypatch.setenv("DANK_CINEMA_DISCORD_CLIENT_SECRET", "secret-value")
    monkeypatch.setenv(
        "DANK_CINEMA_DISCORD_REDIRECT_URI",
        "https://stoneyverify.discloud.dev/wrong/callback",
    )

    status = cinema_site.cinema_oauth_status()

    assert status["ready"] is False
    assert status["redirect_uri_ready"] is False
    assert status["redirect_uri"] == ""


def test_full_site_episode_playback_is_direct_and_not_discord_room_scoped() -> None:
    from pathlib import Path

    root = Path(cinema_site.__file__).resolve().parent
    script = (root / "assets" / "cinema_site.js").read_text(encoding="utf-8")
    styles = (root / "assets" / "cinema_site.css").read_text(encoding="utf-8")

    assert 'await api("/play"' in script
    assert 'media_type: mediaType' in script
    assert 'payload.series_id = Number' in script
    assert 'payload.season_number = Number' in script
    assert 'payload.episode_number = Number' in script
    assert "playOnSite(episodeItem" in script
    assert 'action.kind === "play"' in script
    assert 'resume.addEventListener("click", () => playOnSite(action, resume))' in script
    assert 'card.classList.add("episode-playable")' in script
    assert 'event.target.closest("button, select, option")' in script
    assert 'if (sourceChoice) payload.source_choice = String(sourceChoice)' in script
    assert '"Playback Source"' in script
    assert '"Automatic • best available"' in script
    assert '${source.seeds} reported seeds' in script
    assert '"▶ Play Automatically"' in script
    assert '"▶ Play Selected Source"' in script
    assert 'row.dataset.sourceChoice = String(source.source_choice || "")' in script
    assert 'row.setAttribute("aria-pressed", "false")' in script
    assert 'playOnSite(d, sourcePlay, selectedSourceChoice)' in script
    assert '"▶ Resume"' in script
    assert '"▶ Play"' in script
    assert "Open Discord to Play" not in script
    assert "hostSession?.is_host" not in script
    assert ".episode-play" in styles
    assert ".episode-card.episode-playable" in styles
    assert ".source-choice.selected" in styles
    assert ".source-play-action" in styles
    assert 'const libraryAvailable = data.library_available !== false' in script
    assert '"state-card library-degraded"' in script
    assert "if (libraryAvailable) {" in script


def test_tv_details_do_not_claim_series_title_is_a_playable_source() -> None:
    source = __import__("pathlib").Path(cinema_site.__file__).read_text(encoding="utf-8")

    assert 'if media_type == "movie":' in source
    assert "search_exact_movie_sources(" in source
    assert "filter_outcome_for_catalog(" not in source
    assert "variant.swarm_health" not in source
    assert '"watch_party_picks",' in source
    assert 'catalog.get("top_movies"' not in source


def test_full_site_uses_single_composed_brand_and_responsive_tmdb_art() -> None:
    from pathlib import Path

    root = Path(cinema_site.__file__).resolve().parent
    script = (root / "assets" / "cinema_site.js").read_text(encoding="utf-8")
    styles = (root / "assets" / "cinema_site.css").read_text(encoding="utf-8")

    assert 'dank-cinema-brand.webp?v=art-system-v5' in script
    assert "dank-cinema-brand-mark.webp" not in script
    assert "dank-cinema-brand-wordmark.webp" not in script
    assert 'class="brand-lockup-img"' not in script  # created through node(), not HTML text
    assert 'node("img", "brand-lockup-img")' in script
    assert ".brand-mark" not in styles
    assert ".brand-word" not in styles
    assert ".brand-lockup-img" in styles
    assert "function configureArtwork(" in script
    assert "image.srcset = variants" in script
    assert 'kind === "backdrop"' in script
    assert 'kind === "still"' in script
    assert 'kind === "profile"' in script


def test_cinema_boot_preserves_discord_snowflakes_as_strings() -> None:
    from pathlib import Path

    guild_id = 1514374173517152418
    user_id = 629459300854661120

    html = cinema_site._site_html(guild_id, user_id)
    script = (
        Path(cinema_site.__file__).resolve().parent / "assets" / "cinema_site.js"
    ).read_text(encoding="utf-8")

    assert f'"guildId":"{guild_id}"' in html
    assert f'"userId":"{user_id}"' in html
    assert f'"guildId":{guild_id}' not in html
    assert f'"userId":{user_id}' not in html
    assert "/** @typedef {{guildId:string,userId:string}} CinemaBoot */" in script
    assert 'const BOOT = window.__DANK_CINEMA_BOOT__ || { guildId: "", userId: "" };' in script
    assert 'const API_BASE = `/cinema/${String(BOOT.guildId)}/api`;' in script
    assert "guildId:number" not in script
    assert "userId:number" not in script


def test_full_site_uses_real_navigation_icons_and_cache_busted_assets() -> None:
    from pathlib import Path

    root = Path(cinema_site.__file__).resolve().parent
    script = (root / "assets" / "cinema_site.js").read_text(encoding="utf-8")
    styles = (root / "assets" / "cinema_site.css").read_text(encoding="utf-8")
    source = Path(cinema_site.__file__).read_text(encoding="utf-8")

    assert 'bell: "♢"' not in script
    assert '"⌂"' not in script
    assert "const SVG_ICON_PATHS = {" in script
    assert 'bell: \'<path d="M18 9' in script
    assert 'searchBtn.appendChild(uiIcon("search"))' in script
    assert 'bell.appendChild(uiIcon("bell"))' in script
    assert ': uiIcon("profile")' in script
    assert 'b.append(uiIcon(iconName), node("span", "bottom-nav-label", label))' in script
    assert ".ui-icon svg" in styles
    assert ".bottom-nav-label" in styles
    assert 'href="/cinema/assets/site.css?v=10"' in source
    assert 'src="/cinema/assets/site.js?v=17"' in source


def test_cinema_responsive_layout_keeps_mobile_readable_without_breaking_desktop() -> None:
    from pathlib import Path

    root = Path(cinema_site.__file__).resolve().parent
    styles = (root / "assets" / "cinema_site.css").read_text(encoding="utf-8")
    source = Path(cinema_site.__file__).read_text(encoding="utf-8")

    assert (
        '<meta name="viewport" '
        'content="width=device-width,initial-scale=1,minimum-scale=1,'
        'viewport-fit=cover,interactive-widget=resizes-content">'
    ) in source
    assert 'href="/cinema/assets/site.css?v=10"' in source

    assert "--content:min(1560px,calc(100vw - 48px))" in styles
    assert "@media(min-width:1800px)" in styles
    assert "@media(max-width:1199px)" in styles
    assert "@media(max-width:820px)" in styles
    assert "@media(max-width:620px)" in styles
    assert "@media(max-width:390px)" in styles

    assert "grid-template-columns:minmax(0,1fr) auto" in styles
    assert ".details-grid{grid-template-columns:minmax(0,1fr)}" in styles
    assert ".details-hero{min-height:0;border-radius:19px}" in styles
    assert ".details-bg{height:280px}" in styles
    assert "padding:188px 18px 20px" in styles
    assert ".feed-title,.feed-meta,.section-sub,.details-overview,.notification-body" in styles
    assert "overflow-wrap:anywhere" in styles
    assert "grid-template-columns:repeat(5,minmax(0,1fr))" in styles
    assert "min-height:54px" in styles
    assert ".feed-result-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}" in styles
    assert ".feed-result-card{" in styles
    assert "-webkit-text-size-adjust:100%" in styles
    assert "text-size-adjust:100%" in styles


def test_full_site_auto_quality_and_source_search_controls_are_real() -> None:
    from pathlib import Path

    script = (Path(cinema_site.__file__).resolve().parent / "assets" / "cinema_site.js").read_text(encoding="utf-8")
    site_source = Path(cinema_site.__file__).read_text(encoding="utf-8")

    assert "function autoQualityMode()" in script
    assert "saveData" in script
    assert "effectiveType" in script
    assert "deviceMemory" in script
    assert "hardwareConcurrency" in script
    assert 'applyVisualQuality("auto")' in script
    assert 'input.dispatchEvent(new Event("input", { bubbles: true }))' in script
    assert "search_custom_media_sources(guild_id, query)" not in site_source
    assert "search_movie_sources(guild_id, query)" in site_source


def test_shared_playback_service_applies_saved_host_speed_without_blocking_playback() -> None:
    from pathlib import Path

    source = Path(cinema_playback_service.__file__).read_text(encoding="utf-8")

    assert "profile = await get_cinema_user(initial_host_id)" in source
    assert 'preferences.get("playback_speed")' in source
    assert 'action="speed"' in source
    assert "except (CinemaStorageUnavailable, TypeError, ValueError):" in source
    assert "Personalization must never prevent otherwise valid Cinema playback." in source


def test_notifications_keep_truthful_unread_badge_after_mark_read() -> None:
    from pathlib import Path

    script = (Path(cinema_site.__file__).resolve().parent / "assets" / "cinema_site.js").read_text(encoding="utf-8")

    assert "const unreadCount = notifications.filter((item) => !item.read_at).length;" in script
    assert "notifications_unread: unreadCount" in script
    assert "state.home = null;\n                renderNotifications();" not in script



def test_episode_search_parser_accepts_shared_cinema_notation() -> None:
    assert parse_episode_query("Example Show S3E7") == ("Example Show", 3, 7)
    assert parse_episode_query("Example Show S3 E7") == ("Example Show", 3, 7)
    assert parse_episode_query("Example Show 3x07") == ("Example Show", 3, 7)
    assert parse_episode_query("Example Show season 3 episode 7") == (
        "Example Show",
        3,
        7,
    )
    assert parse_episode_query("Example Show") is None



def test_episode_cards_preserve_exact_details_identity() -> None:
    from pathlib import Path

    script = (Path(cinema_site.__file__).resolve().parent / "assets" / "cinema_site.js").read_text(encoding="utf-8")
    styles = (Path(cinema_site.__file__).resolve().parent / "assets" / "cinema_site.css").read_text(encoding="utf-8")

    assert 'params.set("season", String(season))' in script
    assert 'params.set("episode", String(episode))' in script
    assert "requestedEpisode = null" in script
    assert 'card.classList.add("episode-current")' in script
    assert 'card.setAttribute("aria-current", "true")' in script
    assert 'params.get("episode")' in script
    assert 'current.scrollIntoView({ block: "center"' in script
    assert ".episode-card.episode-current" in styles



def test_feed_center_capabilities_health_and_refresh_queries_are_truthful() -> None:
    source = SimpleNamespace(
        source_id="external-ref",
        label="Reference Search",
        endpoint_url="https://example.org/search?q={query}",
        provider_type="external",
        category="tv",
        enabled=True,
    )
    payload = cinema_feed_service._payload(
        source,
        guild_id=123,
        include_endpoint=False,
    )

    assert payload["search_capable"] is False
    assert payload["discovery_capable"] is False
    assert payload["playback_capable"] is False
    assert payload["health_state"] == "reference"
    assert payload["supported_media_types"] == ["tv"]
    assert "endpoint_url" not in payload

    assert cinema_feed_service._default_refresh_query("movies") == "movie"
    assert cinema_feed_service._default_refresh_query("tv") == "tv"
    assert cinema_feed_service._default_refresh_query("anime") == "anime"
    assert cinema_feed_service._default_refresh_query("documentaries") == "documentary"
    assert cinema_feed_service._default_refresh_query("custom") == "movie"


def test_feed_center_structured_health_reflects_real_refresh_state(monkeypatch) -> None:
    source = SimpleNamespace(
        source_id="movie-json",
        label="Movie JSON",
        endpoint_url="https://example.org/api",
        provider_type="json",
        category="movies",
        enabled=True,
    )
    key = (456, "movie-json")
    cinema_feed_service._RUNTIME_STATE[key] = {
        "refreshed_at": 123456,
        "ok": True,
        "error": "",
        "titles": ["Example Movie"],
    }
    try:
        payload = cinema_feed_service._payload(
            source,
            guild_id=456,
            include_endpoint=True,
        )
    finally:
        cinema_feed_service._RUNTIME_STATE.pop(key, None)

    assert payload["search_capable"] is True
    assert payload["discovery_capable"] is True
    assert payload["playback_capable"] is True
    assert payload["health_state"] == "online"
    assert payload["last_refresh_ok"] is True
    assert payload["newly_discovered"] == ["Example Movie"]
    assert payload["endpoint_url"] == "https://example.org/api"


def test_rss_refresh_does_not_force_movie_query(monkeypatch) -> None:
    source = SimpleNamespace(
        source_id="eztv",
        label="EzTV",
        provider_type="feed",
        category="custom",
        enabled=True,
    )
    registry = SimpleNamespace(revision=1, sources=(source,))
    captured: list[str] = []

    async def fake_registry(_guild_id: int, *, refresh: bool = False):
        assert refresh is True
        return {}, registry

    async def fake_preview(_source, *, query: str, limit: int):
        captured.append(query)
        assert limit == 8
        return MediaSourceSearchOutcome(variants=(), errors=())

    monkeypatch.setattr(cinema_feed_service, "load_media_source_registry", fake_registry)
    monkeypatch.setattr(cinema_feed_service, "preview_custom_media_source", fake_preview)
    cinema_feed_service._RUNTIME_STATE.clear()

    asyncio.run(cinema_feed_service.refresh_feed(123, source_id="eztv"))

    assert captured == [""]
    runtime = cinema_feed_service._RUNTIME_STATE[(123, "eztv")]
    assert runtime["ok"] is True
    assert runtime["result_count"] == 0
    assert runtime["refresh_query"] == ""
    assert "no playable magnet or .torrent items" in runtime["discovery_warning"]


def test_structured_search_refresh_keeps_category_default_query(monkeypatch) -> None:
    source = SimpleNamespace(
        source_id="json-a",
        label="JSON A",
        provider_type="json",
        category="movies",
        enabled=True,
    )
    registry = SimpleNamespace(revision=1, sources=(source,))
    captured: list[str] = []

    async def fake_registry(_guild_id: int, *, refresh: bool = False):
        assert refresh is True
        return {}, registry

    async def fake_preview(_source, *, query: str, limit: int):
        captured.append(query)
        return MediaSourceSearchOutcome(variants=(), errors=())

    monkeypatch.setattr(cinema_feed_service, "load_media_source_registry", fake_registry)
    monkeypatch.setattr(cinema_feed_service, "preview_custom_media_source", fake_preview)
    cinema_feed_service._RUNTIME_STATE.clear()

    asyncio.run(cinema_feed_service.refresh_feed(123, source_id="json-a"))

    assert captured == ["movie"]


def test_feed_state_returns_real_paged_discoveries(monkeypatch) -> None:
    row = {
        "guild_id": 123,
        "source_id": "eztv",
        "discovery_key": "abc",
        "title": "Example Show",
        "media_type": "tv",
        "tmdb_id": 42,
        "metadata": {
            "source_label": "EzTV",
            "category": "tv",
            "release_title": "Example.Show.S03E09.1080p",
            "poster_url": "https://image.tmdb.org/t/p/w500/example.jpg",
            "year": 2026,
            "rating": 8.1,
        },
        "playable": True,
        "first_seen_at": "2026-10-05T21:30:00+00:00",
        "last_seen_at": "2026-10-05T21:35:00+00:00",
    }

    async def fake_registry(_guild_id: int, *, refresh: bool = False):
        return {}, SimpleNamespace(revision=7, sources=[])

    async def fake_page(_guild_id: int, *, query: str, page: int, page_size: int):
        assert query == "Example"
        assert page == 2
        assert page_size == 8
        return {
            "rows": [row],
            "query": query,
            "page": 2,
            "page_size": 8,
            "total": 17,
            "total_pages": 3,
            "has_previous": True,
            "has_next": True,
        }

    async def fake_enrich(_guild_id: int, rows, *, max_items: int = 4):
        assert max_items == 4
        return list(rows)

    monkeypatch.setattr(cinema_feed_service, "load_media_source_registry", fake_registry)
    monkeypatch.setattr(cinema_feed_service, "page_discoveries", fake_page)
    monkeypatch.setattr(cinema_feed_service, "enrich_discovery_rows", fake_enrich)

    data = asyncio.run(
        cinema_feed_service.feed_state(
            123,
            can_manage=True,
            refresh=False,
            query="Example",
            page=2,
            page_size=8,
        )
    )

    assert data["revision"] == 7
    assert data["results_warning"] == ""
    assert data["pagination"] == {
        "query": "Example",
        "page": 2,
        "page_size": 8,
        "total": 17,
        "total_pages": 3,
        "has_previous": True,
        "has_next": True,
    }
    assert len(data["results"]) == 1
    result = data["results"][0]
    assert result["source_id"] == "eztv"
    assert result["title"] == "Example Show"
    assert result["tmdb_id"] == 42
    assert result["poster_url"].endswith("example.jpg")


def test_feed_discovery_page_uses_exact_count_range_and_release_title_search(monkeypatch) -> None:
    calls: dict[str, object] = {}

    class FakeQuery:
        def select(self, columns: str, *, count: str | None = None):
            calls["select"] = (columns, count)
            return self

        def eq(self, column: str, value: object):
            calls["eq"] = (column, value)
            return self

        def or_(self, filters: str):
            calls["or"] = filters
            return self

        def order(self, column: str, *, desc: bool = False):
            calls["order"] = (column, desc)
            return self

        def range(self, start: int, end: int):
            calls["range"] = (start, end)
            return self

        def execute(self):
            return SimpleNamespace(
                data=[
                    {
                        "guild_id": 123,
                        "source_id": "eztv",
                        "discovery_key": "row",
                        "title": "Collision",
                        "metadata": {
                            "release_title": "Collision 2026 S01E21 1080p"
                        },
                    }
                ],
                count=17,
            )

    class FakeClient:
        def table(self, name: str):
            calls["table"] = name
            return FakeQuery()

    async def fake_execute(_label: str, operation):
        return operation(FakeClient())

    monkeypatch.setattr(cinema_discovery_service, "execute", fake_execute)

    page = asyncio.run(
        cinema_discovery_service.page_discoveries(
            123,
            query="S01E21",
            page=2,
            page_size=8,
        )
    )

    assert calls["select"] == ("*", "exact")
    assert calls["eq"] == ("guild_id", 123)
    assert calls["range"] == (8, 15)
    assert "title.ilike.%S01E21%" in str(calls["or"])
    assert "metadata->>release_title.ilike.%S01E21%" in str(calls["or"])
    assert page["total"] == 17
    assert page["page"] == 2
    assert page["total_pages"] == 3
    assert page["has_previous"] is True
    assert page["has_next"] is True


def test_episode_release_enrichment_retries_without_year_and_forces_tv(monkeypatch) -> None:
    queries: list[str] = []

    async def fake_search(query: str, *, limit: int, include_adult: bool):
        queries.append(query)
        assert limit == 8
        assert include_adult is False
        if query == "collision":
            return (
                CinemaMedia(
                    media_type="tv",
                    tmdb_id=331033,
                    title="Collision",
                    year=2026,
                    poster_url="https://image.tmdb.org/t/p/w500/collision.jpg",
                ),
            )
        return ()

    monkeypatch.setattr(cinema_discovery_service, "search_catalog", fake_search)

    media = asyncio.run(
        cinema_discovery_service._resolve_media(
            "Collision 2026 S01E21 1080p HEVC x265-MeGusta",
            "custom",
        )
    )

    assert queries[0] == "collision"
    assert media is not None
    assert media.media_type == "tv"
    assert media.tmdb_id == 331033
    assert media.poster_url.endswith("collision.jpg")


def test_feed_discovery_query_builder_detects_episode_release() -> None:
    assert cinema_discovery_service._looks_like_episode_release(
        "Collision.2026.S01E21.1080p"
    )
    assert cinema_discovery_service._discovery_search_queries(
        "Collision 2026 S01E21 1080p HEVC"
    ) == ("collision", "collision 2026")


def test_runtime_feed_result_preserves_real_variant_stats() -> None:
    variant = ResolvedMediaVariant(
        title="Example Movie 2026 1080p",
        source_id="rss-a",
        source_label="RSS A",
        source_ref="magnet:?xt=urn:btih:" + ("a" * 40),
        file_size=4 * 1024 * 1024 * 1024,
        seeds=22,
        leechers=4,
        peers=26,
        metadata={"release_name": {"year": 2026}},
    )

    result = cinema_feed_service._runtime_result_payload(
        variant,
        category="movies",
    )

    assert result["title"] == "Example Movie 2026 1080p"
    assert result["source_label"] == "RSS A"
    assert result["category"] == "movies"
    assert result["year"] == 2026
    assert result["seeds"] == 22
    assert result["leechers"] == 4
    assert result["peers"] == 26
    assert result["file_size"] == 4 * 1024 * 1024 * 1024
    assert "source_ref" not in result


def test_full_site_feed_center_uses_real_search_pagination_and_source_capabilities() -> None:
    from pathlib import Path

    script = (Path(cinema_site.__file__).resolve().parent / "assets" / "cinema_site.js").read_text(encoding="utf-8")

    assert "function sourceHealthLabel(source)" in script
    assert 'reference: "Reference link"' in script
    assert 'source.search_capable ? "Search" : ""' in script
    assert 'source.provider_type !== "external"' in script
    assert "function feedResultCard(result)" in script
    assert '"Latest Feed Results"' in script
    assert 'saved feed result' in script
    assert 'matching “" + state.feedQuery + "”.' in script
    assert '"No feed results yet. Refresh an enabled RSS or structured source below' in script
    assert '"Search in Cinema"' in script
    assert '"View Details"' in script
    assert "last_refresh_result_count" in script
    assert "playable result" in script
    assert "source.discovery_warning" in script
    assert '"Search feed results…"' in script
    assert '"Previous"' in script
    assert '"Next"' in script
    assert '"feed-page-status"' in script
    assert "pagination.total_pages" in script
    assert 'page_size: "8"' in script
    assert 'feedAction({ action: "refresh", source_id: source.source_id })' in script
    assert 'query: "movie"' not in script
    assert "Supports:" in script



def test_same_title_movies_keep_distinct_canonical_room_identity(monkeypatch) -> None:
    manager = MovieNightManager()
    room = manager.create_room(
        guild_id=100,
        channel_id=200,
        host_id=42,
        stream_token="",
    )
    monkeypatch.setattr(cinema_playback_service, "get_movie_night_manager", lambda: manager)

    first = CinemaMedia(media_type="movie", tmdb_id=1001, title="Halloween", year=1978)
    second = CinemaMedia(media_type="movie", tmdb_id=2002, title="Halloween", year=2007)

    for media, token in ((first, "4"), (second, "5")):
        metadata = cinema_playback_service.catalog_metadata(media)
        outcome = MediaSourceSearchOutcome(
            variants=(
                ResolvedMediaVariant(
                    title=f"Halloween.{media.year}.1080p",
                    source_id=f"source-{media.tmdb_id}",
                    source_label="Provider",
                    source_ref="magnet:?xt=urn:btih:" + token * 40,
                    file_size=1000,
                    seeds=10,
                    leechers=1,
                    peers=11,
                    metadata={},
                ),
            )
        )
        cinema_playback_service.materialize_search_results(
            room,
            outcome,
            proposer_id=42,
            query=f"Halloween {media.year}",
            catalog_metadata=metadata,
        )

    first_candidate = cinema_playback_service.find_catalog_candidate(
        room,
        cinema_playback_service.catalog_metadata(first),
    )
    second_candidate = cinema_playback_service.find_catalog_candidate(
        room,
        cinema_playback_service.catalog_metadata(second),
    )

    assert first_candidate is not None
    assert second_candidate is not None
    assert first_candidate.candidate_id != second_candidate.candidate_id
    assert len(room.candidates) == 2


def test_theater_queue_search_returns_movies_without_fake_series_queue_items(monkeypatch) -> None:
    room = SimpleNamespace(guild_id=100, host_id=42)

    async def room_and_user(_request):
        return room, 42

    async def catalog(_query, *, limit=30, include_adult=False):
        _ = (limit, include_adult)
        return (
            CinemaMedia(media_type="tv", tmdb_id=77, title="Example Show"),
            CinemaMedia(media_type="movie", tmdb_id=123, title="Example Movie", year=2026),
        )

    monkeypatch.setattr(movie_night_web, "_room_and_user", room_and_user)
    monkeypatch.setattr(movie_night_web, "search_cinema_catalog", catalog)

    response = asyncio.run(
        movie_night_web.movie_night_queue_search(
            SimpleNamespace(query={"q": "Example"})
        )
    )
    payload = __import__("json").loads(response.text)

    assert [item["media_type"] for item in payload["results"]] == ["movie"]
    assert payload["results"][0]["tmdb_id"] == 123
    assert "exact episode" in payload["hint"].lower()


def test_theater_queue_add_materializes_exact_playable_movie(monkeypatch) -> None:
    manager = MovieNightManager()
    room = manager.create_room(
        guild_id=100,
        channel_id=200,
        host_id=42,
        stream_token="",
    )
    movie = CinemaMedia(media_type="movie", tmdb_id=123, title="Example Movie", year=2026)
    details = CinemaDetails(media=movie)
    metadata = cinema_playback_service.catalog_metadata(movie)
    outcome = MediaSourceSearchOutcome(
        variants=(
            ResolvedMediaVariant(
                title="Example.Movie.2026.1080p",
                source_id="provider",
                source_label="Provider",
                source_ref="magnet:?xt=urn:btih:" + "6" * 40,
                file_size=1000,
                seeds=20,
                leechers=2,
                peers=22,
                metadata={},
            ),
        )
    )

    async def room_and_user(_request):
        return room, 42

    async def get_details(_kind, _tmdb_id):
        return details

    async def exact_sources(_guild_id, *, media):
        assert media.tmdb_id == 123
        return metadata, "Example Movie", outcome

    async def state_payload(current_room, uid):
        return {"queue": list(current_room.queue), "uid": uid}

    class Request:
        async def json(self):
            return {
                "action": "add",
                "media_type": "movie",
                "tmdb_id": 123,
            }

    monkeypatch.setattr(movie_night_web, "_room_and_user", room_and_user)
    monkeypatch.setattr(movie_night_web, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(movie_night_web, "get_cinema_details", get_details)
    monkeypatch.setattr(movie_night_web, "search_exact_movie_sources", exact_sources)
    monkeypatch.setattr(movie_night_web, "_state_payload", state_payload)
    monkeypatch.setattr(cinema_playback_service, "get_movie_night_manager", lambda: manager)

    response = asyncio.run(movie_night_web.movie_night_queue_action(Request()))
    payload = __import__("json").loads(response.text)

    assert len(payload["queue"]) == 1
    candidate = cinema_playback_service.find_catalog_candidate(room, metadata)
    assert candidate is not None
    assert room.queue == [candidate.candidate_id]
    assert manager.ranked_variants(room.room_id, candidate.candidate_id)


def test_theater_queue_play_next_prioritizes_selected_item(monkeypatch) -> None:
    manager = MovieNightManager()
    room = manager.create_room(
        guild_id=100,
        channel_id=200,
        host_id=42,
        stream_token="",
    )
    candidates = [
        manager.nominate(
            room.room_id,
            user_id=42,
            title=f"Movie {index}",
            auto_vote=False,
        )
        for index in range(1, 4)
    ]
    for candidate in candidates:
        manager.queue_winner(room.room_id, candidate_id=candidate.candidate_id)

    async def room_and_user(_request):
        return room, 42

    async def state_payload(current_room, uid):
        return {"queue": list(current_room.queue), "uid": uid}

    class Request:
        async def json(self):
            return {
                "action": "play_next",
                "candidate_id": candidates[2].candidate_id,
            }

    monkeypatch.setattr(movie_night_web, "_room_and_user", room_and_user)
    monkeypatch.setattr(movie_night_web, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(movie_night_web, "_state_payload", state_payload)

    response = asyncio.run(movie_night_web.movie_night_queue_action(Request()))
    payload = __import__("json").loads(response.text)

    assert payload["queue"][0] == candidates[2].candidate_id
    assert set(payload["queue"]) == {candidate.candidate_id for candidate in candidates}



def test_preferred_source_selection_is_shared_and_falls_back_safely(monkeypatch) -> None:
    first = SimpleNamespace(source_id="alpha", source_label="Alpha Source")
    preferred = SimpleNamespace(source_id="beta", source_label="Beta Premium")

    async def profile(_user_id: int):
        return {"preferences": {"preferred_source": "beta"}}

    monkeypatch.setattr(cinema_playback_service, "get_cinema_user", profile)
    chosen = asyncio.run(
        cinema_playback_service.select_preferred_variant(
            42,
            [first, preferred],
        )
    )
    assert chosen is preferred

    async def no_match(_user_id: int):
        return {"preferences": {"preferred_source": "missing"}}

    monkeypatch.setattr(cinema_playback_service, "get_cinema_user", no_match)
    fallback = asyncio.run(
        cinema_playback_service.select_preferred_variant(
            42,
            [first, preferred],
        )
    )
    assert fallback is first



def test_adult_policy_filters_saved_library_and_provider_results() -> None:
    snapshot = {
        "watchlist": [
            {"title": "Safe", "metadata": {"adult": False}},
            {"title": "Adult", "metadata": {"adult": True}},
        ],
        "continue_watching": [
            {"title": "Adult Episode", "metadata": {"adult": True}},
        ],
        "recently_watched": [
            {"title": "Safe History", "metadata": {}},
        ],
        "watch_again": [],
        "series_progress": [],
    }
    filtered = cinema_site._filter_library_snapshot_for_policy(
        snapshot,
        adult_enabled=False,
    )
    assert [row["title"] for row in filtered["watchlist"]] == ["Safe"]
    assert filtered["continue_watching"] == []
    assert [row["title"] for row in filtered["recently_watched"]] == ["Safe History"]

    adult_variant = ResolvedMediaVariant(
        title="Example.Movie.XXX.1080p",
        source_id="adult",
        source_label="Adult",
        source_ref="magnet:?xt=urn:btih:" + "7" * 40,
        file_size=100,
        seeds=1,
        leechers=1,
        peers=2,
        metadata={"category": "adult"},
    )
    safe_variant = ResolvedMediaVariant(
        title="Example.Movie.2026.1080p",
        source_id="safe",
        source_label="Safe",
        source_ref="magnet:?xt=urn:btih:" + "8" * 40,
        file_size=100,
        seeds=1,
        leechers=1,
        peers=2,
        metadata={},
    )
    from stoney_verify.cinema_media_identity import filter_adult_provider_results

    outcome = filter_adult_provider_results(
        MediaSourceSearchOutcome(variants=(adult_variant, safe_variant)),
        enabled=False,
    )
    assert outcome.variants == (safe_variant,)


def test_episode_identity_preserves_series_adult_flag() -> None:
    adult_series = CinemaMedia(
        media_type="tv",
        tmdb_id=77,
        title="Adult Series",
        adult=True,
    )
    metadata = episode_catalog_metadata(
        series=adult_series,
        episode=_episode(),
    )
    assert metadata["adult"] is True


def test_full_site_notification_ui_uses_live_room_action() -> None:
    from pathlib import Path

    script = (Path(cinema_site.__file__).resolve().parent / "assets" / "cinema_site.js").read_text(encoding="utf-8")
    source = Path(cinema_site.__file__).read_text(encoding="utf-8")

    assert 'action.kind === "room" && action.watch_url' in script
    assert 'button("Join Theater"' in script
    assert "location.href = action.watch_url" in script
    assert "guild_id=_guild_id" in source
    assert 'action["watch_url"] = movie_night_watch_url' in source
    assert 'action["available"] = False' in source


def test_cinema_search_and_queue_honor_shared_adult_policy() -> None:
    from pathlib import Path

    site_source = Path(cinema_site.__file__).read_text(encoding="utf-8")
    watch_source = Path(movie_night_web.__file__).read_text(encoding="utf-8")

    assert "looks_explicit_adult(query)" in site_source
    assert "include_adult=adult_enabled" in site_source
    assert "filter_adult_provider_results(" in site_source
    assert "looks_explicit_adult(query)" in watch_source
    assert "include_adult=adult_enabled" in watch_source
    assert "Adult-content Cinema search is disabled for this server." in watch_source



def test_theater_queue_add_rejects_malformed_identity_as_bad_request(monkeypatch) -> None:
    from aiohttp import web

    room = SimpleNamespace(room_id="room-1", guild_id=100, host_id=42)

    async def room_and_user(_request):
        return room, 42

    class Request:
        async def json(self):
            return {
                "action": "add",
                "media_type": "movie",
                "tmdb_id": "not-a-number",
            }

    monkeypatch.setattr(movie_night_web, "_room_and_user", room_and_user)
    monkeypatch.setattr(
        movie_night_web,
        "get_movie_night_manager",
        lambda: SimpleNamespace(),
    )

    try:
        asyncio.run(movie_night_web.movie_night_queue_action(Request()))
    except Exception as exc:
        assert isinstance(exc, web.HTTPBadRequest)
        assert "tmdb_id" in exc.text
    else:
        raise AssertionError("Malformed Queue Add identity must be rejected as HTTP 400.")


def test_watch_player_is_the_only_durable_playback_progress_writer() -> None:
    from pathlib import Path

    site_source = Path(cinema_site.__file__).read_text(encoding="utf-8")
    watch_source = Path(movie_night_web.__file__).read_text(encoding="utf-8")
    script = (Path(cinema_site.__file__).resolve().parent / "assets" / "cinema_site.js").read_text(encoding="utf-8")

    assert 'if action == "progress":' not in site_source
    assert 'action: "progress"' not in script
    assert 'app.router.add_post("/movie/{room_id}/progress", movie_night_progress)' in watch_source
    assert "record_progress(" in watch_source
