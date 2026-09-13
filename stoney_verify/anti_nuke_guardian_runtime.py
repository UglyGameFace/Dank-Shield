from __future__ import annotations

"""Broad audit-surface guardian for the canonical AntiNuke engine.

This module owns no independent punishment policy. It classifies high-confidence
security-sensitive Discord audit events, resolves sparse gateway attribution safely,
and feeds them into :mod:`stoney_verify.anti_nuke`. A guild-wide circuit breaker
also prevents several delegated operators from splitting one coordinated attack
across separate per-user thresholds.
"""

import asyncio
import time
from collections import defaultdict, deque
from typing import Any, Deque, Optional

import discord

from . import anti_nuke


_ACTIONS: dict[str, tuple[str, str, str, Optional[int]]] = {
    "guild_update": ("Server identity/security mutation", "antinuke_channel_delete_threshold", "channel_update", None),
    "channel_create": ("Channel creation", "antinuke_channel_delete_threshold", "channel_create", None),
    "channel_update": ("Channel settings mutation", "antinuke_channel_delete_threshold", "channel_update", None),
    "channel_delete": ("Channel deletion", "antinuke_channel_delete_threshold", "channel_delete", None),
    "overwrite_create": ("Channel overwrite creation", "antinuke_channel_delete_threshold", "channel_update", None),
    "overwrite_update": ("Channel overwrite mutation", "antinuke_channel_delete_threshold", "channel_update", None),
    "overwrite_delete": ("Channel overwrite deletion", "antinuke_channel_delete_threshold", "channel_update", None),
    "role_delete": ("Role deletion", "antinuke_role_delete_threshold", "role_delete", None),
    "ban": ("Member ban", "antinuke_ban_threshold", "ban", None),
    "unban": ("Ban-list removal", "antinuke_ban_threshold", "ban", None),
    "kick": ("Member kick", "antinuke_kick_threshold", "kick", None),
    "member_prune": ("Member prune", "antinuke_kick_threshold", "member_prune", 1),
    "invite_delete": ("Invite deletion", "antinuke_channel_delete_threshold", "channel_delete", None),
    "webhook_create": ("Webhook creation", "antinuke_webhook_create_threshold", "webhook_create", None),
    "webhook_update": ("Webhook mutation", "antinuke_webhook_create_threshold", "webhook_update", None),
    "webhook_delete": ("Webhook deletion", "antinuke_webhook_create_threshold", "webhook_delete", None),
    "emoji_delete": ("Emoji deletion", "antinuke_channel_delete_threshold", "channel_delete", None),
    "integration_delete": ("Integration deletion", "antinuke_role_delete_threshold", "role_update", None),
    "sticker_delete": ("Sticker deletion", "antinuke_channel_delete_threshold", "channel_delete", None),
    "scheduled_event_delete": ("Scheduled-event cancellation", "antinuke_channel_delete_threshold", "channel_delete", None),
    "thread_delete": ("Thread/forum-post deletion", "antinuke_channel_delete_threshold", "channel_delete", None),
    "app_command_permission_update": ("Application-command permission mutation", "antinuke_role_delete_threshold", "role_update", None),
    "soundboard_sound_delete": ("Soundboard deletion", "antinuke_channel_delete_threshold", "channel_delete", None),
    "automod_rule_create": ("Discord AutoMod rule creation", "antinuke_role_delete_threshold", "role_update", None),
    "automod_rule_update": ("Discord AutoMod rule mutation", "antinuke_role_delete_threshold", "role_update", None),
    "automod_rule_delete": ("Discord AutoMod rule deletion", "antinuke_role_delete_threshold", "role_delete", None),
}

_GUILD_UPDATE_SECURITY_FIELDS = frozenset({
    "name", "icon", "banner", "splash", "discovery_splash", "vanity_url_code",
    "description", "verification_level", "explicit_content_filter", "mfa_level", "owner",
})

