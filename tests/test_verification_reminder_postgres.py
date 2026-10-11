from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from stoney_verify import verification_reminder_postgres as ledger


def _new_snowflake() -> int:
    # Isolate every test row on the same temporary PostgreSQL service.
    return int(str(uuid4().int)[:18])


def test_no_secret_means_closed(monkeypatch):
    monkeypatch.delenv(ledger.DATABASE_URL_ENV, raising=False)
    assert ledger.ready_sync() is False
    assert ledger.claim_sync(1, 2, datetime.now(timezone.utc), repeat_hours=48, max_reminders=3) is False


def test_invalid_claim_does_not_connect(monkeypatch):
    monkeypatch.setattr(ledger, "_connect", lambda: pytest.fail("must not attempt connection"))
    now = datetime.now(timezone.utc)
    assert not ledger.claim_sync(0, 4, now, repeat_hours=48, max_reminders=3)
    assert not ledger.claim_sync(1, 0, now, repeat_hours=48, max_reminders=3)
    assert not ledger.claim_sync(1, 4, now.replace(tzinfo=None), repeat_hours=48, max_reminders=3)
    assert not ledger.claim_sync(1, 4, now, repeat_hours=11, max_reminders=3)
    assert not ledger.claim_sync(1, 4, now, repeat_hours=48, max_reminders=6)


requires_postgres = pytest.mark.skipif(
    os.getenv("RUN_REMINDER_PG_INTEGRATION") != "1",
    reason="PostgreSQL integration tests run in dedicated workflow with a database",
)


@requires_postgres
def test_postgres_claims_are_isolated_capped_and_reset_only_on_rejoin():
    assert ledger.ready_sync()
    guild = _new_snowflake()
    user = _new_snowflake()
    other_guild = _new_snowflake()
    joined = datetime.now(timezone.utc) - timedelta(days=10)
    assert ledger.claim_sync(guild, user, joined, repeat_hours=48, max_reminders=2)
    assert not ledger.claim_sync(guild, user, joined, repeat_hours=48, max_reminders=2)

    # Independent guilds must not inherit another guild's reminder history.
    assert ledger.claim_sync(other_guild, user, joined, repeat_hours=48, max_reminders=2)

    with ledger._connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "update public.dank_verify_reminder_delivery "
                "set last_claimed_at = now() - interval '49 hours' "
                "where guild_id = %s and user_id = %s",
                (str(guild), str(user)),
            )

    assert ledger.claim_sync(guild, user, joined, repeat_hours=48, max_reminders=2)
    assert not ledger.claim_sync(guild, user, joined, repeat_hours=48, max_reminders=2)

    # A new Discord join event resets the attempt budget, never a bot restart.
    assert ledger.claim_sync(guild, user, joined + timedelta(days=1), repeat_hours=48, max_reminders=2)
    assert not ledger.claim_sync(guild, user, joined + timedelta(days=1), repeat_hours=48, max_reminders=2)


@requires_postgres
def test_concurrent_claims_allow_only_one_sender():
    guild = _new_snowflake()
    user = _new_snowflake()
    joined = datetime.now(timezone.utc) - timedelta(days=4)

    def claim(_: int) -> bool:
        return ledger.claim_sync(guild, user, joined, repeat_hours=48, max_reminders=3)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(claim, range(12)))
    assert results.count(True) == 1
    assert results.count(False) == 11

    with ledger._connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select attempts from public.dank_verify_reminder_delivery "
                "where guild_id=%s and user_id=%s",
                (str(guild), str(user)),
            )
            assert cursor.fetchone()[0] == 1
