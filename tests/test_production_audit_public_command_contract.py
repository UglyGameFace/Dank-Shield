"""The production audit must inspect Dank Shield's *final* public surface.

The audit command is not a normal public doorway; inspect its pure requirements
and audit function without importing bot globals, starting the Discord runtime,
or contacting a server.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from stoney_verify.command_surface_contract import (
    PUBLIC_DANK_CHILDREN,
    PUBLIC_GLOBAL_COMMAND_NAMES,
)

AUDIT = Path(__file__).resolve().parents[1] / "stoney_verify" / "commands_ext" / "public_production_audit.py"

_NAMES = {
    "_REQUIRED_GLOBAL_COMMANDS",
    "_REQUIRED_CONTEXT_MENU_COMMANDS",
    "_REQUIRED_GROUP_SUBCOMMANDS",
}


def _audit_namespace() -> dict[str, Any]:
    tree = ast.parse(AUDIT.read_text(encoding="utf-8"), filename=str(AUDIT))
    assignments = [
        node for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id in _NAMES
    ]
    methods = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_append_command_audit"
    ]
    assert len(assignments) == 3
    assert len(methods) == 1
    scope: dict[str, Any] = {"PUBLIC_GLOBAL_COMMAND_NAMES": PUBLIC_GLOBAL_COMMAND_NAMES}
    module = ast.Module(body=assignments + methods, type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(AUDIT), "exec"), scope)
    return scope


def _exercise_audit(
    scope: dict[str, Any],
    roots: set[str],
    children: set[str],
    user_menus: set[str],
) -> tuple[list[str], list[str], list[str]]:
    scope["_TREE"] = object()
    scope["_tree_command_counts"] = lambda: (len(roots) + len(user_menus), 0)
    scope["_tree_global_command"] = lambda name: object() if name in roots else None
    scope["_tree_user_context_command"] = lambda name: object() if name in user_menus else None
    scope["_group_has_subcommand"] = (
        lambda group, child: group == "dank" and child in children
    )
    blockers: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []
    scope["_append_command_audit"](blockers, warnings, ok)
    return blockers, warnings, ok


def test_requirements_match_final_public_command_contract() -> None:
    scope = _audit_namespace()
    slash = tuple(n for n in PUBLIC_GLOBAL_COMMAND_NAMES if n != "View Dank Profile")
    assert scope["_REQUIRED_GLOBAL_COMMANDS"] == slash
    assert scope["_REQUIRED_CONTEXT_MENU_COMMANDS"] == ("View Dank Profile",)
    children = ("home", "purge", "setup", "upload")
    assert scope["_REQUIRED_GROUP_SUBCOMMANDS"] == tuple(("dank", c) for c in children)
    assert set(children).issubset(PUBLIC_DANK_CHILDREN)
    assert "stoney" not in slash
    for stale in ("ticket-intake", "ticket-category", "ticket-panel"):
        assert stale not in slash
    text = AUDIT.read_text(encoding="utf-8")
    assert "type=discord.AppCommandType.user" in text
    assert "including \`/ticket-panel\`" not in text


def test_correct_public_tree_does_not_trigger_legacy_false_blocker() -> None:
    scope = _audit_namespace()
    slash = set(scope["_REQUIRED_GLOBAL_COMMANDS"])
    blockers, warnings, ok = _exercise_audit(
        scope, slash, {"home", "purge", "setup", "upload"}, {"View Dank Profile"}
    )
    assert blockers == []
    assert warnings == []
    assert any("public slash command roots" in line for line in ok)
    assert any("user context menu" in line for line in ok)
    assert any("home, purge, setup and upload" in line for line in ok)


def test_real_missing_public_commands_are_still_blockers() -> None:
    scope = _audit_namespace()
    slash = set(scope["_REQUIRED_GLOBAL_COMMANDS"])
    blockers, _, _ = _exercise_audit(
        scope, slash - {"verify"}, {"home", "setup", "upload"}, set()
    )
    messages = "\n".join(blockers)
    assert "Missing required public slash commands: \`/verify\`" in messages
    assert "Missing required public user context menus" in messages
    assert "View Dank Profile" in messages
    assert "Missing required public subcommands: \`/dank purge\`" in messages
    assert "/stoney" not in messages
    assert "/ticket-intake" not in messages
