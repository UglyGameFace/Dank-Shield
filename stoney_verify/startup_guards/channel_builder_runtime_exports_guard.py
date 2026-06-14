from __future__ import annotations

"""Restore first-class Channel Builder runtime exports used by the structured API.

The API routes import these names from services.channel_builder_runtime. Keep the
functions attached there so route startup does not fail, while all dashboard
mutations still run through operation_queue.submit_operation from the route.
"""

import asyncio
from typing import Any, Optional

import discord

_DONE = False


def _channel_name(value: Any) -> str:
    try:
        return str(getattr(value, "name", "") or "").strip()
    except Exception:
        return ""


def _find_channel(guild: discord.Guild, item: dict[str, Any]) -> Optional[discord.abc.GuildChannel]:
    try:
        current_id = int(item.get("current_id") or 0)
    except Exception:
        current_id = 0
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


def _list_channels_payload_factory(rt: Any):
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
    return list_channels_payload


def _preflight_factory(rt: Any):
    def preflight_channel_builder_plan(guild: discord.Guild, items: list[dict[str, Any]]) -> dict[str, Any]:
        errors = list(rt.validate_channel_builder_items(items) or [])
        create_items = [item for item in items if item.get("action") == "create"]
        rename_items = [item for item in items if item.get("action") == "rename"]
        current_count = len(list(getattr(guild, "channels", []) or []))
        if current_count + len(create_items) > int(getattr(rt, "DISCORD_CHANNEL_LIMIT", 500)):
            errors.append(
                f"server channel limit would be exceeded: {current_count}+{len(create_items)}>{int(getattr(rt, 'DISCORD_CHANNEL_LIMIT', 500))}"
            )

        category_child_counts: dict[str, int] = {}
        for category in list(getattr(guild, "categories", []) or []):
            category_child_counts[_channel_name(category).lower()] = len(list(getattr(category, "channels", []) or []))
        planned_category_adds: dict[str, int] = {}
        for item in create_items:
            category_name = str(item.get("category") or "").strip().lower()
            if not category_name:
                continue
            planned_category_adds[category_name] = planned_category_adds.get(category_name, 0) + 1
        for category_name, add_count in planned_category_adds.items():
            existing = category_child_counts.get(category_name, 0)
            if existing + add_count > int(getattr(rt, "CATEGORY_CHILD_LIMIT", 50)):
                errors.append(f"category #{category_name} would exceed Discord child limit: {existing}+{add_count}>50")

        return {
            "ok": not errors,
            "errors": errors[:25],
            "creates": len(create_items),
            "renames": len(rename_items),
            "skips": len([item for item in items if item.get("action") in {"skip", "keep"}]),
            "current_channel_count": current_count,
            "planned_channel_count": current_count + len(create_items),
        }
    return preflight_channel_builder_plan


def _execute_factory(rt: Any):
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
            return {"status": "failed", "error": "guild_not_found", "guild_id": str(guild_id), "changed": [], "skipped": []}

        validation = rt.validate_channel_builder_items(items)
        preflight = rt.preflight_channel_builder_plan(guild, items)
        if validation or not bool(preflight.get("ok")):
            return {"status": "failed", "error": "preflight_failed", "validation_errors": validation, "preflight": preflight, "changed": [], "skipped": []}

        changed: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        reason = f"Dank Shield Channel Builder actor={actor_id or 'dashboard'} mode={mode}"

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
                    await channel.edit(name=final_name, reason=reason)
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
                    created: Any
                    if kind == "category":
                        created = await guild.create_category(final_name, reason=reason)
                    elif kind == "voice":
                        created = await guild.create_voice_channel(final_name, category=parent, reason=reason)
                    elif kind == "forum" and hasattr(guild, "create_forum"):
                        created = await guild.create_forum(final_name, category=parent, reason=reason)
                    else:
                        created = await guild.create_text_channel(final_name, category=parent, reason=reason)
                    changed.append({"id": item.get("id"), "action": action, "type": kind, "channel_id": str(getattr(created, "id", "")), "name": final_name})
                    await asyncio.sleep(0.25)
                    continue
                failed.append({"id": item.get("id"), "action": action, "error": "unsupported_action"})
            except Exception as exc:
                failed.append({"id": item.get("id"), "action": action, "error": type(exc).__name__, "detail": str(exc)[:160]})

        status = "failed" if failed and not changed else ("partial" if failed else "succeeded")
        return {"status": status, "changed": changed, "skipped": skipped, "failed": failed, "dry_run": bool(dry_run), "preflight": preflight}
    return execute_channel_builder_plan


def apply() -> bool:
    global _DONE
    if _DONE:
        return True
    try:
        from stoney_verify.services import channel_builder_runtime as rt
        rt.list_channels_payload = _list_channels_payload_factory(rt)
        rt.preflight_channel_builder_plan = _preflight_factory(rt)
        rt.execute_channel_builder_plan = _execute_factory(rt)
        _DONE = True
        print("🧩 channel_builder_runtime_exports_guard active; structured Channel Builder runtime exports restored")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ channel_builder_runtime_exports_guard failed: {exc!r}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
