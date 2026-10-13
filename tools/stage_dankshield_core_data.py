"""Explicit, one-shot Supabase -> existing Discloud PG17 staging command.

Example in a private Discloud operator runner (NOT bot startup):
  python -m tools.stage_dankshield_core_data
  python -m tools.stage_dankshield_core_data --apply

Dry-run is the default. An apply requires verified source identity, target
database, disk allowance and an installed staging-only schema.
Never prints credentials or user records, and never switches the live bot.
"""
from __future__ import annotations

import argparse
import json
import os
from urllib.parse import urlparse

from stoney_verify.core_data_stage import (
    DEFAULT_MAX_BYTES, SOURCE_KEYS, apply_stage, plan_source, rest_page_reader
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Stage approved critical Dank Shield rows without cutting over"
    )
    parser.add_argument("--apply", action="store_true",
                        help="Stage rows in private PostgreSQL after explicit safety checks")
    parser.add_argument("--tables", nargs="*", default=None,
                        help="Approved tables only; absent means all first-slice tables")
    parser.add_argument("--max-mib", type=int, default=DEFAULT_MAX_BYTES // 1048576,
                        help="Source payload budget in MiB, up to 32 (default 12)")
    parser.add_argument("--page-size", type=int, default=100)
    args = parser.parse_args()
    if args.max_mib < 1 or args.max_mib > 32:
        parser.error("--max-mib must be between 1 and 32")
    if args.tables is not None and any(t not in SOURCE_KEYS for t in args.tables):
        parser.error("only approved source tables are allowed")

    try:
        from supabase import create_client
        src_url = os.environ.get("SUPABASE_URL", "").strip()
        src_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not src_url or not src_key:
            raise ValueError("source Supabase credentials are unavailable")
        host = (urlparse(src_url).hostname or "").lower()
        if not host.endswith(".supabase.co"):
            raise ValueError("source Supabase URL must use expected service hostname")
        expected_ref = os.environ.get("DANK_MIGRATION_SOURCE_REF", "").strip().lower()
        if args.apply and (not expected_ref or host.split(".")[0] != expected_ref):
            raise ValueError("verified Supabase project ref required before staging")
        reader = rest_page_reader(create_client(src_url, src_key))

        def scan():
            return plan_source(
                reader, selected_tables=args.tables,
                max_bytes=args.max_mib * 1048576, page_size=args.page_size,
            )

        plan = scan()
        print(json.dumps({"mode": "dry_run" if not args.apply else "preflight",
                          **plan.summary}, separators=(",", ":"),
                         sort_keys=True))
        if not args.apply:
            print("No source or destination records were modified.")
            return 0

        dsn = (
            os.environ.get("DANK_CORE_POSTGRES_DATABASE_URL")
            or os.environ.get("DANK_VERIFY_REMINDER_DATABASE_URL")
            or ""
        ).strip()
        expected_db = os.environ.get("DANK_MIGRATION_EXPECTED_DB", "").strip()
        budget_str = os.environ.get("DANK_PG_STORAGE_BUDGET_MB", "").strip()
        if not dsn or not expected_db or not budget_str:
            raise ValueError("verified private destination and disk budget are required")
        disk_budget = int(budget_str)
        import psycopg
        with psycopg.connect(
            dsn, connect_timeout=5, options="-c statement_timeout=15000"
        ) as connection:
            result = apply_stage(
                plan, connection, expected_db=expected_db,
                disk_budget_mb=disk_budget, source_reread=scan,
            )
        print(json.dumps({"mode": "staged", **result},
                         separators=(",", ":"), sort_keys=True))
        return 0
    except Exception as exc:
        # psycopg and REST exceptions may expose connection strings or rows.
        # Preserve a non-secret failure type, never the exception text.
        print(json.dumps({
            "mode": "failed",
            "error_type": type(exc).__name__,
            "note": "No bot database routing was changed. Check private logs.",
        }))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
