from __future__ import annotations

"""Safety audit for the public /dank setup surface.

This catches regressions that are easy to miss in Discord UI testing:
- broad setup UX monkey-patches leaking into unrelated screens
- optional verification idle kick not documented as off-by-default/per-server
- private server IDs/names in setup-facing code
"""

from pathlib import Path
import ast
import sys

ROOT = Path(__file__).resolve().parents[1]

PRIVATE_MARKERS = (
    "1098088221457514609",
    "1232631147649830992",
    "1317042307903651901",
    "1357215261001912320",
    "1514374173517152418",
    "Stoney Balonney",
    "The 420 Lobby",
    "DickHeads",
)

SETUP_FILES = [
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_solid.py",
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_group.py",
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_start.py",
    ROOT / "stoney_verify" / "startup_guards" / "setup_feature_health_scoreboard.py",
    ROOT / "stoney_verify" / "startup_guards" / "setup_scoreboard_command.py",
    ROOT / "stoney_verify" / "startup_guards" / "setup_verification_idle_kick_controls.py",
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_recommend.py",
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_fresh_choice.py",
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_full_customization.py",
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_recovery.py",
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_cleanup.py",
    ROOT / "stoney_verify" / "config_history_ui.py",
    ROOT / "stoney_verify" / "setup_service_state.py",
    ROOT / "stoney_verify" / "startup_guards" / "verification_idle_kick_feature.py",
]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _button_inventory(
    path: Path,
    class_name: str,
) -> dict[str, tuple[str, str, str]]:
    text = _read(path)

    try:
        tree = ast.parse(text, filename=str(path))
    except SyntaxError:
        return {}

    classes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
        and node.name == class_name
    ]

    if len(classes) != 1:
        return {}

    result: dict[str, tuple[str, str, str]] = {}

    for method in classes[0].body:
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

            func = decorator.func

            if not (
                isinstance(func, ast.Attribute)
                and func.attr == "button"
            ):
                continue

            label = ""
            custom_id = ""

            for keyword in decorator.keywords:
                if keyword.arg not in {
                    "label",
                    "custom_id",
                }:
                    continue

                if not isinstance(
                    keyword.value,
                    ast.Constant,
                ):
                    continue

                if not isinstance(
                    keyword.value.value,
                    str,
                ):
                    continue

                if keyword.arg == "label":
                    label = keyword.value.value
                elif keyword.arg == "custom_id":
                    custom_id = keyword.value.value

            result[method.name] = (
                label,
                custom_id,
                ast.get_source_segment(text, method) or "",
            )

    return result


