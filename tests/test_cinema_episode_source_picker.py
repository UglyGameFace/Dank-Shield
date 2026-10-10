from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp import web

from stoney_verify import cinema_playback_service, cinema_site
from stoney_verify.cinema_catalog import CinemaDetails, CinemaEpisode, CinemaMedia
from stoney_verify.cinema_media_identity import episode_catalog_metadata
from stoney_verify.media_metadata import parse_release_name
from stoney_verify.media_source_resolver import MediaSourceSearchOutcome, ResolvedMediaVariant
from stoney_verify.movie_night import MovieNightManager


def _episode_fixture(monkeypatch, *, adult=False):
    series = CinemaMedia(
        media_type="tv", tmdb_id=77, title="Example Show",
        year=2026, adult=adult,
    )
    episode = CinemaEpisode(
        series_id=77, season_number=1, episode_number=1,
        tmdb_id=901, title="Pilot",
    )
    risky_ref = "magnet:?xt=urn:btih:" + "a" * 40
    safe_ref = "magnet:?xt=urn:btih:" + "b" * 40
    variants = tuple(
        ResolvedMediaVariant(
            title=name, source_id=provider, source_label=provider,
            source_ref=ref, file_size=1000, seeds=seeds,
            leechers=2, peers=seeds + 2,
            metadata={"release_name": parse_release_name(name)},
        )
        for name, provider, ref, seeds in (
            ("Example.Show.S01E01.1080p.HEVC.x265.mkv", "risky", risky_ref, 80),
            ("Example.Show.S01E01.720p.H264.x264.mp4", "alternative", safe_ref, 6),
        )
    )
    outcome = MediaSourceSearchOutcome(variants=variants)
    search_calls = []

    async def site_identity(_request):
        return 100, 42

    async def details(media_type, tmdb_id):
        assert (media_type, tmdb_id) == ("tv", 77)
        return CinemaDetails(media=series)

    async def season(series_id, season_number):
        assert (series_id, season_number) == (77, 1)
        return [episode]

    async def adult_enabled(_guild_id):
        return False

    async def exact(guild_id, *, series, episode):
        assert guild_id == 100
        assert series.tmdb_id == 77
        assert episode.tmdb_id == 901
        search_calls.append(True)
        return episode_catalog_metadata(series=series, episode=episode), "Example Show S01E01", outcome

    monkeypatch.setattr(cinema_site, "_site_identity", site_identity)
    monkeypatch.setattr(cinema_site, "get_details", details)
    monkeypatch.setattr(cinema_site, "get_season", season)
    monkeypatch.setattr(cinema_site, "_guild_adult_content_enabled", adult_enabled)
    monkeypatch.setattr(cinema_site, "search_exact_episode_sources", exact)
    cinema_site._SOURCE_SNAPSHOT_CACHE.clear()
    return episode, search_calls, risky_ref, safe_ref


def _request(tmdb_id=901):
    return SimpleNamespace(
        match_info={"series_id": "77", "season_number": "1", "episode_number": "1"},
        query={"tmdb_id": str(tmdb_id)},
    )


def test_episode_source_picker_lists_exact_episode_without_raw_media_urls(monkeypatch):
    _ep, searches, risky_ref, safe_ref = _episode_fixture(monkeypatch)

    response = asyncio.run(cinema_site.cinema_episode_sources_api(_request()))
    data = json.loads(response.text)

    assert response.status == 200
    assert data["episode"] == {
        "series_id": 77, "season_number": 1, "episode_number": 1,
        "tmdb_id": 901,
    }
    assert len(data["sources"]) == 2
    # Browser compatibility comes before a misleading high provider seed count.
    assert data["sources"][0]["source_id"] == "alternative"
    assert data["sources"][0]["video_risk"] == "unverified"
    assert data["sources"][1]["source_id"] == "risky"
    assert data["sources"][1]["video_risk"] == "risky"
    assert all(len(row["source_choice"]) == 24 for row in data["sources"])
    assert risky_ref not in response.text and safe_ref not in response.text
    assert len(searches) == 1


def test_episode_manual_source_choice_uses_picker_snapshot_and_exact_identity(monkeypatch):
    _ep, searches, _risky_ref, safe_ref = _episode_fixture(monkeypatch)
    monkeypatch.setenv("DANK_MEDIA_PUBLIC_BASE_URL", "https://cinema.example")
    monkeypatch.setenv("DANK_TORRENT_STREAM_SECRET", "cinema-test-secret")
    manager = MovieNightManager()
    monkeypatch.setattr(cinema_site, "get_movie_night_manager", lambda: manager)
    monkeypatch.setattr(cinema_playback_service, "get_movie_night_manager", lambda: manager)
    expected_ref = safe_ref
    started = []

    async def start_variant(room_id, *, actor_id, candidate_id, variant_id, automatic=False):
        room = manager.get(room_id)
        assert room is not None and room.host_id == actor_id == 42
        chosen = room.candidates[candidate_id].variants[variant_id]
        started.append(chosen.source_ref)
        return SimpleNamespace(room=room)

    monkeypatch.setattr(cinema_site, "start_room_variant", start_variant)
    listed = json.loads(
        asyncio.run(cinema_site.cinema_episode_sources_api(_request())).text
    )
    choice = listed["sources"][0]["source_choice"]

    class PlayRequest:
        async def json(self):
            return {
                "media_type": "episode", "series_id": 77, "season_number": 1,
                "episode_number": 1, "tmdb_id": 901,
                "source_choice": choice,
            }

    response = asyncio.run(cinema_site.cinema_play_api(PlayRequest()))
    result = json.loads(response.text)

    assert result["source"]["selection_mode"] == "manual"
    assert result["source"]["source_id"] == "alternative"
    assert started == [expected_ref]
    assert len(searches) == 1  # Picker -> Play uses one bounded, one-shot snapshot.
    assert "magnet:?" not in response.text
    assert cinema_site._SOURCE_SNAPSHOT_CACHE == {}


def test_episode_picker_rejects_stale_episode_identity_before_provider_search(monkeypatch):
    _ep, searches, _risky, _safe = _episode_fixture(monkeypatch)
    with pytest.raises(web.HTTPConflict):
        asyncio.run(cinema_site.cinema_episode_sources_api(_request(tmdb_id=999)))
    assert searches == []


def test_episode_picker_obeys_guild_adult_policy(monkeypatch):
    _ep, searches, _risky, _safe = _episode_fixture(monkeypatch, adult=True)
    with pytest.raises(web.HTTPForbidden):
        asyncio.run(cinema_site.cinema_episode_sources_api(_request()))
    assert searches == []


def test_episode_picker_is_lazy_and_has_an_explicit_manual_play_action():
    source = (Path(__file__).resolve().parents[1] / "stoney_verify/assets/cinema_site.js").read_text()
    assert 'button("Choose Release"' in source
    assert "chooseRelease.addEventListener(\"click\", async (event) => {" in source
    assert "if (!opening || sourcesLoaded) return;" in source
    assert "playOnSite(episodeItem, row, source.source_choice)" in source
    assert 'sourcePanel.addEventListener("click", (event) => event.stopPropagation())' in source
    assert "source.video_risk === \"risky\"" in source
