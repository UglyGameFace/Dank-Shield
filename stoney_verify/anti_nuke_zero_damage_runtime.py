from __future__ import annotations

"""Final defensive AntiNuke invariants layered after self-action proof."""

import asyncio
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Optional

import discord

from . import anti_nuke
from . import anti_nuke_guardian_runtime as guardian
from . import anti_nuke_incident_runtime as incident
from . import anti_nuke_self_action_runtime as self_action
from . import guild_config

_INSTALL_FLAG = "_dank_antinuke_zero_damage_runtime_installed"
_SELF_FLAG = "_dank_antinuke_zero_damage_self_patched"
_GUARDIAN_FLAG = "_dank_antinuke_zero_damage_guardian_patched"
_HEALTH_FLAG = "_dank_antinuke_zero_damage_health_patched"

SUPPORTED_DISCORD_PY = "2.7.1"
_QUARANTINE_VERSION = 1
_QUARANTINE_TTL_SECONDS = 1800
_QUARANTINE_LOCK = threading.Lock()
_EJECTION_IN_PROGRESS: set[int] = set()
_WARNED: set[int] = set()

_Q_ACTIVE = "antinuke_compromise_quarantined"
_Q_UNTIL = "antinuke_compromise_quarantine_until"
_Q_ACTION = "antinuke_compromise_action"
_Q_AT = "antinuke_compromise_detected_at"

_EXTENDED_ACTIONS: dict[str, tuple[str, str, str, Optional[int]]] = {
    "message_delete": ("Message deletion", "antinuke_channel_delete_threshold", "message_delete", 1),
    "message_bulk_delete": ("Bulk message deletion", "antinuke_channel_delete_threshold", "message_bulk_delete", 1),
    "invite_create": ("Invite creation", "antinuke_channel_delete_threshold", "invite_create", None),
    "invite_update": ("Invite mutation", "antinuke_channel_delete_threshold", "invite_update", None),
    "integration_create": ("Integration creation", "antinuke_role_delete_threshold", "integration_create", None),
    "integration_update": ("Integration mutation", "antinuke_role_delete_threshold", "integration_update", 1),
    "emoji_create": ("Emoji creation", "antinuke_channel_delete_threshold", "emoji_create", None),
    "emoji_update": ("Emoji mutation", "antinuke_channel_delete_threshold", "emoji_update", None),
    "sticker_create": ("Sticker creation", "antinuke_channel_delete_threshold", "sticker_create", None),
    "sticker_update": ("Sticker mutation", "antinuke_channel_delete_threshold", "sticker_update", None),
    "scheduled_event_create": ("Scheduled-event creation", "antinuke_channel_delete_threshold", "scheduled_event_create", None),
    "scheduled_event_update": ("Scheduled-event mutation", "antinuke_channel_delete_threshold", "scheduled_event_update", None),
    "thread_create": ("Thread/forum-post creation", "antinuke_channel_delete_threshold", "thread_create", None),
    "thread_update": ("Thread/forum-post mutation", "antinuke_channel_delete_threshold", "thread_update", None),
    "stage_instance_create": ("Stage instance creation", "antinuke_channel_delete_threshold", "stage_create", None),
    "stage_instance_update": ("Stage instance mutation", "antinuke_channel_delete_threshold", "stage_update", None),
    "stage_instance_delete": ("Stage instance deletion", "antinuke_channel_delete_threshold", "stage_delete", 1),
    "soundboard_sound_create": ("Soundboard creation", "antinuke_channel_delete_threshold", "soundboard_create", None),
    "soundboard_sound_update": ("Soundboard mutation", "antinuke_channel_delete_threshold", "soundboard_update", None),
    "onboarding_prompt_create": ("Onboarding prompt creation", "antinuke_channel_delete_threshold", "onboarding_update", None),
    "onboarding_prompt_update": ("Onboarding prompt mutation", "antinuke_channel_delete_threshold", "onboarding_update", 1),
    "onboarding_prompt_delete": ("Onboarding prompt deletion", "antinuke_channel_delete_threshold", "onboarding_update", 1),
    "onboarding_create": ("Guild onboarding creation", "antinuke_channel_delete_threshold", "onboarding_update", None),
    "onboarding_update": ("Guild onboarding mutation", "antinuke_channel_delete_threshold", "onboarding_update", 1),
    "home_settings_create": ("Server Guide creation", "antinuke_channel_delete_threshold", "home_settings", None),
    "home_settings_update": ("Server Guide mutation", "antinuke_channel_delete_threshold", "home_settings", 1),
    "member_move": ("Member voice move", "antinuke_kick_threshold", "member_move", None),
    "member_disconnect": ("Member voice disconnect", "antinuke_kick_threshold", "member_disconnect", None),
}

