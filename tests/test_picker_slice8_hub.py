"""Picker slice 8: Community Hub pickers on the house picker framework.

Covers routing parity (each migrated picker's pick handler makes the
same state changes / saves the original raw-select callback made),
>25 paging, the source-scan contract, and the Class C member-facing
fit for SessionSelect / MyGroupSelect.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from stoney_verify.commands_ext import public_community_hub as ui

OWNER = 111
GUILD = 222

MIGRATED_OPTION_PICKERS = [
    "CommunityHubSectionSelect",
    "AvailableGameSelect",
    "SessionPreferenceSelect",
    "AtlasTypeSelect",
    "MinecraftEditionSelect",
    "SessionControlSectionSelect",
    "EventSelect",
    "SafetyReportSelect",
    "AnalyticsRangeSelect",
    "ModeSelect",
    "PublicNetworkSelect",
    "HubSettingsSectionSelect",
    "PendingPartnerSelect",
    "ActivePartnerSelect",
    "SessionSelect",
    "MyGroupSelect",
    "StaffSessionSelect",
]
MIGRATED_ENTITY_PICKERS = [
    "MatchSafetyUserSelect",
    "RoleTargetSelect",
    "CategorySelect",
]


def _interaction(user_id: int = OWNER, guild_id: int = GUILD) -> SimpleNamespace:
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        guild_id=guild_id,
        guild=SimpleNamespace(id=guild_id, name="Test Guild"),
        client=SimpleNamespace(get_guild=lambda guild_id: SimpleNamespace(name="Other Guild")),
        response=SimpleNamespace(
            is_done=lambda: False,
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
        message=SimpleNamespace(embeds=[]),
    )


def _session(sid: str = "s1", **overrides):
    row = {
        "id": sid,
        "game_name": "Minecraft",
        "state": "open",
        "capacity": 6,
        "guild_id": GUILD,
    }
    row.update(overrides)
    return row


@pytest.fixture(autouse=True)
def _base_mocks(monkeypatch):
    monkeypatch.setattr(ui, "_defer_update", AsyncMock())
    monkeypatch.setattr(ui, "_edit_private_original", AsyncMock())
    monkeypatch.setattr(ui, "_followup", AsyncMock())
    monkeypatch.setattr(ui, "_private", AsyncMock())


def _edited_view() -> object:
    return ui._edit_private_original.await_args.kwargs["view"]


# ---------------------------------------------------------------- routing parity


def test_section_select_routes_to_chosen_home_section():
    picker = ui.CommunityHubSectionSelect(OWNER, "play")
    asyncio.run(picker.handle_pick(_interaction(), "explore"))
    view = _edited_view()
    assert isinstance(view, ui.CommunityHubView)
    assert view.section == "explore"
    assert "Events & Discovery" in ui._edit_private_original.await_args.kwargs["embed"].title


def test_available_game_select_reopens_find_players_for_game(monkeypatch):
    monkeypatch.setattr(ui.hub, "list_available_users", AsyncMock(return_value=[]))
    monkeypatch.setattr(ui.hub, "list_active_sessions", AsyncMock(return_value=[]))
    monkeypatch.setattr(ui.hub, "availability_summary", AsyncMock(return_value={"top_games": []}))
    picker = ui.AvailableGameSelect(OWNER, {"top_games": [{"name": "Minecraft", "count": 2}]})
    asyncio.run(picker.handle_pick(_interaction(), "Minecraft"))
    view = _edited_view()
    assert isinstance(view, ui.FindPlayersView)
    assert view.selected_game == "Minecraft"


def test_session_preference_select_applies_style_filter(monkeypatch):
    opener = AsyncMock()
    monkeypatch.setattr(ui, "_open_find_players_source", opener)
    picker = ui.SessionPreferenceSelect(OWNER, partner=False)
    asyncio.run(picker.handle_pick(_interaction(), "style:casual"))
    assert opener.await_args.kwargs["play_style"] == "casual"
    assert opener.await_args.kwargs["partner"] is False


def test_session_preference_select_pages_forward(monkeypatch):
    opener = AsyncMock()
    monkeypatch.setattr(ui, "_open_find_players_source", opener)
    picker = ui.SessionPreferenceSelect(OWNER, partner=True, page=1, has_next=True)
    asyncio.run(picker.handle_pick(_interaction(), "page:next"))
    assert opener.await_args.kwargs["page"] == 2
    assert opener.await_args.kwargs["partner"] is True


def test_atlas_type_select_maps_all_to_unfiltered(monkeypatch):
    opener = AsyncMock()
    monkeypatch.setattr(ui, "_open_atlas_groups", opener)
    picker = ui.AtlasTypeSelect(OWNER, game="Minecraft", region="", ready=False, group_type="clan")
    asyncio.run(picker.handle_pick(_interaction(), "all"))
    assert opener.await_args.kwargs["group_type"] == ""
    asyncio.run(picker.handle_pick(_interaction(), "clan"))
    assert opener.await_args.kwargs["group_type"] == "clan"


def test_minecraft_edition_select_maps_all_sentinel(monkeypatch):
    opener = AsyncMock()
    monkeypatch.setattr(ui, "_open_minecraft_players", opener)
    picker = ui.MinecraftEditionSelect(OWNER, "")
    asyncio.run(picker.handle_pick(_interaction(), "ALL"))
    assert opener.await_args.kwargs["edition"] == ""
    asyncio.run(picker.handle_pick(_interaction(), "JAVA"))
    assert opener.await_args.kwargs["edition"] == "JAVA"


def test_event_select_opens_event_detail(monkeypatch):
    monkeypatch.setattr(ui.hub, "list_event_attendees", AsyncMock(return_value=[]))
    events = [{"id": "e1", "title": "Game Night", "game_name": "Minecraft", "starts_at": None}]
    picker = ui.EventSelect(OWNER, events)
    asyncio.run(picker.handle_pick(_interaction(), "e1"))
    view = _edited_view()
    assert isinstance(view, ui.EventDetailView)
    assert view.event_id == "e1"


def test_safety_report_select_requires_staff_then_opens_detail(monkeypatch):
    reports = [{"id": "r1", "reason": "spam", "state": "open", "reporter_user_id": "5"}]
    picker = ui.SafetyReportSelect(OWNER, reports)
    monkeypatch.setattr(ui, "_staff_authorized", lambda interaction: False)
    asyncio.run(picker.handle_pick(_interaction(), "r1"))
    ui._private.assert_awaited()
    ui._edit_private_original.assert_not_awaited()
    ui._private.reset_mock()
    monkeypatch.setattr(ui, "_staff_authorized", lambda interaction: True)
    asyncio.run(picker.handle_pick(_interaction(), "r1"))
    view = _edited_view()
    assert isinstance(view, ui.SafetyReportDetailView)
    assert view.report_id == "r1"


def test_analytics_range_select_reloads_summary(monkeypatch):
    summary = AsyncMock(return_value={"days": 30})
    monkeypatch.setattr(ui.hub, "analytics_summary", summary)
    picker = ui.AnalyticsRangeSelect(OWNER, 7)
    asyncio.run(picker.handle_pick(_interaction(), "30"))
    summary.assert_awaited_once_with(GUILD, days=30)
    view = _edited_view()
    assert isinstance(view, ui.AnalyticsView)


def test_mode_select_saves_mode(monkeypatch):
    update = AsyncMock(return_value={"mode": "smart"})
    monkeypatch.setattr(ui.hub, "update_settings", update)
    runtime = SimpleNamespace(invalidate_settings=MagicMock())
    monkeypatch.setattr(ui, "ensure_community_hub_runtime", lambda client: runtime)
    picker = ui.ModeSelect(OWNER, "minimal")
    asyncio.run(picker.handle_pick(_interaction(), "smart"))
    assert update.await_args.args[1] == {"mode": "smart"}
    runtime.invalidate_settings.assert_called_once_with(GUILD)
    assert isinstance(_edited_view(), ui.HubSettingsView)


def test_hub_settings_section_select_switches_section(monkeypatch):
    monkeypatch.setattr(ui.hub, "get_settings", AsyncMock(return_value={"mode": "minimal"}))
    picker = ui.HubSettingsSectionSelect(OWNER, "general")
    asyncio.run(picker.handle_pick(_interaction(), "rooms"))
    view = _edited_view()
    assert isinstance(view, ui.HubSettingsView)
    assert view.section == "rooms"


def test_session_control_section_select_switches_section(monkeypatch):
    switch = AsyncMock()
    monkeypatch.setattr(ui.SessionControlView, "_switch_section", switch)
    picker = ui.SessionControlSectionSelect(OWNER, "s1", "session")
    asyncio.run(picker.handle_pick(_interaction(), "people"))
    switch.assert_awaited_once()
    assert switch.await_args.args[1] == "people"


def test_public_network_select_denies_revoked_listing(monkeypatch):
    groups = [{"game": "Minecraft", "community": "The 420 Lobby", "capacity": 6,
               "join_url": "https://discord.gg/goodcode"}]
    monkeypatch.setattr(ui.hub, "list_public_lfg_directory", AsyncMock(return_value=[]))
    picker = ui.PublicNetworkSelect(OWNER, groups, "mc")
    asyncio.run(picker.handle_pick(_interaction(), "0"))
    ui._followup.assert_awaited_once()
    ui._edit_private_original.assert_not_awaited()


def test_public_network_select_opens_detail_for_live_listing(monkeypatch):
    from stoney_verify.community_public_lfg import sanitize_directory

    raw = [{"game_name": "Minecraft", "capacity": 6,
            "invite_url": "https://discord.gg/goodcode", "community_name": "The 420 Lobby"}]
    groups = sanitize_directory(raw, game="")
    monkeypatch.setattr(ui.hub, "list_public_lfg_directory", AsyncMock(return_value=raw))
    picker = ui.PublicNetworkSelect(OWNER, groups, "")
    asyncio.run(picker.handle_pick(_interaction(), "0"))
    assert isinstance(_edited_view(), ui.PublicNetworkDetailView)


def test_pending_partner_select_opens_decision(monkeypatch):
    bot = MagicMock()
    bot.get_guild = MagicMock(return_value=SimpleNamespace(name="Other Guild"))
    links = [{"id": "l1", "state": "pending", "guild_a_id": "1", "guild_b_id": "2",
              "requested_by_guild_id": "2"}]
    picker = ui.PendingPartnerSelect(OWNER, 1, links, bot)
    asyncio.run(picker.handle_pick(_interaction(), "l1"))
    view = _edited_view()
    assert isinstance(view, ui.PendingPartnerDecisionView)
    assert view.link_id == "l1"


def test_active_partner_select_opens_decision(monkeypatch):
    bot = MagicMock()
    bot.get_guild = MagicMock(return_value=SimpleNamespace(name="Other Guild"))
    links = [{"id": "l2", "state": "active", "guild_a_id": "1", "guild_b_id": "2",
              "requested_by_guild_id": "1"}]
    picker = ui.ActivePartnerSelect(OWNER, 1, links, bot)
    asyncio.run(picker.handle_pick(_interaction(), "l2"))
    view = _edited_view()
    assert isinstance(view, ui.ActivePartnerDecisionView)
    assert view.link_id == "l2"


def test_staff_session_select_opens_session_controls(monkeypatch):
    monkeypatch.setattr(ui.hub, "get_session", AsyncMock(return_value=_session()))
    monkeypatch.setattr(ui.hub, "list_session_members", AsyncMock(return_value=[]))
    picker = ui.StaffSessionSelect(OWNER, [_session()])
    assert picker.allow_anyone is False
    asyncio.run(picker.handle_pick(_interaction(), "s1"))
    view = _edited_view()
    assert isinstance(view, ui.SessionControlView)
    assert view.session_id == "s1"


# ---------------------------------------------------------------- entity pickers (Class A)


def test_match_safety_user_select_toggles_block(monkeypatch):
    set_block = AsyncMock()
    monkeypatch.setattr(ui.hub, "set_match_block", set_block)
    monkeypatch.setattr(ui.hub, "list_match_blocks", AsyncMock(return_value=["999"]))
    picker = ui.MatchSafetyUserSelect(OWNER, [])
    asyncio.run(picker._on_pick(_interaction(), SimpleNamespace(id=999)))
    set_block.assert_awaited_once_with(GUILD, OWNER, 999, True)
    assert isinstance(_edited_view(), ui.MatchSafetyView)


def test_match_safety_user_select_rejects_self_block(monkeypatch):
    set_block = AsyncMock()
    monkeypatch.setattr(ui.hub, "set_match_block", set_block)
    picker = ui.MatchSafetyUserSelect(OWNER, [])
    asyncio.run(picker._on_pick(_interaction(), SimpleNamespace(id=OWNER)))
    ui._private.assert_awaited_once()
    set_block.assert_not_awaited()


def test_role_target_select_assigns_role_via_mutation_queue(monkeypatch):
    monkeypatch.setattr(ui.hub, "list_session_members", AsyncMock(return_value=[{"user_id": 999}]))
    run_mutation = AsyncMock(return_value=("succeeded", _session()))
    monkeypatch.setattr(ui, "_run_session_mutation", run_mutation)
    monkeypatch.setattr(ui, "_operation_feedback", AsyncMock(return_value=True))
    runtime = SimpleNamespace(refresh_session_card=AsyncMock())
    monkeypatch.setattr(ui, "ensure_community_hub_runtime", lambda client: runtime)
    picker = ui.RoleTargetSelect(OWNER, "s1", "host")
    asyncio.run(picker._on_pick(_interaction(), SimpleNamespace(id=999)))
    assert run_mutation.await_args.kwargs["action"] == "assign_host"
    assert isinstance(_edited_view(), ui.SessionControlView)


def test_category_select_saves_parent_category(monkeypatch):
    update = AsyncMock(return_value={})
    monkeypatch.setattr(ui.hub, "update_settings", update)
    runtime = SimpleNamespace(invalidate_settings=MagicMock())
    monkeypatch.setattr(ui, "ensure_community_hub_runtime", lambda client: runtime)
    picker = ui.CategorySelect(OWNER)
    asyncio.run(picker._on_pick(_interaction(), SimpleNamespace(id=555)))
    assert update.await_args.args[1] == {"parent_category_id": "555"}


# ---------------------------------------------------------------- paging


def test_session_select_pages_beyond_25():
    sessions = [_session(f"s{i}") for i in range(30)]
    first = ui.SessionSelect(OWNER, sessions, partner=False)
    assert first.pages == 2
    assert len(first.choices) == 25
    second = first._for_page(1)
    assert second.custom_id != first.custom_id
    assert second.custom_id.endswith(":1")
    reachable = {choice.value for choice in first.choices} | {choice.value for choice in second.choices}
    assert reachable == {f"s{i}" for i in range(30)}


def test_public_network_select_pages_beyond_25():
    groups = [{"game": f"G{i}", "community": "C", "capacity": 6,
               "join_url": "https://discord.gg/x"} for i in range(30)]
    picker = ui.PublicNetworkSelect(OWNER, groups, "")
    assert picker.pages == 2
    assert len(picker.options) == 25
    page_two = picker._for_page(1)
    assert page_two.custom_id != picker.custom_id
    assert {choice.value for choice in page_two.choices} == {"25", "26", "27", "28", "29"}


def test_event_select_pages_beyond_25():
    events = [{"id": f"e{i}", "title": f"Event {i}", "game_name": "Minecraft"} for i in range(30)]
    picker = ui.EventSelect(OWNER, events)
    assert picker.pages == 2
    reachable = {c.value for c in picker.choices} | {c.value for c in picker._for_page(1).choices}
    assert reachable == {f"e{i}" for i in range(30)}


# ---------------------------------------------------------------- Class C fit


def test_session_select_is_allow_anyone_and_routes_other_users(monkeypatch):
    monkeypatch.setattr(ui.hub, "get_session", AsyncMock(return_value=_session()))
    monkeypatch.setattr(ui.hub, "list_session_members", AsyncMock(return_value=[]))
    picker = ui.SessionSelect(OWNER, [_session()], partner=False)
    assert picker.allow_anyone is True
    other = _interaction(user_id=999)
    assert asyncio.run(picker.interaction_check(other)) is True
    asyncio.run(picker.handle_pick(other, "s1"))
    view = _edited_view()
    assert isinstance(view, ui.SessionDetailView)
    assert view.session_id == "s1"


def test_session_select_partner_pick_revalidates_discovery(monkeypatch):
    approved = AsyncMock(return_value=[])
    monkeypatch.setattr(ui.hub, "list_partner_discovery_sessions", approved)
    picker = ui.SessionSelect(OWNER, [_session(guild_id=555)], partner=True)
    asyncio.run(picker.handle_pick(_interaction(user_id=999), "s1"))
    approved.assert_awaited_once_with(GUILD, limit=100)
    assert "no longer publicly available" in ui._edit_private_original.await_args.kwargs["content"]


def test_my_group_select_is_allow_anyone_and_routes_other_users(monkeypatch):
    monkeypatch.setattr(ui.hub, "get_session", AsyncMock(return_value=_session()))
    monkeypatch.setattr(ui.hub, "list_session_members", AsyncMock(return_value=[]))
    picker = ui.MyGroupSelect(OWNER, [_session()])
    assert picker.allow_anyone is True
    assert picker.custom_id == "dank:hub:mine:select:v1"
    other = _interaction(user_id=999)
    assert asyncio.run(picker.interaction_check(other)) is True
    asyncio.run(picker.handle_pick(other, "s1"))
    view = _edited_view()
    assert isinstance(view, ui.SessionDetailView)
    assert view.return_kind == "mine"


# ---------------------------------------------------------------- host openers


def test_host_views_use_openers_not_embedded_selects():
    sessions = [_session()]
    hosts = [
        ui.CommunityHubView(OWNER),
        ui.FindPlayersView(OWNER, sessions, partner=False),
        ui.EventsView(OWNER, [{"id": "e1", "title": "T", "game_name": "G"}]),
        ui.MyGroupsView(OWNER, sessions),
        ui.StaffSessionsView(OWNER, sessions),
        ui.AnalyticsView(OWNER, days=7),
    ]
    for host in hosts:
        assert not any(isinstance(child, discord.ui.Select) for child in host.children), type(host).__name__
        assert len(host.children) <= 25
    find_ids = {getattr(child, "custom_id", "") for child in hosts[1].children}
    assert "dank:hub:find:select:open:v1" in find_ids
    assert "dank:hub:find:preferences:open:v1" in find_ids


# ---------------------------------------------------------------- source scan


def test_no_migrated_picker_subclasses_raw_discord_selects():
    source = Path(ui.__file__).read_text(encoding="utf-8")
    for name in MIGRATED_OPTION_PICKERS + MIGRATED_ENTITY_PICKERS:
        match = re.search(rf"class {name}\(([^)]*)\)", source)
        assert match is not None, name
        bases = match.group(1)
        assert "discord.ui.Select" not in bases, name
        assert "discord.ui.UserSelect" not in bases, name
        assert "discord.ui.ChannelSelect" not in bases, name
    assert not re.search(r"class \w+\(discord\.ui\.(Select|UserSelect|ChannelSelect)\)", source)
    for name in MIGRATED_OPTION_PICKERS:
        cls = getattr(ui, name)
        assert issubclass(cls, ui.DankPickerView), name
    for name in ("MatchSafetyUserSelect", "RoleTargetSelect"):
        assert issubclass(getattr(ui, name), ui.DankUserSelect), name
    assert issubclass(ui.CategorySelect, ui.DankChannelSelect)
