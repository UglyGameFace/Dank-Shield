from __future__ import annotations

"""Canonical service-selection and completion state for ``/dank setup``.

Every owner-facing setup screen must read this module instead of inventing its
own defaults or aliases. The live guild configuration remains authoritative;
this module normalizes that configuration and serializes owner edits so rapid
Discord interactions cannot overwrite one another with stale state.
"""

import asyncio
from dataclasses import dataclass
from typing import Any, Mapping

from .globals import now_utc
from .guild_config import get_guild_config, invalidate_guild_config


_SERVICE_KEYS = (
    "tickets_enabled",
    "verification_enabled",
    "voice_verification_enabled",
    "id_verify_enabled",
    "spam_guard_enabled",
    "moderation_enabled",
)
_SERVICE_STATE_LOCKS: dict[tuple[int, int], asyncio.Lock] = {}


def _service_state_lock(guild_id: int) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    key = (id(loop), int(guild_id))
    lock = _SERVICE_STATE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _SERVICE_STATE_LOCKS[key] = lock
    return lock


@dataclass(frozen=True)
class SetupServiceState:
    setup_choice: str
    setup_label: str
    tickets: bool
    simple_verify: bool
    voice_verify: bool
    id_verify: bool
    spam_guard: bool
    logs: bool
    completed: bool = False
    completed_at: str = ""
    source: str = "guild_config"

    @property
    def verification_enabled(self) -> bool:
        return bool(self.simple_verify or self.voice_verify or self.id_verify)

    @property
    def verification(self) -> bool:
        """Compatibility alias for the public one-button verification switch."""
        return bool(self.simple_verify)

    @property
    def voice(self) -> bool:
        return bool(self.voice_verify)

    @property
    def spamguard(self) -> bool:
        return bool(self.spam_guard)

    @property
    def moderation(self) -> bool:
        return bool(self.logs)

    @property
    def any_enabled(self) -> bool:
        return bool(
            self.tickets
            or self.verification_enabled
            or self.spam_guard
            or self.logs
        )

    def as_service_payload(self) -> dict[str, bool]:
        return {
            "tickets_enabled": bool(self.tickets),
            "verification_enabled": bool(self.simple_verify),
            "voice_verification_enabled": bool(self.voice_verify),
            "id_verify_enabled": bool(self.id_verify),
            "spam_guard_enabled": bool(self.spam_guard),
            "moderation_enabled": bool(self.logs),
        }

    def as_payload(self) -> dict[str, bool]:
        return self.as_service_payload()

    def enabled_labels(self) -> list[str]:
        labels: list[str] = []
        if self.tickets:
            labels.append("Tickets")
        if self.simple_verify:
            labels.append("Simple Verify")
        if self.voice_verify:
            labels.append("Voice Verify")
        if self.id_verify:
            labels.append("ID/Web Verify")
        if self.spam_guard:
            labels.append("SpamGuard")
        if self.logs:
            labels.append("Logs")
        return labels


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return bool(default)
    try:
        text = str(value).strip().lower()
    except Exception:
        return bool(default)
    if text in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "n", "off", "disabled", ""}:
        return False
    return bool(default)


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    if cfg is None:
        return default
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
    for bucket in ("settings", "config", "metadata", "meta"):
        try:
            nested = cfg.get(bucket) if hasattr(cfg, "get") else getattr(cfg, bucket, None)
            if isinstance(nested, Mapping) and nested.get(key) is not None:
                return nested.get(key)
        except Exception:
            continue
    return default


def _first_bool(
    cfg: Any,
    keys: tuple[str, ...],
    *,
    default: bool,
) -> tuple[bool, bool]:
    for key in keys:
        value = _cfg_value(cfg, key, None)
        if value is not None:
            return _safe_bool(value, default), True
    return bool(default), False


