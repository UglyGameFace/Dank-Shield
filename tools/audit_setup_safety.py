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
    advanced = _button_inventory(
        recommend,
        "ManageSetupView",
    )
    danger_advanced = _button_inventory(
        recommend,
        "AdvancedDangerZoneView",
    )
    choices = _button_inventory(
        fresh,
        "SetupTypeChoiceView",
    )

    expected_home = {
        "continue_setup": (
            "Start / Continue Setup",
            "dank_setup_home:continue",
        ),
        "health": (
            "Setup Check",
            "dank_setup_home:health",
        ),
        "launch": (
            "Test / Launch",
            "dank_setup_home:launch",
        ),
    }

    expected_guided = {
        "fix_next": (
            "Fix Next Item",
            "dank_setup_guided:fix_next",
        ),
        "review": (
            "Setup Check",
            "dank_setup_guided:review",
        ),
        "change_type": (
            "Change Setup Type",
            "dank_setup_guided:change_type",
        ),
        "advanced": (
            "Advanced Options",
            "dank_setup_guided:advanced",
        ),
        "home": (
            "Back Home",
            "dank_setup_guided:home",
        ),
    }

    expected_advanced = {
    "core_setup": "Core Setup",
    "member_experience": "Member Experience",
    "monitoring_repair": "Monitoring & Repair",
    "appearance": "Appearance",
    "danger_zone": "Danger Zone",
    "help_faq": "Help / FAQ",
    "home": "Back Home",
}

    expected_choices = {
        "basic": (
            "Basic Server",
            "dank_setup_choice:basic",
        ),
        "basic_verify": (
            "Basic Verify",
            "dank_setup_choice:basic_verify",
        ),
        "helpdesk": (
            "Help Desk",
            "dank_setup_choice:helpdesk",
        ),
        "voice_check": (
            "Voice Verify",
            "dank_setup_choice:voice",
        ),
        "custom": (
            "Custom",
            "dank_setup_choice:custom",
        ),
    }

    for method, expected in expected_home.items():
        actual = home.get(method)

        if actual is None:
            failures.append(
                f"{recommend.relative_to(ROOT)}: "
                f"ProductSetupHomeView missing `{method}`"
            )
            continue

        if actual[:2] != expected:
            failures.append(
                f"{recommend.relative_to(ROOT)}: "
                f"ProductSetupHomeView.{method} is "
                f"{actual[:2]!r}, expected {expected!r}"
            )

    for method, expected in expected_guided.items():
        actual = guided.get(method)

        if actual is None:
            failures.append(
                f"{recommend.relative_to(ROOT)}: "
                f"ContinueSetupView missing `{method}`"
            )
            continue

        if actual[:2] != expected:
            failures.append(
                f"{recommend.relative_to(ROOT)}: "
                f"ContinueSetupView.{method} is "
                f"{actual[:2]!r}, expected {expected!r}"
            )

    change_type = guided.get("change_type")

    if (
        change_type is not None
        and "await _open_choose_setup_type(interaction)"
        not in change_type[2]
    ):
        failures.append(
            f"{recommend.relative_to(ROOT)}: "
            "Change Setup Type does not open the existing "
            "setup-type picker"
        )

    for method, expected_label in (
        expected_advanced.items()
    ):
        actual = advanced.get(method)

        if actual is None:
            failures.append(
                f"{recommend.relative_to(ROOT)}: "
                f"ManageSetupView missing `{method}`"
            )
            continue

        if actual[0] != expected_label:
            failures.append(
                f"{recommend.relative_to(ROOT)}: "
                f"ManageSetupView.{method} label is "
                f"{actual[0]!r}, expected "
                f"{expected_label!r}"
            )

    flat_forbidden = {
    "Features On / Off",
    "Ticket Choices",
    "Protection",
    "Modlog Tracking",
    "Timers & Behavior",
    "Server Design",
    "Detailed Role / Channel Mapping",
    "Permission Repair",
    "Recovery / Start Over",
}
    exposed_flat = {
        button[0]
        for button in advanced.values()
        if button[0] in flat_forbidden
    }
    if exposed_flat:
        failures.append(
            f"{recommend.relative_to(ROOT)}: ManageSetupView still exposes "
            f"flat advanced actions: {sorted(exposed_flat)!r}"
        )

    recovery = danger_advanced.get("recovery")
    if recovery is None or recovery[0] != "Recovery / Start Over":
        failures.append(
            f"{recommend.relative_to(ROOT)}: AdvancedDangerZoneView must own "
            "Recovery / Start Over"
        )

    for method, expected in expected_choices.items():
        actual = choices.get(method)

        if actual is None:
            failures.append(
                f"{fresh.relative_to(ROOT)}: "
                f"SetupTypeChoiceView missing `{method}`"
            )
            continue

        if actual[:2] != expected:
            failures.append(
                f"{fresh.relative_to(ROOT)}: "
                f"SetupTypeChoiceView.{method} is "
                f"{actual[:2]!r}, expected {expected!r}"
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
        text = _read(path)

        for marker in forbidden:
            if marker in text:
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
