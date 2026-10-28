"""Inventory direct Supabase dependencies before enabling PostgreSQL production routing.

Read-only source analysis; never opens a database connection. Run:
    python tools/audit_supabase_production_callers.py
    python tools/audit_supabase_production_callers.py --json
"""
from __future__ import annotations

import argparse
import ast
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("stoney_verify",)
CALL_NAMES = {"get_supabase", "create_client", "reset_supabase"}
METHOD_NAMES = {"table", "rpc", "from_", "schema"}


def scan_source(path: Path) -> list[dict[str, object]]:
    """Return source locations, without reading credentials or executing code."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[dict[str, object]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else None
        )
        if name not in CALL_NAMES | METHOD_NAMES:
            continue
        # Method names may occur on non-Supabase clients. These are candidates,
        # not confirmed dependencies, and require manual review.
        hits.append({
            "line": node.lineno,
            "call": name,
            "classification": "direct" if name in CALL_NAMES else "candidate",
        })
    return sorted(hits, key=lambda hit: (hit["line"], hit["call"]))


def inventory(root: Path = ROOT) -> dict[str, object]:
    findings: list[dict[str, object]] = []
    for dirname in SOURCE_DIRS:
        directory = root / dirname
        if not directory.is_dir():
            raise FileNotFoundError(f"missing production source directory: {dirname}")
        for path in sorted(directory.rglob("*.py")):
            for hit in scan_source(path):
                findings.append({"file": path.relative_to(root).as_posix(), **hit})
    counts = Counter(str(hit["classification"]) for hit in findings)
    files = sorted({str(hit["file"]) for hit in findings})
    return {
        "schema": "dankshield-supabase-caller-inventory-v1",
        "files_with_calls": len(files),
        "direct_calls": counts["direct"],
        "candidate_calls": counts["candidate"],
        "findings": findings,
        "warning": (
            "Static call-site inventory only. Candidate methods may belong to "
            "other clients; dynamic imports and indirect wrappers require review. "
            "This does not authorize PostgreSQL routing or database cutover."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()
    result = inventory()
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"Files: {result['files_with_calls']}; direct calls: "
              f"{result['direct_calls']}; candidates: {result['candidate_calls']}")
        for item in result["findings"]:
            print(f"{item['file']}:{item['line']} "
                  f"{item['classification']} {item['call']}")
        print(result["warning"])


if __name__ == "__main__":
    main()
