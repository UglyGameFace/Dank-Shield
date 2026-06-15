from __future__ import annotations

from stoney_verify.startup_guards.auto_schema_bootstrap import SCHEMA_SQL


def test_ticket_schema_has_atomicity_guardrails():
    sql = SCHEMA_SQL.lower()

    assert "uq_tickets_guild_ticket_number" in sql
    assert "public.tickets (guild_id, ticket_number)" in sql
    assert "where ticket_number is not null" in sql

    assert "uq_tickets_channel_id" in sql
    assert "public.tickets (channel_id)" in sql
    assert "where nullif(channel_id, '') is not null" in sql

    assert "uq_tickets_discord_thread_id" in sql
    assert "public.tickets (discord_thread_id)" in sql
    assert "where nullif(discord_thread_id, '') is not null" in sql


def test_ticket_schema_preserves_existing_duplicate_history():
    sql = SCHEMA_SQL.lower()

    assert "skipping uq_tickets_guild_ticket_number" in sql
    assert "duplicate historical ticket numbers" in sql
    assert "skipping uq_tickets_channel_id" in sql
    assert "duplicate historical channel ids" in sql
    assert "skipping uq_tickets_discord_thread_id" in sql
    assert "duplicate historical thread ids" in sql
