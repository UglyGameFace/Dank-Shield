from __future__ import annotations

"""First-class Channel Builder runtime service.

This module owns the Discord-facing Channel Builder behavior. API/startup guards
should call into this service instead of carrying mutation logic inline.
"""

from typing import Any, Optional

import discord
from aiohttp import web

DISCORD_CHANNEL_LIMIT = 500
CATEGORY_CHILD_LIMIT = 50


def safe_str(value: Any, default: str = "") -> str:
    try:
        if value is None:
            return default
        text = str(value).strip()
        return text if text else default
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def normalize_action(value: Any) -> str:
    text = safe_str(value).lower().replace("-", "_")
    if text in {"create", "rename", "keep", "skip", "conflict"}:
        return text
    return "skip"


def normalize_channel_type(value: Any) -> str:
    text = safe_str(value).lower().replace("announcement", "news")
    if text in {"text", "voice", "forum", "news", "category"}:
        return text
    return "text"


def normalize_channel_builder_items(raw: Any, *, limit: int = 150) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    items: list[dict[str, Any]] = []
    for index, row in enumerate(raw[:limit]):
        if not isinstance(row, dict):
            continue
        action = normalize_action(row.get("action"))
        selected = row.get("selected") is not False
        if not selected:
            action = "skip"
        items.append(
            {
                "index": index,
                "id": safe_str(row.get("id") or f"row-{index + 1}"),
                "action": action,
                "type": normalize_channel_type(row.get("type")),
                "base_name": safe_str(row.get("baseName") or row.get("base_name") or row.get("name"))[:100],
                "final_name": safe_str(row.get("finalName") or row.get("final_name"))[:100],
                "current_name": safe_str(row.get("currentName") or row.get("current_name"))[:100],
                "current_id": safe_int(
                    row.get("channelId")
                    or row.get("channel_id")
                    or row.get("currentChannelId")
                    or row.get("current_channel_id")
                    or row.get("currentId")
                    or row.get("current_id"),
                    0,
                ),
                "category": safe_str(row.get("category"))[:100],
                "protected": bool(row.get("protected")),
                "selected": selected,
            }
        )
    return items


