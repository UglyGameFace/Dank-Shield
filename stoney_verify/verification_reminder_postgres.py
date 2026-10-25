from __future__ import annotations

"""Durable, atomic verification reminder claims on Discloud PostgreSQL.

This module is intentionally the sole reminder-ledger implementation. It never
touches Supabase's guild configs, verification roles, or other bot data.
Schema creation is an explicit deployment/migration step, not a bot startup job.
"""

import os
from datetime import datetime, timedelta, timezone

DATABASE_URL_ENV = "DANK_VERIFY_REMINDER_DATABASE_URL"
TABLE = "public.dank_verify_reminder_delivery"

_CLAIM_SQL = """
insert into public.dank_verify_reminder_delivery
    (guild_id, user_id, joined_at, last_claimed_at, attempts)
values (%s, %s, %s, now(), 1)
on conflict (guild_id, user_id) do update
   set joined_at = excluded.joined_at,
       last_claimed_at = now(),
       attempts = case
           when public.dank_verify_reminder_delivery.joined_at
                is distinct from excluded.joined_at
           then 1
           else public.dank_verify_reminder_delivery.attempts + 1
       end
 where public.dank_verify_reminder_delivery.joined_at
       is distinct from excluded.joined_at
    or (
        public.dank_verify_reminder_delivery.attempts < %s
        and public.dank_verify_reminder_delivery.last_claimed_at
            <= now() - make_interval(hours => %s)
    )
returning attempts
"""


def _connect():
    """Never use a generic DATABASE_URL or log private connection credentials."""
    url = os.getenv(DATABASE_URL_ENV, "").strip()
    if not url:
        raise RuntimeError(f"{DATABASE_URL_ENV} is not configured")
    import psycopg

    # Bound connection and query execution. The connection is closed per claim,
    # avoiding idle PostgreSQL sessions for thousands of sparsely active guilds.
    return psycopg.connect(
        url,
        connect_timeout=5,
        options="-c statement_timeout=5000",
    )


def ready_sync() -> bool:
    """Prove reachability, permission and full expected ledger column shape."""
    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select guild_id, user_id, joined_at, last_claimed_at, attempts "
                    "from public.dank_verify_reminder_delivery limit 0"
                )
                cursor.execute(
                    "select "
                    "has_table_privilege(current_user, "
                    "'public.dank_verify_reminder_delivery', 'SELECT') "
                    "and has_table_privilege(current_user, "
                    "'public.dank_verify_reminder_delivery', 'INSERT') "
                    "and has_table_privilege(current_user, "
                    "'public.dank_verify_reminder_delivery', 'UPDATE') "
                    "and has_table_privilege(current_user, "
                    "'public.dank_verify_reminder_delivery', 'DELETE')"
                )
                row = cursor.fetchone()
                return bool(row and row[0] is True)
    except Exception:
        # A missing table, VLAN route, environment secret or DB error must block
        # the Enable button and all worker sends.
        return False


def claim_sync(
    guild_id: int,
    user_id: int,
    joined_at: datetime,
    *,
    repeat_hours: int,
    max_reminders: int,
) -> bool:
    """One transaction prevents duplicate sends from restarts/concurrent bots.

    Claims reserve an attempt *before* posting a message. A failed Discord send
    consumes that attempt rather than risking duplicate pings during a restart.
    """
    if (
        guild_id <= 0 or user_id <= 0
        or not isinstance(joined_at, datetime) or joined_at.tzinfo is None
        or not 12 <= repeat_hours <= 720
        or not 1 <= max_reminders <= 5
    ):
        return False

    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    _CLAIM_SQL,
                    (
                        str(guild_id),
                        str(user_id),
                        joined_at.astimezone(timezone.utc),
                        int(max_reminders),
                        int(repeat_hours),
                    ),
                )
                return cursor.fetchone() is not None
    except Exception:
        # An uncertain transaction result means DO NOT send another reminder.
        return False


def stale_candidates_sync(
    cutoff: datetime,
    *,
    after: tuple[datetime, str, str] | None = None,
    limit: int = 12,
) -> list[tuple[str, str, datetime, datetime]]:
    """Read a bounded keyset page; NEVER interpret a DB error as no rows.

    All rows are just compact counters, not names, message copies or DM content.
    Only the caller may decide a record is safe to delete after Discord REST
    confirms that the user has actually left the guild.
    """
    if cutoff.tzinfo is None or not 1 <= limit <= 50:
        raise ValueError("Invalid verification reminder cleanup scan")
    sql = (
        "select guild_id, user_id, joined_at, last_claimed_at "
        "from public.dank_verify_reminder_delivery "
        "where last_claimed_at < %s "
    )
    params: list[object] = [cutoff]
    if after is not None:
        sql += "and (last_claimed_at, guild_id, user_id) > (%s, %s, %s) "
        params.extend(after)
    sql += "order by last_claimed_at, guild_id, user_id limit %s"
    params.append(limit)
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, tuple(params))
            return list(cursor.fetchall())


