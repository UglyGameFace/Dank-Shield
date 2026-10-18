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

from stoney_verify.guild_config import (
    compare_and_swap_guild_config_key,
    get_guild_config,
    upsert_guild_config,
)
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
    try:
        panel = await _publish_panel(channel, previous)
    except discord.HTTPException as exc:
        return await interaction.followup.send(
            f"Role/channel saved, but the Call the Crew panel could not be posted: {type(exc).__name__}. "
            "Check channel permissions and run /dank cheers configure again.",
            ephemeral=True,
        )
    final = {**setting, "panel_message_id": str(panel.id)}
    persisted = await _save(guild.id, final)
    return await interaction.followup.send(
        f"CHEERS is configured for {role.mention} in {channel.mention}. "
        f"Members can use the persistent **CALL THE CREW** button: {panel.jump_url}. "
        f"Minimum role-ping cooldown: {cooldown_minutes} minutes. "
        "Only the application owner can change this setup. /toke is unaffected."
        + ("" if persisted else " ⚠️ Panel ID persistence failed; rerunning setup may repost the panel."),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _deliver_recruitment_ping(guild: discord.Guild) -> str:
    """Shared command/button sender: fresh settings and one per-guild throttle."""
    lock = _locks.get(guild.id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[guild.id] = lock
    async with lock:
        settings = _settings(await get_guild_config(guild.id, refresh=True))
        if not settings.get("enabled"):
            return "CHEERS is disabled or not configured. Ask the bot owner to use /dank cheers configure."
        try:
            role_id = int(settings.get("role_id", 0) or 0)
            channel_id = int(settings.get("channel_id", 0) or 0)
            minutes = int(settings.get("cooldown_minutes", 30) or 30)
            last_sent_at = int(settings.get("last_sent_at", 0) or 0)
        except (TypeError, ValueError):
            return "CHEERS setup is invalid. Owner must reconfigure its role and channel."
        role, channel, error = _valid_destination(guild, role_id, channel_id)
        if error:
            return error
        now = int(time.time())
        cooldown = max(_MIN_COOLDOWN, min(_MAX_COOLDOWN, minutes)) * 60
        remaining = cooldown - (now - last_sent_at)
        if remaining > 0:
            return f"Next CHEERS role ping available in {(remaining + 59) // 60} minute(s)."
        embed = discord.Embed(
            title="🔥 CHEERS: SPARK RELAY",
            description=(
                "Players wanted! Join an online reaction game, catch the spark, "
                "and help the crew beat the high score.\n\n"
                f"**[PLAY ON THE 420 LOBBY]({LOBBY_URL})**\n"
                "Sign in with Discord, go online in the lobby, and join a round."
            ),
            color=discord.Color.from_rgb(180, 238, 132),
        )
        embed.set_footer(text="Website CHEERS • Not a /toke sesh notification")
        # Atomically claim the cooldown in shared guild storage before any
        # non-idempotent Discord mention. Process-local locks alone cannot
        # prevent a second bot worker/shard from pinging the same guild.
        # If the RPC is missing or the claim loses a race, fail closed.
        try:
            reserved, _config = await compare_and_swap_guild_config_key(
                guild.id, KEY,
                expected=settings,
                value={**settings, "last_sent_at": now},
                source="cheers_role_ping_claim",
            )
        except Exception as exc:
            print(f"⚠️ cheers cooldown claim guild={guild.id} error={type(exc).__name__}")
            return "CHEERS cooldown storage is unavailable. No role ping was sent."
        if not reserved:
            return "Another CHEERS invitation is already being handled. Try after the cooldown."
        try:
            sent = await channel.send(
                content=role.mention,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(everyone=False, users=False, roles=[role]),
            )
        except discord.HTTPException as exc:
            return (
                f"Could not confirm the CHEERS announcement: {type(exc).__name__}. "
                "The cooldown remains reserved to prevent duplicate role pings."
            )
        return f"CHEERS invitation posted in {channel.mention}: {sent.jump_url}"


class CheersCallView(discord.ui.View):
    """Persistent member-accessible button. No website webhook or bot token."""
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="CALL THE CREW", style=discord.ButtonStyle.success,
        custom_id="dank_shield:cheers:call_crew:v1",
    )
    async def call_crew(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if (interaction.guild is None or not isinstance(interaction.user, discord.Member)
                or interaction.user.bot):
            return await interaction.response.send_message(
                "Use this button inside your Discord server.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await _deliver_recruitment_ping(interaction.guild)
        except Exception as exc:
            print(f"⚠️ cheers_call_crew failure guild={interaction.guild.id} type={type(exc).__name__}")
            result = "CHEERS invitations are temporarily unavailable. Try later."
        await interaction.followup.send(
            result, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )


def _panel_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🔥 THE 420 LOBBY | CHEERS",
        description=(
            "Want other players for Spark Relay? Tap **CALL THE CREW** to invite "
            "the server's configured CHEERS role. Shared cooldowns prevent spam.\n\n"
            f"**[JOIN THE ONLINE GAME]({LOBBY_URL})**"
        ),
        color=discord.Color.from_rgb(185, 245, 151),
    )
    embed.set_footer(text="Role pings need owner configuration • /toke stays separate")
    return embed


async def _publish_panel(channel: discord.TextChannel, previous: Mapping) -> discord.Message:
    previous_channel = str(previous.get("channel_id", ""))
    previous_id = str(previous.get("panel_message_id", "") or "")
    if previous_channel == str(channel.id) and previous_id.isdecimal():
        try:
            existing = await channel.fetch_message(int(previous_id))
            await existing.edit(
                embed=_panel_embed(), view=CheersCallView(),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return existing
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
    return await channel.send(
        embed=_panel_embed(), view=CheersCallView(),
        allowed_mentions=discord.AllowedMentions.none(),
    )


@cheers_group.command(name="ping", description="Owner only: manually call the configured role to play CHEERS.")
async def cheers_ping(interaction: discord.Interaction) -> None:
    if not await _only_app_owner(interaction):
        return
    await interaction.response.defer(ephemeral=True, thinking=True)
    try:
        message = await _deliver_recruitment_ping(interaction.guild)
    except Exception as exc:
        print(f"⚠️ cheers_owner_ping failure guild={interaction.guild.id} type={type(exc).__name__}")
        message = "CHEERS invitation failed. Check bot permissions and guild-config storage."
    return await interaction.followup.send(
        message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
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
    _bot.add_view(CheersCallView())
    _registered = True
