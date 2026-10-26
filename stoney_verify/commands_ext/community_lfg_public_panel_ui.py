from __future__ import annotations

"""Public LFG panel staff setup, reusing the Search-Safe channel browser."""

from typing import Any
import discord

from .. import community_lfg_public_panel as panel
from ..guild_config import get_guild_config
from ..ui.resource_browser import DankGuildResourceBrowserView
from .public_community_hub import _staff_authorized


async def _notice(interaction: discord.Interaction, text: str) -> None:
    if interaction.response.is_done():
        await interaction.followup.send(
            text[:1800], ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    else:
        await interaction.response.send_message(
            text[:1800], ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


def _owner_check(interaction: discord.Interaction, owner_id: int, guild_id: int) -> bool:
    return bool(
        interaction.guild is not None and int(interaction.guild.id) == guild_id
        and int(interaction.user.id) == owner_id and _staff_authorized(interaction)
    )


async def _setup_embed(guild: discord.Guild) -> discord.Embed:
    cfg = await get_guild_config(guild.id)
    current = panel._record(cfg)
    embed = discord.Embed(
        title="🎮 Public LFG Panel • Staff Setup",
        description=(
            "Post one **always-available gaming panel** for everyone in a channel. "
            "It updates the same message as public sessions change. "
            "Members use its buttons to open their **own private menus**. "
            "Private groups, private notes, member IDs and voice presence are never displayed."
        ),
        color=discord.Color.blurple(),
    )
    if panel._is_unavailable(cfg):
        status = "⚠️ Server configuration unavailable. Changes are blocked."
    elif current:
        status = (f"✅ Running in <#{current['channel_id']}>. "
                  "Use Refresh if it's out of date, or Use This Channel to move it.")
    else:
        status = "Not installed. Choose a channel or use this channel."
    embed.add_field(name="Current panel", value=status, inline=False)
    embed.add_field(
        name="Automatic updates",
        value="Near-live refresh (typically within a minute on small deployments), plus manual refresh. "
              "No message spam, member pings or extra slash commands.",
        inline=False,
    )
    embed.add_field(
        name="Permissions",
        value="Dank Shield needs View Channel, Send Messages, Embed Links and Read Message History. "
              "Only Manage Server staff can install, move or remove the panel.",
        inline=False,
    )
    embed.add_field(
        name="Database readiness",
        value="The panel reads the existing Community Hub sessions database. "
              "If that database is unavailable, it shows a temporary-unavailable notice rather than made-up players.",
        inline=False,
    )
    return embed


class PublicPanelSetupView(discord.ui.View):
    def __init__(self, owner_id: int, guild_id: int):
        super().__init__(timeout=900)
        self.owner_id = int(owner_id)
        self.guild_id = int(guild_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if _owner_check(interaction, self.owner_id, self.guild_id):
            return True
        await _notice(interaction, "Only the server manager who opened this setup can change the public panel.")
        return False

    async def _install(self, interaction: discord.Interaction, channel: Any) -> None:
        if not _owner_check(interaction, self.owner_id, self.guild_id):
            return await _notice(interaction, "Manage Server permission required.")
        if not interaction.response.is_done():
            await interaction.response.defer()
        if not isinstance(channel, discord.TextChannel):
            return await _notice(interaction, "Select a normal text channel.")
        try:
            status = await panel.install_public_board(interaction.guild, channel)
        except (RuntimeError, ValueError, discord.HTTPException) as exc:
            return await _notice(interaction, f"Couldn't install the panel: {str(exc)[:320]}")
        await interaction.edit_original_response(
            embed=await _setup_embed(interaction.guild),
            content=None, view=PublicPanelSetupView(self.owner_id, self.guild_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await _notice(interaction, "✅ " + status)

    @discord.ui.button(label="Use This Channel", emoji="📍", style=discord.ButtonStyle.primary, row=0)
    async def use_here(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._install(interaction, interaction.channel)

    @discord.ui.button(label="Choose Channel", emoji="🧭", style=discord.ButtonStyle.secondary, row=0)
    async def choose(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild = interaction.guild
        async def picked(pick_interaction: discord.Interaction, channel: Any) -> None:
            await self._install(pick_interaction, channel)
        async def home(back_interaction: discord.Interaction) -> None:
            await show_public_panel_setup(back_interaction, self.owner_id)
        browser = DankGuildResourceBrowserView(
            guild=guild,
            author_id=self.owner_id,
            resource_kinds=("text",),
            on_pick=picked,
            on_home=home,
            custom_id="dank_lfg_public_panel_channel",
            title="Where should the public LFG panel live?",
            placeholder="Search channels by name, ID or emoji…",
            home_label="Back to Panel Setup",
        )
        await interaction.response.edit_message(
            embed=browser.embed(), view=browser,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Refresh Live Panel", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.defer()
        result = await panel.refresh_one(interaction.guild, force=True)
        await _notice(interaction, (
            "✅ Live panel refreshed." if result in {"updated", "unchanged"}
            else "⚠️ Refresh couldn't complete: " + result
        ))

    @discord.ui.button(label="Remove Panel", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.defer()
        try:
            removed = await panel.remove_public_board(interaction.guild)
        except RuntimeError as exc:
            return await _notice(interaction, str(exc)[:330])
        await interaction.edit_original_response(
            content=None, embed=await _setup_embed(interaction.guild),
            view=PublicPanelSetupView(self.owner_id, self.guild_id),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await _notice(interaction,
            "✅ Public LFG panel disabled. Previous buttons won't authorize new actions."
            if removed else "No active public LFG panel was found.")

    @discord.ui.button(label="Staff Dashboard", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_community_hub import _open_staff_dashboard, _defer_update
        await _defer_update(interaction)
        await _open_staff_dashboard(interaction, self.owner_id)


async def show_public_panel_setup(interaction: discord.Interaction, owner_id: int) -> None:
    if not _owner_check(interaction, owner_id, int(interaction.guild_id or 0)):
        return await _notice(interaction, "Only Manage Server staff can configure this panel.")
    if not interaction.response.is_done():
        await interaction.response.defer()
    try:
        embed = await _setup_embed(interaction.guild)
    except Exception:
        return await _notice(interaction, "Server panel settings are unavailable right now.")
    await interaction.edit_original_response(
        embed=embed, content=None,
        view=PublicPanelSetupView(owner_id, interaction.guild.id),
        allowed_mentions=discord.AllowedMentions.none(),
    )
