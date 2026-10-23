from __future__ import annotations

"""Explicit, opt-in migration of the dormant Action Center claim ledger.

Default invocation performs NO writes. Never invoke at bot startup; do not
alter the existing verification-reminder ledger.
"""
import argparse
from pathlib import Path
from stoney_verify.action_center_postgres import _connect, ledger_ready_sync

MIGRATION = (Path(__file__).resolve().parents[1] /
             "postgres/migrations/202610230001_action_center_delivery_reservations.sql")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Explicitly install additive private ledger DDL")
    args = parser.parse_args()
    if not args.apply:
        print("Dry run: no database changes. Add --apply only in an approved private PostgreSQL migration operation.")
        return 0
    try:
        with _connect() as db, db.cursor() as cur:
            cur.execute(MIGRATION.read_text(encoding="utf-8"))
        ready = ledger_ready_sync()
    except Exception as exc:
        print(f"Action ledger migration failed safely: {type(exc).__name__}. Credentials are not logged.")
        return 1
    if not ready:
        print("The schema could not be verified. No reminder delivery is authorized.")
        return 1
    print("Action claim tables verified. They remain inactive; no worker or message sending enabled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