def delete_departed_sync(
    guild_id: str,
    user_id: str,
    joined_at: datetime,
    cutoff: datetime,
) -> bool:
    """Compare both membership generation and age to avoid deleting fresh claims.

    The caller MUST already have confirmed REST 404 for this guild member.
    """
    if joined_at.tzinfo is None or cutoff.tzinfo is None:
        return False
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "delete from public.dank_verify_reminder_delivery "
                "where guild_id = %s and user_id = %s and joined_at = %s "
                "and last_claimed_at < %s returning 1",
                (guild_id, user_id, joined_at, cutoff),
            )
            return cursor.fetchone() is not None


# Manual reminders are a separate product action, never an override of the
# scheduled reminder claim history. Both use the SAME private PostgreSQL DSN.
MANUAL_GUILD_TABLE = "public.dank_verify_manual_guild_budget"
MANUAL_MEMBER_TABLE = "public.dank_verify_manual_member_delivery"


class ManualClaimUnavailable(RuntimeError):
    """Safe, user-facing reason a manual batch could not be reserved."""


_MANUAL_GUILD_CLAIM_SQL = """
insert into public.dank_verify_manual_guild_budget
    (guild_id, last_claimed_at, window_started_at, window_claims)
values (%s, now(), now(), 1)
on conflict (guild_id) do update
set last_claimed_at = now(),
    window_started_at = case
        when public.dank_verify_manual_guild_budget.window_started_at <= now() - interval '1 hour'
        then now() else public.dank_verify_manual_guild_budget.window_started_at end,
    window_claims = case
        when public.dank_verify_manual_guild_budget.window_started_at <= now() - interval '1 hour'
        then 1 else public.dank_verify_manual_guild_budget.window_claims + 1 end
where public.dank_verify_manual_guild_budget.last_claimed_at <= now() - interval '60 seconds'
  and (public.dank_verify_manual_guild_budget.window_started_at <= now() - interval '1 hour'
       or public.dank_verify_manual_guild_budget.window_claims < 4)
returning guild_id
"""

_MANUAL_MEMBER_CLAIM_SQL = """
insert into public.dank_verify_manual_member_delivery
    (guild_id, user_id, joined_at, last_claimed_at, attempts)
values (%s, %s, %s, now(), 1)
on conflict (guild_id, user_id) do update
set joined_at = excluded.joined_at,
    last_claimed_at = now(),
    attempts = case
      when public.dank_verify_manual_member_delivery.joined_at is distinct from excluded.joined_at
      then 1 else public.dank_verify_manual_member_delivery.attempts + 1 end
where public.dank_verify_manual_member_delivery.joined_at is distinct from excluded.joined_at
   or (public.dank_verify_manual_member_delivery.attempts < 2
       and public.dank_verify_manual_member_delivery.last_claimed_at <= now() - interval '12 hours')
returning user_id
"""


def manual_ready_sync() -> bool:
    """Fail closed until the separately approved additive manual migration exists."""
    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select guild_id, last_claimed_at, window_started_at, window_claims "
                    "from public.dank_verify_manual_guild_budget limit 0"
                )
                cursor.execute(
                    "select guild_id, user_id, joined_at, last_claimed_at, attempts "
                    "from public.dank_verify_manual_member_delivery limit 0"
                )
                for table in (MANUAL_GUILD_TABLE, MANUAL_MEMBER_TABLE):
                    cursor.execute(
                        "select has_table_privilege(current_user, %s, 'SELECT,INSERT,UPDATE')",
                        (table,),
                    )
                    row = cursor.fetchone()
                    if not (row and row[0] is True):
                        return False
        return True
    except Exception:
        return False


def reserve_manual_batch_sync(
    guild_id: int, members: list[tuple[int, datetime]],
) -> tuple[int, ...]:
    """Atomically reserve one guild send window and up to 3 member attempts.

    Separate durable records preserve scheduled cooldowns/attempt budgets.
    No retry on uncertain DB commit, and no partial committed reservations.
    """
    if not isinstance(guild_id, int) or guild_id <= 0 or not 1 <= len(members) <= 3:
        raise ValueError("Invalid manual reminder batch")
    seen = set()
    for member_id, joined in members:
        if (not isinstance(member_id, int) or member_id <= 0 or member_id in seen
                or not isinstance(joined, datetime) or joined.tzinfo is None):
            raise ValueError("Invalid manual reminder member")
        seen.add(member_id)
    try:
        claimed: list[int] = []
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(_MANUAL_GUILD_CLAIM_SQL, (str(guild_id),))
                if cursor.fetchone() is None:
                    raise ManualClaimUnavailable(
                        "Manual sending is limited to once per minute and four "
                        "batches per hour for this server."
                    )
                for member_id, joined in members:
                    cursor.execute(
                        _MANUAL_MEMBER_CLAIM_SQL,
                        (str(guild_id), str(member_id), joined.astimezone(timezone.utc)),
                    )
                    if cursor.fetchone() is not None:
                        claimed.append(member_id)
        return tuple(claimed)
    except ManualClaimUnavailable:
        raise
    except Exception as exc:
        # psycopg errors can embed connection details: never reveal them.
        raise ManualClaimUnavailable(
            "The private manual reminder database is unavailable; no new sends were authorized."
        ) from None




