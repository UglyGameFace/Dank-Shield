from __future__ import annotations

"""AntiNuke live-incident reliability layer.

This runtime does not own AntiNuke policy. It preserves last-known AntiNuke
security state through temporary authoritative-config outages, prewarms that
state on ready, surfaces owner-originated destructive bursts that Discord cannot
contain, and replaces the gateway audit listener with one sparse-attribution
recovery owner for specialized authority events.
"""

import asyncio
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Optional

import discord

from . import anti_nuke
from . import anti_nuke_gateway_runtime as gateway
from . import anti_nuke_guardian_runtime as guardian
from . import guild_config

_INSTALL_FLAG = "_dank_antinuke_incident_runtime_installed"
_SECURITY_STATE_PATCH_FLAG = "_dank_antinuke_security_state_patched"
_OWNER_POLICY_PATCH_FLAG = "_dank_antinuke_owner_compromise_policy_patched"
_OWNER_ALERT_COOLDOWN_KEY = "__owner_compromise_alert__"
_DISABLED_EVENT_WARN_AT: dict[int, float] = {}
_SECURITY_STATE_LOCK = threading.Lock()
_SECURITY_STATE_VERSION = 1
_SECURITY_STATE_MEMORY: dict[int, dict[str, Any]] = {}
_SPECIAL_ACTIONS = frozenset(
    {
        "bot_add",
        "role_create",
        "role_update",
        "member_role_update",
        "member_update",
    }
)

_OWNER_POLICY_BENIGN = "benign"
_OWNER_POLICY_BOUNDED = "bounded"
_OWNER_POLICY_IMMEDIATE = "immediate"
_OWNER_BOUNDED_THRESHOLD_FLOOR = 5
_OWNER_BOUNDED_AGGREGATE_FLOOR = 8
_OWNER_BOUNDED_SLOW_FLOOR = 8
_OWNER_IMMEDIATE_ACTIONS = frozenset(
    {
        "channel_delete",
        "overwrite_create",
        "overwrite_update",
        "overwrite_delete",
        "role_delete",
        "member_prune",
        "webhook_update",
        "webhook_delete",
        "integration_update",
        "integration_delete",
        "app_command_permission_update",
        "automod_rule_update",
        "automod_rule_delete",
        "message_delete",
        "message_bulk_delete",
        "stage_instance_delete",
        "onboarding_prompt_update",
        "onboarding_prompt_delete",
        "onboarding_update",
        "home_settings_update",
    }
)
_OWNER_IMMEDIATE_ACTION_KEYS = frozenset(
    {
        "channel_delete",
        "role_delete",
        "webhook_delete",
        "message_delete",
        "message_bulk_delete",
        "member_prune",
    }
)
_OWNER_GUILD_IMMEDIATE_FIELDS = frozenset({"owner", "mfa_level"})
_OWNER_GUILD_BOUNDED_FIELDS = frozenset(
    {
        "verification_level",
        "explicit_content_filter",
        "rules_channel_id",
        "public_updates_channel_id",
        "safety_alerts_channel_id",
        "features",
    }
)
_OWNER_GUILD_ROUTINE_FIELDS = frozenset(
    {
        "name",
        "icon",
        "banner",
        "splash",
        "discovery_splash",
        "vanity_url_code",
        "description",
        "default_message_notifications",
        "afk_channel_id",
        "afk_timeout",
        "system_channel_id",
        "system_channel_flags",
        "preferred_locale",
        "premium_progress_bar_enabled",
    }
)


def _security_state_path() -> Path:
    raw = str(
        os.getenv("DANK_ANTINUKE_SECURITY_STATE_FILE")
        or "data/antinuke_security_state.json"
    ).strip()
    return Path(raw or "data/antinuke_security_state.json")


def _load_security_state_document() -> dict[str, Any]:
    path = _security_state_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": _SECURITY_STATE_VERSION, "guilds": {}}
    if not isinstance(payload, dict):
        return {"version": _SECURITY_STATE_VERSION, "guilds": {}}
    guilds = payload.get("guilds")
    if not isinstance(guilds, dict):
        guilds = {}
    return {"version": _SECURITY_STATE_VERSION, "guilds": dict(guilds)}


