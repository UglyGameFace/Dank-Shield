from __future__ import annotations

from types import SimpleNamespace

import discord

from stoney_verify import permission_repair, permission_repair_core, permission_repair_ui
from stoney_verify.ui import DankPickerView


def _component(view: discord.ui.View, custom_id: str):
    matches = [
        child
        for child in view.children
        if str(getattr(child, "custom_id", "") or "") == custom_id
    ]
    assert len(matches) == 1
    return matches[0]


def test_fix_access_target_control_is_dedicated_browser_button() -> None:
    state = permission_repair.PermissionRepairState(
        guild=SimpleNamespace(me=None),
        actor_id=123,
    )
    view = permission_repair.TargetPermissionRepairView(state)
    target = _component(view, "dank_permission_repair:target")

    assert isinstance(target, discord.ui.Button)
    assert "Choose" in str(target.label)
    assert not any(isinstance(child, discord.ui.ChannelSelect) for child in view.children)
    assert issubclass(permission_repair.TargetChannelPickerView, DankPickerView)


def test_canonical_wrapper_binds_core_return_points_to_dedicated_ui() -> None:
    assert permission_repair.TargetPermissionRepairView is permission_repair_ui.TargetPermissionRepairView
    assert permission_repair.open_target_permission_repair is permission_repair_ui.open_target_permission_repair
    assert permission_repair_core.TargetPermissionRepairView is permission_repair_ui.TargetPermissionRepairView
    assert permission_repair_core.open_target_permission_repair is permission_repair_ui.open_target_permission_repair


def test_target_browser_pages_bot_owned_candidates_without_discord_entity_select(monkeypatch) -> None:
    candidates = [
        permission_repair_ui.TargetCandidate(
            channel_id=index + 1,
            label=f"channel-{index + 1}",
            description="Text • Text channel",
            emoji="💬",
        )
        for index in range(61)
    ]
    monkeypatch.setattr(
        permission_repair_ui,
        "_channel_candidates",
        lambda *_args, **_kwargs: candidates,
    )

    state = permission_repair.PermissionRepairState(
        guild=SimpleNamespace(me=None),
        actor_id=77,
    )
    actor = SimpleNamespace(id=77)

    first = permission_repair_ui.TargetChannelPickerView(state, actor=actor, page=0)
    middle = permission_repair_ui.TargetChannelPickerView(state, actor=actor, page=1)
    last = permission_repair_ui.TargetChannelPickerView(state, actor=actor, page=2)

    assert first.page_count == 3
    assert len(first.choices) == 25
    assert len(middle.choices) == 25
    assert len(last.choices) == 11
    assert not any(isinstance(child, discord.ui.ChannelSelect) for child in first.children)
    assert any(str(getattr(child, "label", "")) == "Search" for child in first.children)


def test_target_catalog_filters_hidden_channels_and_searches_name_id(monkeypatch) -> None:
    class FakeChannel:
        def __init__(self, channel_id: int, name: str, *, visible: bool) -> None:
            self.id = channel_id
            self.name = name
            self.type = discord.ChannelType.text
            self.category = None
            self.visible = visible

        def permissions_for(self, _actor):
            return SimpleNamespace(
                view_channel=self.visible,
                manage_channels=False,
            )

    monkeypatch.setattr(permission_repair_core, "_target_supported", lambda _channel: True)

    visible = FakeChannel(101, "mod-log", visible=True)
    hidden = FakeChannel(202, "owner-secret", visible=False)
    tickets = FakeChannel(303, "ticket-help", visible=True)
    guild = SimpleNamespace(
        channels=[visible, hidden, tickets],
        owner_id=999,
    )
    actor = SimpleNamespace(
        id=44,
        guild_permissions=SimpleNamespace(administrator=False),
    )
    state = permission_repair.PermissionRepairState(guild=guild, actor_id=44)

    all_candidates = permission_repair_ui._channel_candidates(state, actor)
    assert [item.channel_id for item in all_candidates] == [101, 303]

    name_search = permission_repair_ui._channel_candidates(state, actor, query="ticket")
    assert [item.channel_id for item in name_search] == [303]

    id_search = permission_repair_ui._channel_candidates(state, actor, query="<#101>")
    assert [item.channel_id for item in id_search] == [101]


def test_dedicated_picker_has_explicit_error_handlers() -> None:
    assert "on_error" in permission_repair_ui.TargetChannelPickerView.__dict__
    assert "on_error" in permission_repair_ui.TargetPermissionRepairView.__dict__
