from __future__ import annotations

"""Native per-guild AntiNuke protection for Dank Shield.

The engine handles high-confidence destructive actions attributed through Discord's
audit log. It does not guess an attacker when Discord cannot prove who performed an
action, but it keeps attribution and containment on a short, cache-backed hot path so
bursts do not turn the protection engine itself into a bottleneck.

Runtime ownership:
- settings live in the existing per-guild ``guild_configs.settings`` JSON bucket;
- Discord audit logs provide actor attribution;
- the existing modlog receives incidents;
- listeners are registered on the shared bot when this native module is imported
  by the live event path.

No startup guard, monkey patch, or parallel runtime tree is used.
"""

import asyncio
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Any, Deque, Mapping, Optional

import discord

from .globals import bot
from .guild_config import get_guild_config, upsert_guild_config
from .modlog import _post_modlog


ANTINUKE_DEFAULTS: dict[str, Any] = {
    "antinuke_enabled": False,
    "antinuke_mode": "contain",
    "antinuke_window_seconds": 15,
    "antinuke_channel_delete_threshold": 2,
    "antinuke_role_delete_threshold": 2,
    "antinuke_ban_threshold": 3,
    "antinuke_kick_threshold": 3,
    "antinuke_webhook_create_threshold": 2,
    "antinuke_protect_role_escalation": True,
    "antinuke_trusted_user_ids": [],
    "antinuke_trusted_role_ids": [],
}

DANGEROUS_PERMISSION_NAMES: tuple[str, ...] = (
    "administrator",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "ban_members",
    "kick_members",
    "manage_webhooks",
)

_DESTRUCTIVE_THRESHOLD_KEYS: tuple[str, ...] = (
    "antinuke_channel_delete_threshold",
    "antinuke_role_delete_threshold",
    "antinuke_ban_threshold",
    "antinuke_kick_threshold",
    "antinuke_webhook_create_threshold",
)

_ACTION_WINDOWS: dict[tuple[int, int, str], Deque[float]] = defaultdict(deque)
_TRIGGER_COOLDOWNS: dict[tuple[int, int, str], float] = {}
_SEEN_AUDIT_ENTRY_IDS: dict[int, float] = {}
_TRIGGER_COOLDOWN_SECONDS = 30.0
_AUDIT_ENTRY_MAX_AGE_SECONDS = 30.0
_AUDIT_DEDUPE_TTL_SECONDS = 90.0
_AUDIT_SEARCH_LIMIT = 50
_AGGREGATE_ACTION_KEY = "__destructive__"
_CONTAINMENT_COOLDOWN_KEY = "__containment__"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled"}:
        return False
    return bool(default)


def _safe_id_list(value: Any, *, limit: int = 100) -> list[int]:
    if isinstance(value, (list, tuple, set)):
        source = list(value)
    elif value is None:
        source = []
    else:
        source = [value]

    out: list[int] = []
    seen: set[int] = set()
    for raw in source:
        candidate = _safe_int(raw, 0)
        if candidate <= 0 or candidate in seen:
            continue
        seen.add(candidate)
        out.append(candidate)
        if len(out) >= limit:
            break
    return out


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    return default