def _choice_defaults(choice: str) -> dict[str, bool]:
    choice = str(choice or "").strip().lower()
    defaults = {
        "tickets": False,
        "simple_verify": False,
        "voice_verify": False,
        "id_verify": False,
        "spam_guard": False,
        "logs": False,
    }
    if choice in {"basic_server", "help_desk"}:
        defaults.update(tickets=True, spam_guard=True, logs=True)
    elif choice == "basic_verify":
        defaults.update(simple_verify=True, spam_guard=True, logs=True)
    elif choice == "voice_check":
        defaults.update(tickets=True, voice_verify=True, spam_guard=True, logs=True)
    elif choice == "id_check":
        defaults.update(tickets=True, id_verify=True, spam_guard=True, logs=True)
    elif choice == "id_voice_check":
        defaults.update(
            tickets=True,
            voice_verify=True,
            id_verify=True,
            spam_guard=True,
            logs=True,
        )
    return defaults


def service_state_from_config(cfg: Any) -> SetupServiceState:
    choice = str(_cfg_value(cfg, "setup_choice", "") or "").strip().lower()
    label = str(_cfg_value(cfg, "setup_choice_label", "") or "").strip()
    defaults = _choice_defaults(choice)

    tickets, _ = _first_bool(
        cfg,
        ("tickets_enabled", "ticket_service_enabled", "ticketing_enabled"),
        default=defaults["tickets"],
    )
    voice, _ = _first_bool(
        cfg,
        (
            "voice_verification_enabled",
            "vc_verify_enabled",
            "voice_verify_enabled",
            "verification_allows_voice",
        ),
        default=defaults["voice_verify"],
    )
    id_verify, _ = _first_bool(
        cfg,
        (
            "id_verify_enabled",
            "web_verify_enabled",
            "id_web_verify_enabled",
            "verification_requires_id",
        ),
        default=defaults["id_verify"],
    )
    simple, simple_explicit = _first_bool(
        cfg,
        ("basic_verify_enabled", "basic_button_verify_enabled"),
        default=defaults["simple_verify"],
    )
    if not simple_explicit:
        aggregate = _cfg_value(cfg, "verification_enabled", None)
        if aggregate is not None and (
            choice in {"custom_setup", "basic_verify"}
            or (not choice and not voice and not id_verify)
        ):
            simple = _safe_bool(aggregate, defaults["simple_verify"])

    spam_guard, _ = _first_bool(
        cfg,
        ("spam_guard_enabled",),
        default=defaults["spam_guard"],
    )
    logs, _ = _first_bool(
        cfg,
        ("logs_enabled", "moderation_enabled"),
        default=defaults["logs"],
    )

    if voice or id_verify:
        tickets = True
        logs = True
    if spam_guard:
        logs = True

    return SetupServiceState(
        setup_choice=choice,
        setup_label=label or choice.replace("_", " ").title() or "Not chosen yet",
        tickets=bool(tickets),
        simple_verify=bool(simple),
        voice_verify=bool(voice),
        id_verify=bool(id_verify),
        spam_guard=bool(spam_guard),
        logs=bool(logs),
        completed=_safe_bool(_cfg_value(cfg, "setup_completed", False), False),
        completed_at=str(_cfg_value(cfg, "setup_completed_at", "") or "").strip(),
        source=str(_cfg_value(cfg, "config_last_write_source", "guild_config") or "guild_config"),
    )


async def load_setup_service_state(guild_id: int) -> SetupServiceState:
    cfg = await get_guild_config(int(guild_id), refresh=True)
    return service_state_from_config(cfg)


def _verification_mode(*, simple: bool, voice: bool, id_verify: bool) -> tuple[str, str]:
    if id_verify and voice:
        return "id_voice_check", "id_voice_check"
    if id_verify:
        return "id_check", "id_check"
    if voice:
        return "voice_check", "voice_check"
    if simple:
        return "basic_verify", "basic_button"
    return "none", "none"


