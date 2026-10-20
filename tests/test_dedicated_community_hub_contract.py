from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
NEW=ROOT/"supabase_fresh/migrations/202610200016_dankshield_community_hub.sql"
SMOKE=ROOT/"supabase_fresh/tests/community_hub_smoke.sql"
SOURCE=ROOT/"supabase/migrations/20260925193000_community_hub.sql"
MATCH=ROOT/"supabase/migrations/20260926044000_community_hub_matchmaking_loop.sql"
HUBLINK=ROOT/"supabase/migrations/20260926054000_community_hub_hublink.sql"
EXTERNAL=ROOT/"supabase/migrations/20261018010000_community_hub_external_the100.sql"
TABLES=("dank_community_hub_settings","dank_community_sessions",
    "dank_community_session_members","dank_community_managed_resources",
    "dank_community_session_events","dank_community_metrics_hourly",
    "dank_community_game_metrics_hourly","dank_community_notification_prefs",
    "dank_community_notification_state","dank_community_availability",
    "dank_community_user_blocks","dank_community_reports",
    "dank_community_events","dank_community_event_attendees",
    "dank_community_partner_links","dank_community_hub_link_codes")


def test_hub_schema_retains_canonical_table_set_and_security() -> None:
    new=NEW.read_text(encoding="utf-8").lower()
    source=SOURCE.read_text(encoding="utf-8").lower()
    hublink=HUBLINK.read_text(encoding="utf-8").lower()
    for table in TABLES:
        assert f"public.{table}" in new
        assert f"alter table public.{table} enable row level security" in new
        assert (table in source or table in hublink)
    assert "security definer" not in new.split("-- from:",1)[1]
    assert "security invoker" in new
    assert "set search_path = pg_catalog, public, pg_temp" in new
    assert "grant execute on function public.community_hub_create_session" in new
    assert "grant execute on function public.community_hub_create_link_code" in new
    assert "to service_role" in new


def test_hub_followup_runtime_features_not_omitted() -> None:
    new=NEW.read_text(encoding="utf-8")
    for path in (SOURCE,MATCH,HUBLINK,EXTERNAL):
        assert str(path.relative_to(ROOT)) in new
    for needle in (
       "community_hub_normalize_formation","community_hub_quick_match",
       "community_hub_create_link_code","community_hub_redeem_link_code",
       "community_hub_expire_link_codes","external_the100_enabled",
       "idx_dank_community_hublink_expiry",
    ):
        assert needle in new
    smoke=SMOKE.read_text(encoding="utf-8")
    assert "set local role service_role" in smoke
    assert "Session creation replay created duplicate" in smoke
    assert "cross-server sessions" in smoke
    assert "rollback;" in smoke


def test_hub_foreign_key_index_fix_is_targeted() -> None:
    fix=(ROOT/"supabase_fresh/migrations/202610200017_dankshield_community_hub_fk_indexes.sql").read_text(encoding="utf-8")
    smoke=(ROOT/"supabase_fresh/tests/community_hub_fk_index_smoke.sql").read_text(encoding="utf-8")
    for target in (
      "dank_community_managed_resources (session_id)",
      "dank_community_reports (session_id)",
      "dank_community_sessions (parent_session_id)",
    ):
      assert target in fix
    assert "begin;" in smoke and "rollback;" in smoke
    assert "Expected three real source FKs" in smoke
