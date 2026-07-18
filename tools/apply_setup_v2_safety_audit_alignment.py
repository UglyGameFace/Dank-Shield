from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "tools/audit_setup_safety.py"

text = AUDIT.read_text(encoding="utf-8")
start = text.index("def _assert_native_setup_ux_owners(")
end = text.index("def _assert_idle_kick_is_per_guild_and_off_by_default", start)

replacement = r'''def _assert_native_setup_ux_owners(
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


'''

AUDIT.write_text(text[:start] + replacement + text[end:], encoding="utf-8")
compile(AUDIT.read_text(encoding="utf-8"), str(AUDIT), "exec")
print("PASS: aligned setup safety audit with one-path plain-language UX")
