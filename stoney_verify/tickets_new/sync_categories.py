from __future__ import annotations

"""
Native ticket sync/backfill category discovery helpers.

Startup sync/backfill must not depend on one env-only category or private server
naming. These helpers prefer per-guild guild_configs from the cache, then scan
only obvious ticket-looking channels to repair misplaced/open/closed tickets.

The async caller should load get_guild_config(guild.id, refresh=True) before
using these helpers so get_cached_guild_config has fresh data.
"""

import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import discord

from ..guild_config import get_cached_guild_config


TICKET_NAME_RE = re.compile(r"^(ticket|closed)-(\d{1,8})$", re.I)
TOPIC_OWNER_RE = re.compile(r"(?:^|;)(owner_id|requester_id)=\d{15,22}(?:;|$)", re.I)
TOPIC_NUMBER_RE = re.compile(r"(?:^|;)ticket_number=\d+(?:;|$)", re.I)
TOPIC_CATEGORY_RE = re.compile(r"(?:^|;)category=[^;]+(?:;|$)", re.I)


@dataclass(frozen=True)
class TicketSyncCategoryConfig:
    guild_id: int
    active_category_id: int
    archive_category_id: int
    transcripts_channel_id: int
    ticket_prefix: str
    source: str


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def sync_category_config_from_cache(guild: discord.Guild) -> TicketSyncCategoryConfig:
    cfg = get_cached_guild_config(int(guild.id))
    return TicketSyncCategoryConfig(
        guild_id=int(guild.id),
        active_category_id=_safe_int(getattr(cfg, "ticket_category_id", 0), 0),
        archive_category_id=_safe_int(getattr(cfg, "ticket_archive_category_id", 0), 0),
        transcripts_channel_id=_safe_int(getattr(cfg, "transcripts_channel_id", 0), 0),
        ticket_prefix=(_safe_str(getattr(cfg, "ticket_prefix", "ticket"), "ticket") or "ticket").lower(),
        source=_safe_str(getattr(cfg, "source", "cache"), "cache"),
    )


def _category_by_id(guild: discord.Guild, category_id: int) -> Optional[discord.CategoryChannel]:
    category_id = _safe_int(category_id, 0)
    if category_id <= 0:
        return None
    try:
        channel = guild.get_channel(category_id)
        if isinstance(channel, discord.CategoryChannel):
            return channel
    except Exception:
        pass
    return None


