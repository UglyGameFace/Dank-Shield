from __future__ import annotations

"""Voice Safety channel-scope policy (configuration only, never audio authority).

An installed Discord bot identity can receive from at most one VC per guild.
This module discovers qualifying rooms; it does not acquire a receiver lease
or claim that queued rooms are being monitored.
"""

from typing import Any, Mapping

import discord


COVERAGE_KEY = "voice_safety_coverage_mode"
SINGLE = "single"
ALL = "all"
MIN_HUMANS = 2


def coverage_mode(config: Mapping[str, Any]) -> str:
    # Existing guilds remain single-channel until staff explicitly switch scope.
    return ALL if config.get(COVERAGE_KEY) == ALL else SINGLE


def human_count(channel: Any) -> int:
    return sum(
        not getattr(member, "bot", False)
        for member in tuple(getattr(channel, "members", ()) or ())
    )


def channel_in_scope(guild: Any, config: Mapping[str, Any], channel: Any) -> bool:
    """Is this an existing, bot-accessible normal VC covered by policy?"""
    if not isinstance(channel, discord.VoiceChannel):
        return False
    try:
        cid = int(channel.id)
        if cid <= 0 or guild.get_channel(cid) is None:
            return False
        if coverage_mode(config) == SINGLE and cid != int(
            config.get("voice_safety_voice_channel_id") or 0
        ):
            return False
        me = guild.me
        if me is None:
            return False
        perms = channel.permissions_for(me)
        return bool(perms.view_channel and perms.connect)
    except (AttributeError, TypeError, ValueError):
        return False


def qualifying_channels(guild: Any, config: Mapping[str, Any]) -> list[discord.VoiceChannel]:
    """Deterministic busiest-first candidates, humans only, capped by caller."""
    eligible = (
        voice for voice in tuple(getattr(guild, "voice_channels", ()) or ())
        if channel_in_scope(guild, config, voice)
        and human_count(voice) >= MIN_HUMANS
    )
    return sorted(eligible, key=lambda voice: (-human_count(voice), int(voice.id)))
