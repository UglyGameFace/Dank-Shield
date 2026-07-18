from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECOMMEND = ROOT / "stoney_verify/commands_ext/public_setup_recommend.py"


def replace_named_test(text: str, name: str, replacement: str) -> str:
    marker = f"def {name}("
    start = text.index(marker)
    next_def = text.find("\ndef ", start + len(marker))
    if next_def == -1:
        return text[:start] + replacement.rstrip() + "\n"
    return text[:start] + replacement.rstrip() + "\n\n" + text[next_def + 1 :]


# ---------------------------------------------------------------------------
# Production wording: Setup Check must teach the same one-path workflow.
# ---------------------------------------------------------------------------
recommend = RECOMMEND.read_text(encoding="utf-8")
production_replacements = {
    '"Turn on at least one feature under "\n            "**Advanced Options → Features On / Off**."':
        '"Press **Continue Setup** and turn on at least one feature."',
    '"Ticket choices could not be checked. "\n                    "Open **Advanced Options → Ticket Choices**."':
        '"Ticket choices could not be checked. Press **Continue Setup** to fix them."',
    'f"Basic Verify: `{\'ON\' if services[\'basic_verify\'] else \'OFF\'}`\\n"':
        'f"Simple Verify: `{\'ON\' if services[\'basic_verify\'] else \'OFF\'}`\\n"',
    '"Press **Start / Continue Setup** to finish "\n            "anything required.\\n"\n            "Press **Test / Launch** after this page says ready."':
        '"Press **Continue Setup** to fix anything required.\\n"\n            "Press **Test & Launch** after this page says ready."',
}
for old, new in production_replacements.items():
    recommend = recommend.replace(old, new)
RECOMMEND.write_text(recommend, encoding="utf-8")


