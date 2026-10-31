from __future__ import annotations

"""Compact owner controls for Connection Intelligence in /dank members."""

from typing import Any

import discord

from .member_role_browser_common import OwnedView, reply_ephemeral, require_review
from ..member_connection_controls import (
    ConnectionControlError, channel_protection_status, load_controls,
    protect_channel, remove_protection, toggle_review,
)


async def _authorized(interaction: discord.Interaction) -> bool:
    if not await require_review(interaction):
        return False
    actor = interaction.user
    perms = getattr(actor, "guild_permissions", None)
    if not isinstance(actor, discord.Member) or not perms or not (
        perms.administrator or perms.manage_guild
    ):
        await reply_ephemeral(
            interaction, "Only a server owner, Administrator, or Manage Server staff can change connection settings."
        )
        return False
    return True


async def _display(interaction: discord.Interaction, embed: discord.Embed, view: discord.ui.View) -> None:
    from .member_command_center import _replace_panel
    await _replace_panel(interaction, embed=embed, view=view)


async def _back_to_intelligence(interaction: discord.Interaction, owner_id: int) -> None:
    from .member_command_center import IntelligenceMenuView, _intelligence_embed
    await _display(interaction, _intelligence_embed(), IntelligenceMenuView(owner_id))


async def _render_controls(interaction: discord.Interaction, owner_id: int) -> None:
    try:
        _, config = await load_controls(interaction.guild.id)
    except Exception:
        return await reply_ephemeral(
            interaction, "Connection controls cannot read current settings. No changes were made."
        )
    count = len(config["protected"])
    drift = sum(
        channel_protection_status(interaction.guild, cid, entry) !=
        "active (administrators still bypass overwrites)"
        for cid, entry in config["protected"].items()
    )
    embed = discord.Embed(
        title="🔗 Connection Intelligence",
        description=(
            "**Monitor:** " + ("On" if config["monitor"] else "Off") +
            " · Check recorded relationships after bans.\n"
            "**Review suspicious accounts:** " + ("On" if config["review"] else "Off") +
            " · Privately flag new arrivals with verified banned-account links.\n"
            f"**Protect restricted channels:** {count} channel(s) guarded"
            + (f" · {drift} require attention" if drift else "") +
            "\n\nOnly recorded, guild-local evidence is used. These checks never punish members."
        ),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Private staff review by default • No link is proof of a leak")
    await _display(interaction, embed, ConnectionControlsView(owner_id, config))


async def open_connection_controls(interaction: discord.Interaction, owner_id: int) -> None:
    if not await _authorized(interaction):
        return
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    await _render_controls(interaction, owner_id)


class ConnectionControlsView(OwnedView):
    def __init__(self, owner_id: int, config: dict[str, Any]) -> None:
        super().__init__(owner_id)
        for component in self.children:
            if getattr(component, "custom_id", "") == "dank:connections:monitor":
                component.label = "Monitor: On" if config["monitor"] else "Monitor: Off"
            if getattr(component, "custom_id", "") == "dank:connections:review":
                component.label = "Review: On" if config["review"] else "Review: Off"

    @discord.ui.button(label="Monitor", emoji="📡", style=discord.ButtonStyle.secondary,
                       custom_id="dank:connections:monitor", row=0)
    async def monitor(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._toggle(interaction, "monitor")

    @discord.ui.button(label="Review", emoji="🔎", style=discord.ButtonStyle.secondary,
                       custom_id="dank:connections:review", row=0)
    async def review(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._toggle(interaction, "review")

    async def _toggle(self, interaction: discord.Interaction, which: str) -> None:
        if not await _authorized(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await toggle_review(interaction.guild.id, which)
        except Exception:
            return await reply_ephemeral(
                interaction, "Setting could not be saved and verified. Previous setting remains in effect."
            )
        await _render_controls(interaction, self.owner_id)

    @discord.ui.button(label="Protect restricted channels", emoji="🔒",
                       style=discord.ButtonStyle.primary, row=1)
    async def protection(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        await _render_protection(interaction, self.owner_id)

    @discord.ui.button(label="Back to Intelligence", emoji="◀️",
                       style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _back_to_intelligence(interaction, self.owner_id)


async def _render_protection(interaction: discord.Interaction, owner_id: int) -> None:
    try:
        _, controls = await load_controls(interaction.guild.id)
    except Exception:
        return await reply_ephemeral(interaction, "Cannot read saved channel protection. No changes made.")
    entries = controls["protected"]
    drift = [
        cid for cid, item in entries.items()
        if channel_protection_status(interaction.guild, cid, item) !=
        "active (administrators still bypass overwrites)"
    ]
    embed = discord.Embed(
        title="🔒 Restricted channels",
        description=(
            f"Protected: **{len(entries)} / 10**"
            + (f" · **{len(drift)} need review**" if drift else "") +
            "\nSelect a trusted role and a text channel to keep ordinary members out. "
            "Dank Shield denies @everyone access and allows the chosen trusted role. "
            "Existing staff/admin rights still apply. This never bans, mutes, "
            "or removes a member because of their connections."
            "\n\nDiscord administrators can bypass channel restrictions. "
            "Screenshots and private reposting cannot be prevented."
        ),
        color=discord.Color.blurple(),
    )
    await _display(interaction, embed, ProtectChannelsView(owner_id, bool(entries)))


class ProtectChannelsView(OwnedView):
    def __init__(self, owner_id: int, has_entries: bool) -> None:
        super().__init__(owner_id)
        for component in self.children:
            if getattr(component, "custom_id", "") == "dank:connections:remove":
                component.disabled = not has_entries

    @discord.ui.button(label="Add protected channel", emoji="➕", style=discord.ButtonStyle.primary, row=0)
    async def add(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction):
            return
        await _browse_trusted_roles(interaction, self.owner_id)

    @discord.ui.button(label="Review or remove", emoji="🧾", style=discord.ButtonStyle.secondary,
                       custom_id="dank:connections:remove", row=0)
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            _, config = await load_controls(interaction.guild.id)
        except Exception:
            return await reply_ephemeral(interaction, "Cannot read the current protection list.")
        if not config["protected"]:
            return await _render_protection(interaction, self.owner_id)
        view = RemoveProtectionView(self.owner_id, interaction.guild, config["protected"])
        embed = discord.Embed(
            title="Review protected channels",
            description="Select one channel to inspect or restore its original visibility. "
                        "No other channels will be changed.",
            color=discord.Color.blurple(),
        )
        await _display(interaction, embed, view)

    @discord.ui.button(label="Connection controls", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        await _render_controls(interaction, self.owner_id)


async def _browse_trusted_roles(interaction: discord.Interaction, owner_id: int) -> None:
    from ..ui.resource_browser import DankGuildResourceBrowserView

    async def home(back: discord.Interaction) -> None:
        if not await _authorized(back):
            return
        await back.response.defer(ephemeral=True)
        await _render_protection(back, owner_id)

    async def picked(select: discord.Interaction, role: Any) -> None:
        if not await _authorized(select):
            return
        if not isinstance(role, discord.Role) or role.id == select.guild.default_role.id or role.managed:
            return await reply_ephemeral(select, "Choose a normal, non-managed trusted role.")
        await _browse_private_channels(select, owner_id, role.id)

    browser = DankGuildResourceBrowserView(
        guild=interaction.guild, author_id=owner_id, resource_kinds=("role",),
        on_pick=picked, custom_id="dank:connection_roles:v1",
        title="Choose the trusted role", placeholder="Search trusted roles…",
        on_home=home, home_label="Restricted channels",
    )
    await _display(interaction, browser.embed(), browser)


async def _browse_private_channels(interaction: discord.Interaction, owner_id: int, role_id: int) -> None:
    from ..ui.resource_browser import DankGuildResourceBrowserView

    async def home(back: discord.Interaction) -> None:
        if not await _authorized(back):
            return
        await _browse_trusted_roles(back, owner_id)

    async def picked(select: discord.Interaction, channel: Any) -> None:
        if not await _authorized(select):
            return
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != select.guild.id:
            return await reply_ephemeral(select, "Choose a text channel in this server.")
        role = select.guild.get_role(role_id)
        if role is None:
            return await reply_ephemeral(select, "Trusted role no longer exists. Choose it again.")
        embed = discord.Embed(
            title="Confirm channel protection",
            description=(
                f"**Channel:** {channel.mention}\n**Trusted role:** {role.mention}\n\n"
                "Dank Shield will deny @everyone visibility and allow this role. "
                "Existing extra role/member visibility grants will block the change, "
                "not be silently removed. Category permission sync may become unsynced. "
                "Administrators still bypass visibility restrictions."
            ),
            color=discord.Color.orange(),
        )
        await _display(select, embed, ConfirmProtectionView(owner_id, channel.id, role_id))

    browser = DankGuildResourceBrowserView(
        guild=interaction.guild, author_id=owner_id, resource_kinds=("text",),
        on_pick=picked, custom_id="dank:connection_channels:v1",
        title="Choose a restricted text channel", placeholder="Search text channels…",
        on_home=home, home_label="Choose trusted role",
    )
    await _display(interaction, browser.embed(), browser)


class ConfirmProtectionView(OwnedView):
    def __init__(self, owner_id: int, channel_id: int, role_id: int) -> None:
        super().__init__(owner_id)
        self.channel_id, self.role_id = channel_id, role_id

    @discord.ui.button(label="Protect this channel", emoji="🔒", style=discord.ButtonStyle.danger, row=0)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        channel = interaction.guild.get_channel(self.channel_id)
        role = interaction.guild.get_role(self.role_id)
        try:
            await protect_channel(interaction.guild, interaction.user, channel, role)
        except ConnectionControlError as exc:
            return await reply_ephemeral(interaction, f"Not protected: {exc}")
        except Exception:
            return await reply_ephemeral(
                interaction, "Channel protection could not be completed. Check the protection list "
                             "for a pending recovery entry before retrying."
            )
        await _render_protection(interaction, self.owner_id)

    @discord.ui.button(label="Cancel", emoji="◀️", style=discord.ButtonStyle.secondary, row=0)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        await _render_protection(interaction, self.owner_id)


class ProtectedChannelSelect(discord.ui.Select):
    def __init__(self, owner_id: int, guild: discord.Guild, entries: dict[str, dict[str, Any]]) -> None:
        self.owner_id = owner_id
        options = []
        for cid, entry in entries.items():
            channel = guild.get_channel(int(cid))
            name = str(getattr(channel, "name", None) or "Deleted channel")
            status = channel_protection_status(guild, cid, entry)
            options.append(discord.SelectOption(
                label=name[:100], value=cid, description=status[:100],
            ))
        super().__init__(placeholder="Choose a protected channel…", options=options[:10], row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _authorized(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        cid = self.values[0]
        try:
            _, config = await load_controls(interaction.guild.id)
        except Exception:
            return await reply_ephemeral(interaction, "Cannot verify the current channel record.")
        entry = config["protected"].get(cid)
        if not entry:
            return await reply_ephemeral(interaction, "That channel is no longer protected.")
        channel = interaction.guild.get_channel(int(cid))
        role = interaction.guild.get_role(int(entry["role_id"]))
        embed = discord.Embed(
            title="Review channel protection",
            description=(
                f"**Channel:** {channel.mention if channel else cid}\n"
                f"**Trusted role:** {role.mention if role else 'Removed role'}\n"
                f"**Status:** {channel_protection_status(interaction.guild, cid, entry)}\n\n"
                "Restoring changes only the two visibility permissions Dank Shield set. "
                "If someone changed those permissions, restoration stops rather than overwriting them."
            ),
            color=discord.Color.blurple(),
        )
        await _display(interaction, embed, ConfirmRestoreView(self.owner_id, cid))


class RemoveProtectionView(OwnedView):
    def __init__(self, owner_id: int, guild: discord.Guild, entries: dict[str, dict[str, Any]]) -> None:
        super().__init__(owner_id)
        self.add_item(ProtectedChannelSelect(owner_id, guild, entries))

    @discord.ui.button(label="Restricted channels", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        await _render_protection(interaction, self.owner_id)


class ConfirmRestoreView(OwnedView):
    def __init__(self, owner_id: int, channel_id: str) -> None:
        super().__init__(owner_id)
        self.channel_id = channel_id

    @discord.ui.button(label="Restore previous visibility", emoji="↩️",
                       style=discord.ButtonStyle.danger, row=0)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _authorized(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            await remove_protection(interaction.guild, interaction.user, self.channel_id)
        except ConnectionControlError as exc:
            return await reply_ephemeral(interaction, f"Visibility not restored: {exc}")
        except Exception:
            return await reply_ephemeral(
                interaction, "Restore could not be verified. Check the saved protection list before retrying."
            )
        await _render_protection(interaction, self.owner_id)

    @discord.ui.button(label="Keep protection", emoji="◀️",
                       style=discord.ButtonStyle.secondary, row=0)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer(ephemeral=True)
        await _render_protection(interaction, self.owner_id)
