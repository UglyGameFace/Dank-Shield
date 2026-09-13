from __future__ import annotations

"""Require strict preventive readiness before AntiNuke contain mode can be armed."""

from typing import Any, Mapping, Optional

import discord

from . import anti_nuke
from .anti_nuke_readiness_strict import delegated_authority_blockers

_INSTALL_FLAG = "_dank_antinuke_readiness_gate_installed"
_HEALTH_FLAG = "_dank_antinuke_readiness_gate_health_patched"
_SAVE_FLAG = "_dank_antinuke_readiness_gate_save_patched"


def _enabled_contain(settings: Mapping[str, Any] | None) -> bool:
    if not isinstance(settings, Mapping):
        return False
    enabled = settings.get("antinuke_enabled", False)
    if not isinstance(enabled, bool):
        enabled = str(enabled or "").strip().lower() in {
            "1", "true", "yes", "on", "enabled"
        }
    return bool(enabled) and str(
        settings.get("antinuke_mode") or "contain"
    ).strip().lower() == "contain"


def _patch_health() -> bool:
    if bool(getattr(anti_nuke, _HEALTH_FLAG, False)):
        return False
    original = anti_nuke.antinuke_permission_health

    def wrapped(
        guild: discord.Guild,
        settings: Optional[Mapping[str, Any]] = None,
    ) -> list[str]:
        missing = list(original(guild, settings))
        clean = anti_nuke.normalize_antinuke_settings(settings or {})
        if _enabled_contain(clean):
            missing.extend(delegated_authority_blockers(guild))
        return list(dict.fromkeys(missing))

    anti_nuke.antinuke_permission_health = wrapped
    setattr(anti_nuke, _HEALTH_FLAG, True)
    return True


def _patch_save(bot: discord.Client) -> bool:
    if bool(getattr(anti_nuke, _SAVE_FLAG, False)):
        return False
    original = anti_nuke.save_antinuke_settings

    async def wrapped(guild_id: int, patch: Mapping[str, Any]) -> dict[str, Any]:
        gid = int(guild_id)
        current = await anti_nuke.get_antinuke_settings(gid)
        candidate = anti_nuke.normalize_antinuke_settings(
            {**current, **dict(patch or {})}
        )
        getter = getattr(bot, "get_guild", None)
        guild = getter(gid) if callable(getter) else None
        if guild is not None and _enabled_contain(candidate):
            blockers = delegated_authority_blockers(guild)
            if blockers:
                raise RuntimeError(
                    "AntiNuke contain mode cannot be armed while delegated "
                    "AntiNuke-risk authority remains: " + "; ".join(blockers[:6])
                )
        return await original(gid, patch)

    anti_nuke.save_antinuke_settings = wrapped
    setattr(anti_nuke, _SAVE_FLAG, True)
    return True


def install_anti_nuke_readiness_gate_runtime(bot: discord.Client) -> bool:
    if bool(getattr(bot, _INSTALL_FLAG, False)):
        return False
    health = _patch_health()
    save = _patch_save(bot)
    setattr(bot, _INSTALL_FLAG, True)
    print(
        "🔒 AntiNuke strict readiness gate active: contain mode requires no "
        "delegated AntiNuke-risk authority; "
        f"health={'patched' if health else 'ready'}; "
        f"save={'patched' if save else 'ready'}"
    )
    return True


__all__ = ["install_anti_nuke_readiness_gate_runtime"]
