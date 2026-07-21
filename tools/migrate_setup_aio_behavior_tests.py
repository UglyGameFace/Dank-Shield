from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"ERROR: expected one {label}; found {count}")
    return text.replace(old, new, 1)


def replace_between(text: str, start: str, end: str, replacement: str, *, label: str) -> str:
    start_at = text.find(start)
    if start_at < 0:
        raise SystemExit(f"ERROR: {label} start marker not found")
    end_at = text.find(end, start_at)
    if end_at < 0:
        raise SystemExit(f"ERROR: {label} end marker not found")
    return text[:start_at] + replacement.rstrip() + "\n\n" + text[end_at:]


path = Path("tests/test_config_history_ui_behavior.py")
text = path.read_text(encoding="utf-8")
text = replace_once(
    text,
    '''        "Back to Other Settings",\n        "Back Home",\n''',
    '''        "Back to All Features",\n        "Setup Home",\n        "Close",\n''',
    label="history navigation set",
)
text = text.replace("assert max(rows.values()) <= 2", "assert max(rows.values()) <= 3", 1)
text = replace_once(
    text,
    '''        "Back to History",\n        "Back to Other Settings",\n''',
    '''        "Back to History",\n        "Back to All Features",\n        "Setup Home",\n        "Close",\n''',
    label="version detail navigation set",
)
text = replace_once(
    text,
    '''        "Review Selected",\n        "Back to Version",\n''',
    '''        "Review Selected",\n        "Back to Version",\n        "Setup Home",\n        "Close",\n''',
    label="selective picker navigation set",
)
path.write_text(text, encoding="utf-8")


path = Path("tests/test_setup_automatic_review_behavior.py")
text = path.read_text(encoding="utf-8")
for old, new in (
    ("Test & Launch", "Test Your Setup"),
    ("Back Home", "Setup Home"),
    ("Other Settings", "All Features & Settings"),
    ("Change Setup Type", "Change Setup Plan"),
):
    text = text.replace(old, new)
path.write_text(text, encoding="utf-8")


path = Path("tests/test_setup_bot_access_feature.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    "def test_logs_and_safety_exposes_plain_check_bot_access_feature() -> None:\n"
    "    view = recommend.AdvancedMonitoringRepairView()",
    "def test_security_section_separates_access_check_from_permission_repair() -> None:\n"
    "    view = recommend.AdvancedSecurityView()",
    1,
)
text = text.replace(
    'assert access.custom_id == "dank_setup_advanced_monitoring:bot_access"',
    'assert access.custom_id == "dank_setup_security:access"',
    1,
)
text = text.replace("assert access.row == 1", "assert access.row == 0", 1)
text = text.replace(
    'assert repair.custom_id == "dank_setup_advanced_monitoring:permission_repair"',
    'assert repair.custom_id == "dank_setup_security:repair"',
    1,
)
path.write_text(text, encoding="utf-8")


path = Path("tests/test_setup_first_screen_semantics.py")
text = path.read_text(encoding="utf-8")
text = text.replace('"Tickets + Server Basics"', '"Recommended Setup"')
text = text.replace('"Choose My Own Features"', '"Choose Core Features"')
path.write_text(text, encoding="utf-8")


Path("tests/test_setup_permission_repair_native_route.py").write_text(
    '''from __future__ import annotations\n\nimport asyncio\nfrom collections import Counter\nfrom types import SimpleNamespace\nfrom typing import Any\n\nimport discord\nimport pytest\n\nfrom stoney_verify import setup_permission_repair_services\nfrom stoney_verify.commands_ext import public_setup_recommend as recommend\n\n\ndef button(view: discord.ui.View, label: str) -> discord.ui.Button:\n    matches = [\n        child\n        for child in view.children\n        if isinstance(child, discord.ui.Button)\n        and str(getattr(child, "label", "") or "") == label\n    ]\n    assert len(matches) == 1\n    return matches[0]\n\n\ndef test_native_permission_repair_route_calls_owned_service(\n    monkeypatch: pytest.MonkeyPatch,\n) -> None:\n    events: list[Any] = []\n\n    async def allow(_interaction: Any) -> bool:\n        return True\n\n    async def open_repair(interaction: Any) -> None:\n        events.append(interaction)\n\n    monkeypatch.setattr(recommend.solid, "_require_setup_permission", allow)\n    monkeypatch.setattr(\n        setup_permission_repair_services,\n        "open_permission_repair",\n        open_repair,\n    )\n\n    interaction = SimpleNamespace(guild=SimpleNamespace(id=123))\n    asyncio.run(recommend._open_permission_repair(interaction))\n    assert events == [interaction]\n\n\ndef test_security_button_uses_native_permission_repair_route(\n    monkeypatch: pytest.MonkeyPatch,\n) -> None:\n    events: list[str] = []\n\n    async def route(interaction: Any) -> None:\n        events.append("repair")\n\n    monkeypatch.setattr(recommend, "_open_permission_repair", route)\n    view = recommend.AdvancedSecurityView()\n    repair = button(view, "Fix Channel Permissions")\n\n    assert repair.custom_id == "dank_setup_security:repair"\n    assert repair.row == 1\n    asyncio.run(repair.callback(SimpleNamespace()))\n    assert events == ["repair"]\n\n\ndef test_security_and_logs_keep_access_check_distinct_from_repair() -> None:\n    security = recommend.AdvancedSecurityView()\n    logs = recommend.AdvancedLogsActivityView()\n\n    assert button(security, "Check Bot Access").custom_id == "dank_setup_security:access"\n    assert button(security, "Fix Channel Permissions").custom_id == "dank_setup_security:repair"\n    assert button(logs, "Check Activity Access").custom_id == "dank_setup_logs:access"\n    assert not any(\n        str(getattr(child, "label", "") or "") == "Fix Channel Permissions"\n        for child in logs.children\n    )\n\n\ndef test_manage_setup_rows_are_discord_safe() -> None:\n    for view in (\n        recommend.ManageSetupView(),\n        recommend.AdvancedSettingsHubView(),\n        recommend.AdvancedSecurityView(),\n        recommend.AdvancedLogsActivityView(),\n    ):\n        rows = Counter(int(getattr(child, "row", 0) or 0) for child in view.children)\n        assert rows\n        assert all(count <= 5 for count in rows.values())\n        assert len(view.children) <= 25\n\n\ndef test_owned_permission_repair_service_remains() -> None:\n    assert callable(setup_permission_repair_services.open_permission_repair)\n    assert callable(setup_permission_repair_services.apply_permission_repair)\n    assert callable(setup_permission_repair_services.result_embed)\n''',
    encoding="utf-8",
)


