from __future__ import annotations

"""
Per-guild ticket category resolver.

This is the native source-level home for ticket category resolution. Runtime
patches should not contain the real business rules forever; they should call
this helper until ticket creation/close/reopen flows are fully folded into the
normal service layer.

Public-production rules:
- resolve from guild_configs, not private/env server IDs
- fail loudly when setup is missing instead of creating tickets in random places
- verify bot permissions before channel creation/move
- never search by a private server-specific name
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Optional

import discord

from ..guild_config import get_guild_config


class TicketCategoryResolutionError(RuntimeError):
    """Raised when a configured ticket category cannot be resolved safely."""


@dataclass(frozen=True)
class TicketCategoryResolution:
    guild_id: int
    category_id: int
    category_name: str
    category: discord.CategoryChannel
    source: str


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _setup_hint(label: str) -> str:
    return (
        f"{label} category is not configured. Run `/stoney setup`, then choose "
        "**Auto-Fix Missing Defaults** to create categories or **Choose Existing Items → Ticket Basics** "
        "to select your own existing categories."
    )


def _permission_missing_for_category(
    guild: discord.Guild,
    category: discord.CategoryChannel,
    *,
    require_manage_channels: bool = True,
) -> list[str]:
    missing: list[str] = []
    try:
        me = guild.me
        if me is None:
            return ["Bot member unavailable"]
        perms = category.permissions_for(me)
        if not perms.view_channel:
            missing.append("View Channel")
        if require_manage_channels and not perms.manage_channels:
            missing.append("Manage Channels")
        return missing
    except Exception:
        return ["Could not inspect category permissions"]


async def _load_guild_config(guild: discord.Guild, *, refresh: bool = True) -> Any:
    try:
        return await asyncio.wait_for(get_guild_config(guild.id, refresh=refresh), timeout=5.0)
    except Exception as e:
        raise TicketCategoryResolutionError(
            f"Could not load this server's saved ticket setup: {type(e).__name__}: {e}. Run `/stoney setup` and try again."
        ) from e


def _resolve_category_channel(guild: discord.Guild, category_id: int, *, label: str) -> discord.CategoryChannel:
    if category_id <= 0:
        raise TicketCategoryResolutionError(_setup_hint(label))

    channel = guild.get_channel(int(category_id))
    if not isinstance(channel, discord.CategoryChannel):
        raise TicketCategoryResolutionError(
            f"Configured {label} category `{category_id}` no longer exists or is not a category. "
            "Run `/stoney setup` and choose **Choose Existing Items → Ticket Basics** to select the correct category."
        )
    return channel


async def resolve_active_ticket_category(
    guild: discord.Guild,
    *,
    refresh: bool = True,
    require_manage_channels: bool = True,
) -> TicketCategoryResolution:
    cfg = await _load_guild_config(guild, refresh=refresh)
    category_id = _safe_int(getattr(cfg, "ticket_category_id", 0), 0)
    category = _resolve_category_channel(guild, category_id, label="open ticket")

    missing = _permission_missing_for_category(
        guild,
        category,
        require_manage_channels=require_manage_channels,
    )
    if missing:
        raise TicketCategoryResolutionError(
            f"I cannot create tickets in `{category.name}`. Missing: {', '.join(missing)}. "
            "Fix the category permissions or run `/stoney setup` and choose **Choose Existing Items → Ticket Basics**."
        )

    return TicketCategoryResolution(
        guild_id=int(guild.id),
        category_id=int(category.id),
        category_name=str(category.name),
        category=category,
        source=str(getattr(cfg, "source", "guild_configs")),
    )


async def resolve_archive_ticket_category(
    guild: discord.Guild,
    *,
    refresh: bool = True,
    require_manage_channels: bool = True,
) -> TicketCategoryResolution:
    cfg = await _load_guild_config(guild, refresh=refresh)
    category_id = _safe_int(getattr(cfg, "ticket_archive_category_id", 0), 0)
    category = _resolve_category_channel(guild, category_id, label="archive ticket")

    missing = _permission_missing_for_category(
        guild,
        category,
        require_manage_channels=require_manage_channels,
    )
    if missing:
        raise TicketCategoryResolutionError(
            f"I cannot move closed tickets to `{category.name}`. Missing: {', '.join(missing)}. "
            "Fix the category permissions or run `/stoney setup` and choose **Choose Existing Items → Ticket Basics**."
        )

    return TicketCategoryResolution(
        guild_id=int(guild.id),
        category_id=int(category.id),
        category_name=str(category.name),
        category=category,
        source=str(getattr(cfg, "source", "guild_configs")),
    )


def channel_is_in_category(channel: discord.TextChannel, category: Optional[discord.CategoryChannel]) -> bool:
    try:
        return bool(category is not None and int(getattr(channel.category, "id", 0) or 0) == int(category.id))
    except Exception:
        return False


async def assert_ticket_channel_in_active_category(channel: discord.TextChannel) -> None:
    resolved = await resolve_active_ticket_category(channel.guild, refresh=True, require_manage_channels=False)
    if not channel_is_in_category(channel, resolved.category):
        raise TicketCategoryResolutionError(
            f"Ticket channel `{channel.name}` was created outside configured open ticket category `{resolved.category_name}`. "
            "Run `/stoney setup` and verify **Ticket Basics**."
        )


__all__ = [
    "TicketCategoryResolution",
    "TicketCategoryResolutionError",
    "assert_ticket_channel_in_active_category",
    "channel_is_in_category",
    "resolve_active_ticket_category",
    "resolve_archive_ticket_category",
]
