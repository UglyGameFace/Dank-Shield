from __future__ import annotations

"""Explicit one-shot Discloud PostgreSQL migration for Simple Verify reminders.

Never called on bot startup. Run via an approved private-network deployment
command after setting DANK_VERIFY_REMINDER_DATABASE_URL in the bot environment.
"""

import argparse
from pathlib import Path

from stoney_verify.verification_reminder_postgres import _connect

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "postgres/migrations/202610110001_verify_unverified_reminders.sql"
)


MANUAL_MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "postgres/migrations/202610250001_verify_manual_reminders.sql"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize Dank Shield's private PostgreSQL reminder ledger")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--apply", action="store_true", help="Apply original automatic reminder schema")
    group.add_argument("--apply-manual", action="store_true", help="Apply additive separate manual reminder schema")
    args = parser.parse_args()

    if not args.apply and not args.apply_manual:
        print("No changes made. Pass --apply or --apply-manual after explicit operator approval.")
        return 0

    sql = (MANUAL_MIGRATION if args.apply_manual else MIGRATION).read_text(encoding="utf-8")
    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql)
                if args.apply_manual:
                    cursor.execute(
                        "select guild_id, last_claimed_at, window_started_at, window_claims "
                        "from public.dank_verify_manual_guild_budget limit 0"
                    )
                    cursor.execute(
                        "select guild_id, user_id, joined_at, last_claimed_at, attempts "
                        "from public.dank_verify_manual_member_delivery limit 0"
                    )
                else:
                    cursor.execute(
                        "select guild_id, user_id, joined_at, last_claimed_at, attempts "
                        "from public.dank_verify_reminder_delivery limit 0"
                    )
    except Exception as exc:
        # Keep credentials and DSN out of logs even when psycopg includes them
        # in its exception details.
        print(f"Reminder database initialization failed ({type(exc).__name__}).")
        return 1

    print(
        ("Manual" if args.apply_manual else "Scheduled")
        + " reminder schema exists and is readable on the configured private PostgreSQL connection."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
