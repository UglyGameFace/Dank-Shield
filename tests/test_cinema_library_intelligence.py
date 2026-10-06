from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from stoney_verify import (
    cinema_feed_personalization,
    cinema_library_intelligence,
    cinema_library_service,
    cinema_site,
)
from stoney_verify.cinema_catalog import CinemaEpisode, CinemaMedia


def test_library_snapshot_exposes_favorites_ratings_and_rewatch_state() -> None:
    rows = [
        {
            "user_id": 42,
            "media_type": "movie",
            "tmdb_id": 123,
            "season_number": 0,
            "episode_number": 0,
            "title": "Example Movie",
            "metadata": {"genres": ["Crime"], "poster_url": "poster"},
            "progress_seconds": 0,
            "duration_seconds": 7200,
            "completed": True,
            "watchlisted": True,
            "favorite": True,
            "rating": 9,
            "play_count": 2,
            "last_watched_at": "2026-10-05T20:00:00+00:00",
            "last_completed_at": "2026-10-05T20:00:00+00:00",
            "watchlisted_at": "2026-10-01T20:00:00+00:00",
            "favorite_at": "2026-10-05T20:00:00+00:00",
            "rated_at": "2026-10-05T20:00:00+00:00",
        }
    ]

    snapshot = cinema_library_service.library_snapshot_from_rows(rows)

    assert snapshot["watchlist"][0]["tmdb_id"] == 123
    assert snapshot["favorites"][0]["favorite"] is True
    assert snapshot["rated"][0]["rating"] == 9
    assert snapshot["watch_again"][0]["play_count"] == 2


