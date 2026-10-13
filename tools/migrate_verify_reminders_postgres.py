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


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize Dank Shield's private PostgreSQL reminder ledger")
    parser.add_argument("--apply", action="store_true", help="Apply idempotent ledger SQL on the configured PostgreSQL database")
    args = parser.parse_args()

    if not args.apply:
        print("No database changes made. Pass --apply during an authorized deployment operation.")
        return 0

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

    print("Reminder ledger exists and is readable on the configured PostgreSQL connection.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
