"""Keep redundant Python user-site startup mutation out of Dank Shield boot.

The basic panel exists through the canonical command registry. The remaining
sitecustomize verification compatibility owner is deliberately NOT changed here.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "stoney_verify/commands_ext/__init__.py"
PANEL = ROOT / "stoney_verify/commands_ext/public_verify_basic_panel.py"
HOST = ROOT / "sitecustomize.py"


def _static_registry_values(name: str):
    tree = ast.parse(REGISTRY.read_text(encoding="utf-8"), filename=str(REGISTRY))
    assignments = [
        node for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == name
    ]
    assert len(assignments) == 1
    return ast.literal_eval(assignments[0].value)


def test_user_site_startup_duplicate_is_absent() -> None:
    assert not (ROOT / "usercustomize.py").exists()


def test_basic_verify_registration_is_native_and_not_duplicated() -> None:
    specs = _static_registry_values("COMMAND_MODULES")
    core = _static_registry_values("_PUBLIC_CORE_MODULES")
    expected = (
        "public_verify_basic_panel",
        "register_public_verify_basic_panel_commands",
        "core: /verify panel basic button verification command",
    )
    assert specs.count(expected) == 1
    names = [spec[0] for spec in specs]
    assert len(names) == len(set(names)), "Canonical command modules are duplicated"
    assert core.count("public_verify_basic_panel") == 1
    assert core.index("public_verify_basic_panel") < core.index("public_verify_group")
    assert "public_verify_group" in core


def test_basic_verify_existing_public_profiles_and_runtime_remain() -> None:
    source = REGISTRY.read_text(encoding="utf-8")
    assert '"public": _PUBLIC_CORE_MODULES' in source
    assert '"minimal": tuple(x for x in _PUBLIC_CORE_MODULES' in source
    assert '"public-admin": _PUBLIC_CORE_MODULES + _PUBLIC_ADMIN_EXTRA_MODULES' in source
    assert '"dev": _LEGACY_MODULES + _PUBLIC_CORE_MODULES + _PUBLIC_ADMIN_EXTRA_MODULES' in source
    panel = PANEL.read_text(encoding="utf-8")
    assert "def register_public_verify_basic_panel_commands(" in panel
    assert "_install_basic_verify_runtime(bot)" in panel
    assert "def _attach() -> bool:" in panel
    assert "verify_group.add_command(command)" in panel
    assert "def apply() -> bool:" in panel
    assert "apply()" in panel


def test_remains_one_verified_host_compat_owner_until_followup() -> None:
    host = HOST.read_text(encoding="utf-8")
    assert "import stoney_verify.commands_ext.public_verify_basic_panel" in host
    assert "basic_verification_mode_guard.apply()" in host
    assert "_force_verify_panel_command_module()" in host
    assert "usercustomize" not in (ROOT / "main.py").read_text(encoding="utf-8")
