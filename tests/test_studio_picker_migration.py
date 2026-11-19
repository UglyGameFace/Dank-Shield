"""Slice 1 picker migration: studios use the house picker framework.

Proves the three channel pickers (Embed Studio destination, Exit Card
Studio, Welcome Card Studio) list EVERY text channel of a >25-channel
guild across their pages, in server order with categories shown, and
that picking a channel routes into the same destination logic the raw
Discord selects used before the migration.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

from stoney_verify import embed_studio_ui as embed_ui
from stoney_verify import exit_card_studio_ui as exit_ui
from stoney_verify import welcome_card_studio_ui as welcome_ui

GUILD_ID = 1514374173517152418
OWNER_ID = 629459300854661120
CHANNEL_COUNT = 60  # The 420 Lobby scale: well past one 25-option page.


def _fake_guild(count: int = CHANNEL_COUNT) -> SimpleNamespace:
    guild = SimpleNamespace(id=GUILD_ID)
    categories = {
        2000 + i: SimpleNamespace(name=f"Category {i}")
        for i in range((count + 19) // 20)
    }
    channels = []
    for index in range(count):
        channel = object.__new__(discord.TextChannel)
        channel.id = 1000 + index
        channel.name = f"chan-{index:02}"
        channel.guild = guild
        channel.category_id = 2000 + (index // 20)
        channels.append(channel)
    by_id = {channel.id: channel for channel in channels}
    by_id.update(categories)
    guild.text_channels = channels
    guild.get_channel = lambda channel_id: by_id.get(int(channel_id))
    return guild


def _all_option_values(picker) -> list[str]:
    values: list[str] = []
    for page in range(picker.pages):
        page_view = picker._for_page(page)
        select = page_view.children[0]
        values.extend(str(option.value) for option in select.options)
    return values


def test_embed_destination_picker_pages_cover_every_channel():
    guild = _fake_guild()
    session = embed_ui.StudioSession(guild_id=GUILD_ID, owner_id=OWNER_ID)
    interaction = SimpleNamespace(guild=guild)
    picker = embed_ui._destination_picker_view(interaction, session)

    assert picker.pages == 3
    values = _all_option_values(picker)
    assert values == [str(1000 + i) for i in range(CHANNEL_COUNT)]
    first = picker.children[0]
    assert first.custom_id == "dank:embed_studio:destination:v1:0"
    assert first.options[0].label == "#chan-00"
    assert first.options[0].description == "Category 0"
    last = picker._for_page(2).children[0]
    assert len(last.options) == 10
    assert last.options[-1].description == "Category 2"
    # Pager buttons exist so later pages are reachable from page one.
    assert any(
        getattr(item, "custom_id", "") == "dank:embed_studio:picker:next"
        for item in picker.children
    )


def test_embed_destination_pick_routes_to_select_studio_destination(monkeypatch):
    guild = _fake_guild()
    session = embed_ui.StudioSession(guild_id=GUILD_ID, owner_id=OWNER_ID)
    session.post_key = "999:888"  # Destination change must clear the edit target.
    homes = []

    async def fake_home(interaction, session_arg, *, notice=""):
        homes.append(notice)

    monkeypatch.setattr(embed_ui, "_home", fake_home)
    picker = embed_ui._destination_picker_view(SimpleNamespace(guild=guild), session)
    interaction = SimpleNamespace(guild=guild)
    asyncio.run(picker.handle_pick(interaction, "1042"))

    assert session.channel_id == 1042
    assert session.post_key == ""
    assert homes and "Channel set to <#1042>." in homes[0]


def test_exit_channel_picker_pages_cover_every_channel():
    guild = _fake_guild()
    picker = exit_ui.ExitChannelPickerView(owner_id=OWNER_ID, guild=guild)

    assert picker.pages == 3
    values = _all_option_values(picker)
    assert values == [str(1000 + i) for i in range(CHANNEL_COUNT)]
    first = picker.children[0]
    assert first.custom_id == "dank:exit:studio:channel:v1:0"
    assert first.options[0].label == "#chan-00"
    assert first.options[0].description == "Category 0"


def test_exit_channel_pick_routes_to_save_exit_card_channel(monkeypatch):
    guild = _fake_guild()
    saved = []

    async def fake_save(interaction, channel):
        saved.append(channel)

    monkeypatch.setattr(exit_ui, "_save_exit_card_channel", fake_save)
    picker = exit_ui.ExitChannelPickerView(owner_id=OWNER_ID, guild=guild)
    interaction = SimpleNamespace(guild=guild)
    asyncio.run(picker.handle_pick(interaction, "1007"))

    assert len(saved) == 1
    assert saved[0].id == 1007
    assert isinstance(saved[0], discord.TextChannel)


def test_welcome_channel_picker_pages_cover_every_channel():
    guild = _fake_guild()
    picker = welcome_ui.WelcomeChannelPickerView(owner_id=OWNER_ID, guild=guild)

    assert picker.pages == 3
    values = _all_option_values(picker)
    assert values == [str(1000 + i) for i in range(CHANNEL_COUNT)]
    first = picker.children[0]
    assert first.custom_id == "dank:welcome:studio:join_channel:v1:0"
    assert first.options[0].label == "#chan-00"
    assert first.options[0].description == "Category 0"


def test_welcome_channel_pick_routes_to_save_live_channel(monkeypatch):
    guild = _fake_guild()
    saved = []

    async def fake_save(interaction, owner_id, channel):
        saved.append((owner_id, channel))

    monkeypatch.setattr(welcome_ui, "_save_live_welcome_card_channel", fake_save)
    picker = welcome_ui.WelcomeChannelPickerView(owner_id=OWNER_ID, guild=guild)
    interaction = SimpleNamespace(guild=guild)
    asyncio.run(picker.handle_pick(interaction, "1059"))

    assert len(saved) == 1
    assert saved[0][0] == OWNER_ID
    assert saved[0][1].id == 1059


def test_single_page_guild_keeps_unpaged_custom_id():
    guild = _fake_guild(5)
    picker = welcome_ui.WelcomeChannelPickerView(owner_id=OWNER_ID, guild=guild)
    assert picker.pages == 1
    assert picker.children[0].custom_id == "dank:welcome:studio:join_channel:v1"
    assert _all_option_values(picker) == [str(1000 + i) for i in range(5)]


def test_studio_menus_have_no_raw_discord_selects_left():
    session = embed_ui.StudioSession(guild_id=GUILD_ID, owner_id=OWNER_ID)
    home = embed_ui.StudioHomeView(session)
    raw = [
        item for item in home.children
        if isinstance(item, (discord.ui.ChannelSelect, discord.ui.RoleSelect))
        and not item.__class__.__module__.startswith("stoney_verify.ui.picker")
    ]
    # The only native select left on any studio surface is the framework's
    # own DankRoleSelect wrapper in Role Controls.
    assert raw == []
    roles = embed_ui.StudioRolesMenu(session)
    wrappers = [
        item for item in roles.children
        if item.__class__.__name__ == "DankRoleSelect"
    ]
    assert len(wrappers) == 1
    assert wrappers[0].custom_id == "dank:embed_studio:role:v1"
