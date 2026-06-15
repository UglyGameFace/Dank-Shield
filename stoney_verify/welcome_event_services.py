from __future__ import annotations

"""Owned setup service for ProBot-style join/leave messages.

The static welcome/start-here message stays separate. This service gives every
server a clear /dank setup path for optional join and leave announcements.
"""

from typing import Any, Optional

import discord


async def _send_ephemeral(interaction: discord.Interaction, content: str = "", **kwargs: Any) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(content, ephemeral=True, **kwargs)
        else:
            await interaction.followup.send(content, ephemeral=True, **kwargs)
    except Exception:
        pass


async def _defer(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    return default


def _cfg_bool(cfg: Any, key: str, default: bool = False) -> bool:
    try:
        raw = _cfg_value(cfg, key, None)
        if raw is None:
            return bool(default)
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}
    except Exception:
        return bool(default)


def _cfg_int(cfg: Any, key: str, default: int = 0) -> int:
    try:
        raw = _cfg_value(cfg, key, None)
        if raw is None or isinstance(raw, bool):
            return int(default)
        text = str(raw).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _can_post(channel: discord.TextChannel, me: Optional[discord.Member]) -> list[str]:
    if me is None:
        return ["Bot member unavailable"]
    perms = channel.permissions_for(me)
    checks = {
        "View Channel": perms.view_channel,
        "Send Messages": perms.send_messages,
        "Embed Links": perms.embed_links,
        "Read Message History": perms.read_message_history,
    }
    return [name for name, ok in checks.items() if not ok]


def _preview_embed(guild: discord.Guild, member: discord.Member, *, kind: str) -> discord.Embed:
    if kind == "leave":
        title = f"👋 {member.display_name} left"
        body = f"{member.display_name} left **{guild.name}**. Member count: {getattr(guild, 'member_count', '') or 'unknown'}."
        color = discord.Color.dark_grey()
    else:
        title = f"👋 Welcome, {member.display_name}!"
        body = f"Welcome to **{guild.name}**, {member.mention}! Head to the start-here channels to get settled."
        color = discord.Color.green()
    embed = discord.Embed(title=title, description=body, color=color, timestamp=discord.utils.utcnow())
    try:
        embed.set_thumbnail(url=member.display_avatar.url)
    except Exception:
        pass
    embed.set_footer(text="Preview • dank_shield:welcome_event:v1")
    return embed


