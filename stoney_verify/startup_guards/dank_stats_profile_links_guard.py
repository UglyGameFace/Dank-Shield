from __future__ import annotations

"""Permanent convergence guard for Dank Stats and member profile links.

The normal stats service remains the single owner of names, counts, channel
creation, and durable channel IDs. This guard adds the missing eventual-
consistency boundary: enabled displays are force-reconciled on ready and every
two minutes, so a Discord rename failure, deleted channel, stale saved ID, or
missed lifecycle event cannot leave the category broken indefinitely.

Join/leave cards no longer render a raw ``<@id>`` mention inside an embed. On
Discord mobile that mention can open the misleading "you do not have access"
popup when the client cannot resolve its local member object. The embed title
now owns the canonical Discord profile URL and the body keeps a copyable ID.
"""

import asyncio
from typing import Any, Optional

import discord
from discord.ext import tasks

from stoney_verify.globals import bot

_RECONCILE_MINUTES = 2
_INSTALLED = False


def _log(message: str) -> None:
    try:
        print(f"📊 dank_stats_profile_links_guard {message}")
    except Exception:
        pass


def _profile_url(user_id: int) -> str:
    return f"https://discord.com/users/{int(user_id)}"


def _display_name(member: Any) -> str:
    return str(
        getattr(member, "display_name", None)
        or getattr(member, "global_name", None)
        or getattr(member, "name", None)
        or f"User {getattr(member, 'id', 'unknown')}"
    )


def _avatar_url(member: Any) -> str:
    try:
        return str(member.display_avatar.url)
    except Exception:
        return ""


def _member_count(member: Any) -> str:
    try:
        value = getattr(member.guild, "member_count", None)
        return str(int(value)) if value is not None else "unknown"
    except Exception:
        return "unknown"


async def _send_join(member: discord.Member, channel: Optional[discord.TextChannel]) -> None:
    from stoney_verify.startup_guards import member_lifecycle_router_guard as router

    if not router._bot_can_send(channel):
        router._log(
            f"join log skipped guild={member.guild.id} member={member.id}: "
            "join/leave target missing or not writable"
        )
        return

    name = _display_name(member)
    embed = discord.Embed(
        title=f"👋 {name} joined",
        url=_profile_url(int(member.id)),
        description=(
            f"Member: **{discord.utils.escape_markdown(name)}**\n"
            f"User ID: `{int(member.id)}`\n"
            f"Members now: **{_member_count(member)}**."
        ),
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow(),
    )
    avatar = _avatar_url(member)
    if avatar:
        embed.set_thumbnail(url=avatar)
    embed.set_footer(text="Tap the member name to open their Discord profile • dank_shield:join_leave_event:v4")
    await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    router._log(f"join log sent guild={member.guild.id} member={member.id} channel={channel.id} profile_link=v4")


async def _send_leave(member: discord.Member, channel: Optional[discord.TextChannel]) -> None:
    from stoney_verify.startup_guards import member_lifecycle_router_guard as router

    if not router._bot_can_send(channel):
        router._log(
            f"leave log skipped guild={member.guild.id} member={member.id}: "
            "join/leave target missing or not writable"
        )
        return

    name = _display_name(member)
    embed = discord.Embed(
        title=f"👋 {name} left",
        url=_profile_url(int(member.id)),
        description=(
            f"Member: **{discord.utils.escape_markdown(name)}**\n"
            f"User ID: `{int(member.id)}`\n"
            f"Members now: **{_member_count(member)}**."
        ),
        color=discord.Color.dark_gray(),
        timestamp=discord.utils.utcnow(),
    )
    avatar = _avatar_url(member)
    if avatar:
        embed.set_thumbnail(url=avatar)
    embed.set_footer(text="Tap the member name to open their Discord profile • dank_shield:join_leave_event:v4")
    await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    router._log(f"leave log sent guild={member.guild.id} member={member.id} channel={channel.id} profile_link=v4")


async def _reconcile_one(guild: discord.Guild) -> bool:
    try:
        from stoney_verify.security_stats import refresh_security_stats_display

        return bool(await refresh_security_stats_display(guild, force=True))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        _log(f"reconcile failed guild={getattr(guild, 'id', 'unknown')} error={type(exc).__name__}: {exc}")
        return False


@tasks.loop(minutes=_RECONCILE_MINUTES)
async def _convergence_loop() -> None:
    for guild in list(getattr(bot, "guilds", []) or []):
        await _reconcile_one(guild)


@_convergence_loop.before_loop
async def _before_convergence_loop() -> None:
    await bot.wait_until_ready()


def _patch_member_cards() -> None:
    from stoney_verify.startup_guards import member_lifecycle_router_guard as router

    router._send_join_leave_join = _send_join
    router._send_public_join = _send_join
    router._send_public_leave = _send_leave


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _patch_member_cards()
    if not _convergence_loop.is_running():
        _convergence_loop.start()
    _INSTALLED = True
    _log("active profile_links=v4 stats_reconcile_minutes=2")


install()
