from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

from stoney_verify.commands_ext import public_share_router as ui
from stoney_verify.ui.resource_browser import DankGuildResourceBrowserView


class TextChannel:
    def __init__(self, channel_id: int, guild, *, name: str, category=None,
                 bot_attachments: bool = True, nsfw: bool = False):
        self.id = channel_id
        self.guild = guild
        self.name = name
        self.mention = f"<#{channel_id}>"
        self.category = category
        self._bot_attachments = bot_attachments
        self._nsfw = nsfw

    def is_nsfw(self):
        return self._nsfw

    def permissions_for(self, member):
        return SimpleNamespace(
            view_channel=True,
            send_messages=True,
            attach_files=self._bot_attachments,
            read_message_history=True,
        )


def _fixture(monkeypatch):
    guild = SimpleNamespace(id=111, me=SimpleNamespace(id=99), roles=[])
    category = SimpleNamespace(name="🔗 SHARE ROUTES")
    channels = [
        TextChannel(100 + i, guild, name=f"share-channel-{i}", category=category)
        for i in range(34)
    ]
    channels.append(TextChannel(250, guild, name="video-results", category=None))
    guild.channels = channels
    guild.get_channel = lambda cid: next((item for item in channels if item.id == cid), None)
    actor = SimpleNamespace(id=42)
    monkeypatch.setattr(ui.discord, "TextChannel", TextChannel)
    return guild, actor, channels


def test_private_category_text_channel_can_be_request_and_result(monkeypatch):
    guild, actor, channels = _fixture(monkeypatch)
    request = channels[0]
    assert ui._video_destination_blocker(guild, actor, request, request.id) == ""
    assert asyncio.run(ui._request_channel_blocker(guild, request, [])) == ""


def test_request_conflict_and_video_permissions_are_explained(monkeypatch):
    guild, actor, channels = _fixture(monkeypatch)
    chosen = channels[1]
    routes = [{"source_channel_id": chosen.id, "target_channel_id": 250}]
    message = asyncio.run(ui._request_channel_blocker(guild, chosen, routes))
    assert "forwarding rule" in message.lower()
    assert ui._video_destination_blocker(guild, actor, None, chosen.id)
    chosen._bot_attachments = False
    assert "Attach Files" in ui._video_destination_blocker(guild, actor, chosen, chosen.id)
    chosen._bot_attachments = True
    chosen._nsfw = True
    assert "age-restricted" in ui._video_destination_blocker(guild, actor, chosen, chosen.id)


def test_destination_picker_lists_every_available_text_channel(monkeypatch):
    guild, actor, channels = _fixture(monkeypatch)
    interactions = []

    async def allowed(_):
        return True

    async def current_request(_gid):
        return channels[0].id

    async def render(interaction, **kwargs):
        interactions.append(kwargs)

    monkeypatch.setattr(ui, "_require_manage", allowed)
    monkeypatch.setattr(ui, "guild_download_channel_id", current_request)
    monkeypatch.setattr(ui, "_replace", render)

    view = ui.ShareRouterDestinationChoiceView(actor.id, "channel")
    button = next(button for button in view.children if button.label == "Choose Channel")
    interaction = SimpleNamespace(guild=guild, user=actor)
    asyncio.run(button.callback(interaction))
    browser = interactions[-1]["view"]
    assert isinstance(browser, DankGuildResourceBrowserView)
    assert len(browser.candidates) == len(channels)
    assert browser.page_count == 2
    assert {candidate.resource_id for candidate in browser.candidates} == {
        item.id for item in channels
    }
    assert any(button.label == "Next" for button in browser.children)
    assert any(button.label == "Search" for button in browser.children)
    assert any(button.label == "Use This Channel" for button in browser.children)


