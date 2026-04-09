from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import discord

from ..globals import TICKET_CATEGORY_ID, now_utc
from .repository import (
    get_ticket_by_any_channel_id,
    insert_ticket,
    safe_optional_update_by_channel_id,
    update_ticket_by_channel_id,
)
from .service import (
    _extract_ticket_number_from_name,
    _parse_owner_id_from_topic,
    _parse_ticket_number_from_topic,
    _safe_str,
    _title_for_ticket,
)

OPEN_STATUS = "open"
CLAIMED_STATUS = "claimed"
CLOSED_STATUS = "closed"
DELETED_STATUS = "deleted"

VALID_PRIORITIES = {"low", "medium", "high", "urgent"}

TICKET_NAME_RE = re.compile(r"^(ticket|closed)-(\d+)$", re.I)


def _utc_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        try:
            return dt.isoformat()
        except Exception:
            return None


def _safe_topic(channel: discord.TextChannel) -> str:
    try:
        return str(channel.topic or "")
    except Exception:
        return ""


def _guess_category_from_channel(channel: discord.TextChannel) -> str:
    topic = _safe_topic(channel)

    match = re.search(r"(?:^|;)category=([^;]+)(?:;|$)", topic)
    if match:
        value = str(match.group(1)).strip().lower()
        if value:
            return value

    name = str(channel.name or "").strip().lower()

    if "verify" in name:
        return "verification_issue"
    if "support" in name:
        return "support"
    if "ghost" in name:
        return "ghost"

    return "support"


def _guess_is_ghost(channel: discord.TextChannel) -> bool:
    try:
        topic = _safe_topic(channel).lower()
        if "ghost=true" in topic:
            return True
    except Exception:
        pass

    try:
        return "ghost" in str(channel.name or "").lower()
    except Exception:
        return False


def _guess_status_from_channel(channel: discord.TextChannel) -> str:
    try:
        name = str(channel.name or "").strip().lower()
        if name.startswith("closed-"):
            return CLOSED_STATUS
    except Exception:
        pass

    return OPEN_STATUS


async def _fetch_recent_messages(
    channel: discord.TextChannel,
    limit: int = 50,
) -> List[discord.Message]:
    rows: List[discord.Message] = []

    try:
        async for msg in channel.history(limit=limit, oldest_first=False):
            rows.append(msg)
    except Exception as e:
        print(f"⚠️ Failed reading recent messages for #{channel.name}: {repr(e)}")

    return rows


def _extract_claimed_staff_id_from_messages(
    messages: List[discord.Message],
) -> Optional[str]:
    """
    IMPORTANT:
    - Do NOT trust bot-authored "Ticket claimed by ..." messages as the claimer ID.
    - Prefer a mentioned user ID in the content.
    - Fall back to the non-bot message author only when appropriate.
    """
    for msg in messages:
        try:
            raw_content = str(msg.content or "")
            content = raw_content.lower()
        except Exception:
            raw_content = ""
            content = ""

        if not any(term in content for term in ("claimed", "assigned", "transferred")):
            continue

        try:
            mention_ids = re.findall(r"<@!?(\d+)>", raw_content)
            if mention_ids:
                for mention_id in mention_ids:
                    cleaned = str(mention_id).strip()
                    if cleaned:
                        return cleaned
        except Exception:
            pass

        try:
            explicit_id = re.search(
                r"(?:claimed by|assigned to|transferred to)\D{0,12}(\d{15,22})",
                raw_content,
                re.I,
            )
            if explicit_id:
                return str(explicit_id.group(1)).strip()
        except Exception:
            pass

        try:
            author = getattr(msg, "author", None)
            if author and getattr(author, "id", None) and not getattr(author, "bot", False):
                return str(author.id)
        except Exception:
            pass

    return None


def _extract_initial_message(messages: List[discord.Message]) -> str:
    if not messages:
        return ""

    ordered = list(reversed(messages))
    for msg in ordered:
        try:
            content = str(msg.content or "").strip()
        except Exception:
            content = ""
        if content:
            return content[:4000]

    return ""


