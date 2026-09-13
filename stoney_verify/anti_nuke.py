from __future__ import annotations

"""Native per-guild AntiNuke protection for Dank Shield."""

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
_AUDIT_CLAIM_LOCKS: dict[tuple[int, str], asyncio.Lock] = {}
_CONTAINMENT_LOCKS: dict[tuple[int, int], asyncio.Lock] = {}

_TRIGGER_COOLDOWN_SECONDS = 30.0
_AUDIT_ENTRY_MAX_AGE_SECONDS = 30.0
_AUDIT_DEDUPE_TTL_SECONDS = 90.0
_AUDIT_SEARCH_LIMIT = 50
_AUDIT_LOOKUP_RETRIES = 4

_AGGREGATE_ACTION_KEY = "__destructive__"
_SLOW_BURN_ACTION_KEY = "__structural_slow_burn__"
_SLOW_BURN_ACTIONS = frozenset(
    {
        "channel_delete",
        "role_delete",
        "webhook_create",
        "webhook_update",
        "webhook_delete",
    }
)
_SLOW_BURN_WINDOW_SECONDS = 600
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
    mode = str(
        _cfg_value(cfg, "antinuke_mode", defaults["antinuke_mode"]) or "contain"
    ).strip().lower()
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
                    _cfg_value(
                        cfg,
                        "antinuke_window_seconds",
                        defaults["antinuke_window_seconds"],
                    ),
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
                    _cfg_value(
                        cfg,
                        "antinuke_ban_threshold",
                        defaults["antinuke_ban_threshold"],
                    ),
                    defaults["antinuke_ban_threshold"],
                ),
            ),
        ),
        "antinuke_kick_threshold": max(
            2,
            min(
                50,
                _safe_int(
                    _cfg_value(
                        cfg,
                        "antinuke_kick_threshold",
                        defaults["antinuke_kick_threshold"],
                    ),
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
            _cfg_value(
                cfg,
                "antinuke_trusted_user_ids",
                defaults["antinuke_trusted_user_ids"],
            )
        ),
        "antinuke_trusted_role_ids": _safe_id_list(
            _cfg_value(
                cfg,
                "antinuke_trusted_role_ids",
                defaults["antinuke_trusted_role_ids"],
            )
        ),
    }


async def get_antinuke_settings(
    guild_id: int,
    *,
    refresh: bool = False,
) -> dict[str, Any]:
    cfg = await get_guild_config(int(guild_id), refresh=bool(refresh))
    return normalize_antinuke_settings(cfg)


async def save_antinuke_settings(
    guild_id: int,
    patch: Mapping[str, Any],
) -> dict[str, Any]:
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
    return any(
        bool(getattr(permissions, name, False))
        for name in DANGEROUS_PERMISSION_NAMES
    )


def _actor_role_ids(actor: Any) -> set[int]:
    out: set[int] = set()
    for role in list(getattr(actor, "roles", []) or []):
        role_id = _safe_int(getattr(role, "id", 0), 0)
        if role_id > 0:
            out.add(role_id)
    return out


def _actor_is_owner_or_bot(guild: discord.Guild, actor: Any) -> bool:
    actor_id = _safe_int(getattr(actor, "id", 0), 0)
    if actor_id <= 0:
        return False
    if actor_id == _safe_int(getattr(guild, "owner_id", 0), 0):
        return True
    try:
        return bool(
            getattr(bot, "user", None) is not None
            and actor_id == int(bot.user.id)
        )
    except Exception:
        return False


def _actor_is_configured_trusted(
    actor: Any,
    settings: Mapping[str, Any],
    *,
    ignore_role_ids: Optional[set[int]] = None,
) -> bool:
    actor_id = _safe_int(getattr(actor, "id", 0), 0)
    if actor_id <= 0:
        return False

    trusted_users = set(
        _safe_id_list(settings.get("antinuke_trusted_user_ids"))
    )
    if actor_id in trusted_users:
        return True

    ignored = {
        _safe_int(value, 0)
        for value in (ignore_role_ids or set())
        if _safe_int(value, 0) > 0
    }
    trusted_roles = set(
        _safe_id_list(settings.get("antinuke_trusted_role_ids"))
    ) - ignored
    return bool(trusted_roles.intersection(_actor_role_ids(actor)))


def is_trusted_actor(
    guild: discord.Guild,
    actor: Any,
    settings: Mapping[str, Any],
    *,
    ignore_role_ids: Optional[set[int]] = None,
) -> bool:
    """Compatibility trust helper.

    Owner/bot are absolute roots. Configured trusted users/roles are delegated
    operators, but AntiNuke's destructive-event engine still applies emergency
    ceilings to them rather than treating them as infinitely exempt.
    """

    if _actor_is_owner_or_bot(guild, actor):
        return True
    return _actor_is_configured_trusted(
        actor,
        settings,
        ignore_role_ids=ignore_role_ids,
    )


def _role_is_default(role: Any) -> bool:
    try:
        return bool(role.is_default())
    except Exception:
        return False


def _role_is_managed(role: Any) -> bool:
    return bool(getattr(role, "managed", False))


def _role_is_below(role: Any, other: Any) -> bool:
    try:
        return bool(role < other)
    except Exception:
        return False


def _member_is_manageable_by_bot(
    guild: discord.Guild,
    member: Any,
) -> bool:
    me = getattr(guild, "me", None)
    bot_top = getattr(me, "top_role", None)
    member_top = getattr(member, "top_role", None)
    if bot_top is None or member_top is None:
        return False
    return _role_is_below(member_top, bot_top)


def _dangerous_hierarchy_blockers(
    guild: discord.Guild,
    member: Any,
    settings: Mapping[str, Any],
) -> list[str]:
    """Return dangerous roles/members that Dank Shield cannot contain."""

    top_role = getattr(member, "top_role", None)
    if top_role is None:
        return ["resolve Dank Shield top role"]

    blockers: list[str] = []
    for role in list(getattr(guild, "roles", []) or []):
        if not role_has_dangerous_permissions(role):
            continue

        role_name = str(
            getattr(role, "name", "dangerous-role") or "dangerous-role"
        )
        if _role_is_default(role):
            blockers.append("@everyone grants dangerous permissions")
            continue

        role_members = list(getattr(role, "members", []) or [])
        if role_members and all(
            _actor_is_owner_or_bot(guild, found)
            for found in role_members
        ):
            continue

        if _role_is_managed(role):
            blockers.append(f"managed @{role_name} cannot be stripped")
            continue

        if not _role_is_below(role, top_role):
            blockers.append(f"@{role_name}")
            continue

        for holder in role_members:
            if _actor_is_owner_or_bot(guild, holder):
                continue
            holder_top = getattr(holder, "top_role", None)
            if holder_top is None or not _role_is_below(holder_top, top_role):
                holder_id = _safe_int(getattr(holder, "id", 0), 0)
                holder_label = (
                    f"member {holder_id}" if holder_id > 0 else "a member"
                )
                blockers.append(
                    f"{holder_label} outranks Dank Shield while inheriting "
                    f"@{role_name}"
                )

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

    permissions = getattr(member, "guild_permissions", None)
    if permissions is None:
        return ["Resolve bot permissions"]

    missing: list[str] = []
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
        if not bool(
            getattr(permissions, "kick_members", False)
            or getattr(permissions, "administrator", False)
        ):
            missing.append("Kick Members")

        hierarchy_blockers = _dangerous_hierarchy_blockers(
            guild,
            member,
            clean,
        )
        if hierarchy_blockers:
            unique = list(dict.fromkeys(hierarchy_blockers))
            shown = ", ".join(unique[:5])
            if len(unique) > 5:
                shown += f" (+{len(unique) - 5} more)"
            missing.append(
                f"Role hierarchy: move Dank Shield above {shown}"
            )

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


def _slow_burn_threshold(settings: Mapping[str, Any]) -> int:
    return max(4, _aggregate_threshold(settings) * 2)


def _trigger_ready(
    guild_id: int,
    actor_id: int,
    action_key: str,
) -> bool:
    key = (int(guild_id), int(actor_id), str(action_key))
    last = _TRIGGER_COOLDOWNS.get(key)
    if last is None:
        return True
    return time.monotonic() - last >= _TRIGGER_COOLDOWN_SECONDS


def _mark_triggered(
    guild_id: int,
    actor_id: int,
    action_key: str,
) -> None:
    _TRIGGER_COOLDOWNS[
        (int(guild_id), int(actor_id), str(action_key))
    ] = time.monotonic()


def _lock_for(
    store: dict[Any, asyncio.Lock],
    key: Any,
) -> asyncio.Lock:
    lock = store.get(key)
    if lock is None:
        lock = asyncio.Lock()
        store[key] = lock
    return lock


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
        datetime.now(timezone.utc)
        - created_at.astimezone(timezone.utc)
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
            f"guild={getattr(guild, 'id', 'unknown')} "
            f"action={action_name} attempt={attempt}/{retries} "
            f"error={type(exc).__name__}: {exc}"
        )
    except Exception:
        pass


