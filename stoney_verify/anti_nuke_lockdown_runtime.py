from __future__ import annotations

"""Final AntiNuke lockdown invariants.

This runtime closes control-plane and structural-destruction gaps that only become
obvious when the existing AntiNuke layers are considered together. It installs
after durable hostile-identity enforcement so reputation remains authoritative.

The policy deliberately distinguishes ordinary moderation from structural
security changes. Trusted moderators keep bounded ban/kick/timeout behavior, but
contain mode gives no grace to server-structure deletion, permission-overwrite
corruption, destructive AutoMod mutation, or bulk message purge.
"""

from types import SimpleNamespace
from typing import Any, Iterable, Mapping, Optional

import discord

_INSTALL_FLAG = "_dank_antinuke_lockdown_runtime_installed"
_POLICY_PATCH_FLAG = "_dank_antinuke_lockdown_policy_patched"
_HISTORY_PATCH_FLAG = "_dank_antinuke_lockdown_history_patched"
_GUARDIAN_PATCH_FLAG = "_dank_antinuke_lockdown_guardian_patched"
_BOT_ADD_PATCH_FLAG = "_dank_antinuke_lockdown_bot_add_patched"
_OWNER_PATCH_FLAG = "_dank_antinuke_lockdown_owner_first_strike_patched"
_PROTECTED_CONTROL_ROLE_SETTINGS_KEY = "_dank_lockdown_protected_role_ids"

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

# These permissions can destroy durable guild content that the original readiness
# model did not classify as dangerous.
_EXTRA_DANGEROUS_PERMISSIONS = (
    "manage_messages",
    "manage_threads",
    "manage_events",
    "manage_expressions",
    "manage_emojis_and_stickers",
)

# Canonical processor keys that always become first-strike in contain mode. Keep
# routine moderation (ban/kick/timeout/member-role cleanup) on its existing bounded
# thresholds so AntiNuke does not punish a moderator for doing ordinary work.
_STRICT_PROCESS_ACTION_KEYS = frozenset(
    {
        "channel_delete",
        "role_delete",
        "webhook_delete",
        "message_bulk_delete",
    }
)

# Guardian action names whose exact audit semantics prove a structural/security
# mutation even when their canonical counter key is shared with benign updates.
_STRICT_GUARDIAN_ACTIONS = frozenset(
    {
        "guild_update",
        "channel_delete",
        "overwrite_create",
        "overwrite_update",
        "overwrite_delete",
        "role_delete",
        "member_prune",
        "webhook_update",
        "webhook_delete",
        "integration_delete",
        "app_command_permission_update",
        "automod_rule_update",
        "automod_rule_delete",
        "message_bulk_delete",
    }
)

