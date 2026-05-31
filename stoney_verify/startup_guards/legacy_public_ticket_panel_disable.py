from __future__ import annotations

"""Disable the legacy public ticket-create panel path.

`tickets_new.panel.TicketPanelView` is still useful as a compatibility shell for
old messages, but it must not create tickets anymore. The clean public panel in
`commands_ext.public_ticket_panel_clean` is the only allowed public create path.

This keeps the staff ticket controls registered while turning stale public panel
buttons into a clear "repost the clean panel" message instead of a second ticket
creator.
"""

from typing import Any

import discord


def _log(message: str) -> None:
    try:
        print(f"✅ legacy_public_ticket_panel_disable: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ legacy_public_ticket_panel_disable: {message}")
    except Exception:
        pass


async def _reply_legacy_disabled(interaction: discord.Interaction) -> None:
    content = (
        "⚠️ This is an old Dank Shield ticket panel and has been disabled so it "
        "cannot create duplicate or wrong-menu tickets.\n\n"
        "Staff: run `/ticket-panel post` in the correct support channel and use "
        "the new **category-menu Create Ticket** panel."
    )
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                content,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.response.send_message(
                content,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
    except Exception:
        pass


class DisabledLegacyTicketPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Create Ticket",
        style=discord.ButtonStyle.secondary,
        custom_id="ticket_create",
        emoji="⚠️",
    )
    async def legacy_create_ticket(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await _reply_legacy_disabled(interaction)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
        _warn(
            "disabled legacy ticket panel error "
            f"guild={getattr(getattr(interaction, 'guild', None), 'id', None)} "
            f"item={type(item).__name__} error={type(error).__name__}: {error!r}"
        )
        await _reply_legacy_disabled(interaction)


def apply() -> bool:
    try:
        from ..tickets_new import panel as panel_mod
    except Exception as e:
        _warn(f"could not import tickets_new.panel: {e!r}")
        return False

    if getattr(panel_mod, "_LEGACY_PUBLIC_TICKET_PANEL_DISABLED", False):
        return True

    try:
        panel_mod.TicketPanelView = DisabledLegacyTicketPanelView
        setattr(panel_mod, "_LEGACY_PUBLIC_TICKET_PANEL_DISABLED", True)
        _log("replaced legacy TicketPanelView with disabled compatibility view")
        return True
    except Exception as e:
        _warn(f"patch failed: {e!r}")
        return False


apply()

__all__ = ["apply", "DisabledLegacyTicketPanelView"]