def normalize_custom_service_patch(
    payload: Mapping[str, Any],
    *,
    allow_id_verify: bool = False,
) -> dict[str, Any]:
    tickets = _safe_bool(payload.get("tickets_enabled"), False)
    simple = _safe_bool(payload.get("verification_enabled"), False)
    voice = _safe_bool(payload.get("voice_verification_enabled"), False)
    id_verify = bool(
        allow_id_verify
        and _safe_bool(payload.get("id_verify_enabled"), False)
    )
    spam_guard = _safe_bool(payload.get("spam_guard_enabled"), False)
    logs = _safe_bool(payload.get("moderation_enabled"), False)

    if voice or id_verify:
        tickets = True
        logs = True
    if spam_guard:
        logs = True

    enabled: list[str] = []
    if tickets:
        enabled.append("Tickets")
    if simple:
        enabled.append("Simple Verify")
    if voice:
        enabled.append("Voice Verify")
    if id_verify:
        enabled.append("ID/Web Verify")
    if spam_guard:
        enabled.append("SpamGuard")
    if logs:
        enabled.append("Logs")
    label = "Your features: " + (", ".join(enabled) if enabled else "No features selected")
    panel_style, mode = _verification_mode(
        simple=simple,
        voice=voice,
        id_verify=id_verify,
    )

    return {
        "tickets_enabled": tickets,
        "ticket_service_enabled": tickets,
        "verification_enabled": simple,
        "basic_verify_enabled": simple,
        "basic_button_verify_enabled": simple,
        "voice_verification_enabled": voice,
        "vc_verify_enabled": voice,
        "voice_verify_enabled": voice,
        "verification_allows_voice": voice,
        "id_verify_enabled": id_verify,
        "web_verify_enabled": id_verify,
        "id_web_verify_enabled": id_verify,
        "verification_requires_id": id_verify,
        "spam_guard_enabled": spam_guard,
        "moderation_enabled": logs,
        "logs_enabled": logs,
        "verification_panel_style": panel_style,
        "verification_mode": mode,
        "verify_mode": mode,
        "setup_choice": "custom_setup",
        "setup_choice_label": label,
        "setup_choice_description": "Custom feature choices.",
        "setup_choice_member_sees": label,
        "setup_completed": False,
    }


def apply_custom_service_toggle(
    payload: Mapping[str, Any],
    key: str,
    *,
    allow_id_verify: bool = False,
) -> tuple[dict[str, bool], bool, bool, str]:
    clean = {
        service_key: _safe_bool(payload.get(service_key), False)
        for service_key in _SERVICE_KEYS
    }
    if not allow_id_verify:
        clean["id_verify_enabled"] = False
    if key not in clean or (key == "id_verify_enabled" and not allow_id_verify):
        return clean, False, False, "That core feature is not available for this server."

    next_value = not clean[key]
    if not next_value:
        required_by: list[str] = []
        if key == "tickets_enabled":
            if clean["voice_verification_enabled"]:
                required_by.append("Voice Verify")
            if clean["id_verify_enabled"]:
                required_by.append("ID/Web Verify")
        if key == "moderation_enabled":
            if clean["voice_verification_enabled"]:
                required_by.append("Voice Verify")
            if clean["id_verify_enabled"]:
                required_by.append("ID/Web Verify")
            if clean["spam_guard_enabled"]:
                required_by.append("SpamGuard")
        if required_by:
            dependency = " and ".join(required_by)
            label = {
                "tickets_enabled": "Tickets",
                "moderation_enabled": "Essential Logs",
            }[key]
            return (
                clean,
                True,
                False,
                f"**{dependency}** needs **{label}**. Turn the dependent feature off first.",
            )

    clean[key] = next_value
    dependency_note = ""
    enabled_for_dependency: list[str] = []
    if key in {"voice_verification_enabled", "id_verify_enabled"} and next_value:
        for dependency_key, label in (
            ("tickets_enabled", "Tickets"),
            ("moderation_enabled", "Essential Logs"),
        ):
            if not clean[dependency_key]:
                enabled_for_dependency.append(label)
                clean[dependency_key] = True
        if enabled_for_dependency:
            feature_label = (
                "Voice Verify"
                if key == "voice_verification_enabled"
                else "ID/Web Verify"
            )
            dependency_note = (
                f"{feature_label} needs Tickets and Essential Logs, so Dank Shield also turned on: **"
                + "**, **".join(enabled_for_dependency)
                + "**."
            )
    elif key == "spam_guard_enabled" and next_value and not clean["moderation_enabled"]:
        clean["moderation_enabled"] = True
        dependency_note = (
            "SpamGuard needs Essential Logs, so Dank Shield also turned on "
            "**Essential Logs**."
        )

    return clean, bool(clean[key]), True, dependency_note


