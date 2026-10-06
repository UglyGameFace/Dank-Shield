from __future__ import annotations

from pathlib import Path

from stoney_verify import cinema_site
from stoney_verify.cinema_feed_personalization import (
    _rule_matches,
    group_feed_results,
    source_trust_payload,
)


def test_group_feed_results_collapses_sources_and_surfaces_quality_upgrade() -> None:
    rows = [
        {
            "title": "Example Show",
            "media_type": "tv",
            "tmdb_id": 777,
            "source_id": "rss-a",
            "source_label": "RSS A",
            "category": "tv",
            "release_title": "Example.Show.S03E09.1080p.WEB.x265-GROUPA.mkv",
            "seeds": 42,
            "peers": 50,
            "file_size": 1_800_000_000,
            "playable": True,
            "first_seen_at": "2026-10-05T20:00:00+00:00",
            "genres": ["Drama"],
            "people": ["Example Actor"],
            "studios": ["Example Studios"],
        },
        {
            "title": "Example Show",
            "media_type": "tv",
            "tmdb_id": 777,
            "source_id": "rss-b",
            "source_label": "RSS B",
            "category": "tv",
            "release_title": "Example.Show.S03E09.2160p.WEB.AV1-GROUPB.mkv",
            "seeds": 19,
            "peers": 24,
            "file_size": 4_400_000_000,
            "playable": True,
            "first_seen_at": "2026-10-05T21:00:00+00:00",
            "genres": ["Drama"],
            "people": ["Example Actor"],
            "studios": ["Example Studios"],
        },
    ]

    grouped = group_feed_results(rows)

    assert len(grouped) == 1
    result = grouped[0]
    assert result["release_count"] == 2
    assert result["source_count"] == 2
    assert result["new_episode"] is True
    assert result["season_number"] == 3
    assert result["episode_number"] == 9
    assert result["upgrade_available"] is True
    assert result["best_quality"] == "2160p"
    assert result["best_release"]["source_id"] == "rss-b"


def test_feed_rule_matches_canonical_people_genre_and_studio_metadata() -> None:
    grouped = group_feed_results(
        [
            {
                "title": "Example Movie",
                "media_type": "movie",
                "tmdb_id": 123,
                "source_id": "feed-1",
                "release_title": "Example.Movie.2026.1080p.WEB.x265-GROUP.mkv",
                "playable": True,
                "genres": ["Crime", "Drama"],
                "people": ["Bryan Cranston"],
                "studios": ["A24"],
                "franchises": ["Example Universe"],
            }
        ]
    )
    assert len(grouped) == 1
    result = grouped[0]

    for rule_type, query in (
        ("person", "Bryan Cranston"),
        ("genre", "Crime"),
        ("studio", "A24"),
        ("franchise", "Example Universe"),
    ):
        assert _rule_matches(
            result,
            {
                "enabled": True,
                "rule_type": rule_type,
                "query": query,
                "media_type": None,
                "tmdb_id": None,
                "filters": {"playable_only": True},
            },
        )


def test_advanced_feed_filters_require_hdr_subtitles_and_size_bounds() -> None:
    result = group_feed_results(
        [
            {
                "title": "Example Movie",
                "media_type": "movie",
                "tmdb_id": 123,
                "source_id": "feed-1",
                "release_title": "Example.Movie.2026.2160p.HDR.WEB.x265.MULTI-SUB-GROUP.mkv",
                "subtitle_languages": ["English", "Spanish"],
                "file_size": 6 * 1024 * 1024 * 1024,
                "seeds": 33,
                "playable": True,
            }
        ]
    )[0]

    matching = {
        "enabled": True,
        "rule_type": "filter",
        "query": "",
        "media_type": None,
        "tmdb_id": None,
        "filters": {
            "playable_only": True,
            "hdr_only": True,
            "subtitles_only": True,
            "min_seeds": 10,
            "min_size_bytes": 4 * 1024 * 1024 * 1024,
            "max_size_bytes": 8 * 1024 * 1024 * 1024,
            "resolutions": ["2160p"],
            "codecs": ["x265"],
            "languages": [],
            "excluded_terms": [],
            "source_ids": [],
            "categories": [],
        },
    }
    assert _rule_matches(result, matching)

    too_large = {
        **matching,
        "filters": {
            **matching["filters"],
            "max_size_bytes": 5 * 1024 * 1024 * 1024,
        },
    }
    assert not _rule_matches(result, too_large)


