from __future__ import annotations

import asyncio
from types import SimpleNamespace

from stoney_verify import cinema_catalog, cinema_site, movie_night_web
from stoney_verify.cinema_catalog import CinemaDetails, CinemaEpisode, CinemaMedia
from stoney_verify.cinema_media_identity import (
    episode_catalog_metadata,
    filter_outcome_for_catalog,
    release_matches_catalog,
)
from stoney_verify import cinema_playback_service
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


def test_cinema_site_play_requires_existing_host_room(monkeypatch) -> None:
    manager = MovieNightManager()
    room = manager.create_room(
        guild_id=100,
        channel_id=200,
        host_id=42,
        stream_token="",
    )
    monkeypatch.setattr(cinema_site, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(cinema_site, "_site_identity", lambda _request: (100, 99))

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
        assert "room that you host" in exc.text
    else:
        raise AssertionError("A non-host site identity must not replace Cinema media.")


def test_full_site_episode_playback_is_real_and_host_scoped() -> None:
    from pathlib import Path

    root = Path(cinema_site.__file__).resolve().parent
    script = (root / "assets" / "cinema_site.js").read_text(encoding="utf-8")
    styles = (root / "assets" / "cinema_site.css").read_text(encoding="utf-8")

    assert 'await api("/play"' in script
    assert 'media_type: mediaType' in script
    assert 'payload.series_id = Number' in script
    assert 'payload.season_number = Number' in script
    assert 'payload.episode_number = Number' in script
    assert 'hostSession?.is_host' in script
    assert '"▶ Resume in Theater"' in script
    assert '"▶ Play in Theater"' in script
    assert ".episode-play" in styles


def test_tv_details_do_not_claim_series_title_is_a_playable_source() -> None:
    source = __import__("pathlib").Path(cinema_site.__file__).read_text(encoding="utf-8")

    assert 'if media_type == "movie":' in source
    assert "filter_outcome_for_catalog(" in source
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