_PANIC_WEIGHTS: dict[str, int] = {
    "guild_update": 4, "bot_add": 4, "channel_create": 2, "channel_delete": 3,
    "overwrite_create": 3, "overwrite_update": 3, "overwrite_delete": 3,
    "role_create": 2, "role_update": 3, "role_delete": 3, "ban": 1,
    "unban": 1, "kick": 1, "member_prune": 4, "invite_delete": 2,
    "webhook_create": 2, "webhook_update": 2, "webhook_delete": 3,
    "emoji_delete": 2, "integration_delete": 3, "sticker_delete": 2,
    "scheduled_event_delete": 2, "app_command_permission_update": 3,
    "automod_rule_create": 1, "automod_rule_update": 3, "automod_rule_delete": 4,
}
_PANIC_ACTIONS = frozenset(_PANIC_WEIGHTS)
_PANIC_MODERATION_ACTIONS = frozenset({"ban", "unban", "kick"})
_PANIC_SEVERE_ACTIONS = frozenset({
    "guild_update", "bot_add", "channel_delete", "overwrite_create", "overwrite_update",
    "overwrite_delete", "role_delete", "member_prune", "webhook_delete",
    "integration_delete", "app_command_permission_update", "automod_rule_delete",
})
_PANIC_WINDOW_SECONDS = 10.0
_PANIC_SCORE_THRESHOLD = 7
_PANIC_HIGH_RISK_MIN_EVENTS = 2
_PANIC_SEVERE_EVENT_THRESHOLD = 2
_PANIC_MODERATION_EVENT_THRESHOLD = 12
_PANIC_ACTOR_THRESHOLD = 2
_PANIC_HOLD_SECONDS = 60.0
_PANIC_EVENTS: dict[int, Deque[tuple[float, int, Any, str, int]]] = defaultdict(deque)
_PANIC_UNTIL: dict[int, float] = {}
_INSTALL_FLAG = "_dank_antinuke_guardian_installed"
_OVERWRITE_ACTIONS = ("overwrite_create", "overwrite_update", "overwrite_delete")
_AUTOMOD_ACTIONS = ("automod_rule_create", "automod_rule_update", "automod_rule_delete")
_CREATION_ROLLBACK_ACTIONS = ("channel_create", "webhook_create")
_CREATION_ROLLBACK_DONE: dict[int, float] = {}


class _EntryProxy:
    __slots__ = ("_entry", "user")

    def __init__(self, entry: Any, user: Any) -> None:
        self._entry = entry
        self.user = user

    def __getattr__(self, name: str) -> Any:
        return getattr(self._entry, name)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _action_name(entry: Any) -> str:
    action = getattr(entry, "action", None)
    name = getattr(action, "name", None)
    if name:
        return str(name).strip().lower()
    text = str(action or "").strip().lower()
    return text.rsplit(".", 1)[-1] if "." in text else text


def _target_id(entry: Any) -> Optional[int]:
    value = _safe_int(getattr(getattr(entry, "target", None), "id", 0), 0)
    return value if value > 0 else None


def _changed_diff_fields(entry: Any, fields: frozenset[str]) -> list[str]:
    before = getattr(entry, "before", None)
    after = getattr(entry, "after", None)
    if before is None or after is None:
        return []
    changed: list[str] = []
    for name in sorted(fields):
        old = getattr(before, name, None)
        new = getattr(after, name, None)
        if old != new and (old is not None or new is not None):
            changed.append(name)
    return changed


def _guild_update_security_fields(entry: Any) -> list[str]:
    return _changed_diff_fields(entry, _GUILD_UPDATE_SECURITY_FIELDS)


def _target_label(action_name: str, entry: Any) -> str:
    target = getattr(entry, "target", None)
    target_id = _safe_int(getattr(target, "id", 0), 0)
    name = str(getattr(target, "name", "") or getattr(target, "code", "") or target or "Unknown")
    if action_name == "guild_update":
        fields = _guild_update_security_fields(entry)
        return "Server settings • " + (", ".join(fields[:6]) if fields else "identity/security")
    if action_name.startswith("overwrite_") or action_name.startswith("channel_"):
        return f"#{name} (`{target_id}`)" if target_id else f"#{name}"
    if action_name.startswith("role_"):
        return f"@{name} (`{target_id}`)" if target_id else f"@{name}"
    if action_name == "member_prune":
        return "Member prune operation"
    if action_name in {"ban", "unban", "kick"}:
        return f"{name} (`{target_id}`)" if target_id else name
    return f"{name} (`{target_id}`)" if target_id else name


