from __future__ import annotations

"""Final AntiNuke lockdown invariants.

This runtime closes control-plane and audit-surface gaps that are dangerous only
when the pieces are considered together.  It deliberately runs after the
hostile-identity runtime so durable reputation remains authoritative.

Discord still has one physical boundary this code cannot remove: the guild owner
cannot be kicked, banned, or role-stripped by a bot.  Owner-originated destructive
activity is therefore made first-strike observable, while every delegated actor
loses destructive trust grace in contain mode.
"""

from typing import Any, Iterable, Mapping, Optional

import discord

_INSTALL_FLAG = "_dank_antinuke_lockdown_runtime_installed"
_POLICY_PATCH_FLAG = "_dank_antinuke_lockdown_policy_patched"
_HISTORY_PATCH_FLAG = "_dank_antinuke_lockdown_history_patched"
_GUARDIAN_PATCH_FLAG = "_dank_antinuke_lockdown_guardian_patched"
_BOT_ADD_PATCH_FLAG = "_dank_antinuke_lockdown_bot_add_patched"
_OWNER_PATCH_FLAG = "_dank_antinuke_lockdown_owner_first_strike_patched"

_PROTECTED_SECURITY_PREFIXES = ("antinuke_", "anti_nuke_")
_PROTECTED_SECURITY_KEYS = frozenset(
    {
        "server_control_role_id",
        "server_control_role_ids",
        "control_role_id",
        "control_role_ids",
        "perm_role_id",
        "perm_role_ids",
        "top_level_role_id",
        "bot_admin_role_id",
        "bot_admin_role_ids",
        "bot_owner_role_id",
        "owner_role_id",
        "admin_role_id",
    }
)
_CONTROL_ROLE_KEYS = (
    "server_control_role_id",
    "control_role_id",
    "perm_role_id",
    "top_level_role_id",
    "bot_admin_role_id",
    "bot_owner_role_id",
    "owner_role_id",
    "admin_role_id",
)
_CONTROL_ROLE_LIST_KEYS = (
    "server_control_role_ids",
    "control_role_ids",
    "perm_role_ids",
    "bot_admin_role_ids",
)
_CONTAINER_KEYS = ("settings", "config", "metadata", "meta")

_EXTRA_DANGEROUS_PERMISSIONS = (
    "manage_messages",
    "manage_threads",
    "manage_events",
    "manage_expressions",
    "manage_emojis_and_stickers",
    "move_members",
    "mute_members",
    "deafen_members",
)

# action -> (label, configured threshold key, aggregate counter key, override)
_EXTRA_AUDIT_ACTIONS: dict[str, tuple[str, str, str, Optional[int]]] = {
    "message_delete": (
        "Message deletion",
        "antinuke_channel_delete_threshold",
        "message_delete",
        None,
    ),
    "message_bulk_delete": (
        "Bulk message deletion",
        "antinuke_channel_delete_threshold",
        "message_delete",
        1,
    ),
    "integration_create": (
        "Integration creation",
        "antinuke_role_delete_threshold",
        "integration_update",
        None,
    ),
    "integration_update": (
        "Integration mutation",
        "antinuke_role_delete_threshold",
        "integration_update",
        None,
    ),
    "invite_create": (
        "Invite creation",
        "antinuke_channel_delete_threshold",
        "invite_create",
        None,
    ),
    "emoji_update": (
        "Emoji mutation",
        "antinuke_channel_delete_threshold",
        "expression_update",
        None,
    ),
    "sticker_update": (
        "Sticker mutation",
        "antinuke_channel_delete_threshold",
        "expression_update",
        None,
    ),
    "scheduled_event_update": (
        "Scheduled-event mutation",
        "antinuke_channel_delete_threshold",
        "event_update",
        None,
    ),
    "thread_update": (
        "Thread/forum-post mutation",
        "antinuke_channel_delete_threshold",
        "thread_update",
        None,
    ),
    "stage_instance_delete": (
        "Stage instance deletion",
        "antinuke_channel_delete_threshold",
        "channel_delete",
        1,
    ),
    "member_move": (
        "Member voice move",
        "antinuke_kick_threshold",
        "member_move",
        None,
    ),
    "member_disconnect": (
        "Member voice disconnect",
        "antinuke_kick_threshold",
        "member_move",
        None,
    ),
}

