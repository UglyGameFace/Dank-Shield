from __future__ import annotations

"""Make ticket deletion safer than one-click nuking an open ticket.

Open tickets should be closed first, which posts the closed-ticket staff panel and
runs the normal transcript/archive lifecycle. Deleting from the closed panel is
still supported. This prevents accidental one-click delete from an active ticket
while keeping the existing close/reopen/delete services intact.
"""

from typing import Any

import discord


def _log(message: str) -> None:
    try:
        print(f"✅ ticket_delete_lifecycle_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_delete_lifecycle_guard: {message}")
    except Exception:
        pass


async def _safe_reply(interaction: discord.Interaction, content: str) -> None:
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


def apply() -> bool:
    try:
        from .. import transcripts as tx
    except Exception as e:
        _warn(f"could not import transcripts: {e!r}")
        return False

    if getattr(tx, "_TICKET_DELETE_LIFECYCLE_GUARD_APPLIED", False):
        return True

    original_cls = getattr(tx, "TicketOpenActionsView", None)
    if not isinstance(original_cls, type):
        _warn("TicketOpenActionsView is not a class")
        return False

    class SafeTicketOpenActionsView(discord.ui.View):
        def __init__(self) -> None:
            super().__init__(timeout=None)

        @discord.ui.button(
            label="Close Ticket",
            style=discord.ButtonStyle.danger,
            emoji="🔒",
            custom_id="sv:ticket:close",
            row=0,
        )
        async def close_ticket(
            self,
            interaction: discord.Interaction,
            button: discord.ui.Button,
        ) -> None:
            # Reuse the proven close behavior exactly; only delete-open changes.
            original_view = original_cls()
            return await original_view.close_ticket(interaction, button)

        @discord.ui.button(
            label="Delete Ticket",
            style=discord.ButtonStyle.secondary,
            emoji="🗑️",
            custom_id="sv:ticket:delete_open",
            row=0,
        )
        async def delete_open_ticket(
            self,
            interaction: discord.Interaction,
            button: discord.ui.Button,
        ) -> None:
            _ = button
            channel = interaction.channel
            if not isinstance(channel, discord.TextChannel):
                return await _safe_reply(interaction, "❌ Invalid channel.")

            try:
                is_deleted = await tx._ticket_is_deleted(channel)
                if is_deleted:
                    return await _safe_reply(interaction, "❌ Ticket is already deleted.")
            except Exception:
                pass

            try:
                is_closed = await tx._ticket_is_closed(channel)
            except Exception:
                is_closed = False

            if is_closed:
                # If a stale open-control button is still visible after close, route
                # to the real closed-ticket delete behavior instead of failing.
                try:
                    closed_view = tx.StaffClosedTicketView()
                    return await closed_view.delete_ticket(interaction, button)
                except Exception as e:
                    _warn(f"closed delete handoff failed channel={getattr(channel, 'id', 0)}: {e!r}")
                    return await _safe_reply(interaction, "❌ Could not hand this off to the closed-ticket delete flow.")

            return await _safe_reply(
                interaction,
                "🔒 Close this ticket first, then use **Delete Ticket** from the closed-ticket staff panel. "
                "That keeps transcripts, archive state, and audit logs safer than one-click deleting an active ticket.",
            )

    try:
        SafeTicketOpenActionsView.__name__ = "TicketOpenActionsView"
        setattr(tx, "TicketOpenActionsView", SafeTicketOpenActionsView)
        setattr(tx, "_TICKET_DELETE_LIFECYCLE_GUARD_APPLIED", True)
        _log("open-ticket Delete now requires the ticket to be closed first")
        return True
    except Exception as e:
        _warn(f"patch failed: {e!r}")
        return False


apply()

__all__ = ["apply"]