def validate_channel_builder_items(items: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    targets: dict[str, int] = {}
    for item in items:
        action = item.get("action")
        if action in {"skip", "keep"}:
            continue
        final_name = safe_str(item.get("final_name"))
        if not final_name:
            errors.append(f"row {int(item.get('index', 0)) + 1}: final_name required")
            continue
        if len([*final_name]) > 100:
            errors.append(f"row {int(item.get('index', 0)) + 1}: final_name is over Discord's 100 character limit")
        key = final_name.lower()
        if key in targets:
            errors.append(f"duplicate target name #{final_name}")
        targets[key] = int(item.get("index", 0))
        if action == "conflict":
            errors.append(f"row {int(item.get('index', 0)) + 1}: conflict must be fixed before queueing")
        if action == "rename" and not item.get("current_id") and not item.get("current_name"):
            errors.append(f"row {int(item.get('index', 0)) + 1}: rename requires current channel id or current name")
    return errors[:25]


async def get_guild_or_response(server: Any, guild_id: Any) -> tuple[Optional[discord.Guild], Optional[web.Response]]:
    if hasattr(server, "_get_guild_or_error"):
        return await server._get_guild_or_error(guild_id)
    gid = safe_int(guild_id, 0)
    guild = server.bot.get_guild(gid) if gid else None
    if guild is None:
        return None, server._json_error("Guild not found", 404)
    return guild, None


def channel_kind(channel: Any) -> str:
    if isinstance(channel, discord.CategoryChannel):
        return "category"
    if isinstance(channel, discord.VoiceChannel):
        return "voice"
    if getattr(discord, "ForumChannel", None) and isinstance(channel, discord.ForumChannel):
        return "forum"
    if isinstance(channel, discord.TextChannel):
        try:
            return "news" if bool(channel.is_news()) else "text"
        except Exception:
            return "text"
    return safe_str(getattr(channel, "type", "unknown"), "unknown")


def channel_payload(channel: Any) -> dict[str, Any]:
    parent = getattr(channel, "category", None)
    return {
        "id": str(getattr(channel, "id", "")),
        "name": safe_str(getattr(channel, "name", "")),
        "type": channel_kind(channel),
        "position": getattr(channel, "position", None),
        "category_id": str(getattr(parent, "id", "")) if parent else None,
        "category_name": safe_str(getattr(parent, "name", "")) if parent else None,
        "mention": safe_str(getattr(channel, "mention", "")),
    }


def snapshot_channel(channel: Any) -> dict[str, Any]:
    parent = getattr(channel, "category", None)
    return {
        "channel_id": str(getattr(channel, "id", "")),
        "name": safe_str(getattr(channel, "name", "")),
        "type": channel_kind(channel),
        "category_id": str(getattr(parent, "id", "")) if parent else None,
        "category_name": safe_str(getattr(parent, "name", "")) if parent else None,
        "position": getattr(channel, "position", None),
        "nsfw": bool(getattr(channel, "nsfw", False)),
        "slowmode_delay": getattr(channel, "slowmode_delay", None),
        "sync_permissions": getattr(channel, "permissions_synced", None),
    }


def sort_channels(channels: list[Any]) -> list[Any]:
    def key(channel: Any) -> tuple[int, int, str]:
        parent = getattr(channel, "category", None)
        parent_pos = getattr(parent, "position", -1) if parent else -1
        return (int(parent_pos or -1), int(getattr(channel, "position", 0) or 0), safe_str(getattr(channel, "name", "")))

    return sorted(channels, key=key)


def find_category(guild: discord.Guild, name: str) -> Optional[discord.CategoryChannel]:
    target = safe_str(name).lower()
    if not target:
        return None
    for category in getattr(guild, "categories", []) or []:
        if safe_str(category.name).lower() == target:
            return category
    return None


async def ensure_category(guild: discord.Guild, name: str, *, reason: str) -> Optional[discord.CategoryChannel]:
    existing = find_category(guild, name)
    if existing is not None:
        return existing
    if not name:
        return None
    return await guild.create_category(name=name[:100], reason=reason)


def find_channel(guild: discord.Guild, item: dict[str, Any]) -> Optional[discord.abc.GuildChannel]:
    current_id = safe_int(item.get("current_id"), 0)
    if current_id > 0:
        channel = guild.get_channel(current_id)
        if channel is not None:
            return channel
    current_name = safe_str(item.get("current_name")).lower()
    if current_name:
        for channel in getattr(guild, "channels", []) or []:
            if safe_str(getattr(channel, "name", "")).lower() == current_name:
                return channel
    return None


def bot_member_for(guild: discord.Guild) -> Optional[discord.Member]:
    try:
        member = getattr(guild, "me", None)
        if member is not None:
            return member
        client_user = getattr(getattr(guild, "_state", None), "user", None)
        return guild.get_member(int(getattr(client_user, "id", 0))) if client_user else None
    except Exception:
        return None


def preflight_channel_builder_plan(guild: discord.Guild, items: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    member = bot_member_for(guild)
    if member is None:
        errors.append("Bot member could not be resolved in this guild.")
    elif not getattr(member.guild_permissions, "manage_channels", False):
        errors.append("Bot is missing Manage Channels permission.")

    create_items = [item for item in items if item.get("action") == "create"]
    if len(getattr(guild, "channels", []) or []) + len(create_items) > DISCORD_CHANNEL_LIMIT:
        errors.append("This plan may exceed Discord's guild channel limit.")

    category_adds: dict[str, int] = {}
    for item in create_items:
        if item.get("type") == "forum" and not hasattr(guild, "create_forum"):
            errors.append("Forum channels are not supported by the installed discord.py version.")
        category = safe_str(item.get("category"))
        if category:
            category_adds[category.lower()] = category_adds.get(category.lower(), 0) + 1
    for category in getattr(guild, "categories", []) or []:
        count = len(getattr(category, "channels", []) or []) + category_adds.get(safe_str(category.name).lower(), 0)
        if count > CATEGORY_CHILD_LIMIT:
            errors.append(f"Category {category.name} may exceed Discord's 50 channel category child limit.")

    for item in items:
        if item.get("action") == "rename" and find_channel(guild, item) is None:
            errors.append(f"row {int(item.get('index', 0)) + 1}: existing channel not found for rename")

    return {"ok": not errors, "errors": errors[:25], "warnings": warnings[:25]}


async def create_channel(guild: discord.Guild, item: dict[str, Any], *, reason: str) -> dict[str, Any]:
    channel_type = safe_str(item.get("type"), "text")
    final_name = safe_str(item.get("final_name"))[:100]
    category = await ensure_category(guild, safe_str(item.get("category")), reason=reason)

    if channel_type == "voice":
        channel = await guild.create_voice_channel(name=final_name, category=category, reason=reason)
    elif channel_type == "forum" and hasattr(guild, "create_forum"):
        channel = await guild.create_forum(name=final_name, category=category, reason=reason)
    elif channel_type == "news":
        try:
            channel = await guild.create_text_channel(name=final_name, category=category, news=True, reason=reason)
        except TypeError:
            channel = await guild.create_text_channel(name=final_name, category=category, reason=reason)
    elif channel_type == "category":
        channel = await ensure_category(guild, final_name, reason=reason)
    else:
        channel = await guild.create_text_channel(name=final_name, category=category, reason=reason)

    snapshot = snapshot_channel(channel)
    return {
        "ok": True,
        "action": "create",
        "row_id": item.get("id"),
        "channel_id": str(getattr(channel, "id", "")),
        "name": getattr(channel, "name", final_name),
        "type": channel_type,
        "snapshot_after": snapshot,
        "rollback": {"action": "delete_created_channel", "channel_id": snapshot.get("channel_id"), "name": snapshot.get("name")},
    }


async def rename_channel(guild: discord.Guild, item: dict[str, Any], *, reason: str) -> dict[str, Any]:
    channel = find_channel(guild, item)
    if channel is None:
        return {"ok": False, "action": "rename", "row_id": item.get("id"), "error": "existing channel not found"}
    before_snapshot = snapshot_channel(channel)
    before = safe_str(getattr(channel, "name", ""))
    final_name = safe_str(item.get("final_name"))[:100]
    if before == final_name:
        return {"ok": True, "action": "keep", "row_id": item.get("id"), "channel_id": str(getattr(channel, "id", "")), "name": before, "snapshot_before": before_snapshot}
    await channel.edit(name=final_name, reason=reason)
    after_snapshot = snapshot_channel(channel)
    return {
        "ok": True,
        "action": "rename",
        "row_id": item.get("id"),
        "channel_id": str(getattr(channel, "id", "")),
        "before": before,
        "after": final_name,
        "snapshot_before": before_snapshot,
        "snapshot_after": after_snapshot,
        "rollback": {
            "action": "rename_channel",
            "channel_id": before_snapshot.get("channel_id"),
            "name": before_snapshot.get("name"),
            "category_id": before_snapshot.get("category_id"),
            "position": before_snapshot.get("position"),
        },
    }


async def execute_channel_builder_plan(*, server: Any, guild_id: int, actor_id: int, items: list[dict[str, Any]], mode: str, dry_run: bool) -> dict[str, Any]:
    guild, err = await get_guild_or_response(server, guild_id)
    if err is not None:
        return {"status": "failed", "error": "guild not found"}
    assert guild is not None

    preflight = preflight_channel_builder_plan(guild, items)
    if not preflight.get("ok"):
        return {"status": "failed", "guild_id": str(guild_id), "preflight": preflight, "error": "Channel Builder preflight failed"}

    reason = f"Dank Shield Channel Builder {mode} by {actor_id or 'dashboard'}"
    results: list[dict[str, Any]] = []
    rollback_plan: list[dict[str, Any]] = []
    counts = {"create": 0, "rename": 0, "keep": 0, "skip": 0, "failed": 0}

    for item in items:
        action = safe_str(item.get("action"))
        if action in {"skip", "conflict"}:
            counts["skip"] += 1
            results.append({"ok": True, "action": "skip", "row_id": item.get("id")})
            continue
        if action == "keep":
            counts["keep"] += 1
            results.append({"ok": True, "action": "keep", "row_id": item.get("id"), "name": item.get("final_name")})
            continue
        if dry_run:
            counts[action if action in counts else "skip"] = counts.get(action, 0) + 1
            results.append({"ok": True, "dry_run": True, "action": action, "row_id": item.get("id"), "target": item.get("final_name"), "channel_id": item.get("current_id") or None})
            continue
        try:
            if action == "create":
                result = await create_channel(guild, item, reason=reason)
                counts["create"] += 1
            elif action == "rename":
                result = await rename_channel(guild, item, reason=reason)
                if result.get("ok"):
                    counts["rename"] += 1 if result.get("action") == "rename" else 0
                    counts["keep"] += 1 if result.get("action") == "keep" else 0
                else:
                    counts["failed"] += 1
            else:
                result = {"ok": True, "action": "skip", "row_id": item.get("id")}
                counts["skip"] += 1
        except Exception as exc:
            counts["failed"] += 1
            result = {"ok": False, "action": action, "row_id": item.get("id"), "error": repr(exc)}
        if isinstance(result, dict) and result.get("rollback"):
            rollback_plan.append(dict(result["rollback"]))
        results.append(result)

    rollback_plan.reverse()
    return {
        "status": "partial" if counts["failed"] else "succeeded",
        "mode": mode,
        "dry_run": dry_run,
        "guild_id": str(guild_id),
        "preflight": preflight,
        "counts": counts,
        "results": results,
        "rollback_plan": rollback_plan,
        "rollback_available": bool(rollback_plan),
    }


async def list_channels_payload(*, server: Any, guild_id: Any) -> tuple[dict[str, Any] | None, web.Response | None]:
    guild, err = await get_guild_or_response(server, guild_id)
    if err is not None:
        return None, err
    assert guild is not None
    channels = [
        channel
        for channel in sort_channels(list(getattr(guild, "channels", []) or []))
        if channel_kind(channel) in {"category", "text", "news", "voice", "forum"}
    ]
    return {"guild_id": str(guild.id), "channels": [channel_payload(channel) for channel in channels], "total": len(channels)}, None
