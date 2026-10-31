#!/usr/bin/env python3
"""Block NEW unpartitioned laundry-list views and growing oversized embeds.

Existing legacy views are recorded by the full inventory, not silently
declared remediated. This check compares changed Python files to their PR base
and fails additions that introduce the same usability problems again.
"""
from __future__ import annotations

import argparse
import ast
import subprocess
from pathlib import Path


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], text=True, capture_output=True, check=check,
    )


def _metrics(source: str, *, path: str) -> dict[tuple[str, str], tuple[int, bool]]:
    tree = ast.parse(source, filename=path)
    found: dict[tuple[str, str], tuple[int, bool]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and (
            node.name.endswith("View") or node.name.endswith("Panel")
        ):
            buttons = sum(
                isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                and any(
                    isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Attribute)
                    and d.func.attr == "button"
                    for d in method.decorator_list
                )
                for method in node.body
            )
            if buttons:
                partitioned = any(
                    isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "clear_items"
                    for sub in ast.walk(node)
                )
                found[("view", node.name)] = (buttons, partitioned)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Approximate, conservatively, all add_field calls within one
            # builder; a conditional page may legitimately have more.
            if not any(
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "Embed"
                for sub in ast.walk(node)
            ):
                continue
            fields = sum(
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "add_field"
                for sub in ast.walk(node)
            )
            if fields:
                found[("embed", node.name)] = (fields, False)
    return found


def check_regressions(base: str, root: Path) -> list[str]:
    changed = _git("diff", "--name-only", f"{base}...HEAD", "--", str(root)).stdout.splitlines()
    failures: list[str] = []
    for filename in changed:
        path = Path(filename)
        if path.suffix != ".py" or not path.exists():
            continue
        current = _metrics(path.read_text(encoding="utf-8"), path=filename)
        older = _git("show", f"{base}:{filename}", check=False)
        previous = _metrics(older.stdout, path=filename) if older.returncode == 0 else {}
        for (kind, name), (count, partitioned) in current.items():
            old_count = previous.get((kind, name), (0, False))[0]
            limit = 9 if kind == "view" else 4
            if count <= limit or (kind == "view" and partitioned):
                continue
            if old_count <= limit or count > old_count:
                failures.append(
                    f"{filename}: {kind} {name} has {count} "
                    f"{'buttons' if kind == 'view' else 'fields'} "
                    f"(base: {old_count}); split into focused sections"
                )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--root", type=Path, default=Path("stoney_verify"))
    args = parser.parse_args()
    failures = check_regressions(args.base, args.root)
    for failure in failures:
        print("MOBILE UI REGRESSION:", failure)
    if failures:
        print(f"FAIL: {len(failures)} new or expanded unpartitioned dense panels.")
        return 1
    print("PASS: no newly introduced oversized unpartitioned UI surfaces.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