_EXTRA_PANIC_WEIGHTS: dict[str, int] = {
    "message_delete": 2,
    "message_bulk_delete": 4,
    "integration_create": 3,
    "integration_update": 3,
    "invite_create": 1,
    "emoji_update": 1,
    "sticker_update": 1,
    "scheduled_event_update": 2,
    "thread_update": 2,
    "stage_instance_delete": 2,
    "member_move": 1,
    "member_disconnect": 1,
}
_EXTRA_SEVERE_ACTIONS = frozenset(
    {
        "message_bulk_delete",
        "integration_create",
        "integration_update",
        "stage_instance_delete",
    }
)
_EXTRA_SLOW_BURN_COUNTERS = frozenset(
    {
        spec[2]
        for spec in _EXTRA_AUDIT_ACTIONS.values()
        if spec[2]
    }
)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _safe_id_list(value: Any, *, limit: int = 200) -> list[int]:
    if isinstance(value, (list, tuple, set, frozenset)):
        source: Iterable[Any] = value
    elif value is None:
        source = ()
    else:
        source = (value,)

    out: list[int] = []
    seen: set[int] = set()
    for raw in source:
        if isinstance(raw, str) and ("," in raw or ";" in raw):
            parts: Iterable[Any] = raw.replace(";", ",").split(",")
        else:
            parts = (raw,)
        for part in parts:
            item = _safe_int(part, 0)
            if item <= 0 or item in seen:
                continue
            seen.add(item)
            out.append(item)
            if len(out) >= limit:
                return out
    return out


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    for container in _CONTAINER_KEYS:
        try:
            nested = getattr(cfg, container, None)
            if isinstance(nested, Mapping) and nested.get(key) is not None:
                return nested.get(key)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(container)
                if isinstance(nested, Mapping) and nested.get(key) is not None:
                    return nested.get(key)
        except Exception:
            pass
    return default


def _control_role_ids(cfg: Any) -> set[int]:
    out: set[int] = set()
    for key in _CONTROL_ROLE_KEYS:
        value = _safe_int(_cfg_value(cfg, key, 0), 0)
        if value > 0:
            out.add(value)
    for key in _CONTROL_ROLE_LIST_KEYS:
        out.update(_safe_id_list(_cfg_value(cfg, key, None)))
    return {value for value in out if value > 0}


def _is_protected_restore_key(key: Any) -> bool:
    name = str(key or "").strip().lower()
    if not name:
        return False
    if name in _PROTECTED_SECURITY_KEYS:
        return True
    return any(name.startswith(prefix) for prefix in _PROTECTED_SECURITY_PREFIXES)


def _sanitize_security_snapshot(
    snapshot: Mapping[str, Any],
    current: Mapping[str, Any],
) -> dict[str, Any]:
    """Preserve live security-root values during generic history restore."""

    safe = dict(snapshot or {})
    live = dict(current or {})

    for key in list(safe):
        if key in _CONTAINER_KEYS or not _is_protected_restore_key(key):
            continue
        if key in live:
            safe[key] = live[key]
        else:
            safe.pop(key, None)

    for container in _CONTAINER_KEYS:
        saved_nested_raw = safe.get(container)
        live_nested_raw = live.get(container)
        if not isinstance(saved_nested_raw, Mapping) and not isinstance(
            live_nested_raw, Mapping
        ):
            continue

        saved_nested = (
            dict(saved_nested_raw) if isinstance(saved_nested_raw, Mapping) else {}
        )
        live_nested = (
            dict(live_nested_raw) if isinstance(live_nested_raw, Mapping) else {}
        )
        security_keys = {
            str(key)
            for key in set(saved_nested) | set(live_nested)
            if _is_protected_restore_key(key)
        }
        for key in security_keys:
            if key in live_nested:
                saved_nested[key] = live_nested[key]
            else:
                saved_nested.pop(key, None)
        safe[container] = saved_nested

    return safe


def _filter_restore_items(values: Iterable[str]) -> tuple[list[str], list[str]]:
    allowed: list[str] = []
    blocked: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if not item:
            continue
        if _is_protected_restore_key(item):
            blocked.append(item)
        else:
            allowed.append(item)
    return allowed, blocked