async def _find_recent_audit_entry(
    guild: discord.Guild,
    action_name: str,
    *,
    target_id: Optional[int] = None,
    retries: int = _AUDIT_LOOKUP_RETRIES,
) -> Optional[Any]:
    action = _audit_action(action_name)
    if action is None:
        return None

    attempts = max(1, int(retries))
    for attempt in range(1, attempts + 1):
        try:
            async for entry in guild.audit_logs(
                limit=_AUDIT_SEARCH_LIMIT,
                action=action,
            ):
                if _audit_entry_seen(entry):
                    continue
                if not _audit_entry_is_fresh(entry):
                    continue
                if target_id is not None:
                    target = getattr(entry, "target", None)
                    found_target_id = _safe_int(
                        getattr(target, "id", 0),
                        0,
                    )
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
            await asyncio.sleep(min(1.2, 0.4 * attempt))
    return None


async def _claim_recent_audit_entry(
    guild: discord.Guild,
    action_name: str,
    *,
    target_id: Optional[int] = None,
    retries: int = _AUDIT_LOOKUP_RETRIES,
) -> Optional[Any]:
    """Atomically find and consume one audit entry for an event callback."""

    key = (int(guild.id), str(action_name))
    lock = _lock_for(_AUDIT_CLAIM_LOCKS, key)
    async with lock:
        entry = await _find_recent_audit_entry(
            guild,
            action_name,
            target_id=target_id,
            retries=retries,
        )
        if entry is None:
            return None
        if _consume_audit_entry(entry):
            return None
        return entry


