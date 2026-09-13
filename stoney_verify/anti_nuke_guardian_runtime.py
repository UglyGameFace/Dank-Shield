from __future__ import annotations

"""Broad audit-surface guardian for the canonical AntiNuke engine.

This module owns no independent punishment policy. It classifies security-sensitive
Discord audit events, resolves sparse gateway attribution safely, and feeds those
events into :mod:`stoney_verify.anti_nuke`. A short guild-wide circuit breaker also
prevents several delegated operators from splitting one coordinated attack across
separate per-user thresholds.
"""

import asyncio
import time
from collections import defaultdict, deque
from typing import Any, Deque, Optional

import discord

from . import anti_nuke


# label, saved threshold key, canonical counter key, optional hard override
_ACTIONS: dict[str, tuple[str, str, str, Optional[int]]] = {
    "guild_update": ("Server identity/settings mutation", "antinuke_channel_delete_threshold", "channel_update", None),
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
    "invite_create": ("Invite creation", "antinuke_channel_delete_threshold", "channel_update", None),
    "invite_update": ("Invite mutation", "antinuke_channel_delete_threshold", "channel_update", None),
    "invite_delete": ("Invite deletion", "antinuke_channel_delete_threshold", "channel_delete", None),
    "webhook_create": ("Webhook creation", "antinuke_webhook_create_threshold", "webhook_create", None),
    "webhook_update": ("Webhook mutation", "antinuke_webhook_create_threshold", "webhook_update", None),
    "webhook_delete": ("Webhook deletion", "antinuke_webhook_create_threshold", "webhook_delete", None),
    "emoji_create": ("Emoji creation", "antinuke_channel_delete_threshold", "channel_update", None),
    "emoji_update": ("Emoji mutation", "antinuke_channel_delete_threshold", "channel_update", None),
    "emoji_delete": ("Emoji deletion", "antinuke_channel_delete_threshold", "channel_delete", None),
    "integration_delete": ("Integration deletion", "antinuke_role_delete_threshold", "role_update", None),
    "sticker_create": ("Sticker creation", "antinuke_channel_delete_threshold", "channel_update", None),
    "sticker_update": ("Sticker mutation", "antinuke_channel_delete_threshold", "channel_update", None),
    "sticker_delete": ("Sticker deletion", "antinuke_channel_delete_threshold", "channel_delete", None),
    "scheduled_event_create": ("Scheduled-event creation", "antinuke_channel_delete_threshold", "channel_update", None),
    "scheduled_event_update": ("Scheduled-event mutation", "antinuke_channel_delete_threshold", "channel_update", None),
    "scheduled_event_delete": ("Scheduled-event cancellation", "antinuke_channel_delete_threshold", "channel_delete", None),
    "thread_delete": ("Thread/forum-post deletion", "antinuke_channel_delete_threshold", "channel_delete", None),
    "app_command_permission_update": ("Application-command permission mutation", "antinuke_role_delete_threshold", "role_update", None),
    "soundboard_sound_delete": ("Soundboard deletion", "antinuke_channel_delete_threshold", "channel_delete", None),
    "automod_rule_create": ("Discord AutoMod rule creation", "antinuke_role_delete_threshold", "role_update", None),
    "automod_rule_update": ("Discord AutoMod rule mutation", "antinuke_role_delete_threshold", "role_update", None),
    "automod_rule_delete": ("Discord AutoMod rule deletion", "antinuke_role_delete_threshold", "role_delete", None),
    "onboarding_prompt_delete": ("Onboarding prompt deletion", "antinuke_channel_delete_threshold", "channel_delete", None),
    "onboarding_update": ("Server onboarding mutation", "antinuke_channel_delete_threshold", "channel_update", None),
    "home_settings_update": ("Server guide mutation", "antinuke_channel_delete_threshold", "channel_update", None),
}

_PANIC_ACTIONS = frozenset(
    {
        "guild_update", "channel_delete", "overwrite_create", "overwrite_update", "overwrite_delete",
        "role_update", "role_delete", "ban", "unban", "kick", "member_prune", "invite_delete",
        "webhook_update", "webhook_delete", "emoji_delete", "integration_delete", "sticker_delete",
        "scheduled_event_delete", "thread_delete", "app_command_permission_update",
        "soundboard_sound_delete", "automod_rule_update", "automod_rule_delete",
        "onboarding_prompt_delete", "onboarding_update", "home_settings_update",
    }
)
_PANIC_WINDOW_SECONDS = 10.0
_PANIC_EVENT_THRESHOLD = 3
_PANIC_ACTOR_THRESHOLD = 2
_PANIC_HOLD_SECONDS = 60.0
_PANIC_EVENTS: dict[int, Deque[tuple[float, int, Any]]] = defaultdict(deque)
_PANIC_UNTIL: dict[int, float] = {}
_INSTALL_FLAG = "_dank_antinuke_guardian_installed"


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