# discord.py exposes a dedicated bulk-delete audit event. Single moderator message
# deletion remains ordinary moderation; bulk purge is the destructive surface.
_EXTRA_AUDIT_ACTIONS: dict[str, tuple[str, str, str, Optional[int]]] = {
    "message_bulk_delete": (
        "Bulk message deletion",
        "antinuke_channel_delete_threshold",
        "message_bulk_delete",
        1,
    ),
}
_EXTRA_PANIC_WEIGHTS = {"message_bulk_delete": 4}
_EXTRA_SEVERE_ACTIONS = frozenset({"message_bulk_delete"})


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
    """Preserve live AntiNuke/control-plane values during generic restore."""

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
        saved_raw = safe.get(container)
        live_raw = live.get(container)
        if not isinstance(saved_raw, Mapping) and not isinstance(live_raw, Mapping):
            continue
        saved = dict(saved_raw) if isinstance(saved_raw, Mapping) else {}
        current_nested = dict(live_raw) if isinstance(live_raw, Mapping) else {}
        protected = {
            str(key)
            for key in set(saved) | set(current_nested)
            if _is_protected_restore_key(key)
        }
        for key in protected:
            if key in current_nested:
                saved[key] = current_nested[key]
            else:
                saved.pop(key, None)
        safe[container] = saved

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
    return bool(enabled) and str(
        settings.get("antinuke_mode") or "contain"
    ).strip().lower() == "contain"


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
        snapshot = history._row_dict(safe_version.get("snapshot"))  # noqa: SLF001
        safe_version["snapshot"] = _sanitize_security_snapshot(snapshot, current)
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

    original_get = anti_nuke.get_antinuke_settings
    original_process = anti_nuke._process_claimed_destructive_event  # noqa: SLF001
    original_health = anti_nuke.antinuke_permission_health
    original_configured_trust = anti_nuke._actor_is_configured_trusted  # noqa: SLF001

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

        explicit_trusted = set(
            _safe_id_list(settings.get("antinuke_trusted_role_ids"))
        )
        try:
            cfg = await guild_config.get_guild_config(gid, refresh=bool(refresh))
            protected_roles = _control_role_ids(cfg)
        except Exception:
            protected_roles = set()

        implicit_protected = protected_roles - explicit_trusted
        if protected_roles:
            settings["antinuke_trusted_role_ids"] = list(
                dict.fromkeys([*sorted(explicit_trusted), *sorted(protected_roles)])
            )
        if implicit_protected:
            settings[_PROTECTED_CONTROL_ROLE_SETTINGS_KEY] = sorted(implicit_protected)
        else:
            settings.pop(_PROTECTED_CONTROL_ROLE_SETTINGS_KEY, None)
        return settings

    def configured_trust_without_implicit_control_roles(
        actor: Any,
        settings: Mapping[str, Any],
        *,
        ignore_role_ids: Optional[set[int]] = None,
    ) -> bool:
        ignored = {
            _safe_int(value, 0)
            for value in (ignore_role_ids or set())
            if _safe_int(value, 0) > 0
        }
        ignored.update(
            _safe_id_list(settings.get(_PROTECTED_CONTROL_ROLE_SETTINGS_KEY))
        )
        return bool(
            original_configured_trust(
                actor,
                settings,
                ignore_role_ids=ignored,
            )
        )

    async def strict_structural_process(
        guild: discord.Guild,
        *,
        entry: Any,
        action_key: str,
        action_label: str,
        target_label: str,
        threshold_key: str,
        threshold_override: Optional[int] = None,
    ) -> bool:
        try:
            settings = await protected_get(int(guild.id))
        except Exception:
            settings = None
        if _enabled_contain(settings) and action_key in _STRICT_PROCESS_ACTION_KEYS:
            threshold_override = 1
        return await original_process(
            guild,
            entry=entry,
            action_key=action_key,
            action_label=action_label,
            target_label=target_label,
            threshold_key=threshold_key,
            threshold_override=threshold_override,
        )

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
    anti_nuke.get_antinuke_settings = protected_get
    anti_nuke._actor_is_configured_trusted = (  # noqa: SLF001
        configured_trust_without_implicit_control_roles
    )
    anti_nuke._process_claimed_destructive_event = strict_structural_process  # noqa: SLF001
    anti_nuke.antinuke_permission_health = lockdown_health
    setattr(anti_nuke, _POLICY_PATCH_FLAG, True)
    return True


def _rollback_actor_proxy() -> Any:
    """Actor shape that is never owner/trusted; rollback target still comes from entry."""

    return SimpleNamespace(id=0, roles=[])


