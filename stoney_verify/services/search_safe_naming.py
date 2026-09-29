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
import weakref
from typing import Any

import discord

from stoney_verify.services import naming_identity
from stoney_verify.services import naming_mutation_locks
from stoney_verify.services import role_mutation_authority
from stoney_verify.operation_queue import with_retry
from stoney_verify.share_router_resources import is_share_router_design_resource

DEFAULT_REPAIR_BATCH_SIZE = 25
MAX_REPAIR_BATCH_SIZE = 25

_RESOURCE_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


async def _safe_name_edit(
    resource: Any,
    *,
    name: str,
    reason: str,
    guild_id: int,
    kind: str,
) -> Any:
    """Reserve process-wide REST headroom and retry one idempotent name PATCH."""

    from stoney_verify.startup_guards.discord_api_safety import (
        reserve_bulk_discord_rest_requests,
    )

    await reserve_bulk_discord_rest_requests(
        1,
        label=f"search_safe:{kind}:guild={int(guild_id)}",
    )

    async def _edit() -> Any:
        return await resource.edit(name=name, reason=reason)

    return await with_retry(
        _edit,
        attempts=3,
        base_delay=0.75,
        max_delay=8.0,
        concurrency_key=f"search-safe-name:{int(guild_id)}:{kind}",
    )


def _resource_lock(kind: str, guild_id: int, resource_id: int) -> asyncio.Lock:
    key = f"{kind}:{int(guild_id)}:{int(resource_id)}"
    lock = _RESOURCE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _RESOURCE_LOCKS[key] = lock
    return lock


def _role_blocker(role: Any, *, actor: Any = None) -> str:
    guild = getattr(role, "guild", None)
    if guild is None:
        return "Role is not attached to a guild."

    # Human-reviewed repairs must obey the same live actor + bot hierarchy
    # boundary as the canonical /role editor. Automatic policy enforcement has
    # no initiating human actor, so it retains the bot-policy checks below.
    if actor is not None:
        blockers = role_mutation_authority.role_mutation_blockers(guild, actor, role)
        return " ".join(str(item) for item in blockers if str(item).strip())

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


def _target_row(kind: str, resource: Any, *, actor: Any = None) -> dict[str, Any] | None:
    if kind == "channel" and is_share_router_design_resource(resource):
        return None
    before = str(getattr(resource, "name", "") or "").strip()
    after = naming_identity.search_safe_display_name(before).strip()
    if not before or not after or before == after:
        return None

    blocker = _role_blocker(resource, actor=actor) if kind == "role" else _channel_blocker(resource)
    return {
        "kind": kind,
        "id": _safe_int(getattr(resource, "id", 0), 0),
        "before": before,
        "after": after,
        "editable": not bool(blocker),
        "blocker": blocker,
    }


def scan_search_safe_targets(
    guild: discord.Guild,
    *,
    actor: Any = None,
) -> list[dict[str, Any]]:
    """Return styled role/channel names, optionally scoped to a live human actor."""
    rows: list[dict[str, Any]] = []

    for role in list(getattr(guild, "roles", []) or []):
        try:
            if bool(role.is_default()):
                continue
        except Exception:
            pass
        row = _target_row("role", role, actor=actor)
        if row is not None:
            rows.append(row)

    for channel in list(getattr(guild, "channels", []) or []):
        if isinstance(channel, discord.CategoryChannel):
            continue
        row = _target_row("channel", channel, actor=actor)
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


def normalize_design_plan_items(
    items: list[dict[str, Any]],
    policy: dict[str, Any],
) -> list[dict[str, Any]]:
    """Apply Search-Safe policy to preview rows before transactional Apply.

    Doing this at plan time keeps preview, snapshot, Apply and Undo aligned.
    Categories and protected/failed rows are intentionally untouched.
    """
    if policy.get("mode") != naming_identity.NAMING_MODE_SEARCH_SAFE:
        return [dict(item) for item in items]
    if not bool(policy.get("channels", True)):
        return [dict(item) for item in items]

    out: list[dict[str, Any]] = []
    warning = "Search-Safe Naming normalized styled letter glyphs; decoration was preserved."
    for raw in items:
        item = dict(raw)
        kind = str(item.get("kind") or "").strip().lower()
        status = str(item.get("status") or "").strip().lower()
        if kind == "category" or status in {"protected", "failed"}:
            out.append(item)
            continue

        before = str(item.get("before") or "")
        after = str(item.get("after") or "")
        safe_after = naming_identity.search_safe_display_name(after).strip()
        if safe_after and safe_after != after:
            item["after"] = safe_after
            if safe_after == before:
                item["status"] = "unchanged"
            elif status in {"", "unchanged", "changed"}:
                item["status"] = "changed"
            warnings = [
                str(value)
                for value in list(item.get("warnings") or [])
                if str(value).strip()
            ]
            if warning not in warnings:
                warnings.append(warning)
            item["warnings"] = warnings
            item["search_safe_naming"] = True
        out.append(item)
    return out