async def _resolve_actor(guild: discord.Guild, entry: Any) -> Optional[Any]:
    actor = getattr(entry, "user", None)
    actor_id = _safe_int(getattr(actor, "id", 0), 0) or _safe_int(getattr(entry, "user_id", 0), 0)
    if actor_id <= 0:
        return None
    getter = getattr(guild, "get_member", None)
    if callable(getter):
        try:
            member = getter(actor_id)
        except Exception:
            member = None
        if member is not None:
            return member
    if isinstance(actor, discord.Member):
        return actor
    fetcher = getattr(guild, "fetch_member", None)
    if callable(fetcher):
        try:
            member = await fetcher(actor_id)
        except Exception:
            member = None
        if member is not None:
            return member
    return actor if _safe_int(getattr(actor, "id", 0), 0) > 0 else None


async def _resolve_overwrite_channel(guild: discord.Guild, entry: Any) -> Optional[Any]:
    target = getattr(entry, "target", None)
    if callable(getattr(target, "set_permissions", None)):
        return target
    target_id = _target_id(entry)
    if target_id is None:
        return None
    getter = getattr(guild, "get_channel", None)
    if callable(getter):
        try:
            channel = getter(target_id)
        except Exception:
            channel = None
        if channel is not None:
            return channel
    fetcher = getattr(guild, "fetch_channel", None)
    if callable(fetcher):
        try:
            return await fetcher(target_id)
        except Exception:
            return None
    return None


async def _resolve_overwrite_principal(guild: discord.Guild, entry: Any) -> Optional[Any]:
    principal = getattr(entry, "extra", None)
    principal_id = _safe_int(getattr(principal, "id", 0), 0)
    if principal_id <= 0:
        return None
    if isinstance(principal, (discord.Role, discord.Member)):
        return principal
    role_getter = getattr(guild, "get_role", None)
    if callable(role_getter):
        try:
            role = role_getter(principal_id)
        except Exception:
            role = None
        if role is not None:
            return role
    member_getter = getattr(guild, "get_member", None)
    if callable(member_getter):
        try:
            member = member_getter(principal_id)
        except Exception:
            member = None
        if member is not None:
            return member
    fetcher = getattr(guild, "fetch_member", None)
    if callable(fetcher):
        try:
            return await fetcher(principal_id)
        except Exception:
            return None
    return None


async def _rollback_untrusted_overwrite(guild: discord.Guild, entry: Any, actor: Any, action_name: str) -> str:
    settings = await anti_nuke.get_antinuke_settings(int(guild.id))
    if not settings["antinuke_enabled"] or settings["antinuke_mode"] != "contain":
        return ""
    if anti_nuke._actor_is_owner_or_bot(guild, actor) or anti_nuke._actor_is_configured_trusted(actor, settings):  # noqa: SLF001
        return ""
    channel = await _resolve_overwrite_channel(guild, entry)
    principal = await _resolve_overwrite_principal(guild, entry)
    if channel is None or principal is None:
        return "overwrite rollback unavailable: target could not be resolved"
    try:
        if action_name == "overwrite_create":
            await channel.set_permissions(principal, overwrite=None, reason="Dank Shield AntiNuke rollback: unauthorized overwrite creation")
            return "removed unauthorized newly created overwrite"
        before = getattr(entry, "before", None)
        if before is None:
            return "overwrite rollback unavailable: audit before-state missing"
        current_overwrite = channel.overwrites_for(principal)
        current_allow, current_deny = current_overwrite.pair()
        restore_allow = getattr(before, "allow", current_allow)
        restore_deny = getattr(before, "deny", current_deny)
        if not isinstance(restore_allow, discord.Permissions):
            restore_allow = current_allow
        if not isinstance(restore_deny, discord.Permissions):
            restore_deny = current_deny
        restored = discord.PermissionOverwrite.from_pair(restore_allow, restore_deny)
        await channel.set_permissions(principal, overwrite=restored, reason="Dank Shield AntiNuke rollback: unauthorized overwrite mutation")
        return "restored overwrite to its audited previous state"
    except Exception as exc:
        return f"overwrite rollback failed safely: {type(exc).__name__}"


async def _resolve_automod_rule(guild: discord.Guild, entry: Any) -> Optional[Any]:
    target = getattr(entry, "target", None)
    if callable(getattr(target, "delete", None)) or callable(getattr(target, "edit", None)):
        return target
    target_id = _target_id(entry)
    if target_id is None:
        return None
    fetcher = getattr(guild, "fetch_automod_rule", None)
    if callable(fetcher):
        try:
            return await fetcher(target_id)
        except Exception:
            return None
    return None


