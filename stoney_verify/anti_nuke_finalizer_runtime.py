from __future__ import annotations

"""Final AntiNuke runtime invariants that must hold before app import.

This module does not own AntiNuke policy. It closes two boot-time integrity gaps:
contain-mode readiness must include the authority required to restore Discord
AutoMod state, and the audit gateway must be the sole channel-create attribution
owner when the moderation audit gateway is actually available.
"""

from typing import Any, Mapping, Optional

import discord

from . import anti_nuke
from . import anti_nuke_gateway_runtime as gateway
from . import anti_nuke_guardian_runtime as guardian

_INSTALL_FLAG = "_dank_antinuke_finalizer_runtime_installed"
_HEALTH_PATCH_FLAG = "_dank_antinuke_manage_guild_health_patched"


def _patch_permission_health() -> bool:
    """Make contain-mode readiness prove AutoMod restoration authority."""

    if bool(getattr(anti_nuke, _HEALTH_PATCH_FLAG, False)):
        return False

    original = anti_nuke.antinuke_permission_health

    def wrapped(
        guild: discord.Guild,
        settings: Optional[Mapping[str, Any]] = None,
    ) -> list[str]:
        missing = list(original(guild, settings))
        clean = anti_nuke.normalize_antinuke_settings(settings or {})
        if not clean["antinuke_enabled"] or clean["antinuke_mode"] != "contain":
            return missing

        member = getattr(guild, "me", None)
        permissions = getattr(member, "guild_permissions", None)
        if permissions is None:
            return missing

        has_manage_guild = bool(
            getattr(permissions, "manage_guild", False)
            or getattr(permissions, "administrator", False)
        )
        if not has_manage_guild and "Manage Server" not in missing:
            missing.append("Manage Server")
        return missing

    anti_nuke.antinuke_permission_health = wrapped
    setattr(anti_nuke, _HEALTH_PATCH_FLAG, True)
    return True


def _retire_legacy_channel_create_owner(bot: discord.Client) -> bool:
    """Prevent REST/native and gateway paths from racing for channel-create audit IDs.

    The native listener is kept when the moderation audit gateway is unavailable.
    When the gateway runtime is active, guardian REST reconciliation already covers
    sparse gateway actor payloads, so retaining the native listener only creates a
    consume-before-rollback race.
    """

    moderation = bool(getattr(getattr(bot, "intents", None), "moderation", False))
    gateway_ready = bool(
        getattr(bot, gateway._INSTALL_FLAG, False)  # noqa: SLF001
        or getattr(bot, guardian._INSTALL_FLAG, False)  # noqa: SLF001
    )
    if not moderation or not gateway_ready:
        return False

    remover = getattr(bot, "remove_listener", None)
    legacy = getattr(anti_nuke, "antinuke_on_guild_channel_create", None)
    if not callable(remover) or not callable(legacy):
        return False

    try:
        remover(legacy, "on_guild_channel_create")
    except Exception:
        return False
    return True


def install_anti_nuke_finalizer_runtime(bot: discord.Client) -> bool:
    if bool(getattr(bot, _INSTALL_FLAG, False)):
        return False

    health_patched = _patch_permission_health()
    channel_owner_retired = _retire_legacy_channel_create_owner(bot)
    setattr(bot, _INSTALL_FLAG, True)

    print(
        "🛡️ AntiNuke finalizer active: "
        f"manage-server readiness={'patched' if health_patched else 'already native'}; "
        f"channel-create owner={'gateway' if channel_owner_retired else 'native fallback preserved'}"
    )
    return True


__all__ = ["install_anti_nuke_finalizer_runtime"]
