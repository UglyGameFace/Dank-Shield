from __future__ import annotations

"""
Service enablement gates.

This is the runtime companion to setup-health/service presets. Diagnostics are
not enough: if a guild has not enabled a service, live runtime paths should deny
that service politely instead of partially running or creating confusing state.
"""

from typing import Any, Dict, Optional

import discord

from .guild_config import get_guild_config


SERVICE_FLAGS: Dict[str, str] = {
    "tickets": "tickets_enabled",
    "verification": "verification_enabled",
    "voice_verification": "voice_verification_enabled",
    "moderation": "moderation_enabled",
}

SERVICE_LABELS: Dict[str, str] = {
    "tickets": "Tickets",
    "verification": "ID verification",
    "voice_verification": "Voice verification",
    "moderation": "Moderation/modlog",
}


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        raw = str(value or "").strip().lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
        return default
    except Exception:
        return default


def _default_for_service(service: str) -> bool:
    # Newly provisioned public guilds default to tickets-only. Keep the same
    # behavior when a legacy row lacks service flags.
    return str(service) == "tickets"


async def is_service_enabled(guild_id: int | str, service: str) -> bool:
    key = str(service or "").strip().lower()
    flag = SERVICE_FLAGS.get(key)
    if not flag:
        return False

    cfg = await get_guild_config(int(str(guild_id)))
    return _safe_bool(cfg.raw.get(flag), _default_for_service(key))


async def service_status(guild_id: int | str) -> Dict[str, bool]:
    cfg = await get_guild_config(int(str(guild_id)))
    return {
        service: _safe_bool(cfg.raw.get(flag), _default_for_service(service))
        for service, flag in SERVICE_FLAGS.items()
    }


def disabled_service_message(service: str) -> str:
    key = str(service or "").strip().lower()
    label = SERVICE_LABELS.get(key, key or "This service")
    return (
        f"❌ **{label} is not enabled for this server.**\n"
        "An admin can run `/setup-services` to enable it, then `/setup-targets` and `/setup-finish` to complete setup."
    )


async def send_disabled_service_interaction(
    interaction: Optional[discord.Interaction],
    service: str,
) -> bool:
    if interaction is None:
        return False

    try:
        content = disabled_service_message(service)
        allowed = discord.AllowedMentions.none()
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True, allowed_mentions=allowed)
        else:
            await interaction.response.send_message(content, ephemeral=True, allowed_mentions=allowed)
        return True
    except Exception:
        return False


class ServiceDisabled(RuntimeError):
    def __init__(self, service: str, *, responded: bool = False):
        self.service = str(service)
        self.responded = bool(responded)
        super().__init__(disabled_service_message(service))


__all__ = [
    "SERVICE_FLAGS",
    "SERVICE_LABELS",
    "ServiceDisabled",
    "disabled_service_message",
    "is_service_enabled",
    "send_disabled_service_interaction",
    "service_status",
]