def active_category_from_cache(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    cfg = sync_category_config_from_cache(guild)
    return _category_by_id(guild, cfg.active_category_id)


def archive_category_from_cache(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    cfg = sync_category_config_from_cache(guild)
    return _category_by_id(guild, cfg.archive_category_id)


def configured_ticket_categories(guild: discord.Guild) -> list[discord.CategoryChannel]:
    out: list[discord.CategoryChannel] = []
    seen: set[int] = set()
    for category in (active_category_from_cache(guild), archive_category_from_cache(guild)):
        if category is None:
            continue
        cid = int(category.id)
        if cid in seen:
            continue
        seen.add(cid)
        out.append(category)
    return out


def is_transcript_channel(channel: discord.TextChannel) -> bool:
    try:
        cfg = sync_category_config_from_cache(channel.guild)
        return bool(cfg.transcripts_channel_id > 0 and int(channel.id) == int(cfg.transcripts_channel_id))
    except Exception:
        return False


def channel_is_in_configured_archive(channel: discord.TextChannel) -> bool:
    try:
        archive = archive_category_from_cache(channel.guild)
        return bool(archive is not None and channel.category is not None and int(channel.category.id) == int(archive.id))
    except Exception:
        return False


def channel_is_in_configured_active(channel: discord.TextChannel) -> bool:
    try:
        active = active_category_from_cache(channel.guild)
        return bool(active is not None and channel.category is not None and int(channel.category.id) == int(active.id))
    except Exception:
        return False


def channel_lifecycle_location(channel: discord.TextChannel) -> str:
    try:
        if channel_is_in_configured_archive(channel):
            return f"archive:{channel.category.name if channel.category else 'unknown'}"
        if channel_is_in_configured_active(channel):
            return f"active:{channel.category.name if channel.category else 'unknown'}"
        if channel.category is not None:
            return f"category:{channel.category.name}"
    except Exception:
        pass
    return "uncategorized"


def _topic(channel: discord.TextChannel) -> str:
    try:
        return str(channel.topic or "")
    except Exception:
        return ""


def _name(channel: discord.TextChannel) -> str:
    try:
        return str(channel.name or "").strip().lower()
    except Exception:
        return ""


def channel_looks_like_ticket(channel: discord.TextChannel) -> bool:
    if is_transcript_channel(channel):
        return False

    name = _name(channel)
    topic = _topic(channel)

    if TICKET_NAME_RE.match(name):
        return True

    if TOPIC_OWNER_RE.search(topic) and (TOPIC_NUMBER_RE.search(topic) or TOPIC_CATEGORY_RE.search(topic)):
        return True

    if channel_is_in_configured_archive(channel) and (TOPIC_OWNER_RE.search(topic) or TOPIC_NUMBER_RE.search(topic)):
        return True

    if channel_is_in_configured_active(channel) and (TOPIC_OWNER_RE.search(topic) or TOPIC_NUMBER_RE.search(topic) or TICKET_NAME_RE.match(name)):
        return True

    return False


def merge_unique_categories(
    guild: discord.Guild,
    extra_categories: Optional[Iterable[Any]] = None,
) -> list[discord.CategoryChannel]:
    out: list[discord.CategoryChannel] = []
    seen: set[int] = set()

    for category in configured_ticket_categories(guild):
        cid = int(category.id)
        if cid not in seen:
            out.append(category)
            seen.add(cid)

    for category in list(extra_categories or []):
        if not isinstance(category, discord.CategoryChannel):
            continue
        cid = int(category.id)
        if cid not in seen:
            out.append(category)
            seen.add(cid)

    return out


def candidate_ticket_channels(
    guild: discord.Guild,
    *,
    extra_categories: Optional[Iterable[Any]] = None,
    extra_channels: Optional[Iterable[Any]] = None,
) -> list[discord.TextChannel]:
    out: list[discord.TextChannel] = []
    seen: set[int] = set()

    for category in merge_unique_categories(guild, extra_categories):
        try:
            for channel in list(category.text_channels):
                if not isinstance(channel, discord.TextChannel):
                    continue
                cid = int(channel.id)
                if cid in seen or is_transcript_channel(channel):
                    continue
                if channel_looks_like_ticket(channel) or category in configured_ticket_categories(guild):
                    out.append(channel)
                    seen.add(cid)
        except Exception:
            continue

    for channel in list(extra_channels or []):
        if not isinstance(channel, discord.TextChannel):
            continue
        cid = int(channel.id)
        if cid in seen or is_transcript_channel(channel):
            continue
        if channel_looks_like_ticket(channel):
            out.append(channel)
            seen.add(cid)

    try:
        for channel in list(guild.text_channels):
            if not isinstance(channel, discord.TextChannel):
                continue
            cid = int(channel.id)
            if cid in seen or is_transcript_channel(channel):
                continue
            if channel_looks_like_ticket(channel):
                out.append(channel)
                seen.add(cid)
    except Exception:
        pass

    return out


__all__ = [
    "TicketSyncCategoryConfig",
    "active_category_from_cache",
    "archive_category_from_cache",
    "candidate_ticket_channels",
    "channel_is_in_configured_active",
    "channel_is_in_configured_archive",
    "channel_lifecycle_location",
    "channel_looks_like_ticket",
    "configured_ticket_categories",
    "is_transcript_channel",
    "merge_unique_categories",
    "sync_category_config_from_cache",
]
