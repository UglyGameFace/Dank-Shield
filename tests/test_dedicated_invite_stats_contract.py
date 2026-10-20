from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
NEW=ROOT/"supabase_fresh/migrations/202610200015_dankshield_invite_stats.sql"
SMOKE=ROOT/"supabase_fresh/tests/invite_stats_smoke.sql"
SOURCE=ROOT/"supabase/migrations/20260802225500_durable_invite_stats.sql"
RUNTIME=ROOT/"stoney_verify/durable_invite_stats.py"


def test_existing_invite_service_contract_preserved() -> None:
    sql=NEW.read_text(encoding="utf-8")
    old=SOURCE.read_text(encoding="utf-8")
    runtime=RUNTIME.read_text(encoding="utf-8")
    for name in ("dank_invite_block_stats","dank_invite_block_events",
                 "record_dank_invite_block_event"):
        assert name in old and name in sql and name in runtime
    for param in ("p_event_hash","p_guild_id","p_blocked_count",
                  "p_seed_count","p_source"):
        assert param in sql and param in runtime
    assert "security invoker" in sql
    assert "on conflict(event_hash) do nothing" in sql
    assert "grant execute on function public.record_dank_invite_block_event" in sql


def test_preserves_totals_without_historical_event_import() -> None:
    sql=NEW.read_text(encoding="utf-8")
    assert "from public.guild_configs" in sql
    assert "max(v.value::bigint)" in sql
    assert "greatest(public.dank_invite_block_stats.invites_blocked" in sql
    assert "from public.dank_invite_block_events" not in sql
    assert "alter table public.dank_invite_block_stats enable row level security" in sql
    assert "alter table public.dank_invite_block_events enable row level security" in sql
    assert "revoke all on public.dank_invite_block_events from public, anon, authenticated" in sql
    smoke=SMOKE.read_text(encoding="utf-8")
    assert "begin;" in smoke and "rollback;" in smoke
    assert "replayed event leaked into another guild" in smoke