async def _save_event_channel(interaction: discord.Interaction, channel: discord.TextChannel, *, kind: str) -> None:
    from stoney_verify.commands_ext.public_setup_group import _require_setup_permission, _upsert_config
    from stoney_verify.guild_config import invalidate_guild_config

    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send_ephemeral(interaction, "❌ This must be used inside a server.")
    await _defer(interaction)
    me = guild.me if isinstance(guild.me, discord.Member) else None
    missing = _can_post(channel, me)
    if missing:
        return await interaction.followup.send(
            f"❌ I cannot post join/leave messages in {channel.mention}. Missing: {', '.join(missing)}.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    if kind == "leave":
        patch = {
            "goodbye_channel_id": str(int(channel.id)),
            "leave_channel_id": str(int(channel.id)),
            "welcome_leave_enabled": True,
            "goodbye_enabled": True,
            "leave_message_enabled": True,
            "welcome_events_updated_by_id": str(int(interaction.user.id)),
        }
        label = "leave/goodbye"
    else:
        patch = {
            "join_welcome_channel_id": str(int(channel.id)),
            "welcome_join_enabled": True,
            "join_welcome_enabled": True,
            "welcome_events_updated_by_id": str(int(interaction.user.id)),
        }
        label = "join/welcome"
    await _upsert_config(int(guild.id), patch)
    invalidate_guild_config(int(guild.id))
    await interaction.followup.send(f"✅ {label.title()} messages enabled in {channel.mention}.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def _set_enabled(interaction: discord.Interaction, *, join: Optional[bool] = None, leave: Optional[bool] = None) -> None:
    from stoney_verify.commands_ext.public_setup_group import _require_setup_permission, _upsert_config
    from stoney_verify.guild_config import invalidate_guild_config

    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send_ephemeral(interaction, "❌ This must be used inside a server.")
    await _defer(interaction)
    patch: dict[str, Any] = {"welcome_events_updated_by_id": str(int(interaction.user.id))}
    if join is not None:
        patch["welcome_join_enabled"] = bool(join)
        patch["join_welcome_enabled"] = bool(join)
    if leave is not None:
        patch["welcome_leave_enabled"] = bool(leave)
        patch["goodbye_enabled"] = bool(leave)
        patch["leave_message_enabled"] = bool(leave)
    await _upsert_config(int(guild.id), patch)
    invalidate_guild_config(int(guild.id))
    await interaction.followup.send("✅ Join/leave event settings updated.", ephemeral=True)


class JoinLeaveChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, *, kind: str) -> None:
        self.kind = kind
        label = "leave/goodbye" if kind == "leave" else "join/welcome"
        super().__init__(
            placeholder=f"Choose channel for {label} messages...",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text],
            custom_id=f"dank_setup_welcome_events:{kind}:channel",
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        channel = self.values[0] if self.values else None
        if not isinstance(channel, discord.TextChannel):
            return await _send_ephemeral(interaction, "❌ Pick a normal text channel.")
        await _save_event_channel(interaction, channel, kind=self.kind)


class WelcomeEventsCenterView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)
        self.add_item(JoinLeaveChannelSelect(kind="join"))
        self.add_item(JoinLeaveChannelSelect(kind="leave"))

    @discord.ui.button(label="Preview Join", emoji="👁️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_welcome_events:preview_join", row=2)
    async def preview_join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        guild = interaction.guild
        if guild is None or member is None:
            return await _send_ephemeral(interaction, "❌ This must be used inside a server.")
        await _send_ephemeral(interaction, "Join preview only.", embed=_preview_embed(guild, member, kind="join"), allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Preview Leave", emoji="👁️", style=discord.ButtonStyle.secondary, custom_id="dank_setup_welcome_events:preview_leave", row=2)
    async def preview_leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        guild = interaction.guild
        if guild is None or member is None:
            return await _send_ephemeral(interaction, "❌ This must be used inside a server.")
        await _send_ephemeral(interaction, "Leave preview only.", embed=_preview_embed(guild, member, kind="leave"), allowed_mentions=discord.AllowedMentions.none())

    @discord.ui.button(label="Disable Join", emoji="🔕", style=discord.ButtonStyle.secondary, custom_id="dank_setup_welcome_events:disable_join", row=3)
    async def disable_join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _set_enabled(interaction, join=False)

    @discord.ui.button(label="Disable Leave", emoji="🔕", style=discord.ButtonStyle.secondary, custom_id="dank_setup_welcome_events:disable_leave", row=3)
    async def disable_leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _set_enabled(interaction, leave=False)

    @discord.ui.button(label="Disable Both", emoji="⛔", style=discord.ButtonStyle.danger, custom_id="dank_setup_welcome_events:disable_both", row=3)
    async def disable_both(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _set_enabled(interaction, join=False, leave=False)


async def open_welcome_events_center(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext.public_setup_group import _require_setup_permission
    from stoney_verify.guild_config import get_guild_config

    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send_ephemeral(interaction, "❌ This must be used inside a server.")
    cfg = await get_guild_config(int(guild.id), refresh=True)
    join_enabled = _cfg_bool(cfg, "welcome_join_enabled", False) or _cfg_bool(cfg, "join_welcome_enabled", False)
    leave_enabled = _cfg_bool(cfg, "welcome_leave_enabled", False) or _cfg_bool(cfg, "goodbye_enabled", False) or _cfg_bool(cfg, "leave_message_enabled", False)
    join_channel = guild.get_channel(_cfg_int(cfg, "join_welcome_channel_id") or _cfg_int(cfg, "welcome_channel_id"))
    leave_channel = guild.get_channel(_cfg_int(cfg, "goodbye_channel_id") or _cfg_int(cfg, "leave_channel_id") or _cfg_int(cfg, "welcome_channel_id"))

    embed = discord.Embed(
        title="👋 Join / Leave Messages",
        description="Enable ProBot-style join and leave announcements for this server from setup. These are optional and safe by default: disabled until an admin chooses channels.",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Join messages", value=f"{'✅ Enabled' if join_enabled else '❌ Disabled'}\nChannel: {join_channel.mention if isinstance(join_channel, discord.TextChannel) else 'not set'}", inline=True)
    embed.add_field(name="Leave messages", value=f"{'✅ Enabled' if leave_enabled else '❌ Disabled'}\nChannel: {leave_channel.mention if isinstance(leave_channel, discord.TextChannel) else 'not set'}", inline=True)
    embed.add_field(
        name="How to use",
        value="Pick a channel for join messages and/or leave messages below. Picking a channel also enables that message type. Use previews first if you want to see the default style.",
        inline=False,
    )
    embed.set_footer(text="/dank setup • Feature Centers • Welcome Center • Join/Leave")
    await _send_ephemeral(interaction, embed=embed, view=WelcomeEventsCenterView(), allowed_mentions=discord.AllowedMentions.none())


__all__ = ["open_welcome_events_center", "WelcomeEventsCenterView", "JoinLeaveChannelSelect"]
