from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
M=ROOT/"supabase_fresh/migrations/202610200023_dankshield_operation_queues.sql"
S=ROOT/"supabase_fresh/tests/operation_queues_smoke.sql"
W=ROOT/".github/workflows/dankshield-operation-queues-sql.yml"

def test_canonical_operation_sql_reused_and_no_historical_queue_copy():
    sql=M.read_text(encoding="utf8")
    index=0
    for path in ("supabase/migrations/20260613_bot_operation_jobs.sql",
                 "supabase/migrations/20260811175500_operation_queue_security_hardening.sql"):
        original=(ROOT/path).read_text(encoding="utf8")
        next_pos=sql.find(original,index)
        assert next_pos>=index
        index=next_pos+len(original)
    for term in ("create table public.bot_commands","id uuid primary key default gen_random_uuid()",
                 "status text not null default 'pending'","bot_commands_guild_status_created_idx",
                 "alter table public.bot_commands enable row level security",
                 "grant select,insert,update,delete on table public.bot_commands"):
        assert term in sql
    assert "insert into public.bot_commands" not in sql
    assert "insert into public.bot_operation_jobs" not in sql

def test_queue_smoke_never_persists_synthetic_rows():
    q=S.read_text(encoding="utf8")
    w=W.read_text(encoding="utf8")
    assert q.startswith("-- All synthetic")
    assert q.endswith("rollback;\n")
    assert "Historical queue replay risk" in q
    assert "unique_violation" in q
    assert "Per-guild command execution isolation broken" in q
    assert "set local role service_role" in q
    assert "postgres:16" in w
    assert "pull_request:" in w
    assert "secrets." not in w
    assert "discloud/deploy-action" not in w