def normalize_antinuke_settings(cfg: Any) -> dict[str, Any]:
    defaults = dict(ANTINUKE_DEFAULTS)
    mode = str(_cfg_value(cfg, "antinuke_mode", defaults["antinuke_mode"]) or "contain").strip().lower()
    if mode not in {"alert", "contain"}:
        mode = "contain"

    return {
        "antinuke_enabled": _safe_bool(
            _cfg_value(cfg, "antinuke_enabled", defaults["antinuke_enabled"]),
            bool(defaults["antinuke_enabled"]),
        ),
        "antinuke_mode": mode,
        "antinuke_window_seconds": max(
            5,
            min(
                120,
                _safe_int(
                    _cfg_value(cfg, "antinuke_window_seconds", defaults["antinuke_window_seconds"]),
                    defaults["antinuke_window_seconds"],
                ),
            ),
        ),
        "antinuke_channel_delete_threshold": max(
            2,
            min(
                25,
                _safe_int(
                    _cfg_value(
                        cfg,
                        "antinuke_channel_delete_threshold",
                        defaults["antinuke_channel_delete_threshold"],
                    ),
                    defaults["antinuke_channel_delete_threshold"],
                ),
            ),
        ),
        "antinuke_role_delete_threshold": max(
            2,
            min(
                25,
                _safe_int(
                    _cfg_value(
                        cfg,
                        "antinuke_role_delete_threshold",
                        defaults["antinuke_role_delete_threshold"],
                    ),
                    defaults["antinuke_role_delete_threshold"],
                ),
            ),
        ),
        "antinuke_ban_threshold": max(
            2,
            min(
                50,
                _safe_int(
                    _cfg_value(cfg, "antinuke_ban_threshold", defaults["antinuke_ban_threshold"]),
                    defaults["antinuke_ban_threshold"],
                ),
            ),
        ),
        "antinuke_kick_threshold": max(
            2,
            min(
                50,
                _safe_int(
                    _cfg_value(cfg, "antinuke_kick_threshold", defaults["antinuke_kick_threshold"]),
                    defaults["antinuke_kick_threshold"],
                ),
            ),
        ),
        "antinuke_webhook_create_threshold": max(
            2,
            min(
                25,
                _safe_int(
                    _cfg_value(
                        cfg,
                        "antinuke_webhook_create_threshold",
                        defaults["antinuke_webhook_create_threshold"],
                    ),
                    defaults["antinuke_webhook_create_threshold"],
                ),
            ),
        ),
        "antinuke_protect_role_escalation": _safe_bool(
            _cfg_value(
                cfg,
                "antinuke_protect_role_escalation",
                defaults["antinuke_protect_role_escalation"],
            ),
            True,
        ),
        "antinuke_trusted_user_ids": _safe_id_list(
            _cfg_value(cfg, "antinuke_trusted_user_ids", defaults["antinuke_trusted_user_ids"])
        ),
        "antinuke_trusted_role_ids": _safe_id_list(
            _cfg_value(cfg, "antinuke_trusted_role_ids", defaults["antinuke_trusted_role_ids"])
        ),
    }


async def get_antinuke_settings(guild_id: int, *, refresh: bool = False) -> dict[str, Any]:
    """Read AntiNuke settings through the existing guild-config cache by default.

    The destructive-event path must not force a remote DB round trip for every
    Discord event. Explicit UI refreshes can still request a fresh read.
    """

    cfg = await get_guild_config(int(guild_id), refresh=bool(refresh))
    return normalize_antinuke_settings(cfg)


async def save_antinuke_settings(guild_id: int, patch: Mapping[str, Any]) -> dict[str, Any]:
    allowed = set(ANTINUKE_DEFAULTS)
    clean_patch = {
        str(key): value
        for key, value in dict(patch or {}).items()
        if str(key) in allowed
    }
    saved = await upsert_guild_config(int(guild_id), clean_patch)
    return normalize_antinuke_settings(saved)


def dangerous_permissions_added(before: Any, after: Any) -> list[str]:
    added: list[str] = []
    before_permissions = getattr(before, "permissions", before)
    after_permissions = getattr(after, "permissions", after)
    for name in DANGEROUS_PERMISSION_NAMES:
        try:
            old = bool(getattr(before_permissions, name, False))
            new = bool(getattr(after_permissions, name, False))
        except Exception:
            old = False
            new = False
        if new and not old:
            added.append(name)
    return added


def role_has_dangerous_permissions(role: Any) -> bool:
    permissions = getattr(role, "permissions", None)
    if permissions is None:
        return False
    return any(bool(getattr(permissions, name, False)) for name in DANGEROUS_PERMISSION_NAMES)


def _actor_role_ids(actor: Any) -> set[int]:
    out: set[int] = set()
    for role in list(getattr(actor, "roles", []) or []):
        role_id = _safe_int(getattr(role, "id", 0), 0)
        if role_id > 0:
            out.add(role_id)
    return out


def is_trusted_actor(guild: discord.Guild, actor: Any, settings: Mapping[str, Any]) -> bool:
    actor_id = _safe_int(getattr(actor, "id", 0), 0)
    if actor_id <= 0:
        return False
    if actor_id == _safe_int(getattr(guild, "owner_id", 0), 0):
        return True
    try:
        if getattr(bot, "user", None) is not None and actor_id == int(bot.user.id):
            return True
    except Exception:
        pass

    trusted_users = set(_safe_id_list(settings.get("antinuke_trusted_user_ids")))
    if actor_id in trusted_users:
        return True

    trusted_roles = set(_safe_id_list(settings.get("antinuke_trusted_role_ids")))
    return bool(trusted_roles.intersection(_actor_role_ids(actor)))