async def _rollback_untrusted_automod(guild: discord.Guild, entry: Any, actor: Any, action_name: str) -> str:
    """Undo undelegated native Discord AutoMod mutations when audit state permits."""

    settings = await anti_nuke.get_antinuke_settings(int(guild.id))
    if not settings["antinuke_enabled"] or settings["antinuke_mode"] != "contain":
        return ""
    if anti_nuke._actor_is_owner_or_bot(guild, actor) or anti_nuke._actor_is_configured_trusted(actor, settings):  # noqa: SLF001
        return ""

    before = getattr(entry, "before", None)
    try:
        if action_name == "automod_rule_create":
            rule = await _resolve_automod_rule(guild, entry)
            if rule is None or not callable(getattr(rule, "delete", None)):
                return "AutoMod rollback unavailable: created rule could not be resolved"
            await rule.delete(reason="Dank Shield AntiNuke rollback: unauthorized AutoMod rule creation")
            return "deleted unauthorized newly created AutoMod rule"

        if action_name == "automod_rule_update":
            rule = await _resolve_automod_rule(guild, entry)
            if rule is None or not callable(getattr(rule, "edit", None)) or before is None:
                return "AutoMod rollback unavailable: rule or before-state missing"
            kwargs: dict[str, Any] = {}
            for name in ("name", "event_type", "actions", "trigger", "enabled", "exempt_roles", "exempt_channels"):
                if hasattr(before, name):
                    value = getattr(before, name)
                    if value is not None:
                        kwargs[name] = value
            if not kwargs:
                return "AutoMod rollback unavailable: no restorable fields in audit entry"
            kwargs["reason"] = "Dank Shield AntiNuke rollback: unauthorized AutoMod rule mutation"
            await rule.edit(**kwargs)
            return "restored unauthorized AutoMod mutation from audit before-state"

        if action_name == "automod_rule_delete":
            if before is None:
                return "AutoMod rollback unavailable: deleted rule before-state missing"
            required = {
                "name": getattr(before, "name", None),
                "event_type": getattr(before, "event_type", None),
                "trigger": getattr(before, "trigger", None),
                "actions": getattr(before, "actions", None),
            }
            if any(value is None for value in required.values()):
                return "AutoMod rollback unavailable: deleted rule definition incomplete"
            creator = getattr(guild, "create_automod_rule", None)
            if not callable(creator):
                return "AutoMod rollback unavailable: guild cannot recreate rules"
            kwargs = dict(required)
            kwargs["enabled"] = bool(getattr(before, "enabled", False))
            kwargs["exempt_roles"] = list(getattr(before, "exempt_roles", []) or [])
            kwargs["exempt_channels"] = list(getattr(before, "exempt_channels", []) or [])
            kwargs["reason"] = "Dank Shield AntiNuke rollback: unauthorized AutoMod rule deletion"
            await creator(**kwargs)
            return "recreated unauthorizedly deleted AutoMod rule from audit before-state"
    except Exception as exc:
        return f"AutoMod rollback failed safely: {type(exc).__name__}"
    return ""


def _prune_creation_rollback_done() -> None:
    now = time.monotonic()
    ttl = float(getattr(anti_nuke, "_AUDIT_DEDUPE_TTL_SECONDS", 90.0) or 90.0)
    for entry_id, seen_at in list(_CREATION_ROLLBACK_DONE.items()):
        if now - seen_at > ttl:
            _CREATION_ROLLBACK_DONE.pop(entry_id, None)


async def _resolve_created_channel(guild: discord.Guild, entry: Any) -> Optional[Any]:
    target = getattr(entry, "target", None)
    if callable(getattr(target, "delete", None)):
        return target
    target_id = _target_id(entry)
    if target_id is None:
        return None
    getter = getattr(guild, "get_channel", None)
    if callable(getter):
        try:
            channel = getter(target_id)
        except Exception:
            channel = None
        if channel is not None:
            return channel
    fetcher = getattr(guild, "fetch_channel", None)
    if callable(fetcher):
        try:
            return await fetcher(target_id)
        except Exception:
            return None
    return None


async def _resolve_created_webhook(guild: discord.Guild, entry: Any) -> Optional[Any]:
    target = getattr(entry, "target", None)
    if callable(getattr(target, "delete", None)):
        return target
    target_id = _target_id(entry)
    if target_id is None:
        return None
    loader = getattr(guild, "webhooks", None)
    if not callable(loader):
        return None
    try:
        webhooks = list(await loader())
    except Exception:
        return None
    for webhook in webhooks:
        if _safe_int(getattr(webhook, "id", 0), 0) == target_id:
            return webhook
    return None


