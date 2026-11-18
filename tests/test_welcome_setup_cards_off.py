"""Welcome Card discoverability: plain cards-off state + one-tap enable.

Cards stay OFF by default, but a guild that opens /dank welcome with
welcome_card_enabled false now sees a plain "Welcome cards are off"
state and a one-tap enable path instead of a bare studio. Guilds with
cards already on see no change.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

from stoney_verify import welcome_setup_ui as welcome

NOTICE = "🪄 Welcome cards are off"


def _buttons(view):
    return {
        item.label for item in view.children if isinstance(item, discord.ui.Button)
    }


def test_enable_button_offered_when_cards_off_except_static():
    off = {}
    for section in ("join", "exit", "tools"):
        panel = welcome.WelcomeSetupView(owner_id=10, config=off, section=section)
        assert "Enable Welcome Cards" in _buttons(panel)
        assert len(panel.children) <= 9
    static = welcome.WelcomeSetupView(owner_id=10, config=off, section="static")
    assert "Enable Welcome Cards" not in _buttons(static)


def test_no_enable_button_when_cards_already_on():
    on = {"welcome_card_enabled": True}
    for section in welcome._WELCOME_SECTIONS:
        panel = welcome.WelcomeSetupView(owner_id=10, config=on, section=section)
        assert "Enable Welcome Cards" not in _buttons(panel)


def test_embed_leads_with_cards_off_notice_outside_join_section():
    guild = SimpleNamespace(get_channel=lambda _id: None)
    embed = asyncio.run(welcome._welcome_embed(guild, {}, section="tools"))
    assert embed.fields[0].name == NOTICE
    # The Join Card section leads with its own status field instead.
    join_embed = asyncio.run(welcome._welcome_embed(guild, {}, section="join"))
    assert all(field.name != NOTICE for field in join_embed.fields)
    assert "Status: **Off**" in join_embed.fields[0].value


def test_embed_has_no_notice_when_cards_on():
    guild = SimpleNamespace(get_channel=lambda _id: None)
    embed = asyncio.run(welcome._welcome_embed(
        guild, {"welcome_card_enabled": True}, section="tools"))
    assert all(field.name != NOTICE for field in embed.fields)
