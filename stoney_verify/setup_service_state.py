from __future__ import annotations

"""Canonical service-selection and completion state for ``/dank setup``.

Every owner-facing setup screen must read this module instead of inventing its
own defaults or aliases.  The live guild configuration remains authoritative;
this module only normalizes that configuration into one consistent view.
"""

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from .globals import now_utc
from .guild_config import get_guild_config, invalidate_guild_config


_SERVICE_KEYS = (
    "tickets_enabled",
    "verification_enabled",
    "voice_verification_enabled",
    "spam_guard_enabled",
    "moderation_enabled",
)


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
            "spam_guard_enabled": bool(self.spam_guard),
            "moderation_enabled": bool(self.logs),
        }

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
    """Return ``(value, explicitly_present)`` for the first saved alias."""

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
        defaults.update(
            tickets=True,
            voice_verify=True,
            spam_guard=True,
            logs=True,
        )
    elif choice == "id_check":
        defaults.update(
            tickets=True,
            id_verify=True,
            spam_guard=True,
            logs=True,
        )
    elif choice == "id_voice_check":
        defaults.update(
            tickets=True,
            voice_verify=True,
            id_verify=True,
            spam_guard=True,
            logs=True,
        )
    # custom_setup deliberately defaults every feature OFF.  Only explicitly
    # saved switches may turn custom features on.
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
        # In Custom Setup and Basic Verify this flag means the public one-button
        # verification feature.  For old unlabelled configs, treat it as Simple
        # Verify only when no specialised verification mode is selected.
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

    # These workflows require the supporting ticket/log infrastructure.  This
    # mirrors the actual guided setup requirements without pretending Simple
    # Verify is enabled when only ID/Web or Voice Verify was selected.
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


def normalize_custom_service_patch(payload: Mapping[str, Any]) -> dict[str, Any]:
    tickets = _safe_bool(payload.get("tickets_enabled"), False)
    simple = _safe_bool(payload.get("verification_enabled"), False)
    voice = _safe_bool(payload.get("voice_verification_enabled"), False)
    spam_guard = _safe_bool(payload.get("spam_guard_enabled"), False)
    logs = _safe_bool(payload.get("moderation_enabled"), False)

    if voice:
        simple = True
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
    if spam_guard:
        enabled.append("SpamGuard")
    if logs:
        enabled.append("Logs")
    label = "Your features: " + (", ".join(enabled) if enabled else "No features selected")

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
        "spam_guard_enabled": spam_guard,
        "moderation_enabled": logs,
        "logs_enabled": logs,
        "id_verify_enabled": False,
        "web_verify_enabled": False,
        "id_web_verify_enabled": False,
        "verification_requires_id": False,
        "verification_panel_style": (
            "voice_check" if voice else "basic_verify" if simple else "none"
        ),
        "verification_mode": (
            "voice_check" if voice else "basic_button" if simple else "none"
        ),
        "verify_mode": (
            "voice_check" if voice else "basic_button" if simple else "none"
        ),
        "setup_choice": "custom_setup",
        "setup_choice_label": label,
        "setup_choice_description": "Custom feature choices.",
        "setup_choice_member_sees": label,
        "setup_completed": False,
    }


async def save_custom_service_state(
    guild_id: int,
    payload: Mapping[str, Any],
    *,
    actor: Any = None,
) -> SetupServiceState:
    from .commands_ext.public_setup_config_writer import upsert_guild_config

    final: dict[str, Any] = normalize_custom_service_patch(payload)
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
    "load_setup_service_state",
    "mark_setup_completed",
    "normalize_custom_service_patch",
    "save_custom_service_state",
    "service_state_from_config",
]
