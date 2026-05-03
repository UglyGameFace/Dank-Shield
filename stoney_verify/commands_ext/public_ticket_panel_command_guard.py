from __future__ import annotations

from typing import Any, Optional

import discord

LEGACY_MENU_FIRST_CUSTOM_ID = "sv:ticket:public:create_menu:v4"
_REGISTERED = False


def _log(message: str) -> None:
    try:
        print(f"public_ticket_panel_command_guard {message}")
    except Exception:
        pass


async def _send_ephemeral(interaction: discord.Interaction, content: str) -> None:
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


def _looks_like_create_ticket_button(item: Any) -> bool:
    try:
        label = str(getattr(item, "label", "") or "").lower()
        custom_id = str(getattr(item, "custom_id", "") or "").lower()
        return "create" in label and "ticket" in label or "ticket" in custom_id and "create" in custom_id
    except Exception:
        return False


async def _delegate_to_authoritative_ticket_panel(interaction: discord.Interaction) -> None:
    try:
        from stoney_verify.tickets_new.panel import TicketPanelView
    except Exception as e:
        return await _send_ephemeral(
            interaction,
            f"Ticket panel is unavailable right now. Restart the bot and repost the ticket panel. ({type(e).__name__})",
        )

    try:
        view = TicketPanelView()
    except Exception as e:
        return await _send_ephemeral(
            interaction,
            f"Ticket panel failed to load. Restart the bot and repost the ticket panel. ({type(e).__name__})",
        )

    for item in getattr(view, "children", []) or []:
        if not _looks_like_create_ticket_button(item):
            continue
        callback = getattr(item, "callback", None)
        if callable(callback):
            return await callback(interaction)

    return await _send_ephemeral(
        interaction,
        "This is an old ticket panel message. Please ask staff to delete it and repost the ticket panel from setup.",
    )


class LegacyMenuFirstTicketPanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Create Ticket",
        style=discord.ButtonStyle.green,
        emoji="🎫",
        custom_id=LEGACY_MENU_FIRST_CUSTOM_ID,
    )
    async def create_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _delegate_to_authoritative_ticket_panel(interaction)


def register_public_ticket_panel_command_guard_commands(bot: Any, tree: Any) -> None:
    global _REGISTERED
    _ = tree
    if _REGISTERED:
        return
    try:
        bot.add_view(LegacyMenuFirstTicketPanelView())
        _REGISTERED = True
        _log("registered stale menu-first button compatibility view")
    except Exception as e:
        _log(f"compatibility view registration failed: {e!r}")


__all__ = ["register_public_ticket_panel_command_guard_commands", "LegacyMenuFirstTicketPanelView"]