def available_manual_member_ids_sync(
    guild_id: int, members: list[tuple[int, datetime]],
) -> set[int]:
    """Read-only batch eligibility for one preview page, never an authorization.

    Actual send still requires the same transactionally atomic claim, since
    another shard or moderator may win between preview and confirmation.
    """
    if not members:
        return set()
    if guild_id <= 0 or len(members) > 100:
        raise ValueError("Invalid manual preview batch")
    seen: set[int] = set()
    for uid, joined in members:
        if (uid <= 0 or uid in seen or not isinstance(joined, datetime)
                or joined.tzinfo is None):
            raise ValueError("Invalid manual preview member")
        seen.add(uid)
    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select user_id, joined_at, last_claimed_at, attempts "
                    "from public.dank_verify_manual_member_delivery "
                    "where guild_id = %s and user_id = any(%s)",
                    (str(guild_id), [str(uid) for uid in seen]),
                )
                rows = cursor.fetchall()
        claimed = {int(uid): (joined, last, attempts) for uid, joined, last, attempts in rows}
        now = datetime.now(timezone.utc)
        allowed: set[int] = set()
        for uid, joined in members:
            previous = claimed.get(uid)
            if previous is None or previous[0] != joined.astimezone(timezone.utc):
                allowed.add(uid)
            elif previous[2] < 2 and previous[1] <= now - timedelta(hours=12):
                allowed.add(uid)
        return allowed
    except Exception:
        raise ManualClaimUnavailable(
            "Manual reminder history is unavailable. No recipients were selected."
        ) from None


def manual_stale_candidates_sync(
    cutoff: datetime, *, after: tuple[datetime, str, str] | None = None, limit: int = 12,
) -> list[tuple[str, str, datetime, datetime]]:
    """Read-only, keyset-bounded candidates. Discord must prove departure."""
    if cutoff.tzinfo is None or not 1 <= limit <= 50:
        raise ValueError("Invalid manual cleanup request")
    sql = (
        "select guild_id, user_id, joined_at, last_claimed_at "
        "from public.dank_verify_manual_member_delivery where last_claimed_at < %s "
    )
    params: list[object] = [cutoff]
    if after is not None:
        sql += "and (last_claimed_at, guild_id, user_id) > (%s, %s, %s) "
        params.extend(after)
    sql += "order by last_claimed_at, guild_id, user_id limit %s"
    params.append(limit)
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, tuple(params))
            return list(cursor.fetchall())


def delete_manual_departed_sync(
    guild_id: str, user_id: str, joined_at: datetime, cutoff: datetime,
) -> bool:
    """Only after the worker received an authoritative Unknown Member (10007)."""
    if joined_at.tzinfo is None or cutoff.tzinfo is None:
        raise ValueError("Invalid retention timestamps")
    with _connect() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "delete from public.dank_verify_manual_member_delivery "
                "where guild_id = %s and user_id = %s and joined_at = %s "
                "and last_claimed_at < %s returning 1",
                (guild_id, user_id, joined_at, cutoff),
            )
            return cursor.fetchone() is not None


def apply_manual_schema_sync() -> bool:
    """Explicit operator-only schema application. Never invoked by bot startup."""
    from pathlib import Path

    sql_path = (
        Path(__file__).resolve().parents[1]
        / "postgres/migrations/202610250001_verify_manual_reminders.sql"
    )
    try:
        statement = sql_path.read_text(encoding="utf-8")
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(statement)
        return manual_ready_sync()
    except Exception:
        return False


def _operator_cli() -> int:
    """Installed with the bot even though top-level tools/ is excluded by Discloud."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Private verification reminder PostgreSQL maintenance; default is read-only."
    )
    parser.add_argument(
        "--apply-manual", action="store_true",
        help="Explicitly apply the additive manual reminder tables after backup",
    )
    args = parser.parse_args()
    if not args.apply_manual:
        print("No database changes requested. Pass --apply-manual only after an approved backup.")
        return 0
    if not apply_manual_schema_sync():
        print("Manual schema verification failed; automatic reminders remain independent.")
        return 1
    print("Manual reminder tables installed and verified on the configured private database.")
    return 0


if __name__ == "__main__":
    raise SystemExit(_operator_cli())