async def _rollback_untrusted_creation(guild: discord.Guild, entry: Any, actor: Any, action_name: str) -> str:
    """Remove resources created by an undelegated destructive actor exactly once.

    This rollback intentionally has its own short-lived dedupe. Enforcement dedupe
    may already be claimed by the native REST fallback before the audit gateway event
    arrives; cleanup must still occur without punishing the same actor twice.
    """

    if action_name not in _CREATION_ROLLBACK_ACTIONS:
        return ""
    settings = await anti_nuke.get_antinuke_settings(int(guild.id))
    if not settings["antinuke_enabled"] or settings["antinuke_mode"] != "contain":
        return ""
    if anti_nuke._actor_is_owner_or_bot(guild, actor) or anti_nuke._actor_is_configured_trusted(actor, settings):  # noqa: SLF001
        return ""

    entry_id = _safe_int(getattr(entry, "id", 0), 0)
    lock_key = (int(guild.id), f"rollback-create:{entry_id or action_name}")
    lock = anti_nuke._lock_for(anti_nuke._AUDIT_CLAIM_LOCKS, lock_key)  # noqa: SLF001
    async with lock:
        _prune_creation_rollback_done()
        if entry_id > 0 and entry_id in _CREATION_ROLLBACK_DONE:
            return ""
        try:
            if action_name == "channel_create":
                channel = await _resolve_created_channel(guild, entry)
                if channel is None:
                    return "channel rollback unavailable: created channel could not be resolved"
                await channel.delete(reason="Dank Shield AntiNuke rollback: unauthorized channel creation")
                result = "deleted unauthorized newly created channel"
            else:
                webhook = await _resolve_created_webhook(guild, entry)
                if webhook is None:
                    return "webhook rollback unavailable: created webhook could not be resolved"
                await webhook.delete(reason="Dank Shield AntiNuke rollback: unauthorized webhook creation")
                result = "deleted unauthorized newly created webhook"
        except Exception as exc:
            return f"creation rollback failed safely: {type(exc).__name__}"
        if entry_id > 0:
            _CREATION_ROLLBACK_DONE[entry_id] = time.monotonic()
        return result


def _generic_role_update(entry: Any) -> bool:
    before = getattr(entry, "before", None)
    after = getattr(entry, "after", None)
    if before is None or after is None:
        return False
    if anti_nuke.dangerous_permissions_added(before, after):
        return False
    if anti_nuke.dangerous_permissions_changed(before, after):
        return True
    for attr in ("position", "name", "hoist", "mentionable", "colour", "color", "icon", "unicode_emoji"):
        old = getattr(before, attr, None)
        new = getattr(after, attr, None)
        if old != new and (old is not None or new is not None):
            return True
    return False


def _role_update_panic_weight(entry: Any) -> int:
    before = getattr(entry, "before", None)
    after = getattr(entry, "after", None)
    if before is None or after is None:
        return 1
    if anti_nuke.dangerous_permissions_changed(before, after):
        return 4
    old_position = getattr(before, "position", None)
    new_position = getattr(after, "position", None)
    if old_position != new_position and (old_position is not None or new_position is not None):
        return 4
    return 1


def _panic_weight(action_name: str, entry: Any = None) -> int:
    if action_name == "role_update" and entry is not None:
        return _role_update_panic_weight(entry)
    return max(0, int(_PANIC_WEIGHTS.get(str(action_name), 0)))