async def _claim_recent_audit_entry_any(
    guild: discord.Guild,
    action_names: tuple[str, ...],
    *,
    retries: int = 3,
) -> tuple[Optional[str], Optional[Any]]:
    """Claim the newest unconsumed audit entry across related action types."""

    names = tuple(str(name) for name in action_names if str(name))
    if not names:
        return None, None

    key = (int(guild.id), "any:" + ",".join(sorted(names)))
    lock = _lock_for(_AUDIT_CLAIM_LOCKS, key)
    async with lock:
        attempts = max(1, int(retries))
        for attempt in range(1, attempts + 1):
            candidates: list[tuple[str, Any]] = []
            for action_name in names:
                entry = await _find_recent_audit_entry(
                    guild,
                    action_name,
                    retries=1,
                )
                if entry is not None:
                    candidates.append((action_name, entry))

            if candidates:
                action_name, entry = max(
                    candidates,
                    key=lambda item: _safe_int(
                        getattr(item[1], "id", 0),
                        0,
                    ),
                )
                if not _consume_audit_entry(entry):
                    return action_name, entry

            if attempt < attempts:
                await asyncio.sleep(min(1.0, 0.35 * attempt))

    return None, None


def _manageable_dangerous_roles(
    guild: discord.Guild,
    actor: discord.Member,
) -> tuple[list[discord.Role], list[discord.Role]]:
    me = getattr(guild, "me", None)
    if not isinstance(me, discord.Member):
        return [], []

    member_manageable = _member_is_manageable_by_bot(guild, actor)
    removable: list[discord.Role] = []
    blocked: list[discord.Role] = []

    for role in list(getattr(actor, "roles", []) or []):
        try:
            if _role_is_default(role):
                continue
            if not role_has_dangerous_permissions(role):
                continue
            if (
                not member_manageable
                or _role_is_managed(role)
                or not _role_is_below(role, me.top_role)
            ):
                blocked.append(role)
            else:
                removable.append(role)
        except Exception:
            blocked.append(role)

    return removable, blocked


