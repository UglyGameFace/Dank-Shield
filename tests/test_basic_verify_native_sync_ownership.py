"""Regression boundary for retiring the global Basic Verify CommandTree.sync hook.

The guard still owns a legacy ID-verification allowlist. Verify that its
startup entrypoint keeps that behavior without writing a global Discord sync
hook; canonical command registration and sync stay feature/instance-owned.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from stoney_verify.command_surface_contract import PUBLIC_GLOBAL_COMMAND_NAMES

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "stoney_verify/startup_guards/basic_verification_mode_guard.py"
REGISTRY = ROOT / "stoney_verify/commands_ext/__init__.py"
NATIVE_TREE = ROOT / "stoney_verify/command_runtime.py"
PANEL = ROOT / "stoney_verify/commands_ext/public_verify_basic_panel.py"
ID_ALLOWLIST = ROOT / "stoney_verify/startup_guards/id_verify_allowlist_guard.py"
HOST = ROOT / "sitecustomize.py"


def _guard_apply() -> Any:
    tree = ast.parse(GUARD.read_text(encoding="utf-8"))
    funcs = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "apply"
    ]
    assert len(funcs) == 1
    unit = ast.Module(body=funcs, type_ignores=[])
    scope: dict[str, Any] = {}
    exec(compile(ast.fix_missing_locations(unit), str(GUARD), "exec"), scope)
    return scope["apply"]


def test_basic_verify_guard_has_no_global_sync_mutation() -> None:
    guard = GUARD.read_text(encoding="utf-8")
    assert "def _install_sync_hook" not in guard
    assert "_ORIGINAL_SYNC" not in guard
    assert "_verify_panel_ready" not in guard
    assert "app_commands.CommandTree.sync =" not in guard
    assert "builtins.__import__" not in guard
    assert "def _install_legacy_allowlist()" in guard
    assert "def _install_panel_command()" in guard
    native = NATIVE_TREE.read_text(encoding="utf-8")
    assert "class DankCommandTree(app_commands.CommandTree):" in native
    assert "async def sync(self, *, guild:" in native


def test_guard_startup_preserves_allowlist_panel_and_idempotence() -> None:
    apply = _guard_apply()
    scope = apply.__globals__
    executed = []
    scope["_READY"] = False
    scope["_install_legacy_allowlist"] = lambda: executed.append("allowlist") or True
    scope["_install_panel_command"] = lambda: executed.append("panel") or True
    scope["_log"] = lambda message: executed.append(message)
    assert apply() is True
    assert executed[:2] == ["allowlist", "panel"]
    assert len([x for x in executed if "basic_button_owner=native_runtime" in x]) == 1
    executed.clear()
    assert apply() is True
    assert executed == ["panel"]


def test_guard_fail_closed_when_both_compat_owners_unavailable() -> None:
    apply = _guard_apply()
    scope = apply.__globals__
    executed = []
    scope["_READY"] = False
    scope["_install_legacy_allowlist"] = lambda: executed.append("allowlist") or False
    scope["_install_panel_command"] = lambda: executed.append("panel") or False
    scope["_log"] = lambda message: executed.append(message)
    assert apply() is False
    assert scope["_READY"] is False
    assert executed == ["allowlist", "panel"]


def test_native_basic_verify_and_legacy_ticket_owners_remain() -> None:
    registry = REGISTRY.read_text(encoding="utf-8")
    assert registry.count('("public_verify_basic_panel", "register_public_verify_basic_panel_commands"') == 1
    assert '"public_verify_basic_panel",' in registry
    assert '("public_verify_basic_panel"' not in str(PUBLIC_GLOBAL_COMMAND_NAMES)
    panel = PANEL.read_text(encoding="utf-8")
    assert "verify_group.add_command(command)" in panel
    assert "install_basic_verify_runtime(" in panel
    host = HOST.read_text(encoding="utf-8")
    assert "basic_verification_mode_guard.apply()" in host
    allowlist = ID_ALLOWLIST.read_text(encoding="utf-8")
    assert "def _patch_verify_ui()" in allowlist
    assert "def _patch_verification_ticket_flow()" in allowlist
    assert "def apply()" in allowlist
