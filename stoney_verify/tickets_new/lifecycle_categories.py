from __future__ import annotations

"""
Native ticket lifecycle category movement helpers.

This module owns the clean production behavior for ticket channel movement:
- closed tickets move to the configured archive category from guild_configs
- reopened tickets move back to the configured active/open category
- movement preserves ticket-specific overwrites with sync_permissions=False
- failures are explicit and loggable instead of silently using env/category-name
  fallbacks that may belong to another server
"""

from dataclasses import dataclass
from typing import Optional

import discord

from .category_resolver import (
    TicketCategoryResolution,
    TicketCategoryResolutionError,
    channel_is_in_category,
    resolve_active_ticket_category,
    resolve_archive_ticket_category,
)


@dataclass(frozen=True)
class TicketLifecycleMoveResult:
    moved: bool
    already_correct: bool
    channel_id: int
    channel_name: str
    target_category_id: int
    target_category_name: str
    source: str
    reason: str


def _debug(message: str) -> None:
    try:
        print(f"🎫 ticket_lifecycle_categories {message}")
    except Exception:
        pass


async def _move_channel_to_resolved_category(
    channel: discord.TextChannel,
    resolved: TicketCategoryResolution,
    *,
    audit_reason: str,
) -> TicketLifecycleMoveResult:
    if channel_is_in_category(channel, resolved.category):
        return TicketLifecycleMoveResult(
            moved=False,
            already_correct=True,
            channel_id=int(channel.id),
            channel_name=str(channel.name),
            target_category_id=int(resolved.category_id),
            target_category_name=str(resolved.category_name),
            source=str(resolved.source),
            reason="already in target category",
        )

    await channel.edit(
        category=resolved.category,
        sync_permissions=False,
        reason=audit_reason,
    )

    _debug(
        f"moved channel={channel.id} name={channel.name!r} "
        f"target_category={resolved.category_id} source={resolved.source} reason={audit_reason!r}"
    )

    return TicketLifecycleMoveResult(
        moved=True,
        already_correct=False,
        channel_id=int(channel.id),
        channel_name=str(channel.name),
        target_category_id=int(resolved.category_id),
        target_category_name=str(resolved.category_name),
        source=str(resolved.source),
        reason=audit_reason,
    )


async def move_ticket_to_archive_category(
    channel: discord.TextChannel,
    *,
    refresh: bool = True,
) -> TicketLifecycleMoveResult:
    resolved = await resolve_archive_ticket_category(
        channel.guild,
        refresh=refresh,
        require_manage_channels=True,
    )
    return await _move_channel_to_resolved_category(
        channel,
        resolved,
        audit_reason="Ticket closed -> move to configured archive category",
    )


async def move_ticket_to_active_category(
    channel: discord.TextChannel,
    *,
    refresh: bool = True,
) -> TicketLifecycleMoveResult:
    resolved = await resolve_active_ticket_category(
        channel.guild,
        refresh=refresh,
        require_manage_channels=True,
    )
    return await _move_channel_to_resolved_category(
        channel,
        resolved,
        audit_reason="Ticket reopened -> move to configured active category",
    )


async def lifecycle_location_label(channel: discord.TextChannel) -> str:
    try:
        archive = await resolve_archive_ticket_category(channel.guild, refresh=False, require_manage_channels=False)
        if channel_is_in_category(channel, archive.category):
            return f"archive:{archive.category_name}"
    except Exception:
        pass

    try:
        active = await resolve_active_ticket_category(channel.guild, refresh=False, require_manage_channels=False)
        if channel_is_in_category(channel, active.category):
            return f"active:{active.category_name}"
    except Exception:
        pass

    try:
        if channel.category is not None:
            return f"category:{channel.category.name}"
    except Exception:
        pass

    return "uncategorized"


async def try_move_ticket_to_archive_category(channel: discord.TextChannel) -> bool:
    try:
        await move_ticket_to_archive_category(channel)
        return True
    except TicketCategoryResolutionError as e:
        _debug(f"archive move skipped channel={getattr(channel, 'id', None)}: {e}")
        return False
    except Exception as e:
        _debug(f"archive move failed channel={getattr(channel, 'id', None)}: {type(e).__name__}: {e}")
        return False


async def try_move_ticket_to_active_category(channel: discord.TextChannel) -> bool:
    try:
        await move_ticket_to_active_category(channel)
        return True
    except TicketCategoryResolutionError as e:
        _debug(f"active move skipped channel={getattr(channel, 'id', None)}: {e}")
        return False
    except Exception as e:
        _debug(f"active move failed channel={getattr(channel, 'id', None)}: {type(e).__name__}: {e}")
        return False


__all__ = [
    "TicketLifecycleMoveResult",
    "lifecycle_location_label",
    "move_ticket_to_active_category",
    "move_ticket_to_archive_category",
    "try_move_ticket_to_active_category",
    "try_move_ticket_to_archive_category",
]
