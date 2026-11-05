from __future__ import annotations

"""Single setup entrypoint for member-facing cards and operational lifecycle logs.

Existing welcome, exit and audit implementations remain authoritative. This view
only unifies navigation and writes the canonical operational log destination.
"""

import discord

from .guild_config import get_guild_config
from .welcome_event_services import _cfg_int, _send_ephemeral


def _channel(guild: discord.Guild, channel_id: int) -> str:
    channel = guild.get_channel(channel_id) if channel_id else None
    return channel.mention if isinstance(channel, discord.TextChannel) else "Not configured"


def _log_channel_id(cfg: object) -> int:
    from .startup_guards.member_lifecycle_router_guard import JOIN_LEAVE_KEYS

    for key in JOIN_LEAVE_KEYS:
        value = _cfg_int(cfg, key)
        if value:
            return value
    return 0


def _summary(guild: discord.Guild, cfg: object) -> discord.Embed:
    join_id = _cfg_int(cfg, "join_welcome_channel_id") or _cfg_int(cfg, "welcome_channel_id")
    exit_id = _cfg_int(cfg, "goodbye_channel_id") or _cfg_int(cfg, "leave_channel_id")
    log_id = _log_channel_id(cfg)
    embed = discord.Embed(
        title="👋 Member Welcome & Activity",
        description="One setup center for arrivals, departures, and operational records. Existing settings are preserved.",
        color=discord.Color.green(),
    )
    embed.add_field(
        name="Delivery destinations",
        value=(
            f"🎉 Welcome: {_channel(guild, join_id)}\\n"
            f"👋 Goodbye: {_channel(guild, exit_id)}\\n"
            f"📋 Operational log: {_channel(guild, log_id)}"
        ),
        inline=False,
    )
    if log_id and log_id in (join_id, exit_id):
        embed.add_field(
            name="Shared-channel delivery",
            value="A successful same-channel card suppresses the duplicate operational event; a failed card leaves the log fallback available.",
            inline=False,
        )
    embed.add_field(
        name="Static start-here message",
        value="Use `/dank welcome set-channel`, `/dank welcome template`, then `/dank welcome post` to update the persistent message.",
        inline=False,
    )
    embed.set_footer(text="Staff Modlog remains independent. No historical settings are reset.")
    return embed


class OperationalLogSelect(discord.ui.ChannelSelect):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Choose operational join/leave log channel",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from .commands_ext.public_setup_group import _require_setup_permission, _upsert_config
        from .commands_ext.public_setup_logs import _join_leave_alias_payload
        from .guild_config import invalidate_guild_config

        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        selected = self.values[0] if self.values else None
        if guild is None or selected is None:
            return await _send_ephemeral(interaction, "❌ Select a server text channel.")
        channel = guild.get_channel(int(selected.id))
        if not isinstance(channel, discord.TextChannel):
            return await _send_ephemeral(interaction, "❌ Select a normal server text channel.")
        me = guild.me
        if me is None:
            return await _send_ephemeral(interaction, "❌ Cannot verify the bot's channel permissions.")
        perms = channel.permissions_for(me)
        if not all((perms.view_channel, perms.send_messages, perms.embed_links)):
            return await _send_ephemeral(interaction, "❌ Bot needs View Channel, Send Messages and Embed Links in that channel.")
        try:
            await _upsert_config(int(guild.id), _join_leave_alias_payload(str(channel.id)))
            invalidate_guild_config(int(guild.id))
            cfg = await get_guild_config(int(guild.id), refresh=True)
        except Exception as exc:
            return await _send_ephemeral(interaction, f"❌ Could not save log channel: {type(exc).__name__}.")
        await interaction.response.edit_message(embed=_summary(guild, cfg), view=MemberLifecycleSetupView())


class MemberLifecycleSetupView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=300)
        self.add_item(OperationalLogSelect())

    @discord.ui.button(label="Welcome & Goodbye", emoji="🎉", style=discord.ButtonStyle.primary, row=0)
    async def cards(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .commands_ext.public_setup_group import _require_setup_permission
        from .welcome_event_services import WelcomeEventsCenterView, _build_center_embed
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return
        cfg = await get_guild_config(int(guild.id), refresh=True)
        await interaction.response.edit_message(embed=_build_center_embed(guild, cfg), view=WelcomeEventsCenterView(guild=guild, cfg=cfg))

    @discord.ui.button(label="Check Delivery", emoji="🔎", style=discord.ButtonStyle.secondary, row=0)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .commands_ext.public_setup_group import _require_setup_permission
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return
        cfg = await get_guild_config(int(guild.id), refresh=True)
        log_id = _log_channel_id(cfg)
        channel = guild.get_channel(log_id) if log_id else None
        if not isinstance(channel, discord.TextChannel) or guild.me is None:
            return await _send_ephemeral(interaction, "❌ Operational log channel is missing or unconfigured.")
        perms = channel.permissions_for(guild.me)
        missing = [label for label, allowed in (
            ("View Channel", perms.view_channel),
            ("Send Messages", perms.send_messages),
            ("Embed Links", perms.embed_links),
        ) if not allowed]
        await _send_ephemeral(interaction, f"{'❌ Missing: ' + ', '.join(missing) if missing else '✅ Operational logs can be sent'} in {channel.mention}.")

    @discord.ui.button(label="Static Welcome Help", emoji="📌", style=discord.ButtonStyle.secondary, row=2)
    async def static_welcome(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .commands_ext.public_setup_group import _require_setup_permission
        if not await _require_setup_permission(interaction):
            return
        await _send_ephemeral(interaction, "Use `/dank welcome set-channel` to choose #start-here, `/dank welcome template` to edit text, then `/dank welcome post` to publish or update without duplicates.")


async def open_member_lifecycle_setup(interaction: discord.Interaction) -> None:
    from .commands_ext.public_setup_group import _require_setup_permission
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send_ephemeral(interaction, "This setup is only available inside a server.")
    cfg = await get_guild_config(int(guild.id), refresh=True)
    await _send_ephemeral(interaction, embed=_summary(guild, cfg), view=MemberLifecycleSetupView(), allowed_mentions=discord.AllowedMentions.none())
