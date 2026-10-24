from __future__ import annotations

"""Staff-only discovery of existing notification systems.

Never creates a notification policy, modifies another owner's configuration,
queries live recipients, reserves a claim, or sends any Discord message.
The original feature routes recheck their own permissions at click time.
"""

import discord

from stoney_verify.action_notification_owners import (
    NOTIFICATION_OWNERS, find_notification_owner,
)
from stoney_verify.action_notification_status import (
    NotificationPolicyStatus, inspect_saved_notification_status,
)
from stoney_verify.panel_lifecycle import PRIVATE_MENU_TTL_SECONDS
from .public_action_builder import _defer, _msg, _replace
from .public_staff_scope import scoped_interaction_is_staff


def _directory_embed(statuses: tuple[NotificationPolicyStatus, ...] | None = None) -> discord.Embed:
    e = discord.Embed(
        title="🧭 Notification Systems • Owner Directory",
        description=(
            "**Action & Reminder Center › Existing notification systems**\n"
            "These systems already have their own workers, policies and data. "
            "Select a system to open its **existing** control panel. "
            "This directory does not check live enablement or merge delivery engines.\n\n"
            "🔒 **Action Center Member Setup DMs remain disabled.**"
        ),
        color=discord.Color.blurple(),
    )
    by_key = {entry.key: entry for entry in statuses or ()}
    status_labels = {
        "saved_enabled": "✅ Saved enabled",
        "saved_disabled": "🔕 Saved disabled",
        "not_configured": "⚪ Not configured",
        "unavailable": "⚠️ Unavailable",
        "owner_managed": "🧭 Owner-managed / not inspected",
    }
    for item in NOTIFICATION_OWNERS:
        status = by_key.get(item.key)
        status_text = ""
        if status is not None:
            label = status_labels.get(status.state, "⚠️ Unverified")
            status_text = f"\n**Saved policy:** {label}\n{status.detail}"
        e.add_field(
            name=f"{item.emoji} {item.label}",
            value=f"{item.description}\n*{item.delivery_contract}*{status_text}",
            inline=False,
        )
    e.set_footer(
        text=(
            "Read-only saved-policy snapshot • Not delivery or worker health"
            if statuses is not None else
            "Staff only • Navigation, not a status probe • No sends or database writes"
        )
    )
    return e


async def open_existing_notification_owner(interaction: discord.Interaction, key: str) -> None:
    if interaction.guild is None or not scoped_interaction_is_staff(interaction):
        return await _msg(interaction, "Server staff permission is required to open notification controls.")
    item = find_notification_owner(key)
    if item is None:
        return await _msg(interaction, "That notification system is not registered.")
    # This is the SAME central registry route used by /dank Home and Search.
    # It delegates the final authorization and operation to the canonical owner.
    from .public_command_surface_v2 import _route_feature
    await _route_feature(interaction, item.feature_route)


class NotificationOwnerPicker(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Choose an existing notification system…",
            min_values=1, max_values=1, row=0,
            options=[
                discord.SelectOption(
                    label=item.label,
                    value=item.key,
                    description=item.description[:100],
                    emoji=item.emoji,
                )
                for item in NOTIFICATION_OWNERS
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await open_existing_notification_owner(interaction, self.values[0])


class NotificationOwnerDirectoryView(discord.ui.View):
    def __init__(self, user_id: int, guild_id: int) -> None:
        super().__init__(timeout=PRIVATE_MENU_TTL_SECONDS)
        self.user_id = int(user_id)
        self.guild_id = int(guild_id)
        self.add_item(NotificationOwnerPicker())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (
            interaction.guild_id != self.guild_id
            or int(interaction.user.id) != self.user_id
            or not scoped_interaction_is_staff(interaction)
        ):
            await _msg(interaction, "Open your own staff notification directory in this server.")
            return False
        return True

    @discord.ui.button(label="Check Saved Status", emoji="🔎", style=discord.ButtonStyle.secondary, row=1)
    async def check_saved_status(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if interaction.guild_id != self.guild_id or not scoped_interaction_is_staff(interaction):
            return await _msg(interaction, "Open your own staff notification directory in this server.")
        await _defer(interaction)
        try:
            statuses = await inspect_saved_notification_status(self.guild_id)
        except Exception:
            return await _msg(interaction, "Notification status could not be checked. No policies were changed.")
        await _replace(
            interaction, embed=_directory_embed(statuses),
            view=NotificationOwnerDirectoryView(self.user_id, self.guild_id),
        )

    @discord.ui.button(label="Back to Action Center", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .public_action_center import open_action_center
        await open_action_center(interaction)


async def open_notification_owner_directory(interaction: discord.Interaction) -> None:
    if interaction.guild is None or not scoped_interaction_is_staff(interaction):
        return await _msg(interaction, "Only authorized staff can inspect notification system controls.")
    await _defer(interaction)
    await _replace(
        interaction,
        embed=_directory_embed(),
        view=NotificationOwnerDirectoryView(interaction.user.id, interaction.guild.id),
    )
