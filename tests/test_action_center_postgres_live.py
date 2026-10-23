from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from urllib.parse import urlsplit
import os

import psycopg
import pytest

# This suite uses real, mutating SQL against a disposable GitHub Actions
# PostgreSQL 17 service. Standard unit CI does NOT provision that service.
# An explicit opt-in prevents missing-DB failures and protects real databases.
pytestmark = pytest.mark.skipif(
    os.getenv("DANK_ACTION_CENTER_TEST_PG") != "1",
    reason="Real PostgreSQL contract tests run only in dedicated Action Center CI",
)


@pytest.fixture(autouse=True)
def require_disposable_postgres_only():
    url = urlsplit(os.getenv("DANK_VERIFY_REMINDER_DATABASE_URL", ""))
    if url.hostname not in ("127.0.0.1", "localhost") or url.path != "/postgres":
        pytest.fail(
            "PostgreSQL integration tests require a disposable local postgres database"
        )


from stoney_verify import action_center_postgres as claims


def test_private_postgres_ready_and_atomic_member_setup_claims(monkeypatch):
    assert claims.ledger_ready_sync()
    today = date(2026, 10, 23)
    args = {
        "local_day": today, "cooldown_hours": 72,
        "max_attempts": 2, "max_guild_per_day": 2,
    }
    # Multiple bot shards can race. Exactly one may consume first attempt.
    with ThreadPoolExecutor(max_workers=2) as executor:
        result = list(executor.map(
            lambda _: claims.reserve_member_setup_sync(12345, 67890, 3, **args),
            range(2),
        ))
    assert result.count(True) == 1
    assert result.count(False) == 1
    with claims._connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select attempts from public.dank_action_center_delivery "
            "where guild_id='12345' and user_id='67890' and condition_token='3'"
        )
        assert cur.fetchone() == (1,)
        cur.execute("select claims from public.dank_action_center_daily_budget where guild_id='12345'")
        assert cur.fetchone() == (1,)
        # Move claim timestamp back to emulate a true old attempt.
        cur.execute(
            "update public.dank_action_center_delivery set last_claimed_at=now()-interval '73 hours' "
            "where guild_id='12345' and user_id='67890'"
        )
    assert claims.reserve_member_setup_sync(12345,67890,3,**args)
    assert not claims.reserve_member_setup_sync(12345,67890,3,**args)
    with claims._connect() as conn, conn.cursor() as cur:
        cur.execute("select claims from public.dank_action_center_daily_budget where guild_id='12345'")
        assert cur.fetchone() == (2,)
        cur.execute("select attempts from public.dank_action_center_delivery where guild_id='12345'")
        assert cur.fetchone() == (2,)


def test_daily_budget_full_rolls_back_member_claim_and_allows_new_date():
    today = date(2026,10,23)
    options = dict(local_day=today,cooldown_hours=72,max_attempts=3,max_guild_per_day=1)
    assert claims.reserve_member_setup_sync(22345,77890,1,**options)
    assert not claims.reserve_member_setup_sync(22345,77891,1,**options)
    with claims._connect() as conn,conn.cursor() as cur:
        cur.execute(
            "select count(*) from public.dank_action_center_delivery where guild_id='22345'"
        )
        assert cur.fetchone() == (1,)
        cur.execute(
            "select claims from public.dank_action_center_daily_budget where guild_id='22345'"
        )
        assert cur.fetchone() == (1,)
    # A different local day receives a fresh guild cap, but the per-member
    # attempt still has its independent cooldown.
    assert claims.reserve_member_setup_sync(
        22345,77891,1,local_day=date(2026,10,24),
        cooldown_hours=72,max_attempts=3,max_guild_per_day=1,
    )


def test_nonexistent_migration_or_lost_connection_means_no_delivery(monkeypatch):
    def offline():
        raise psycopg.OperationalError("unavailable")
    monkeypatch.setattr(claims,"_connect",offline)
    assert not claims.ledger_ready_sync()
    assert not claims.reserve_member_setup_sync(
        123,456,1,local_day=date(2026,10,23),
        cooldown_hours=72,max_attempts=2,max_guild_per_day=1,
    )


def test_read_only_member_revision_and_guild_day_snapshot_matches_claims():
    """4D.2 must see real counters and cooldown without consuming a claim."""
    today = date(2026, 10, 23)
    before = claims.read_member_setup_claim_snapshot_sync(
        33110, 44220, 5, local_day=today
    )
    assert before.attempts == 0
    assert before.last_claimed_at is None
    assert before.guild_claims_today == 0
    assert claims.reserve_member_setup_sync(
        33110, 44220, 5, local_day=today,
        cooldown_hours=72, max_attempts=2, max_guild_per_day=2,
    )
    after = claims.read_member_setup_claim_snapshot_sync(
        33110, 44220, 5, local_day=today
    )
    assert after.attempts == 1
    assert after.last_claimed_at is not None
    assert after.last_claimed_at.tzinfo is not None
    assert after.guild_claims_today == 1
    # Observing the snapshot again cannot consume an additional attempt.
    repeated = claims.read_member_setup_claim_snapshot_sync(
        33110, 44220, 5, local_day=today
    )
    assert repeated == after
    tomorrow = claims.read_member_setup_claim_snapshot_sync(
        33110, 44220, 5, local_day=date(2026, 10, 24),
    )
    assert tomorrow.attempts == 1
    assert tomorrow.guild_claims_today == 0
    other_revision = claims.read_member_setup_claim_snapshot_sync(
        33110, 44220, 6, local_day=today,
    )
    assert other_revision.attempts == 0
    assert other_revision.guild_claims_today == 1


def test_operator_canary_audit_requires_durable_request_before_final_status():
    from uuid import uuid4
    from stoney_verify import action_canary_audit as audit

    assert audit.audit_ready_sync()
    operation = uuid4()
    assert audit.begin_canary_audit_sync(operation, 99877, 88776, 77665, 12)
    assert not audit.begin_canary_audit_sync(operation, 99877, 88776, 77665, 12)
    with claims._connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select status,claimed,sent,finished_at from public.dank_action_center_canary_audit "
            "where operation_id=%s", (operation,),
        )
        assert cur.fetchone() == ("requested", False, False, None)
    assert audit.finish_canary_audit_sync(
        operation, status="cancelled", claimed=True, sent=False
    )
    assert not audit.finish_canary_audit_sync(
        operation, status="sent", claimed=True, sent=True
    )
    with claims._connect() as conn, conn.cursor() as cur:
        cur.execute(
            "select status,claimed,sent,finished_at from public.dank_action_center_canary_audit "
            "where operation_id=%s", (operation,),
        )
        status, claimed, sent, finished_at = cur.fetchone()
        assert (status, claimed, sent) == ("cancelled", True, False)
        assert finished_at is not None
