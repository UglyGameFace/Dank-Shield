from __future__ import annotations

"""Final AntiNuke runtime invariants that must hold before app import.

This module does not own AntiNuke policy. It closes boot-time integrity gaps:
contain-mode readiness must include the authority required to restore Discord
AutoMod state, managed integration roles must only block readiness when their
holders are actually uncontainable, and the audit gateway must be the sole
channel-create attribution owner when the moderation audit gateway is available.
"""

from typing import Any, Mapping, Optional

import discord

from . import anti_nuke
from . import anti_nuke_gateway_runtime as gateway
from . import anti_nuke_guardian_runtime as guardian

_INSTALL_FLAG = "_dank_antinuke_finalizer_runtime_installed"
_HEALTH_PATCH_FLAG = "_dank_antinuke_manage_guild_health_patched"
_MANAGED_ROLE_PATCH_FLAG = "_dank_antinuke_managed_role_readiness_patched"
_MANAGED_OVERWRITE_PATCH_FLAG = "_dank_antinuke_managed_overwrite_readiness_patched"


def _managed_role_is_safely_containable(guild: discord.Guild, role: Any) -> bool:
    """Return whether a managed role can be neutralized by removing its holders."""

    holders = [
        found
        for found in list(getattr(role, "members", []) or [])
        if not anti_nuke._actor_is_owner_or_bot(guild, found)  # noqa: SLF001
    ]
    return not holders or all(
        anti_nuke._member_is_manageable_by_bot(guild, found)  # noqa: SLF001
        for found in holders
    )


def _patch_managed_role_readiness() -> bool:
    """Ignore managed-role blockers only when every matching holder is containable."""

    if bool(getattr(anti_nuke, _MANAGED_ROLE_PATCH_FLAG, False)):
        return False

    original = anti_nuke._dangerous_hierarchy_blockers  # noqa: SLF001

    def wrapped(
        guild: discord.Guild,
        member: Any,
        settings: Mapping[str, Any],
    ) -> list[str]:
        blockers = list(original(guild, member, settings))
        safe_labels: set[str] = set()
        unsafe_labels: set[str] = set()

        for role in list(getattr(guild, "roles", []) or []):
            if not anti_nuke.role_has_dangerous_permissions(role):
                continue
            if anti_nuke._role_is_default(role):  # noqa: SLF001
                continue
            if not anti_nuke._role_is_managed(role):  # noqa: SLF001
                continue

            role_name = str(
                getattr(role, "name", "dangerous-role") or "dangerous-role"
            )
            label = f"managed @{role_name} cannot be stripped"
            if _managed_role_is_safely_containable(guild, role):
                safe_labels.add(label)
            else:
                unsafe_labels.add(label)

        removable = safe_labels - unsafe_labels
        if not removable:
            return blockers
        return [item for item in blockers if item not in removable]

    anti_nuke._dangerous_hierarchy_blockers = wrapped  # noqa: SLF001
    setattr(anti_nuke, _MANAGED_ROLE_PATCH_FLAG, True)
    return True


def _patch_managed_overwrite_readiness() -> bool:
    """Apply the same containment truth to dangerous managed-role overwrites."""

    if bool(getattr(anti_nuke, _MANAGED_OVERWRITE_PATCH_FLAG, False)):
        return False

    original = anti_nuke._dangerous_overwrite_blockers  # noqa: SLF001

    def wrapped(guild: discord.Guild, bot_member: Any) -> list[str]:
        blockers = list(original(guild, bot_member))
        safe_labels: set[str] = set()
        unsafe_labels: set[str] = set()

        for channel in list(getattr(guild, "channels", []) or []):
            overwrites = getattr(channel, "overwrites", None)
            if not hasattr(overwrites, "items"):
                continue
            try:
                items = list(overwrites.items())
            except Exception:
                continue

            channel_name = str(getattr(channel, "name", "channel") or "channel")
            for role, overwrite in items:
                if hasattr(role, "roles") and hasattr(role, "top_role"):
                    continue
                if not anti_nuke._overwrite_grants_dangerous_permissions(overwrite):  # noqa: SLF001
                    continue
                if anti_nuke._role_is_default(role):  # noqa: SLF001
                    continue
                if not anti_nuke._role_is_managed(role):  # noqa: SLF001
                    continue

                role_name = str(getattr(role, "name", "role") or "role")
                label = (
                    f"#{channel_name} grants dangerous permissions to "
                    f"unmanageable @{role_name}"
                )
                if _managed_role_is_safely_containable(guild, role):
                    safe_labels.add(label)
                else:
                    unsafe_labels.add(label)

        removable = safe_labels - unsafe_labels
        if not removable:
            return blockers
        return [item for item in blockers if item not in removable]

    anti_nuke._dangerous_overwrite_blockers = wrapped  # noqa: SLF001
    setattr(anti_nuke, _MANAGED_OVERWRITE_PATCH_FLAG, True)
    return True


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
    """Prevent REST/native and gateway paths from racing for channel-create audit IDs."""

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

    managed_role_patched = _patch_managed_role_readiness()
    managed_overwrite_patched = _patch_managed_overwrite_readiness()
    health_patched = _patch_permission_health()
    channel_owner_retired = _retire_legacy_channel_create_owner(bot)
    setattr(bot, _INSTALL_FLAG, True)

    print(
        "🛡️ AntiNuke finalizer active: "
        f"managed-role readiness={'patched' if managed_role_patched else 'already native'}; "
        f"managed-overwrite readiness={'patched' if managed_overwrite_patched else 'already native'}; "
        f"manage-server readiness={'patched' if health_patched else 'already native'}; "
        f"channel-create owner={'gateway' if channel_owner_retired else 'native fallback preserved'}"
    )
    return True


__all__ = ["install_anti_nuke_finalizer_runtime"]