def _extract_owner_member(
    guild: discord.Guild,
    channel: discord.TextChannel,
) -> Optional[discord.Member]:
    owner_id = _parse_owner_id_from_topic(channel)
    if owner_id:
        try:
            member = guild.get_member(int(owner_id))
            if member is not None:
                return member
        except Exception:
            pass

    try:
        for target, overwrite in (channel.overwrites or {}).items():
            if not isinstance(target, discord.Member):
                continue
            if getattr(target, "bot", False):
                continue
            if overwrite.view_channel is True:
                return target
    except Exception:
        pass

    return None


def _ticket_number_for_channel(channel: discord.TextChannel) -> Optional[int]:
    ticket_number = _extract_ticket_number_from_name(channel.name)
    if ticket_number is not None:
        return ticket_number

    ticket_number = _parse_ticket_number_from_topic(channel)
    if ticket_number is not None:
        return ticket_number

    return None


def _is_ticket_channel(channel: discord.TextChannel) -> bool:
    name = str(channel.name or "").strip().lower()
    topic = _safe_topic(channel).lower()

    if TICKET_NAME_RE.match(name):
        return True

    if "owner_id=" in topic and "ticket_number=" in topic:
        return True

    if "owner_id=" in topic and "category=" in topic:
        return True

    return False


def _discover_ticket_categories(guild: discord.Guild) -> List[discord.CategoryChannel]:
    categories: List[discord.CategoryChannel] = []

    if TICKET_CATEGORY_ID:
        try:
            ch = guild.get_channel(int(TICKET_CATEGORY_ID))
            if isinstance(ch, discord.CategoryChannel):
                categories.append(ch)
        except Exception:
            pass

    try:
        for cat in guild.categories:
            if cat in categories:
                continue

            name = str(cat.name or "").lower()
            if "ticket" in name or "verify" in name or "support" in name:
                categories.append(cat)
    except Exception:
        pass

    return categories


def _candidate_ticket_channels(guild: discord.Guild) -> List[discord.TextChannel]:
    categories = _discover_ticket_categories(guild)
    out: List[discord.TextChannel] = []
    seen: Set[int] = set()

    for category in categories:
        try:
            for channel in list(category.text_channels):
                if int(channel.id) in seen:
                    continue
                seen.add(int(channel.id))
                out.append(channel)
        except Exception:
            continue

    try:
        for channel in list(guild.text_channels):
            if int(channel.id) in seen:
                continue
            if not _is_ticket_channel(channel):
                continue
            seen.add(int(channel.id))
            out.append(channel)
    except Exception:
        pass

    return out


def _build_core_ticket_payload(
    *,
    guild: discord.Guild,
    channel: discord.TextChannel,
    owner: Optional[discord.Member],
    source: str,
    messages: List[discord.Message],
) -> Dict[str, Any]:
    category = _guess_category_from_channel(channel)
    is_ghost = _guess_is_ghost(channel)
    guessed_status = _guess_status_from_channel(channel)
    ticket_number = _ticket_number_for_channel(channel)
    claimed_by = _extract_claimed_staff_id_from_messages(messages)
    initial_message = _extract_initial_message(messages)
    now_iso = _utc_iso(now_utc())

    owner_id = str(owner.id) if owner else str(_parse_owner_id_from_topic(channel) or "")
    username = _safe_str(owner) if owner else (owner_id or "Unknown User")
    title = (
        _title_for_ticket(owner, category, is_ghost)
        if owner is not None
        else f"{'[GHOST] ' if is_ghost else ''}{category.title()} - {username}"[:180]
    )

    status = CLAIMED_STATUS if claimed_by and guessed_status == OPEN_STATUS else guessed_status

    return {
        "guild_id": str(guild.id),
        "user_id": owner_id or "",
        "username": username,
        "title": title,
        "category": "ghost" if is_ghost else category,
        "status": status,
        "priority": "medium",
        "claimed_by": claimed_by,
        "closed_by": None,
        "closed_reason": None,
        "initial_message": initial_message,
        "ai_category_confidence": 0,
        "mod_suggestion": None,
        "mod_suggestion_confidence": 0,
        "updated_at": now_iso,
        "discord_thread_id": str(channel.id),
        "channel_id": str(channel.id),
        "channel_name": channel.name,
        "ticket_number": int(ticket_number) if ticket_number is not None else None,
        "assigned_to": claimed_by,
        "reopened_at": None,
        "sla_deadline": None,
        "is_ghost": bool(is_ghost),
        "source": source,
    }


