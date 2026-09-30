from __future__ import annotations

"""Self-selectable Stoner/Sesh roles plus the member-facing /toke flow."""

import asyncio
import re
import weakref
from collections.abc import Mapping
from time import monotonic
from typing import Any, Optional

import discord
from discord import app_commands

from stoney_verify.community_pings_service import (
    COMMUNITY_PINGS_KEY,
    LEGACY_SESH_PING_ROLE_KEY,
    LEGACY_STONER_ROLE_KEY,
    LEGACY_TOKE_CHANNEL_KEY,
    parse_community_pings,
    toke_role_ids,
)
from stoney_verify.ui.picker import (
    DankChannelSelect,
    DankRoleSelect,
)

STONER_ROLE_KEY = LEGACY_STONER_ROLE_KEY
SESH_PING_ROLE_KEY = LEGACY_SESH_PING_ROLE_KEY
TOKE_CHANNEL_KEY = LEGACY_TOKE_CHANNEL_KEY

TOKE_USER_COOLDOWN_SECONDS = 15 * 60
TOKE_GUILD_COOLDOWN_SECONDS = 5 * 60

_TOKE_USER_LAST: dict[tuple[int, int], float] = {}
_TOKE_GUILD_LAST: dict[int, float] = {}
_TOKE_LOCKS: weakref.WeakValueDictionary[int, asyncio.Lock] = weakref.WeakValueDictionary()
_COMMUNITY_ROLE_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _configured_ids(config: Mapping[str, Any]) -> tuple[int, int, int]:
    return (
        _safe_int(config.get(STONER_ROLE_KEY), 0),
        _safe_int(config.get(SESH_PING_ROLE_KEY), 0),
        _safe_int(config.get(TOKE_CHANNEL_KEY), 0),
    )


def _member_has_role_id(member: Any, role_id: int) -> bool:
    rid = int(role_id or 0)
    if rid <= 0:
        return False
    for role in list(getattr(member, "roles", []) or []):
        if _safe_int(getattr(role, "id", 0), 0) == rid:
            return True
    return False


def _clean_member_message(value: Any) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    text = discord.utils.escape_mentions(text)
    return text[:180]


def _format_wait(seconds: float) -> str:
    remaining = max(1, int(round(seconds)))
    minutes, secs = divmod(remaining, 60)
    if minutes and secs:
        return f"{minutes}m {secs}s"
    if minutes:
        return f"{minutes}m"
    return f"{secs}s"


def _cooldown_remaining(*, now: float, guild_id: int, user_id: int) -> tuple[float, str]:
    user_last = _TOKE_USER_LAST.get((int(guild_id), int(user_id)), 0.0)
    guild_last = _TOKE_GUILD_LAST.get(int(guild_id), 0.0)
    user_remaining = max(0.0, TOKE_USER_COOLDOWN_SECONDS - (now - user_last)) if user_last else 0.0
    guild_remaining = max(0.0, TOKE_GUILD_COOLDOWN_SECONDS - (now - guild_last)) if guild_last else 0.0
    if user_remaining >= guild_remaining and user_remaining > 0:
        return user_remaining, "your /toke cooldown"
    if guild_remaining > 0:
        return guild_remaining, "this server's /toke cooldown"
    return 0.0, ""


def _prune_cooldowns(now: float) -> None:
    horizon = max(TOKE_USER_COOLDOWN_SECONDS, TOKE_GUILD_COOLDOWN_SECONDS) * 2
    for key, ts in list(_TOKE_USER_LAST.items()):
        if now - ts > horizon:
            _TOKE_USER_LAST.pop(key, None)
    for key, ts in list(_TOKE_GUILD_LAST.items()):
        if now - ts > horizon:
            _TOKE_GUILD_LAST.pop(key, None)


def _toke_lock(guild_id: int) -> asyncio.Lock:
    gid = int(guild_id)
    lock = _TOKE_LOCKS.get(gid)
    if lock is None:
        lock = asyncio.Lock()
        _TOKE_LOCKS[gid] = lock
    return lock


