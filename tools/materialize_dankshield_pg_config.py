"""Operator-only migration of staged guild settings into PostgreSQL shadow.

Default invocation is read-only. --apply does NOT change the Discord bot's
canonical Supabase guild config, does not modify the voice bot, and never
triggers schema changes automatically.
"""
from __future__ import annotations

import argparse
import json
import os

from stoney_verify.postgres_storage_budget import migration_database_budget_mb
from stoney_verify.postgres_guild_config_shadow import (
    import_config_shadow, inspect_staged_config,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify or materialize staged guild configs into shadow-only PostgreSQL"
    )
    parser.add_argument("--apply", action="store_true",
                        help="Explicit shadow-only import, never a live DB cutover")
    args = parser.parse_args()
    url = (os.environ.get("DANK_CORE_POSTGRES_DATABASE_URL")
           or os.environ.get("DANK_VERIFY_REMINDER_DATABASE_URL") or "").strip()
    if not url:
        print(json.dumps({"error": "private_postgres_url_missing"}))
        return 2
    try:
        import psycopg
        with psycopg.connect(
            url, connect_timeout=5, options="-c statement_timeout=15000"
        ) as connection:
            if not args.apply:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        cursor.execute("set transaction read only")
                        result = inspect_staged_config(cursor)
                print(json.dumps(result, sort_keys=True))
                return 0
            expected = os.environ.get("DANK_MIGRATION_EXPECTED_DB", "").strip()
            budget = migration_database_budget_mb(os.environ)
            result = import_config_shadow(
                connection, expected_database=expected, disk_budget_mb=budget
            )
            print(json.dumps(result, sort_keys=True))
            return 0
    except Exception as exc:
        # Driver error strings can disclose passwords or record payloads.
        print(json.dumps({"error": "shadow_verification_failed",
                          "kind": type(exc).__name__}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