def policy_fingerprint(policy: dict[str, Any] | Any) -> dict[str, Any]:
    raw = dict(policy) if isinstance(policy, dict) else {}
    return {
        "mode": str(raw.get("mode") or naming_identity.NAMING_MODE_PRESERVE),
        "roles": bool(raw.get("roles", True)),
        "channels": bool(raw.get("channels", True)),
        "categories": bool(raw.get("categories", False)),
    }


def policy_matches_snapshot(snapshot: Any, current: Any) -> bool:
    if not isinstance(snapshot, dict):
        return False
    return policy_fingerprint(snapshot) == policy_fingerprint(current)


def policy_adjusted_name_for_policy(
    policy: dict[str, Any] | Any,
    *,
    kind: str,
    name: Any,
) -> str:
    """Return the live name permitted by one already-loaded naming policy."""

    raw = str(name or "").strip()
    if not raw:
        return raw

    clean_kind = str(kind or "").strip().lower()
    if clean_kind in {"text", "voice", "stage", "forum", "media", "channel"}:
        clean_kind = "channel"

    current = policy_fingerprint(policy)
    if current["mode"] != naming_identity.NAMING_MODE_SEARCH_SAFE:
        return raw
    if clean_kind == "role" and not current["roles"]:
        return raw
    if clean_kind == "channel" and not current["channels"]:
        return raw
    if clean_kind == "category" and not current["categories"]:
        return raw
    if clean_kind not in {"role", "channel", "category"}:
        return raw
    return naming_identity.search_safe_display_name(raw).strip() or raw


async def policy_adjusted_name(
    guild_id: Any,
    *,
    kind: str,
    name: Any,
) -> str:
    """Return the final live name an enabled guild policy permits."""

    policy = await naming_identity.get_naming_policy(guild_id, refresh=True)
    return policy_adjusted_name_for_policy(policy, kind=kind, name=name)


