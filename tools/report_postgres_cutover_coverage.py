"""Read-only coverage report for the staged Supabase-to-PostgreSQL migration.

This is a planning gate, not a claim that all production data is staged.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

# Support both `python tools/report_postgres_cutover_coverage.py` and
# `python -m tools.report_postgres_cutover_coverage` from the repo root.
import sys
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.audit_supabase_production_callers import ROOT, inventory

# The first-slice allowlist is source code, not a production DB query.
from stoney_verify.core_data_stage import SOURCE_KEYS

PRIORITY_PATH_TERMS = {
    "guild_configuration": ("guild_config", "settings"),
    "tickets": ("ticket",),
    "verification": ("verif", "member"),
    "community": ("community", "lfg"),
    "security": ("security", "antinuke", "invite"),
}


def coverage(root: Path = ROOT) -> dict[str, object]:
    report = inventory(root)
    direct_files = sorted({
        str(hit["file"]) for hit in report["findings"]
        if hit["classification"] == "direct"
    })
    referenced_tables = sorted({str(hit["table_name"]) for hit in report["findings"]\n        if hit["call"] == "table" and hit.get("table_name")})
    unrecognized_tables = sorted(set(referenced_tables) - set(SOURCE_KEYS))
    groups = {
        group: [path for path in direct_files
                if any(term in path.lower() for term in terms)]
        for group, terms in PRIORITY_PATH_TERMS.items()
    }
    grouped = {path for paths in groups.values() for path in paths}
    return {
        "schema": "dankshield-pg-cutover-coverage-v1",
        "staging_table_count": len(SOURCE_KEYS),
        "staging_tables": sorted(SOURCE_KEYS),
        "literal_table_references": referenced_tables,
        "literal_tables_not_in_staging": unrecognized_tables,
        "direct_supabase_files": len(direct_files),
        "direct_supabase_file_groups": groups,
        "other_direct_supabase_files": sorted(set(direct_files) - grouped),
        "production_postgresql_ready": False,
        "cutover_authorized": False,
        "mandatory_gates": [
            "confirm exact Discloud PostgreSQL provisioning, connectivity and storage headroom",
            "reconcile all owned source tables and record-level guild/ticket/member/config parity",
            "implement and test all required production PostgreSQL repositories and routing",
            "prove consistent snapshot or change capture and reconcile concurrent writes",
            "run exact-head CI including /toke and live read/write canary",
            "test rollback without losing writes made after cutover",
        ],
        "warning": (
            "Staging tables are only an initial allowlist. A matching path or "
            "table name does not prove schema coverage, ownership or parity. "
            "No production cutover is authorized by this report."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    result = coverage()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Staged table allowlist: {result['staging_table_count']}")
        print(f"Files with direct Supabase calls: {result['direct_supabase_files']}")
        for group, paths in result["direct_supabase_file_groups"].items():
            print(f"{group}: {len(paths)} files")
        print(f"Other direct callers: {len(result['other_direct_supabase_files'])}")
        print(f"Literal tables outside staging: {len(result['literal_tables_not_in_staging'])}")
        print("CUTOVER BLOCKED: mandatory production gates are not satisfied.")


if __name__ == "__main__":
    main()
