from __future__ import annotations

"""Manual-only, no-write-by-default Action Center owner audit schema install.

Never called by main, startup or any GitHub deployment workflow. This private
PostgreSQL additive DDL is separate from the existing 4C claims migration.
"""

import argparse
from pathlib import Path
from stoney_verify.action_canary_audit import audit_ready_sync
from stoney_verify.verification_reminder_postgres import _connect

SQL = Path(__file__).resolve().parents[1] / "postgres/migrations/202610230002_action_center_canary_audit.sql"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Explicit private PostgreSQL migration authorization")
    args = parser.parse_args()
    if not args.apply:
        print("Dry-run only: no schema changes. Audit migration needs an explicitly approved private-DB operation.")
        return 0
    try:
        with _connect() as db, db.cursor() as cur:
            cur.execute(SQL.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Audit migration failed safely: {type(exc).__name__}; credentials are never logged.")
        return 1
    if not audit_ready_sync():
        print("Audit table/grants not verified; automatic and canary DMs remain unauthorized.")
        return 1
    print("Audit schema verified; this does not activate canary delivery or scheduled reminders.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