def _assert_native_setup_ux_owners(
    failures: list[str],
) -> None:
    recommend = (
        ROOT
        / "stoney_verify"
        / "commands_ext"
        / "public_setup_recommend.py"
    )
    fresh = (
        ROOT
        / "stoney_verify"
        / "commands_ext"
        / "public_setup_fresh_choice.py"
    )
    retired_guard = (
        ROOT
        / "stoney_verify"
        / "startup_guards"
        / ("setup_" + "ux_clarity_guard.py")
    )

    recommend_text = _read(recommend)
    fresh_text = _read(fresh)

    if retired_guard.exists():
        failures.append(
            f"{retired_guard.relative_to(ROOT)}: "
            "obsolete global setup UX wrapper still exists"
        )

    inventories = {
        "ProductSetupHomeView": _button_inventory(
            recommend,
            "ProductSetupHomeView",
        ),
        "ContinueSetupView": _button_inventory(
            recommend,
            "ContinueSetupView",
        ),
        "ManageSetupView": _button_inventory(
            recommend,
            "ManageSetupView",
        ),
        "AdvancedSettingsHubView": _button_inventory(
            recommend,
            "AdvancedSettingsHubView",
        ),
        "AdvancedDangerZoneView": _button_inventory(
            recommend,
            "AdvancedDangerZoneView",
        ),
        "CustomServiceModeView": _button_inventory(
            fresh,
            "CustomServiceModeView",
        ),
    }

    required_methods = {
        "ProductSetupHomeView": {
            "continue_setup",
            "more_options",
            "close",
        },
        "ContinueSetupView": {
            "fix_next",
            "home",
            "close",
        },
        "ManageSetupView": {
            "change_type",
            "advanced_settings",
            "health",
            "recovery",
            "help_faq",
            "home",
            "close",
        },
        "AdvancedSettingsHubView": {
            "core",
            "tickets",
            "verification",
            "security",
            "logs_activity",
            "design",
            "history",
            "back",
            "home",
            "close",
        },
        "AdvancedDangerZoneView": {
            "recovery",
            "back",
            "home",
            "close",
        },
        "CustomServiceModeView": {
            "continue_guided",
            "back",
            "home",
            "close",
        },
    }

    for owner, required in required_methods.items():
        actual = inventories.get(owner, {})
        if set(actual) != required:
            failures.append(
                f"setup owner {owner} must expose {sorted(required)!r}; "
                f"found {sorted(actual)!r}"
            )
            continue

        labels = [item[0].strip() for item in actual.values()]
        custom_ids = [item[1].strip() for item in actual.values()]
        if any(not label for label in labels):
            failures.append(
                f"setup owner {owner} has an empty button label"
            )
        if any(not custom_id for custom_id in custom_ids):
            failures.append(
                f"setup owner {owner} has an empty custom_id"
            )
        if len(custom_ids) != len(set(custom_ids)):
            failures.append(
                f"setup owner {owner} has duplicate custom_ids"
            )

    home_primary = inventories["ProductSetupHomeView"].get(
        "continue_setup"
    )
    if home_primary is not None:
        for route in (
            "_open_completed_summary(interaction)",
            "_open_test_launch(interaction)",
            "_open_guided_setup(interaction)",
            "_open_choose_setup_type(interaction)",
        ):
            if route not in home_primary[2]:
                failures.append(
                    "setup-home primary action is missing route "
                    f"`{route}`"
                )

    manage_recovery = inventories["ManageSetupView"].get(
        "recovery"
    )
    if (
        manage_recovery is None
        or "await _open_advanced_danger_zone(interaction)"
        not in manage_recovery[2]
        or "_open_recovery_center(interaction)"
        in manage_recovery[2]
    ):
        failures.append(
            "Manage Setup must route repair/restart through the "
            "separate warning screen"
        )

    danger_recovery = inventories[
        "AdvancedDangerZoneView"
    ].get("recovery")
    if (
        danger_recovery is None
        or "await _open_recovery_center(interaction)"
        not in danger_recovery[2]
    ):
        failures.append(
            "the separate repair/restart screen must own the "
            "real recovery-center route"
        )

    custom_continue = inventories[
        "CustomServiceModeView"
    ].get("continue_guided")
    if (
        custom_continue is None
        or "recommend._open_guided_setup(interaction)"
        not in custom_continue[2]
    ):
        failures.append(
            "the core-feature picker must return to Quick Setup"
        )

    destructive_terms = (
        "repair",
        "restart",
        "reset",
        "delete",
        "cleanup",
        "start over",
        "danger",
    )
    for owner in (
        "ProductSetupHomeView",
        "ContinueSetupView",
        "AdvancedSettingsHubView",
        "CustomServiceModeView",
    ):
        inventory = inventories[owner]
        exposed = sorted(
            label
            for label, _custom_id, _source in inventory.values()
            if any(
                term in label.lower()
                for term in destructive_terms
            )
        )
        if exposed:
            failures.append(
                f"normal setup owner {owner} exposes destructive "
                f"actions: {exposed!r}"
            )

    if "class SetupTypeChoiceSelect(discord.ui.Select)" not in fresh_text:
        failures.append(
            "setup type chooser must use SetupTypeChoiceSelect"
        )
    for marker in (
        'custom_id="dank_setup_choice:basic"',
        'custom_id="dank_setup_choice:basic_verify"',
        'custom_id="dank_setup_choice:helpdesk"',
        'custom_id="dank_setup_choice:voice"',
        'custom_id="dank_setup_choice:custom"',
    ):
        if marker in fresh_text:
            failures.append(
                "old setup-type button wall marker remains: "
                f"`{marker}`"
            )

    for current_label in (
        "Recommended Setup",
        "Simple Verify",
        "Help Desk / Tickets",
        "Voice Verify",
        "Choose Core Features",
    ):
        if f'"{current_label}"' not in fresh_text:
            failures.append(
                f"current setup plan `{current_label}` is missing"
            )

    for stale in (
        "Test & Launch",
        "More Options",
        "Other Settings",
        "Back Home",
        "Change Setup Type",
        "Fix Setup or Start Over",
        "Choose My Own Features",
        "Tickets + Server Basics",
        "Member Experience",
        "Monitoring & Repair",
        "Setup Check / Diagnostics",
        "Start / Continue Setup",
        "Test / Launch",
        "Fix Next Item",
        "Advanced Options",
        "Detailed Role / Channel Mapping",
    ):
        if stale in recommend_text or stale in fresh_text:
            failures.append(
                f"stale public setup wording remains: `{stale}`"
            )

    guard_dir = ROOT / "stoney_verify" / "startup_guards"
    forbidden = (
        "_setup_ux_clarity_wrapped",
        'setattr(discord.InteractionResponse, '
        '"edit_message", wrapped_edit_message)',
    )
    for guard_path in guard_dir.glob("*.py"):
        guard_text = _read(guard_path)
        for marker in forbidden:
            if marker in guard_text:
                failures.append(
                    f"{guard_path.relative_to(ROOT)}: obsolete global "
                    f"setup UX wrapper marker remains: `{marker}`"
                )