# ---------------------------------------------------------------------------
# Advanced / More Options behavior tests.
# ---------------------------------------------------------------------------
advanced_path = ROOT / "tests/test_setup_advanced_options_behavior.py"
advanced_path.write_text('''from __future__ import annotations

import asyncio
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import discord
import pytest

from stoney_verify.commands_ext import public_protection_center
from stoney_verify.commands_ext import public_setup_recommend as recommend


def run(coroutine: Any) -> Any:
    return asyncio.run(coroutine)


def labels(view: discord.ui.View) -> set[str]:
    return {str(getattr(child, "label", "") or "") for child in view.children}


def find_button(view: discord.ui.View, label: str) -> discord.ui.Button:
    matches = [child for child in view.children if str(getattr(child, "label", "") or "") == label]
    assert len(matches) == 1
    assert isinstance(matches[0], discord.ui.Button)
    return matches[0]


def assert_mobile_rows(view: discord.ui.View) -> None:
    counts = Counter(int(getattr(child, "row", 0) or 0) for child in view.children)
    assert counts
    assert max(counts.values()) <= 2


class FakeResponse:
    async def edit_message(self, **kwargs: Any) -> None:
        return None

    async def send_message(self, *args: Any, **kwargs: Any) -> None:
        return None


class FakeInteraction:
    def __init__(self) -> None:
        self.guild = SimpleNamespace(id=8080)
        self.user = SimpleNamespace(id=77)
        self.response = FakeResponse()


def test_more_options_is_secondary_and_literal():
    assert labels(recommend.ManageSetupView()) == {
        "Change Setup Type",
        "Other Settings",
        "Check Setup for Problems",
        "Fix Setup or Start Over",
        "Help",
        "Back Home",
    }


def test_other_settings_hub_uses_literal_task_names():
    assert labels(recommend.AdvancedSettingsHubView()) == {
        "Features, Roles & Channels",
        "Tickets",
        "Logs & Safety",
        "Server Design",
        "Back to More Options",
        "Back Home",
    }


def test_other_settings_submenus_keep_existing_tools():
    assert labels(recommend.AdvancedCoreSetupView()) == {
        "Turn Features On / Off",
        "Timers & Rules",
        "Choose Roles & Channels",
        "Back to Other Settings",
        "Back Home",
    }
    assert labels(recommend.AdvancedMemberExperienceView()) == {
        "Ticket Choices",
        "Back to Other Settings",
        "Back Home",
    }
    assert labels(recommend.AdvancedMonitoringRepairView()) == {
        "Choose What Gets Logged",
        "Spam & Raid Protection",
        "Fix Channel Permissions",
        "Back to Other Settings",
        "Back Home",
    }
    assert labels(recommend.AdvancedAppearanceView()) == {
        "Server Design",
        "Back to Other Settings",
        "Back Home",
    }
    assert labels(recommend.AdvancedDangerZoneView()) == {
        "Fix or Start Over",
        "Back to More Options",
        "Back Home",
    }


def test_reset_is_not_mixed_into_normal_settings():
    for view in (
        recommend.AdvancedSettingsHubView(),
        recommend.AdvancedCoreSetupView(),
        recommend.AdvancedMemberExperienceView(),
        recommend.AdvancedMonitoringRepairView(),
        recommend.AdvancedAppearanceView(),
    ):
        assert "Fix or Start Over" not in labels(view)
    assert "Fix or Start Over" in labels(recommend.AdvancedDangerZoneView())


def test_all_secondary_pages_are_mobile_compact():
    for view in (
        recommend.ManageSetupView(),
        recommend.AdvancedSettingsHubView(),
        recommend.AdvancedCoreSetupView(),
        recommend.AdvancedMemberExperienceView(),
        recommend.AdvancedMonitoringRepairView(),
        recommend.AdvancedAppearanceView(),
        recommend.AdvancedDangerZoneView(),
    ):
        assert_mobile_rows(view)


@pytest.mark.parametrize(
    ("view_cls", "label", "route_name"),
    (
        (recommend.ManageSetupView, "Change Setup Type", "_open_choose_setup_type"),
        (recommend.ManageSetupView, "Other Settings", "_open_advanced_settings"),
        (recommend.ManageSetupView, "Check Setup for Problems", "_open_health_check"),
        (recommend.ManageSetupView, "Fix Setup or Start Over", "_open_advanced_danger_zone"),
        (recommend.ManageSetupView, "Back Home", "_home_edit"),
        (recommend.AdvancedCoreSetupView, "Turn Features On / Off", "_open_services"),
        (recommend.AdvancedCoreSetupView, "Timers & Rules", "_open_timers_behavior"),
        (recommend.AdvancedCoreSetupView, "Choose Roles & Channels", "_open_existing_server"),
        (recommend.AdvancedMemberExperienceView, "Ticket Choices", "_open_ticket_menu"),
        (recommend.AdvancedMonitoringRepairView, "Choose What Gets Logged", "_open_modlog_tracking"),
        (recommend.AdvancedMonitoringRepairView, "Spam & Raid Protection", "_open_protection_options"),
        (recommend.AdvancedMonitoringRepairView, "Fix Channel Permissions", "_open_permission_repair"),
        (recommend.AdvancedDangerZoneView, "Fix or Start Over", "_open_recovery_center"),
    ),
)
def test_buttons_reuse_existing_runtime_routes(monkeypatch: pytest.MonkeyPatch, view_cls: type[discord.ui.View], label: str, route_name: str) -> None:
    events: list[str] = []

    async def route(*args: Any, **kwargs: Any) -> None:
        events.append(route_name)

    monkeypatch.setattr(recommend, route_name, route)
    run(find_button(view_cls(), label).callback(FakeInteraction()))
    assert events == [route_name]


@pytest.mark.parametrize(
    ("label", "route_name"),
    (
        ("Features, Roles & Channels", "_open_advanced_core_setup"),
        ("Tickets", "_open_advanced_member_experience"),
        ("Logs & Safety", "_open_advanced_monitoring_repair"),
        ("Server Design", "_open_advanced_appearance"),
    ),
)
def test_other_settings_groups_open_focused_submenus(monkeypatch: pytest.MonkeyPatch, label: str, route_name: str) -> None:
    events: list[str] = []

    async def route(*args: Any, **kwargs: Any) -> None:
        events.append(route_name)

    monkeypatch.setattr(recommend, route_name, route)
    run(find_button(recommend.AdvancedSettingsHubView(), label).callback(FakeInteraction()))
    assert events == [route_name]


def test_more_options_screen_uses_canonical_view(monkeypatch: pytest.MonkeyPatch) -> None:
    interaction = FakeInteraction()
    captured: dict[str, Any] = {}

    async def allow(*args: Any, **kwargs: Any) -> bool:
        return True

    async def edit(interaction_arg: Any, *, embed: discord.Embed, view: discord.ui.View) -> None:
        captured["interaction"] = interaction_arg
        captured["embed"] = embed
        captured["view"] = view

    monkeypatch.setattr(recommend.solid, "_require_setup_permission", allow)
    monkeypatch.setattr(recommend.solid, "_edit_or_followup", edit)
    run(recommend._open_manage_setup(interaction))
    assert captured["interaction"] is interaction
    assert captured["embed"].title == "••• More Options"
    assert isinstance(captured["view"], recommend.ManageSetupView)
    assert any("Fix Setup or Start Over" in field.name for field in captured["embed"].fields)


def test_other_settings_screen_uses_canonical_hub(monkeypatch: pytest.MonkeyPatch) -> None:
    interaction = FakeInteraction()
    captured: dict[str, Any] = {}

    async def allow(*args: Any, **kwargs: Any) -> bool:
        return True

    async def edit(interaction_arg: Any, *, embed: discord.Embed, view: discord.ui.View) -> None:
        captured["embed"] = embed
        captured["view"] = view

    monkeypatch.setattr(recommend.solid, "_require_setup_permission", allow)
    monkeypatch.setattr(recommend.solid, "_edit_or_followup", edit)
    run(recommend._open_advanced_settings(interaction))
    assert captured["embed"].title == "⚙️ Other Settings"
    assert isinstance(captured["view"], recommend.AdvancedSettingsHubView)


def test_protection_reuses_protection_center(monkeypatch: pytest.MonkeyPatch) -> None:
    interaction = FakeInteraction()
    events: list[str] = []

    async def allow(*args: Any, **kwargs: Any) -> bool:
        return True

    async def refresh(interaction_arg: Any, *, content: str) -> None:
        assert interaction_arg is interaction
        assert "Other Settings" in content
        events.append("protection")

    monkeypatch.setattr(recommend.solid, "_require_setup_permission", allow)
    monkeypatch.setattr(public_protection_center, "_refresh_panel", refresh)
    run(recommend._open_protection_options(interaction))
    assert events == ["protection"]


def test_timers_rules_reuses_existing_behavior_view(monkeypatch: pytest.MonkeyPatch) -> None:
    interaction = FakeInteraction()
    captured: dict[str, Any] = {}

    async def allow(*args: Any, **kwargs: Any) -> bool:
        return True

    async def add_section(embed: discord.Embed, guild: Any, section: str) -> None:
        captured["section"] = section
        captured["guild"] = guild

    async def edit(interaction_arg: Any, *, embed: discord.Embed, view: discord.ui.View) -> None:
        captured["interaction"] = interaction_arg
        captured["embed"] = embed
        captured["view"] = view

    monkeypatch.setattr(recommend.solid, "_require_setup_permission", allow)
    monkeypatch.setattr(recommend.solid, "_add_saved_setup_section", add_section)
    monkeypatch.setattr(recommend.solid, "_edit_or_followup", edit)
    run(recommend._open_timers_behavior(interaction))
    assert captured["section"] == "behavior"
    assert captured["guild"] is interaction.guild
    assert captured["interaction"] is interaction
    assert captured["embed"].title == "⏱️ Timers & Rules"
    assert isinstance(captured["view"], recommend.solid.BehaviorSettingsView)


def test_unused_plain_manage_duplicate_is_removed():
    text = Path("stoney_verify/commands_ext/public_setup_fresh_choice.py").read_text(encoding="utf-8")
    assert "class PlainManageSetupView" not in text
    assert "Advanced Options" not in text


def test_vague_group_names_are_not_user_facing():
    text = Path("stoney_verify/commands_ext/public_setup_recommend.py").read_text(encoding="utf-8")
    for stale in ("Member Experience", "Core Setup", "Monitoring & Repair", "Danger Zone"):
        assert stale not in text
''', encoding="utf-8")