async def _contain_actor(
    guild: discord.Guild,
    actor: Any,
    *,
    reason: str,
) -> tuple[list[str], list[str]]:
    actor_id = _safe_int(
        actor if isinstance(actor, int) else getattr(actor, "id", 0),
        0,
    )
    if actor_id <= 0:
        return [], []
    if actor_id == _safe_int(getattr(guild, "owner_id", 0), 0):
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

    return (
        [str(role.name) for role in removable],
        [str(role.name) for role in blocked],
    )


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
    embed.add_field(
        name="Target",
        value=target_label[:1024] or "Unknown",
        inline=False,
    )
    if count_label:
        embed.add_field(
            name="Threshold",
            value=count_label[:1024],
            inline=False,
        )
    embed.add_field(
        name="Response",
        value=response_label[:1024],
        inline=False,
    )
    if details:
        embed.add_field(
            name="Details",
            value=details[:1024],
            inline=False,
        )
    embed.set_footer(text="Dank Shield AntiNuke • audit-log attributed")
    await _post_modlog(guild, embed)


async def _process_claimed_destructive_event(
    guild: discord.Guild,
    *,
    entry: Any,
    action_key: str,
    action_label: str,
    target_label: str,
    threshold_key: str,
    threshold_override: Optional[int] = None,
) -> bool:
    settings = await get_antinuke_settings(int(guild.id))
    if not settings["antinuke_enabled"]:
        return False

    actor = getattr(entry, "user", None)
    actor_id = _safe_int(getattr(actor, "id", 0), 0)
    if actor_id <= 0:
        return True
    if _actor_is_owner_or_bot(guild, actor):
        return True

    delegated_trust = _actor_is_configured_trusted(actor, settings)
    window_seconds = int(settings["antinuke_window_seconds"])

    if threshold_override is not None:
        threshold = max(1, int(threshold_override))
    elif delegated_trust:
        threshold = int(settings[threshold_key])
    else:
        # Unknown/untrusted actors get no free destructive action. Configured
        # trusted operators use the owner's saved burst limits instead.
        threshold = 1

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
    aggregate_threshold = (
        _aggregate_threshold(settings) if delegated_trust else 1
    )

    category_triggered = count >= threshold
    aggregate_triggered = aggregate_count >= aggregate_threshold

    slow_count = 0
    slow_threshold = 0
    slow_triggered = False
    if action_key in _SLOW_BURN_ACTIONS:
        slow_count = _record_action(
            int(guild.id),
            actor_id,
            _SLOW_BURN_ACTION_KEY,
            window_seconds=_SLOW_BURN_WINDOW_SECONDS,
        )
        slow_threshold = _slow_burn_threshold(settings)
        slow_triggered = slow_count >= slow_threshold

    if not category_triggered and not aggregate_triggered and not slow_triggered:
        return True

    containment_lock = _lock_for(
        _CONTAINMENT_LOCKS,
        (int(guild.id), actor_id),
    )
    async with containment_lock:
        if not _trigger_ready(
            int(guild.id),
            actor_id,
            _CONTAINMENT_COOLDOWN_KEY,
        ):
            return True

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

        if containment_succeeded:
            _mark_triggered(
                int(guild.id),
                actor_id,
                _CONTAINMENT_COOLDOWN_KEY,
            )

    if settings["antinuke_mode"] == "alert":
        response = "Alert-only mode: no roles were changed."
    elif containment_succeeded:
        response = (
            "Contained actor by removing dangerous roles: "
            + ", ".join(removed)
        )
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
            "Containment failed because the actor or dangerous roles outrank "
            "Dank Shield, or Discord manages the role: "
            + ", ".join(blocked)
            + ". Dank Shield will retry on the next attributed destructive action."
        )
    else:
        response = (
            "Containment could not remove a dangerous role from the actor. "
            "Dank Shield will retry on the next attributed destructive action."
        )

    trigger_source: list[str] = []
    if not delegated_trust:
        trigger_source.append("untrusted actor • first-strike policy")
    if category_triggered:
        trigger_source.append(f"{action_key} {count}/{threshold}")
    if aggregate_triggered:
        trigger_source.append(
            f"mixed destructive actions {aggregate_count}/{aggregate_threshold}"
        )
    if slow_triggered:
        trigger_source.append(
            "structural slow-burn "
            f"{_SLOW_BURN_WINDOW_SECONDS}s {slow_count}/{slow_threshold}"
        )

    await _post_incident(
        guild,
        title="🚨 AntiNuke Triggered",
        actor=actor,
        action_label=action_label,
        target_label=target_label,
        response_label=response,
        count_label=(
            f"{window_seconds}s window • " + " • ".join(trigger_source)
        ),
    )
    return True