def test_source_trust_is_derived_from_real_refresh_history() -> None:
    trust = source_trust_payload(
        {
            "refresh_count": 10,
            "success_count": 9,
            "failure_count": 1,
            "total_results": 68,
            "last_result_count": 8,
            "last_error": "",
            "last_refreshed_at": "2026-10-05T21:00:00+00:00",
            "last_success_at": "2026-10-05T21:00:00+00:00",
        }
    )

    assert trust["trust_score"] is not None
    assert 80 <= trust["trust_score"] <= 100
    assert trust["success_rate"] == 0.9
    assert trust["refresh_count"] == 10
    assert trust["total_results"] == 68


def test_feed_center_client_exposes_personalization_without_source_refs() -> None:
    script = (
        Path(cinema_site.__file__).resolve().parent / "assets" / "cinema_site.js"
    ).read_text(encoding="utf-8")

    for expected in (
        '"My Feed"',
        '"Collections"',
        '"Feed Rules"',
        '"Private RSS / JSON source"',
        '"Actor / creator"',
        '"Genre"',
        '"Studio"',
        '"Franchise"',
        '"Require HDR"',
        '"Require subtitles"',
        '"Quality upgrade"',
        '"Open My Feed"',
        '"+ New Feed Rule"',
    ):
        assert expected in script

    assert 'api("/feed-rules"' in script
    assert 'api("/feed-queue"' in script
    assert '"Add to Queue"' in script
    assert '"Actual items discovered from your enabled RSS and structured sources."' in script
    assert "magnet:?" not in script


def test_feed_personalization_schema_is_service_role_only() -> None:
    migration = (
        Path(cinema_site.__file__).resolve().parents[1]
        / "supabase"
        / "migrations"
        / "20261006031500_dank_cinema_feed_personalization.sql"
    ).read_text(encoding="utf-8")

    for table in (
        "dank_cinema_feed_rules",
        "dank_cinema_user_feed_discoveries",
        "dank_cinema_feed_source_health",
    ):
        assert f"create table if not exists public.{table}" in migration
        assert f"alter table public.{table} enable row level security" in migration
        assert f"revoke all on table public.{table} from anon, authenticated" in migration
        assert f"grant all on table public.{table} to service_role" in migration

    assert "'franchise'" in migration


def test_feed_auto_refresh_worker_is_bounded_and_started_with_media_server() -> None:
    root = Path(cinema_site.__file__).resolve().parents[1]
    service = (
        Path(cinema_site.__file__).resolve().parent / "cinema_feed_service.py"
    ).read_text(encoding="utf-8")
    server = (
        Path(cinema_site.__file__).resolve().parent / "torrent_media_server.py"
    ).read_text(encoding="utf-8")
    env = (root / ".env.example").read_text(encoding="utf-8")

    assert 'DANK_CINEMA_FEED_REFRESH_SECONDS", "900"' in service
    assert 'DANK_CINEMA_FEED_REFRESH_BATCH", "24"' in service
    assert "max(300, min(raw, 86400))" in service
    assert "await asyncio.sleep(0.75)" in service
    assert "start_cinema_feed_refresh_worker()" in server
    assert "DANK_CINEMA_FEED_REFRESH_SECONDS=900" in env
    assert "DANK_CINEMA_FEED_REFRESH_BATCH=24" in env
