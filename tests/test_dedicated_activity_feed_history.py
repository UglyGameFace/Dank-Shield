from pathlib import Path
import ast

ROOT=Path(__file__).resolve().parents[1]
SQL=(ROOT/"supabase_fresh/migrations/202610200025_dankshield_activity_feed_source_trigger.sql")
SMOKE=(ROOT/"supabase_fresh/tests/activity_feed_source_trigger_smoke.sql")
SCRIPT=(ROOT/"tools/stage_dankshield_activity_feed.py")
CI=(ROOT/".github/workflows/dankshield-activity-feed-sql.yml")
TRANSFER=(ROOT/".github/workflows/dankshield-activity-feed-transfer.yml")

def test_source_normalization_only_for_new_rows():
    sql=SQL.read_text(encoding="utf8")
    assert "create or replace function public.sync_activity_feed_meta_columns()" in sql
    assert "new.meta := new.metadata" in sql
    assert "new.metadata := coalesce(new.metadata,new.meta,'{}'::jsonb)" in sql
    assert "new.search_text := coalesce(new.search_text,'')" in sql
    assert "create trigger trg_sync_activity_feed_meta_columns" in sql
    assert "before insert or update on public.activity_feed_events" in sql
    assert "insert into public.activity_feed_events" not in sql

def test_full_original_feed_is_not_silently_normalized():
    script=SCRIPT.read_text(encoding="utf8")
    ast.parse(script)
    for required in (
        "SOURCE_REF = \"hyncpbvqyjojxhjzcdzg\"",
        "DESTINATION_REF = \"viamriafkbqhquvrsnua\"",
        "CONFIRMATION = \"COPY_DANKSHIELD_ACTIVITY_FEED_OFFLINE_ONLY\"",
        "set transaction isolation level repeatable read, read only",
        "set transaction read only",
        "default_transaction_read_only=on",
        "disable trigger trg_sync_activity_feed_meta_columns",
        "enable trigger trg_sync_activity_feed_meta_columns",
        "source_preflight(src)",
        "write_batch(dest,batch)",
        "digest(dest)!=original_digest",
        "coalesce(jsonb_agg(to_jsonb(f) order by f.id),'[]'::jsonb)=%s::jsonb",
        "jsonb_populate_recordset(",
        "missing source" if False else "source/destination collision",
    ):
        assert required in script
    assert "print(payload)" not in script
    assert "delete from" not in script.lower()

def test_rollback_sql_covers_both_modes():
    smoke=SMOKE.read_text(encoding="utf8")
    assert smoke.startswith("-- Isolated")
    assert smoke.endswith("rollback;\n")
    assert "Historical metadata was silently rewritten" in smoke
    assert "disable trigger trg_sync_activity_feed_meta_columns" in smoke
    assert "enable trigger trg_sync_activity_feed_meta_columns" in smoke
    assert "set local role service_role" in smoke

def test_ci_and_owner_workflow_are_isolated():
    w=CI.read_text(encoding="utf8")
    t=TRANSFER.read_text(encoding="utf8")
    assert "postgres:16" in w and "pull_request:" in w
    assert "secrets." not in w and "discloud" not in w.lower()
    assert "workflow_dispatch:" in t
    assert "github.ref == 'refs/heads/main'" in t
    assert "github.actor == 'UglyGameFace'" in t
    assert "secrets.DANK_SOURCE_DATABASE_URL" in t
    assert "secrets.DANK_FRESH_DATABASE_URL" in t
    assert "persist-credentials: false" in t
    assert "schedule:" not in t
