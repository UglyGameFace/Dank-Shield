from __future__ import annotations

"""Single setup entrypoint for member-facing cards and operational lifecycle logs.

Existing welcome, exit and audit implementations remain authoritative. This view
only unifies navigation and writes the canonical operational log destination.
"""

import discord

from .guild_config import get_guild_config
from .welcome_event_services import _cfg_int, _send_ephemeral
from .ui import DankGuildResourceBrowserView


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
    from .welcome_card_runtime import resolve_join_card_channel
    from .exit_card_runtime import resolve_exit_card_channel
    join_card, _ = resolve_join_card_channel(guild, cfg)
    exit_card, _ = resolve_exit_card_channel(guild, cfg)
    join_id = join_card.id if join_card else 0
    exit_id = exit_card.id if exit_card else 0
    log_id = _log_channel_id(cfg)
    embed = discord.Embed(
        title="👋 Member Welcome & Activity",
        description="One setup center for arrivals, departures, and operational records. Existing settings are preserved.",
        color=discord.Color.green(),
    )
    embed.add_field(
        name="Delivery destinations",
        value=(
            f"🎉 Welcome card: {_channel(guild, join_id)}\n"
            f"👋 Goodbye card: {_channel(guild, exit_id)}\n"
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
        value="Open **Static Welcome Help** below to choose a channel, edit the text, and post or update the persistent message.",
        inline=False,
    )
    embed.set_footer(text="Staff Modlog remains independent. No historical settings are reset.")
    return embed


class OperationalLogButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Operational Log Channel", emoji="📋",
            style=discord.ButtonStyle.secondary,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from .commands_ext.public_setup_group import _require_setup_permission
        if not await _require_setup_permission(interaction):
            return
        view = DankGuildResourceBrowserView(
            guild=interaction.guild, author_id=interaction.user.id,
            resource_kinds=("text",), on_pick=self.save_channel,
            custom_id="dank:lifecycle:log", title="Operational Join & Leave Log",
            on_home=open_member_lifecycle_setup,
        )
        await interaction.response.edit_message(embed=view.embed(), view=view)

    async def save_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        from .commands_ext.public_setup_group import _require_setup_permission, _upsert_config
        from .commands_ext.public_setup_logs import _join_leave_alias_payload
        from .guild_config import invalidate_guild_config

        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        channel = guild.get_channel(int(channel.id)) if guild else None
        if not isinstance(channel, discord.TextChannel):
            return await _send_ephemeral(interaction, "❌ Select a normal server text channel.")
        me = guild.me
        if me is None:
            return await _send_ephemeral(interaction, "❌ Cannot verify the bot's channel permissions.")
        perms = channel.permissions_for(me)
        if not all((perms.view_channel, perms.send_messages, perms.embed_links)):
            return await _send_ephemeral(interaction, "❌ Bot needs View Channel, Send Messages and Embed Links in that channel.")
        if not interaction.response.is_done():
            await interaction.response.defer()
        try:
            cfg = await get_guild_config(int(guild.id), refresh=True)
            await _upsert_config(int(guild.id), _join_leave_alias_payload(str(channel.id), current=cfg))
            invalidate_guild_config(int(guild.id))
            cfg = await get_guild_config(int(guild.id), refresh=True)
        except Exception as exc:
            return await _send_ephemeral(interaction, f"❌ Could not save log channel: {type(exc).__name__}.")
        await interaction.edit_original_response(embed=_summary(guild, cfg), view=MemberLifecycleSetupView())


class MemberLifecycleSetupView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=300)
        self.add_item(OperationalLogButton())

    @discord.ui.button(label="Text Announcements", emoji="🎉", style=discord.ButtonStyle.primary, row=0)
    async def cards(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .commands_ext.public_setup_group import _require_setup_permission
        from .welcome_event_services import WelcomeEventsCenterView, _build_center_embed
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return
        if not interaction.response.is_done():
            await interaction.response.defer()
        try:
            cfg = await get_guild_config(int(guild.id), refresh=True)
        except Exception:
            return await _send_ephemeral(interaction, "❌ Could not load welcome settings. Try again shortly.")
        await interaction.edit_original_response(embed=_build_center_embed(guild, cfg), view=WelcomeEventsCenterView(guild=guild, cfg=cfg))

    @discord.ui.button(label="Card Design", emoji="🎨", style=discord.ButtonStyle.secondary, row=0)
    async def card_design(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .welcome_setup_ui import open_welcome_setup
        await open_welcome_setup(interaction, section="join")

    @discord.ui.button(label="Check Delivery", emoji="🔎", style=discord.ButtonStyle.secondary, row=0)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .commands_ext.public_setup_group import _require_setup_permission
        if not await _require_setup_permission(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            cfg = await get_guild_config(int(guild.id), refresh=True)
        except Exception:
            return await _send_ephemeral(interaction, "❌ Could not load delivery settings. Try again shortly.")
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
        from .welcome_setup_ui import open_welcome_setup
        await open_welcome_setup(interaction)


async def open_member_lifecycle_setup(interaction: discord.Interaction) -> None:
    from .commands_ext.public_setup_group import _require_setup_permission
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send_ephemeral(interaction, "This setup is only available inside a server.")
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    try:
        cfg = await get_guild_config(int(guild.id), refresh=True)
    except Exception:
        return await _send_ephemeral(interaction, "❌ Could not load welcome and log settings. Try again shortly.")
    await interaction.followup.send(embed=_summary(guild, cfg), view=MemberLifecycleSetupView(), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