def _enabled_contain(settings: Mapping[str, Any] | None) -> bool:
    if not isinstance(settings, Mapping):
        return False
    enabled = settings.get("antinuke_enabled", False)
    if not isinstance(enabled, bool):
        enabled = str(enabled or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
            "enabled",
        }
    mode = str(settings.get("antinuke_mode") or "contain").strip().lower()
    return bool(enabled) and mode == "contain"


def _patch_config_history_restore() -> bool:
    from . import config_history as history
    from . import config_history_selective as selective

    if bool(getattr(history, _HISTORY_PATCH_FLAG, False)):
        return False

    original_full = history._restore_core_config_version_sync  # noqa: SLF001
    original_plan = selective.plan_selective_restore_sync
    original_selected = selective._restore_core_selected_sync  # noqa: SLF001

    def guarded_full(
        guild_id: int,
        version_id: int,
        version: Mapping[str, Any],
        *,
        table_name: str,
        current: Mapping[str, Any],
        actor_id: Optional[int],
        reason: str,
    ) -> dict[str, Any]:
        safe_version = dict(version or {})
        raw_snapshot = history._row_dict(safe_version.get("snapshot"))  # noqa: SLF001
        safe_version["snapshot"] = _sanitize_security_snapshot(
            raw_snapshot,
            current,
        )
        result = original_full(
            guild_id,
            version_id,
            safe_version,
            table_name=table_name,
            current=current,
            actor_id=actor_id,
            reason=reason,
        )
        result["protected_security_values_preserved"] = True
        return result

    def guarded_plan(guild_id: int, version_id: int) -> dict[str, Any]:
        plan = dict(original_plan(guild_id, version_id))
        if plan.get("domain") != selective.CORE_DOMAIN:
            return plan

        changed, blocked_changed = _filter_restore_items(
            list(plan.get("changed_items") or [])
        )
        missing, blocked_missing = _filter_restore_items(
            list(plan.get("missing_items") or [])
        )
        labels = dict(plan.get("item_labels") or {})
        for key in set(blocked_changed) | set(blocked_missing):
            labels.pop(key, None)
        sections: dict[str, list[str]] = {}
        for section, keys in dict(plan.get("core_sections") or {}).items():
            allowed, _blocked = _filter_restore_items(list(keys or []))
            if allowed:
                sections[str(section)] = allowed

        plan["changed_items"] = changed
        plan["missing_items"] = missing
        plan["item_labels"] = labels
        plan["core_sections"] = sections
        plan["protected_security_items"] = sorted(
            set(blocked_changed) | set(blocked_missing)
        )
        return plan

    def guarded_selected(
        guild_id: int,
        version_id: int,
        *,
        version: Mapping[str, Any],
        table_name: str,
        current: Mapping[str, Any],
        selected: list[str],
        actor_id: Optional[int],
        reason: str,
        mode: str,
    ) -> dict[str, Any]:
        allowed, blocked = _filter_restore_items(selected)
        if blocked and not allowed:
            raise ValueError(
                "AntiNuke and server-control security settings cannot be restored "
                "through generic Configuration History. Use the owner-only security "
                "controls instead."
            )
        result = original_selected(
            guild_id,
            version_id,
            version=version,
            table_name=table_name,
            current=current,
            selected=allowed,
            actor_id=actor_id,
            reason=reason,
            mode=mode,
        )
        if blocked:
            result["blocked_security_items"] = sorted(set(blocked))
        return result

    history._restore_core_config_version_sync = guarded_full  # noqa: SLF001
    selective.plan_selective_restore_sync = guarded_plan
    selective._restore_core_selected_sync = guarded_selected  # noqa: SLF001
    setattr(history, _HISTORY_PATCH_FLAG, True)
    setattr(selective, _HISTORY_PATCH_FLAG, True)
    return True


