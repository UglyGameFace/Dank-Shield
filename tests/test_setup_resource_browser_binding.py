from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

from stoney_verify.setup_resource_picker_binding import (
    _resource_kinds,
    apply_setup_resource_picker_binding,
)
from stoney_verify.ui.resource_browser import (
    DankGuildResourceBrowserView,
    build_resource_candidates,
)


def _role(index: int, name: str | None = None):
    return SimpleNamespace(
        id=10_000 + index,
        name=name or f"role-{index:02d}",
        mention=f"<@&{10_000 + index}>",
        members=[],
        managed=False,
    )


def _channel(index: int, name: str, channel_type: discord.ChannelType):
    return SimpleNamespace(
        id=20_000 + index,
        name=name,
        mention=f"<#{20_000 + index}>",
        type=channel_type,
        category=None,
        channels=[],
    )


class FakeGuild:
    def __init__(self, *, roles=None, channels=None):
        self.roles = list(roles or [])
        self.channels = list(channels or [])
        self._roles = {int(item.id): item for item in self.roles}
        self._channels = {int(item.id): item for item in self.channels}

    def get_role(self, role_id: int):
        return self._roles.get(int(role_id))

    def get_channel(self, channel_id: int):
        return self._channels.get(int(channel_id))


def test_resource_candidates_search_name_id_and_mentions() -> None:
    guild = FakeGuild(
        roles=[_role(1, "Ticket Staff"), _role(2, "Verified")],
        channels=[
            _channel(1, "mod-log", discord.ChannelType.text),
            _channel(2, "Voice Verify", discord.ChannelType.voice),
            _channel(3, "Tickets", discord.ChannelType.category),
        ],
    )

    assert [item.label for item in build_resource_candidates(guild, resource_kinds=("role",), query="ticket")] == ["Ticket Staff"]
    assert [item.label for item in build_resource_candidates(guild, resource_kinds=("text",), query="20001")] == ["mod-log"]
    assert [item.label for item in build_resource_candidates(guild, resource_kinds=("voice",), query="<#20002>")] == ["Voice Verify"]
    assert [item.label for item in build_resource_candidates(guild, resource_kinds=("category",), query="Tickets")] == ["Tickets"]


def test_resource_browser_pages_more_than_discord_option_limit() -> None:
    guild = FakeGuild(roles=[_role(index) for index in range(61)])

    async def picked(_interaction, _resource):
        return None

    async def scenario() -> None:
        first = DankGuildResourceBrowserView(
            guild=guild,
            author_id=123,
            resource_kinds=("role",),
            on_pick=picked,
            custom_id="test:roles",
            page=0,
        )
        second = first.clone(page=1)
        third = first.clone(page=2)

        assert first.page_count == 3
        assert len(first.candidates) == 61
        assert len(first.children[0].options) == 25
        assert len(second.children[0].options) == 25
        assert len(third.children[0].options) == 11
        assert all(len(getattr(child, "custom_id", "") or "") <= 100 for child in first.children)

    asyncio.run(scenario())


def test_resource_kind_mapping_keeps_setup_type_filters() -> None:
    assert _resource_kinds([discord.ChannelType.category]) == ("category",)
    assert _resource_kinds([discord.ChannelType.text]) == ("text",)
    assert _resource_kinds([discord.ChannelType.voice]) == ("voice",)
    assert _resource_kinds([discord.ChannelType.text, discord.ChannelType.voice]) == ("text", "voice")


def test_binding_replaces_live_public_setup_entity_selectors() -> None:
    from stoney_verify.commands_ext import public_setup_full_customization as full
    from stoney_verify.commands_ext import public_setup_recommend as recommend
    from stoney_verify.commands_ext import public_setup_solid as solid

    assert apply_setup_resource_picker_binding() is True

    assert issubclass(solid.SaveRoleSelect, discord.ui.Button)
    assert issubclass(solid.SaveChannelSelect, discord.ui.Button)
    assert not issubclass(solid.SaveRoleSelect, discord.ui.RoleSelect)
    assert not issubclass(solid.SaveChannelSelect, discord.ui.ChannelSelect)

    assert issubclass(full.SaveRoleSelect, discord.ui.Button)
    assert issubclass(full.SaveChannelSelect, discord.ui.Button)
    assert not issubclass(full.SaveRoleSelect, discord.ui.RoleSelect)
    assert not issubclass(full.SaveChannelSelect, discord.ui.ChannelSelect)

    assert issubclass(recommend.GuidedExistingRoleSelect, discord.ui.Button)
    assert issubclass(recommend.GuidedExistingChannelSelect, discord.ui.Button)


def test_live_setup_views_construct_without_native_entity_selects_after_binding() -> None:
    from stoney_verify.commands_ext import public_setup_full_customization as full
    from stoney_verify.commands_ext import public_setup_recommend as recommend
    from stoney_verify.commands_ext import public_setup_solid as solid

    assert apply_setup_resource_picker_binding() is True

    views = [
        solid.TicketBasicsPickerView(),
        solid.AccessRolesPickerView(),
        solid.VerificationChannelsPickerView(),
        solid.LogsStatusPickerView(),
        full.RoleCustomizationPageOne(parent="features"),
        full.DiscordCategoryCustomizationView(parent="features"),
        full.ChannelCustomizationPageOne(parent="features"),
        full.LogStatusCustomizationView(parent="features"),
        recommend.GuidedOneItemView(requirement_key="ticket_staff_role"),
        recommend.GuidedOneItemView(requirement_key="ticket_folder"),
    ]

    for view in views:
        assert not any(isinstance(child, (discord.ui.RoleSelect, discord.ui.ChannelSelect)) for child in view.children)
        assert len(view.children) <= 25
        rows: dict[int, int] = {}
        for child in view.children:
            row = int(getattr(child, "row", 0) or 0)
            rows[row] = rows.get(row, 0) + 1
        assert all(count <= 5 for count in rows.values())


def test_guided_business_logic_stays_owned_by_existing_save_path() -> None:
    import ast
    from pathlib import Path

    from stoney_verify.commands_ext import public_setup_recommend as recommend

    module_source = Path(recommend.__file__).read_text(encoding="utf-8")
    tree = ast.parse(module_source)
    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_guided_save_existing_item"
    )
    source = ast.get_source_segment(module_source, owner) or ""

    assert "_guided_step_is_current" in source
    assert "_guided_item_payload" in source
    assert "_save_config" in source
    assert "_open_guided_setup" in source