def _role_is_default(role: Any) -> bool:
    try:
        return bool(role.is_default())
    except Exception:
        return False


def _role_is_managed(role: Any) -> bool:
    return bool(getattr(role, "managed", False))


def _dangerous_hierarchy_blockers(
    guild: discord.Guild,
    member: Any,
    settings: Mapping[str, Any],
) -> list[str]:
    """Return dangerous roles that make containment impossible by hierarchy.

    Explicitly trusted roles and roles held only by trusted actors are not blockers.
    Managed dangerous roles are blockers for untrusted actors because Discord does
    not let Dank Shield strip managed roles during containment.
    """

    top_role = getattr(member, "top_role", None)
    if top_role is None:
        return ["resolve Dank Shield top role"]

    trusted_role_ids = set(_safe_id_list(settings.get("antinuke_trusted_role_ids")))
    blockers: list[str] = []

    for role in list(getattr(guild, "roles", []) or []):
        role_id = _safe_int(getattr(role, "id", 0), 0)
        if role_id in trusted_role_ids or not role_has_dangerous_permissions(role):
            continue

        role_name = str(getattr(role, "name", "dangerous-role") or "dangerous-role")
        if _role_is_default(role):
            blockers.append("@everyone grants dangerous permissions")
            continue

        role_members = list(getattr(role, "members", []) or [])
        if role_members and all(is_trusted_actor(guild, found, settings) for found in role_members):
            continue

        if _role_is_managed(role):
            blockers.append(f"managed @{role_name} cannot be stripped")
            continue

        try:
            manageable = bool(role < top_role)
        except Exception:
            manageable = False
        if not manageable:
            blockers.append(f"@{role_name}")

    return blockers


def antinuke_permission_health(
    guild: discord.Guild,
    settings: Optional[Mapping[str, Any]] = None,
) -> list[str]:
    clean = normalize_antinuke_settings(settings or {})
    if not clean["antinuke_enabled"]:
        return []

    member = getattr(guild, "me", None)
    if member is None:
        return ["Resolve bot member"]

    missing: list[str] = []
    permissions = getattr(member, "guild_permissions", None)
    if permissions is None:
        return ["Resolve bot permissions"]

    if not bool(
        getattr(permissions, "view_audit_log", False)
        or getattr(permissions, "administrator", False)
    ):
        missing.append("View Audit Log")

    if clean["antinuke_mode"] == "contain":
        if not bool(
            getattr(permissions, "manage_roles", False)
            or getattr(permissions, "administrator", False)
        ):
            missing.append("Manage Roles")

        hierarchy_blockers = _dangerous_hierarchy_blockers(guild, member, clean)
        if hierarchy_blockers:
            shown = ", ".join(hierarchy_blockers[:5])
            extra = len(hierarchy_blockers) - 5
            if extra > 0:
                shown += f" (+{extra} more)"
            missing.append(f"Role hierarchy: move Dank Shield above {shown}")

    return missing


def _record_action(
    guild_id: int,
    actor_id: int,
    action_key: str,
    *,
    window_seconds: int,
) -> int:
    now = time.monotonic()
    key = (int(guild_id), int(actor_id), str(action_key))
    window = _ACTION_WINDOWS[key]
    cutoff = now - max(1, int(window_seconds))
    while window and window[0] < cutoff:
        window.popleft()
    window.append(now)
    return len(window)


def _aggregate_threshold(settings: Mapping[str, Any]) -> int:
    values = [
        max(1, _safe_int(settings.get(key), 1))
        for key in _DESTRUCTIVE_THRESHOLD_KEYS
    ]
    return min(values) if values else 2


def _trigger_ready(guild_id: int, actor_id: int, action_key: str) -> bool:
    key = (int(guild_id), int(actor_id), str(action_key))
    last = _TRIGGER_COOLDOWNS.get(key)
    if last is None:
        return True
    return time.monotonic() - last >= _TRIGGER_COOLDOWN_SECONDS