# ---------------------------------------------------------------------------
# Smaller stale contract updates.
# ---------------------------------------------------------------------------
replacements_by_file: dict[str, tuple[tuple[str, str], ...]] = {
    "tests/test_setup_automatic_review_behavior.py": (
        ('"Fix Next Item"', '"Fix Next Problem"'),
        ('"Test / Launch"', '"Test & Launch"'),
        ('assert "Advanced Options" in labels', 'assert "Other Settings" not in labels'),
        ('assert "Change Setup Type" in labels', 'assert "Change Setup Type" not in labels'),
    ),
    "tests/test_setup_feature_aware_health_static.py": (
        ('Choose Basic Verify or Voice Verify', 'Choose Simple Verify or Voice Verify'),
    ),
    "tests/test_setup_first_run_simple_route_static.py": (
        ('assert "Start / Continue Setup" in home', 'assert "Start Setup" in home\n    assert "More Options" in home'),
        ('assert "Fix Next Item" in guided', 'assert "Set Up This Step" in guided'),
        ('assert "Setup Check" in guided', 'assert "Setup Check" not in guided'),
        ('assert "Change Setup Type" in guided', 'assert "Change Setup Type" not in guided'),
        ('assert "Advanced Options" in guided', 'assert "Advanced Options" not in guided'),
        ('assert "Continue Guided Setup" in custom', 'assert "Continue Setup" in custom'),
        ('assert "Advanced Options" in custom', 'assert "Advanced Options" not in custom\n    assert \'label="Back"\' in custom'),
    ),
    "tests/test_setup_permission_repair_native_route.py": (
        ('"label": "Permission Repair"', '"label": "Fix Channel Permissions"'),
        ('"row": 0,', '"row": 1,'),
        ('"🛠️ **Permission Repair** — preview and repair "\n        "saved setup channel permissions."', '"🛠️ **Fix Channel Permissions** — check and fix "\n        "access to Dank Shield channels."'),
    ),
}
for relative, replacements in replacements_by_file.items():
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    for old, new in replacements:
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Setup-type semantics now use one select instead of a button wall.
# ---------------------------------------------------------------------------
first_screen = ROOT / "tests/test_setup_first_screen_semantics.py"
text = first_screen.read_text(encoding="utf-8")
old_helper_start = text.index("def setup_choice_labels(")
old_helper_end = text.index("def test_new_server_opens_setup_type_before_guided_setup(", old_helper_start)
helper = '''def setup_choice_labels(view: discord.ui.View) -> set[str]:
    result: set[str] = set()
    for child in view.children:
        if isinstance(child, discord.ui.Select):
            result.update(str(option.label) for option in child.options)
    return result


'''
text = text[:old_helper_start] + helper + text[old_helper_end:]
for old, new in (
    ('"Basic Server"', '"Tickets + Server Basics"'),
    ('"Basic Verify"', '"Simple Verify"'),
    ('"Help Desk"', '"Help Desk / Tickets"'),
    ('"Custom"', '"Choose My Own Features"'),
):
    text = text.replace(old, new)