def _patch_anti_nuke_policy(anti_nuke: Any, bot: discord.Client) -> bool:
    from . import guild_config

    if bool(getattr(anti_nuke, _POLICY_PATCH_FLAG, False)):
        return False

    original_trust = anti_nuke._actor_is_configured_trusted  # noqa: SLF001
    original_get = anti_nuke.get_antinuke_settings
    original_health = anti_nuke.antinuke_permission_health

    def strict_trust(
        actor: Any,
        settings: Mapping[str, Any],
        *,
        ignore_role_ids: Optional[set[int]] = None,
    ) -> bool:
        if _enabled_contain(settings):
            return False
        return bool(
            original_trust(
                actor,
                settings,
                ignore_role_ids=ignore_role_ids,
            )
        )

    async def protected_get(
        guild_id: int,
        *,
        refresh: bool = False,
    ) -> dict[str, Any]:
        gid = int(guild_id)
        try:
            settings = dict(await original_get(gid, refresh=bool(refresh)))
        except TypeError:
            settings = dict(await original_get(gid))

        try:
            cfg = await guild_config.get_guild_config(gid, refresh=bool(refresh))
            protected_roles = _control_role_ids(cfg)
        except Exception:
            protected_roles = set()

        if protected_roles:
            existing = _safe_id_list(settings.get("antinuke_trusted_role_ids"))
            settings["antinuke_trusted_role_ids"] = list(
                dict.fromkeys([*existing, *sorted(protected_roles)])
            )
        return settings

    def lockdown_health(
        guild: discord.Guild,
        settings: Optional[Mapping[str, Any]] = None,
    ) -> list[str]:
        missing = list(original_health(guild, settings))
        clean = anti_nuke.normalize_antinuke_settings(settings or {})
        if _enabled_contain(clean):
            moderation = bool(
                getattr(getattr(bot, "intents", None), "moderation", False)
            )
            if not moderation and "Moderation gateway intent" not in missing:
                missing.append("Moderation gateway intent")
        return missing

    anti_nuke.DANGEROUS_PERMISSION_NAMES = tuple(
        dict.fromkeys(
            [
                *tuple(anti_nuke.DANGEROUS_PERMISSION_NAMES),
                *_EXTRA_DANGEROUS_PERMISSIONS,
            ]
        )
    )
    anti_nuke._SLOW_BURN_ACTIONS = frozenset(  # noqa: SLF001
        set(anti_nuke._SLOW_BURN_ACTIONS) | set(_EXTRA_SLOW_BURN_COUNTERS)  # noqa: SLF001
    )
    anti_nuke._actor_is_configured_trusted = strict_trust  # noqa: SLF001
    anti_nuke.get_antinuke_settings = protected_get
    anti_nuke.antinuke_permission_health = lockdown_health
    setattr(anti_nuke, _POLICY_PATCH_FLAG, True)
    return True


def _patch_guardian_surface(guardian: Any) -> bool:
    if bool(getattr(guardian, _GUARDIAN_PATCH_FLAG, False)):
        return False

    guardian._ACTIONS.update(_EXTRA_AUDIT_ACTIONS)  # noqa: SLF001
    guardian._PANIC_WEIGHTS.update(_EXTRA_PANIC_WEIGHTS)  # noqa: SLF001
    guardian._PANIC_ACTIONS = frozenset(guardian._PANIC_WEIGHTS)  # noqa: SLF001
    guardian._PANIC_SEVERE_ACTIONS = frozenset(  # noqa: SLF001
        set(guardian._PANIC_SEVERE_ACTIONS) | set(_EXTRA_SEVERE_ACTIONS)  # noqa: SLF001
    )
    setattr(guardian, _GUARDIAN_PATCH_FLAG, True)
    return True


def _patch_owner_first_strike(incident: Any) -> bool:
    if bool(getattr(incident, _OWNER_PATCH_FLAG, False)):
        return False

    original = incident._process_owner_destructive_event  # noqa: SLF001

    async def first_strike(
        guild: discord.Guild,
        *,
        entry: Any,
        action_key: str,
        action_label: str,
        target_label: str,
        threshold_key: str,
        threshold_override: Optional[int] = None,
    ) -> bool:
        _ = threshold_override
        return await original(
            guild,
            entry=entry,
            action_key=action_key,
            action_label=action_label,
            target_label=target_label,
            threshold_key=threshold_key,
            threshold_override=1,
        )

    incident._process_owner_destructive_event = first_strike  # noqa: SLF001
    setattr(incident, _OWNER_PATCH_FLAG, True)
    return True


