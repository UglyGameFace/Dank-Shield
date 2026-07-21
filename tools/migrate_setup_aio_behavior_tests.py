from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"ERROR: expected one {label}; found {count}")
    return text.replace(old, new, 1)


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
)

for filename in obsolete:
    target = Path(filename)
    if not target.exists():
        raise SystemExit(f"ERROR: obsolete test missing: {filename}")
    target.unlink()

print("✅ Updated active behavior tests for the AIO setup contract")
print("✅ Replaced AST/source-shape permission tests with runtime behavior tests")
print(f"✅ Removed {len(obsolete)} obsolete static/retired checks")
