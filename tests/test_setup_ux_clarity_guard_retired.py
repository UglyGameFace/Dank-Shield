from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

GUARD = (
    ROOT
    / "stoney_verify"
    / "startup_guards"
    / "setup_ux_clarity_guard.py"
)

AUDIT = (
    ROOT
    / "tools"
    / "audit_setup_safety.py"
).read_text(encoding="utf-8")

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

RECOMMEND = (
    ROOT
    / "stoney_verify"
    / "commands_ext"
    / "public_setup_recommend.py"
).read_text(encoding="utf-8")

FRESH = (
    ROOT
    / "stoney_verify"
    / "commands_ext"
    / "public_setup_fresh_choice.py"
).read_text(encoding="utf-8")


def _class(name: str) -> ast.ClassDef:
    tree = ast.parse(RECOMMEND)

    matches = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and node.name == name
    ]

    assert len(matches) == 1
    return matches[0]


def _buttons(
    class_node: ast.ClassDef,
) -> dict[str, tuple[str, str, str]]:
    result: dict[str, tuple[str, str, str]] = {}

    for method in class_node.body:
        if not isinstance(
            method,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        ):
            continue

        for decorator in method.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue

            if not (
                isinstance(
                    decorator.func,
                    ast.Attribute,
                )
                and decorator.func.attr == "button"
            ):
                continue

            values: dict[str, str] = {}

            for keyword in decorator.keywords:
                if keyword.arg not in {
                    "label",
                    "custom_id",
                }:
                    continue

                values[keyword.arg] = str(
                    ast.literal_eval(keyword.value)
                )

            result[method.name] = (
                values.get("label", ""),
                values.get("custom_id", ""),
                (
                    ast.get_source_segment(
                        RECOMMEND,
                        method,
                    )
                    or ""
                ),
            )

    return result


def test_global_setup_ux_guard_is_retired() -> None:
    assert not GUARD.exists()

    for source in (
        REGISTRY,
        SELF_CHECK,
        MAIN,
    ):
        assert "setup_ux_clarity_guard" not in source

    # The CI audit still proves that the retired file is
    # absent, without keeping its old importable module name
    # as a literal stale source reference.
    assert (
        '"setup_" + "ux_clarity_guard.py"'
        in AUDIT
    )
    assert "setup_ux_clarity_guard" not in AUDIT


def test_ci_audit_checks_native_setup_owners() -> None:
    assert (
        "def _assert_native_setup_ux_owners"
        in AUDIT
    )
    assert (
        "_assert_native_setup_ux_owners(failures)"
        in AUDIT
    )
    assert (
        "def _assert_setup_ux_guard_is_scoped"
        not in AUDIT
    )

    for marker in (
        "PRIVATE_MARKERS",
        "_assert_no_private_markers(failures)",
        "_assert_idle_kick_is_per_guild_and_off_by_default(failures)",
    ):
        assert marker in AUDIT


def test_no_global_setup_edit_wrapper_remains() -> None:
    guard_dir = (
        ROOT
        / "stoney_verify"
        / "startup_guards"
    )

    forbidden = (
        "_setup_ux_clarity_wrapped",
        'setattr(discord.InteractionResponse, '
        '"edit_message", wrapped_edit_message)',
    )

    for path in guard_dir.glob("*.py"):
        source = path.read_text(
            encoding="utf-8",
            errors="ignore",
        )

        for marker in forbidden:
            assert marker not in source


def test_native_guided_labels_and_routes_remain() -> None:
    home = _buttons(
        _class("ProductSetupHomeView")
    )
    guided = _buttons(
        _class("ContinueSetupView")
    )
    advanced = _buttons(
        _class("ManageSetupView")
    )

    assert home["continue_setup"][:2] == (
        "Start / Continue Setup",
        "dank_setup_home:continue",
    )
    assert home["health"][0] == "Setup Check"
    assert home["launch"][0] == "Test / Launch"

    assert guided["fix_next"][0] == "Fix Next Item"
    assert guided["review"][0] == "Setup Check"
    assert guided["change_type"][:2] == (
        "Change Setup Type",
        "dank_setup_guided:change_type",
    )
    assert (
        "await _open_choose_setup_type(interaction)"
        in guided["change_type"][2]
    )
    assert guided["advanced"][0] == (
        "Advanced Options"
    )
    assert guided["home"][0] == "Back Home"

    assert advanced["services"][0] == (
        "Features On / Off"
    )
    assert advanced["ticket_choices"][0] == (
        "Ticket Choices"
    )
    assert advanced["timers_behavior"][0] == (
        "Timers & Behavior"
    )
    assert advanced["detailed_mapping"][0] == (
        "Detailed Role / Channel Mapping"
    )
    assert advanced["recovery"][0] == (
        "Recovery / Start Over"
    )


def test_public_setup_type_picker_remains() -> None:
    for label in (
        "Basic Server",
        "Basic Verify",
        "Help Desk",
        "Voice Verify",
        "Custom",
    ):
        assert f'label="{label}"' in FRESH