def _mark_triggered(guild_id: int, actor_id: int, action_key: str) -> None:
    _TRIGGER_COOLDOWNS[(int(guild_id), int(actor_id), str(action_key))] = time.monotonic()


def _prune_seen_audit_entries() -> None:
    now = time.monotonic()
    stale = [
        key
        for key, seen_at in _SEEN_AUDIT_ENTRY_IDS.items()
        if now - seen_at > _AUDIT_DEDUPE_TTL_SECONDS
    ]
    for key in stale[:500]:
        _SEEN_AUDIT_ENTRY_IDS.pop(key, None)


def _audit_entry_seen(entry: Any) -> bool:
    entry_id = _safe_int(getattr(entry, "id", 0), 0)
    if entry_id <= 0:
        return False
    _prune_seen_audit_entries()
    return entry_id in _SEEN_AUDIT_ENTRY_IDS


def _consume_audit_entry(entry: Any) -> bool:
    entry_id = _safe_int(getattr(entry, "id", 0), 0)
    if entry_id <= 0:
        return False

    _prune_seen_audit_entries()
    if entry_id in _SEEN_AUDIT_ENTRY_IDS:
        return True
    _SEEN_AUDIT_ENTRY_IDS[entry_id] = time.monotonic()
    return False


def _audit_entry_is_fresh(
    entry: Any,
    *,
    max_age_seconds: float = _AUDIT_ENTRY_MAX_AGE_SECONDS,
) -> bool:
    created_at = getattr(entry, "created_at", None)
    if not isinstance(created_at, datetime):
        return True
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    age = (
        datetime.now(timezone.utc) - created_at.astimezone(timezone.utc)
    ).total_seconds()
    return -2.0 <= age <= max(1.0, float(max_age_seconds))


def _audit_action(name: str) -> Any:
    return getattr(discord.AuditLogAction, str(name), None)


def _log_audit_lookup_failure(
    guild: discord.Guild,
    action_name: str,
    exc: BaseException,
    *,
    attempt: int,
    retries: int,
) -> None:
    try:
        print(
            "⚠️ antinuke audit lookup failed "
            f"guild={getattr(guild, 'id', 'unknown')} action={action_name} "
            f"attempt={attempt}/{retries} error={type(exc).__name__}: {exc}"
        )
    except Exception:
        pass


async def _find_recent_audit_entry(
    guild: discord.Guild,
    action_name: str,
    *,
    target_id: Optional[int] = None,
    retries: int = 3,
) -> Optional[Any]:
    action = _audit_action(action_name)
    if action is None:
        return None

    attempts = max(1, int(retries))
    for attempt in range(1, attempts + 1):
        try:
            async for entry in guild.audit_logs(limit=_AUDIT_SEARCH_LIMIT, action=action):
                if _audit_entry_seen(entry) or not _audit_entry_is_fresh(entry):
                    continue
                if target_id is not None:
                    target = getattr(entry, "target", None)
                    found_target_id = _safe_int(getattr(target, "id", 0), 0)
                    if found_target_id != int(target_id):
                        continue
                return entry
        except discord.Forbidden as exc:
            _log_audit_lookup_failure(
                guild,
                action_name,
                exc,
                attempt=attempt,
                retries=attempts,
            )
            return None
        except Exception as exc:
            _log_audit_lookup_failure(
                guild,
                action_name,
                exc,
                attempt=attempt,
                retries=attempts,
            )

        if attempt < attempts:
            await asyncio.sleep(0.4 * attempt)
    return None


def _manageable_dangerous_roles(
    guild: discord.Guild,
    actor: discord.Member,
) -> tuple[list[discord.Role], list[discord.Role]]:
    me = getattr(guild, "me", None)
    if not isinstance(me, discord.Member):
        return [], []

    removable: list[discord.Role] = []
    blocked: list[discord.Role] = []
    for role in list(getattr(actor, "roles", []) or []):
        try:
            if _role_is_default(role) or not role_has_dangerous_permissions(role):
                continue
            if _role_is_managed(role):
                blocked.append(role)
                continue
            if role < me.top_role:
                removable.append(role)
            else:
                blocked.append(role)
        except Exception:
            continue
    return removable, blocked