def test_request_picker_does_not_hide_managed_category_or_conflicting_rows(monkeypatch):
    guild, actor, channels = _fixture(monkeypatch)
    results = []

    async def allowed(_):
        return True

    async def routes(_gid):
        return [{"source_channel_id": channels[1].id, "target_channel_id": 250}]

    async def render(interaction, **kwargs):
        results.append(kwargs)

    monkeypatch.setattr(ui, "_require_manage", allowed)
    monkeypatch.setattr(ui, "guild_routes", routes)
    monkeypatch.setattr(ui, "_replace", render)
    view = ui.ShareRouterRequestChoiceView(actor.id)
    button = next(button for button in view.children if button.label == "Choose Channel")
    asyncio.run(button.callback(SimpleNamespace(guild=guild, user=actor)))
    browser = results[-1]["view"]
    assert len(browser.candidates) == len(channels)
    assert any(candidate.resource_id == channels[1].id for candidate in browser.candidates)
    assert asyncio.run(ui._request_channel_blocker(
        guild, channels[1], asyncio.run(routes(guild.id))
    ))


def test_one_channel_shortcut_confirms_before_atomic_no_dm_save(monkeypatch):
    from contextlib import asynccontextmanager

    guild, actor, channels = _fixture(monkeypatch)
    channel = channels[0]
    interaction = SimpleNamespace(
        guild=guild,
        user=actor,
        channel=channel,
    )
    displayed, saved = [], []

    async def allowed(_):
        return True

    async def routes(_):
        return []

    async def render(_, **kwargs):
        displayed.append(kwargs)

    @asynccontextmanager
    async def lock_context():
        yield

    async def persist(gid, *, channel_id, mode, destination_id):
        saved.append((gid, channel_id, mode, destination_id))

    async def current_embed(_):
        return discord.Embed(title="Video Downloads")

    edits = []

    async def defer(**kwargs):
        assert kwargs["ephemeral"] and kwargs["thinking"]

    async def edit(**kwargs):
        edits.append(kwargs)

    interaction.response = SimpleNamespace(defer=defer)
    interaction.edit_original_response = edit

    monkeypatch.setattr(ui, "_require_manage", allowed)
    monkeypatch.setattr(ui, "guild_routes", routes)
    monkeypatch.setattr(ui, "_replace", render)
    monkeypatch.setattr(ui, "_guild_source_lock", lambda _gid: lock_context())
    monkeypatch.setattr(ui, "configure_guild_download", persist)
    monkeypatch.setattr(ui, "_video_delivery_embed", current_embed)

    asyncio.run(ui._confirm_current_video_channel(interaction))
    assert not saved
    assert "DMs:** Off" in displayed[-1]["embed"].description
    view = displayed[-1]["view"]
    assert isinstance(view, ui.ShareRouterSameChannelConfirmView)

    button = next(child for child in view.children if child.label == "Save Requests + Results Here")
    asyncio.run(button.callback(interaction))
    assert saved == [(guild.id, channel.id, "channel", channel.id)]
    assert "DMs are off" in edits[-1]["content"]


def test_one_channel_shortcut_rechecks_forwarding_conflicts_before_save(monkeypatch):
    from contextlib import asynccontextmanager

    guild, actor, channels = _fixture(monkeypatch)
    channel = channels[0]
    edits, writes = [], []

    async def allowed(_):
        return True

    async def routes(_):
        return [{"source_channel_id": channel.id, "target_channel_id": 250}]

    @asynccontextmanager
    async def lock():
        yield

    async def persist(*args, **kwargs):
        writes.append((args, kwargs))

    async def defer(**kwargs):
        return None

    async def edit(**kwargs):
        edits.append(kwargs)

    monkeypatch.setattr(ui, "_require_manage", allowed)
    monkeypatch.setattr(ui, "guild_routes", routes)
    monkeypatch.setattr(ui, "_guild_source_lock", lambda _gid: lock())
    monkeypatch.setattr(ui, "configure_guild_download", persist)
    interaction = SimpleNamespace(
        guild=guild, user=actor, channel=channel,
        response=SimpleNamespace(defer=defer),
        edit_original_response=edit,
    )
    view = ui.ShareRouterSameChannelConfirmView(actor.id, channel.id)
    button = next(item for item in view.children if item.label == "Save Requests + Results Here")
    asyncio.run(button.callback(interaction))
    assert not writes
    assert "forwarding rule" in edits[-1]["content"].lower()