def _patch_guardian_surface(guardian: Any, anti_nuke: Any) -> bool:
    if bool(getattr(guardian, _GUARDIAN_PATCH_FLAG, False)):
        return False

    guardian._ACTIONS.update(_EXTRA_AUDIT_ACTIONS)  # noqa: SLF001
    for action_name in _STRICT_GUARDIAN_ACTIONS:
        spec = guardian._ACTIONS.get(action_name)  # noqa: SLF001
        if spec is None:
            continue
        label, threshold_key, counter_key, _override = spec
        guardian._ACTIONS[action_name] = (  # noqa: SLF001
            label,
            threshold_key,
            counter_key,
            1,
        )

    guardian._PANIC_WEIGHTS.update(_EXTRA_PANIC_WEIGHTS)  # noqa: SLF001
    guardian._PANIC_ACTIONS = frozenset(guardian._PANIC_WEIGHTS)  # noqa: SLF001
    guardian._PANIC_SEVERE_ACTIONS = frozenset(  # noqa: SLF001
        set(guardian._PANIC_SEVERE_ACTIONS) | set(_EXTRA_SEVERE_ACTIONS)  # noqa: SLF001
    )

    original_overwrite = guardian._rollback_untrusted_overwrite  # noqa: SLF001
    original_automod = guardian._rollback_untrusted_automod  # noqa: SLF001

    async def strict_overwrite(guild, entry, actor, action_name):
        settings = await anti_nuke.get_antinuke_settings(int(guild.id))
        if (
            _enabled_contain(settings)
            and not anti_nuke._actor_is_owner_or_bot(guild, actor)  # noqa: SLF001
        ):
            actor = _rollback_actor_proxy()
        return await original_overwrite(guild, entry, actor, action_name)

    async def strict_automod(guild, entry, actor, action_name):
        settings = await anti_nuke.get_antinuke_settings(int(guild.id))
        if (
            action_name in {"automod_rule_update", "automod_rule_delete"}
            and _enabled_contain(settings)
            and not anti_nuke._actor_is_owner_or_bot(guild, actor)  # noqa: SLF001
        ):
            actor = _rollback_actor_proxy()
        return await original_automod(guild, entry, actor, action_name)

    guardian._rollback_untrusted_overwrite = strict_overwrite  # noqa: SLF001
    guardian._rollback_untrusted_automod = strict_automod  # noqa: SLF001
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

        # Exact hostile reputation always outranks owner authorization.
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

        actor_id = _safe_int(getattr(actor, "id", 0), 0)
        owner_id = _safe_int(getattr(guild, "owner_id", 0), 0)
        if actor_id > 0 and actor_id == owner_id:
            # A physical guild-owner bot install is explicit authorization. The
            # trusted-user list describes trusted *actors* elsewhere in AntiNuke;
            # it is not a second, hidden allowlist for bot target IDs.
            return

        return await original(guild, entry, actor)

    guardian._handle_bot_add = strict_bot_add  # noqa: SLF001
    setattr(guardian, _BOT_ADD_PATCH_FLAG, True)
    return True


def install_anti_nuke_lockdown_runtime(bot: discord.Client) -> bool:
    """Install AntiNuke's final structural no-grace invariants once."""

    if bool(getattr(bot, _INSTALL_FLAG, False)):
        return False

    from . import anti_nuke
    from . import anti_nuke_guardian_runtime as guardian
    from . import anti_nuke_hostile_actor_runtime as hostile
    from . import anti_nuke_incident_runtime as incident

    history_patched = _patch_config_history_restore()
    policy_patched = _patch_anti_nuke_policy(anti_nuke, bot)
    guardian_patched = _patch_guardian_surface(guardian, anti_nuke)
    owner_patched = _patch_owner_first_strike(incident)
    bot_add_patched = _patch_bot_add_guardian(guardian, anti_nuke, hostile)

    setattr(bot, _INSTALL_FLAG, True)
    print(
        "🛡️ AntiNuke lockdown active: security-history restore guard="
        f"{'patched' if history_patched else 'already active'}; "
        f"structural first-strike={'active' if policy_patched else 'already active'}; "
        f"audit/rollback surface={'hardened' if guardian_patched else 'already hardened'}; "
        f"owner first-strike={'active' if owner_patched else 'already active'}; "
        f"owner bot-add authorization={'active' if bot_add_patched else 'already active'}"
    )
    return True


__all__ = ["install_anti_nuke_lockdown_runtime"]