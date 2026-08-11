from __future__ import annotations

"""Canonical Channel Builder listing, preflight, and Discord mutation service."""

import asyncio
from typing import Any, Optional

import discord

from . import channel_builder_runtime as rt
from ..operation_queue import with_retry


def _channel_name(value: Any) -> str:
    try:
        return str(getattr(value, "name", "") or "").strip()
    except Exception:
        return ""


def _find_channel(guild: discord.Guild, item: dict[str, Any]) -> Optional[discord.abc.GuildChannel]:
    current_id = rt.safe_int(item.get("current_id"), 0)
    if current_id:
        channel = guild.get_channel(current_id)
        if isinstance(channel, discord.abc.GuildChannel):
            return channel
    wanted = str(item.get("current_name") or item.get("base_name") or "").strip().lower()
    if wanted:
        for channel in list(getattr(guild, "channels", []) or []):
            if _channel_name(channel).lower() == wanted:
                return channel
    return None


def _find_category(guild: discord.Guild, name: str) -> Optional[discord.CategoryChannel]:
    wanted = str(name or "").strip().lower()
    if not wanted:
        return None
    for category in list(getattr(guild, "categories", []) or []):
        if _channel_name(category).lower() == wanted:
            return category
    return None


async def list_channels_payload(*, server: Any, guild_id: Any) -> tuple[dict[str, Any] | None, Any | None]:
    guild, err = await rt.get_guild_or_response(server, guild_id)
    if err is not None:
        return None, err
    assert guild is not None
    channels = [rt.channel_payload(channel) for channel in list(getattr(guild, "channels", []) or [])]
    categories = [rt.channel_payload(category) for category in list(getattr(guild, "categories", []) or [])]
    return {
        "guild_id": str(getattr(guild, "id", "")),
        "guild_name": str(getattr(guild, "name", "")),
        "channel_count": len(channels),
        "categories": categories,
        "channels": channels,
    }, None


def preflight_channel_builder_plan(guild: discord.Guild, items: list[dict[str, Any]]) -> dict[str, Any]:
    errors = list(rt.validate_channel_builder_items(items) or [])
    create_items = [item for item in items if item.get("action") == "create"]
    rename_items = [item for item in items if item.get("action") == "rename"]
    current_count = len(list(getattr(guild, "channels", []) or []))
    if current_count + len(create_items) > rt.DISCORD_CHANNEL_LIMIT:
        errors.append(f"server channel limit would be exceeded: {current_count}+{len(create_items)}>{rt.DISCORD_CHANNEL_LIMIT}")

    category_child_counts = {
        _channel_name(category).lower(): len(list(getattr(category, "channels", []) or []))
        for category in list(getattr(guild, "categories", []) or [])
    }
    planned_category_adds: dict[str, int] = {}
    for item in create_items:
        category_name = str(item.get("category") or "").strip().lower()
        if category_name:
            planned_category_adds[category_name] = planned_category_adds.get(category_name, 0) + 1
    for category_name, add_count in planned_category_adds.items():
        existing = category_child_counts.get(category_name, 0)
        if existing + add_count > rt.CATEGORY_CHILD_LIMIT:
            errors.append(f"category #{category_name} would exceed Discord child limit: {existing}+{add_count}>{rt.CATEGORY_CHILD_LIMIT}")

    me = getattr(guild, "me", None)
    try:
        perms = me.guild_permissions if isinstance(me, discord.Member) else None
        if create_items and not bool(perms and (perms.manage_channels or perms.administrator)):
            errors.append("Dank Shield is missing Manage Channels for channel creation.")
    except Exception:
        errors.append("Dank Shield channel-management permissions could not be verified.")

    return {
        "ok": not errors,
        "errors": errors[:25],
        "creates": len(create_items),
        "renames": len(rename_items),
        "skips": len([item for item in items if item.get("action") in {"skip", "keep"}]),
        "current_channel_count": current_count,
        "planned_channel_count": current_count + len(create_items),
    }


async def execute_channel_builder_plan(
    *,
    server: Any,
    guild_id: Any,
    actor_id: Any = 0,
    items: list[dict[str, Any]],
    mode: str = "apply_plan",
    dry_run: bool = False,
) -> dict[str, Any]:
    gid = rt.safe_int(guild_id, 0)
    guild = server.bot.get_guild(gid) if gid else None
    if guild is None:
        return {"status": "failed", "error": "guild_not_found", "guild_id": str(guild_id), "changed": [], "skipped": [], "failed": []}

    validation = rt.validate_channel_builder_items(items)
    preflight = preflight_channel_builder_plan(guild, items)
    if validation or not bool(preflight.get("ok")):
        return {"status": "failed", "error": "preflight_failed", "validation_errors": validation, "preflight": preflight, "changed": [], "skipped": [], "failed": []}

    changed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    reason = f"Dank Shield Channel Builder actor={actor_id or 'dashboard'} mode={mode}"
    retry_key = f"channel-builder:{gid}"

    for item in list(items or []):
        action = str(item.get("action") or "skip")
        final_name = str(item.get("final_name") or "").strip()[:100]
        kind = str(item.get("type") or "text")
        try:
            if action in {"skip", "keep"}:
                skipped.append({"id": item.get("id"), "action": action, "name": final_name})
                continue
            if action == "rename":
                channel = _find_channel(guild, item)
                if channel is None:
                    failed.append({"id": item.get("id"), "action": action, "error": "channel_not_found"})
                    continue
                if dry_run:
                    changed.append({"id": item.get("id"), "action": action, "from": _channel_name(channel), "to": final_name, "dry_run": True})
                    continue
                await with_retry(lambda: channel.edit(name=final_name, reason=reason), attempts=3, concurrency_key=retry_key)
                changed.append({"id": item.get("id"), "action": action, "channel_id": str(getattr(channel, "id", "")), "to": final_name})
                await asyncio.sleep(0.25)
                continue
            if action == "create":
                if not final_name:
                    failed.append({"id": item.get("id"), "action": action, "error": "final_name_required"})
                    continue
                parent = _find_category(guild, str(item.get("category") or ""))
                if dry_run:
                    changed.append({"id": item.get("id"), "action": action, "type": kind, "name": final_name, "category": _channel_name(parent), "dry_run": True})
                    continue

                async def create_one() -> Any:
                    if kind == "category":
                        return await guild.create_category(final_name, reason=reason)
                    if kind == "voice":
                        return await guild.create_voice_channel(final_name, category=parent, reason=reason)
                    if kind == "forum" and hasattr(guild, "create_forum"):
                        return await guild.create_forum(final_name, category=parent, reason=reason)
                    return await guild.create_text_channel(final_name, category=parent, reason=reason)

                created = await with_retry(create_one, attempts=3, concurrency_key=retry_key)
                changed.append({"id": item.get("id"), "action": action, "type": kind, "channel_id": str(getattr(created, "id", "")), "name": final_name})
                await asyncio.sleep(0.25)
                continue
            failed.append({"id": item.get("id"), "action": action, "error": "unsupported_action"})
        except Exception as exc:
            failed.append({"id": item.get("id"), "action": action, "error": type(exc).__name__, "detail": str(exc)[:160]})

    status = "failed" if failed and not changed else ("partial" if failed else "succeeded")
    return {
        "status": status,
        "attempted": len(items),
        "succeeded": len(changed),
        "skipped_count": len(skipped),
        "failed_count": len(failed),
        "changed": changed,
        "skipped": skipped,
        "failed": failed,
        "dry_run": bool(dry_run),
        "preflight": preflight,
    }


__all__ = ["execute_channel_builder_plan", "list_channels_payload", "preflight_channel_builder_plan"]
