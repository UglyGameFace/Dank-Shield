from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

GUARD_PATH = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "setup_permission_repair_guard.py"
)

REGISTRY_PATH = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "__init__.py"
)

MAIN_PATH = ROOT / "main.py"

RECOMMEND_PATH = (
    ROOT
    / "stoney_verify"
    / "commands_ext"
    / "public_setup_recommend.py"
)

SERVICE_PATH = (
    ROOT
    / "stoney_verify"
    / "setup_permission_repair_services.py"
)

GUARD = GUARD_PATH.read_text(encoding="utf-8")
REGISTRY = REGISTRY_PATH.read_text(encoding="utf-8")
MAIN = MAIN_PATH.read_text(encoding="utf-8")
RECOMMEND = RECOMMEND_PATH.read_text(encoding="utf-8")
SERVICE = SERVICE_PATH.read_text(encoding="utf-8")


def _guard_tree() -> ast.Module:
    return ast.parse(
        GUARD,
        filename=str(GUARD_PATH),
    )


def test_permission_repair_is_not_a_startup_guard() -> None:
    assert (
        "stoney_verify.startup_guards."
        "setup_permission_repair_guard"
        not in REGISTRY
    )

    assert (
        "setup_permission_repair_guard active"
        not in REGISTRY
    )

    assert "setup_permission_repair_guard" not in MAIN


def test_legacy_home_payload_patch_is_removed() -> None:
    tree = _guard_tree()

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

    assert "_attach_button" not in names
    assert "_wrapped_build_main_setup_payload" not in names

    for forbidden in (
        "_ORIGINAL_BUILD_MAIN",
        "_setup_permission_repair_wrapped",
        (
            "solid._build_main_setup_payload = "
            "_wrapped_build_main_setup_payload"
        ),
        'getattr(solid, "_build_main_setup_payload"',
    ):
        assert forbidden not in GUARD


def test_module_has_no_import_time_apply_call() -> None:
    tree = _guard_tree()

    calls = [
        node
        for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "apply"
    ]

    assert calls == []


def test_compatibility_apply_is_mutation_free() -> None:
    tree = _guard_tree()

    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "apply"
    ]

    assert len(matches) == 1

    source = (
        ast.get_source_segment(
            GUARD,
            matches[0],
        )
        or ""
    )

    assert "_PATCHED = True" in source
    assert "return True" in source
    assert "public_setup_solid" not in source
    assert "_build_main_setup_payload" not in source
    assert "setattr(" not in source


def test_repair_engine_and_views_remain() -> None:
    tree = _guard_tree()

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

    required = {
        "_build_targets",
        "_preview_or_apply",
        "_result_embed",
        "_open_permission_repair",
        "_apply_permission_repair",
        "PermissionRepairButton",
        "PermissionRepairConfirmView",
        "PermissionRepairDoneView",
        "apply",
    }

    assert required <= names


def test_public_compatibility_exports_remain() -> None:
    tree = _guard_tree()

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

    assert {
        "apply",
        "PermissionRepairButton",
        "PermissionRepairConfirmView",
    } <= exports


def test_canonical_setup_route_still_owns_entry() -> None:
    assert "async def _open_permission_repair(" in RECOMMEND

    assert (
        "setup_permission_repair_services."
        "open_permission_repair"
        in RECOMMEND
    )

    assert (
        'custom_id="dank_setup_advanced_monitoring:permission_repair"'
        in RECOMMEND
    )

    assert "async def open_permission_repair(" in SERVICE
    assert "async def apply_permission_repair(" in SERVICE


def test_import_and_apply_do_not_replace_home_builder() -> None:
    script = r'''
import importlib
import sys

from stoney_verify.commands_ext import public_setup_solid as solid

before = solid._build_main_setup_payload

sys.modules.pop(
    "stoney_verify.startup_guards."
    "setup_permission_repair_guard",
    None,
)

module = importlib.import_module(
    "stoney_verify.startup_guards."
    "setup_permission_repair_guard"
)

assert solid._build_main_setup_payload is before
assert module.apply() is True
assert solid._build_main_setup_payload is before

for name in (
    "_build_targets",
    "_preview_or_apply",
    "_result_embed",
    "PermissionRepairConfirmView",
    "PermissionRepairDoneView",
):
    assert hasattr(module, name)
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
