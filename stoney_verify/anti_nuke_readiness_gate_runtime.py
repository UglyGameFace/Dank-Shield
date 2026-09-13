from __future__ import annotations

"""Apply the delegated-authority readiness gate only to Strict Lockdown."""

from typing import Any, Mapping, Optional

import discord

from . import anti_nuke
from .anti_nuke_readiness_strict import delegated_authority_blockers

_INSTALL_FLAG = "_dank_antinuke_readiness_gate_installed"
_HEALTH_FLAG = "_dank_antinuke_readiness_gate_health_patched"
_SAVE_FLAG = "_dank_antinuke_readiness_gate_save_patched"
_WARNED_GUILDS: set[int] = set()
STRICT_LOCKDOWN_KEY = "antinuke_strict_lockdown"


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


def _strict_lockdown_requested(settings: Mapping[str, Any] | None) -> bool:
    if not isinstance(settings, Mapping):
        return False
    return _safe_bool(settings.get(STRICT_LOCKDOWN_KEY), False) and str(
        settings.get("antinuke_mode") or "contain"
    ).strip().lower() == "contain"


def _strict_lockdown_active(settings: Mapping[str, Any] | None) -> bool:
    return _enabled_contain(settings) and _strict_lockdown_requested(settings)


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
        if _strict_lockdown_active(clean):
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
        if guild is not None and _strict_lockdown_requested(candidate):
            blockers = delegated_authority_blockers(guild)
            if blockers:
                raise RuntimeError(
                    "Strict Lockdown cannot be enabled while delegated server "
                    "authority remains: " + "; ".join(blockers[:6])
                )
        return await original(gid, patch)

    anti_nuke.save_antinuke_settings = wrapped
    setattr(anti_nuke, _SAVE_FLAG, True)
    return True


async def _warn_preexisting_unsafe_guild(guild: discord.Guild) -> bool:
    """Surface Strict Lockdown configs whose permission model no longer qualifies."""

    gid = int(guild.id)
    try:
        settings = await anti_nuke.get_antinuke_settings(gid, refresh=True)
    except TypeError:
        settings = await anti_nuke.get_antinuke_settings(gid)
    except Exception as exc:
        print(
            "Security strict-readiness reconciliation failed "
            f"guild={gid} error={type(exc).__name__}: {exc}"
        )
        return False

    if not _strict_lockdown_active(settings):
        _WARNED_GUILDS.discard(gid)
        return False

    blockers = delegated_authority_blockers(guild)
    if not blockers:
        _WARNED_GUILDS.discard(gid)
        return False

    print(
        "Security Strict Lockdown readiness blocked "
        f"guild={gid} blockers={' | '.join(blockers[:8])}"
    )
    if gid in _WARNED_GUILDS:
        return True
    _WARNED_GUILDS.add(gid)

    try:
        await anti_nuke._post_incident(  # noqa: SLF001
            guild,
            title="Security Strict Lockdown Readiness Required",
            actor=getattr(guild, "owner", None),
            action_label="Strict Lockdown permission model needs attention",
            target_label="Server permission model",
            response_label=(
                "Normal containment remains available. Strict Lockdown stays "
                "unready until the listed delegated authority is removed."
            ),
            details=" • ".join(blockers[:8]),
        )
    except Exception:
        pass
    return True


def _install_reconciliation_listeners(bot: discord.Client) -> bool:
    adder = getattr(bot, "add_listener", None)
    if not callable(adder):
        return False

    async def on_ready() -> None:
        for guild in list(getattr(bot, "guilds", []) or []):
            await _warn_preexisting_unsafe_guild(guild)

    async def on_guild_join(guild: discord.Guild) -> None:
        await _warn_preexisting_unsafe_guild(guild)

    try:
        adder(on_ready, "on_ready")
        adder(on_guild_join, "on_guild_join")
    except Exception:
        return False
    return True


def install_anti_nuke_readiness_gate_runtime(bot: discord.Client) -> bool:
    if bool(getattr(bot, _INSTALL_FLAG, False)):
        return False
    health = _patch_health()
    save = _patch_save(bot)
    reconcile = _install_reconciliation_listeners(bot)
    setattr(bot, _INSTALL_FLAG, True)
    print(
        "Security readiness gate active: normal contain allows delegated staff; "
        "Strict Lockdown requires delegated-risk cleanup; "
        f"health={'patched' if health else 'ready'}; "
        f"save={'patched' if save else 'ready'}; "
        f"reconciliation={'active' if reconcile else 'unavailable'}"
    )
    return True


__all__ = [
    "STRICT_LOCKDOWN_KEY",
    "_strict_lockdown_active",
    "_strict_lockdown_requested",
    "install_anti_nuke_readiness_gate_runtime",
]
