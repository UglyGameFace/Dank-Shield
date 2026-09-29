from __future__ import annotations

"""Native-search-safe naming policy and bounded repair helpers.

This module changes only the letter glyphs that make Discord's own search miss a
resource. Emojis, separators, brackets, and other surrounding decoration remain
in the live display name. Categories stay fully styled by default.

The semantic alias/history layer lives in naming_identity.py. Keeping mutation
logic here prevents autocomplete/history persistence from becoming a second
server-design engine.
"""

import asyncio
from typing import Any

import discord

from stoney_verify.services import naming_identity

DEFAULT_REPAIR_BATCH_SIZE = 25
MAX_REPAIR_BATCH_SIZE = 25

_RESOURCE_LOCKS: dict[str, asyncio.Lock] = {}


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _resource_lock(kind: str, guild_id: int, resource_id: int) -> asyncio.Lock:
    key = f"{kind}:{int(guild_id)}:{int(resource_id)}"
    lock = _RESOURCE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _RESOURCE_LOCKS[key] = lock
    return lock


def _release_resource_lock(kind: str, guild_id: int, resource_id: int, lock: asyncio.Lock) -> None:
    key = f"{kind}:{int(guild_id)}:{int(resource_id)}"
    if _RESOURCE_LOCKS.get(key) is lock and not lock.locked():
        _RESOURCE_LOCKS.pop(key, None)


def _role_blocker(role: Any) -> str:
    guild = getattr(role, "guild", None)
    if guild is None:
        return "Role is not attached to a guild."
    try:
        if bool(role.is_default()):
            return "@everyone is never renamed."
    except Exception:
        pass
    if bool(getattr(role, "managed", False)):
        return "Discord/integration-managed roles cannot be renamed by Dank Shield."

    me = getattr(guild, "me", None)
    if not isinstance(me, discord.Member):
        return "Dank Shield's guild member is unavailable."
    perms = getattr(me, "guild_permissions", None)
    if not bool(getattr(perms, "administrator", False) or getattr(perms, "manage_roles", False)):
        return "Dank Shield is missing Manage Roles."

    try:
        if getattr(role, "position", 0) >= getattr(me.top_role, "position", -1):
            return "Role is at or above Dank Shield's highest role."
    except Exception:
        return "Dank Shield could not verify the role hierarchy."
    return ""


def _channel_blocker(channel: Any) -> str:
    guild = getattr(channel, "guild", None)
    if guild is None:
        return "Channel is not attached to a guild."
    if isinstance(channel, discord.CategoryChannel):
        return "Categories intentionally keep their full visual styling."

    me = getattr(guild, "me", None)
    if not isinstance(me, discord.Member):
        return "Dank Shield's guild member is unavailable."
    try:
        perms = channel.permissions_for(me)
    except Exception:
        perms = getattr(me, "guild_permissions", None)
    if not bool(getattr(perms, "administrator", False) or getattr(perms, "manage_channels", False)):
        return "Dank Shield is missing Manage Channels for this channel."
    return ""


def _target_row(kind: str, resource: Any) -> dict[str, Any] | None:
    before = str(getattr(resource, "name", "") or "").strip()
    after = naming_identity.search_safe_display_name(before).strip()
    if not before or not after or before == after:
        return None

    blocker = _role_blocker(resource) if kind == "role" else _channel_blocker(resource)
    return {
        "kind": kind,
        "id": _safe_int(getattr(resource, "id", 0), 0),
        "before": before,
        "after": after,
        "editable": not bool(blocker),
        "blocker": blocker,
    }


def scan_search_safe_targets(guild: discord.Guild) -> list[dict[str, Any]]:
    """Return current styled role/channel names whose letters block native search."""
    rows: list[dict[str, Any]] = []

    for role in list(getattr(guild, "roles", []) or []):
        try:
            if bool(role.is_default()):
                continue
        except Exception:
            pass
        row = _target_row("role", role)
        if row is not None:
            rows.append(row)

    for channel in list(getattr(guild, "channels", []) or []):
        if isinstance(channel, discord.CategoryChannel):
            continue
        row = _target_row("channel", channel)
        if row is not None:
            rows.append(row)

    rows.sort(
        key=lambda row: (
            0 if bool(row.get("editable")) else 1,
            str(row.get("kind") or ""),
            str(row.get("before") or "").casefold(),
            int(row.get("id") or 0),
        )
    )
    return rows


def search_safe_summary(guild: discord.Guild) -> dict[str, int]:
    rows = scan_search_safe_targets(guild)
    return {
        "total": len(rows),
        "editable": sum(1 for row in rows if row.get("editable")),
        "blocked": sum(1 for row in rows if not row.get("editable")),
        "roles": sum(1 for row in rows if row.get("kind") == "role"),
        "channels": sum(1 for row in rows if row.get("kind") == "channel"),
    }


