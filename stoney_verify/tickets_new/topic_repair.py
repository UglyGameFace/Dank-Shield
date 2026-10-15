"""Explicit, bounded cleanup of legacy member-visible ticket topics.

No startup jobs, silent sweeping, database mutation or opportunistic repair.
Only a staff-triggered preview and guild-manager-confirmed apply can run this.
Every edit must re-verify the matching durable DB ticket row.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any, Sequence

import discord

from .repository import get_ticket_by_any_channel_id
from .sync_categories import (
    channel_is_in_configured_active,
    channel_is_in_configured_archive,
    is_transcript_channel,
)
from .topic_policy import PUBLIC_TICKET_TOPIC, is_public_ticket_topic

# Intentionally strict. Unknown/custom topics must never be overwritten.
_LEGACY_TOPIC = re.compile(
    r"owner_id=(?P<owner>\d+);"
    r"category=(?P<category>[a-zA-Z0-9_-]+);"
    r"ghost=(?P<ghost>true|false);"
    r"ticket_number=(?P<number>\d+)",
    re.IGNORECASE,
)
MAX_INSPECT = 25
MAX_REPAIR = 10


@dataclass(frozen=True)
class TopicDecision:
    safe: bool
    reason: str


@dataclass(frozen=True)
class TopicPreview:
    eligible_ids: tuple[int, ...]
    candidates_checked: int
    legacy_topics_seen: int
    skipped: int
    remaining_unchecked: bool


def _positive_int(value: Any) -> int:
    try:
        raw = str(value or "").strip()
        result = int(raw)
        return result if result > 0 else 0
    except (ValueError, TypeError):
        return 0


def _strict_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if str(value).strip().lower() in {"true", "1"}:
        return True
    if str(value).strip().lower() in {"false", "0"}:
        return False
    return None


def evaluate_legacy_topic(
    *,
    guild_id: int,
    channel_id: int,
    topic: str,
    row: dict[str, Any] | None,
) -> TopicDecision:
    """Return safe=True only for a matching canonical legacy topic and row."""
    if is_public_ticket_topic(topic):
        return TopicDecision(False, "already_clean")
    matched = _LEGACY_TOPIC.fullmatch(str(topic or "").strip())
    if matched is None:
        return TopicDecision(False, "custom_or_unknown_topic")
    if not isinstance(row, dict):
        return TopicDecision(False, "no_durable_record")

    gid = _positive_int(guild_id)
    cid = _positive_int(channel_id)
    if not gid or not cid or _positive_int(row.get("guild_id")) != gid:
        return TopicDecision(False, "guild_mismatch")

    # Require at least one channel ID and reject ANY conflicting stored ID.
    ids = [
        _positive_int(row.get("channel_id")),
        _positive_int(row.get("discord_thread_id")),
    ]
    present_ids = [v for v in ids if v]
    if not present_ids or any(v != cid for v in present_ids):
        return TopicDecision(False, "channel_mismatch")

    if str(row.get("status") or "").strip().lower() not in {"open", "claimed", "closed"}:
        return TopicDecision(False, "invalid_ticket_status")

    topic_owner = _positive_int(matched.group("owner"))
    owners = [
        _positive_int(row.get(key))
        for key in ("user_id", "owner_id", "requester_id")
    ]
    owners = [v for v in owners if v]
    if not topic_owner or not owners or any(v != topic_owner for v in owners):
        return TopicDecision(False, "owner_mismatch")

    if _positive_int(row.get("ticket_number")) != _positive_int(matched.group("number")):
        return TopicDecision(False, "ticket_number_mismatch")
    if not _positive_int(matched.group("number")):
        return TopicDecision(False, "invalid_ticket_number")

    if str(row.get("category") or "").strip().casefold() != matched.group("category").casefold():
        return TopicDecision(False, "category_mismatch")
    if _strict_bool(row.get("is_ghost")) is not (matched.group("ghost").lower() == "true"):
        return TopicDecision(False, "ghost_mismatch")

    return TopicDecision(True, "verified")


def _in_ticket_area(channel: discord.TextChannel) -> bool:
    if is_transcript_channel(channel):
        return False
    return channel_is_in_configured_active(channel) or channel_is_in_configured_archive(channel)


async def preview_legacy_topics(guild: discord.Guild) -> TopicPreview:
    """At most 25 DB lookups and 10 eligible channels, across one guild only."""
    try:
        from ..guild_config import get_guild_config
        await get_guild_config(int(guild.id), refresh=True)
    except Exception:
        # If the authoritative ticket categories cannot be resolved, do not
        # try to guess which channels are safe.
        return TopicPreview((), 0, 0, 0, True)

    inspected = legacy = skipped = 0
    eligible: list[int] = []
    remaining = False
    for channel in guild.text_channels:
        if not isinstance(channel, discord.TextChannel) or not _in_ticket_area(channel):
            continue
        if not _LEGACY_TOPIC.fullmatch(str(channel.topic or "").strip()):
            continue
        legacy += 1
        if inspected >= MAX_INSPECT or len(eligible) >= MAX_REPAIR:
            remaining = True
            break
        inspected += 1
        try:
            row = await get_ticket_by_any_channel_id(channel.id)
        except Exception:
            row = None
        decision = evaluate_legacy_topic(
            guild_id=guild.id, channel_id=channel.id,
            topic=channel.topic or "", row=row,
        )
        if decision.safe:
            eligible.append(int(channel.id))
        else:
            skipped += 1
    return TopicPreview(tuple(eligible), inspected, legacy, skipped, remaining)


async def repair_verified_topics(
    guild: discord.Guild, channel_ids: Sequence[int]
) -> dict[str, int]:
    """Repair confirmed IDs only, with fresh row, topic and permissions checks."""
    summary = {"cleaned": 0, "skipped": 0, "failed": 0}
    requested = [int(i) for i in channel_ids]
    if not requested or len(requested) > MAX_REPAIR or len(set(requested)) != len(requested):
        return {"cleaned": 0, "skipped": len(requested), "failed": 0}

    try:
        from ..guild_config import get_guild_config
        await get_guild_config(int(guild.id), refresh=True)
    except Exception:
        return {"cleaned": 0, "skipped": len(requested), "failed": 0}

    me = guild.me
    if not isinstance(me, discord.Member):
        return {"cleaned": 0, "skipped": len(requested), "failed": 0}

    for cid in requested:
        channel = guild.get_channel(cid)
        if not isinstance(channel, discord.TextChannel) or not _in_ticket_area(channel):
            summary["skipped"] += 1
            continue
        if not channel.permissions_for(me).manage_channels:
            summary["skipped"] += 1
            continue
        before = str(channel.topic or "")
        try:
            row = await get_ticket_by_any_channel_id(channel.id)
        except Exception:
            summary["skipped"] += 1
            continue
        decision = evaluate_legacy_topic(
            guild_id=guild.id, channel_id=channel.id, topic=before, row=row,
        )
        if not decision.safe or str(channel.topic or "") != before:
            summary["skipped"] += 1
            continue
        try:
            await channel.edit(
                topic=PUBLIC_TICKET_TOPIC,
                reason="Dank Shield: owner-confirmed ticket topic privacy repair",
            )
            summary["cleaned"] += 1
        except (discord.HTTPException, discord.Forbidden):
            summary["failed"] += 1
        if summary["cleaned"] + summary["failed"] < len(requested):
            await asyncio.sleep(0.5)
    return summary
