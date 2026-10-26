from __future__ import annotations

"""Opt-in, identity-scoped suppression of trusted advertisers' embeds.

Discord SUPPRESS_EMBEDS is a moderation flag. This does not edit text, delete
posts, replace ads, obscure components, or grant a channel-wide exemption.
"""

import asyncio
from collections import deque
import time
from typing import Any

import discord

from .invite_trusted_advertisers import clean_rules, trusted_rule

# Cap extra moderation writes; never let an ad burst trigger a Discloud REST
# spike. Skipped suppressions stay visible, rather than retrying in a flood.
_RECENT_WRITES: deque[float] = deque()
_MAX_AUTOMATIC_WRITES_PER_MINUTE = 12
_WRITE_LOCK = asyncio.Lock()
_MANUAL_COOLDOWN: dict[tuple[int, int], float] = {}


def needs_collapse(message: Any, config: Any, *, require_opt_in: bool = True) -> bool:
    rule = trusted_rule(message, config)
    if not rule or (require_opt_in and not rule.get("suppress_embeds")):
        return False
    # Only messages with real rich embeds; don't suppress normal user chat,
    # native component-only application layouts, or text-only messages.
    if not list(getattr(message, "embeds", []) or []):
        return False
    flags = getattr(message, "flags", None)
    if bool(getattr(flags, "suppress_embeds", False)):
        return False
    return True


def _channel_permissions(message: Any) -> bool:
    channel = getattr(message, "channel", None)
    guild = getattr(message, "guild", None)
    me = getattr(guild, "me", None)
    if not isinstance(channel, discord.TextChannel) or me is None:
        return False
    perms = channel.permissions_for(me)
    return bool(
        getattr(perms, "view_channel", False)
        and getattr(perms, "manage_messages", False)
    )


async def _take_auto_slot() -> bool:
    async with _WRITE_LOCK:
        now = time.monotonic()
        while _RECENT_WRITES and _RECENT_WRITES[0] <= now - 60.0:
            _RECENT_WRITES.popleft()
        if len(_RECENT_WRITES) >= _MAX_AUTOMATIC_WRITES_PER_MINUTE:
            return False
        _RECENT_WRITES.append(now)
        return True


async def suppress_one(message: Any, config: Any, *, automatic: bool = True) -> bool:
    """Never suppress a message not authorized by its exact sender+channel."""
    if not needs_collapse(message, config, require_opt_in=automatic):
        return False
    if not _channel_permissions(message):
        return False
    # Staff-triggered recent suppression shares this global write budget with
    # automated suppression, so several guilds cannot accidentally burst REST.
    if not await _take_auto_slot():
        return False
    try:
        # Discord allows MANAGE_MESSAGES to change SUPPRESS_EMBEDS on another
        # author's post. Do not alter content, buttons, or author.
        await message.edit(suppress=True)
        print(
            "🫥 trusted_advert: embed_collapsed "
            f"guild={message.guild.id} channel={message.channel.id} "
            f"bot={message.author.id} message={message.id}"
        )
        return True
    except (discord.Forbidden, discord.HTTPException) as exc:
        print(
            "⚠️ trusted_advert: embed_collapse_denied "
            f"guild={getattr(message.guild, 'id', '?')} reason={type(exc).__name__}"
        )
        return False


async def suppress_new_trusted_advert(message: Any) -> bool:
    """Called only on likely bot embeds, not on every ordinary guild message."""
    if (getattr(message, "guild", None) is None
            or not bool(getattr(getattr(message, "author", None), "bot", False))
            or not list(getattr(message, "embeds", []) or [])):
        return False
    from .invite_policy_engine import load_invite_policy

    try:
        cfg, _settings = await load_invite_policy(message.guild)
        return await suppress_one(message, cfg)
    except Exception as exc:
        print(
            "⚠️ trusted_advert: embed_collapse_unavailable "
            f"guild={getattr(message.guild, 'id', '?')} reason={type(exc).__name__}"
        )
        return False


async def collapse_recent_trusted_adverts(
    guild: Any, channel: Any, bot_id: int, *,
    scan_limit: int = 15, max_edits: int = 3,
) -> dict[str, int]:
    """Explicit owner action for posts already present in the ad channel.

    A small, bounded message-history lookup; never a bulk purge.
    All candidates are checked again against the *fresh current* guild policy.
    """
    gid, cid = int(guild.id), int(channel.id)
    if not isinstance(channel, discord.TextChannel):
        raise ValueError("Select a server text channel.")
    if max_edits > 3 or scan_limit > 15 or max_edits < 1 or scan_limit < 1:
        raise ValueError("Recent collapse is bounded to 15 scanned and 3 changed posts.")
    if not _channel_permissions(type("_Lookup", (), {"guild": guild, "channel": channel})()):
        raise PermissionError("Dank Shield needs View Channel and Manage Messages.")
    from .invite_policy_engine import load_invite_policy
    cfg, _settings = await load_invite_policy(guild, refresh=True)
    if not any(x["bot_id"] == int(bot_id) and x["channel_id"] == cid for x in clean_rules(cfg)):
        raise PermissionError("The exact bot/channel pair is not approved.")
    now = time.monotonic()
    key = (gid, cid)
    if len(_MANUAL_COOLDOWN) > 512:
        _MANUAL_COOLDOWN.clear()
    if now - _MANUAL_COOLDOWN.get(key, -1e9) < 60:
        raise ValueError("Recent previews were checked just now. Try again in one minute.")
    _MANUAL_COOLDOWN[key] = now

    counts = {"scanned": 0, "collapsed": 0, "skipped": 0}
    async for message in channel.history(limit=scan_limit):
        counts["scanned"] += 1
        if int(getattr(getattr(message, "author", None), "id", 0) or 0) != int(bot_id):
            counts["skipped"] += 1
            continue
        if not needs_collapse(message, cfg, require_opt_in=False):
            counts["skipped"] += 1
            continue
        if await suppress_one(message, cfg, automatic=False):
            counts["collapsed"] += 1
        else:
            counts["skipped"] += 1
        if counts["collapsed"] >= max_edits:
            break
    return counts
