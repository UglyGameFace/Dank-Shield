from __future__ import annotations

"""Manual-only future Action Center claim adapter, NEVER called by a worker.

Uses the existing private reminder PostgreSQL DSN. Claimed attempts are
committed before Discord send; failures consume attempts. The existing
verification reminder worker and ledger remain the only verification sender.
The 4C UI never calls this module or activates any sends.
"""
import os
from datetime import date, datetime, timezone

from .verification_reminder_postgres import _connect

MEMBER_SETUP_CONDITION = "member_setup_revision"

class _BudgetFull(RuntimeError):
    pass


def ledger_ready_sync() -> bool:
    try:
        with _connect() as db, db.cursor() as cur:
            cur.execute(
                "select guild_id,user_id,condition,condition_token,last_claimed_at,attempts "
                "from public.dank_action_center_delivery limit 0"
            )
            cur.execute(
                "select guild_id,local_day,claims from public.dank_action_center_daily_budget limit 0"
            )
            return True
    except Exception:
        return False


def reserve_member_setup_sync(
    guild_id: int, user_id: int, revision: int,
    *, local_day: date, cooldown_hours: int,
    max_attempts: int, max_guild_per_day: int,
) -> bool:
    """Future-only fail-closed claim API. Never use for verification.

    A later 4D worker must freshly prove that the member still needs exactly
    this revision before AND after the claim, and must honor quiet hours.
    """
    if (
        type(guild_id) is not int or type(user_id) is not int or
        type(revision) is not int or type(local_day) is not date or
        guild_id <= 0 or user_id <= 0 or revision <= 0 or
        cooldown_hours not in (48, 72, 168) or
        type(max_attempts) is not int or not 1 <= max_attempts <= 3 or
        type(max_guild_per_day) is not int or not 1 <= max_guild_per_day <= 10
    ):
        return False
    try:
        with _connect() as db:
            with db.cursor() as cur:
                # Serialize all daily-budget writes for one guild/day. The
                # lock is transaction scoped and releases on commit/rollback.
                cur.execute(
                    "select pg_advisory_xact_lock(hashtext(%s), hashtext(%s))",
                    (str(guild_id), local_day.isoformat()),
                )
                cur.execute(
                    """
                    insert into public.dank_action_center_delivery
                      (guild_id,user_id,condition,condition_token,last_claimed_at,attempts)
                    values (%s,%s,%s,%s,now(),1)
                    on conflict (guild_id,user_id,condition,condition_token)
                    do update set last_claimed_at=now(),
                                  attempts=public.dank_action_center_delivery.attempts + 1
                    where public.dank_action_center_delivery.attempts < %s
                      and public.dank_action_center_delivery.last_claimed_at
                          <= now() - make_interval(hours => %s)
                    returning attempts
                    """,
                    (str(guild_id), str(user_id), MEMBER_SETUP_CONDITION,
                     str(revision), max_attempts, cooldown_hours),
                )
                if cur.fetchone() is None:
                    # A skipped claim must not consume the daily budget.
                    return False
                cur.execute(
                    """
                    insert into public.dank_action_center_daily_budget(guild_id,local_day,claims)
                    values (%s,%s,1)
                    on conflict (guild_id,local_day)
                    do update set claims=public.dank_action_center_daily_budget.claims+1
                    where public.dank_action_center_daily_budget.claims < %s
                    returning claims
                    """,
                    (str(guild_id), local_day, max_guild_per_day),
                )
                if cur.fetchone() is None:
                    # Both inserts MUST roll back, or the member would lose a
                    # reminder attempt without using the daily guild budget.
                    raise _BudgetFull()
            return True
    except Exception:
        # An uncertain commit, quota outage or missing migration means NO SEND.
        return False