async def _handle_threshold_event(
    guild: discord.Guild,
    *,
    audit_action: str,
    action_key: str,
    action_label: str,
    target_id: Optional[int],
    target_label: str,
    threshold_key: str,
    threshold_override: Optional[int] = None,
) -> bool:
    settings = await get_antinuke_settings(int(guild.id))
    if not settings["antinuke_enabled"]:
        return False

    entry = await _claim_recent_audit_entry(
        guild,
        audit_action,
        target_id=target_id,
    )
    if entry is None:
        return False

    return await _process_claimed_destructive_event(
        guild,
        entry=entry,
        action_key=action_key,
        action_label=action_label,
        target_label=target_label,
        threshold_key=threshold_key,
        threshold_override=threshold_override,
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
    if (
        not settings["antinuke_enabled"]
        or not settings["antinuke_protect_role_escalation"]
    ):
        return

    entry = await _claim_recent_audit_entry(
        guild,
        "role_update",
        target_id=int(after.id),
    )
    if entry is None:
        return

    actor = getattr(entry, "user", None)
    if _actor_is_owner_or_bot(guild, actor):
        return

    rollback = "Alert-only mode: dangerous permission change was not reverted."
    if settings["antinuke_mode"] == "contain":
        try:
            me = guild.me
            if (
                isinstance(me, discord.Member)
                and _role_is_below(after, me.top_role)
                and not after.managed
            ):
                await after.edit(
                    permissions=before.permissions,
                    reason=(
                        "Dank Shield AntiNuke rollback: "
                        "dangerous role permission escalation"
                    ),
                )
                rollback = "Reverted the role permissions to their previous state."
            else:
                rollback = (
                    "Could not revert the role because it is managed or above "
                    "Dank Shield's role."
                )
        except Exception as exc:
            rollback = f"Role rollback failed safely: {type(exc).__name__}."

        actor_id = _safe_int(getattr(actor, "id", 0), 0)
        lock = _lock_for(_CONTAINMENT_LOCKS, (int(guild.id), actor_id))
        async with lock:
            removed, blocked = await _contain_actor(
                guild,
                actor,
                reason=(
                    "Dank Shield AntiNuke containment: "
                    "dangerous role permission escalation"
                ),
            )

        if removed:
            rollback += (
                " Removed dangerous actor roles: "
                + ", ".join(removed)
                + "."
            )
        if blocked:
            rollback += (
                " Could not remove actor roles because of hierarchy/managed roles: "
                + ", ".join(blocked)
                + "."
            )

    await _post_incident(
        guild,
        title="🚨 AntiNuke Permission Escalation",
        actor=actor,
        action_label=(
            "Dangerous permissions added to a role: " + ", ".join(added)
        ),
        target_label=f"@{after.name} (`{after.id}`)",
        response_label=rollback,
    )


async def _handle_member_dangerous_role_grant(
    before: discord.Member,
    after: discord.Member,
) -> None:
    guild = after.guild
    settings = await get_antinuke_settings(int(guild.id))
    if (
        not settings["antinuke_enabled"]
        or not settings["antinuke_protect_role_escalation"]
    ):
        return

    trusted_role_ids = set(
        _safe_id_list(settings.get("antinuke_trusted_role_ids"))
    )
    before_ids = {int(role.id) for role in list(before.roles or [])}
    new_roles = [
        role
        for role in list(after.roles or [])
        if int(role.id) not in before_ids
        and (
            role_has_dangerous_permissions(role)
            or int(role.id) in trusted_role_ids
        )
    ]
    if not new_roles:
        return

    entry = await _claim_recent_audit_entry(
        guild,
        "member_role_update",
        target_id=int(after.id),
    )
    if entry is None:
        return

    actor = getattr(entry, "user", None)
    if _actor_is_owner_or_bot(guild, actor):
        return

    sensitive_names = ", ".join(
        str(getattr(role, "name", role.id)) for role in new_roles
    )
    response = "Alert-only mode: security-sensitive role grant was not reverted."

    if settings["antinuke_mode"] == "contain":
        me = guild.me
        target_manageable = _member_is_manageable_by_bot(guild, after)
        removable = [
            role
            for role in new_roles
            if (
                isinstance(me, discord.Member)
                and target_manageable
                and not role.managed
                and _role_is_below(role, me.top_role)
            )
        ]
        blocked = [role for role in new_roles if role not in removable]

        if removable:
            try:
                await after.remove_roles(
                    *removable,
                    reason=(
                        "Dank Shield AntiNuke rollback: security-sensitive role grant"
                    ),
                )
                response = (
                    "Removed newly granted security-sensitive roles: "
                    + ", ".join(role.name for role in removable)
                    + "."
                )
            except Exception as exc:
                response = (
                    "Security-sensitive role rollback failed safely: "
                    f"{type(exc).__name__}."
                )
                blocked = list(dict.fromkeys([*blocked, *removable]))
                removable = []

        if blocked:
            response += (
                " Could not remove higher/managed roles or modify the target member: "
                + ", ".join(role.name for role in blocked)
                + "."
            )

        actor_id = _safe_int(getattr(actor, "id", 0), 0)
        lock = _lock_for(_CONTAINMENT_LOCKS, (int(guild.id), actor_id))
        async with lock:
            removed_actor, blocked_actor = await _contain_actor(
                guild,
                actor,
                reason=(
                    "Dank Shield AntiNuke containment: security-sensitive role grant"
                ),
            )

        if removed_actor:
            response += (
                " Removed dangerous actor roles: "
                + ", ".join(removed_actor)
                + "."
            )
        if blocked_actor:
            response += (
                " Could not remove actor roles because of hierarchy/managed roles: "
                + ", ".join(blocked_actor)
                + "."
            )

    await _post_incident(
        guild,
        title="🚨 AntiNuke Security-Sensitive Role Grant",
        actor=actor,
        action_label=(
            "Dangerous or trusted-exemption role(s) granted to a member"
        ),
        target_label=f"{after.mention} (`{after.id}`) • {sensitive_names}",
        response_label=response,
    )


async def _handle_dangerous_role_create(role: discord.Role) -> None:
    if not role_has_dangerous_permissions(role):
        return

    guild = role.guild
    settings = await get_antinuke_settings(int(guild.id))
    if not settings["antinuke_enabled"]:
        return

    entry = await _claim_recent_audit_entry(
        guild,
        "role_create",
        target_id=int(role.id),
    )
    if entry is None:
        return

    actor = getattr(entry, "user", None)
    if _actor_is_owner_or_bot(guild, actor):
        return

    response = "Alert-only mode: newly created dangerous role was left unchanged."
    if settings["antinuke_mode"] == "contain":
        deleted = False
        try:
            me = guild.me
            if (
                isinstance(me, discord.Member)
                and not role.managed
                and _role_is_below(role, me.top_role)
            ):
                await role.delete(
                    reason=(
                        "Dank Shield AntiNuke rollback: dangerous role creation"
                    )
                )
                deleted = True
        except Exception:
            deleted = False

        actor_id = _safe_int(getattr(actor, "id", 0), 0)
        lock = _lock_for(_CONTAINMENT_LOCKS, (int(guild.id), actor_id))
        async with lock:
            removed, blocked = await _contain_actor(
                guild,
                actor,
                reason=(
                    "Dank Shield AntiNuke containment: dangerous role creation"
                ),
            )

        response = (
            "Deleted the newly created dangerous role."
            if deleted
            else (
                "Could not delete the newly created dangerous role because of "
                "Discord hierarchy/management."
            )
        )
        if removed:
            response += (
                " Removed dangerous actor roles: "
                + ", ".join(removed)
                + "."
            )
        if blocked:
            response += (
                " Could not remove actor roles: "
                + ", ".join(blocked)
                + "."
            )

    await _post_incident(
        guild,
        title="🚨 AntiNuke Dangerous Role Created",
        actor=actor,
        action_label="Dangerous role created",
        target_label=f"@{role.name} (`{role.id}`)",
        response_label=response,
    )


async def _handle_bot_add(member: discord.Member) -> None:
    if not bool(getattr(member, "bot", False)):
        return

    guild = member.guild
    settings = await get_antinuke_settings(int(guild.id))
    if not settings["antinuke_enabled"]:
        return

    entry = await _claim_recent_audit_entry(
        guild,
        "bot_add",
        target_id=int(member.id),
    )
    if entry is None:
        return

    actor = getattr(entry, "user", None)
    if _actor_is_owner_or_bot(guild, actor):
        return

    response = "Alert-only mode: newly added bot was left in the server."
    if settings["antinuke_mode"] == "contain":
        removed_bot = False
        try:
            await guild.kick(
                member,
                reason=(
                    "Dank Shield AntiNuke rollback: untrusted bot addition"
                ),
            )
            removed_bot = True
        except Exception:
            removed_bot = False

        actor_id = _safe_int(getattr(actor, "id", 0), 0)
        lock = _lock_for(_CONTAINMENT_LOCKS, (int(guild.id), actor_id))
        async with lock:
            removed, blocked = await _contain_actor(
                guild,
                actor,
                reason=(
                    "Dank Shield AntiNuke containment: untrusted bot addition"
                ),
            )

        response = (
            "Removed the newly added bot."
            if removed_bot
            else (
                "Could not remove the newly added bot. Check Kick Members and "
                "role hierarchy immediately."
            )
        )
        if removed:
            response += (
                " Removed dangerous inviter roles: "
                + ", ".join(removed)
                + "."
            )
        if blocked:
            response += (
                " Could not remove inviter roles: "
                + ", ".join(blocked)
                + "."
            )

    await _post_incident(
        guild,
        title="🚨 AntiNuke Untrusted Bot Added",
        actor=actor,
        action_label="Bot added to server",
        target_label=f"{member} (`{member.id}`)",
        response_label=response,
    )


async def _handle_webhook_change(
    channel: discord.abc.GuildChannel,
) -> None:
    guild = channel.guild
    settings = await get_antinuke_settings(int(guild.id))
    if not settings["antinuke_enabled"]:
        return

    action_name, entry = await _claim_recent_audit_entry_any(
        guild,
        ("webhook_create", "webhook_update", "webhook_delete"),
    )
    if entry is None or action_name is None:
        return

    labels = {
        "webhook_create": "Webhook creation",
        "webhook_update": "Webhook modification",
        "webhook_delete": "Webhook deletion",
    }

    await _process_claimed_destructive_event(
        guild,
        entry=entry,
        action_key=action_name,
        action_label=labels.get(action_name, "Webhook administrative change"),
        target_label=(
            f"#{getattr(channel, 'name', 'channel')} (`{channel.id}`)"
        ),
        threshold_key="antinuke_webhook_create_threshold",
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
        target_label=(
            f"#{getattr(channel, 'name', 'deleted-channel')} (`{channel.id}`)"
        ),
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
    handled = await _handle_threshold_event(
        member.guild,
        audit_action="kick",
        action_key="kick",
        action_label="Mass member kicks",
        target_id=int(member.id),
        target_label=f"{member} (`{member.id}`)",
        threshold_key="antinuke_kick_threshold",
    )
    if handled:
        return

    await _handle_threshold_event(
        member.guild,
        audit_action="member_prune",
        action_key="member_prune",
        action_label="Member prune triggered",
        target_id=None,
        target_label=(
            f"prune event observed while {member} (`{member.id}`) left"
        ),
        threshold_key="antinuke_kick_threshold",
        threshold_override=1,
    )


@bot.listen("on_webhooks_update")
async def antinuke_on_webhooks_update(
    channel: discord.abc.GuildChannel,
) -> None:
    await _handle_webhook_change(channel)


@bot.listen("on_guild_role_create")
async def antinuke_on_guild_role_create(role: discord.Role) -> None:
    await _handle_dangerous_role_create(role)


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


@bot.listen("on_member_join")
async def antinuke_on_member_join(member: discord.Member) -> None:
    await _handle_bot_add(member)


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