async def _contain_actor(
    guild: discord.Guild,
    actor: Any,
    *,
    reason: str,
) -> tuple[list[str], list[str]]:
    actor_id = _safe_int(getattr(actor, "id", 0), 0)
    if actor_id <= 0 or actor_id == _safe_int(getattr(guild, "owner_id", 0), 0):
        return [], []

    member = actor if isinstance(actor, discord.Member) else None
    if member is None:
        try:
            member = guild.get_member(actor_id) or await guild.fetch_member(actor_id)
        except Exception:
            member = None
    if not isinstance(member, discord.Member):
        return [], []

    removable, blocked = _manageable_dangerous_roles(guild, member)
    if removable:
        try:
            await member.remove_roles(*removable, reason=reason)
        except Exception:
            blocked = [*blocked, *removable]
            removable = []
    return [str(role.name) for role in removable], [str(role.name) for role in blocked]


def _actor_label(actor: Any) -> str:
    actor_id = _safe_int(getattr(actor, "id", 0), 0)
    mention = getattr(actor, "mention", None)
    name = str(actor or "Unknown")
    if mention and actor_id > 0:
        return f"{mention} (`{actor_id}`)"
    if actor_id > 0:
        return f"{name} (`{actor_id}`)"
    return name


async def _post_incident(
    guild: discord.Guild,
    *,
    title: str,
    actor: Any,
    action_label: str,
    target_label: str,
    response_label: str,
    count_label: str = "",
    details: str = "",
) -> None:
    embed = discord.Embed(
        title=title,
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="Actor", value=_actor_label(actor), inline=False)
    embed.add_field(name="Detected", value=action_label, inline=False)
    embed.add_field(name="Target", value=target_label[:1024] or "Unknown", inline=False)
    if count_label:
        embed.add_field(name="Threshold", value=count_label[:1024], inline=False)
    embed.add_field(name="Response", value=response_label[:1024], inline=False)
    if details:
        embed.add_field(name="Details", value=details[:1024], inline=False)
    embed.set_footer(text="Dank Shield AntiNuke • audit-log attributed")
    await _post_modlog(guild, embed)


async def _handle_threshold_event(
    guild: discord.Guild,
    *,
    audit_action: str,
    action_key: str,
    action_label: str,
    target_id: Optional[int],
    target_label: str,
    threshold_key: str,
) -> None:
    settings = await get_antinuke_settings(int(guild.id))
    if not settings["antinuke_enabled"]:
        return

    entry = await _find_recent_audit_entry(guild, audit_action, target_id=target_id)
    if entry is None or _consume_audit_entry(entry):
        return

    actor = getattr(entry, "user", None)
    actor_id = _safe_int(getattr(actor, "id", 0), 0)
    if actor_id <= 0 or is_trusted_actor(guild, actor, settings):
        return

    window_seconds = int(settings["antinuke_window_seconds"])
    threshold = int(settings[threshold_key])
    count = _record_action(
        int(guild.id),
        actor_id,
        action_key,
        window_seconds=window_seconds,
    )
    aggregate_count = _record_action(
        int(guild.id),
        actor_id,
        _AGGREGATE_ACTION_KEY,
        window_seconds=window_seconds,
    )
    aggregate_threshold = _aggregate_threshold(settings)

    category_triggered = count >= threshold
    aggregate_triggered = aggregate_count >= aggregate_threshold
    if not category_triggered and not aggregate_triggered:
        return

    if not _trigger_ready(int(guild.id), actor_id, _CONTAINMENT_COOLDOWN_KEY):
        return

    removed: list[str] = []
    blocked: list[str] = []
    containment_succeeded = settings["antinuke_mode"] == "alert"

    if settings["antinuke_mode"] == "contain":
        removed, blocked = await _contain_actor(
            guild,
            actor,
            reason=f"Dank Shield AntiNuke containment: {action_label}",
        )
        containment_succeeded = bool(removed) and not blocked

    if settings["antinuke_mode"] == "alert":
        response = "Alert-only mode: no roles were changed."
    elif containment_succeeded:
        response = "Contained actor by removing dangerous roles: " + ", ".join(removed)
    elif removed and blocked:
        response = (
            "Partial containment only. Removed: "
            + ", ".join(removed)
            + ". Still blocked by higher/managed dangerous roles: "
            + ", ".join(blocked)
            + ". Dank Shield will retry on the next attributed destructive action."
        )
    elif blocked:
        response = (
            "Containment failed because dangerous roles are higher than Dank Shield or managed by Discord: "
            + ", ".join(blocked)
            + ". Dank Shield will retry on the next attributed destructive action."
        )
    else:
        response = (
            "Containment could not remove a dangerous role from the actor. "
            "Dank Shield will retry on the next attributed destructive action."
        )

    if containment_succeeded:
        _mark_triggered(int(guild.id), actor_id, _CONTAINMENT_COOLDOWN_KEY)

    trigger_source = []
    if category_triggered:
        trigger_source.append(f"{action_key} {count}/{threshold}")
    if aggregate_triggered:
        trigger_source.append(f"mixed destructive actions {aggregate_count}/{aggregate_threshold}")

    await _post_incident(
        guild,
        title="🚨 AntiNuke Triggered",
        actor=actor,
        action_label=action_label,
        target_label=target_label,
        response_label=response,
        count_label=f"{window_seconds}s window • " + " • ".join(trigger_source),
    )


