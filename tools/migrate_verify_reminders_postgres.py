from __future__ import annotations

"""Explicit one-shot Discloud PostgreSQL migration for Simple Verify reminders.

Never called on bot startup. Run via an approved private-network deployment
command after setting DANK_VERIFY_REMINDER_DATABASE_URL in the bot environment.
"""

import argparse
from pathlib import Path

from stoney_verify.verification_reminder_postgres import _connect, apply_manual_schema_sync

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "postgres/migrations/202610110001_verify_unverified_reminders.sql"
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

    if args.apply_manual:
        if apply_manual_schema_sync():
            print("Manual reminder ledger applied and verified.")
            return 0
        print("Manual reminder ledger migration failed; scheduled rows were untouched.")
        return 1

    sql = MIGRATION.read_text(encoding="utf-8")
    try:
        with _connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql)
                cursor.execute(
                    "select guild_id, user_id, joined_at, last_claimed_at, attempts "
                    "from public.dank_verify_reminder_delivery limit 0"
                )
    except Exception as exc:
        # Keep credentials and DSN out of logs even when psycopg includes them
        # in its exception details.
        print(f"Reminder database initialization failed ({type(exc).__name__}).")
        return 1

    print("Scheduled reminder schema exists and is readable on the private PostgreSQL connection.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
