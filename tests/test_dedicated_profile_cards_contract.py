from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MIG=ROOT/"supabase_fresh/migrations/202610200018_dankshield_profile_cards.sql"
SMOKE=ROOT/"supabase_fresh/tests/profile_cards_smoke.sql"
BASE=ROOT/"supabase/migrations/20260725_live_profile_cards.sql"
LATER=ROOT/"supabase/migrations/202607270001_live_profile_cards_per_member.sql"
RUNTIME=ROOT/"stoney_verify/profile_card_service.py"


def test_complete_live_profile_contract_in_correct_migration_order() -> None:
    sql=MIG.read_text(encoding="utf-8")
    legacy=BASE.read_text(encoding="utf-8")
    later=LATER.read_text(encoding="utf-8")
    for table in ("dank_profile_users","dank_profile_guild_settings",
                  "dank_live_profile_cards"):
        assert table in legacy and table in sql
        assert f"alter table public.{table} enable row level security" in sql
        assert f"grant select, insert, update, delete on public.{table} to service_role" in sql
        assert f"revoke all on public.{table} from public, anon, authenticated" in sql
    assert sql.index("primary key (guild_id, channel_id)") < sql.index("primary key (guild_id, channel_id, user_id)")
    assert "primary key (guild_id, channel_id, user_id)" in later
    assert 'on_conflict="guild_id,channel_id,user_id"' in RUNTIME.read_text(encoding="utf-8")
    assert "create policy" not in sql.lower()


def test_profile_smoke_uses_real_backend_role_and_rollbacks() -> None:
    smoke=SMOKE.read_text(encoding="utf-8")
    assert "set local role service_role" in smoke
    assert "Server privacy override missing" in smoke
    assert "Multiple member cards in channel lost" in smoke
    assert "Delete one card affected another member" in smoke
    assert "rollback;" in smoke
