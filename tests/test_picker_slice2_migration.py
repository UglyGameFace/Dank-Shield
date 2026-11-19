"""Slice 2 picker migration: /dank home, Help, and the section pickers in
Protection Center, Member Setup and the Action Center use the house
picker framework.

Proves each migrated picker keeps the same options (labels, order,
defaults) and routes into the same destination logic the raw Discord
selects used, that long lists page via chunk_choices instead of being
silently cut at 25, and that no migrated raw select class remains.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord

from stoney_verify import navigation_registry as registry
from stoney_verify.action_center_service import ActionSnapshot
from stoney_verify.commands_ext import public_action_center as actions
from stoney_verify.commands_ext import public_command_surface_v2 as nav
from stoney_verify.commands_ext import public_member_setup as setup
from stoney_verify.commands_ext import public_protection_center as protection
from stoney_verify.help_guides import HELP_GUIDES
from stoney_verify.ui.picker import DankPickerView

ROOT = Path(__file__).resolve().parents[1]
OWNER = SimpleNamespace(id=42)


def _interaction(**kwargs):
    return SimpleNamespace(user=OWNER, **kwargs)


def _opener(view: discord.ui.View, custom_id: str):
    return next(
        item for item in view.children
        if str(getattr(item, "custom_id", "")) == custom_id
    )


def _all_option_values(picker) -> list[str]:
    values: list[str] = []
    for page in range(picker.pages):
        select = picker._for_page(page).children[0]
        values.extend(str(option.value) for option in select.options)
    return values


# --- /dank home section picker -------------------------------------------


def test_home_section_picker_lists_every_category_in_order():
    home = nav.CompactDankHomeView(42)
    opener = _opener(home, "dank:home:sections:open:v1")
    picker = opener._build(_interaction())
    assert isinstance(picker, DankPickerView)
    select = picker.children[0]
    assert select.custom_id == "dank:home:sections:v1"
    assert str(select.placeholder) == "🧭 Choose a section…"
    assert [str(option.label) for option in select.options] == [
        category.label for category in registry.CATEGORIES
    ]
    assert [str(option.value) for option in select.options] == [
        category.key for category in registry.CATEGORIES
    ]


def test_home_section_pick_routes_to_open_category(monkeypatch):
    seen = []

    async def fake_open_category(interaction, category_key):
        seen.append(category_key)

    monkeypatch.setattr(nav, "_open_category", fake_open_category)
    picker = nav._section_picker_view(_interaction(), back="home")
    asyncio.run(picker.handle_pick(_interaction(), "community"))
    assert seen == ["community"]


def test_home_and_directory_views_have_no_raw_select():
    for view in (nav.CompactDankHomeView(42), nav.FeatureDirectoryView(42)):
        assert not any(isinstance(item, discord.ui.Select) for item in view.children)
        _opener(view, "dank:home:sections:open:v1")


def test_navigation_picker_pages_long_lists_without_truncation():
    choices = [
        nav.DankChoice(label=f"Item {index}", value=f"item-{index}")
        for index in range(60)
    ]
    picker = nav._NavigationPickerView(
        42, choices=choices, on_pick=lambda i, v: asyncio.sleep(0),
        custom_id="dank:test:long:v1", placeholder="Pick one",
    )
    assert picker.pages == 3
    assert picker.children[0].custom_id == "dank:test:long:v1:0"
    assert _all_option_values(picker) == [f"item-{index}" for index in range(60)]
    assert any(
        getattr(item, "custom_id", "") == "dank:navigation:picker:next"
        for item in picker.children
    )


# --- Help pickers ----------------------------------------------------------


def test_help_topic_picker_routes_to_topic_panel(monkeypatch):
    seen = []

    async def fake_replace(interaction, **kwargs):
        seen.append(kwargs)

    monkeypatch.setattr(nav, "_replace_panel", fake_replace)
    picker = nav._help_topic_picker_view(_interaction(), back="help_home")
    select = picker.children[0]
    assert select.custom_id == "dank:help:topics:v2"
    assert [str(option.value) for option in select.options] == [
        key for key, _label, _summary, _features in nav._HELP_TOPICS
    ]
    asyncio.run(picker.handle_pick(_interaction(), "safety"))
    assert seen[-1]["embed"].title.endswith("Members & safety")
    assert isinstance(seen[-1]["view"], nav.HelpTopicView)


def test_help_category_picker_routes_to_category_panel(monkeypatch):
    seen = []

    async def fake_replace(interaction, **kwargs):
        seen.append(kwargs)

    monkeypatch.setattr(nav, "_replace_panel", fake_replace)
    view = nav.HelpCategoryView(42, "design")
    opener = _opener(view, "dank:help:category:open:v1")
    picker = opener._build(_interaction())
    asyncio.run(picker.handle_pick(_interaction(), "tickets"))
    assert isinstance(seen[-1]["view"], nav.HelpCategoryView)
    tickets = registry.category_by_key("tickets")
    assert tickets is not None and tickets.label in str(seen[-1]["embed"].title)


def test_help_feature_picker_lists_category_features_and_routes_to_guide(monkeypatch):
    seen = []

    async def fake_show_guide(interaction, feature_key, *, category_key=""):
        seen.append((feature_key, category_key))

    monkeypatch.setattr(nav, "_show_help_guide", fake_show_guide)
    for category in registry.CATEGORIES:
        view = nav.HelpCategoryView(42, category.key)
        opener = _opener(view, "dank:help:feature-select:open:v1")
        picker = opener._build(_interaction())
        select = picker.children[0]
        assert select.custom_id == "dank:help:feature-select:v3"
        assert {str(option.value) for option in select.options} == {
            feature.key for feature in registry.features_for_category(category.key)
        }
        assert all(
            str(option.description) == HELP_GUIDES[str(option.value)].purpose[:100]
            for option in select.options
        )
    picker = nav._help_feature_picker_view(_interaction(), "community")
    asyncio.run(picker.handle_pick(_interaction(), "tickets"))
    assert seen == [("tickets", "community")]


def test_help_search_picker_pages_results_without_truncation(monkeypatch):
    seen = []

    async def fake_show_guide(interaction, feature_key, **_kwargs):
        seen.append(feature_key)

    monkeypatch.setattr(nav, "_show_help_guide", fake_show_guide)
    matches = list(registry.FEATURES[:30])
    view = nav.HelpSearchResultsView(42, matches, query="test")
    opener = _opener(view, "dank:help:search-results:open:v1")
    picker = opener._build(_interaction())
    assert picker.pages == 2
    assert _all_option_values(picker) == [feature.key for feature in matches]
    asyncio.run(picker.handle_pick(_interaction(), matches[27].key))
    assert seen == [matches[27].key]


def test_help_views_have_no_raw_selects():
    views = [
        nav.CompactHelpView(42),
        nav.HelpTopicView(42, nav._HELP_TOPICS[0][0]),
        nav.HelpCategoryView(42, registry.CATEGORIES[0].key),
        nav.HelpSearchResultsView(42, list(registry.FEATURES[:3]), query="x"),
    ]
    for view in views:
        assert not any(isinstance(item, discord.ui.Select) for item in view.children)


# --- Protection Center section picker --------------------------------------


def test_protection_section_picker_routes_to_refresh_panel(monkeypatch):
    events = []

    async def allowed(_interaction):
        return True

    async def refresh(_interaction, *, section=None, **_kwargs):
        events.append(section)

    monkeypatch.setattr(protection, "_require_setup_permission", allowed)
    monkeypatch.setattr(protection, "_refresh_panel", refresh)
    picker = protection._ProtectionSectionPickerView(author_id=1, current="overview")
    select = picker.children[0]
    assert select.custom_id == "dank_protection:section:v2"
    assert [str(option.value) for option in select.options] == [
        key for key, _label, _hint in protection._PROTECTION_SECTIONS
    ]
    assert [bool(option.default) for option in select.options] == [
        key == "overview" for key, _label, _hint in protection._PROTECTION_SECTIONS
    ]
    asyncio.run(picker.handle_pick(_interaction(), "antinuke"))
    assert events == ["antinuke"]


def test_protection_view_opens_picker_and_degraded_disables_it():
    view = protection.ProtectionCenterView(author_id=1, cfg={}, spam={}, section="overview")
    _opener(view, "dank_protection:section:open:v1")
    assert not any(isinstance(item, discord.ui.Select) for item in view.children)
    degraded = protection.ProtectionCenterView(
        author_id=1, cfg={}, spam={}, section="overview", degraded=True,
    )
    opener = _opener(degraded, "dank_protection:section:open:v1")
    assert opener.disabled is True


# --- Member Setup section picker -------------------------------------------


def test_member_setup_section_picker_routes_to_admin_panel(monkeypatch):
    events = []
    sections = []

    async def allowed(*_args, **_kwargs):
        return True

    async def pending(_interaction):
        return None

    async def load_setup(*_args, **_kwargs):
        return {}

    def fake_embed(*_args, section="roles"):
        sections.append(section)
        return "summary"

    async def replace(_interaction, **kwargs):
        events.append(kwargs["view"])

    monkeypatch.setattr(setup, "_staff_authorized", allowed)
    monkeypatch.setattr(setup, "_defer_panel_update", pending)
    monkeypatch.setattr(setup, "load_guild_setup_state", load_setup)
    monkeypatch.setattr(setup, "_admin_embed", fake_embed)
    monkeypatch.setattr(setup, "_replace", replace)
    picker = setup._MemberSetupSectionPickerView(123, "roles")
    select = picker.children[0]
    assert select.custom_id == "dank:member_setup:admin:section:v2"
    assert [str(option.value) for option in select.options] == [
        "roles", "categories", "publish", "gate",
    ]
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=123), guild_id=77, guild=SimpleNamespace(id=77),
    )
    asyncio.run(picker.handle_pick(interaction, "gate"))
    assert isinstance(events[-1], setup.MemberSetupAdminView)
    assert events[-1].section == "gate"
    assert sections == ["gate"]


def test_member_setup_admin_view_opens_picker():
    view = setup.MemberSetupAdminView(123, section="roles")
    _opener(view, "dank:member_setup:admin:section:open:v1")
    assert not any(isinstance(item, discord.ui.Select) for item in view.children)


# --- Action Center section picker ------------------------------------------


def _snapshot() -> ActionSnapshot:
    return ActionSnapshot(
        guild_id=42, user_id=7, items=(),
        checked_sources=("verification", "member_setup"),
        unavailable_sources=(),
    )


def test_action_section_picker_keeps_options_and_routes(monkeypatch):
    seen = []

    async def fake_open(interaction, *, section="my"):
        seen.append(section)

    monkeypatch.setattr(actions, "open_action_center", fake_open)
    picker = actions._ActionSectionPickerView(7, 42, "staff")
    select = picker.children[0]
    assert select.custom_id == "dank:actions:section:v2"
    assert [(str(o.label), str(o.value), bool(o.default)) for o in select.options] == [
        ("My Actions", "my", False), ("Staff Tools", "staff", True),
    ]
    asyncio.run(picker.handle_pick(_interaction(), "my"))
    assert seen == ["my"]


def test_action_section_picker_denies_non_owner():
    sent = []

    class _Response:
        @staticmethod
        def is_done() -> bool:
            return True

    async def fake_send(**kwargs):
        sent.append(kwargs.get("content"))

    picker = actions._ActionSectionPickerView(7, 42, "my")
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=99), guild_id=42,
        response=_Response(), followup=SimpleNamespace(send=fake_send),
    )
    assert asyncio.run(picker.interaction_check(interaction)) is False
    assert sent == ["Open your own staff Action Center."]


def test_action_center_views_open_picker_only_for_staff():
    staff = actions.ActionCenterView(7, _snapshot(), is_staff=True, section="my")
    _opener(staff, "dank:actions:section:open:v1")
    assert not any(isinstance(item, discord.ui.Select) for item in staff.children)
    regular = actions.ActionCenterView(7, _snapshot(), is_staff=False, section="my")
    assert not any(
        isinstance(item, actions._ActionSectionOpenButton) for item in regular.children
    )


# --- No migrated raw selects remain -----------------------------------------


def test_slice2_files_have_no_migrated_raw_select_classes():
    migrated = (
        "_HomeSectionSelect", "_HelpTopicSelect", "_HelpCategorySelect",
        "_HelpFeatureSelect", "_HelpSearchResultSelect",
        "_ActionSectionSelect", "_MemberSetupSectionSelect",
        "_ProtectionSectionSelect",
    )
    files = (
        "stoney_verify/commands_ext/public_command_surface_v2.py",
        "stoney_verify/commands_ext/public_action_center.py",
        "stoney_verify/commands_ext/public_member_setup.py",
        "stoney_verify/commands_ext/public_protection_center.py",
    )
    for relative in files:
        source = (ROOT / relative).read_text(encoding="utf-8")
        for name in migrated:
            assert f"class {name}(" not in source, f"{name} still defined in {relative}"
            assert f"add_item({name}" not in source, f"{name} still used in {relative}"
    surface = (ROOT / files[0]).read_text(encoding="utf-8")
    assert "class _NavigationPickerView(DankPickerView):" in surface
