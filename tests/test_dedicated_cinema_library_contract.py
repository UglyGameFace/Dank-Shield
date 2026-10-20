from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "supabase_fresh/migrations/202610200019_dankshield_cinema_library.sql"
SMOKE = ROOT / "supabase_fresh/tests/cinema_library_smoke.sql"
SOURCES = (
 "supabase/migrations/20261004123000_dank_cinema_library.sql",
 "supabase/migrations/20261006031500_dank_cinema_feed_personalization.sql",
 "supabase/migrations/20261006050000_dank_cinema_library_intelligence.sql",
 "supabase/migrations/20261006051500_dank_cinema_standalone_playback.sql",
)
TABLES = (
 "dank_cinema_users", "dank_cinema_user_media", "dank_cinema_feed_discoveries",
 "dank_cinema_notifications", "dank_cinema_feed_rules",
 "dank_cinema_user_feed_discoveries", "dank_cinema_feed_source_health",
 "dank_cinema_watch_sessions", "dank_cinema_lists", "dank_cinema_list_items",
)


def test_all_canonical_cinema_migrations_in_order() -> None:
    content = SQL.read_text(encoding="utf-8")
    positions = [content.index(path) for path in SOURCES]
    assert positions == sorted(positions)
    for table in TABLES:
        assert f"public.{table}" in content
        assert f"revoke all on public.{table} from public, anon, authenticated, service_role;" in content
        assert f"grant select, insert, update, delete on public.{table} to service_role;" in content
    for path in SOURCES:
        assert (ROOT / path).exists()
    assert "check (session_mode in ('private', 'watch_party', 'standalone'))" in content


def test_cinema_smoke_is_rollback_only_real_role() -> None:
    smoke = SMOKE.read_text(encoding="utf-8")
    assert smoke.startswith("-- All synthetic")
    assert "begin;" in smoke and "rollback;" in smoke
    assert "set local role service_role;" in smoke
    assert "Cinema resume upsert failed" in smoke
    assert "Cross-server Cinema row contamination" in smoke
    assert "Cinema table role grants wrong" in smoke