async def _handle_role_permission_escalation(
    before: discord.Role,
    after: discord.Role,
) -> None:
    added = dangerous_permissions_added(before, after)
    if not added:
        return

    guild = after.guild
    settings = await get_antinuke_settings(int(guild.id))
    if not settings["antinuke_enabled"] or not settings["antinuke_protect_role_escalation"]:
        return

    entry = await _find_recent_audit_entry(guild, "role_update", target_id=int(after.id))
    if entry is None or _consume_audit_entry(entry):
        return
    actor = getattr(entry, "user", None)
    if is_trusted_actor(guild, actor, settings):
        return

    rollback = "Alert-only mode: dangerous permission change was not reverted."
    if settings["antinuke_mode"] == "contain":
        try:
            me = guild.me
            if isinstance(me, discord.Member) and after < me.top_role and not after.managed:
                await after.edit(
                    permissions=before.permissions,
                    reason="Dank Shield AntiNuke rollback: dangerous role permission escalation",
                )
                rollback = "Reverted the role permissions to their previous state."
            else:
                rollback = "Could not revert the role because it is managed or above Dank Shield's role."
        except Exception as exc:
            rollback = f"Role rollback failed safely: {type(exc).__name__}."

        removed, blocked = await _contain_actor(
            guild,
            actor,
            reason="Dank Shield AntiNuke containment: dangerous role permission escalation",
        )
        if removed:
            rollback += " Removed dangerous actor roles: " + ", ".join(removed) + "."
        if blocked:
            rollback += " Could not remove actor roles above/managed by Discord: " + ", ".join(blocked) + "."

    await _post_incident(
        guild,
        title="🚨 AntiNuke Permission Escalation",
        actor=actor,
        action_label="Dangerous permissions added to a role: " + ", ".join(added),
        target_label=f"@{after.name} (`{after.id}`)",
        response_label=rollback,
    )


async def _handle_member_dangerous_role_grant(
    before: discord.Member,
    after: discord.Member,
) -> None:
    before_ids = {int(role.id) for role in list(before.roles or [])}
    new_roles = [
        role
        for role in list(after.roles or [])
        if int(role.id) not in before_ids and role_has_dangerous_permissions(role)
    ]
    if not new_roles:
        return

    guild = after.guild
    settings = await get_antinuke_settings(int(guild.id))
    if not settings["antinuke_enabled"] or not settings["antinuke_protect_role_escalation"]:
        return

    entry = await _find_recent_audit_entry(
        guild,
        "member_role_update",
        target_id=int(after.id),
    )
    if entry is None or _consume_audit_entry(entry):
        return
    actor = getattr(entry, "user", None)
    if is_trusted_actor(guild, actor, settings):
        return

    response = "Alert-only mode: dangerous role grant was not reverted."
    if settings["antinuke_mode"] == "contain":
        me = guild.me
        removable = [
            role
            for role in new_roles
            if isinstance(me, discord.Member) and not role.managed and role < me.top_role
        ]
        blocked = [role for role in new_roles if role not in removable]
        if removable:
            try:
                await after.remove_roles(
                    *removable,
                    reason="Dank Shield AntiNuke rollback: dangerous role grant",
                )
                response = "Removed newly granted dangerous roles: " + ", ".join(
                    role.name for role in removable
                ) + "."
            except Exception as exc:
                response = f"Dangerous role rollback failed safely: {type(exc).__name__}."
        if blocked:
            response += " Could not remove higher/managed roles: " + ", ".join(
                role.name for role in blocked
            ) + "."

        removed_actor, blocked_actor = await _contain_actor(
            guild,
            actor,
            reason="Dank Shield AntiNuke containment: dangerous role grant",
        )
        if removed_actor:
            response += " Removed dangerous actor roles: " + ", ".join(removed_actor) + "."
        if blocked_actor:
            response += " Could not remove actor roles above/managed by Discord: " + ", ".join(blocked_actor) + "."

    await _post_incident(
        guild,
        title="🚨 AntiNuke Dangerous Role Grant",
        actor=actor,
        action_label="Dangerous role(s) granted to a member",
        target_label=f"{after.mention} (`{after.id}`) • " + ", ".join(
            role.name for role in new_roles
        ),
        response_label=response,
    )


