from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase_fresh/migrations/202610200014_dankshield_staff_metrics.sql"
SQL_SMOKE = ROOT / "supabase_fresh/tests/staff_metrics_smoke.sql"
WORKER = ROOT / "stoney_verify/workers/metrics_sync_worker.py"
WORKSPACE = ROOT / "stoney_verify/tickets_new/workspace_service.py"


def test_worker_upsert_is_backed_by_guild_scoped_unique_key() -> None:
    migration = MIGRATION.read_text(encoding="utf-8")
    worker = WORKER.read_text(encoding="utf-8")
    assert "on_conflict=\"guild_id,staff_id\"" in worker
    assert 'sb.table("staff_metrics").upsert(' in worker
    assert "constraint staff_metrics_guild_staff_unique unique (guild_id, staff_id)" in migration
    for required in ("guild_id text not null", "staff_id text not null", "staff_name text not null"):
        assert required in migration


def test_workspace_staff_report_contract_and_access() -> None:
    migration = MIGRATION.read_text(encoding="utf-8")
    workspace = WORKSPACE.read_text(encoding="utf-8")
    for column in ("id", "guild_id", "staff_id", "staff_name",
                   "tickets_handled", "approvals", "denials",
                   "avg_response_minutes", "last_active"):
        assert f'row.get("{column}")' in workspace
        assert column in migration
    assert "alter table public.staff_metrics enable row level security" in migration
    assert "revoke all on public.staff_metrics from public, anon, authenticated" in migration
    assert "grant select,insert,update,delete on public.staff_metrics to service_role" in migration
    smoke = SQL_SMOKE.read_text(encoding="utf-8")
    assert "begin;" in smoke and "rollback;" in smoke
    assert "990000000000000001" in smoke