def _panic_state(guild: discord.Guild, actor: Any, action_name: str, *, entry: Any = None) -> tuple[bool, bool, list[Any]]:
    guild_id = int(guild.id)
    now = time.monotonic()
    active = now < float(_PANIC_UNTIL.get(guild_id, 0.0) or 0.0)
    if action_name not in _PANIC_ACTIONS or anti_nuke._actor_is_owner_or_bot(guild, actor):  # noqa: SLF001
        return active, False, []
    actor_id = _safe_int(getattr(actor, "id", 0), 0)
    if actor_id <= 0:
        return active, False, []
    weight = _panic_weight(action_name, entry)
    if weight <= 0:
        return active, False, []
    window = _PANIC_EVENTS[guild_id]
    cutoff = now - _PANIC_WINDOW_SECONDS
    while window and window[0][0] < cutoff:
        window.popleft()
    window.append((now, actor_id, actor, action_name, weight))
    actors = {seen_id: seen_actor for _seen_at, seen_id, seen_actor, _seen_action, _seen_weight in window}
    score = sum(seen_weight for *_prefix, seen_weight in window)
    moderation_events = sum(1 for _seen_at, _seen_id, _seen_actor, seen_action, _seen_weight in window if seen_action in _PANIC_MODERATION_ACTIONS)
    high_risk_events = sum(1 for _seen_at, _seen_id, _seen_actor, seen_action, seen_weight in window if seen_action not in _PANIC_MODERATION_ACTIONS and seen_weight >= 2)
    severe_events = [(seen_id, seen_action) for _seen_at, seen_id, _seen_actor, seen_action, _seen_weight in window if seen_action in _PANIC_SEVERE_ACTIONS]
    severe_actors = {seen_id for seen_id, _seen_action in severe_events}
    enough_actors = len(actors) >= _PANIC_ACTOR_THRESHOLD
    weighted_attack = score >= _PANIC_SCORE_THRESHOLD and high_risk_events >= _PANIC_HIGH_RISK_MIN_EVENTS
    severe_coordination = len(severe_events) >= _PANIC_SEVERE_EVENT_THRESHOLD and len(severe_actors) >= _PANIC_ACTOR_THRESHOLD
    moderation_flood = moderation_events >= _PANIC_MODERATION_EVENT_THRESHOLD
    triggered = not active and enough_actors and (severe_coordination or weighted_attack or moderation_flood)
    if triggered:
        _PANIC_UNTIL[guild_id] = now + _PANIC_HOLD_SECONDS
        active = True
    return active, triggered, list(actors.values())


def _clear_panic(guild_id: int) -> None:
    _PANIC_EVENTS.pop(int(guild_id), None)
    _PANIC_UNTIL.pop(int(guild_id), None)


async def _contain_peer(guild: discord.Guild, actor: Any) -> tuple[list[str], list[str]]:
    actor_id = _safe_int(getattr(actor, "id", 0), 0)
    lock = anti_nuke._lock_for(anti_nuke._CONTAINMENT_LOCKS, (int(guild.id), actor_id))  # noqa: SLF001
    async with lock:
        return await anti_nuke._contain_actor(guild, actor, reason="Dank Shield AntiNuke panic: coordinated destructive activity")  # noqa: SLF001


async def _contain_observed_peers(guild: discord.Guild, current_actor: Any, observed: list[Any]) -> tuple[list[int], list[str]]:
    current_id = _safe_int(getattr(current_actor, "id", 0), 0)
    contained: list[int] = []
    blocked: list[str] = []
    for peer in observed:
        peer_id = _safe_int(getattr(peer, "id", 0), 0)
        if peer_id <= 0 or peer_id == current_id or anti_nuke._actor_is_owner_or_bot(guild, peer):  # noqa: SLF001
            continue
        removed, failures = await _contain_peer(guild, peer)
        if removed and not failures:
            contained.append(peer_id)
        elif failures:
            blocked.append(f"{peer_id}: {', '.join(failures)}")
    return contained, blocked


async def _post_panic_incident(guild: discord.Guild, *, actor: Any, action_label: str, target_label: str, observed: list[Any]) -> None:
    contained, blocked = await _contain_observed_peers(guild, actor, observed)
    response = "Coordinated destructive burst detected across multiple executors. " + f"Delegated allowances are suspended for {int(_PANIC_HOLD_SECONDS)}s."
    if contained:
        response += " Peer actors contained: " + ", ".join(str(value) for value in contained) + "."
    if blocked:
        response += " Peer blockers: " + " | ".join(blocked[:5]) + "."
    await anti_nuke._post_incident(  # noqa: SLF001
        guild,
        title="🚨 AntiNuke Coordinated Panic",
        actor=actor,
        action_label=action_label,
        target_label=target_label,
        response_label=response,
        count_label=(f"{int(_PANIC_WINDOW_SECONDS)}s weighted guild window • " f"{_PANIC_SEVERE_EVENT_THRESHOLD}+ severe actions across " f"{_PANIC_ACTOR_THRESHOLD}+ actors, or score {_PANIC_SCORE_THRESHOLD}+ " f"with {_PANIC_HIGH_RISK_MIN_EVENTS}+ high-risk events, or " f"{_PANIC_MODERATION_EVENT_THRESHOLD}+ moderation actions"),
        details="Guild-wide circuit breaker immediately catches separate executors performing high-confidence structural/security destruction while keeping broader weighted thresholds for ordinary administrative work.",
    )