first_screen.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Native front-door contracts: Custom Back returns to setup type, not Home.
# ---------------------------------------------------------------------------
front = ROOT / "tests/test_setup_front_door_native_no_patch.py"
text = front.read_text(encoding="utf-8")
text = replace_named_test(text, "test_custom_home_returns_to_canonical_home", '''def test_custom_back_returns_to_setup_type_choice() -> None:
    body = _owner_source(FRESH, "CustomServiceModeView")
    assert "async def back(" in body
    assert "await recommend._open_choose_setup_type(interaction)" in body
    assert "await recommend._home_edit(interaction)" not in body''')
text = replace_named_test(text, "test_setup_choices_and_guided_routes_remain", '''def test_setup_choices_and_guided_routes_remain() -> None:
    source = _source(FRESH)
    for marker in (
        "class SetupTypeChoiceSelect(",
        "class SetupTypeChoiceView(",
        "class CustomServiceModeView(",
        "Continue Setup",
        "recommend._open_guided_setup(",
        "recommend._open_choose_setup_type(",
        "id_verify_allowed_for_guild",
    ):
        assert marker in source
    assert "Continue Guided Setup" not in source
    assert "recommend._open_manage_setup(" not in source''')
front.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Feature-aware review owns one main action plus Home only.
# ---------------------------------------------------------------------------
health_next = ROOT / "tests/test_setup_health_next_action_guard_retired.py"
text = health_next.read_text(encoding="utf-8")
text = text.replace('        "SetupReviewAdvancedButton",\n', '        "SetupReviewHomeButton",\n')
text = text.replace('    assert "Start / Continue Setup" in body\n    assert "Test / Launch" in body', '    assert "Continue Setup" in body\n    assert "Test & Launch" in body\n    assert "Start / Continue Setup" not in body\n    assert "Test / Launch" not in body')
text = text.replace('    assert "SetupReviewAdvancedButton()" in body\n    assert "SetupReviewChangeTypeButton()" in body\n    assert "SetupReviewHomeButton()" in body', '    assert "SetupReviewHomeButton()" in body\n    assert "SetupReviewAdvancedButton()" not in body\n    assert "SetupReviewChangeTypeButton()" not in body')
health_next.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Single guided front-door static contract.
# ---------------------------------------------------------------------------
single = ROOT / "tests/test_setup_single_guided_front_door_static.py"
text = single.read_text(encoding="utf-8")
text = text.replace('assert "Continue Guided Setup" in custom', 'assert "Continue Setup" in custom')
text = replace_named_test(text, "test_advanced_tools_still_exist", '''def test_advanced_tools_still_exist():
    """Every optional tool remains reachable under literal secondary labels."""
    import ast

    tree = ast.parse(RECOMMEND)
    classes = {
        node.name: ast.get_source_segment(RECOMMEND, node) or ""
        for node in tree.body
        if isinstance(node, ast.ClassDef)
    }

    more = classes["ManageSetupView"]
    for label in (
        "Change Setup Type",
        "Other Settings",
        "Check Setup for Problems",
        "Fix Setup or Start Over",
        "Help",
        "Back Home",
    ):
        assert label in more

    hub = classes["AdvancedSettingsHubView"]
    for label in (
        "Features, Roles & Channels",
        "Tickets",
        "Logs & Safety",
        "Server Design",
    ):
        assert label in hub

    advanced = "\\n".join(
        classes[name]
        for name in (
            "AdvancedCoreSetupView",
            "AdvancedMemberExperienceView",
            "AdvancedMonitoringRepairView",
            "AdvancedAppearanceView",
            "AdvancedDangerZoneView",
        )
    )
    for label in (
        "Turn Features On / Off",
        "Ticket Choices",
        "Spam & Raid Protection",
        "Choose What Gets Logged",
        "Timers & Rules",
        "Server Design",
        "Choose Roles & Channels",
        "Fix Channel Permissions",
        "Fix or Start Over",
        "Back Home",
    ):
        assert label in advanced

    for route in (
        "_open_services",
        "_open_ticket_menu",
        "_open_protection_options",
        "_open_modlog_tracking",
        "_open_timers_behavior",
        "_open_existing_server",
        "_open_permission_repair",
        "_open_recovery_center",
        "_home_edit",
    ):
        assert route in advanced

    assert "class PlainManageSetupView" not in FRESH
    assert "Member Experience" not in RECOMMEND''')
