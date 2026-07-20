from __future__ import annotations

"""Native per-guild AntiNuke protection for Dank Shield.

The engine intentionally handles only high-confidence destructive actions that can
be attributed through Discord's audit log. It does not guess an attacker when the
bot cannot prove who performed an action.

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
    "antinuke_channel_delete_threshold": 3,
    "antinuke_role_delete_threshold": 3,
    "antinuke_ban_threshold": 5,
    "antinuke_kick_threshold": 5,
    "antinuke_webhook_create_threshold": 3,
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

_ACTION_WINDOWS: dict[tuple[int, int, str], Deque[float]] = defaultdict(deque)
_TRIGGER_COOLDOWNS: dict[tuple[int, int, str], float] = {}
_SEEN_AUDIT_ENTRY_IDS: dict[int, float] = {}
_TRIGGER_COOLDOWN_SECONDS = 30.0
_AUDIT_ENTRY_MAX_AGE_SECONDS = 12.0
_AUDIT_DEDUPE_TTL_SECONDS = 60.0


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
            min(120, _safe_int(_cfg_value(cfg, "antinuke_window_seconds", defaults["antinuke_window_seconds"]), 15)),
        ),
        "antinuke_channel_delete_threshold": max(
            2,
            min(25, _safe_int(_cfg_value(cfg, "antinuke_channel_delete_threshold", defaults["antinuke_channel_delete_threshold"]), 3)),
        ),
        "antinuke_role_delete_threshold": max(
            2,
            min(25, _safe_int(_cfg_value(cfg, "antinuke_role_delete_threshold", defaults["antinuke_role_delete_threshold"]), 3)),
        ),
        "antinuke_ban_threshold": max(
            2,
            min(50, _safe_int(_cfg_value(cfg, "antinuke_ban_threshold", defaults["antinuke_ban_threshold"]), 5)),
        ),
        "antinuke_kick_threshold": max(
            2,
            min(50, _safe_int(_cfg_value(cfg, "antinuke_kick_threshold", defaults["antinuke_kick_threshold"]), 5)),
        ),
        "antinuke_webhook_create_threshold": max(
            2,
            min(25, _safe_int(_cfg_value(cfg, "antinuke_webhook_create_threshold", defaults["antinuke_webhook_create_threshold"]), 3)),
        ),
        "antinuke_protect_role_escalation": _safe_bool(
            _cfg_value(cfg, "antinuke_protect_role_escalation", defaults["antinuke_protect_role_escalation"]),
            True,
        ),
        "antinuke_trusted_user_ids": _safe_id_list(
            _cfg_value(cfg, "antinuke_trusted_user_ids", defaults["antinuke_trusted_user_ids"])
        ),
        "antinuke_trusted_role_ids": _safe_id_list(
            _cfg_value(cfg, "antinuke_trusted_role_ids", defaults["antinuke_trusted_role_ids"])
        ),
    }


async def get_antinuke_settings(guild_id: int) -> dict[str, Any]:
    cfg = await get_guild_config(int(guild_id), refresh=True)
    return normalize_antinuke_settings(cfg)


async def save_antinuke_settings(guild_id: int, patch: Mapping[str, Any]) -> dict[str, Any]:
    allowed = set(ANTINUKE_DEFAULTS)
    clean_patch = {str(key): value for key, value in dict(patch or {}).items() if str(key) in allowed}
    await upsert_guild_config(int(guild_id), clean_patch)
    return await get_antinuke_settings(int(guild_id))


def antinuke_permission_health(guild: discord.Guild, settings: Optional[Mapping[str, Any]] = None) -> list[str]:
    clean = normalize_antinuke_settings(settings or {})
    if not clean["antinuke_enabled"]:
        return []

    member = getattr(guild, "me", None)
    if not isinstance(member, discord.Member):
        return ["Resolve bot member"]

    missing: list[str] = []
    permissions = member.guild_permissions
    if not bool(getattr(permissions, "view_audit_log", False) or getattr(permissions, "administrator", False)):
        missing.append("View Audit Log")
    if clean["antinuke_mode"] == "contain" and not bool(
        getattr(permissions, "manage_roles", False) or getattr(permissions, "administrator", False)
    ):
        missing.append("Manage Roles")
    return missing


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


def _record_action(guild_id: int, actor_id: int, action_key: str, *, window_seconds: int) -> int:
    now = time.monotonic()
    key = (int(guild_id), int(actor_id), str(action_key))
    window = _ACTION_WINDOWS[key]
    cutoff = now - max(1, int(window_seconds))
    while window and window[0] < cutoff:
        window.popleft()
    window.append(now)
    return len(window)


def _trigger_ready(guild_id: int, actor_id: int, action_key: str) -> bool:
    now = time.monotonic()
    key = (int(guild_id), int(actor_id), str(action_key))
    last = _TRIGGER_COOLDOWNS.get(key, 0.0)
    if now - last < _TRIGGER_COOLDOWN_SECONDS:
        return False
    _TRIGGER_COOLDOWNS[key] = now
    return True


def _audit_entry_is_fresh(entry: Any, *, max_age_seconds: float = _AUDIT_ENTRY_MAX_AGE_SECONDS) -> bool:
    created_at = getattr(entry, "created_at", None)
    if not isinstance(created_at, datetime):
        return True
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - created_at.astimezone(timezone.utc)).total_seconds()
    return -2.0 <= age <= max(1.0, float(max_age_seconds))


def _consume_audit_entry(entry: Any) -> bool:
    entry_id = _safe_int(getattr(entry, "id", 0), 0)
    if entry_id <= 0:
        return False

    now = time.monotonic()
    stale = [key for key, seen_at in _SEEN_AUDIT_ENTRY_IDS.items() if now - seen_at > _AUDIT_DEDUPE_TTL_SECONDS]
    for key in stale[:200]:
        _SEEN_AUDIT_ENTRY_IDS.pop(key, None)

    if entry_id in _SEEN_AUDIT_ENTRY_IDS:
        return True
    _SEEN_AUDIT_ENTRY_IDS[entry_id] = now
    return False


def _audit_action(name: str) -> Any:
    return getattr(discord.AuditLogAction, str(name), None)


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

    for attempt in range(max(1, int(retries))):
        try:
            async for entry in guild.audit_logs(limit=10, action=action):
                if not _audit_entry_is_fresh(entry):
                    continue
                if target_id is not None:
                    target = getattr(entry, "target", None)
                    found_target_id = _safe_int(getattr(target, "id", 0), 0)
                    if found_target_id > 0 and found_target_id != int(target_id):
                        continue
                return entry
        except discord.Forbidden:
            return None
        except Exception:
            pass

        if attempt + 1 < max(1, int(retries)):
            await asyncio.sleep(0.6 * (attempt + 1))
    return None


def _manageable_dangerous_roles(guild: discord.Guild, actor: discord.Member) -> tuple[list[discord.Role], list[discord.Role]]:
    me = getattr(guild, "me", None)
    if not isinstance(me, discord.Member):
        return [], []

    removable: list[discord.Role] = []
    blocked: list[discord.Role] = []
    for role in list(getattr(actor, "roles", []) or []):
        try:
            if role.is_default() or role.managed or not role_has_dangerous_permissions(role):
                continue
            if role < me.top_role:
                removable.append(role)
            else:
                blocked.append(role)
        except Exception:
            continue
    return removable, blocked


async def _contain_actor(guild: discord.Guild, actor: Any, *, reason: str) -> tuple[list[str], list[str]]:
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
    embed = discord.Embed(title=title, color=discord.Color.red(), timestamp=datetime.now(timezone.utc))
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

    threshold = int(settings[threshold_key])
    count = _record_action(
        int(guild.id),
        actor_id,
        action_key,
        window_seconds=int(settings["antinuke_window_seconds"]),
    )
    if count < threshold or not _trigger_ready(int(guild.id), actor_id, action_key):
        return

    removed: list[str] = []
    blocked: list[str] = []
    if settings["antinuke_mode"] == "contain":
        removed, blocked = await _contain_actor(
            guild,
            actor,
            reason=f"Dank Shield AntiNuke containment: {action_label}",
        )

    if settings["antinuke_mode"] == "alert":
        response = "Alert-only mode: no roles were changed."
    elif removed:
        response = "Contained actor by removing dangerous roles: " + ", ".join(removed)
    elif blocked:
        response = "Containment triggered, but Dank Shield could not remove higher/managed dangerous roles: " + ", ".join(blocked)
    else:
        response = "Containment triggered, but no manageable dangerous roles were present on the actor."

    await _post_incident(
        guild,
        title="🚨 AntiNuke Triggered",
        actor=actor,
        action_label=action_label,
        target_label=target_label,
        response_label=response,
        count_label=f"{count} actions in {settings['antinuke_window_seconds']}s • trigger at {threshold}",
    )


async def _handle_role_permission_escalation(before: discord.Role, after: discord.Role) -> None:
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
        elif blocked:
            rollback += " Could not remove actor roles above/managed by Discord: " + ", ".join(blocked) + "."

    await _post_incident(
        guild,
        title="🚨 AntiNuke Permission Escalation",
        actor=actor,
        action_label="Dangerous permissions added to a role: " + ", ".join(added),
        target_label=f"@{after.name} (`{after.id}`)",
        response_label=rollback,
    )


async def _handle_member_dangerous_role_grant(before: discord.Member, after: discord.Member) -> None:
    before_ids = {int(role.id) for role in list(before.roles or [])}
    new_roles = [role for role in list(after.roles or []) if int(role.id) not in before_ids and role_has_dangerous_permissions(role)]
    if not new_roles:
        return

    guild = after.guild
    settings = await get_antinuke_settings(int(guild.id))
    if not settings["antinuke_enabled"] or not settings["antinuke_protect_role_escalation"]:
        return

    entry = await _find_recent_audit_entry(guild, "member_role_update", target_id=int(after.id))
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
                response = "Removed newly granted dangerous roles: " + ", ".join(role.name for role in removable) + "."
            except Exception as exc:
                response = f"Dangerous role rollback failed safely: {type(exc).__name__}."
        if blocked:
            response += " Could not remove higher/managed roles: " + ", ".join(role.name for role in blocked) + "."

        removed_actor, blocked_actor = await _contain_actor(
            guild,
            actor,
            reason="Dank Shield AntiNuke containment: dangerous role grant",
        )
        if removed_actor:
            response += " Removed dangerous actor roles: " + ", ".join(removed_actor) + "."
        elif blocked_actor:
            response += " Could not remove actor roles above/managed by Discord: " + ", ".join(blocked_actor) + "."

    await _post_incident(
        guild,
        title="🚨 AntiNuke Dangerous Role Grant",
        actor=actor,
        action_label="Dangerous role(s) granted to a member",
        target_label=f"{after.mention} (`{after.id}`) • " + ", ".join(role.name for role in new_roles),
        response_label=response,
    )


@bot.listen("on_guild_channel_delete")
async def antinuke_on_guild_channel_delete(channel: discord.abc.GuildChannel) -> None:
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
async def antinuke_on_member_ban(guild: discord.Guild, user: discord.User | discord.Member) -> None:
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
async def antinuke_on_webhooks_update(channel: discord.abc.GuildChannel) -> None:
    guild = channel.guild
    settings = await get_antinuke_settings(int(guild.id))
    if not settings["antinuke_enabled"]:
        return

    entry = await _find_recent_audit_entry(guild, "webhook_create", target_id=None, retries=2)
    if entry is None or _consume_audit_entry(entry):
        return
    actor = getattr(entry, "user", None)
    actor_id = _safe_int(getattr(actor, "id", 0), 0)
    if actor_id <= 0 or is_trusted_actor(guild, actor, settings):
        return

    threshold = int(settings["antinuke_webhook_create_threshold"])
    count = _record_action(
        int(guild.id),
        actor_id,
        "webhook_create",
        window_seconds=int(settings["antinuke_window_seconds"]),
    )
    if count < threshold or not _trigger_ready(int(guild.id), actor_id, "webhook_create"):
        return

    removed: list[str] = []
    blocked: list[str] = []
    if settings["antinuke_mode"] == "contain":
        removed, blocked = await _contain_actor(
            guild,
            actor,
            reason="Dank Shield AntiNuke containment: webhook creation flood",
        )

    if settings["antinuke_mode"] == "alert":
        response = "Alert-only mode: no roles were changed."
    elif removed:
        response = "Contained actor by removing dangerous roles: " + ", ".join(removed)
    elif blocked:
        response = "Could not remove higher/managed dangerous roles: " + ", ".join(blocked)
    else:
        response = "Containment triggered, but no manageable dangerous roles were present on the actor."

    await _post_incident(
        guild,
        title="🚨 AntiNuke Triggered",
        actor=actor,
        action_label="Webhook creation flood",
        target_label=f"#{getattr(channel, 'name', 'channel')} (`{channel.id}`)",
        response_label=response,
        count_label=f"{count} webhook creates in {settings['antinuke_window_seconds']}s • trigger at {threshold}",
    )


@bot.listen("on_guild_role_update")
async def antinuke_on_guild_role_update(before: discord.Role, after: discord.Role) -> None:
    await _handle_role_permission_escalation(before, after)


@bot.listen("on_member_update")
async def antinuke_on_member_update(before: discord.Member, after: discord.Member) -> None:
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