async def _claim_priority_entry(guild: discord.Guild, action_names: tuple[str, ...], *, target_id: Optional[int] = None, retries: int = 3) -> Optional[tuple[Any, Any]]:
    names = tuple(str(name) for name in action_names if str(name))
    if not names:
        return None
    key = (int(guild.id), "guardian:" + ",".join(sorted(names)))
    lock = anti_nuke._lock_for(anti_nuke._AUDIT_CLAIM_LOCKS, key)  # noqa: SLF001
    async with lock:
        attempts = max(1, int(retries))
        for attempt in range(1, attempts + 1):
            candidates: list[tuple[Any, Any]] = []
            for action_name in names:
                action = anti_nuke._audit_action(action_name)  # noqa: SLF001
                if action is None:
                    continue
                try:
                    async for entry in guild.audit_logs(limit=anti_nuke._AUDIT_SEARCH_LIMIT, action=action, _dank_priority=True):  # noqa: SLF001
                        if anti_nuke._audit_entry_seen(entry) or not anti_nuke._audit_entry_is_fresh(entry):  # noqa: SLF001
                            continue
                        if target_id is not None:
                            found_target_id = _safe_int(getattr(getattr(entry, "target", None), "id", 0), 0)
                            if found_target_id != int(target_id):
                                continue
                        actor = await _resolve_actor(guild, entry)
                        if actor is None:
                            continue
                        candidates.append((entry, actor))
                        break
                except discord.Forbidden as exc:
                    anti_nuke._log_audit_lookup_failure(guild, action_name, exc, attempt=attempt, retries=attempts)  # noqa: SLF001
                    return None
                except Exception as exc:
                    anti_nuke._log_audit_lookup_failure(guild, action_name, exc, attempt=attempt, retries=attempts)  # noqa: SLF001
            if candidates:
                entry, actor = max(candidates, key=lambda item: _safe_int(getattr(item[0], "id", 0), 0))
                if not anti_nuke._consume_audit_entry(entry):  # noqa: SLF001
                    return _EntryProxy(entry, actor), actor
            if attempt < attempts:
                await asyncio.sleep(min(1.0, 0.3 * attempt))
    return None


async def _rest_reconcile(guild: discord.Guild, entry: Any, action_name: str, spec: tuple[str, str, str, Optional[int]]) -> None:
    await asyncio.sleep(0.35)
    claimed = await _claim_priority_entry(guild, (action_name,), target_id=_target_id(entry))
    if claimed is None:
        return
    found_entry, actor = claimed
    if action_name in _CREATION_ROLLBACK_ACTIONS:
        await _rollback_untrusted_creation(guild, found_entry, actor, action_name)
    if action_name in _OVERWRITE_ACTIONS:
        await _rollback_untrusted_overwrite(guild, found_entry, actor, action_name)
    if action_name in _AUTOMOD_ACTIONS:
        await _rollback_untrusted_automod(guild, found_entry, actor, action_name)
    await _process(guild, found_entry, actor, action_name, spec)


async def _process(guild: discord.Guild, entry: Any, actor: Any, action_name: str, spec: tuple[str, str, str, Optional[int]]) -> None:
    label, threshold_key, counter_key, override = spec
    panic_active, panic_triggered, observed = _panic_state(guild, actor, action_name, entry=entry)
    handled = await anti_nuke._process_claimed_destructive_event(  # noqa: SLF001
        guild,
        entry=entry,
        action_key=counter_key,
        action_label=label,
        target_label=_target_label(action_name, entry),
        threshold_key=threshold_key,
        threshold_override=1 if panic_active else override,
    )
    if not handled:
        _clear_panic(int(guild.id))
        return
    if panic_triggered:
        await _post_panic_incident(guild, actor=actor, action_label=label, target_label=_target_label(action_name, entry), observed=observed)