def _target_label(action_name: str, entry: Any) -> str:
    target = getattr(entry, "target", None)
    target_id = _safe_int(getattr(target, "id", 0), 0)
    name = str(getattr(target, "name", "") or getattr(target, "code", "") or target or "Unknown")
    if action_name == "guild_update":
        return "Server identity/settings"
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


def _panic_state(guild: discord.Guild, actor: Any, action_name: str) -> tuple[bool, bool, list[Any]]:
    guild_id = int(guild.id)
    now = time.monotonic()
    active = now < float(_PANIC_UNTIL.get(guild_id, 0.0) or 0.0)
    if action_name not in _PANIC_ACTIONS or anti_nuke._actor_is_owner_or_bot(guild, actor):  # noqa: SLF001
        return active, False, []

    actor_id = _safe_int(getattr(actor, "id", 0), 0)
    if actor_id <= 0:
        return active, False, []

    window = _PANIC_EVENTS[guild_id]
    cutoff = now - _PANIC_WINDOW_SECONDS
    while window and window[0][0] < cutoff:
        window.popleft()
    window.append((now, actor_id, actor))
    actors = {seen_id: seen_actor for _seen_at, seen_id, seen_actor in window}
    triggered = not active and len(window) >= _PANIC_EVENT_THRESHOLD and len(actors) >= _PANIC_ACTOR_THRESHOLD
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
        return await anti_nuke._contain_actor(  # noqa: SLF001
            guild,
            actor,
            reason="Dank Shield AntiNuke panic: coordinated destructive activity",
        )


async def _rest_reconcile(guild: discord.Guild, entry: Any, action_name: str, spec: tuple[str, str, str, Optional[int]]) -> None:
    """Do not lose sparse gateway evidence; let canonical REST attribution retry it."""

    label, threshold_key, counter_key, override = spec
    await asyncio.sleep(0.35)
    await anti_nuke._handle_threshold_event(  # noqa: SLF001
        guild,
        audit_action=action_name,
        action_key=counter_key,
        action_label=label,
        target_id=_target_id(entry),
        target_label=_target_label(action_name, entry),
        threshold_key=threshold_key,
        threshold_override=override,
    )


async def _process(guild: discord.Guild, entry: Any, actor: Any, action_name: str, spec: tuple[str, str, str, Optional[int]]) -> None:
    label, threshold_key, counter_key, override = spec
    panic_active, panic_triggered, observed = _panic_state(guild, actor, action_name)
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
    if not panic_triggered:
        return

    current_id = _safe_int(getattr(actor, "id", 0), 0)
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

    response = (
        f"Coordinated destructive burst detected across multiple executors. "
        f"Delegated allowances are suspended for {int(_PANIC_HOLD_SECONDS)}s."
    )
    if contained:
        response += " Peer actors contained: " + ", ".join(str(value) for value in contained) + "."
    if blocked:
        response += " Peer blockers: " + " | ".join(blocked[:5]) + "."
    await anti_nuke._post_incident(  # noqa: SLF001
        guild,
        title="🚨 AntiNuke Coordinated Panic",
        actor=actor,
        action_label=label,
        target_label=_target_label(action_name, entry),
        response_label=response,
        count_label=f"{int(_PANIC_WINDOW_SECONDS)}s guild window • {_PANIC_EVENT_THRESHOLD}+ actions • {_PANIC_ACTOR_THRESHOLD}+ actors",
        details="Guild-wide circuit breaker prevents several delegated operators from splitting one attack below separate per-user thresholds.",
    )


async def _on_audit_log_entry_create(entry: discord.AuditLogEntry) -> None:
    guild = getattr(entry, "guild", None)
    if guild is None:
        return

    action_name = _action_name(entry)
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
    if anti_nuke._consume_audit_entry(claimed):  # noqa: SLF001
        return
    await _process(guild, claimed, actor, action_name, spec)


def install_anti_nuke_guardian_runtime(bot: discord.Client) -> bool:
    if bool(getattr(bot, _INSTALL_FLAG, False)):
        return False
    bot.add_listener(_on_audit_log_entry_create, "on_audit_log_entry_create")
    setattr(bot, _INSTALL_FLAG, True)

    moderation = bool(getattr(getattr(bot, "intents", None), "moderation", False))
    if moderation:
        print("🛡️ AntiNuke guardian active: broad audit coverage and coordinated panic enabled")
    else:
        print("⚠️ AntiNuke guardian installed without moderation intent; native/REST coverage remains but broad gateway coverage may be unavailable")
    return True


__all__ = ["install_anti_nuke_guardian_runtime"]
