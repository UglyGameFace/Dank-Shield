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


@requires_postgres
def test_retention_keeps_active_counter_and_deletes_only_matching_stale_generation():
    guild = _new_snowflake()
    user = _new_snowflake()
    joined = datetime.now(timezone.utc) - timedelta(days=150)
    assert ledger.claim_sync(guild, user, joined, repeat_hours=48, max_reminders=1)

    with ledger._connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "update public.dank_verify_reminder_delivery "
                "set last_claimed_at = now() - interval '100 days' "
                "where guild_id = %s and user_id = %s",
                (str(guild), str(user)),
            )
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    rows = ledger.stale_candidates_sync(cutoff, limit=50)
    assert any(row[0:2] == (str(guild), str(user)) for row in rows)

    # A row isn't removed simply because it is old. Correct joined_at
    # generation AND explicit confirmed-departure call are required.
    assert not ledger.delete_departed_sync(
        str(guild), str(user), joined + timedelta(seconds=1), cutoff
    )
    assert not ledger.claim_sync(guild, user, joined, repeat_hours=48, max_reminders=1)
    assert ledger.delete_departed_sync(str(guild), str(user), joined, cutoff)
    assert not ledger.delete_departed_sync(str(guild), str(user), joined, cutoff)


@requires_postgres
def test_retention_never_deletes_fresh_claim_even_if_old_scan_had_selected_it():
    guild = _new_snowflake()
    user = _new_snowflake()
    joined = datetime.now(timezone.utc) - timedelta(days=150)
    assert ledger.claim_sync(guild, user, joined, repeat_hours=48, max_reminders=3)
    with ledger._connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "update public.dank_verify_reminder_delivery "
                "set last_claimed_at = now() - interval '100 days' "
                "where guild_id = %s and user_id = %s",
                (str(guild), str(user)),
            )
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    assert any(row[0:2] == (str(guild), str(user))
               for row in ledger.stale_candidates_sync(cutoff, limit=50))
    assert ledger.claim_sync(guild, user, joined, repeat_hours=48, max_reminders=3)
    assert not ledger.delete_departed_sync(str(guild), str(user), joined, cutoff)


def test_manual_claim_invalid_input_does_not_touch_database(monkeypatch):
    monkeypatch.setattr(ledger, "_connect", lambda: pytest.fail("must not access database"))
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError):
        ledger.reserve_manual_batch_sync(0, [(5, now)])
    with pytest.raises(ValueError):
        ledger.reserve_manual_batch_sync(1, [])
    with pytest.raises(ValueError):
        ledger.reserve_manual_batch_sync(1, [(5, now)] * 2)
    with pytest.raises(ValueError):
        ledger.reserve_manual_batch_sync(1, [(5, now.replace(tzinfo=None))])