def _community_role_lock(guild_id: int, user_id: int) -> asyncio.Lock:
    key = f"{int(guild_id)}:{int(user_id)}"
    lock = _COMMUNITY_ROLE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _COMMUNITY_ROLE_LOCKS[key] = lock
    return lock


def _ping_permission_ready(role: discord.Role, permissions: discord.Permissions) -> bool:
    return bool(role.mentionable or permissions.mention_everyone)


def _toke_allowed_mentions(role: discord.abc.Snowflake) -> discord.AllowedMentions:
    return discord.AllowedMentions(
        users=False,
        roles=[role],
        everyone=False,
        replied_user=False,
    )


async def _config(guild: discord.Guild) -> Mapping[str, Any]:
    from stoney_verify.guild_config import get_guild_config
    return await get_guild_config(int(guild.id), refresh=True)


async def _staff_authorized(interaction: discord.Interaction) -> bool:
    from .public_setup_group import _require_setup_permission
    return bool(await _require_setup_permission(interaction))


def _profile_safe_blocker(
    guild: discord.Guild,
    role: discord.Role,
    config: Mapping[str, Any],
) -> str:
    from .public_self_roles_group import _profile_cosmetic_role_blocker
    return str(_profile_cosmetic_role_blocker(guild, role, config) or "")


async def _save_mapping(
    interaction: discord.Interaction,
    *,
    key: str,
    value: int,
) -> Mapping[str, Any]:
    guild = interaction.guild
    if guild is None:
        return {}
    from stoney_verify.guild_config import upsert_guild_config
    return await upsert_guild_config(
        int(guild.id),
        {
            key: str(int(value)),
            "__config_write_mode": "explicit_override",
            "__config_write_source": "profile_builder_community_pings",
            "__config_write_actor_id": str(getattr(interaction.user, "id", "") or ""),
            "__config_write_allow_keys": [key],
        },
    )


async def _clear_mappings(
    interaction: discord.Interaction,
    keys: tuple[str, ...],
) -> Mapping[str, Any]:
    guild = interaction.guild
    if guild is None:
        return {}
    from stoney_verify.guild_config import clear_guild_config_keys
    return await clear_guild_config_keys(
        int(guild.id),
        keys,
        source="profile builder community pings",
        actor=interaction.user,
    )


