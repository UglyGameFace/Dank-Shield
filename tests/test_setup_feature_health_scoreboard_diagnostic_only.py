from __future__ import annotations

import ast
import importlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

TARGET = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "setup_feature_health_scoreboard.py"
)
REGISTRY = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "__init__.py"
)
MAIN = ROOT / "main.py"
SCOREBOARD_COMMAND = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "setup_scoreboard_command.py"
)
IDLE_SCOREBOARD = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "setup_idle_kick_scoreboard_guard.py"
)
PANEL_DOCTOR = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "ticket_panel_doctor_stability_guard.py"
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _tree(path: Path) -> ast.Module:
    return ast.parse(
        _source(path),
        filename=str(path),
    )


def _owners(path: Path) -> dict[str, ast.AST]:
    return {
        node.name: node
        for node in _tree(path).body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.ClassDef,
            ),
        )
    }


def test_diagnostic_module_is_preserved() -> None:
    assert TARGET.exists()

    owners = _owners(TARGET)

    required = {
        "FeatureHealth",
        "_load_config",
        "_load_service_state",
        "_can_manage_role",
        "_can_use_channel",
        "_ticket_score",
        "_ticket_menu_score",
        "_verification_score",
        "_voice_score",
        "_logs_score",
        "_spam_score",
        "_automation_score",
        "_database_score",
        "build_feature_scoreboard",
        "_scoreboard_value",
        "_fixes_value",
        "_actions_value",
        "_next_step",
    }

    assert required.issubset(owners)


def test_startup_wrapper_is_removed() -> None:
    source = _source(TARGET)
    owners = _owners(TARGET)

    assert "_wrap_setup_health" not in owners
    assert "apply" not in owners
    assert "_PATCHED =" not in source
    assert "_feature_scoreboard_wrapped" not in source
    assert (
        'setattr(solid, "_build_health_embed"'
        not in source
    )


def test_no_import_time_apply_call_remains() -> None:
    tree = _tree(TARGET)

    apply_calls = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Name)
            and node.value.func.id == "apply"
        )
    ]

    assert apply_calls == []


def test_public_exports_are_diagnostic_only() -> None:
    tree = _tree(TARGET)

    assignments = [
        node
        for node in tree.body
        if (
            isinstance(node, ast.Assign)
            and any(
                isinstance(target, ast.Name)
                and target.id == "__all__"
                for target in node.targets
            )
        )
    ]

    assert len(assignments) == 1

    exported = ast.literal_eval(
        assignments[0].value
    )

    assert exported == [
        "FeatureHealth",
        "build_feature_scoreboard",
    ]


def test_startup_registration_is_removed() -> None:
    marker = "setup_feature_health_scoreboard"

    assert marker not in _source(REGISTRY)
    assert marker not in _source(MAIN)


def test_scoreboard_command_still_uses_engine() -> None:
    source = _source(SCOREBOARD_COMMAND)

    assert (
        "setup_feature_health_scoreboard as scoreboard"
        in source
    )
    assert (
        "scoreboard.build_feature_scoreboard(guild)"
        in source
    )


def test_idle_kick_extension_dependencies_remain() -> None:
    source = _source(IDLE_SCOREBOARD)
    target_source = _source(TARGET)

    for consumer_marker in (
        "scoreboard.asyncio.gather",
        "scoreboard._load_config",
        "scoreboard._load_service_state",
        'getattr(scoreboard, "FeatureHealth")',
        'getattr(scoreboard, "_cfg_get")',
        'getattr(scoreboard, "_role")',
        'getattr(scoreboard, "_bot_member")',
    ):
        assert consumer_marker in source

    for owner_marker in (
        "import asyncio",
        "async def _load_config(",
        "async def _load_service_state(",
        "class FeatureHealth:",
        "def _cfg_get(",
        "def _role(",
        "def _bot_member(",
    ):
        assert owner_marker in target_source


def test_ticket_panel_doctor_dependency_remains() -> None:
    source = _source(PANEL_DOCTOR)
    target_source = _source(TARGET)

    assert "setup_feature_health_scoreboard" in source
    assert "_ticket_score" in source
    assert "def _ticket_score(" in target_source


def test_import_does_not_mutate_solid_health_builder() -> None:
    from stoney_verify.commands_ext import (
        public_setup_solid as solid,
    )

    module_name = (
        "stoney_verify.startup_guards."
        "setup_feature_health_scoreboard"
    )

    scoreboard = importlib.import_module(
        module_name
    )

    before = solid._build_health_embed

    importlib.reload(scoreboard)

    after = solid._build_health_embed

    assert after is before
    assert callable(
        scoreboard.build_feature_scoreboard
    )
    assert hasattr(scoreboard, "FeatureHealth")
