from __future__ import annotations

"""Safety audit for the public ``/dank setup`` surface.

The permanent setup audit focuses on architecture and high-risk regressions:
- canonical public setup owners remain present and unambiguous;
- normal setup screens do not expose destructive recovery actions;
- retired setup UX monkey-patch wrappers do not return;
- verification timers are owned natively by the setup module;
- the no-start auto-remove runtime remains per-guild and off by default;
- private server identifiers never leak into setup-facing code.

Behavior details belong in pytest. This file intentionally stays a compact
architecture/safety audit rather than duplicating the behavior suite.
"""

import ast
from pathlib import Path

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
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_recommend.py",
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_fresh_choice.py",
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_full_customization.py",
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_recovery.py",
    ROOT / "stoney_verify" / "commands_ext" / "public_setup_cleanup.py",
    ROOT / "stoney_verify" / "config_history_ui.py",
    ROOT / "stoney_verify" / "setup_service_state.py",
    ROOT / "stoney_verify" / "startup_guards" / "setup_feature_health_scoreboard.py",
    ROOT / "stoney_verify" / "startup_guards" / "setup_scoreboard_command.py",
    ROOT / "stoney_verify" / "startup_guards" / "verification_idle_kick_feature.py",
]


def _read(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _button_inventory(
    path: Path,
    class_name: str,
) -> dict[str, tuple[str, str, str]]:
    """Return decorated buttons declared directly by one class."""

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
            (ast.FunctionDef, ast.AsyncFunctionDef),
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
                if keyword.arg not in {"label", "custom_id"}:
                    continue
                if not isinstance(keyword.value, ast.Constant):
                    continue
                if not isinstance(keyword.value.value, str):
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


def _assert_exact_button_owners(
    failures: list[str],
    path: Path,
    expected: dict[str, set[str]],
) -> dict[str, dict[str, tuple[str, str, str]]]:
    inventories: dict[str, dict[str, tuple[str, str, str]]] = {}

    for owner, required in expected.items():
        inventory = _button_inventory(path, owner)
        inventories[owner] = inventory
        actual = set(inventory)
        if actual != required:
            failures.append(
                f"setup owner {owner} must expose {sorted(required)!r}; "
                f"found {sorted(actual)!r}"
            )
            continue

        labels = [item[0].strip() for item in inventory.values()]
        custom_ids = [item[1].strip() for item in inventory.values()]
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

    return inventories


def _assert_native_setup_ux_owners(failures: list[str]) -> None:
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

    recommend_text = _read(recommend)
    fresh_text = _read(fresh)

    retired_wrappers = (
        ROOT / "stoney_verify" / "startup_guards" / "setup_ux_clarity_guard.py",
        ROOT
        / "stoney_verify"
        / "startup_guards"
        / "setup_verification_idle_kick_controls.py",
    )
    for path in retired_wrappers:
        if path.exists():
            failures.append(
                f"{path.relative_to(ROOT)}: obsolete setup UI wrapper still exists"
            )

    inventories = _assert_exact_button_owners(
        failures,
        recommend,
        {
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
        },
    )

    fresh_inventories = _assert_exact_button_owners(
        failures,
        fresh,
        {
            "CustomServiceModeView": {
                "continue_guided",
                "back",
                "home",
                "close",
            },
            "SetupTypeChoiceView": {
                "home",
                "close",
            },
        },
    )
    inventories.update(fresh_inventories)

    home_primary = inventories.get(
        "ProductSetupHomeView",
        {},
    ).get("continue_setup")
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

    manage_recovery = inventories.get(
        "ManageSetupView",
        {},
    ).get("recovery")
    if (
        manage_recovery is None
        or "await _open_advanced_danger_zone(interaction)"
        not in manage_recovery[2]
        or "_open_recovery_center(interaction)"
        in manage_recovery[2]
    ):
        failures.append(
            "Manage Setup must route repair/restart through the warning screen"
        )

    danger_recovery = inventories.get(
        "AdvancedDangerZoneView",
        {},
    ).get("recovery")
    if (
        danger_recovery is None
        or "await _open_recovery_center(interaction)"
        not in danger_recovery[2]
    ):
        failures.append(
            "the separate repair/restart screen must own the recovery route"
        )

    custom_continue = inventories.get(
        "CustomServiceModeView",
        {},
    ).get("continue_guided")
    if (
        custom_continue is None
        or "recommend._open_guided_setup("
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
        "SetupTypeChoiceView",
    ):
        inventory = inventories.get(owner, {})
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
                f"normal setup owner {owner} exposes destructive actions: "
                f"{exposed!r}"
            )

    if "class SetupTypeChoiceSelect(discord.ui.Select)" not in fresh_text:
        failures.append(
            "setup type chooser must use SetupTypeChoiceSelect"
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


def _assert_native_verification_timer_controls(
    failures: list[str],
) -> None:
    solid = (
        ROOT
        / "stoney_verify"
        / "commands_ext"
        / "public_setup_solid.py"
    )

    inventories = _assert_exact_button_owners(
        failures,
        solid,
        {
            "VerificationTimerSettingsView": {
                "enable_wait",
                "disable_wait",
                "change_wait_hours",
                "clear_wait",
                "idle_timer",
            },
            "VerificationIdleTimerSettingsView": {
                "enable_idle",
                "disable_idle",
                "change_idle_minutes",
            },
        },
    )

    labels = {
        label
        for inventory in inventories.values()
        for label, _custom_id, _source in inventory.values()
    }
    for required in (
        "Enable Wait Timer",
        "Disable + Clear Wait Timer",
        "Advanced No-Start Timer",
        "Enable No-Start Timer",
        "Disable + Clear No-Start",
        "Change No-Start Minutes",
    ):
        if required not in labels:
            failures.append(
                f"native verification timer control is missing `{required}`"
            )


def _assert_idle_kick_is_per_guild_and_off_by_default(
    failures: list[str],
) -> None:
    feature = (
        ROOT
        / "stoney_verify"
        / "startup_guards"
        / "verification_idle_kick_feature.py"
    )
    feature_text = _read(feature)

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
            failures.append(
                f"{feature.relative_to(ROOT)}: idle-kick runtime missing "
                f"per-guild/off-by-default marker `{needle}`"
            )


def _assert_no_private_markers(failures: list[str]) -> None:
    for path in SETUP_FILES:
        text = _read(path)
        for marker in PRIVATE_MARKERS:
            if marker in text:
                failures.append(
                    f"{path.relative_to(ROOT)}: private marker must not appear "
                    f"in public setup code: {marker}"
                )


def _assert_python_parseable(failures: list[str]) -> None:
    for path in SETUP_FILES:
        if not path.exists():
            continue
        try:
            ast.parse(_read(path), filename=str(path))
        except SyntaxError as exc:
            failures.append(
                f"{path.relative_to(ROOT)}:{exc.lineno}: "
                f"syntax error: {exc.msg}"
            )


def main() -> int:
    failures: list[str] = []
    _assert_python_parseable(failures)
    _assert_no_private_markers(failures)
    _assert_native_setup_ux_owners(failures)
    _assert_native_verification_timer_controls(failures)
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