def _assert_idle_kick_is_per_guild_and_off_by_default(failures: list[str]) -> None:
    feature = ROOT / "stoney_verify" / "startup_guards" / "verification_idle_kick_feature.py"
    controls = ROOT / "stoney_verify" / "startup_guards" / "setup_verification_idle_kick_controls.py"
    feature_text = _read(feature)
    controls_text = _read(controls)

    required_feature = (
        '"verification_idle_kick_enabled"',
        '"verification_idle_kick_minutes"',
        "enabled = _safe_bool",
        "False)",
        "member.guild.id",
        "guild.id",
        "_is_pending",
        "_open_verification_ticket",
    )
    for needle in required_feature:
        if needle not in feature_text:
            failures.append(f"{feature.relative_to(ROOT)}: idle-kick feature missing required per-guild/off-by-default marker `{needle}`")

    required_controls = (
        "Optional per-server feature",
        "off by default",
        "verification_idle_kick_enabled",
        "verification_idle_kick_minutes",
        "Enable / Set Minutes",
        "Disable",
    )
    for needle in required_controls:
        if needle not in controls_text:
            failures.append(f"{controls.relative_to(ROOT)}: setup controls missing plain-language marker `{needle}`")


def _assert_no_private_markers(failures: list[str]) -> None:
    for path in SETUP_FILES:
        text = _read(path)
        for marker in PRIVATE_MARKERS:
            if marker in text:
                failures.append(f"{path.relative_to(ROOT)}: private marker must not appear in public setup code: {marker}")


def _assert_python_parseable(failures: list[str]) -> None:
    for path in SETUP_FILES:
        if not path.exists():
            continue
        try:
            ast.parse(_read(path), filename=str(path))
        except SyntaxError as e:
            failures.append(f"{path.relative_to(ROOT)}:{e.lineno}: syntax error: {e.msg}")


def main() -> int:
    failures: list[str] = []
    _assert_python_parseable(failures)
    _assert_no_private_markers(failures)
    _assert_native_setup_ux_owners(failures)
    _assert_idle_kick_is_per_guild_and_off_by_default(failures)

    if failures:
        print("Setup safety audit failed:")
        for item in failures:
            print(" -", item)
        return 1

    print("Setup safety audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
