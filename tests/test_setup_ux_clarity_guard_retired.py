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
    home = _buttons(_class("ProductSetupHomeView"))
    guided = _buttons(_class("ContinueSetupView"))
    more = _buttons(_class("ManageSetupView"))
    settings = _buttons(_class("AdvancedSettingsHubView"))
    core = _buttons(_class("AdvancedCoreSetupView"))
    tickets = _buttons(_class("AdvancedMemberExperienceView"))
    safety = _buttons(_class("AdvancedMonitoringRepairView"))
    appearance = _buttons(_class("AdvancedAppearanceView"))
    recovery = _buttons(_class("AdvancedDangerZoneView"))

    assert home["continue_setup"][:2] == ("Start Setup", "dank_setup_home:continue")
    assert home["more_options"][0] == "More Options"
    assert set(home) == {"continue_setup", "more_options"}

    assert guided["fix_next"][0] == "Set Up This Step"
    assert guided["home"][0] == "Back Home"
    assert set(guided) == {"fix_next", "home"}

    assert more["change_type"][0] == "Change Setup Type"
    assert more["advanced_settings"][0] == "Other Settings"
    assert more["health"][0] == "Check Setup for Problems"
    assert more["recovery"][0] == "Fix Setup or Start Over"
    assert more["help_faq"][0] == "Help"
    assert more["home"][0] == "Back Home"

    assert settings["core"][0] == "Features, Roles & Channels"
    assert settings["tickets"][0] == "Tickets"
    assert settings["safety"][0] == "Logs & Safety"
    assert settings["design"][0] == "Server Design"

    assert core["services"][0] == "Turn Features On / Off"
    assert core["timers_behavior"][0] == "Timers & Rules"
    assert core["detailed_mapping"][0] == "Choose Roles & Channels"
    assert tickets["ticket_choices"][0] == "Ticket Choices"
    assert safety["modlog_tracking"][0] == "Choose What Gets Logged"
    assert safety["protection"][0] == "Spam & Raid Protection"
    assert safety["permission_repair"][0] == "Fix Channel Permissions"
    assert appearance["server_design"][0] == "Server Design"
    assert recovery["recovery"][0] == "Fix or Start Over"

def test_public_setup_type_picker_remains() -> None:
    assert "class SetupTypeChoiceSelect(discord.ui.Select)" in FRESH
    for label in (
        "Tickets + Server Basics",
        "Simple Verify",
        "Help Desk / Tickets",
        "Voice Verify",
        "Choose My Own Features",
    ):
        assert f'"{label}"' in FRESH