async def _handle_bot_add(guild: discord.Guild, entry: Any, actor: Any) -> None:
    settings = await anti_nuke.get_antinuke_settings(int(guild.id))
    if not settings["antinuke_enabled"] or anti_nuke._actor_is_owner_or_bot(guild, actor):  # noqa: SLF001
        return
    target = getattr(entry, "target", None)
    panic_active, panic_triggered, observed = _panic_state(guild, actor, "bot_add", entry=entry)
    response = "Alert-only mode: newly added bot was left in the server."
    if settings["antinuke_mode"] == "contain":
        removed_bot = False
        try:
            if target is not None:
                await guild.kick(target, reason="Dank Shield AntiNuke rollback: untrusted bot addition")
                removed_bot = True
        except Exception:
            removed_bot = False
        removed_actor, blocked_actor = await _contain_peer(guild, actor)
        response = "Removed the newly added bot." if removed_bot else "Could not remove the newly added bot."
        if removed_actor:
            response += " Inviter containment: " + ", ".join(removed_actor) + "."
        if blocked_actor:
            response += " Inviter blockers: " + ", ".join(blocked_actor) + "."
    await anti_nuke._post_incident(guild, title="🚨 AntiNuke Untrusted Bot Added", actor=actor, action_label="Bot added to server", target_label=_target_label("bot_add", entry), response_label=response, details="Gateway-fast rollback; REST propagation was not required.")  # noqa: SLF001
    if panic_active and panic_triggered:
        await _post_panic_incident(guild, actor=actor, action_label="Bot added to server", target_label=_target_label("bot_add", entry), observed=observed)


async def _on_guild_channel_update_fallback(before: discord.abc.GuildChannel, after: discord.abc.GuildChannel) -> None:
    if getattr(before, "overwrites", None) == getattr(after, "overwrites", None):
        return
    guild = after.guild
    settings = await anti_nuke.get_antinuke_settings(int(guild.id))
    if not settings["antinuke_enabled"]:
        return
    await asyncio.sleep(0.2)
    claimed = await _claim_priority_entry(guild, _OVERWRITE_ACTIONS, target_id=int(after.id), retries=2)
    if claimed is None:
        return
    entry, actor = claimed
    action_name = _action_name(entry)
    spec = _ACTIONS.get(action_name)
    if spec is None:
        return
    await _rollback_untrusted_overwrite(guild, entry, actor, action_name)
    await _process(guild, entry, actor, action_name, spec)


async def _on_audit_log_entry_create(entry: discord.AuditLogEntry) -> None:
    guild = getattr(entry, "guild", None)
    if guild is None:
        return
    action_name = _action_name(entry)
    if action_name == "bot_add":
        actor = await _resolve_actor(guild, entry)
        if actor is None:
            return
        claimed = _EntryProxy(entry, actor)
        if anti_nuke._consume_audit_entry(claimed):  # noqa: SLF001
            return
        await _handle_bot_add(guild, claimed, actor)
        return
    if action_name == "guild_update" and not _guild_update_security_fields(entry):
        return
    if action_name == "role_update":
        if not _generic_role_update(entry):
            return
        spec = ("Role hierarchy/settings mutation", "antinuke_role_delete_threshold", "role_update", None)
    else:
        spec = _ACTIONS.get(action_name)
    if spec is None:
        return
    actor = await _resolve_actor(guild, entry)
    if actor is None:
        await _rest_reconcile(guild, entry, action_name, spec)
        return
    claimed = _EntryProxy(entry, actor)
    if action_name in _CREATION_ROLLBACK_ACTIONS:
        await _rollback_untrusted_creation(guild, claimed, actor, action_name)
    if anti_nuke._consume_audit_entry(claimed):  # noqa: SLF001
        return
    if action_name in _OVERWRITE_ACTIONS:
        await _rollback_untrusted_overwrite(guild, claimed, actor, action_name)
    if action_name in _AUTOMOD_ACTIONS:
        await _rollback_untrusted_automod(guild, claimed, actor, action_name)
    await _process(guild, claimed, actor, action_name, spec)


def install_anti_nuke_guardian_runtime(bot: discord.Client) -> bool:
    if bool(getattr(bot, _INSTALL_FLAG, False)):
        return False
    bot.add_listener(_on_audit_log_entry_create, "on_audit_log_entry_create")
    bot.add_listener(_on_guild_channel_update_fallback, "on_guild_channel_update")
    setattr(bot, _INSTALL_FLAG, True)
    moderation = bool(getattr(getattr(bot, "intents", None), "moderation", False))
    if moderation:
        print("🛡️ AntiNuke guardian active: broad audit coverage, overwrite/AutoMod/creation rollback, high-impact guild filtering, and weighted coordinated panic enabled")
    else:
        print("⚠️ AntiNuke guardian installed without moderation intent; native/REST coverage remains but broad gateway coverage may be unavailable")
    return True


__all__ = ["install_anti_nuke_guardian_runtime"]