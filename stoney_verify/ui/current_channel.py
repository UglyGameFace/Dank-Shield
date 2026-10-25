"""One canonical safe 'Use This Channel' resolver for Discord setup screens.

Never reads the dropdown's current selection. Button interactions already know
the channel where the menu was opened. Feature owners remain responsible for
staff authorization, storage CAS, channel privacy, and domain-specific checks.
"""
from __future__ import annotations

from typing import Any

import discord


def current_text_channel(
    interaction: discord.Interaction, *,
    require_bot_view: bool = True,
    require_bot_send: bool = True,
    require_bot_embed: bool = False,
) -> discord.TextChannel | None:
    """Resolve only the exact invoking guild text channel, fail closed otherwise.

    A thread, DM, forum, voice or foreign guild channel is never coerced to an
    unrelated text channel. Recheck current bot rights instead of using a stale
    or unsubmitted Discord native selector value.
    """
    guild = getattr(interaction, "guild", None)
    current = getattr(interaction, "channel", None)
    if guild is None or current is None:
        return None
    try:
        cid = int(getattr(current, "id", 0) or 0)
        if cid <= 0:
            return None
        channel = guild.get_channel(cid)
        if not isinstance(channel, discord.TextChannel):
            return None
        if int(channel.guild.id) != int(guild.id):
            return None
        me = getattr(guild, "me", None)
        if require_bot_view or require_bot_send or require_bot_embed:
            if me is None:
                return None
            permissions = channel.permissions_for(me)
            if ((require_bot_view and not permissions.view_channel)
                    or (require_bot_send and not permissions.send_messages)
                    or (require_bot_embed and not permissions.embed_links)):
                return None
        return channel
    except (AttributeError, TypeError, ValueError):
        return None


def eligible_browser_current_channel(
    interaction: discord.Interaction, *, guild: Any,
    kinds: tuple[str, ...], predicate: Any = None,
) -> discord.TextChannel | None:
    """Resolve the current channel for text browsers, using their own filter.

    The normal browser on_pick callback performs the feature's authority and
    save checks. We deliberately do not guess a destination for voice,
    category, or role-only browsers.
    """
    if "text" not in kinds and "channel" not in kinds:
        return None
    channel = current_text_channel(
        interaction, require_bot_view=False, require_bot_send=False
    )
    if channel is None or int(channel.guild.id) != int(getattr(guild, "id", 0)):
        return None
    if predicate is not None:
        try:
            if not bool(predicate(channel)):
                return None
        except Exception:
            return None
    return channel