async def enforce_role_name(role: discord.Role) -> bool:
    """Enforce an enabled guild policy after a role create/rename event."""
    before = str(getattr(role, "name", "") or "").strip()
    after = naming_identity.search_safe_display_name(before).strip()
    if not before or before == after:
        return False

    guild = getattr(role, "guild", None)
    gid = _safe_int(getattr(guild, "id", 0), 0)
    rid = _safe_int(getattr(role, "id", 0), 0)
    if gid <= 0 or rid <= 0:
        return False

    policy = await naming_identity.get_naming_policy(gid)
    if policy.get("mode") != naming_identity.NAMING_MODE_SEARCH_SAFE or not bool(policy.get("roles", True)):
        return False
    if _role_blocker(role):
        return False

    lock = _resource_lock("role", gid, rid)
    try:
        async with lock:
            fresh = guild.get_role(rid) if guild is not None else None
            if not isinstance(fresh, discord.Role):
                return False
            current = str(fresh.name or "").strip()
            desired = naming_identity.search_safe_display_name(current).strip()
            if not desired or desired == current or _role_blocker(fresh):
                return False
            await fresh.edit(
                name=desired[:100],
                reason="Dank Shield Search-Safe Naming policy",
            )
            return True
    except (discord.Forbidden, discord.HTTPException):
        return False
    finally:
        _release_resource_lock("role", gid, rid, lock)


async def enforce_channel_name(channel: discord.abc.GuildChannel) -> bool:
    """Enforce an enabled guild policy after a channel create/rename event."""
    if isinstance(channel, discord.CategoryChannel):
        return False

    before = str(getattr(channel, "name", "") or "").strip()
    after = naming_identity.search_safe_display_name(before).strip()
    if not before or before == after:
        return False

    guild = getattr(channel, "guild", None)
    gid = _safe_int(getattr(guild, "id", 0), 0)
    cid = _safe_int(getattr(channel, "id", 0), 0)
    if gid <= 0 or cid <= 0:
        return False

    policy = await naming_identity.get_naming_policy(gid)
    if policy.get("mode") != naming_identity.NAMING_MODE_SEARCH_SAFE or not bool(policy.get("channels", True)):
        return False
    if _channel_blocker(channel):
        return False

    lock = _resource_lock("channel", gid, cid)
    try:
        async with lock:
            fresh = guild.get_channel(cid) if guild is not None else None
            if fresh is None or isinstance(fresh, discord.CategoryChannel):
                return False
            current = str(getattr(fresh, "name", "") or "").strip()
            desired = naming_identity.search_safe_display_name(current).strip()
            if not desired or desired == current or _channel_blocker(fresh):
                return False
            await fresh.edit(
                name=desired[:100],
                reason="Dank Shield Search-Safe Naming policy",
            )
            return True
    except (discord.Forbidden, discord.HTTPException):
        return False
    finally:
        _release_resource_lock("channel", gid, cid, lock)


async def apply_search_safe_batch(
    guild: discord.Guild,
    *,
    limit: int = DEFAULT_REPAIR_BATCH_SIZE,
) -> dict[str, Any]:
    """Repair a bounded batch of existing names after an explicit admin preview."""
    batch_limit = max(1, min(_safe_int(limit, DEFAULT_REPAIR_BATCH_SIZE), MAX_REPAIR_BATCH_SIZE))
    initial = scan_search_safe_targets(guild)
    editable = [row for row in initial if row.get("editable")]
    selected = editable[:batch_limit]

    changed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for row in selected:
        kind = str(row.get("kind") or "")
        rid = _safe_int(row.get("id"), 0)
        if rid <= 0:
            continue

        if kind == "role":
            resource = guild.get_role(rid)
            if not isinstance(resource, discord.Role):
                failed.append({**row, "error": "Role disappeared before repair."})
                continue
            before = str(resource.name or "").strip()
            after = naming_identity.search_safe_display_name(before).strip()
            blocker = _role_blocker(resource)
        else:
            resource = guild.get_channel(rid)
            if resource is None or isinstance(resource, discord.CategoryChannel):
                failed.append({**row, "error": "Channel disappeared before repair."})
                continue
            before = str(getattr(resource, "name", "") or "").strip()
            after = naming_identity.search_safe_display_name(before).strip()
            blocker = _channel_blocker(resource)

        if blocker:
            failed.append({**row, "before": before, "after": after, "error": blocker})
            continue
        if not after or after == before:
            continue

        lock = _resource_lock(kind, int(guild.id), rid)
        try:
            async with lock:
                if kind == "role":
                    await resource.edit(
                        name=after[:100],
                        reason="Dank Shield Search-Safe Naming reviewed repair",
                    )
                else:
                    await resource.edit(
                        name=after[:100],
                        reason="Dank Shield Search-Safe Naming reviewed repair",
                    )
            changed.append({**row, "before": before, "after": after})
        except (discord.Forbidden, discord.HTTPException) as exc:
            failed.append({**row, "before": before, "after": after, "error": type(exc).__name__})
        finally:
            _release_resource_lock(kind, int(guild.id), rid, lock)

    remaining_rows = scan_search_safe_targets(guild)
    return {
        "initial_total": len(initial),
        "initial_editable": len(editable),
        "initial_blocked": len(initial) - len(editable),
        "attempted": len(selected),
        "changed": changed,
        "failed": failed,
        "remaining": len(remaining_rows),
        "remaining_editable": sum(1 for row in remaining_rows if row.get("editable")),
        "remaining_blocked": sum(1 for row in remaining_rows if not row.get("editable")),
        "batch_limit": batch_limit,
    }


__all__ = [
    "DEFAULT_REPAIR_BATCH_SIZE",
    "MAX_REPAIR_BATCH_SIZE",
    "apply_search_safe_batch",
    "enforce_channel_name",
    "enforce_role_name",
    "scan_search_safe_targets",
    "search_safe_summary",
]
