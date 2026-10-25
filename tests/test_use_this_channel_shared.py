"""Single canonical Use This Channel contract across Discord Android setup."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import discord
import pytest

from stoney_verify.ui import current_channel as shortcut
from stoney_verify.ui.resource_browser import DankGuildResourceBrowserView
from stoney_verify.commands_ext import public_verify_reminder_ui as reminder_ui
from stoney_verify.verification_reminders import ReminderPolicy


class FakeTextChannel:
    def __init__(self, cid: int = 999, *, allowed: bool = True):
        self.id = cid
        self.guild = None
        self.name = "mod-chat"
        self.type = discord.ChannelType.text
        self.mention = f"<#{cid}>"
        self.category = None
        self.allowed = allowed

    def permissions_for(self, _member):
        return SimpleNamespace(
            view_channel=self.allowed,
            send_messages=self.allowed,
            embed_links=self.allowed,
            read_message_history=self.allowed,
        )


class FakeGuild:
    def __init__(self, channel: FakeTextChannel):
        self.id = 123
        self.me = SimpleNamespace(id=77)
        self.channels = [channel]
        self.roles = []
        self.channel = channel
        channel.guild = self

    def get_channel(self, cid):
        return self.channel if cid == self.channel.id else None

    def get_role(self, _rid):
        return None


class FakeResponse:
    def __init__(self):
        self.done = False
        self.sent = []
        self.deferred = False

    def is_done(self):
        return self.done

    async def send_message(self, text, **kwargs):
        self.done = True
        self.sent.append(str(text))

    async def defer(self, **kwargs):
        self.done = True
        self.deferred = True


def setup(monkeypatch, *, allowed=True, actor=456):
    monkeypatch.setattr(discord, "TextChannel", FakeTextChannel)
    channel = FakeTextChannel(999, allowed=allowed)
    guild = FakeGuild(channel)
    interaction = SimpleNamespace(
        guild=guild, channel=channel,
        user=SimpleNamespace(id=actor),
        response=FakeResponse(),
    )
    return guild,channel,interaction


def test_current_channel_resolves_exact_cached_channel_no_dropdown(monkeypatch):
    guild,ch,interaction=setup(monkeypatch)
    assert shortcut.current_text_channel(interaction) is ch
    assert shortcut.current_text_channel(interaction,require_bot_embed=True) is ch
    interaction.channel=SimpleNamespace(id=444)
    assert shortcut.current_text_channel(interaction) is None
    interaction.channel=ch
    interaction.guild=SimpleNamespace(id=222,get_channel=lambda _id: ch,me=guild.me)
    assert shortcut.current_text_channel(interaction) is None
    interaction.guild=guild
    interaction.channel=SimpleNamespace(id=444,type=discord.ChannelType.public_thread)
    assert shortcut.current_text_channel(interaction) is None


def test_no_missing_bot_permission_or_dm_leak(monkeypatch):
    guild,ch,interaction=setup(monkeypatch,allowed=False)
    assert shortcut.current_text_channel(interaction) is None
    assert shortcut.current_text_channel(
        interaction,require_bot_view=False,require_bot_send=False
    ) is ch
    interaction.guild=None
    assert shortcut.current_text_channel(interaction) is None


def test_search_safe_browser_has_current_channel_button_and_same_owner_callback(monkeypatch):
    guild,ch,interaction=setup(monkeypatch)
    actions=[]

    async def selected(pick, channel):
        actions.append(channel.id)

    browser=DankGuildResourceBrowserView(
        guild=guild,author_id=456,resource_kinds=("text",),
        on_pick=selected,custom_id="test:current-channel",
    )
    assert "Use This Channel" in [getattr(x,"label",None) for x in browser.children]
    assert "Use This Channel" in [getattr(x,"label",None) for x in browser.clone(page=1).children]
    asyncio.run(browser.pick_current_channel(interaction))
    assert actions==[999]
    wrong=SimpleNamespace(user=SimpleNamespace(id=888),guild=guild,channel=ch,
                          response=FakeResponse())
    asyncio.run(browser.pick_current_channel(wrong))
    assert actions==[999]
    assert "another" in wrong.response.sent[0].lower()


def test_browser_uses_feature_predicate_and_does_not_grant_roles(monkeypatch):
    guild,ch,interaction=setup(monkeypatch)
    actions=[]
    async def picked(pick,item):
        actions.append(item.id)
    denied=DankGuildResourceBrowserView(
        guild=guild,author_id=456,resource_kinds=("text",),
        on_pick=picked,custom_id="test:private-channel",
        predicate=lambda channel: False,
    )
    asyncio.run(denied.pick_current_channel(interaction))
    assert not actions
    assert interaction.response.sent
    roles=DankGuildResourceBrowserView(
        guild=guild,author_id=456,resource_kinds=("role",),
        on_pick=picked,custom_id="test:roles",
    )
    assert "Use This Channel" not in [getattr(x,"label",None) for x in roles.children]


def test_reminders_choose_invoking_channel_and_delivery_atomically(monkeypatch):
    guild,ch,interaction=setup(monkeypatch)
    saves=[]
    async def staff(_):return True
    async def load(_guild):
        assert interaction.response.deferred
        return {},ReminderPolicy(delivery="dm")
    async def save(_interaction,_old,updated):
        saves.append(updated)
        return True
    async def replace(_interaction,**kwargs):
        saves.append(kwargs["view"].policy)
    monkeypatch.setattr(reminder_ui,"_require_staff",staff)
    monkeypatch.setattr(reminder_ui,"_load",load)
    monkeypatch.setattr(reminder_ui,"_save",save)
    monkeypatch.setattr(reminder_ui,"_replace",replace)
    monkeypatch.setattr(reminder_ui,"_embed",lambda *args: discord.Embed(title="Reminders"))
    view=reminder_ui.ReminderSettingsView(456,ReminderPolicy(delivery="dm"))
    assert "Use This Channel" in [getattr(x,"label",None) for x in view.children]
    assert "Choose Other Channel" in [getattr(x,"label",None) for x in view.children]
    assert not any(isinstance(x,discord.ui.ChannelSelect) for x in view.children)
    button=next(x for x in view.children if getattr(x,"label",None)=="Use This Channel")
    asyncio.run(button.callback(interaction))
    assert saves
    assert saves[0].reminder_channel_id==999 and saves[0].delivery=="channel"
    assert saves[1]==saves[0]


def test_reminder_use_this_denies_unavailable_current_channel(monkeypatch):
    guild,ch,interaction=setup(monkeypatch,allowed=False)
    async def staff(_): return True
    async def bad_save(*a,**kw): raise AssertionError("invalid channel must not save")
    monkeypatch.setattr(reminder_ui,"_require_staff",staff)
    monkeypatch.setattr(reminder_ui,"_save",bad_save)
    view=reminder_ui.ReminderSettingsView(456,ReminderPolicy())
    button=next(x for x in view.children if getattr(x,"label",None)=="Use This Channel")
    asyncio.run(button.callback(interaction))
    assert interaction.response.sent and "open this menu" in interaction.response.sent[0].lower()


@pytest.mark.parametrize("path",[
    "stoney_verify/embed_studio_ui.py",
    "stoney_verify/welcome_setup_ui.py",
    "stoney_verify/welcome_card_studio_ui.py",
    "stoney_verify/exit_card_studio_ui.py",
    "stoney_verify/profile_card_setup_ui.py",
    "stoney_verify/commands_ext/public_somewhere_420_settings.py",
    "stoney_verify/commands_ext/public_ticket_command_center.py",
    "stoney_verify/commands_ext/public_cleanup_command_center.py",
])
def test_current_channel_shortcuts_route_through_owner_feature(path):
    source=Path(path).read_text()
    assert "Use This Channel" in source
    assert "current_text_channel" in source


def test_ticket_private_alerts_still_restrict_everyone():
    script=Path("stoney_verify/commands_ext/public_ticket_command_center.py").read_text()
    callback=script.split("async def _save_ticket_staff_alert_channel",1)[1].split("class TicketNotificationChannelSelect",1)[0]
    assert "_private_for_staff(channel)" in callback
    assert "upsert_ticket_automation_settings" in callback


def test_cleanup_shortcut_is_selection_only_not_an_immediate_delete():
    source=Path("stoney_verify/commands_ext/public_cleanup_command_center.py").read_text()
    one=source.split("class _ChannelStateView(",1)[1].split("class ChannelPurgeView(",1)[0]
    assert "self.channel_id = channel.id" in one
    assert "_invoke(" not in one
    assert "await _invoke(" not in one
