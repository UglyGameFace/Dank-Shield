"""Run a read-only, explicit guild-config source/PG parity audit.

Usage (private VLAN operator only):
    python -m tools.audit_dankshield_guild_config_parity

Never writes, connects during bot startup, changes backend routing or prints
credentials/config contents. A matching report is NOT a deployment approval.
"""
from __future__ import annotations

import argparse
import json
import os
from urllib.parse import urlparse

from stoney_verify.postgres_guild_config_parity import (
    compare_guild_config_sources,
    supabase_page_reader,
    postgres_page_reader,
)


def _source_ref(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host.endswith(".supabase.co"):
        raise ValueError("source project URL is not a Supabase HTTPS project")
    project_ref = host.split(".", 1)[0]
    if not project_ref or host != f"{project_ref}.supabase.co":
        raise ValueError("source Supabase host is unexpected")
    return project_ref


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare complete current guild config rows, read-only"
    )
    parser.add_argument("--page-size", type=int, default=100)
    args = parser.parse_args()
    try:
        source_url = os.environ.get("SUPABASE_URL", "").strip()
        source_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        expected_ref = os.environ.get("DANK_MIGRATION_SOURCE_REF", "").strip().lower()
        pg_url = (os.environ.get("DANK_CORE_POSTGRES_DATABASE_URL")
                  or os.environ.get("DANK_VERIFY_REMINDER_DATABASE_URL")
                  or "").strip()
        expected_db = os.environ.get("DANK_MIGRATION_EXPECTED_DB", "").strip()
        if not source_url or not source_key or not expected_ref:
            raise ValueError("explicit source identity and credentials required")
        if _source_ref(source_url) != expected_ref:
            raise ValueError("Supabase source project identity mismatch")
        if not pg_url or not expected_db or not expected_db.isidentifier():
            raise ValueError("explicit PG destination identity required")

        from supabase import create_client
        import psycopg

        source_client = create_client(source_url, source_key)
        with psycopg.connect(
            pg_url, autocommit=True, connect_timeout=5,
            options="-c statement_timeout=10000",
        ) as connection:
            with connection.transaction():
                connection.execute(
                    "set transaction isolation level repeatable read, read only"
                )
                current_db = connection.execute(
                    "select current_database()"
                ).fetchone()
                if not current_db or current_db[0] != expected_db:
                    raise RuntimeError("unexpected destination PG database")
                parity = compare_guild_config_sources(
                    supabase_page_reader(source_client),
                    postgres_page_reader(connection),
                    page_size=args.page_size,
                )
        print(json.dumps(parity.public_summary(), sort_keys=True))
        return 0 if parity.matching else 1
    except Exception as exc:
        # Driver/HTTP exceptions can contain DSN, project URL or row values.
        print(json.dumps({
            "status": "unverified",
            "error_type": type(exc).__name__,
            "cutover_authorized": False,
            "warning": "Read-only parity check failed; no data was changed.",
        }))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
