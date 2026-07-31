from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "20260731141000_ticket_counter_durability.sql"
ALLOCATOR = ROOT / "stoney_verify" / "tickets_new" / "counter_allocator.py"
BOOTSTRAP = ROOT / "stoney_verify" / "startup_guards" / "auto_schema_bootstrap.py"
WORKFLOW = ROOT / ".github" / "workflows" / "ticket-counter-sql.yml"


def _text(path: Path) -> str:
    assert path.exists(), f"missing required file: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def test_ticket_counter_is_owned_by_a_real_supabase_migration() -> None:
    sql = _text(MIGRATION).lower()

    assert "create table if not exists public.ticket_counters" in sql
    assert "create or replace function public.reserve_ticket_number" in sql
    assert "security definer" in sql
    assert "set search_path = public, pg_temp" in sql
    assert "greatest(" in sql
    assert "max(t.ticket_number)" in sql
    assert "alter table public.ticket_counters enable row level security" in sql
    assert "grant execute on function public.reserve_ticket_number(text) to service_role" in sql
    assert "revoke all on function public.reserve_ticket_number(text) from public, anon, authenticated" in sql


def test_ticket_counter_migration_is_nondestructive_and_idempotent() -> None:
    sql = _text(MIGRATION).lower()

    forbidden = (
        "drop table",
        "drop schema",
        "truncate table",
        "delete from public.tickets",
        "delete from public.ticket_counters",
        "alter table public.tickets drop",
    )
    for marker in forbidden:
        assert marker not in sql, f"destructive SQL found: {marker}"

    assert "if not exists" in sql
    assert "on conflict (guild_id) do update" in sql
    assert "last_ticket_number = greatest(" in sql


def test_runtime_allocator_uses_the_persistent_counter() -> None:
    allocator = _text(ALLOCATOR)

    assert 'COUNTER_TABLE = "ticket_counters"' in allocator
    assert 'sb.rpc("reserve_ticket_number"' in allocator
    assert "Ticket numbering database unavailable; refusing to create a duplicate ticket number." in allocator
    assert "_highest_current_channel_number" in allocator
    assert "_db_max_ticket_number_sync" in allocator


def test_bootstrap_executes_the_complete_counter_migration_chain() -> None:
    bootstrap = _text(BOOTSTRAP)
    schema_sql = bootstrap.split('SCHEMA_SQL = r"""', 1)[1].split('"""', 1)[0]

    assert "reserve_ticket_number" not in schema_sql
    assert "create table if not exists public.ticket_counters" not in schema_sql
    assert "_BOOTSTRAP_MIGRATION_PATTERNS" in bootstrap
    assert '"*ticket_counter*.sql"' in bootstrap
    assert "sorted(migrations_dir.glob(pattern))" in bootstrap
    assert "_required_bootstrap_migrations" in bootstrap
    assert "Required bootstrap migration pattern matched nothing" in bootstrap


def test_postgres_smoke_proves_legacy_218_continues_at_219() -> None:
    workflow = _text(WORKFLOW)

    assert "('legacy-guild', 218)" in workflow
    assert "find supabase/migrations" in workflow
    assert "-name '*ticket_counter*.sql'" in workflow
    assert "for pass in 1 2" in workflow
    assert "Applying complete ticket-counter migration chain" in workflow
    assert "expected first reserved number 219" in workflow
    assert "expected second reserved number 220" in workflow
    assert "expected fresh guild number 1" in workflow
    assert "guild-id normalization failed" in workflow
    assert "migration lowered or changed reserved floor" in workflow
    assert "service_role is missing reserve_ticket_number execute access" in workflow