# Keep every setup-plan route on the same current language, including the
# compatibility preview path retained in public_setup_recommend.
recommend_path = Path(
    "stoney_verify/commands_ext/public_setup_recommend.py"
)
recommend_text = recommend_path.read_text(encoding="utf-8")
for old, new, label in (
    (
        "Press **Use This Setup** to save this choice, or pick another option from the menu.",
        "Press **Use This Plan** to save this choice, or pick another option from the menu.",
        "setup preview save instruction",
    ),
    (
        'label="Use This Setup"',
        'label="Use This Plan"',
        "setup plan save button",
    ),
    (
        "Saved **Choose My Own Features**. Choose which features are ON or OFF below.",
        "Saved **Choose Core Features**. Choose the core modules this server should use, then press **Continue Quick Setup**.",
        "custom plan saved copy",
    ),
    (
        "Next, return to the guided setup and continue one required step at a time.",
        "Next, return to Quick Setup and continue one required step at a time.",
        "saved plan next step",
    ),
    (
        "Press **Continue Setup** on Setup Home. ",
        "Press **Continue Quick Setup** on Setup Home. ",
        "saved plan home instruction",
    ),
    (
        'label="Preview Only"',
        'label="Preview"',
        "setup plan preview button",
    ),
):
    recommend_text = replace_once(
        recommend_text,
        old,
        new,
        label=label,
    )
recommend_path.write_text(recommend_text, encoding="utf-8")


# Keep the setup safety audit focused on safety and ownership. Exact labels,
# routes, and component behavior are covered by runtime behavior tests.
audit = Path("tools/audit_setup_safety.py")
audit_text = audit.read_text(encoding="utf-8")
audit_text = replace_once(
    audit_text,
    '''    ROOT / "stoney_verify" / "commands_ext" / "public_setup_fresh_choice.py",\n    ROOT / "stoney_verify" / "startup_guards" / "verification_idle_kick_feature.py",\n''',
    '''    ROOT / "stoney_verify" / "commands_ext" / "public_setup_fresh_choice.py",\n    ROOT / "stoney_verify" / "commands_ext" / "public_setup_full_customization.py",\n    ROOT / "stoney_verify" / "commands_ext" / "public_setup_recovery.py",\n    ROOT / "stoney_verify" / "commands_ext" / "public_setup_cleanup.py",\n    ROOT / "stoney_verify" / "config_history_ui.py",\n    ROOT / "stoney_verify" / "setup_service_state.py",\n    ROOT / "stoney_verify" / "startup_guards" / "verification_idle_kick_feature.py",\n''',
    label="expanded setup safety file inventory",
)

new_owner_audit = r'''def _assert_native_setup_ux_owners(
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
'''

audit_text = replace_between(
    audit_text,
    "def _assert_native_setup_ux_owners(",
    "def _assert_idle_kick_is_per_guild_and_off_by_default(",
    new_owner_audit,
    label="setup safety ownership audit",
)
audit.write_text(audit_text, encoding="utf-8")


obsolete = (
    "tests/test_setup_existing_items_plain_entry_static.py",
    "tests/test_setup_feature_aware_health_static.py",
    "tests/test_setup_first_run_guard_retired.py",
    "tests/test_setup_first_run_simple_route_static.py",
    "tests/test_setup_front_door_native_no_patch.py",
    "tests/test_setup_health_action_buttons_guard_retired.py",
    "tests/test_setup_health_next_action_guard_retired.py",
    "tests/test_setup_home_authority_guard_retired.py",
    "tests/test_setup_home_button_emoji_static.py",
    "tests/test_setup_permission_repair_guard_native_no_payload_patch.py",
    "tests/test_setup_single_guided_front_door_static.py",
    "tests/test_setup_single_path_front_door_v2_static.py",
    "tests/test_setup_success_next_step_guard_retired.py",
    "tests/test_setup_ux_clarity_guard_retired.py",
    "tools/test_custom_state_and_launch_actions.py",
    "tools/test_setup_choice_preview_compact.py",
    "tools/test_setup_existing_join_leave_menu_static.py",
    "tools/test_setup_home_final_architecture.py",
)

for filename in obsolete:
    target = Path(filename)
    if not target.exists():
        raise SystemExit(f"ERROR: obsolete test missing: {filename}")
    target.unlink()

print("✅ Updated active behavior tests for the AIO setup contract")
print("✅ Unified the compatibility setup-plan copy")
print("✅ Replaced exact-copy safety checks with ownership and isolation checks")
print(f"✅ Removed {len(obsolete)} obsolete static/retired checks")
