"""Run a read-only capacity check against the existing Discloud PostgreSQL.

Executed explicitly by a private-network operator task, never at bot startup.
No secrets, DSNs, rows or personal data are printed.
"""
from __future__ import annotations

import json
import sys

from stoney_verify.postgres_storage_budget import run_from_environment


def main() -> int:
    try:
        snapshot = run_from_environment()
    except Exception as exc:
        # psycopg exceptions may contain connection details. Only the type is
        # safe to print; the original exception is deliberately suppressed.
        print(json.dumps({
            "state": "unknown",
            "error_type": type(exc).__name__,
            "note": "Read-only PostgreSQL inspection failed; no writes attempted.",
        }))
        return 3
    print(json.dumps(snapshot, separators=(",", ":"), sort_keys=True))
    if snapshot["state"] == "critical":
        return 2
    if snapshot["state"] in ("warning", "budget_unverified"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
