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

    home = _button_inventory(
        recommend,
        "ProductSetupHomeView",
    )
    guided = _button_inventory(
        recommend,
        "ContinueSetupView",
    )
    more = _button_inventory(
        recommend,
        "ManageSetupView",
    )
    settings = _button_inventory(
        recommend,
        "AdvancedSettingsHubView",
    )
    danger = _button_inventory(
        recommend,
        "AdvancedDangerZoneView",
    )
    custom = _button_inventory(
        fresh,
        "CustomServiceModeView",
    )

    expected_home = {
        "continue_setup": (
            "Start Setup",
            "dank_setup_home:continue",
        ),
        "more_options": (
            "More Options",
            "dank_setup_home:more_options",
        ),
    }
    expected_guided = {
        "fix_next": (
            "Set Up This Step",
            "dank_setup_guided:fix_next",
        ),
        "home": (
            "Back Home",
            "dank_setup_guided:home",
        ),
    }
    expected_more = {
        "change_type": "Change Setup Type",
        "advanced_settings": "Other Settings",
        "health": "Check Setup for Problems",
        "recovery": "Fix Setup or Start Over",
        "help_faq": "Help",
        "home": "Back Home",
    }
    expected_settings = {
        "core": "Features, Roles & Channels",
        "tickets": "Tickets",
        "safety": "Logs & Safety",
        "design": "Server Design",
        "back": "Back to More Options",
        "home": "Back Home",
    }
    expected_danger = {
        "recovery": "Fix or Start Over",
        "back": "Back to More Options",
        "home": "Back Home",
    }
    expected_custom = {
        "continue_guided": (
            "Continue Setup",
            "dank_setup_custom:continue_guided",
        ),
        "back": (
            "Back",
            "dank_setup_custom:back",
        ),
    }

    if set(home) != set(expected_home):
        failures.append(
            f"{recommend.relative_to(ROOT)}: ProductSetupHomeView "
            f"must expose only {sorted(expected_home)!r}; "
            f"found {sorted(home)!r}"
        )
    for method, expected in expected_home.items():
        actual = home.get(method)
        if actual is None or actual[:2] != expected:
            failures.append(
                f"{recommend.relative_to(ROOT)}: "
                f"ProductSetupHomeView.{method} is "
                f"{actual[:2] if actual else None!r}, "
                f"expected {expected!r}"
            )

    primary = home.get("continue_setup")
    if primary is not None:
        for route in (
            "_open_test_launch(interaction)",
            "_open_guided_setup(interaction)",
            "_open_choose_setup_type(interaction)",
        ):
            if route not in primary[2]:
                failures.append(
                    f"{recommend.relative_to(ROOT)}: setup-home primary "
                    f"action is missing route `{route}`"
                )

    if set(guided) != set(expected_guided):
        failures.append(
            f"{recommend.relative_to(ROOT)}: ContinueSetupView must "
            f"expose only {sorted(expected_guided)!r}; "
            f"found {sorted(guided)!r}"
        )
    for method, expected in expected_guided.items():
        actual = guided.get(method)
        if actual is None or actual[:2] != expected:
            failures.append(
                f"{recommend.relative_to(ROOT)}: "
                f"ContinueSetupView.{method} is "
                f"{actual[:2] if actual else None!r}, "
                f"expected {expected!r}"
            )

    for method, expected_label in expected_more.items():
        actual = more.get(method)
        if actual is None or actual[0] != expected_label:
            failures.append(
                f"{recommend.relative_to(ROOT)}: ManageSetupView.{method} "
                f"label is {actual[0] if actual else None!r}, "
                f"expected {expected_label!r}"
            )

    more_recovery = more.get("recovery")
    if (
        more_recovery is None
        or "await _open_advanced_danger_zone(interaction)"
        not in more_recovery[2]
        or "_open_recovery_center(interaction)"
        in more_recovery[2]
    ):
        failures.append(
            f"{recommend.relative_to(ROOT)}: More Options must route "
            "Fix Setup or Start Over through the separate recovery screen"
        )

    for method, expected_label in expected_settings.items():
        actual = settings.get(method)
        if actual is None or actual[0] != expected_label:
            failures.append(
                f"{recommend.relative_to(ROOT)}: "
                f"AdvancedSettingsHubView.{method} label is "
                f"{actual[0] if actual else None!r}, "
                f"expected {expected_label!r}"
            )

    for method, expected_label in expected_danger.items():
        actual = danger.get(method)
        if actual is None or actual[0] != expected_label:
            failures.append(
                f"{recommend.relative_to(ROOT)}: "
                f"AdvancedDangerZoneView.{method} label is "
                f"{actual[0] if actual else None!r}, "
                f"expected {expected_label!r}"
            )

    danger_recovery = danger.get("recovery")
    if (
        danger_recovery is None
        or "await _open_recovery_center(interaction)"
        not in danger_recovery[2]
    ):
        failures.append(
            f"{recommend.relative_to(ROOT)}: separate recovery screen "
            "must own the real recovery-center route"
        )

    if set(custom) != set(expected_custom):
        failures.append(
            f"{fresh.relative_to(ROOT)}: CustomServiceModeView must "
            f"expose only {sorted(expected_custom)!r}; "
            f"found {sorted(custom)!r}"
        )
    for method, expected in expected_custom.items():
        actual = custom.get(method)
        if actual is None or actual[:2] != expected:
            failures.append(
                f"{fresh.relative_to(ROOT)}: "
                f"CustomServiceModeView.{method} is "
                f"{actual[:2] if actual else None!r}, "
                f"expected {expected!r}"
            )

    # Normal setup screens must never expose repair/reset/destructive actions.
    destructive_labels = {
        "Fix Setup or Start Over",
        "Fix or Start Over",
        "Recovery / Start Over",
        "Danger Zone",
    }
    for owner_name, inventory in (
        ("ProductSetupHomeView", home),
        ("ContinueSetupView", guided),
        ("AdvancedSettingsHubView", settings),
        ("CustomServiceModeView", custom),
    ):
        exposed = {
            item[0]
            for item in inventory.values()
            if item[0] in destructive_labels
        }
        if exposed:
            failures.append(
                f"{recommend.relative_to(ROOT)}: {owner_name} exposes "
                f"destructive setup actions: {sorted(exposed)!r}"
            )

    # Setup type selection must stay a single compact select, not a button wall.
    if "class SetupTypeChoiceSelect(discord.ui.Select)" not in fresh_text:
        failures.append(
            f"{fresh.relative_to(ROOT)}: setup type chooser must use "
            "SetupTypeChoiceSelect"
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
                f"{fresh.relative_to(ROOT)}: old setup-type button "
                f"wall marker remains: `{marker}`"
            )

    for label in (
        "Tickets + Server Basics",
        "Simple Verify",
        "Help Desk / Tickets",
        "Voice Verify",
        "Choose My Own Features",
    ):
        if f'"{label}"' not in fresh_text:
            failures.append(
                f"{fresh.relative_to(ROOT)}: setup type `{label}` is missing"
            )

    # Retired/vague public setup terms must not return.
    for stale in (
        "Member Experience",
        "Core Setup",
        "Monitoring & Repair",
        "Setup Check / Diagnostics",
        "Start / Continue Setup",
        "Test / Launch",
        "Fix Next Item",
        "Advanced Options",
        "Detailed Role / Channel Mapping",
    ):
        if stale in recommend_text:
            failures.append(
                f"{recommend.relative_to(ROOT)}: stale public setup "
                f"wording remains: `{stale}`"
            )

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
        guard_text = _read(path)

        for marker in forbidden:
            if marker in guard_text:
                failures.append(
                    f"{path.relative_to(ROOT)}: "
                    f"obsolete global setup UX wrapper "
                    f"marker remains: `{marker}`"
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