def _read_security_snapshot(guild_id: int) -> Optional[dict[str, Any]]:
    gid = int(guild_id)
    with _SECURITY_STATE_LOCK:
        remembered = _SECURITY_STATE_MEMORY.get(gid)
        if isinstance(remembered, Mapping):
            return dict(remembered)
        payload = _load_security_state_document()
        raw = payload.get("guilds", {}).get(str(gid))
        if not isinstance(raw, Mapping):
            return None
        clean = anti_nuke.normalize_antinuke_settings(dict(raw))
        _SECURITY_STATE_MEMORY[gid] = dict(clean)
        return dict(clean)


def _write_security_snapshot(guild_id: int, settings: Mapping[str, Any]) -> bool:
    gid = int(guild_id)
    clean = anti_nuke.normalize_antinuke_settings(dict(settings or {}))
    path = _security_state_path()
    tmp = path.with_name(path.name + ".tmp")
    try:
        with _SECURITY_STATE_LOCK:
            remembered = _SECURITY_STATE_MEMORY.get(gid)
            if remembered == clean and path.exists():
                return True

            _SECURITY_STATE_MEMORY[gid] = dict(clean)
            payload = _load_security_state_document()
            guilds = payload.setdefault("guilds", {})
            guilds[str(gid)] = dict(clean)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(
                json.dumps(payload, sort_keys=True, separators=(",", ":")),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        return True
    except Exception as exc:
        try:
            print(
                "⚠️ AntiNuke durable security snapshot write failed "
                f"guild={gid} error={type(exc).__name__}: {exc}"
            )
        except Exception:
            pass
        return False


def _config_source(cfg: Any) -> str:
    try:
        if hasattr(cfg, "get"):
            return str(cfg.get("source") or "").strip().lower()
    except Exception:
        pass
    return str(getattr(cfg, "source", "") or "").strip().lower()


def _patch_security_state_persistence() -> bool:
    """Keep last-known AntiNuke state available through cold DB/config outages."""

    if bool(getattr(anti_nuke, _SECURITY_STATE_PATCH_FLAG, False)):
        return False

    original_save = anti_nuke.save_antinuke_settings

    async def durable_get(
        guild_id: int,
        *,
        refresh: bool = False,
    ) -> dict[str, Any]:
        gid = int(guild_id)
        try:
            cfg = await guild_config.get_guild_config(gid, refresh=bool(refresh))
        except Exception as exc:
            snapshot = _read_security_snapshot(gid)
            if snapshot is not None:
                print(
                    "🛡️ AntiNuke using durable last-known security state "
                    f"guild={gid} config_error={type(exc).__name__}"
                )
                return snapshot
            raise

        source = _config_source(cfg)
        if source.startswith("unavailable:"):
            snapshot = _read_security_snapshot(gid)
            if snapshot is not None:
                print(
                    "🛡️ AntiNuke using durable last-known security state "
                    f"guild={gid} source={source}"
                )
                return snapshot
            print(
                "🚨 AntiNuke authoritative config unavailable with no durable snapshot "
                f"guild={gid}; refusing to invent prior enablement state"
            )
            return anti_nuke.normalize_antinuke_settings(cfg)

        settings = anti_nuke.normalize_antinuke_settings(cfg)
        _write_security_snapshot(gid, settings)
        return settings

    async def durable_save(
        guild_id: int,
        patch: Mapping[str, Any],
    ) -> dict[str, Any]:
        settings = await original_save(int(guild_id), patch)
        _write_security_snapshot(int(guild_id), settings)
        return settings

    anti_nuke.get_antinuke_settings = durable_get
    anti_nuke.save_antinuke_settings = durable_save
    setattr(anti_nuke, _SECURITY_STATE_PATCH_FLAG, True)
    return True


def _is_guild_owner(guild: discord.Guild, actor: Any) -> bool:
    try:
        return int(getattr(actor, "id", 0) or 0) == int(
            getattr(guild, "owner_id", 0) or 0
        )
    except Exception:
        return False


def _is_dank_shield_bot(actor: Any) -> bool:
    try:
        return bool(
            getattr(anti_nuke.bot, "user", None) is not None
            and int(getattr(actor, "id", 0) or 0) == int(anti_nuke.bot.user.id)
        )
    except Exception:
        return False


def _warn_disabled_destructive_event(
    guild: discord.Guild,
    actor: Any,
    action_key: str,
) -> None:
    gid = int(guild.id)
    now = time.monotonic()
    last = float(_DISABLED_EVENT_WARN_AT.get(gid, 0.0) or 0.0)
    if now - last < 30.0:
        return
    _DISABLED_EVENT_WARN_AT[gid] = now
    print(
        "🚨 AntiNuke destructive audit event observed while protection is disabled "
        f"guild={gid} actor={getattr(actor, 'id', 'unknown')} action={action_key}"
    )


def _entry_action_name(entry: Any) -> str:
    try:
        return guardian._action_name(entry)  # noqa: SLF001
    except Exception:
        return ""


def _field_changed(entry: Any, name: str) -> bool:
    before = getattr(entry, "before", None)
    after = getattr(entry, "after", None)
    if before is None or after is None:
        return False
    old = getattr(before, name, None)
    new = getattr(after, name, None)
    return old != new and (old is not None or new is not None)


def _member_role_changes(entry: Any) -> tuple[list[Any], list[Any]]:
    before = getattr(entry, "before", None)
    after = getattr(entry, "after", None)
    before_roles = list(getattr(before, "roles", []) or []) if before is not None else []
    after_roles = list(getattr(after, "roles", []) or []) if after is not None else []
    before_ids = {
        int(getattr(role, "id", 0) or 0)
        for role in before_roles
        if int(getattr(role, "id", 0) or 0) > 0
    }
    after_ids = {
        int(getattr(role, "id", 0) or 0)
        for role in after_roles
        if int(getattr(role, "id", 0) or 0) > 0
    }
    added = [
        role
        for role in after_roles
        if int(getattr(role, "id", 0) or 0) > 0
        and int(getattr(role, "id", 0) or 0) not in before_ids
    ]
    removed = [
        role
        for role in before_roles
        if int(getattr(role, "id", 0) or 0) > 0
        and int(getattr(role, "id", 0) or 0) not in after_ids
    ]
    return added, removed


def _owner_event_policy(
    entry: Any,
    action_key: str,
    settings: Mapping[str, Any],
) -> str:
    """Classify owner activity without trusting the legacy blanket 1-strike wrapper."""

    action_name = _entry_action_name(entry)

    if action_name == "guild_update":
        if any(_field_changed(entry, name) for name in _OWNER_GUILD_IMMEDIATE_FIELDS):
            return _OWNER_POLICY_IMMEDIATE
        if any(_field_changed(entry, name) for name in _OWNER_GUILD_BOUNDED_FIELDS):
            return _OWNER_POLICY_BOUNDED
        if any(_field_changed(entry, name) for name in _OWNER_GUILD_ROUTINE_FIELDS):
            return _OWNER_POLICY_BENIGN
        # A sparse/unknown guild diff is not enough for a compromise accusation,
        # but keep it as bounded evidence instead of silently discarding it.
        return _OWNER_POLICY_BOUNDED

    if action_name == "role_create":
        target = getattr(entry, "target", None)
        if target is not None and anti_nuke.role_has_dangerous_permissions(target):
            return _OWNER_POLICY_IMMEDIATE
        return _OWNER_POLICY_BOUNDED

    if action_name == "role_update":
        before = getattr(entry, "before", None)
        after = getattr(entry, "after", None)
        if anti_nuke.dangerous_permissions_added(before, after):
            return _OWNER_POLICY_IMMEDIATE
        if anti_nuke.dangerous_permissions_changed(before, after):
            return _OWNER_POLICY_BOUNDED
        if _field_changed(entry, "position"):
            return _OWNER_POLICY_BOUNDED
        return _OWNER_POLICY_BENIGN

    if action_name == "member_role_update":
        added, removed = _member_role_changes(entry)
        trusted_role_ids = {
            int(value)
            for value in anti_nuke._safe_id_list(  # noqa: SLF001
                settings.get("antinuke_trusted_role_ids")
            )
        }
        if any(
            anti_nuke.role_has_dangerous_permissions(role)
            or int(getattr(role, "id", 0) or 0) in trusted_role_ids
            for role in added
        ):
            return _OWNER_POLICY_IMMEDIATE
        if removed:
            return _OWNER_POLICY_BOUNDED
        return _OWNER_POLICY_BENIGN

    if action_name in _OWNER_IMMEDIATE_ACTIONS:
        return _OWNER_POLICY_IMMEDIATE
    if action_key in _OWNER_IMMEDIATE_ACTION_KEYS:
        return _OWNER_POLICY_IMMEDIATE

    # Owner moderation, ordinary creation, and lower-confidence administrative
    # activity remain useful burst evidence, but are never enough for a 1/1
    # compromise accusation by themselves.
    return _OWNER_POLICY_BOUNDED


async def _process_owner_destructive_event(
    guild: discord.Guild,
    *,
    entry: Any,
    action_key: str,
    action_label: str,
    target_label: str,
    threshold_key: str,
    threshold_override: Optional[int] = None,
) -> bool:
    """Detect owner compromise without misclassifying routine owner administration."""

    settings = await anti_nuke.get_antinuke_settings(int(guild.id))
    actor = getattr(entry, "user", None)
    actor_id = int(getattr(actor, "id", 0) or 0)
    policy = _owner_event_policy(entry, action_key, settings)
    if policy == _OWNER_POLICY_BENIGN:
        return True
    if not settings["antinuke_enabled"]:
        _warn_disabled_destructive_event(guild, actor, action_key)
        return False
    if actor_id <= 0:
        return True

    window_seconds = int(settings["antinuke_window_seconds"])
    if policy == _OWNER_POLICY_IMMEDIATE:
        threshold = 1
    else:
        threshold = max(
            _OWNER_BOUNDED_THRESHOLD_FLOOR,
            int(settings[threshold_key]),
        )

    count = anti_nuke._record_action(  # noqa: SLF001
        int(guild.id), actor_id, action_key, window_seconds=window_seconds
    )
    aggregate_count = anti_nuke._record_action(  # noqa: SLF001
        int(guild.id),
        actor_id,
        anti_nuke._AGGREGATE_ACTION_KEY,  # noqa: SLF001
        window_seconds=window_seconds,
    )
    aggregate_threshold = anti_nuke._aggregate_threshold(settings)  # noqa: SLF001
    if policy == _OWNER_POLICY_BOUNDED:
        aggregate_threshold = max(
            _OWNER_BOUNDED_AGGREGATE_FLOOR,
            aggregate_threshold,
        )

    slow_count = 0
    slow_threshold = 0
    slow_triggered = False
    if action_key in anti_nuke._SLOW_BURN_ACTIONS:  # noqa: SLF001
        slow_count = anti_nuke._record_action(  # noqa: SLF001
            int(guild.id),
            actor_id,
            anti_nuke._SLOW_BURN_ACTION_KEY,  # noqa: SLF001
            window_seconds=anti_nuke._SLOW_BURN_WINDOW_SECONDS,  # noqa: SLF001
        )
        slow_threshold = anti_nuke._slow_burn_threshold(settings)  # noqa: SLF001
        if policy == _OWNER_POLICY_BOUNDED:
            slow_threshold = max(_OWNER_BOUNDED_SLOW_FLOOR, slow_threshold)
        slow_triggered = slow_count >= slow_threshold

    category_triggered = count >= threshold
    aggregate_triggered = aggregate_count >= aggregate_threshold
    if not category_triggered and not aggregate_triggered and not slow_triggered:
        return True

    if not anti_nuke._trigger_ready(  # noqa: SLF001
        int(guild.id), actor_id, _OWNER_ALERT_COOLDOWN_KEY
    ):
        return True
    anti_nuke._mark_triggered(  # noqa: SLF001
        int(guild.id), actor_id, _OWNER_ALERT_COOLDOWN_KEY
    )

    sources: list[str] = []
    if category_triggered:
        sources.append(f"{action_key} {count}/{threshold}")
    if aggregate_triggered:
        sources.append(
            f"mixed destructive actions {aggregate_count}/{aggregate_threshold}"
        )
    if slow_triggered:
        sources.append(
            "owner long-horizon "
            f"{anti_nuke._SLOW_BURN_WINDOW_SECONDS}s {slow_count}/{slow_threshold}"  # noqa: SLF001
        )

    immediate = policy == _OWNER_POLICY_IMMEDIATE
    await anti_nuke._post_incident(  # noqa: SLF001
        guild,
        title=(
            "🚨 AntiNuke Owner-Compromise Warning"
            if immediate
            else "⚠️ AntiNuke Owner Activity Burst Warning"
        ),
        actor=actor,
        action_label=action_label,
        target_label=target_label,
        response_label=(
            (
                "High-confidence security/destructive guild-owner activity crossed "
                "the AntiNuke emergency boundary. Discord does not allow bots to "
                "kick, ban, strip roles from, or otherwise contain the physical guild "
                "owner. Dank Shield can detect and report this boundary, but it cannot "
                "revoke the owner's Discord account/session. Secure the owner account/"
                "session immediately if this action was not expected."
            )
            if immediate
            else (
                "A bounded burst of owner administrative activity crossed the elevated "
                "owner threshold. No owner containment was attempted. Verify the activity "
                "and secure the owner account/session if the burst was unexpected."
            )
        ),
        count_label=f"{window_seconds}s window • " + " • ".join(sources),
        details=(
            "Owner policy is severity-aware: routine/cosmetic administration is not a "
            "1/1 compromise event, while high-confidence destructive or authority "
            "mutations remain immediate warnings."
        ),
    )
    return True


def _patch_owner_compromise_policy() -> bool:
    """Stop canonical AntiNuke from silently swallowing owner-originated attacks."""

    if bool(getattr(anti_nuke, _OWNER_POLICY_PATCH_FLAG, False)):
        return False

    original = anti_nuke._process_claimed_destructive_event  # noqa: SLF001

    async def wrapped(
        guild: discord.Guild,
        *,
        entry: Any,
        action_key: str,
        action_label: str,
        target_label: str,
        threshold_key: str,
        threshold_override: Optional[int] = None,
    ) -> bool:
        actor = getattr(entry, "user", None)
        if _is_dank_shield_bot(actor):
            return await original(
                guild,
                entry=entry,
                action_key=action_key,
                action_label=action_label,
                target_label=target_label,
                threshold_key=threshold_key,
                threshold_override=threshold_override,
            )
        if _is_guild_owner(guild, actor):
            return await _process_owner_destructive_event(
                guild,
                entry=entry,
                action_key=action_key,
                action_label=action_label,
                target_label=target_label,
                threshold_key=threshold_key,
                threshold_override=threshold_override,
            )

        settings = await anti_nuke.get_antinuke_settings(int(guild.id))
        if not settings["antinuke_enabled"]:
            _warn_disabled_destructive_event(guild, actor, action_key)
        return await original(
            guild,
            entry=entry,
            action_key=action_key,
            action_label=action_label,
            target_label=target_label,
            threshold_key=threshold_key,
            threshold_override=threshold_override,
        )

    anti_nuke._process_claimed_destructive_event = wrapped  # noqa: SLF001
    setattr(anti_nuke, _OWNER_POLICY_PATCH_FLAG, True)
    return True


async def _prewarm_security_state(bot: discord.Client) -> None:
    guilds = list(getattr(bot, "guilds", []) or [])
    if not guilds:
        return

    semaphore = asyncio.Semaphore(5)

    async def warm(guild: discord.Guild) -> None:
        async with semaphore:
            try:
                settings = await anti_nuke.get_antinuke_settings(
                    int(guild.id), refresh=True
                )
                state = "ON" if settings.get("antinuke_enabled") else "OFF"
                print(
                    "🛡️ AntiNuke security state prewarmed "
                    f"guild={guild.id} state={state} mode={settings.get('antinuke_mode')}"
                )
            except Exception as exc:
                print(
                    "🚨 AntiNuke security-state prewarm failed "
                    f"guild={getattr(guild, 'id', 'unknown')} "
                    f"error={type(exc).__name__}: {exc}"
                )

    await asyncio.gather(*(warm(guild) for guild in guilds))


def _install_prewarm_listener(bot: discord.Client) -> bool:
    adder = getattr(bot, "add_listener", None)
    if not callable(adder):
        return False

    async def _on_ready_antinuke_security_prewarm() -> None:
        await _prewarm_security_state(bot)

    try:
        adder(_on_ready_antinuke_security_prewarm, "on_ready")
    except Exception:
        return False
    return True


def _target_id(entry: Any) -> int | None:
    value = guardian._target_id(entry)  # noqa: SLF001
    return int(value) if value is not None else None


async def _process_owner_special_action(
    guild: discord.Guild,
    entry: Any,
    actor: Any,
    action_name: str,
    *,
    consume_entry: bool,
) -> bool:
    """Route owner-only authority events that gateway handlers historically skipped."""

    if not _is_guild_owner(guild, actor):
        return False

    threshold_key = "antinuke_role_delete_threshold"
    action_key = "role_update"
    action_label = ""
    target_label = ""
    immediate = False

    if action_name == "role_create":
        role = getattr(entry, "target", None)
        if role is None or not anti_nuke.role_has_dangerous_permissions(role):
            return False
        action_key = "role_create"
        action_label = "Dangerous role created by guild owner"
        target_label = gateway._target_label(entry)  # noqa: SLF001
        immediate = True

    elif action_name == "role_update":
        added = anti_nuke.dangerous_permissions_added(
            getattr(entry, "before", None),
            getattr(entry, "after", None),
        )
        if not added:
            return False
        action_label = "Guild owner added dangerous role permissions: " + ", ".join(added)
        target_label = gateway._target_label(entry)  # noqa: SLF001
        immediate = True

    elif action_name == "member_role_update":
        target = await gateway._resolve_target_member(guild, entry)  # noqa: SLF001
        if bool(getattr(target, "bot", False)):
            return False
        added, removed = _member_role_changes(entry)
        settings = await anti_nuke.get_antinuke_settings(int(guild.id))
        trusted_role_ids = {
            int(value)
            for value in anti_nuke._safe_id_list(  # noqa: SLF001
                settings.get("antinuke_trusted_role_ids")
            )
        }
        sensitive_added = [
            role
            for role in added
            if anti_nuke.role_has_dangerous_permissions(role)
            or int(getattr(role, "id", 0) or 0) in trusted_role_ids
        ]
        if sensitive_added:
            action_label = "Guild owner granted a security-sensitive role"
            target_label = gateway._member_target_label(entry)  # noqa: SLF001
            immediate = True
        elif removed:
            action_key = "member_role_remove"
            action_label = "Guild owner member-role removal burst"
            target_label = gateway._member_target_label(entry)  # noqa: SLF001
        else:
            return False
    else:
        return False

    claimed = guardian._EntryProxy(entry, actor)  # noqa: SLF001
    if consume_entry and anti_nuke._consume_audit_entry(claimed):  # noqa: SLF001
        return True

    await anti_nuke._process_claimed_destructive_event(  # noqa: SLF001
        guild,
        entry=claimed,
        action_key=action_key,
        action_label=action_label,
        target_label=target_label,
        threshold_key=threshold_key,
        threshold_override=1 if immediate else None,
    )
    return True


async def _dispatch_recovered(
    guild: discord.Guild,
    entry: Any,
    actor: Any,
    action_name: str,
) -> None:
    """Classify a target-correct REST entry only after attribution recovery."""

    if await _process_owner_special_action(
        guild,
        entry,
        actor,
        action_name,
        consume_entry=False,
    ):
        return

    if action_name == "bot_add":
        await guardian._handle_bot_add(guild, entry, actor)  # noqa: SLF001
        return

    if action_name == "role_create":
        await gateway._handle_role_create(guild, entry)  # noqa: SLF001
        return

    if action_name == "role_update":
        if anti_nuke.dangerous_permissions_added(
            getattr(entry, "before", None),
            getattr(entry, "after", None),
        ):
            await gateway._handle_dangerous_role_update(  # noqa: SLF001
                guild,
                entry,
                actor,
            )
            return

        if guardian._generic_role_update(entry):  # noqa: SLF001
            await guardian._process(  # noqa: SLF001
                guild,
                entry,
                actor,
                "role_update",
                (
                    "Role hierarchy/settings mutation",
                    "antinuke_role_delete_threshold",
                    "role_update",
                    None,
                ),
            )
        return

    if action_name == "member_role_update":
        await gateway._handle_member_role_update(  # noqa: SLF001
            guild,
            entry,
            actor,
        )
        return

    if action_name == "member_update" and gateway._timeout_extended(entry):  # noqa: SLF001
        await gateway._handle_member_timeout(  # noqa: SLF001
            guild,
            entry,
            actor,
        )


async def _recover_special_sparse_entry(
    guild: discord.Guild,
    gateway_entry: Any,
    action_name: str,
) -> bool:
    """Recover sparse attribution before trusting gateway diff completeness."""

    await asyncio.sleep(0.25)
    claimed = await guardian._claim_priority_entry(  # noqa: SLF001
        guild,
        (action_name,),
        target_id=_target_id(gateway_entry),
        retries=4,
    )
    if claimed is None:
        print(
            "🚨 AntiNuke sparse attribution recovery FAILED "
            f"guild={guild.id} action={action_name} "
            f"target={_target_id(gateway_entry) or 'unknown'}"
        )
        return False

    entry, actor = claimed
    print(
        "🛡️ AntiNuke sparse attribution recovered "
        f"guild={guild.id} action={action_name} "
        f"actor={getattr(actor, 'id', 'unknown')} "
        f"target={_target_id(entry) or 'unknown'}"
    )
    await _dispatch_recovered(guild, entry, actor, action_name)
    return True


async def _on_audit_log_entry_create(entry: discord.AuditLogEntry) -> None:
    guild = getattr(entry, "guild", None)
    action_name = guardian._action_name(entry)  # noqa: SLF001

    if guild is None or action_name not in _SPECIAL_ACTIONS:
        await gateway._on_audit_log_entry_create(entry)  # noqa: SLF001
        return

    actor = await guardian._resolve_actor(guild, entry)  # noqa: SLF001
    if actor is not None:
        if await _process_owner_special_action(
            guild,
            entry,
            actor,
            action_name,
            consume_entry=True,
        ):
            return
        await gateway._on_audit_log_entry_create(entry)  # noqa: SLF001
        return

    recovered = await _recover_special_sparse_entry(
        guild,
        entry,
        action_name,
    )
    if not recovered:
        return


def install_anti_nuke_incident_runtime(bot: discord.Client) -> bool:
    if bool(getattr(bot, _INSTALL_FLAG, False)):
        return False

    remover = getattr(bot, "remove_listener", None)
    adder = getattr(bot, "add_listener", None)
    if not callable(remover) or not callable(adder):
        return False

    try:
        remover(gateway._on_audit_log_entry_create, "on_audit_log_entry_create")  # noqa: SLF001
        adder(_on_audit_log_entry_create, "on_audit_log_entry_create")
    except Exception as exc:
        print(
            "🚨 AntiNuke incident runtime install FAILED "
            f"error={type(exc).__name__}: {exc}"
        )
        return False

    security_state_patched = _patch_security_state_persistence()
    owner_policy_patched = _patch_owner_compromise_policy()
    prewarm_installed = _install_prewarm_listener(bot)
    setattr(bot, _INSTALL_FLAG, True)

    print(
        "🛡️ AntiNuke incident runtime active: "
        f"durable-security-state={'patched' if security_state_patched else 'already active'}; "
        f"owner-compromise={'detected' if owner_policy_patched else 'already active'}; "
        f"security-prewarm={'installed' if prewarm_installed else 'unavailable'}; "
        "sparse authority events recover attribution before classification"
    )
    return True


__all__ = ["install_anti_nuke_incident_runtime"]
