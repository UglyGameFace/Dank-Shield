from __future__ import annotations

"""Application-owner-only Discord announcement configuration for website CHEERS.

This does not affect /toke and cannot be invoked by website visitors.
Role mention is validated against guild and channel permissions on every send.
"""

import asyncio
import time
import weakref
from collections.abc import Mapping

import discord
from discord import app_commands

from stoney_verify.guild_config import get_guild_config, upsert_guild_config
from .public_setup_group import dank_group

KEY = "cheers_lobby_v1"
LOBBY_URL = "https://the420lobby.com/#spark-relay"
_MIN_COOLDOWN = 15
_MAX_COOLDOWN = 240
_locks: weakref.WeakValueDictionary[int, asyncio.Lock] = weakref.WeakValueDictionary()
_registered = False

cheers_group = app_commands.Group(
    name="cheers",
    description="Owner-only settings and community calls for website CHEERS.",
)


def _settings(config: Mapping) -> dict:
    raw = config.get(KEY)
    if not isinstance(raw, Mapping):
        return {}
    return dict(raw)


async def _only_app_owner(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        await interaction.response.send_message("Run this command in your Discord server.", ephemeral=True)
        return False
    try:
        if await interaction.client.is_owner(interaction.user):
            return True
    except Exception:
        pass
    await interaction.response.send_message(
        "Only the Dank Shield application owner can configure or ping the CHEERS role.",
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    return False


async def _save(guild_id: int, setting: dict) -> bool:
    try:
        updated = await upsert_guild_config(
            guild_id, {KEY: setting, "__config_write_source": "cheers_owner_lobby"}
        )
        if str(updated.get("source", "")).startswith("unavailable:"):
            return False
        actual = _settings(updated)
        return actual == setting
    except Exception as exc:
        print(f"⚠️ cheers_lobby config save failed guild={guild_id} error={type(exc).__name__}")
        return False


def _valid_destination(
    guild: discord.Guild, role_id: int, channel_id: int
) -> tuple[discord.Role | None, discord.TextChannel | None, str]:
    role = guild.get_role(int(role_id))
    channel = guild.get_channel(int(channel_id))
    if not isinstance(role, discord.Role) or role.is_default() or role.managed:
        return None, None, "Pick a normal, assignable server role (never @everyone or an integration role)."
    if not isinstance(channel, discord.TextChannel):
        return None, None, "Pick a normal server text channel."
    me = guild.me
    if not isinstance(me, discord.Member):
        return None, None, "Bot membership and permissions are not available."
    perms = channel.permissions_for(me)
    if not (perms.view_channel and perms.send_messages and perms.embed_links):
        return None, None, "Dank Shield needs View Channel, Send Messages, and Embed Links."
    if not (role.mentionable or perms.mention_everyone):
        return None, None, "Make that role mentionable, or allow Dank Shield to Mention All Roles in this channel."
    return role, channel, ""


@cheers_group.command(name="configure", description="Owner only: choose a CHEERS ping role and announcement channel.")
@app_commands.describe(role="Your CHEERS recruitment role", channel="Where the game invitation will be posted",
                       cooldown_minutes="Minimum minutes between announcements (15–240).")
async def cheers_configure(
    interaction: discord.Interaction,
    role: discord.Role,
    channel: discord.TextChannel,
    cooldown_minutes: app_commands.Range[int, 15, 240] = 30,
) -> None:
    if not await _only_app_owner(interaction):
        return
    guild = interaction.guild
    if role.guild.id != guild.id or channel.guild.id != guild.id:
        return await interaction.response.send_message("Role and channel must belong to this server.", ephemeral=True)
    _, _, error = _valid_destination(guild, role.id, channel.id)
    if error:
        return await interaction.response.send_message(error, ephemeral=True)
    await interaction.response.defer(ephemeral=True, thinking=True)
    previous = _settings(await get_guild_config(guild.id, refresh=True))
    setting = {
        "enabled": True, "role_id": str(role.id), "channel_id": str(channel.id),
        "cooldown_minutes": int(cooldown_minutes),
        "last_sent_at": int(previous.get("last_sent_at", 0) or 0),
    }
    if not await _save(guild.id, setting):
        return await interaction.followup.send(
            "Could not persist CHEERS settings. No role pings were sent. Check guild-config storage.",
            ephemeral=True,
        )
    return await interaction.followup.send(
        f"CHEERS announcement configured for {role.mention} in {channel.mention}. "
        f"Cooldown: {cooldown_minutes} minutes. Use **/dank cheers ping** to invite people to {LOBBY_URL}. "
        "This is separate from /toke.",
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@cheers_group.command(name="ping", description="Owner only: notify your configured role to join Spark Relay.")
async def cheers_ping(interaction: discord.Interaction) -> None:
    if not await _only_app_owner(interaction):
        return
    guild = interaction.guild
    await interaction.response.defer(ephemeral=True, thinking=True)
    lock = _locks.get(guild.id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[guild.id] = lock
    async with lock:
        settings = _settings(await get_guild_config(guild.id, refresh=True))
        if not settings.get("enabled"):
            return await interaction.followup.send("CHEERS is disabled or not configured. Use /dank cheers configure.", ephemeral=True)
        role, channel, error = _valid_destination(
            guild, int(settings.get("role_id", 0) or 0), int(settings.get("channel_id", 0) or 0)
        )
        if error:
            return await interaction.followup.send(error, ephemeral=True)
        now = int(time.time())
        cooldown = max(_MIN_COOLDOWN,min(_MAX_COOLDOWN,int(settings.get("cooldown_minutes",30) or 30))) * 60
        remaining = cooldown - (now - int(settings.get("last_sent_at",0) or 0))
        if remaining > 0:
            return await interaction.followup.send(
                f"Next CHEERS role ping available in {(remaining+59)//60} minute(s).", ephemeral=True
            )
        embed = discord.Embed(
            title="🔥 CHEERS: SPARK RELAY",
            description=(
                "Crew needed! Join a live reaction game, pass the spark, "
                "chase the high score, and beat the timed challenges.\n\n"
                f"**[PLAY ON THE 420 LOBBY]({LOBBY_URL})**\n"
                "Sign in with Discord to join the live player lobby."
            ),
            color=discord.Color.from_rgb(180, 238, 132),
        )
        embed.set_footer(text="CHEERS website game • Separate from /toke sesh alerts")
        try:
            sent = await channel.send(
                content=role.mention,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=[role]),
            )
        except discord.HTTPException as exc:
            return await interaction.followup.send(
                f"Could not post CHEERS invitation: {type(exc).__name__}. Check permissions.",
                ephemeral=True,
            )
        updated = {**settings, "last_sent_at": now}
        saved = await _save(guild.id, updated)
        return await interaction.followup.send(
            f"CHEERS recruitment ping sent in {channel.mention}: {sent.jump_url}"
            + ("" if saved else " ⚠️ Cooldown persistence failed; repair guild-config storage before another ping."),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


@cheers_group.command(name="status", description="Owner only: show CHEERS announcement settings.")
async def cheers_status(interaction: discord.Interaction) -> None:
    if not await _only_app_owner(interaction):
        return
    settings = _settings(await get_guild_config(interaction.guild.id, refresh=True))
    if not settings:
        return await interaction.response.send_message("CHEERS is not configured. Use /dank cheers configure.", ephemeral=True)
    return await interaction.response.send_message(
        f"Enabled: **{bool(settings.get('enabled'))}**\n"
        f"Role ID: `{settings.get('role_id', '')}`\nChannel ID: `{settings.get('channel_id', '')}`\n"
        f"Cooldown: **{settings.get('cooldown_minutes',30)} minutes**\n"
        f"Last ping: `{int(settings.get('last_sent_at',0))}` (Unix time)",
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@cheers_group.command(name="disable", description="Owner only: disable CHEERS role announcements.")
async def cheers_disable(interaction: discord.Interaction) -> None:
    if not await _only_app_owner(interaction):
        return
    current = _settings(await get_guild_config(interaction.guild.id, refresh=True))
    if not current:
        return await interaction.response.send_message("CHEERS was not configured.", ephemeral=True)
    if not await _save(interaction.guild.id,{**current,"enabled":False}):
        return await interaction.response.send_message("Could not save disabled status. No change confirmed.",ephemeral=True)
    return await interaction.response.send_message("CHEERS role notifications disabled. /toke is untouched.",ephemeral=True)


def register_public_cheers_owner_commands(_bot, tree) -> None:
    global _registered
    if _registered:
        return
    dank_group.add_command(cheers_group)
    _registered = True
