from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MIG=ROOT/"supabase_fresh/migrations/202610200020_dankshield_warns.sql"
SMOKE=ROOT/"supabase_fresh/tests/warns_smoke.sql"
WORKFLOW=ROOT/".github/workflows/dankshield-fresh-warns-sql.yml"


def test_original_source_columns_and_restricted_access():
    sql=MIG.read_text(encoding="utf8")
    assert "39485389338" in sql
    for field in ("id uuid primary key default gen_random_uuid()",
                  "guild_id text not null", "user_id text not null",
                  "username text", "reason text not null",
                  "source_message text", "created_at timestamptz default now()"):
        assert field in sql
    assert "create index warns_guild_user_created_idx" in sql
    assert "on public.warns (guild_id, user_id, created_at desc)" in sql
    assert "alter table public.warns enable row level security" in sql
    assert "revoke all on public.warns from public, anon, authenticated, service_role" in sql
    assert "grant select, insert, update, delete on public.warns to service_role" in sql
    assert "insert into public.warns" not in sql
    assert "create policy" not in sql.lower()


def test_smoke_and_runtime_guards():
    from stoney_verify import modlog
    s=SMOKE.read_text(encoding="utf8")
    w=WORKFLOW.read_text(encoding="utf8")
    assert 'sb.table("warns")' in Path(modlog.__file__).read_text(encoding="utf8")
    assert s.startswith("-- Synthetic") and s.endswith("rollback;\n")
    assert "set local role service_role" in s
    assert "Wrong isolated modlog warning counts" in s
    assert "postgres:16" in w
    assert "pull_request:" in w
    assert "discloud/deploy-action" not in w
    assert "secrets." not in w
