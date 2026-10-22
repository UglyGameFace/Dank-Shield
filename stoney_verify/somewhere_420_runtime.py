from __future__ import annotations

"""Single opt-in 4:20 announcement worker. Polls due DB index, never guild lists."""

import asyncio
from datetime import datetime, timezone
from typing import Any

import discord

from .community_pings_service import parse_community_pings, toke_role_ids
from .guild_config import get_guild_config
from .somewhere_420_schedules import Somewhere420StorageUnavailable, claim_due, get_schedule, quiet_at

_TASK: asyncio.Task[None] | None = None
POLL_SECONDS = 20
MAX_BATCH = 16
MAX_PARALLEL = 4


async def deliver_claim(bot: Any, claimed: dict[str, Any]) -> str:
    """At-most-once claim already consumed; never retry a Discord send."""
    kind = str(claimed.get("kind") or "")
    if kind not in ("guild", "member"):
        return "bad-kind"
    ident = int(claimed.get("guild_id") if kind == "guild" else claimed.get("user_id"))
    current = await get_schedule(kind, ident)
    if not current or not current.get("enabled"):
        return "disabled"
    # Click-time disabling or changed destinations must take effect, even on a
    # record already claimed in this sweep.
    if current.get("updated_at") != claimed.get("updated_at"):
        return "changed"

    if kind == "member":
        try:
            user = bot.get_user(ident) or await bot.fetch_user(ident)
            if user is None or user.bot:
                return "missing-member"
            embed = discord.Embed(
                title="🌍💨 It's 4:20 in your timezone!",
                description=(
                    "Your personal 4:20 reminder is here. Open **/dank home → "
                    "Community & Engagement → It's 4:20 Somewhere** in any shared "
                    "server to enjoy the world clock. Disable DMs in Personalize."
                ),
                color=discord.Color.green(),
            )
            await user.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            return "dm-sent"
        except (discord.Forbidden, discord.HTTPException):
            return "dm-blocked"

    guild = bot.get_guild(ident)
    if guild is None:
        return "missing-guild"
    hour = datetime.now(timezone.utc).astimezone(
        __import__("zoneinfo").ZoneInfo(str(current["timezone"]))
    ).hour
    if quiet_at(hour, int(current.get("quiet_start", 22)), int(current.get("quiet_end", 8))):
        return "quiet-hours"

    channel = guild.get_channel(int(current.get("channel_id") or 0))
    if not isinstance(channel, discord.TextChannel):
        return "missing-channel"
    member = guild.me
    if not isinstance(member, discord.Member):
        return "missing-bot"
    perms = channel.permissions_for(member)
    if not (perms.view_channel and perms.send_messages and perms.embed_links):
        return "no-permission"

    content = None
    allowed_mentions = discord.AllowedMentions.none()
    if current.get("notify_role"):
        # Existing Community & Pings capability, never a raw arbitrary role.
        cfg = await get_guild_config(ident, refresh=True)
        if str(cfg.get("source", "")).startswith("unavailable:"):
            return "config-offline"
        _, notify_id = toke_role_ids(parse_community_pings(cfg), cfg)
        role = guild.get_role(notify_id)
        if not isinstance(role, discord.Role):
            return "no-opt-in-role"
        if not (role.mentionable or perms.mention_everyone):
            return "no-mention-permission"
        content = role.mention
        allowed_mentions = discord.AllowedMentions(
            users=False, roles=[role], everyone=False, replied_user=False,
        )

    from .commands_ext.public_somewhere_420 import build_420_embed
    try:
        await channel.send(
            content=content,
            embed=build_420_embed(public=True),
            allowed_mentions=allowed_mentions,
        )
        return "guild-sent"
    except (discord.Forbidden, discord.HTTPException):
        return "discord-send-failed"


async def _loop(bot: Any) -> None:
    await bot.wait_until_ready()
    semaphore = asyncio.Semaphore(MAX_PARALLEL)
    while not bot.is_closed():
        try:
            claimed = await claim_due(limit=MAX_BATCH)
            async def one(row: dict[str, Any]) -> None:
                async with semaphore:
                    try:
                        outcome = await deliver_claim(bot, row)
                        if outcome not in ("guild-sent", "dm-sent"):
                            print(f"[somewhere420] delivery skipped kind={row.get('kind')} reason={outcome}")
                    except (Somewhere420StorageUnavailable, ValueError, KeyError, OSError, TypeError) as exc:
                        print(f"[somewhere420] delivery failed safely: {type(exc).__name__}")
            if claimed:
                await asyncio.gather(*(one(item) for item in claimed))
        except Somewhere420StorageUnavailable:
            # Migration or service connection not installed. Other bot workers
            # continue normally. Never fall back to unauthenticated DB access.
            pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[somewhere420] bounded claim failure: {type(exc).__name__}")
        await asyncio.sleep(POLL_SECONDS)


def start_somewhere_420_worker(bot: Any) -> asyncio.Task[None]:
    global _TASK
    if _TASK is not None and not _TASK.done():
        return _TASK
    _TASK = asyncio.create_task(_loop(bot), name="somewhere-420-opt-in")
    return _TASK
