#!/usr/bin/env python3
"""Read-only inventory of production Supabase dependencies before PG migration.

Usage: python tools/audit_runtime_datastore_calls.py
Never connects to either database or reads environment secrets.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "stoney_verify"
CALL_NAMES = {"get_supabase", "create_client"}
CRITICAL = ("ticket", "guild_config", "member", "verify", "mod", "toke", "lfg")


def scan_file(path: Path) -> list[dict[str, object]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeError) as exc:
        return [{"file": path.relative_to(ROOT).as_posix(), "error": type(exc).__name__}]
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else ""
        )
        if name not in CALL_NAMES:
            continue
        hits.append({"file": path.relative_to(ROOT).as_posix(),
                     "line": node.lineno, "call": name,
                     "critical_area": any(word in path.stem.lower() for word in CRITICAL)})
    return hits


def main() -> int:
    hits = [hit for path in sorted(SOURCE.rglob("*.py")) for hit in scan_file(path)]
    hits.sort(key=lambda hit: (str(hit["file"]), int(hit.get("line", 0))))
    print(json.dumps({"production_postgres_cutover_verified": False,
                      "supabase_call_sites": len([h for h in hits if "call" in h]),
                      "results": hits}, indent=2))
    return 1 if any("error" in hit for hit in hits) else 0


if __name__ == "__main__":
    raise SystemExit(main())
