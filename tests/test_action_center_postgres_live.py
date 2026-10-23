from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

import psycopg

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