async def normalize_design_plan_for_guild(
    guild_id: Any,
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Bind a design preview to the naming policy used to render its final names."""

    policy = await naming_identity.get_naming_policy(guild_id, refresh=True)
    fingerprint = policy_fingerprint(policy)
    return normalize_design_plan_items(items, fingerprint), fingerprint


def normalize_undo_snapshot_items(
    items: list[dict[str, Any]] | list[Any],
    policy: dict[str, Any] | Any,
) -> list[dict[str, Any]]:
    """Make Undo restore targets obey the current Search-Safe authority."""

    out: list[dict[str, Any]] = []
    for raw in list(items or []):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        kind = str(item.get("kind") or "channel").strip().lower()
        old_name = str(item.get("old_name") or item.get("before") or "").strip()
        adjusted = policy_adjusted_name_for_policy(policy, kind=kind, name=old_name)
        if adjusted and adjusted != old_name:
            item["search_safe_original_old_name"] = old_name
            item["old_name"] = adjusted
            item["search_safe_undo_adjusted"] = True
        out.append(item)
    return out


def reviewed_search_safe_batch(
    rows: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    *,
    limit: int = DEFAULT_REPAIR_BATCH_SIZE,
) -> list[dict[str, Any]]:
    """Freeze the exact editable rows an admin reviewed for one bounded batch."""

    batch_limit = max(
        1,
        min(_safe_int(limit, DEFAULT_REPAIR_BATCH_SIZE), MAX_REPAIR_BATCH_SIZE),
    )
    selected: list[dict[str, Any]] = []
    for raw in list(rows or []):
        if not isinstance(raw, dict) or not bool(raw.get("editable")):
            continue
        kind = str(raw.get("kind") or "").strip().lower()
        rid = _safe_int(raw.get("id"), 0)
        before = str(raw.get("before") or "").strip()
        after = str(raw.get("after") or "").strip()
        if kind not in {"role", "channel"} or rid <= 0 or not before or not after:
            continue
        selected.append(
            {
                **dict(raw),
                "kind": kind,
                "id": rid,
                "before": before,
                "after": after,
                "editable": True,
            }
        )
        if len(selected) >= batch_limit:
            break
    return selected


def search_safe_summary(guild: discord.Guild, *, actor: Any = None) -> dict[str, int]:
    rows = scan_search_safe_targets(guild, actor=actor)
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

    if _role_blocker(role):
        return False
    policy = await naming_identity.get_naming_policy(gid, refresh=True)
    if policy.get("mode") != naming_identity.NAMING_MODE_SEARCH_SAFE or not bool(policy.get("roles", True)):
        return False

    guild_lock = naming_mutation_locks.guild_naming_lock(gid)
    resource_lock = _resource_lock("role", gid, rid)
    try:
        async with guild_lock:
            async with resource_lock:
                fresh = guild.get_role(rid) if guild is not None else None
                if not isinstance(fresh, discord.Role):
                    return False
                current = str(fresh.name or "").strip()
                desired = naming_identity.search_safe_display_name(current).strip()
                if not desired or desired == current or _role_blocker(fresh):
                    return False
                await _safe_name_edit(
                    fresh,
                    name=desired[:100],
                    reason="Dank Shield Search-Safe Naming policy",
                    guild_id=gid,
                    kind="role",
                )
                return True
    except (discord.Forbidden, discord.HTTPException):
        return False


async def enforce_channel_name(channel: discord.abc.GuildChannel) -> bool:
    """Enforce an enabled guild policy after a channel create/rename event."""
    if isinstance(channel, discord.CategoryChannel) or is_share_router_design_resource(channel):
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

    if _channel_blocker(channel):
        return False
    policy = await naming_identity.get_naming_policy(gid, refresh=True)
    if policy.get("mode") != naming_identity.NAMING_MODE_SEARCH_SAFE or not bool(policy.get("channels", True)):
        return False

    guild_lock = naming_mutation_locks.guild_naming_lock(gid)
    resource_lock = _resource_lock("channel", gid, cid)
    try:
        async with guild_lock:
            async with resource_lock:
                fresh = guild.get_channel(cid) if guild is not None else None
                if fresh is None or isinstance(fresh, discord.CategoryChannel):
                    return False
                current = str(getattr(fresh, "name", "") or "").strip()
                desired = naming_identity.search_safe_display_name(current).strip()
                if not desired or desired == current or _channel_blocker(fresh):
                    return False
                await _safe_name_edit(
                    fresh,
                    name=desired[:100],
                    reason="Dank Shield Search-Safe Naming policy",
                    guild_id=gid,
                    kind="channel",
                )
                return True
    except (discord.Forbidden, discord.HTTPException):
        return False


async def _apply_search_safe_batch_locked(
    guild: discord.Guild,
    *,
    actor: Any,
    reviewed_rows: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    limit: int = DEFAULT_REPAIR_BATCH_SIZE,
) -> dict[str, Any]:
    """Apply only the exact bounded rows captured by the reviewed preview."""

    batch_limit = max(
        1,
        min(_safe_int(limit, DEFAULT_REPAIR_BATCH_SIZE), MAX_REPAIR_BATCH_SIZE),
    )
    selected = reviewed_search_safe_batch(list(reviewed_rows or []), limit=batch_limit)

    changed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for row in selected:
        kind = str(row.get("kind") or "")
        rid = _safe_int(row.get("id"), 0)
        reviewed_before = str(row.get("before") or "").strip()
        reviewed_after = str(row.get("after") or "").strip()

        if kind == "role":
            resource = guild.get_role(rid)
            if not isinstance(resource, discord.Role):
                failed.append({**row, "error": "Role disappeared after preview."})
                continue
            current = str(resource.name or "").strip()
            blocker = _role_blocker(resource, actor=actor)
        elif kind == "channel":
            resource = guild.get_channel(rid)
            if resource is None or isinstance(resource, discord.CategoryChannel):
                failed.append({**row, "error": "Channel disappeared after preview."})
                continue
            current = str(getattr(resource, "name", "") or "").strip()
            blocker = _channel_blocker(resource)
        else:
            failed.append({**row, "error": "Reviewed resource type is no longer valid."})
            continue

        if current != reviewed_before:
            failed.append(
                {
                    **row,
                    "before": current,
                    "after": reviewed_after,
                    "error": (
                        f"Name changed after preview from {reviewed_before!r} "
                        f"to {current or 'blank'!r}. Preview again."
                    ),
                }
            )
            continue
        if blocker:
            failed.append(
                {
                    **row,
                    "before": current,
                    "after": reviewed_after,
                    "error": blocker,
                }
            )
            continue

        derived_after = naming_identity.search_safe_display_name(current).strip()
        if not derived_after or derived_after != reviewed_after:
            failed.append(
                {
                    **row,
                    "before": current,
                    "after": derived_after,
                    "error": "Search-Safe output changed after preview. Preview again.",
                }
            )
            continue

        lock = _resource_lock(kind, int(guild.id), rid)
        try:
            async with lock:
                if kind == "role":
                    fresh = guild.get_role(rid)
                    if not isinstance(fresh, discord.Role):
                        failed.append({**row, "error": "Role disappeared after preview."})
                        continue
                    current = str(fresh.name or "").strip()
                    blocker = _role_blocker(fresh, actor=actor)
                else:
                    fresh = guild.get_channel(rid)
                    if fresh is None or isinstance(fresh, discord.CategoryChannel):
                        failed.append({**row, "error": "Channel disappeared after preview."})
                        continue
                    current = str(getattr(fresh, "name", "") or "").strip()
                    blocker = _channel_blocker(fresh)

                if current != reviewed_before:
                    failed.append(
                        {
                            **row,
                            "before": current,
                            "after": reviewed_after,
                            "error": (
                                f"Name changed after preview from {reviewed_before!r} "
                                f"to {current or 'blank'!r}. Preview again."
                            ),
                        }
                    )
                    continue
                if blocker:
                    failed.append(
                        {
                            **row,
                            "before": current,
                            "after": reviewed_after,
                            "error": blocker,
                        }
                    )
                    continue

                derived_after = naming_identity.search_safe_display_name(current).strip()
                if not derived_after or derived_after != reviewed_after:
                    failed.append(
                        {
                            **row,
                            "before": current,
                            "after": derived_after,
                            "error": "Search-Safe output changed after preview. Preview again.",
                        }
                    )
                    continue

                await _safe_name_edit(
                    fresh,
                    name=reviewed_after[:100],
                    reason="Dank Shield Search-Safe Naming reviewed repair",
                    guild_id=int(guild.id),
                    kind=kind,
                )
                changed.append(
                    {
                        **row,
                        "before": reviewed_before,
                        "after": reviewed_after,
                    }
                )
        except (discord.Forbidden, discord.HTTPException) as exc:
            failed.append(
                {
                    **row,
                    "before": current,
                    "after": reviewed_after,
                    "error": type(exc).__name__,
                }
            )

    remaining_rows = scan_search_safe_targets(guild, actor=actor)
    return {
        "reviewed": len(selected),
        "attempted": len(selected),
        "changed": changed,
        "failed": failed,
        "remaining": len(remaining_rows),
        "remaining_editable": sum(1 for row in remaining_rows if row.get("editable")),
        "remaining_blocked": sum(1 for row in remaining_rows if not row.get("editable")),
        "batch_limit": batch_limit,
    }


async def apply_search_safe_batch(
    guild: discord.Guild,
    *,
    actor: Any,
    reviewed_rows: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    limit: int = DEFAULT_REPAIR_BATCH_SIZE,
) -> dict[str, Any]:
    """Serialize reviewed Search-Safe repair with Dank Design for this guild."""

    guild_lock = naming_mutation_locks.guild_naming_lock(int(guild.id))
    async with guild_lock:
        return await _apply_search_safe_batch_locked(
            guild,
            actor=actor,
            reviewed_rows=reviewed_rows,
            limit=limit,
        )


__all__ = [
    "DEFAULT_REPAIR_BATCH_SIZE",
    "MAX_REPAIR_BATCH_SIZE",
    "apply_search_safe_batch",
    "enforce_channel_name",
    "enforce_role_name",
    "normalize_design_plan_for_guild",
    "normalize_design_plan_items",
    "normalize_undo_snapshot_items",
    "policy_adjusted_name",
    "policy_adjusted_name_for_policy",
    "policy_fingerprint",
    "policy_matches_snapshot",
    "reviewed_search_safe_batch",
    "scan_search_safe_targets",
    "search_safe_summary",
]
