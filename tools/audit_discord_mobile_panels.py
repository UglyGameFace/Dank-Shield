#!/usr/bin/env python3
"""Inventory EVERY Python Discord panel, with objective mobile-density signals.

This is intentionally source-wide, not a handpicked list of problem modules.
A warning is evidence for inspection, not permission to hide a control:
nested pages must continue to expose their original callbacks.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path


def _decorated_button(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(d, ast.Call)
        and isinstance(d.func, ast.Attribute)
        and d.func.attr == "button"
        for d in node.decorator_list
    )


def _call_matches(node: ast.AST, attr: str) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == attr


def inventory(root: Path) -> dict:
    files = sorted(root.rglob("*.py"))
    surfaces: list[dict] = []
    unparsable: list[str] = []
    for path in files:
        if "__pycache__" in path.parts:
            continue
        relative = str(path.relative_to(root))
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        except (OSError, SyntaxError, UnicodeError):
            unparsable.append(relative)
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and (node.name.endswith("View") or node.name.endswith("Panel")):
                actions = sum(
                    _decorated_button(item)
                    for item in node.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                )
                selects = sum(
                    isinstance(item, ast.FunctionDef) and "Select" in item.name
                    for item in node.body
                )
                has_repartition = any(
                    _call_matches(sub, "clear_items")
                    for sub in ast.walk(node)
                )
                if actions:
                    surfaces.append({
                        "path": relative, "kind": "view", "name": node.name,
                        "line": node.lineno, "action_buttons": actions,
                        "dynamically_partitioned": has_repartition,
                        "attention": actions >= 10 and not has_repartition,
                    })
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Identify embed factories with potentially long inline field
                # inventories, without assuming that calls in subfunctions
                # represent the same visible screen.
                nested = [sub for sub in ast.walk(node)
                          if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                          and sub is not node]
                if nested:
                    continue
                embeds = sum(
                    _call_matches(sub, "Embed")
                    for sub in ast.walk(node)
                )
                if not embeds:
                    continue
                fields = sum(_call_matches(sub, "add_field") for sub in ast.walk(node))
                if fields:
                    surfaces.append({
                        "path": relative, "kind": "embed", "name": node.name,
                        "line": node.lineno, "fields_in_builder": fields,
                        "attention": fields >= 5,
                    })
    heavy = [row for row in surfaces if row["attention"]]
    return {
        "python_files_scanned": len(files) - len(unparsable),
        "ui_surfaces_identified": len(surfaces),
        "attention_count": len(heavy),
        "source_parse_errors": unparsable,
        "attention": sorted(heavy, key=lambda x: (
            -(x.get("action_buttons", x.get("fields_in_builder", 0))),
            x["path"], x["line"],
        )),
        "surfaces": surfaces,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("stoney_verify"))
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--strict-parse", action="store_true")
    args = parser.parse_args()
    report = inventory(args.root)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        "Mobile panel inventory: "
        f"{report['python_files_scanned']} modules, "
        f"{report['ui_surfaces_identified']} surfaces, "
        f"{report['attention_count']} high-density candidates."
    )
    for row in report["attention"][:60]:
        n = row.get("action_buttons", row.get("fields_in_builder"))
        print(f"  {row['path']}:{row['line']} {row['kind']} {row['name']} ({n})")
    if report["source_parse_errors"]:
        print("Could not parse:", ", ".join(report["source_parse_errors"]))
    return int(args.strict_parse and bool(report["source_parse_errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
