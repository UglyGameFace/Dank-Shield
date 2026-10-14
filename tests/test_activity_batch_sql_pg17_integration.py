"""Exercise the production activity-batch migration on disposable PostgreSQL 17.

Never connects to Supabase or Discloud. The CI runner must opt in with a
localhost-only PostgreSQL service and the dedicated test database.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import subprocess
from urllib.parse import urlparse
from uuid import UUID

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_ACTIVITY_BATCH_SQL_INTEGRATION") != "1",
    reason="requires a disposable localhost PostgreSQL 17 CI service",
)


def test_activity_batch_migration_replay_ack_and_atomic_rejection() -> None:
    import psycopg
    from psycopg.types.json import Jsonb

    dsn = os.environ["DANK_TEST_DATABASE_URL"]
    parsed = urlparse(dsn)
    assert parsed.hostname in ("localhost", "127.0.0.1")
    assert parsed.path == "/postgres"

    with psycopg.connect(dsn, autocommit=True) as conn:
        assert int(conn.execute("show server_version_num").fetchone()[0]) // 10000 == 17
        assert conn.execute(
            "select to_regclass('public.member_activity_ledger')"
        ).fetchone()[0] is None

        # Only synthetic roles and records are created in this disposable CI DB.
        conn.execute("""
            do $test$
            begin
              if not exists (select 1 from pg_roles where rolname = 'anon') then
                create role anon;
              end if;
              if not exists (select 1 from pg_roles where rolname = 'authenticated') then
                create role authenticated;
              end if;
              if not exists (select 1 from pg_roles where rolname = 'service_role') then
                create role service_role;
              end if;
            end;
            $test$;
        """)
        conn.execute("""
            create table public.member_activity_ledger (
                guild_id text not null,
                user_id text not null,
                last_activity_at timestamptz not null,
                last_activity_type text not null,
                last_message_at timestamptz,
                last_reaction_at timestamptz,
                last_interaction_at timestamptz,
                last_ticket_message_at timestamptz,
                last_channel_id text,
                last_process_id text,
                event_count bigint not null default 0,
                first_observed_at timestamptz not null default now(),
                updated_at timestamptz not null default now(),
                primary key (guild_id, user_id)
            )
        """)

    sql_path = (
        Path(__file__).resolve().parents[1]
        / "supabase/migrations/20261013120000_member_activity_batch_rpc.sql"
    )
    subprocess.run(
        ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-f", str(sql_path)],
        check=True,
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=30,
    )

    event = {
        "guild_id": "987654321", "user_id": "876543210",
        "activity_type": "message",
        "occurred_at": "2026-10-14T12:00:00+00:00",
        "first_occurred_at": "2026-10-14T11:59:00+00:00",
        "channel_id": "765432100", "process_id": "test-ci",
        "event_count": 3,
    }
    batch = UUID("00000000-0000-4000-8000-000000000001")
    second = UUID("00000000-0000-4000-8000-000000000002")
    rejected = UUID("00000000-0000-4000-8000-000000000003")

    with psycopg.connect(dsn, autocommit=True) as conn:
        assert conn.execute(
            "select has_function_privilege('service_role', "
            "'public.record_member_activity_batch(uuid,jsonb)', 'EXECUTE')"
        ).fetchone()[0] is True
        assert conn.execute(
            "select has_function_privilege('anon', "
            "'public.record_member_activity_batch(uuid,jsonb)', 'EXECUTE')"
        ).fetchone()[0] is False
        assert conn.execute(
            "select has_function_privilege('authenticated', "
            "'public.ack_member_activity_batches(uuid[])', 'EXECUTE')"
        ).fetchone()[0] is False

        for _ in range(2):
            conn.execute(
                "select public.record_member_activity_batch(%s,%s)",
                (batch, Jsonb([event])),
            )
        assert conn.execute(
            "select event_count from public.member_activity_ledger "
            "where guild_id=%s and user_id=%s",
            (event["guild_id"], event["user_id"]),
        ).fetchone()[0] == 3
        assert conn.execute(
            "select count(*) from public.member_activity_batch_receipts "
            "where batch_id=%s", (batch,),
        ).fetchone()[0] == 1

        second_event = {
            **event, "activity_type": "reaction",
            "occurred_at": "2026-10-14T12:01:00+00:00",
            "event_count": 2,
        }
        conn.execute(
            "select public.record_member_activity_batch(%s,%s)",
            (second, Jsonb([second_event])),
        )
        count, reacted_at = conn.execute(
            "select event_count,last_reaction_at "
            "from public.member_activity_ledger "
            "where guild_id=%s and user_id=%s",
            (event["guild_id"], event["user_id"]),
        ).fetchone()
        assert count == 5
        assert reacted_at == datetime(2026, 10, 14, 12, 1, tzinfo=timezone.utc)

        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute(
                "select public.record_member_activity_batch(%s,%s)",
                (rejected, Jsonb([event, {**event, "activity_type": "presence"}])),
            )
        assert conn.execute(
            "select count(*) from public.member_activity_batch_receipts "
            "where batch_id=%s", (rejected,),
        ).fetchone()[0] == 0
        assert conn.execute(
            "select event_count from public.member_activity_ledger "
            "where guild_id=%s and user_id=%s",
            (event["guild_id"], event["user_id"]),
        ).fetchone()[0] == 5

        conn.execute(
            "select public.ack_member_activity_batches(%s)",
            ([batch, second],),
        )
        assert conn.execute(
            "select count(*) from public.member_activity_batch_receipts"
        ).fetchone()[0] == 0
