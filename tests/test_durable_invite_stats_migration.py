from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "202608020001_durable_invite_stats.sql"
POLICY = ROOT / "stoney_verify" / "invite_policy_engine.py"
BOOTSTRAP = ROOT / "stoney_verify" / "startup_guards" / "auto_schema_bootstrap.py"


def test_migration_has_atomic_event_ledger_and_service_role_boundary() -> None:
    sql = MIGRATION.read_text(encoding="utf-8").lower()

    assert "create table if not exists public.dank_invite_block_stats" in sql
    assert "create table if not exists public.dank_invite_block_events" in sql
    assert "event_hash text primary key" in sql
    assert "create or replace function public.record_dank_invite_block_event" in sql
    assert "on conflict (event_hash) do nothing" in sql
    assert "invites_blocked = invites_blocked + p_blocked_count" in sql
    assert "return query select false" in sql
    assert "enable row level security" in sql
    assert "grant execute on function public.record_dank_invite_block_event" in sql
    assert "to service_role" in sql
    assert "to anon" not in sql.split("grant execute", 1)[1]


def test_auto_schema_bootstrap_owns_the_committed_migration() -> None:
    source = BOOTSTRAP.read_text(encoding="utf-8")
    assert '"202608020001_durable_invite_stats.sql"' in source


def test_central_invite_policy_uses_durable_service_not_fixed_one_increment() -> None:
    source = POLICY.read_text(encoding="utf-8")
    assert "durable_invite_stats.record_deleted_invite_decision" in source
    assert "invites_blocked=1" not in source
    assert "stats event queued for durable retry" in source
    assert "except Exception:\n            # Statistics" not in source