@requires_postgres
def test_manual_and_scheduled_ledgers_are_independent_and_restart_safe():
    assert ledger.manual_ready_sync()
    guild, user, other = _new_snowflake(), _new_snowflake(), _new_snowflake()
    joined = datetime.now(timezone.utc) - timedelta(minutes=5)
    assert ledger.available_manual_member_ids_sync(guild, [(user, joined), (other, joined)]) == {user, other}
    assert ledger.reserve_manual_batch_sync(guild, [(user, joined), (other, joined)]) == (user, other)
    assert ledger.available_manual_member_ids_sync(guild, [(user, joined), (other, joined)]) == set()
    # One minute cooldown is persisted by PostgreSQL, not the current worker.
    with pytest.raises(ledger.ManualClaimUnavailable, match="once per minute"):
        ledger.reserve_manual_batch_sync(guild, [(user, joined)])
    with ledger._connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select count(*) from public.dank_verify_reminder_delivery where guild_id=%s",
                (str(guild),),
            )
            assert cursor.fetchone()[0] == 0
            cursor.execute(
                "update public.dank_verify_manual_guild_budget "
                "set last_claimed_at=now()-interval '61 seconds' where guild_id=%s",
                (str(guild),),
            )
    assert ledger.reserve_manual_batch_sync(guild, [(user, joined)]) == ()
    with ledger._connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "update public.dank_verify_manual_guild_budget "
                "set last_claimed_at=now()-interval '61 seconds' where guild_id=%s",
                (str(guild),),
            )
            cursor.execute(
                "update public.dank_verify_manual_member_delivery "
                "set last_claimed_at=now()-interval '7 hours' where guild_id=%s",
                (str(guild),),
            )
    assert ledger.available_manual_member_ids_sync(guild, [(user, joined)]) == {user}
    assert ledger.reserve_manual_batch_sync(guild, [(user, joined)]) == (user,)
    assert ledger.available_manual_member_ids_sync(guild, [(user, joined)]) == set()
    with ledger._connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "update public.dank_verify_manual_guild_budget "
                "set last_claimed_at=now()-interval '61 seconds' where guild_id=%s",
                (str(guild),),
            )
            cursor.execute(
                "update public.dank_verify_manual_member_delivery "
                "set last_claimed_at=now()-interval '7 hours' where guild_id=%s",
                (str(guild),),
            )
    # Third attempt in the same join is authorized after another cooldown,
    # without consuming the independent scheduled reminder history.
    assert ledger.reserve_manual_batch_sync(guild, [(user, joined)]) == (user,)
    with ledger._connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select attempts from public.dank_verify_manual_member_delivery "
                "where guild_id=%s and user_id=%s",
                (str(guild), str(user)),
            )
            assert cursor.fetchone()[0] == 2
            cursor.execute(
                "update public.dank_verify_manual_guild_budget "
                "set last_claimed_at=now()-interval '61 seconds' where guild_id=%s",
                (str(guild),),
            )
    with pytest.raises(ledger.ManualClaimUnavailable, match="four batches"):
        ledger.reserve_manual_batch_sync(guild, [(user, joined + timedelta(days=1))])
    # Independent guild with the same member remains available.
    assert ledger.reserve_manual_batch_sync(_new_snowflake(), [(user, joined)]) == (user,)


@requires_postgres
def test_manual_channel_or_dm_batch_can_claim_24_members_without_lifetime_limit():
    guild = _new_snowflake()
    joined = datetime.now(timezone.utc) - timedelta(hours=1)
    batch = [(_new_snowflake(), joined) for _ in range(24)]
    claimed = ledger.reserve_manual_batch_sync(guild, batch)
    assert claimed == tuple(uid for uid, _ in batch)
    assert ledger.available_manual_member_ids_sync(guild, batch) == set()
    with ledger._connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "update public.dank_verify_manual_guild_budget "
                "set last_claimed_at=now()-interval '61 seconds' where guild_id=%s",
                (str(guild),),
            )
            cursor.execute(
                "update public.dank_verify_manual_member_delivery "
                "set last_claimed_at=now()-interval '7 hours' where guild_id=%s",
                (str(guild),),
            )
    assert ledger.available_manual_member_ids_sync(guild, batch) == {uid for uid, _ in batch}
    assert ledger.reserve_manual_batch_sync(guild, batch) == claimed


@requires_postgres
def test_manual_claim_parallel_shards_only_one_wins():
    guild, user = _new_snowflake(), _new_snowflake()
    joined = datetime.now(timezone.utc)

    def claim(_):
        try:
            return ledger.reserve_manual_batch_sync(guild, [(user, joined)])
        except ledger.ManualClaimUnavailable:
            return ()

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(claim, range(12)))
    assert sum(bool(x) for x in results) == 1
    with ledger._connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select attempts from public.dank_verify_manual_member_delivery "
                "where guild_id=%s and user_id=%s",
                (str(guild), str(user)),
            )
            assert cursor.fetchone()[0] == 1


@requires_postgres
def test_manual_retention_only_verified_departed_generation():
    guild, user = _new_snowflake(), _new_snowflake()
    joined = datetime.now(timezone.utc) - timedelta(days=150)
    assert ledger.reserve_manual_batch_sync(guild, [(user, joined)]) == (user,)
    with ledger._connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "update public.dank_verify_manual_member_delivery "
                "set last_claimed_at=now()-interval '100 days' "
                "where guild_id=%s and user_id=%s",
                (str(guild), str(user)),
            )
    cutoff = datetime.now(timezone.utc) - timedelta(days=90)
    rows = ledger.manual_stale_candidates_sync(cutoff, limit=50)
    assert any(row[:2] == (str(guild), str(user)) for row in rows)
    assert not ledger.delete_manual_departed_sync(
        str(guild), str(user), joined + timedelta(seconds=1), cutoff
    )
    assert ledger.delete_manual_departed_sync(str(guild), str(user), joined, cutoff)