async def _reply(interaction: discord.Interaction, content: str, *, ok: bool = False) -> None:
    prefix = "✅ " if ok else "❌ "
    payload = {
        "content": prefix + content,
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if not interaction.response.is_done():
        await interaction.response.send_message(**payload)
    else:
        await interaction.followup.send(**payload)


async def _defer_private(interaction: discord.Interaction) -> None:
    if interaction.response.is_done():
        return
    await interaction.response.defer(ephemeral=True, thinking=True)


async def _defer_update(interaction: discord.Interaction) -> None:
    if interaction.response.is_done():
        return
    await interaction.response.defer()


async def _setup_embed(guild: discord.Guild) -> discord.Embed:
    cfg = await _config(guild)
    stoner_id, ping_id, channel_id = _configured_ids(cfg)
    stoner = guild.get_role(stoner_id) if stoner_id else None
    ping = guild.get_role(ping_id) if ping_id else None
    channel = guild.get_channel(channel_id) if channel_id else None

    embed = discord.Embed(
        title="🌿 Community & Pings",
        description=(
            "Map existing safe server roles. Stoner is the self-selected community role and "
            "controls who may use /toke. Sesh Pings is the opt-in audience that /toke notifies."
        ),
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Stoner role",
        value=stoner.mention if isinstance(stoner, discord.Role) else "Not configured",
        inline=True,
    )
    embed.add_field(
        name="Sesh Pings role",
        value=ping.mention if isinstance(ping, discord.Role) else "Not configured",
        inline=True,
    )
    embed.add_field(
        name="Preferred sesh channel",
        value=channel.mention if isinstance(channel, discord.TextChannel) else "Use the channel where /toke is run",
        inline=False,
    )
    embed.add_field(
        name="Simple mode",
        value="Map Stoner as both roles if every Stoner should receive every /toke ping.",
        inline=False,
    )
    embed.add_field(
        name="Ping safety",
        value=(
            "The command only allows the mapped Sesh Pings role in AllowedMentions. "
            "If that role is not mentionable, Dank Shield needs Mention @everyone, @here, and All Roles "
            "in the target channel so Discord will deliver the role notification."
        ),
        inline=False,
    )
    return embed


class CommunityPingSetupView(discord.ui.View):
    def __init__(self, author_id: int) -> None:
        super().__init__(timeout=900)
        self.author_id = int(author_id)
        self.add_item(
            DankRoleSelect(
                author_id=self.author_id,
                on_pick=self._set_stoner,
                placeholder="Choose the Stoner role…",
                row=0,
            )
        )
        self.add_item(
            DankRoleSelect(
                author_id=self.author_id,
                on_pick=self._set_ping,
                placeholder="Choose the Sesh Pings role…",
                row=1,
            )
        )
        self.add_item(
            DankChannelSelect(
                author_id=self.author_id,
                on_pick=self._set_channel,
                placeholder="Choose a preferred sesh text channel…",
                channel_types=[discord.ChannelType.text, discord.ChannelType.news],
                row=2,
            )
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if _safe_int(getattr(interaction.user, "id", 0), 0) != self.author_id:
            await _reply(interaction, "Only the staff member who opened this setup can use it.")
            return False
        return True

    async def _set_role(
        self,
        interaction: discord.Interaction,
        role: discord.Role,
        *,
        key: str,
    ) -> None:
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside a server.")
        await _defer_update(interaction)
        cfg = await _config(guild)
        blocker = _profile_safe_blocker(guild, role, cfg)
        if blocker:
            return await _reply(interaction, blocker)
        await _save_mapping(interaction, key=key, value=int(role.id))
        await interaction.edit_original_response(
            embed=await _setup_embed(guild),
            view=CommunityPingSetupView(self.author_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _set_stoner(self, interaction: discord.Interaction, role: discord.Role) -> None:
        await self._set_role(interaction, role, key=STONER_ROLE_KEY)

    async def _set_ping(self, interaction: discord.Interaction, role: discord.Role) -> None:
        await self._set_role(interaction, role, key=SESH_PING_ROLE_KEY)

    async def _set_channel(
        self,
        interaction: discord.Interaction,
        channel: discord.abc.GuildChannel,
    ) -> None:
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        if guild is None or not isinstance(channel, discord.TextChannel):
            return await _reply(interaction, "Choose a normal server text channel.")
        await _defer_update(interaction)
        await _save_mapping(interaction, key=TOKE_CHANNEL_KEY, value=int(channel.id))
        await interaction.edit_original_response(
            embed=await _setup_embed(guild),
            view=CommunityPingSetupView(self.author_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Use Stoner for Both", emoji="🌿", style=discord.ButtonStyle.primary, row=3)
    async def same_role(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside a server.")
        await _defer_update(interaction)
        cfg = await _config(guild)
        stoner_id, _ping_id, _channel_id = _configured_ids(cfg)
        role = guild.get_role(stoner_id) if stoner_id else None
        if not isinstance(role, discord.Role):
            return await _reply(interaction, "Choose the Stoner role first.")
        blocker = _profile_safe_blocker(guild, role, cfg)
        if blocker:
            return await _reply(interaction, blocker)
        await _save_mapping(interaction, key=SESH_PING_ROLE_KEY, value=int(role.id))
        await interaction.edit_original_response(
            embed=await _setup_embed(guild),
            view=CommunityPingSetupView(self.author_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Clear Preferred Channel", emoji="🧹", style=discord.ButtonStyle.secondary, row=3)
    async def clear_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside a server.")
        await _defer_update(interaction)
        await _clear_mappings(interaction, (TOKE_CHANNEL_KEY,))
        await interaction.edit_original_response(
            embed=await _setup_embed(guild),
            view=CommunityPingSetupView(self.author_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Clear Role Mappings", emoji="🗑️", style=discord.ButtonStyle.danger, row=3)
    async def clear_roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside a server.")
        await _defer_update(interaction)
        await _clear_mappings(interaction, (STONER_ROLE_KEY, SESH_PING_ROLE_KEY))
        await interaction.edit_original_response(
            embed=await _setup_embed(guild),
            view=CommunityPingSetupView(self.author_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=4)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside a server.")
        await _defer_update(interaction)
        await interaction.edit_original_response(
            embed=await _setup_embed(guild),
            view=CommunityPingSetupView(self.author_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )


async def open_toke_preset_setup(
    interaction: discord.Interaction,
    *,
    replace_message: bool = False,
) -> None:
    if not await _staff_authorized(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _reply(interaction, "This only works inside a server.")
    if replace_message:
        await _defer_update(interaction)
    else:
        await _defer_private(interaction)
    await interaction.edit_original_response(
        embed=await _setup_embed(guild),
        view=CommunityPingSetupView(int(interaction.user.id)),
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def open_community_ping_setup(
    interaction: discord.Interaction,
    *,
    replace_message: bool = False,
) -> None:
    from .public_community_pings import open_community_ping_setup as open_generic_manager

    await open_generic_manager(interaction, replace_message=replace_message)


async def open_member_community_pings(
    interaction: discord.Interaction,
    *,
    replace_message: bool = False,
) -> None:
    from .public_community_pings import open_member_community_pings as open_generic_member

    await open_generic_member(interaction, replace_message=replace_message)


class TokeCheersView(discord.ui.View):
    def __init__(self, starter_id: int, stoner_role_id: int) -> None:
        super().__init__(timeout=15 * 60)
        self.starter_id = int(starter_id)
        self.stoner_role_id = int(stoner_role_id)
        self.cheered_ids: set[int] = set()
        self.cheers_count = 0

    @discord.ui.button(label="Cheers", emoji="💨", style=discord.ButtonStyle.success)
    async def cheers(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        user_id = _safe_int(getattr(member, "id", 0), 0)
        if member is None or user_id <= 0 or not _member_has_role_id(member, self.stoner_role_id):
            return await _reply(interaction, "The configured Stoner role is required to join this cheers.")
        if user_id in self.cheered_ids:
            return await _reply(interaction, "You already sent cheers on this call.")
        self.cheered_ids.add(user_id)
        self.cheers_count += 1
        button.label = f"Cheers · {self.cheers_count}"
        await interaction.response.edit_message(view=self)


def _target_channel(
    guild: discord.Guild,
    interaction: discord.Interaction,
    configured_channel_id: int,
) -> Optional[discord.TextChannel]:
    if configured_channel_id > 0:
        channel = guild.get_channel(int(configured_channel_id))
        return channel if isinstance(channel, discord.TextChannel) else None
    channel = interaction.channel
    return channel if isinstance(channel, discord.TextChannel) else None


@app_commands.describe(message="Optional short message to include with the sesh ping.")
async def open_toke_command(
    interaction: discord.Interaction,
    message: Optional[str] = None,
) -> None:
    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if guild is None or member is None:
        return await _reply(interaction, "/toke only works inside a server.")

    await _defer_private(interaction)
    cfg = await _config(guild)
    community_model = parse_community_pings(cfg)
    stoner_id, ping_id = toke_role_ids(community_model, cfg)
    _legacy_stoner_id, _legacy_ping_id, channel_id = _configured_ids(cfg)
    if stoner_id <= 0 or ping_id <= 0:
        return await _reply(
            interaction,
            "This server has not configured the /toke starter and notification roles in Community & Pings.",
        )

    stoner_role = guild.get_role(stoner_id)
    ping_role = guild.get_role(ping_id)
    if not isinstance(stoner_role, discord.Role) or not isinstance(ping_role, discord.Role):
        return await _reply(
            interaction,
            "A configured /toke Community & Pings role no longer exists. Staff should repair the mapping.",
        )
    if not _member_has_role_id(member, stoner_id):
        return await _reply(interaction, f"You need the configured Community & Pings starter role {stoner_role.mention} to use /toke.")
    channel = _target_channel(guild, interaction, channel_id)
    if not isinstance(channel, discord.TextChannel):
        return await _reply(
            interaction,
            "The preferred sesh channel is missing, or /toke was used outside a normal text channel.",
        )

    me = guild.me
    if not isinstance(me, discord.Member):
        return await _reply(interaction, "Dank Shield could not resolve its server permissions.")
    perms = channel.permissions_for(me)
    if not (perms.view_channel and perms.send_messages and perms.embed_links):
        return await _reply(interaction, f"Dank Shield cannot post the sesh card in {channel.mention}.")
    if not _ping_permission_ready(ping_role, perms):
        return await _reply(
            interaction,
            f"{ping_role.mention} is not mentionable and Dank Shield lacks Mention @everyone, @here, and All Roles in {channel.mention}.",
        )

    now = monotonic()
    _prune_cooldowns(now)
    remaining, label = _cooldown_remaining(now=now, guild_id=guild.id, user_id=member.id)
    if remaining > 0:
        return await _reply(interaction, f"Wait {_format_wait(remaining)} for {label}.")

    clean_message = _clean_member_message(message)
    async with _toke_lock(int(guild.id)):
        now = monotonic()
        remaining, label = _cooldown_remaining(now=now, guild_id=guild.id, user_id=member.id)
        if remaining > 0:
            return await _reply(interaction, f"Wait {_format_wait(remaining)} for {label}.")

        embed = discord.Embed(
            title="💨 Toke Time",
            description=(
                f"**{discord.utils.escape_markdown(member.display_name)}** is calling the sesh crowd."
                + (f"\n\n> {discord.utils.escape_markdown(clean_message)}" if clean_message else "")
            ),
            color=discord.Color.green(),
            timestamp=discord.utils.utcnow(),
        )
        embed.set_footer(text="Only members with the configured /toke notification role were notified.")
        try:
            sent = await channel.send(
                content=ping_role.mention,
                embed=embed,
                view=TokeCheersView(member.id, stoner_role.id),
                allowed_mentions=_toke_allowed_mentions(ping_role),
            )
        except discord.Forbidden:
            return await _reply(interaction, f"Discord blocked the sesh ping in {channel.mention}.")
        except discord.HTTPException as exc:
            return await _reply(interaction, f"Discord could not send the sesh ping: {type(exc).__name__}.")

        stamp = monotonic()
        _TOKE_USER_LAST[(int(guild.id), int(member.id))] = stamp
        _TOKE_GUILD_LAST[int(guild.id)] = stamp

    await interaction.followup.send(
        f"💨 Toke call sent in {channel.mention}: {sent.jump_url}",
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


__all__ = [
    "CommunityPingSetupView",
    "SESH_PING_ROLE_KEY",
    "STONER_ROLE_KEY",
    "TOKE_CHANNEL_KEY",
    "TOKE_GUILD_COOLDOWN_SECONDS",
    "TOKE_USER_COOLDOWN_SECONDS",
    "TokeCheersView",
    "_clean_member_message",
    "_configured_ids",
    "_cooldown_remaining",
    "_member_has_role_id",
    "_ping_permission_ready",
    "_toke_allowed_mentions",
    "open_community_ping_setup",
    "open_member_community_pings",
    "open_toke_preset_setup",
    "open_toke_command",
]