single.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# UX clarity guard: update the two tests that intentionally pin visible copy.
# ---------------------------------------------------------------------------
ux_path = ROOT / "tests/test_setup_ux_clarity_guard_retired.py"
text = ux_path.read_text(encoding="utf-8")
text = replace_named_test(text, "test_native_guided_labels_and_routes_remain", '''def test_native_guided_labels_and_routes_remain() -> None:
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
    assert recovery["recovery"][0] == "Fix or Start Over"''')
text = replace_named_test(text, "test_public_setup_type_picker_remains", '''def test_public_setup_type_picker_remains() -> None:
    assert "class SetupTypeChoiceSelect(discord.ui.Select)" in FRESH
    for label in (
        "Tickets + Server Basics",
        "Simple Verify",
        "Help Desk / Tickets",
        "Voice Verify",
        "Choose My Own Features",
    ):
        assert f'"{label}"' in FRESH''')
ux_path.write_text(text, encoding="utf-8")


# Compile every touched file before the workflow runs pytest.
for relative in (
    "stoney_verify/commands_ext/public_setup_recommend.py",
    "tests/test_setup_advanced_options_behavior.py",
    "tests/test_setup_automatic_review_behavior.py",
    "tests/test_setup_feature_aware_health_static.py",
    "tests/test_setup_first_run_simple_route_static.py",
    "tests/test_setup_first_screen_semantics.py",
    "tests/test_setup_front_door_native_no_patch.py",
    "tests/test_setup_health_next_action_guard_retired.py",
    "tests/test_setup_permission_repair_native_route.py",
    "tests/test_setup_single_guided_front_door_static.py",
    "tests/test_setup_ux_clarity_guard_retired.py",
):
    path = ROOT / relative
    compile(path.read_text(encoding="utf-8"), str(path), "exec")

print("PASS: aligned legacy setup contracts with the approved one-path UX")
