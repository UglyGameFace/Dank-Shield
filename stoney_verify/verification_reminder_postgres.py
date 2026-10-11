from __future__ import annotations

"""Durable, atomic verification reminder claims on Discloud PostgreSQL.

This module is intentionally the sole reminder-ledger implementation. It never
touches Supabase's guild configs, verification roles, or other bot data.
Schema creation is an explicit deployment/migration step, not a bot startup job.
"""

import os
from datetime import datetime, timezone

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
                    "'public.dank_verify_reminder_delivery', 'UPDATE')"
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
