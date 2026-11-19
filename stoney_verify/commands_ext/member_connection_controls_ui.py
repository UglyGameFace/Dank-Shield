from __future__ import annotations

"""Compact owner controls for Connection Intelligence in /dank members."""

from typing import Any

import discord

from .member_role_browser_common import OwnedView, reply_ephemeral, require_review
from ..ui.picker import DankPickerView, chunk_choices, make_choice
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


class _ConnectionPickerView(DankPickerView):
    """Paged house picker for connection-control panels.

    Pages long choice lists with chunk_choices so every option stays
    reachable past Discord's 25-option select limit (the protected
    channel list used to be silently cut off). Keeps the panel owner
    gate; feature-level authorization still runs inside each pick.
    """

    def __init__(
        self,
        owner_id: int,
        *,
        choices,
        on_pick,
        custom_id: str,
        placeholder: str,
        title: str,
        on_home=None,
        on_cancel=None,
        home_label: str = "Back",
        page: int = 0,
    ) -> None:
        all_choices = list(choices or [])
        pages = chunk_choices(all_choices) or [[]]
        page = max(0, min(int(page), len(pages) - 1))
        super().__init__(
            author_id=int(owner_id),
            choices=pages[page],
            on_pick=on_pick,
            custom_id=f"{custom_id}:{page}" if len(pages) > 1 else custom_id,
            placeholder=(
                f"{placeholder} \u2022 Page {page + 1}/{len(pages)}"
                if len(pages) > 1 else placeholder
            ),
            title=title,
            timeout=900,
            on_home=on_home,
            on_cancel=on_cancel,
            home_label=home_label,
        )
        self._all_choices = all_choices
        self._picker_on_pick = on_pick
        self._base_custom_id = custom_id
        self._base_placeholder = placeholder
        self._picker_title = title
        self._picker_on_home = on_home
        self._picker_on_cancel = on_cancel
        self._picker_home_label = home_label
        self.page = page
        self.pages = len(pages)
        if len(pages) > 1:
            self.add_item(_ConnectionPickerPageButton(delta=-1, disabled=page <= 0))
            self.add_item(_ConnectionPickerPageButton(delta=1, disabled=page >= len(pages) - 1))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != int(self.author_id):
            await reply_ephemeral(
                interaction,
                "\u274c This private member browser belongs to another staff member.",
            )
            return False
        return await require_review(interaction)

    def _for_page(self, page: int) -> "_ConnectionPickerView":
        return _ConnectionPickerView(
            int(self.author_id),
            choices=self._all_choices,
            on_pick=self._picker_on_pick,
            custom_id=self._base_custom_id,
            placeholder=self._base_placeholder,
            title=self._picker_title,
            on_home=self._picker_on_home,
            on_cancel=self._picker_on_cancel,
            home_label=self._picker_home_label,
            page=page,
        )


class _ConnectionPickerPageButton(discord.ui.Button):
    def __init__(self, *, delta: int, disabled: bool) -> None:
        super().__init__(
            label="Previous" if delta < 0 else "Next",
            emoji="\u25c0\ufe0f" if delta < 0 else "\u25b6\ufe0f",
            style=discord.ButtonStyle.secondary,
            custom_id=(
                "dank:connection_controls:picker:prev" if delta < 0
                else "dank:connection_controls:picker:next"
            ),
            row=2,
            disabled=bool(disabled),
        )
        self.delta = int(delta)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, _ConnectionPickerView) or not await view.interaction_check(interaction):
            return
        page = max(0, min(view.page + self.delta, view.pages - 1))
        await interaction.response.edit_message(view=view._for_page(page))


class _ConnectionPickerOpenButton(discord.ui.Button):
    """Swaps a connection-control panel for a house picker on the same message."""

    def __init__(self, *, label: str, custom_id: str, row: int, build) -> None:
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            custom_id=custom_id,
            row=row,
        )
        self._build = build

    async def callback(self, interaction: discord.Interaction) -> None:
        host = self.view
        if isinstance(host, OwnedView) and not await host.interaction_check(interaction):
            return
        picker = self._build(interaction)
        message = getattr(interaction, "message", None)
        embeds = getattr(message, "embeds", None) if message is not None else None
        await _display(interaction, embeds[0] if embeds else discord.Embed(title="\U0001f517 Connection Intelligence"), picker)


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


async def _review_protected_channel(interaction: discord.Interaction, owner_id: int, cid: str) -> None:
    """Original ProtectedChannelSelect pick flow, unchanged."""
    if not await _authorized(interaction):
        return
    await interaction.response.defer(ephemeral=True)
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
    await _display(interaction, embed, ConfirmRestoreView(owner_id, cid))


def _protected_channel_picker(owner_id: int, guild: discord.Guild, entries: dict) -> _ConnectionPickerView:
    """Every protected channel as a house-picker choice — no truncation.

    The raw ProtectedChannelSelect silently cut the list off; the house
    picker pages with chunk_choices so all entries stay reachable.
    """
    choices = []
    for cid, entry in entries.items():
        channel = guild.get_channel(int(cid))
        name = str(getattr(channel, "name", None) or "Deleted channel")
        status = channel_protection_status(guild, cid, entry)
        choices.append(make_choice(name[:100], str(cid), description=status[:100]))

    async def on_pick(interaction: discord.Interaction, value: str) -> None:
        await _review_protected_channel(interaction, owner_id, str(value))

    async def back(interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        await _render_protection(interaction, owner_id)

    return _ConnectionPickerView(
        owner_id,
        choices=choices,
        on_pick=on_pick,
        custom_id="dank:connection_controls:protected_channel:v1",
        placeholder="Choose a protected channel\u2026",
        title="Protected Channels",
        on_home=back,
        on_cancel=back,
        home_label="Restricted channels",
    )


class RemoveProtectionView(OwnedView):
    def __init__(self, owner_id: int, guild: discord.Guild, entries: dict) -> None:
        super().__init__(owner_id)
        self._picker_guild = guild
        self._picker_entries = entries
        self.add_item(_ConnectionPickerOpenButton(
            label="Choose a protected channel\u2026",
            custom_id="dank:connection_controls:protected_channel:open:v1",
            row=0,
            build=lambda interaction: _protected_channel_picker(owner_id, guild, entries),
        ))

    @discord.ui.button(label="Restricted channels", emoji="\u25c0\ufe0f", style=discord.ButtonStyle.secondary, row=1)
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
