from __future__ import annotations

"""Ticket orphan safety helpers for Dank Shield.

A ticket channel is an orphan when Discord channel creation succeeded but the DB
row could not be inserted or synced. Public-production behavior should not leave
unmanaged ticket channels behind.

This module is deliberately small and side-effect scoped:
- it only acts on a channel passed by the caller
- it verifies whether a DB row exists before deleting anything
- it does not reserve ticket numbers
- it does not create ticket rows
- it does not inspect or mutate other guilds
"""

from typing import Any, Callable, Optional

import discord

from .repository import get_ticket_by_any_channel_id as repo_get_ticket_by_any_channel_id


async def ticket_row_exists_for_channel(channel_id: int | str) -> bool:
    try:
        row = await repo_get_ticket_by_any_channel_id(channel_id)
        return isinstance(row, dict) and bool(row)
    except Exception:
        # If the DB is unreachable, do not pretend a row exists. The caller is
        # already in a DB-failure path and should clean up the brand-new channel.
        return False


async def cleanup_unpersisted_ticket_channel(
    channel: discord.TextChannel,
    *,
    owner_id: int | str,
    ticket_number: int | str,
    reason: str = "Ticket creation rolled back because the DB row was not persisted.",
    row_exists: Optional[Callable[[int | str], Any]] = None,
) -> bool:
    """Delete a brand-new ticket channel if it has no persisted ticket row.

    Returns True when the channel was deleted, False when it was kept or cleanup
    failed. This function is intended to run only immediately after ticket create
    DB insert/sync failure.
    """

    exists_fn = row_exists or ticket_row_exists_for_channel

    try:
        exists = await exists_fn(int(channel.id))
    except Exception:
        exists = False

    if exists:
        return False

    try:
        await channel.send(
            "⚠️ Ticket creation failed before Dank Shield could safely save this ticket. "
            "This temporary channel will be removed so staff do not miss an unmanaged ticket. "
            "Please try opening the ticket again in a moment.",
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except Exception:
        pass

    try:
        await channel.delete(
            reason=(
                f"{reason} owner_id={owner_id} ticket_number={ticket_number} "
                f"channel_id={getattr(channel, 'id', 'unknown')}"
            )[:512]
        )
        return True
    except Exception as exc:
        try:
            print(
                "⚠️ Failed deleting orphan ticket channel "
                f"channel={getattr(channel, 'id', 'unknown')} "
                f"owner={owner_id} ticket_number={ticket_number}: {repr(exc)}"
            )
        except Exception:
            pass
        return False


__all__ = ["cleanup_unpersisted_ticket_channel", "ticket_row_exists_for_channel"]
