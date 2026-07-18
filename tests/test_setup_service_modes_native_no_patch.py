from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

MODULE_PATH = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "setup_service_modes.py"
)

SOURCE = MODULE_PATH.read_text(encoding="utf-8")

REGISTRY = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "__init__.py"
).read_text(encoding="utf-8")

SELF_CHECK = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "setup_guided_flow_self_check.py"
).read_text(encoding="utf-8")

MAIN = (
    ROOT
    / "main.py"
).read_text(encoding="utf-8")


def _module_tree() -> ast.Module:
    return ast.parse(
        SOURCE,
        filename=str(MODULE_PATH),
    )


def test_service_modes_is_not_a_startup_guard() -> None:
    for source in (
        REGISTRY,
        SELF_CHECK,
        MAIN,
    ):
        assert "setup_service_modes" not in source


def test_service_modes_patch_owner_is_removed() -> None:
    tree = _module_tree()

    names = {
        node.name
        for node in tree.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
            ),
        )
    }

    assert "_patch_setup_ui" not in names

    for forbidden in (
        "_patch_setup_ui()",
        "_build_main_setup_payload",
        "_build_health_embed",
        "_service_modes_wrapped",
    ):
        assert forbidden not in SOURCE


def test_module_has_no_import_time_installer_call() -> None:
    tree = _module_tree()

    calls = [
        node
        for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id
        == "install_setup_service_modes"
    ]

    assert calls == []


def test_compatibility_installer_remains_safe() -> None:
    tree = _module_tree()

    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name
        == "install_setup_service_modes"
    ]

    assert len(matches) == 1

    source = (
        ast.get_source_segment(
            SOURCE,
            matches[0],
        )
        or ""
    )

    assert "_patch_setup_ui" not in source
    assert "_PATCHED = True" in source
    assert "return True" in source


def test_legitimate_service_exports_remain() -> None:
    tree = _module_tree()

    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "__all__"
            for target in node.targets
        )
    ]

    assert len(assignments) == 1

    exports = set(
        ast.literal_eval(assignments[0].value)
    )

    required = {
        "install_setup_service_modes",
        "load_service_state",
        "build_service_picker_embed",
        "build_spamguard_setup_embed",
        "ServiceModeView",
        "SpamGuardSetupView",
    }

    assert required <= exports


def test_import_does_not_replace_solid_builders() -> None:
    script = r'''
import importlib
import sys

from stoney_verify.commands_ext import public_setup_solid as solid

before_home = solid._build_main_setup_payload
before_health = solid._build_health_embed

sys.modules.pop(
    "stoney_verify.startup_guards.setup_service_modes",
    None,
)

module = importlib.import_module(
    "stoney_verify.startup_guards.setup_service_modes"
)

assert solid._build_main_setup_payload is before_home
assert solid._build_health_embed is before_health

for name in (
    "install_setup_service_modes",
    "load_service_state",
    "build_service_picker_embed",
    "build_spamguard_setup_embed",
    "ServiceModeView",
    "SpamGuardSetupView",
):
    assert hasattr(module, name)

assert module.install_setup_service_modes() is True
assert solid._build_main_setup_payload is before_home
assert solid._build_health_embed is before_health
'''

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, (
        result.stdout + result.stderr
    )
