from __future__ import annotations

"""Restart-safe birthday worker: bounded due claims, opt-in sends, leased role cleanup."""

import asyncio
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

import discord

from .birthday_service import (
    BirthdayStorageUnavailable, birthday_role_expires, claim_delivery,
    claim_due, claim_expired_roles, clear_role_lease, get_guild,
    members_for_day, save_role_lease,
)

_TASK: asyncio.Task[None] | None = None
POLL_SECONDS = 60
MAX_PARALLEL = 4


def _manageable_role(guild: discord.Guild, role: discord.Role) -> bool:
    me = guild.me
    if me is None or role.managed or role.is_default():
        return False
    if not (me.guild_permissions.manage_roles or me.guild_permissions.administrator):
        return False
    return role < me.top_role and not (
        role.permissions.administrator or role.permissions.manage_guild
        or role.permissions.manage_roles or role.permissions.manage_channels
        or role.permissions.ban_members or role.permissions.kick_members
        or role.permissions.moderate_members
    )


async def _deliver_guild(bot: Any, claim: dict[str, Any]) -> None:
    guild_id = int(claim["guild_id"])
    guild = bot.get_guild(guild_id)
    if not isinstance(guild, discord.Guild):
        return
    settings = await get_guild(guild_id)
    if not settings or not settings.get("enabled"):
        return
    # A manager's intervening edits win over a previously-claimed batch.
    if settings.get("updated_at") != claim.get("updated_at"):
        return
    zone = ZoneInfo(str(settings["timezone"]))
    now = datetime.now(timezone.utc)
    local_day = now.astimezone(zone).date()
    if str(claim.get("last_local_date")) != local_day.isoformat():
        return
    scheduled = datetime.combine(
        local_day, time(hour=int(settings.get("announce_hour", 9))), tzinfo=zone
    ).astimezone(timezone.utc)
    if not 0 <= (now - scheduled).total_seconds() <= 3 * 3600:
        return  # No stale birthday blasts after downtime.
    channel = guild.get_channel(int(settings.get("channel_id") or 0))
    me = guild.me
    if not isinstance(channel, discord.TextChannel) or not isinstance(me, discord.Member):
        return
    permissions = channel.permissions_for(me)
    channel_ok = bool(permissions.view_channel and permissions.send_messages and permissions.embed_links)
    role = guild.get_role(int(settings.get("role_id") or 0))
    role_ok = isinstance(role, discord.Role) and _manageable_role(guild, role)
    rows = await members_for_day(guild_id, local_day, str(settings.get("leap_day_policy", "feb28")))
    for row in rows:
        user_id = int(row["user_id"])
        member = guild.get_member(user_id)
        if member is None or member.bot:
            continue
        if channel_ok and row.get("public_wishes"):
            if await claim_delivery(guild_id, user_id, local_day, "announce"):
                embed = discord.Embed(
                    title="🎂 Happy Birthday!",
                    description=f"Today we're celebrating **{discord.utils.escape_markdown(member.display_name)}**! 🎉",
                    color=discord.Color.gold(),
                )
                embed.set_thumbnail(url=member.display_avatar.url)
                embed.set_footer(text="Dank Shield · Birthday celebrations")
                try:
                    await channel.send(
                        embed=embed, allowed_mentions=discord.AllowedMentions.none()
                    )
                except (discord.Forbidden, discord.HTTPException) as exc:
                    print(f"[birthdays] announcement failed guild={guild_id}: {type(exc).__name__}")
        if role_ok and row.get("birthday_role") and role not in member.roles:
            if await claim_delivery(guild_id, user_id, local_day, "role"):
                # Store expiry before assignment so restart can always remove it.
                await save_role_lease(
                    guild_id, user_id, role.id, birthday_role_expires(str(settings["timezone"]), local_day)
                )
                try:
                    await member.add_roles(role, reason="Opt-in birthday role until local midnight")
                except (discord.Forbidden, discord.HTTPException) as exc:
                    print(f"[birthdays] role grant failed guild={guild_id}: {type(exc).__name__}")
        if row.get("dm_wishes") and await claim_delivery(guild_id, user_id, local_day, "dm"):
            try:
                await member.send(
                    f"🎂 Happy birthday! Wishing you an excellent day from {guild.name}.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.HTTPException):
                pass


async def _remove_expired_role(bot: Any, lease: dict[str, Any]) -> None:
    guild_id, user_id, role_id = (
        int(lease["guild_id"]), int(lease["user_id"]), int(lease["role_id"])
    )
    guild = bot.get_guild(guild_id)
    if guild is None:
        return  # Reclaim after the retry lease; don't lose recovery state.
    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except discord.NotFound:
            await clear_role_lease(guild_id, user_id)
            return
        except (discord.Forbidden, discord.HTTPException):
            return
    role = guild.get_role(role_id)
    if role is None or role not in member.roles:
        await clear_role_lease(guild_id, user_id)
        return
    if not _manageable_role(guild, role):
        print(f"[birthdays] role cleanup held by hierarchy guild={guild_id}")
        return
    try:
        await member.remove_roles(role, reason="Birthday role expired at local midnight")
    except (discord.Forbidden, discord.HTTPException):
        return
    await clear_role_lease(guild_id, user_id)


async def _loop(bot: Any) -> None:
    await bot.wait_until_ready()
    semaphore = asyncio.Semaphore(MAX_PARALLEL)
    while not bot.is_closed():
        try:
            guilds, expired = await asyncio.gather(claim_due(limit=24), claim_expired_roles(limit=24))
            async def guarded(operation, row: dict[str, Any]) -> None:
                async with semaphore:
                    try:
                        await operation(bot, row)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        print(f"[birthdays] delivery/recovery failed safely: {type(exc).__name__}")
            await asyncio.gather(
                *(guarded(_deliver_guild, row) for row in guilds),
                *(guarded(_remove_expired_role, row) for row in expired),
            )
        except asyncio.CancelledError:
            raise
        except BirthdayStorageUnavailable as exc:
            print(f"[birthdays] storage unavailable; retrying in 5 minutes: {exc}")
            await asyncio.sleep(300)
            continue
        except Exception as exc:
            print(f"[birthdays] worker failed safely: {type(exc).__name__}")
        await asyncio.sleep(POLL_SECONDS)


def start_birthday_worker(bot: Any) -> asyncio.Task[None]:
    global _TASK
    if _TASK is None or _TASK.done():
        _TASK = asyncio.create_task(_loop(bot), name="dank-native-birthdays")
    return _TASK
