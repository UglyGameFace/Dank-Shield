from __future__ import annotations

from types import SimpleNamespace

import discord

from stoney_verify import permission_repair, permission_repair_core, permission_repair_ui
from stoney_verify.ui import DankGuildResourceBrowserView, DankPickerView
from stoney_verify.ui import resource_browser


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
    assert issubclass(permission_repair.TargetChannelPickerView, DankGuildResourceBrowserView)
    assert issubclass(permission_repair.TargetChannelPickerView, DankPickerView)


def test_canonical_wrapper_binds_core_return_points_to_dedicated_ui() -> None:
    assert permission_repair.TargetPermissionRepairView is permission_repair_ui.TargetPermissionRepairView
    assert permission_repair.open_target_permission_repair is permission_repair_ui.open_target_permission_repair
    assert permission_repair_core.TargetPermissionRepairView is permission_repair_ui.TargetPermissionRepairView
    assert permission_repair_core.open_target_permission_repair is permission_repair_ui.open_target_permission_repair


class FakeChannel:
    def __init__(self, channel_id: int, name: str, *, visible: bool = True) -> None:
        self.id = channel_id
        self.name = name
        self.mention = f"<#{channel_id}>"
        self.type = discord.ChannelType.text
        self.category = None
        self.visible = visible

    def permissions_for(self, _actor):
        return SimpleNamespace(
            view_channel=self.visible,
            manage_channels=False,
        )


def _state_and_actor(channels, *, actor_id: int = 44):
    guild = SimpleNamespace(
        id=9876,
        channels=list(channels),
        owner_id=999,
        get_channel=lambda channel_id: next(
            (item for item in channels if int(item.id) == int(channel_id)),
            None,
        ),
    )
    actor = SimpleNamespace(
        id=actor_id,
        guild_permissions=SimpleNamespace(administrator=False),
    )
    return permission_repair.PermissionRepairState(guild=guild, actor_id=actor_id), actor


def test_target_browser_pages_bot_owned_candidates_without_discord_entity_select(monkeypatch) -> None:
    monkeypatch.setattr(permission_repair_core, "_target_supported", lambda _channel: True)
    channels = [FakeChannel(index + 1, f"channel-{index + 1}") for index in range(61)]
    state, actor = _state_and_actor(channels, actor_id=77)

    first = permission_repair_ui.TargetChannelPickerView(state, actor=actor, page=0)
    middle = permission_repair_ui.TargetChannelPickerView(state, actor=actor, page=1)
    last = permission_repair_ui.TargetChannelPickerView(state, actor=actor, page=2)

    assert first.page_count == 3
    assert len(first.choices) == 25
    assert len(middle.choices) == 25
    assert len(last.choices) == 11
    assert not any(isinstance(child, discord.ui.ChannelSelect) for child in first.children)
    assert any(str(getattr(child, "label", "")) == "Search" for child in first.children)


def test_target_catalog_filters_hidden_channels_and_searches_semantic_name_id(monkeypatch) -> None:
    monkeypatch.setattr(permission_repair_core, "_target_supported", lambda _channel: True)

    visible = FakeChannel(101, "mod-log", visible=True)
    hidden = FakeChannel(202, "owner-secret", visible=False)
    tickets = FakeChannel(303, "「🎫」𝕋𝕚𝕔𝕜𝕖𝕥-ℍ𝕖𝕝𝕡", visible=True)
    state, actor = _state_and_actor([visible, hidden, tickets])

    all_items = permission_repair_ui.TargetChannelPickerView(state, actor=actor)
    assert [item.resource_id for item in all_items.candidates] == [101, 303]

    name_search = permission_repair_ui.TargetChannelPickerView(
        state,
        actor=actor,
        query="ticket help",
    )
    assert [item.resource_id for item in name_search.candidates] == [303]

    id_search = permission_repair_ui.TargetChannelPickerView(
        state,
        actor=actor,
        query="<#101>",
    )
    assert [item.resource_id for item in id_search.candidates] == [101]


def test_fix_access_search_uses_saved_previous_name_aliases(monkeypatch) -> None:
    monkeypatch.setattr(permission_repair_core, "_target_supported", lambda _channel: True)
    current = FakeChannel(404, "daily-bulletin", visible=True)
    state, actor = _state_and_actor([current])

    async def fake_alias_index(guild_id: int):
        assert guild_id == 9876
        return {"channel:404": ("general-news",)}

    monkeypatch.setattr(
        resource_browser.naming_identity,
        "get_search_alias_index",
        fake_alias_index,
    )

    async def scenario() -> None:
        browser = permission_repair_ui.TargetChannelPickerView(state, actor=actor)
        searched = await browser.search("general news")
        assert isinstance(searched, permission_repair_ui.TargetChannelPickerView)
        assert [item.resource_id for item in searched.candidates] == [404]

    import asyncio
    asyncio.run(scenario())


def test_dedicated_picker_has_explicit_error_handlers() -> None:
    assert "on_error" in permission_repair_ui.TargetChannelPickerView.__dict__
    assert "on_error" in permission_repair_ui.TargetPermissionRepairView.__dict__
