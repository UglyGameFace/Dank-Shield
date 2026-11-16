from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from stoney_verify import verification_reminder_enforcement as enforcement


requires_postgres = pytest.mark.skipif(
    os.getenv("RUN_REMINDER_PG_INTEGRATION") != "1",
    reason="Private PostgreSQL integration suite runs in isolated CI",
)


@requires_postgres
def test_postgres_confirmed_deliveries_atomic_reservation_and_rejoin_isolation():
    guild_id = int(str(uuid4().int)[:18])
    member_id = int(str(uuid4().int)[:18])
    joined = datetime.now(timezone.utc) - timedelta(days=5)
    assert enforcement.enforcement_ready_sync()

    for n in range(3):
        assert enforcement.record_delivery_sync(guild_id, member_id, joined)
    assert enforcement.due_candidates_sync(guild_id, grace_hours=24) == []

    with enforcement._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update public.dank_verify_reminder_enforcement "
                "set last_delivered_at = now() - interval '25 hours' "
                "where guild_id = %s and user_id = %s and joined_at = %s",
                (str(guild_id), str(member_id), joined),
            )
    assert enforcement.due_candidates_sync(guild_id, grace_hours=24) == [(member_id, joined)]

    def attempt(_):
        return enforcement.reserve_action_sync(
            guild_id, member_id, joined, grace_hours=24,
        )
    with ThreadPoolExecutor(max_workers=8) as executor:
        attempts = list(executor.map(attempt, range(8)))
    assert attempts.count(True) == 1 and attempts.count(False) == 7
    assert not enforcement.reserve_action_sync(
        guild_id, member_id, joined, grace_hours=24,
    )
    assert not enforcement.record_delivery_sync(guild_id, member_id, joined)
    assert enforcement.finish_action_sync(guild_id, member_id, joined, "kicked")
    assert enforcement.due_candidates_sync(guild_id, grace_hours=24) == []

    rejoined = joined + timedelta(days=1)
    assert enforcement.record_delivery_sync(guild_id, member_id, rejoined)
    assert enforcement.due_candidates_sync(guild_id, grace_hours=24) == []


@requires_postgres
def test_enforcement_retention_requires_old_delivery_and_exact_join():
    guild_id = int(str(uuid4().int)[:18])
    member_id = int(str(uuid4().int)[:18])
    joined = datetime.now(timezone.utc) - timedelta(days=150)
    assert enforcement.record_delivery_sync(guild_id, member_id, joined)
    with enforcement._connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "update public.dank_verify_reminder_enforcement "
                "set last_delivered_at = now() - interval '95 days' "
                "where guild_id = %s and user_id = %s",
                (str(guild_id), str(member_id)),
            )
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    rows = enforcement.stale_candidates_sync(cutoff, limit=50)
    assert (str(guild_id), str(member_id), joined) in [
        (gid, uid, started) for gid, uid, started, _ in rows
    ]
    assert not enforcement.delete_departed_sync(
        str(guild_id), str(member_id), joined + timedelta(days=1), cutoff,
    )
    assert enforcement.delete_departed_sync(str(guild_id), str(member_id), joined, cutoff)
    assert not enforcement.delete_departed_sync(str(guild_id), str(member_id), joined, cutoff)
