from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

GUARD = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "setup_success_next_step_guard.py"
)

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

SOLID = (
    ROOT
    / "stoney_verify"
    / "commands_ext"
    / "public_setup_solid.py"
).read_text(encoding="utf-8")

RECOMMEND = (
    ROOT
    / "stoney_verify"
    / "commands_ext"
    / "public_setup_recommend.py"
).read_text(encoding="utf-8")

DEFAULTS = (
    ROOT
    / "stoney_verify"
    / "commands_ext"
    / "public_setup_defaults.py"
).read_text(encoding="utf-8")


def test_dead_success_next_step_guard_is_retired() -> None:
    assert not GUARD.exists()

    for source in (
        REGISTRY,
        SELF_CHECK,
        MAIN,
    ):
        assert (
            "setup_success_next_step_guard"
            not in source
        )


def test_solid_setup_has_no_retired_auto_fix_target() -> None:
    tree = ast.parse(SOLID)

    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and node.name == "SolidSetupView"
    ]

    assert len(matches) == 1

    methods = {
        node.name
        for node in matches[0].body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
    }

    assert "auto_fix" not in methods


def test_no_guard_reinjects_solid_auto_fix() -> None:
    guard_dir = (
        ROOT
        / "stoney_verify"
        / "startup_guards"
    )

    for path in guard_dir.glob("*.py"):
        source = path.read_text(
            encoding="utf-8",
            errors="ignore",
        )

        assert "SolidSetupView.auto_fix" not in source
        assert "stoney_solid:auto_fix" not in source


def test_native_completion_and_review_remain() -> None:
    for marker in (
        "def _guided_setup_target",
        "def _open_guided_setup",
        "def _open_health_check",
        "class SetupReviewView",
        "Fix Next Item",
        "Test / Launch",
    ):
        assert marker in RECOMMEND

    assert (
        "def _setup_defaults_callback" in DEFAULTS
        or "async def _setup_defaults_callback"
        in DEFAULTS
    )