@bot.listen("on_guild_channel_delete")
async def antinuke_on_guild_channel_delete(
    channel: discord.abc.GuildChannel,
) -> None:
    await _handle_threshold_event(
        channel.guild,
        audit_action="channel_delete",
        action_key="channel_delete",
        action_label="Mass channel deletion",
        target_id=int(channel.id),
        target_label=f"#{getattr(channel, 'name', 'deleted-channel')} (`{channel.id}`)",
        threshold_key="antinuke_channel_delete_threshold",
    )


@bot.listen("on_guild_role_delete")
async def antinuke_on_guild_role_delete(role: discord.Role) -> None:
    await _handle_threshold_event(
        role.guild,
        audit_action="role_delete",
        action_key="role_delete",
        action_label="Mass role deletion",
        target_id=int(role.id),
        target_label=f"@{role.name} (`{role.id}`)",
        threshold_key="antinuke_role_delete_threshold",
    )


@bot.listen("on_member_ban")
async def antinuke_on_member_ban(
    guild: discord.Guild,
    user: discord.User | discord.Member,
) -> None:
    await _handle_threshold_event(
        guild,
        audit_action="ban",
        action_key="ban",
        action_label="Mass member bans",
        target_id=int(user.id),
        target_label=f"{user} (`{user.id}`)",
        threshold_key="antinuke_ban_threshold",
    )


@bot.listen("on_member_remove")
async def antinuke_on_member_remove(member: discord.Member) -> None:
    # Ordinary member leaves have no matching kick audit entry, so they are ignored.
    await _handle_threshold_event(
        member.guild,
        audit_action="kick",
        action_key="kick",
        action_label="Mass member kicks",
        target_id=int(member.id),
        target_label=f"{member} (`{member.id}`)",
        threshold_key="antinuke_kick_threshold",
    )


@bot.listen("on_webhooks_update")
async def antinuke_on_webhooks_update(
    channel: discord.abc.GuildChannel,
) -> None:
    await _handle_threshold_event(
        channel.guild,
        audit_action="webhook_create",
        action_key="webhook_create",
        action_label="Webhook creation flood",
        target_id=None,
        target_label=f"#{getattr(channel, 'name', 'channel')} (`{channel.id}`)",
        threshold_key="antinuke_webhook_create_threshold",
    )


@bot.listen("on_guild_role_update")
async def antinuke_on_guild_role_update(
    before: discord.Role,
    after: discord.Role,
) -> None:
    await _handle_role_permission_escalation(before, after)


@bot.listen("on_member_update")
async def antinuke_on_member_update(
    before: discord.Member,
    after: discord.Member,
) -> None:
    await _handle_member_dangerous_role_grant(before, after)


__all__ = [
    "ANTINUKE_DEFAULTS",
    "DANGEROUS_PERMISSION_NAMES",
    "antinuke_permission_health",
    "dangerous_permissions_added",
    "get_antinuke_settings",
    "is_trusted_actor",
    "normalize_antinuke_settings",
    "role_has_dangerous_permissions",
    "save_antinuke_settings",
]