_STRICT_ACTIONS = frozenset({
    "guild_update", "channel_delete", "overwrite_create", "overwrite_update",
    "overwrite_delete", "role_delete", "ban", "unban", "kick", "member_prune",
    "invite_delete", "webhook_update", "webhook_delete", "emoji_delete",
    "integration_update", "integration_delete", "sticker_delete",
    "scheduled_event_delete", "thread_delete", "stage_instance_delete",
    "app_command_permission_update", "soundboard_sound_delete",
    "automod_rule_update", "automod_rule_delete", "message_delete",
    "message_bulk_delete", "onboarding_prompt_update", "onboarding_prompt_delete",
    "onboarding_update", "home_settings_update",
})

# Extended guild-update coverage is security-only. Routine profile and
# housekeeping fields must never be promoted back into destructive evidence.
_GUILD_SECURITY_FIELDS = frozenset(
    {
        "verification_level",
        "explicit_content_filter",
        "rules_channel_id",
        "public_updates_channel_id",
        "safety_alerts_channel_id",
        "features",
        "mfa_level",
        "owner",
        "vanity_url_code",
    }
)


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
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _enabled_contain(settings: Mapping[str, Any] | None) -> bool:
    if not isinstance(settings, Mapping):
        return False
    return _safe_bool(settings.get("antinuke_enabled"), False) and str(
        settings.get("antinuke_mode") or "contain"
    ).strip().lower() == "contain"


def _state_path() -> Path:
    raw = str(
        os.getenv("DANK_ANTINUKE_COMPROMISE_STATE_FILE")
        or "data/antinuke_compromise_quarantine.json"
    ).strip()
    return Path(raw or "data/antinuke_compromise_quarantine.json")


def _quarantine_seconds() -> int:
    try:
        value = int(
            str(
                os.getenv("DANK_ANTINUKE_COMPROMISE_QUARANTINE_SECONDS")
                or _QUARANTINE_TTL_SECONDS
            )
        )
    except Exception:
        value = _QUARANTINE_TTL_SECONDS
    return max(300, min(86400, value))


def _load_state() -> dict[str, Any]:
    try:
        payload = json.loads(_state_path().read_text(encoding="utf-8"))
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    guilds = payload.get("guilds")
    if not isinstance(guilds, dict):
        guilds = {}
    return {"version": _QUARANTINE_VERSION, "guilds": dict(guilds)}