def test_completion_transition_increments_play_count_once(monkeypatch) -> None:
    async def fake_read(**_kwargs):
        return {
            "completed": False,
            "play_count": 1,
            "first_watched_at": "2026-09-01T00:00:00+00:00",
        }

    async def fake_execute(_label, _action):
        return SimpleNamespace(data=[])

    captured: dict[str, object] = {}

    async def fake_session(_user_id, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(cinema_library_service, "_read_media_row", fake_read)
    monkeypatch.setattr(cinema_library_service, "_execute", fake_execute)
    monkeypatch.setattr(cinema_library_service, "record_watch_session", fake_session)

    result = asyncio.run(
        cinema_library_service.record_progress(
            42,
            media_type="movie",
            tmdb_id=123,
            title="Example Movie",
            progress_seconds=7200,
            duration_seconds=7200,
            completed=True,
            metadata={"genres": ["Crime"]},
            activity_context={
                "session_key": "room:candidate",
                "guild_id": 100,
                "room_id": "room",
                "session_mode": "watch_party",
                "candidate_id": "candidate",
                "is_host": True,
            },
        )
    )

    assert result["completed"] is True
    assert result["play_count"] == 2
    assert result["first_watched_at"] == "2026-09-01T00:00:00+00:00"
    assert result["last_completed_at"]
    assert captured["completed"] is True
    assert captured["context"]["session_mode"] == "watch_party"


def test_partial_progress_does_not_add_completion_read(monkeypatch) -> None:
    async def forbidden_read(**_kwargs):
        raise AssertionError("partial progress must not read current media state")

    async def fake_execute(_label, _action):
        return SimpleNamespace(data=[])

    monkeypatch.setattr(cinema_library_service, "_read_media_row", forbidden_read)
    monkeypatch.setattr(cinema_library_service, "_execute", fake_execute)

    result = asyncio.run(
        cinema_library_service.record_progress(
            42,
            media_type="movie",
            tmdb_id=123,
            title="Example Movie",
            progress_seconds=300,
            duration_seconds=7200,
            completed=False,
        )
    )

    assert result["completed"] is False
    assert result["progress_seconds"] == 300


def test_mark_unwatched_clears_completion_count(monkeypatch) -> None:
    async def fake_read(**_kwargs):
        return {
            "completed": True,
            "play_count": 3,
            "first_watched_at": "2026-08-01T00:00:00+00:00",
            "last_completed_at": "2026-10-01T00:00:00+00:00",
            "metadata": {"genres": ["Drama"]},
        }

    captured: dict[str, object] = {}

    async def fake_write(_user_id, **kwargs):
        captured.update(kwargs["patch"])
        return dict(kwargs["patch"])

    monkeypatch.setattr(cinema_library_service, "_read_media_row", fake_read)
    monkeypatch.setattr(cinema_library_service, "_write_media_patch", fake_write)

    result = asyncio.run(
        cinema_library_service.mark_watched(
            42,
            media_type="movie",
            tmdb_id=123,
            title="Example Movie",
            watched=False,
        )
    )

    assert result["completed"] is False
    assert result["play_count"] == 0
    assert result["first_watched_at"] is None
    assert result["last_completed_at"] is None


def test_library_stats_use_session_history_without_double_querying() -> None:
    media_rows = [
        {
            "media_type": "movie",
            "tmdb_id": 123,
            "play_count": 2,
            "favorite": True,
            "rating": 9,
            "metadata": {"genres": ["Crime", "Drama"]},
        },
        {
            "media_type": "episode",
            "tmdb_id": 9001,
            "play_count": 1,
            "favorite": False,
            "rating": 8,
            "metadata": {"series_id": 77, "genres": ["Drama"]},
        },
    ]
    sessions = [
        {
            "session_mode": "watch_party",
            "max_progress_seconds": 7200,
            "duration_seconds": 7200,
        },
        {
            "session_mode": "private",
            "max_progress_seconds": 1800,
            "duration_seconds": 2700,
        },
        {
            "session_mode": "standalone",
            "max_progress_seconds": 0,
            "duration_seconds": 0,
        },
    ]

    stats = asyncio.run(
        cinema_library_service.library_stats(
            42,
            media_rows=media_rows,
            sessions=sessions,
        )
    )

    assert stats["movies_watched"] == 1
    assert stats["episodes_watched"] == 1
    assert stats["total_completions"] == 3
    assert stats["rewatches"] == 1
    assert stats["watch_hours"] == 2.5
    assert stats["favorites"] == 1
    assert stats["average_rating"] == 8.5
    assert stats["watch_party_sessions"] == 1
    assert stats["private_sessions"] == 1
    assert stats["standalone_sessions"] == 1
    assert stats["top_genres"][0]["name"] in {"Crime", "Drama"}


def test_library_intelligence_preserves_standalone_session_mode() -> None:
    payload = cinema_library_intelligence._session_payload(
        {
            "id": "session-1",
            "media_type": "movie",
            "tmdb_id": 123,
            "title": "Example Movie",
            "guild_id": 100,
            "room_id": "site-room",
            "session_mode": "standalone",
            "is_host": True,
            "max_progress_seconds": 120,
            "duration_seconds": 7200,
            "metadata": {},
        }
    )

    assert payload["session_mode"] == "standalone"
    assert payload["room_id"] == "site-room"
    assert payload["is_host"] is True


def test_upcoming_episode_uses_feed_to_distinguish_available_from_aired(monkeypatch) -> None:
    series = CinemaMedia(
        media_type="tv",
        tmdb_id=77,
        title="Example Show",
        year=2026,
        poster_url="poster",
        backdrop_url="backdrop",
    )
    next_episode = CinemaEpisode(
        series_id=77,
        season_number=1,
        episode_number=2,
        tmdb_id=9002,
        title="Second",
        air_date=date.today().isoformat(),
        still_url="still",
    )

    async def fake_details(_kind, _tmdb_id):
        return SimpleNamespace(media=series, seasons=())

    async def fake_next(_series_id, _season, _episode):
        return next_episode

    monkeypatch.setattr(cinema_library_intelligence, "get_details", fake_details)
    monkeypatch.setattr(cinema_library_intelligence, "get_next_episode", fake_next)

    rows = [
        {
            "media_type": "episode",
            "tmdb_id": 9001,
            "season_number": 1,
            "episode_number": 1,
            "last_watched_at": "2026-10-05T20:00:00+00:00",
            "metadata": {"series_id": 77},
        }
    ]
    feed_groups = [
        {
            "media_type": "tv",
            "tmdb_id": 77,
            "season_number": 1,
            "episode_number": 2,
            "release_count": 2,
            "best_quality": "1080p",
        }
    ]

    result = asyncio.run(
        cinema_library_intelligence.upcoming_episode_rows(
            100,
            42,
            include_adult=False,
            media_rows=rows,
            availability_groups=feed_groups,
        )
    )

    assert result[0]["availability_status"] == "available"
    assert result[0]["availability_label"] == "Available now"
    assert result[0]["available_release_count"] == 2
    assert result[0]["best_quality"] == "1080p"


def test_group_recommendations_only_expose_aggregate_fit(monkeypatch) -> None:
    seed = CinemaMedia(media_type="movie", tmdb_id=10, title="Seed")
    candidate = CinemaMedia(
        media_type="movie",
        tmdb_id=20,
        title="Group Pick",
        rating=8.0,
    )

    async def fake_media(user_id, **_kwargs):
        return [
            {
                "media_type": "movie",
                "tmdb_id": 10,
                "title": "Seed",
                "favorite": True,
                "rating": 9 if int(user_id) == 1 else 8,
                "play_count": 1,
            }
        ]

    async def fake_details(_kind, _tmdb_id):
        return SimpleNamespace(media=seed, recommendations=(candidate,))

    monkeypatch.setattr(cinema_library_intelligence, "list_user_media", fake_media)
    monkeypatch.setattr(cinema_library_intelligence, "get_details", fake_details)

    result = asyncio.run(
        cinema_library_intelligence.group_recommendations(
            100,
            [1, 2],
            include_adult=False,
            availability_groups=[],
        )
    )

    assert result[0]["title"] == "Group Pick"
    assert result[0]["viewer_fit"] == 2
    assert result[0]["viewer_total"] == 2
    assert result[0]["reason"] == "Fits 2 of 2 viewers"
    assert "user_id" not in result[0]
    assert "user_ids" not in result[0]


def test_my_feed_uses_favorites_ratings_and_viewing_history_without_alert_spam(monkeypatch) -> None:
    async def fake_rules(_guild_id, _user_id, **_kwargs):
        return []

    async def fake_library(_user_id):
        return {
            "watchlist": [],
            "favorites": [
                {
                    "media_type": "tv",
                    "tmdb_id": 77,
                }
            ],
            "rated": [
                {
                    "media_type": "movie",
                    "tmdb_id": 123,
                    "rating": 9,
                }
            ],
            "recently_watched": [
                {
                    "media_type": "episode",
                    "tmdb_id": 9001,
                    "metadata": {"series_id": 77},
                }
            ],
        }

    async def fake_profile(_user_id):
        return {"preferences": {"feed_playable_only": True}}

    monkeypatch.setattr(cinema_feed_personalization, "list_feed_rules", fake_rules)
    monkeypatch.setattr(cinema_feed_personalization, "library_snapshot", fake_library)
    monkeypatch.setattr(cinema_feed_personalization, "get_cinema_user", fake_profile)

    results = [
        {
            "title": "Example Show",
            "media_type": "tv",
            "tmdb_id": 77,
            "source_id": "feed-tv",
            "release_title": "Example.Show.S01E02.1080p.WEB.x265-GROUP.mkv",
            "playable": True,
        },
        {
            "title": "Example Movie",
            "media_type": "movie",
            "tmdb_id": 123,
            "source_id": "feed-movie",
            "release_title": "Example.Movie.2026.1080p.WEB.x265-GROUP.mkv",
            "playable": True,
        },
    ]

    snapshot = asyncio.run(
        cinema_feed_personalization.build_personalized_feed(
            100,
            42,
            results,
        )
    )

    reasons = {
        item["tmdb_id"]: set(item.get("match_reasons") or [])
        for item in snapshot["my_feed"]
    }
    assert {"Favorite", "From your viewing history"} <= reasons[77]
    assert "Highly rated" in reasons[123]
    assert snapshot["queue_suggestion_count"] == 2


def test_library_intelligence_schema_is_service_role_only_and_idempotent() -> None:
    root = Path(cinema_site.__file__).resolve().parents[1]
    migration = (
        root
        / "supabase"
        / "migrations"
        / "20261006050000_dank_cinema_library_intelligence.sql"
    ).read_text(encoding="utf-8")

    for column in (
        "favorite",
        "rating",
        "play_count",
        "first_watched_at",
        "last_completed_at",
    ):
        assert f"add column if not exists {column}" in migration

    for table in (
        "dank_cinema_watch_sessions",
        "dank_cinema_lists",
        "dank_cinema_list_items",
    ):
        assert f"create table if not exists public.{table}" in migration
        assert f"alter table public.{table} enable row level security" in migration
        assert f"revoke all on table public.{table} from anon, authenticated" in migration
        assert f"grant all on table public.{table} to service_role" in migration


def test_cinema_client_exposes_bot_native_library_controls_without_raw_sources() -> None:
    root = Path(cinema_site.__file__).resolve().parent
    script = (root / "assets" / "cinema_site.js").read_text(encoding="utf-8")

    for expected in (
        '"Favorites"',
        '"Upcoming"',
        '"History"',
        '"Ratings"',
        '"Lists"',
        '"Stats"',
        '"Because You Watched"',
        '"Recommended For You"',
        '"Add to List"',
        '"Mark Watched"',
        '"Rate 1–10"',
        '"Create List"',
    ):
        assert expected in script

    for action in (
        'action: "favorite"',
        'action: "rating"',
        'action: "watched"',
        'action: "save_list"',
        'action: "delete_list"',
        'action: "list_item"',
    ):
        assert action in script

    assert "magnet:?" not in script
    assert "source_ref" not in script
