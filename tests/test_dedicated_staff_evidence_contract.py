from pathlib import Path
import ast
import sys
import types

ROOT=Path(__file__).resolve().parents[1]
SQL=ROOT/"supabase_fresh/migrations/202610200024_dankshield_staff_evidence.sql"
SMOKE=ROOT/"supabase_fresh/tests/staff_evidence_smoke.sql"
CI=ROOT/".github/workflows/dankshield-staff-evidence-sql.yml"
MANUAL=ROOT/".github/workflows/dankshield-staff-evidence-transfer.yml"
SCRIPT=ROOT/"tools/stage_dankshield_staff_evidence.py"

def test_private_source_contract_and_no_retention_deletes():
    sql=SQL.read_text(encoding="utf8")
    assert "create table public.member_cleanup_settings (" in sql
    assert "create table public.staff_action_logs (" in sql
    assert "id bigint primary key" in sql
    assert "actor_display text not null" in sql
    assert "metadata jsonb not null default" in sql
    assert "default_queue_limit integer not null default 10" in sql
    assert "allow_low_confidence_queue boolean not null default false" in sql
    assert "require_queue_confirmation boolean not null default true" in sql
    assert "alter table public.staff_action_logs enable row level security" in sql
    assert "grant select,insert,update,delete on public.staff_action_logs to service_role" in sql
    assert "create policy" not in sql.lower()
    assert "delete from" not in sql.lower()
    assert "truncate " not in sql.lower()

def test_test_is_bounded_and_rollback_only():
    smoke=SMOKE.read_text(encoding="utf8")
    assert smoke.startswith("-- Rollback-only")
    assert smoke.endswith("rollback;\n")
    assert "set local role service_role" in smoke
    assert "Member cleanup safe defaults changed" in smoke
    ci=CI.read_text(encoding="utf8")
    assert "postgres:16" in ci
    assert "pull_request:" in ci
    assert "secrets." not in ci
    assert "discloud" not in ci.lower()

def test_live_history_transfer_requires_owner_and_source_read_only():
    py=SCRIPT.read_text(encoding="utf8")
    ast.parse(py)
    workflow=MANUAL.read_text(encoding="utf8")
    assert "workflow_dispatch:" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow
    assert "github.actor == 'UglyGameFace'" in workflow
    assert "secrets.DANK_SOURCE_DATABASE_URL" in workflow
    assert "secrets.DANK_FRESH_DATABASE_URL" in workflow
    assert "schedule:" not in workflow
    assert "persist-credentials: false" in workflow
    for fragment in (
      "SOURCE_REF = \"hyncpbvqyjojxhjzcdzg\"",
      "DESTINATION_REF = \"viamriafkbqhquvrsnua\"",
      "set transaction isolation level repeatable read, read only",
      "set transaction read only",
      "CONFIRMATION = \"COPY_DANKSHIELD_STAFF_EVIDENCE_TO_OFFLINE_ONLY\"",
      "MAX_ROWS = 5000",
      "MAX_BYTES = 40 * 1024 * 1024",
      "jsonb_agg(to_jsonb(x) order by x.id)::text",
      "jsonb_populate_recordset(",
      "verify_destination(dest, payload, count)",
    ):
      assert fragment in py
    assert "print(payload)" not in py