def _write_local(guild_id: int, action_name: str, until: int) -> None:
    path = _state_path()
    tmp = path.with_name(path.name + ".tmp")
    with _QUARANTINE_LOCK:
        payload = _load_state()
        payload.setdefault("guilds", {})[str(int(guild_id))] = {
            "active": True,
            "until": int(until),
            "action": str(action_name or "unknown")[:100],
            "detected_at": int(time.time()),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            json.dumps(payload, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        os.replace(tmp, path)


def _local_active(guild_id: int) -> bool:
    with _QUARANTINE_LOCK:
        raw = _load_state().get("guilds", {}).get(str(int(guild_id)))
    return (
        isinstance(raw, Mapping)
        and _safe_bool(raw.get("active"), False)
        and _safe_int(raw.get("until"), 0) > int(time.time())
    )


async def _persist_quarantine(guild_id: int, action_name: str) -> int:
    """Persist locally before ejection and mirror to DB without delaying response."""

    gid = int(guild_id)
    until = int(time.time()) + _quarantine_seconds()
    try:
        _write_local(gid, action_name, until)
    except Exception as exc:
        print(
            "🚨 AntiNuke local compromise quarantine write failed "
            f"guild={gid} error={type(exc).__name__}: {exc}"
        )

    async def mirror() -> None:
        try:
            await guild_config.upsert_guild_config(
                gid,
                {
                    _Q_ACTIVE: True,
                    _Q_UNTIL: until,
                    _Q_ACTION: str(action_name or "unknown")[:100],
                    _Q_AT: int(time.time()),
                },
            )
        except Exception as exc:
            print(
                "⚠️ AntiNuke DB compromise quarantine write failed "
                f"guild={gid} error={type(exc).__name__}: {exc}"
            )

    try:
        asyncio.create_task(mirror(), name=f"dank-antinuke-quarantine-{gid}")
    except Exception:
        await mirror()
    return until


async def quarantine_active(guild_id: int) -> bool:
    gid = int(guild_id)
    if _local_active(gid):
        return True
    try:
        cfg = await guild_config.get_guild_config(gid, refresh=True)
    except Exception:
        return False
    return _safe_bool(cfg.get(_Q_ACTIVE), False) and _safe_int(
        cfg.get(_Q_UNTIL), 0
    ) > int(time.time())


async def _settings_fail_closed(guild_id: int) -> dict[str, Any]:
    gid = int(guild_id)
    try:
        return dict(await anti_nuke.get_antinuke_settings(gid))
    except Exception as exc:
        try:
            snapshot = incident._read_security_snapshot(gid)  # noqa: SLF001
        except Exception:
            snapshot = None
        if isinstance(snapshot, Mapping):
            print(
                "🛡️ AntiNuke self-compromise check using durable snapshot "
                f"guild={gid} error={type(exc).__name__}"
            )
            return dict(snapshot)
        print(
            "🚨 AntiNuke self-compromise state unavailable; failing closed "
            f"guild={gid} error={type(exc).__name__}"
        )
        return {"antinuke_enabled": True, "antinuke_mode": "contain"}


async def _warn_owner(
    bot: discord.Client,
    guild: Any,
    action_name: str,
    *,
    failed: bool,
) -> None:
    owner_id = _safe_int(getattr(guild, "owner_id", 0), 0)
    if owner_id <= 0:
        return
    try:
        user = bot.get_user(owner_id)
    except Exception:
        user = None
    if user is None:
        try:
            user = await bot.fetch_user(owner_id)
        except Exception:
            user = None
    sender = getattr(user, "send", None)
    if not callable(sender):
        return
    state = (
        "Self-ejection failed and will be retried automatically."
        if failed
        else "I am removing my bot identity from the server immediately."
    )
    try:
        await sender(
            "🚨 Dank Shield detected an unverified protected action attributed to its "
            f"own bot identity (`{action_name}`). {state} Rotate the Discord bot "
            "credential before re-adding Dank Shield. A temporary durable quarantine "
            "blocks immediate re-entry."
        )
    except Exception:
        pass


async def attempt_quarantine_ejection(
    bot: discord.Client,
    guild: Any,
    action_name: str,
) -> bool:
    gid = _safe_int(getattr(guild, "id", 0), 0)
    if gid <= 0 or gid in _EJECTION_IN_PROGRESS:
        return False
    _EJECTION_IN_PROGRESS.add(gid)
    try:
        if gid not in _WARNED:
            await _warn_owner(bot, guild, action_name, failed=False)
            _WARNED.add(gid)
        leave = getattr(guild, "leave", None)
        if not callable(leave):
            await _warn_owner(bot, guild, action_name, failed=True)
            return False
        last_error: Optional[BaseException] = None
        for attempt in range(1, 4):
            try:
                await leave()
                self_action._COMPROMISE_GUILDS.add(gid)  # noqa: SLF001
                print(
                    "🛡️ AntiNuke fail-closed self-ejection completed "
                    f"guild={gid} action={action_name} attempt={attempt}"
                )
                return True
            except Exception as exc:
                last_error = exc
                if attempt < 3:
                    await asyncio.sleep(0.35 * attempt)
        self_action._COMPROMISE_GUILDS.discard(gid)  # noqa: SLF001
        print(
            "🚨 AntiNuke CRITICAL self-ejection failed after retries "
            f"guild={gid} action={action_name} error={last_error}"
        )
        await _warn_owner(bot, guild, action_name, failed=True)
        return False
    finally:
        _EJECTION_IN_PROGRESS.discard(gid)


async def _unmatched_self_action(
    bot: discord.Client,
    guild: Any,
    entry: Any,
    action_name: str,
) -> None:
    gid = _safe_int(getattr(guild, "id", 0), 0)
    if gid <= 0:
        return
    settings = await _settings_fail_closed(gid)
    if not _safe_bool(settings.get("antinuke_enabled"), False):
        return
    if str(settings.get("antinuke_mode") or "contain").strip().lower() != "contain":
        try:
            await anti_nuke._post_incident(  # noqa: SLF001
                guild,
                title="🚨 AntiNuke Unverified Dank Shield Action",
                actor=getattr(entry, "user", None),
                action_label=f"Unmatched self-attributed audit action: {action_name}",
                target_label=str(getattr(entry, "target", None) or "Unknown"),
                response_label="Alert-only mode: no self-ejection was performed.",
                details=(
                    "No valid one-time authorization matched this running process."
                ),
            )
        except Exception:
            pass
        return
    await _persist_quarantine(gid, action_name)
    await attempt_quarantine_ejection(bot, guild, action_name)


def _patch_self_action() -> bool:
    if bool(getattr(self_action, _SELF_FLAG, False)):
        return False
    original = self_action._request_spec  # noqa: SLF001

    def request_spec(
        bot: discord.Client,
        route: Any,
        kwargs: Mapping[str, Any],
    ):
        spec = original(bot, route, kwargs)
        if spec is not None:
            return spec
        method = str(getattr(route, "method", "") or "").strip().upper()
        path = self_action._route_path(route)  # noqa: SLF001
        match = re.fullmatch(r"/webhooks/(\d+)(?:/[^/]+)?", path)
        if match and method in {"PATCH", "DELETE"}:
            action = "webhook_update" if method == "PATCH" else "webhook_delete"
            return self_action._spec(  # noqa: SLF001
                (action,), 0, self_action._id_key(match.group(1))  # noqa: SLF001
            )
        match = re.fullmatch(r"/guilds/(\d+)/onboarding", path)
        if match and method in {"PUT", "PATCH"}:
            return self_action._spec(  # noqa: SLF001
                ("onboarding_create", "onboarding_update"),
                int(match.group(1)),
            )
        return None

    self_action._request_spec = request_spec  # noqa: SLF001
    self_action._PROTECTED_ACTIONS = frozenset(  # noqa: SLF001
        set(self_action._PROTECTED_ACTIONS) | set(_EXTENDED_ACTIONS)  # noqa: SLF001
    )
    self_action._unmatched_self_action = _unmatched_self_action  # noqa: SLF001
    setattr(self_action, _SELF_FLAG, True)
    return True


def _patch_guardian() -> bool:
    if bool(getattr(guardian, _GUARDIAN_FLAG, False)):
        return False
    guardian._ACTIONS.update(_EXTENDED_ACTIONS)  # noqa: SLF001
    for name in _STRICT_ACTIONS:
        spec = guardian._ACTIONS.get(name)  # noqa: SLF001
        if spec is not None:
            label, threshold_key, counter_key, _override = spec
            guardian._ACTIONS[name] = (  # noqa: SLF001
                label,
                threshold_key,
                counter_key,
                1,
            )
    guardian._GUILD_UPDATE_SECURITY_FIELDS = frozenset(  # noqa: SLF001
        set(guardian._GUILD_UPDATE_SECURITY_FIELDS) | set(_GUILD_SECURITY_FIELDS)  # noqa: SLF001
    )
    guardian._PANIC_WEIGHTS.update(  # noqa: SLF001
        {name: (4 if name in _STRICT_ACTIONS else 2) for name in _EXTENDED_ACTIONS}
    )
    guardian._PANIC_ACTIONS = frozenset(guardian._PANIC_WEIGHTS)  # noqa: SLF001
    guardian._PANIC_SEVERE_ACTIONS = frozenset(  # noqa: SLF001
        set(guardian._PANIC_SEVERE_ACTIONS) | set(_STRICT_ACTIONS)  # noqa: SLF001
    )
    setattr(guardian, _GUARDIAN_FLAG, True)
    return True


def _patch_health(bot: discord.Client) -> bool:
    if bool(getattr(anti_nuke, _HEALTH_FLAG, False)):
        return False
    original = anti_nuke.antinuke_permission_health

    def wrapped(
        guild: Any,
        settings: Optional[Mapping[str, Any]] = None,
    ) -> list[str]:
        missing = list(original(guild, settings))
        clean = anti_nuke.normalize_antinuke_settings(settings or {})
        if not _enabled_contain(clean):
            return missing
        if str(getattr(discord, "__version__", "") or "") != SUPPORTED_DISCORD_PY:
            missing.append(f"discord.py {SUPPORTED_DISCORD_PY} runtime contract")
        if not bool(
            getattr(
                getattr(bot, "http", None),
                self_action._HTTP_PATCH_FLAG,  # noqa: SLF001
                False,
            )
        ):
            missing.append("AntiNuke self-action HTTP proof")
        if not bool(
            getattr(
                discord.Webhook,
                self_action._WEBHOOK_PATCH_FLAG,  # noqa: SLF001
                False,
            )
        ):
            missing.append("AntiNuke webhook self-action proof")
        return list(dict.fromkeys(missing))

    anti_nuke.antinuke_permission_health = wrapped
    setattr(anti_nuke, _HEALTH_FLAG, True)
    return True


async def _reconcile_quarantine(bot: discord.Client) -> None:
    for guild in list(getattr(bot, "guilds", []) or []):
        gid = _safe_int(getattr(guild, "id", 0), 0)
        if gid > 0 and await quarantine_active(gid):
            await attempt_quarantine_ejection(
                bot,
                guild,
                "durable compromise quarantine",
            )


def install_anti_nuke_zero_damage_runtime(bot: discord.Client) -> bool:
    if bool(getattr(bot, _INSTALL_FLAG, False)):
        return False
    self_patched = _patch_self_action()
    guardian_patched = _patch_guardian()
    health_patched = _patch_health(bot)

    async def on_ready() -> None:
        await _reconcile_quarantine(bot)

    async def on_guild_join(guild: Any) -> None:
        gid = _safe_int(getattr(guild, "id", 0), 0)
        if gid > 0 and await quarantine_active(gid):
            await attempt_quarantine_ejection(
                bot,
                guild,
                "durable compromise quarantine",
            )

    bot.add_listener(on_ready, "on_ready")
    bot.add_listener(on_guild_join, "on_guild_join")
    setattr(bot, _INSTALL_FLAG, True)
    print(
        "🛡️ AntiNuke zero-damage audit hardening active: "
        f"self={'hardened' if self_patched else 'ready'}; "
        f"surface={'expanded' if guardian_patched else 'ready'}; "
        "strict-actions=guardian-scoped; "
        f"health={'strict' if health_patched else 'ready'}; "
        f"discord.py={SUPPORTED_DISCORD_PY}"
    )
    return True


__all__ = [
    "SUPPORTED_DISCORD_PY",
    "attempt_quarantine_ejection",
    "install_anti_nuke_zero_damage_runtime",
    "quarantine_active",
]