def _patch_bot_add_guardian(
    guardian: Any,
    anti_nuke: Any,
    hostile: Any,
) -> bool:
    if bool(getattr(guardian, _BOT_ADD_PATCH_FLAG, False)):
        return False

    original = guardian._handle_bot_add  # noqa: SLF001

    async def strict_bot_add(guild: discord.Guild, entry: Any, actor: Any) -> None:
        target = getattr(entry, "target", None)
        target_id = _safe_int(getattr(target, "id", 0), 0)
        if target_id <= 0:
            return await original(guild, entry, actor)

        settings = await anti_nuke.get_antinuke_settings(int(guild.id))
        if not bool(settings.get("antinuke_enabled")):
            return await original(guild, entry, actor)

        # Durable hostile reputation outranks every allowlist.  A previously
        # confirmed destructive bot may never become safe merely because its ID
        # was later added to trusted users.
        try:
            reputation = await hostile.get_actor_reputation(
                int(guild.id),
                target_id,
                refresh=True,
            )
        except Exception:
            reputation = None
        if reputation and reputation.get("active"):
            return await original(guild, entry, actor)

        trusted_targets = set(
            _safe_id_list(settings.get("antinuke_trusted_user_ids"))
        )
        if target_id in trusted_targets:
            return

        actor_id = _safe_int(getattr(actor, "id", 0), 0)
        owner_id = _safe_int(getattr(guild, "owner_id", 0), 0)
        if actor_id <= 0 or actor_id != owner_id:
            return await original(guild, entry, actor)

        response = "Owner added an unapproved bot."
        if str(settings.get("antinuke_mode") or "contain").lower() == "contain":
            removal_target = target or discord.Object(id=target_id)
            try:
                await guild.kick(
                    removal_target,
                    reason=(
                        "Dank Shield AntiNuke lockdown: owner-added bot was not "
                        "pre-approved"
                    ),
                )
                response = "Removed owner-added bot because its ID was not pre-approved."
            except Exception as exc:
                response = (
                    "Could not remove owner-added unapproved bot: "
                    f"{type(exc).__name__}. Check hierarchy and Kick Members immediately."
                )
        else:
            response += " Alert-only mode did not remove it."

        await anti_nuke._post_incident(  # noqa: SLF001
            guild,
            title="🚨 AntiNuke Unapproved Bot Added By Owner",
            actor=actor,
            action_label="Unapproved bot added to server",
            target_label=f"{target or 'bot'} (`{target_id}`)",
            response_label=response,
            details=(
                "Lockdown requires bot IDs to be explicitly trusted before they are "
                "added. The physical guild owner cannot be contained by a Discord bot."
            ),
        )

    guardian._handle_bot_add = strict_bot_add  # noqa: SLF001
    setattr(guardian, _BOT_ADD_PATCH_FLAG, True)
    return True


def install_anti_nuke_lockdown_runtime(bot: discord.Client) -> bool:
    """Install AntiNuke's final no-grace security invariants once."""

    if bool(getattr(bot, _INSTALL_FLAG, False)):
        return False

    from . import anti_nuke
    from . import anti_nuke_guardian_runtime as guardian
    from . import anti_nuke_hostile_actor_runtime as hostile
    from . import anti_nuke_incident_runtime as incident

    history_patched = _patch_config_history_restore()
    policy_patched = _patch_anti_nuke_policy(anti_nuke, bot)
    guardian_patched = _patch_guardian_surface(guardian)
    owner_patched = _patch_owner_first_strike(incident)
    bot_add_patched = _patch_bot_add_guardian(guardian, anti_nuke, hostile)

    setattr(bot, _INSTALL_FLAG, True)
    print(
        "🛡️ AntiNuke lockdown active: security-history restore guard="
        f"{'patched' if history_patched else 'already active'}; "
        f"delegated destructive trust={'removed' if policy_patched else 'already removed'}; "
        f"audit surface={'expanded' if guardian_patched else 'already expanded'}; "
        f"owner first-strike={'active' if owner_patched else 'already active'}; "
        f"owner bot-add allowlist={'active' if bot_add_patched else 'already active'}"
    )
    return True


__all__ = [
    "install_anti_nuke_lockdown_runtime",
]
