from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MIG=ROOT/"supabase_fresh/migrations/202610200021_dankshield_member_events.sql"
SMOKE=ROOT/"supabase_fresh/tests/member_events_smoke.sql"

def test_private_source_member_events_and_feed_trigger():
    q=MIG.read_text(encoding="utf8")
    for s in ("create table public.member_events", "guild_id text not null",
        "user_id text not null", "id uuid primary key default gen_random_uuid()",
        "metadata jsonb not null", "meta jsonb not null",
        "create index member_events_guild_user_created_idx",
        "alter table public.member_events enable row level security",
        "revoke all on public.member_events from public,anon,authenticated,service_role",
        "grant select,insert,update,delete on public.member_events to service_role",
        "create or replace function public.trg_member_events_to_activity_feed()",
        "perform public.activity_feed_log(", "create trigger member_events_to_activity_feed_ai"):
        assert s in q
    assert "insert into public.member_events" not in q
    assert "create policy" not in q.lower()

def test_historical_data_must_not_replay_events():
    q=SMOKE.read_text(encoding="utf8")
    assert q.startswith("-- Candidate only:")
    assert q.endswith("rollback;\n")
    assert "disable trigger member_events_to_activity_feed_ai" in q
    assert "enable trigger member_events_to_activity_feed_ai" in q
    assert "History backfill replayed stale activity" in q
    assert "set local role service_role" in q