async def _save_custom_service_state_unlocked(
    guild_id: int,
    payload: Mapping[str, Any],
    *,
    actor: Any = None,
    allow_id_verify: bool = False,
) -> SetupServiceState:
    from .commands_ext.public_setup_config_writer import upsert_guild_config

    final: dict[str, Any] = normalize_custom_service_patch(
        payload,
        allow_id_verify=allow_id_verify,
    )
    final.update(
        {
            "setup_service_mode_saved_at": now_utc().isoformat(),
            "__config_write_mode": "setup_builder",
            "__config_write_source": "/dank setup feature picker",
        }
    )
    if actor is not None:
        final["configured_by_id"] = str(getattr(actor, "id", "") or "")
        final["configured_by_name"] = str(actor)

    saved = await upsert_guild_config(int(guild_id), final)
    invalidate_guild_config(int(guild_id))
    return service_state_from_config(saved)


async def save_custom_service_state(
    guild_id: int,
    payload: Mapping[str, Any],
    *,
    actor: Any = None,
    allow_id_verify: bool = False,
) -> SetupServiceState:
    async with _service_state_lock(int(guild_id)):
        return await _save_custom_service_state_unlocked(
            int(guild_id),
            payload,
            actor=actor,
            allow_id_verify=allow_id_verify,
        )


async def toggle_custom_service_state(
    guild_id: int,
    key: str,
    *,
    actor: Any = None,
    allow_id_verify: bool = False,
    expected_current: bool | None = None,
) -> tuple[SetupServiceState, bool, bool, str]:
    """Atomically reload, validate freshness, toggle, and persist one choice."""
    async with _service_state_lock(int(guild_id)):
        current = await load_setup_service_state(int(guild_id))
        current_payload = current.as_payload()
        if str(key) not in current_payload:
            return current, False, False, "That core feature is not available for this server."
        current_value = bool(current_payload[str(key)])
        if expected_current is not None and current_value is not bool(expected_current):
            return (
                current,
                current_value,
                False,
                "This setup screen was out of date, so Dank Shield refreshed it without changing your saved choices.",
            )
        payload, effective, changed, note = apply_custom_service_toggle(
            current_payload,
            str(key),
            allow_id_verify=allow_id_verify,
        )
        if not changed:
            return current, effective, False, note
        saved = await _save_custom_service_state_unlocked(
            int(guild_id),
            payload,
            actor=actor,
            allow_id_verify=allow_id_verify,
        )
        return saved, effective, True, note


async def invalidate_setup_completion(
    guild_id: int,
    *,
    reason: str = "Setup configuration changed",
) -> None:
    from .commands_ext.public_setup_config_writer import upsert_guild_config

    await upsert_guild_config(
        int(guild_id),
        {
            "setup_completed": False,
            "setup_completion_invalidated_at": now_utc().isoformat(),
            "setup_completion_invalidated_reason": str(reason or "")[:300],
            "__config_write_mode": "explicit_override",
            "__config_write_source": "/dank setup completion invalidation",
        },
    )
    invalidate_guild_config(int(guild_id))


async def mark_setup_completed(guild_id: int, *, actor: Any = None) -> SetupServiceState:
    from .commands_ext.public_setup_config_writer import upsert_guild_config

    timestamp = now_utc().isoformat()
    payload: dict[str, Any] = {
        "setup_completed": True,
        "setup_completed_at": timestamp,
        "__config_write_mode": "explicit_override",
        "__config_write_source": "/dank setup finish",
    }
    if actor is not None:
        payload["setup_completed_by_id"] = str(getattr(actor, "id", "") or "")
        payload["setup_completed_by_name"] = str(actor)

    saved = await upsert_guild_config(int(guild_id), payload)
    invalidate_guild_config(int(guild_id))
    return service_state_from_config(saved)


__all__ = [
    "SetupServiceState",
    "apply_custom_service_toggle",
    "invalidate_setup_completion",
    "load_setup_service_state",
    "mark_setup_completed",
    "normalize_custom_service_patch",
    "save_custom_service_state",
    "service_state_from_config",
    "toggle_custom_service_state",
]
