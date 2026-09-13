from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase" / "migrations" / "20260913154500_canonical_runtime_schema_authority.sql"
BOOTSTRAP = ROOT / "stoney_verify" / "startup_guards" / "auto_schema_bootstrap.py"
OPERATION_QUEUE_GUARD = ROOT / "stoney_verify" / "startup_guards" / "operation_queue_schema_guard.py"


def _text(path: Path) -> str:
    assert path.exists(), f"missing required file: {path.relative_to(ROOT)}"
    return path.read_text(encoding="utf-8")


def test_ticket_schema_atomicity_guardrails_are_owned_by_migration():
    sql = _text(MIGRATION).lower()

    assert "uq_tickets_guild_ticket_number" in sql
    assert "public.tickets (guild_id, ticket_number)" in sql
    assert "where ticket_number is not null" in sql

    assert "uq_tickets_channel_id" in sql
    assert "public.tickets (channel_id)" in sql
    assert "where nullif(channel_id, '') is not null" in sql

    assert "uq_tickets_discord_thread_id" in sql
    assert "public.tickets (discord_thread_id)" in sql
    assert "where nullif(discord_thread_id, '') is not null" in sql


def test_ticket_schema_migration_preserves_existing_duplicate_history():
    sql = _text(MIGRATION).lower()

    assert "skipping uq_tickets_guild_ticket_number" in sql
    assert "duplicate historical ticket numbers" in sql
    assert "skipping uq_tickets_channel_id" in sql
    assert "duplicate historical channel ids" in sql
    assert "skipping uq_tickets_discord_thread_id" in sql
    assert "duplicate historical thread ids" in sql


def test_startup_schema_guards_cannot_mutate_database_schema():
    forbidden = (
        "psycopg.connect",
        "cur.execute(",
        "create table",
        "alter table",
        "create index",
        "drop constraint",
        "execute(migration.read_text",
    )

    for path in (BOOTSTRAP, OPERATION_QUEUE_GUARD):
        source = _text(path).lower()
        for marker in forbidden:
            assert marker not in source, f"runtime schema mutation returned in {path.name}: {marker}"

        assert 'schema_sql = ""' in source
        assert "migrations remain" in source