def _build_insert_payload(core: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(core)
    now_iso = _utc_iso(now_utc())

    payload["created_at"] = now_iso

    status = str(payload.get("status") or OPEN_STATUS).lower()

    if status in {OPEN_STATUS, CLAIMED_STATUS}:
        payload["closed_at"] = None
        payload["deleted_at"] = None
        payload["deleted_by"] = None
    elif status == CLOSED_STATUS:
        payload["closed_at"] = now_iso
        payload["deleted_at"] = None
        payload["deleted_by"] = None
    elif status == DELETED_STATUS:
        payload["closed_at"] = now_iso
        payload["deleted_at"] = now_iso
        payload["deleted_by"] = None

    return payload


def _build_update_payload(existing: Dict[str, Any], core: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(core)
    now_iso = _utc_iso(now_utc())

    if not str(payload.get("user_id") or "").strip():
        payload["user_id"] = str(existing.get("user_id") or "")

    if not str(payload.get("username") or "").strip():
        payload["username"] = str(existing.get("username") or "")

    if not str(payload.get("title") or "").strip():
        payload["title"] = str(existing.get("title") or "")

    if not str(payload.get("initial_message") or "").strip():
        payload["initial_message"] = str(existing.get("initial_message") or "")

    if payload.get("ticket_number") is None:
        payload["ticket_number"] = existing.get("ticket_number")

    existing_priority = str(existing.get("priority") or "").strip().lower()
    if existing_priority in VALID_PRIORITIES:
        payload["priority"] = existing_priority

    existing_source = str(existing.get("source") or "").strip()
    if existing_source:
        payload["source"] = existing_source

    if bool(existing.get("category_override")):
        existing_category = str(existing.get("category") or "").strip()
        if existing_category:
            payload["category"] = existing_category

    existing_status = str(existing.get("status") or "").lower()
    payload_status = str(payload.get("status") or OPEN_STATUS).lower()

    existing_claimed_by = str(existing.get("claimed_by") or "").strip()
    existing_assigned_to = str(existing.get("assigned_to") or "").strip()

    if not str(payload.get("claimed_by") or "").strip() and existing_claimed_by:
        payload["claimed_by"] = existing_claimed_by

    if not str(payload.get("assigned_to") or "").strip() and existing_assigned_to:
        payload["assigned_to"] = existing_assigned_to or existing_claimed_by

    if (
        existing_status == CLAIMED_STATUS
        and payload_status == OPEN_STATUS
        and existing_claimed_by
    ):
        payload["status"] = CLAIMED_STATUS
        payload["claimed_by"] = existing_claimed_by
        payload["assigned_to"] = existing_assigned_to or existing_claimed_by

    payload_status = str(payload.get("status") or OPEN_STATUS).lower()

    if payload_status in {OPEN_STATUS, CLAIMED_STATUS}:
        payload["closed_at"] = None
        payload["closed_by"] = None
        payload["closed_reason"] = None
        payload["deleted_at"] = None
        payload["deleted_by"] = None
    elif payload_status == CLOSED_STATUS:
        if existing_status != CLOSED_STATUS or not existing.get("closed_at"):
            payload["closed_at"] = now_iso
        else:
            payload["closed_at"] = existing.get("closed_at")
        payload["deleted_at"] = None
        payload["deleted_by"] = None
        payload["closed_by"] = existing.get("closed_by")
        payload["closed_reason"] = existing.get("closed_reason")
    elif payload_status == DELETED_STATUS:
        payload["deleted_at"] = existing.get("deleted_at") or now_iso
        payload["deleted_by"] = existing.get("deleted_by")
        payload["closed_at"] = existing.get("closed_at") or now_iso
        payload["closed_by"] = existing.get("closed_by")
        payload["closed_reason"] = existing.get("closed_reason")

    return payload


def _normalize_ticket_payload_for_compare(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "guild_id": str(payload.get("guild_id") or ""),
        "user_id": str(payload.get("user_id") or ""),
        "username": str(payload.get("username") or ""),
        "title": str(payload.get("title") or ""),
        "category": str(payload.get("category") or ""),
        "status": str(payload.get("status") or ""),
        "priority": str(payload.get("priority") or ""),
        "claimed_by": str(payload.get("claimed_by") or "") or None,
        "closed_by": str(payload.get("closed_by") or "") or None,
        "closed_reason": str(payload.get("closed_reason") or "") or None,
        "initial_message": str(payload.get("initial_message") or ""),
        "discord_thread_id": str(payload.get("discord_thread_id") or ""),
        "channel_id": str(payload.get("channel_id") or ""),
        "channel_name": str(payload.get("channel_name") or ""),
        "ticket_number": payload.get("ticket_number"),
        "assigned_to": str(payload.get("assigned_to") or "") or None,
        "is_ghost": bool(payload.get("is_ghost")),
        "source": str(payload.get("source") or ""),
    }


def _existing_row_matches_payload(existing: Dict[str, Any], payload: Dict[str, Any]) -> bool:
    left = _normalize_ticket_payload_for_compare(existing)
    right = _normalize_ticket_payload_for_compare(payload)
    return left == right


async def _sync_channel_internal(
    *,
    channel: discord.TextChannel,
    source: str,
    dry_run: bool,
) -> Dict[str, Any]:
    guild = channel.guild
    owner = _extract_owner_member(guild, channel)
    messages = await _fetch_recent_messages(channel, limit=50)
    core_payload = _build_core_ticket_payload(
        guild=guild,
        channel=channel,
        owner=owner,
        source=source,
        messages=messages,
    )

    existing = await get_ticket_by_any_channel_id(channel.id)

    row_summary: Dict[str, Any] = {
        "channel_id": str(channel.id),
        "channel_name": channel.name,
        "status": core_payload.get("status"),
        "user_id": core_payload.get("user_id"),
        "ticket_number": core_payload.get("ticket_number"),
        "action": "unchanged",
    }

    if dry_run:
        row_summary["action"] = "would_update" if existing else "would_insert"
        return row_summary

    if existing:
        update_payload = _build_update_payload(existing, core_payload)

        if _existing_row_matches_payload(existing, update_payload):
            row_summary["action"] = "unchanged"
            return row_summary

        updated_row = await update_ticket_by_channel_id(
            channel.id,
            update_payload,
            allow_thread_fallback=True,
        )
        if updated_row is not None:
            try:
                await safe_optional_update_by_channel_id(
                    channel.id,
                    {
                        "channel_name": channel.name,
                        "is_ghost": core_payload.get("is_ghost"),
                        "ticket_number": core_payload.get("ticket_number"),
                    },
                )
            except Exception:
                pass

            row_summary["action"] = "updated"
            return row_summary

        row_summary["action"] = "error"
        return row_summary

    insert_payload = _build_insert_payload(core_payload)
    inserted_row = await insert_ticket(insert_payload)
    if inserted_row is not None:
        try:
            await safe_optional_update_by_channel_id(
                channel.id,
                {
                    "channel_name": channel.name,
                    "is_ghost": core_payload.get("is_ghost"),
                    "ticket_number": core_payload.get("ticket_number"),
                },
            )
        except Exception:
            pass

        row_summary["action"] = "inserted"
        return row_summary

    row_summary["action"] = "error"
    return row_summary


async def sync_active_ticket_channels_for_guild(
    guild: discord.Guild,
    *,
    source: str = "discord_sync",
    include_closed_visible_channels: bool = True,
    dry_run: bool = False,
) -> Dict[str, Any]:
    categories = _discover_ticket_categories(guild)
    channels = _candidate_ticket_channels(guild)

    summary: Dict[str, Any] = {
        "guild_id": str(guild.id),
        "categories_scanned": len(categories),
        "channels_scanned": 0,
        "matched_ticket_channels": 0,
        "inserted": 0,
        "updated": 0,
        "unchanged": 0,
        "skipped": 0,
        "errors": 0,
        "rows": [],
        "dry_run": bool(dry_run),
    }

    seen: Set[int] = set()

    for channel in channels:
        if int(channel.id) in seen:
            continue
        seen.add(int(channel.id))

        summary["channels_scanned"] += 1

        if not _is_ticket_channel(channel):
            summary["skipped"] += 1
            summary["rows"].append(
                {
                    "channel_id": str(channel.id),
                    "channel_name": channel.name,
                    "action": "skipped",
                    "reason": "not_ticket_channel",
                }
            )
            continue

        status = _guess_status_from_channel(channel)
        if status == CLOSED_STATUS and not include_closed_visible_channels:
            summary["skipped"] += 1
            summary["rows"].append(
                {
                    "channel_id": str(channel.id),
                    "channel_name": channel.name,
                    "action": "skipped",
                    "reason": "closed_filtered_out",
                }
            )
            continue

        summary["matched_ticket_channels"] += 1

        try:
            row_summary = await _sync_channel_internal(
                channel=channel,
                source=source,
                dry_run=dry_run,
            )
            action = str(row_summary.get("action") or "unknown")

            if action == "inserted":
                summary["inserted"] += 1
            elif action == "updated":
                summary["updated"] += 1
            elif action == "unchanged":
                summary["unchanged"] += 1
            elif action == "error":
                summary["errors"] += 1

            summary["rows"].append(row_summary)
        except Exception as e:
            summary["errors"] += 1
            summary["rows"].append(
                {
                    "channel_id": str(channel.id),
                    "channel_name": channel.name,
                    "action": "error",
                    "error": repr(e),
                }
            )

    return summary


async def sync_one_ticket_channel(
    channel: discord.TextChannel,
    *,
    source: str = "discord_sync_one",
    dry_run: bool = False,
) -> Dict[str, Any]:
    guild = channel.guild

    if not _is_ticket_channel(channel):
        return {
            "guild_id": str(guild.id),
            "categories_scanned": 0,
            "channels_scanned": 1,
            "matched_ticket_channels": 0,
            "inserted": 0,
            "updated": 0,
            "unchanged": 0,
            "skipped": 1,
            "errors": 0,
            "rows": [
                {
                    "channel_id": str(channel.id),
                    "channel_name": channel.name,
                    "action": "skipped",
                    "reason": "not_ticket_channel",
                }
            ],
            "dry_run": bool(dry_run),
        }

    try:
        row_summary = await _sync_channel_internal(
            channel=channel,
            source=source,
            dry_run=dry_run,
        )

        result: Dict[str, Any] = {
            "guild_id": str(guild.id),
            "categories_scanned": 0,
            "channels_scanned": 1,
            "matched_ticket_channels": 1,
            "inserted": 0,
            "updated": 0,
            "unchanged": 0,
            "skipped": 0,
            "errors": 0,
            "rows": [row_summary],
            "dry_run": bool(dry_run),
        }

        action = str(row_summary.get("action") or "unknown")
        if action == "inserted":
            result["inserted"] = 1
        elif action == "updated":
            result["updated"] = 1
        elif action == "unchanged":
            result["unchanged"] = 1
        elif action == "error":
            result["errors"] = 1

        return result

    except Exception as e:
        return {
            "guild_id": str(guild.id),
            "categories_scanned": 0,
            "channels_scanned": 1,
            "matched_ticket_channels": 1,
            "inserted": 0,
            "updated": 0,
            "unchanged": 0,
            "skipped": 0,
            "errors": 1,
            "rows": [
                {
                    "channel_id": str(channel.id),
                    "channel_name": channel.name,
                    "action": "error",
                    "error": repr(e),
                }
            ],
            "dry_run": bool(dry_run),
        }


__all__ = [
    "OPEN_STATUS",
    "CLAIMED_STATUS",
    "CLOSED_STATUS",
    "DELETED_STATUS",
    "sync_active_ticket_channels_for_guild",
    "sync_one_ticket_channel",
]