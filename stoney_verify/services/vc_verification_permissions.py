from __future__ import annotations

"""Single writer for the baseline Voice Verify channel permission contract."""

from dataclasses import dataclass
from typing import Any, Mapping, Optional

import discord

from .setup_permission_policy import vc_verification_overwrites


@dataclass(frozen=True)
class VcPermissionReconcileResult:
    changed: tuple[str, ...]
    unchanged: tuple[str, ...]
    failed: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.failed


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


def _cfg_int(cfg: Any, *keys: str) -> int:
    for key in keys:
        try:
            value = int(str(_cfg_value(cfg, key, 0) or 0))
        except Exception:
            value = 0
        if value > 0:
            return value
    return 0


def _role(guild: discord.Guild, cfg: Any, *keys: str) -> Optional[discord.Role]:
    role = guild.get_role(_cfg_int(cfg, *keys))
    return role if isinstance(role, discord.Role) else None


def _target_label(target: Any) -> str:
    try:
        if getattr(target, "is_default", lambda: False)():
            return "@everyone"
    except Exception:
        pass
    return str(getattr(target, "mention", None) or getattr(target, "name", None) or getattr(target, "id", "target"))


async def reconcile_vc_verification_channel(
    guild: discord.Guild,
    channel: Optional[discord.abc.GuildChannel] = None,
    *,
    cfg: Any = None,
    reason: str = "Dank Shield Voice Verify permission reconciliation",
) -> VcPermissionReconcileResult:
    from stoney_verify.guild_config import get_guild_config

    if cfg is None:
        cfg = await get_guild_config(int(guild.id), refresh=True)
    if channel is None:
        channel_id = _cfg_int(
            cfg,
            "vc_verify_channel_id",
            "vc_verify_vc_id",
            "voice_verify_channel_id",
            "voice_verification_channel_id",
        )
        channel = guild.get_channel(channel_id) if channel_id > 0 else None
    voice_types: tuple[type, ...] = tuple(
        item
        for item in (discord.VoiceChannel, getattr(discord, "StageChannel", None))
        if isinstance(item, type)
    )
    if not isinstance(channel, voice_types):
        return VcPermissionReconcileResult((), (), ("Saved Voice Verify channel is missing or is not a voice/stage channel.",))

    staff_role = _role(guild, cfg, "staff_role_id", "ticket_staff_role_id", "support_role_id", "vc_staff_role_id")
    control_role = _role(guild, cfg, "server_control_role_id", "control_role_id", "perm_role_id", "bot_manager_role_id")
    unverified_role = _role(guild, cfg, "unverified_role_id", "waiting_role_id", "pending_role_id")
    verified_role = _role(guild, cfg, "verified_role_id", "approved_role_id")
    resident_role = _role(guild, cfg, "resident_role_id", "member_role_id")
    expected = vc_verification_overwrites(
        guild,
        staff_role=staff_role,
        control_role=control_role,
        unverified_role=unverified_role,
        verified_role=verified_role,
        resident_role=resident_role,
    )

    changed: list[str] = []
    unchanged: list[str] = []
    failed: list[str] = []
    for target, overwrite in expected.items():
        label = _target_label(target)
        try:
            current = channel.overwrites_for(target)
            if current.pair() == overwrite.pair():
                unchanged.append(label)
                continue
            await channel.set_permissions(target, overwrite=overwrite, reason=reason)
            changed.append(label)
        except Exception as exc:
            failed.append(f"{label}: {type(exc).__name__}: {str(exc)[:180]}")
    return VcPermissionReconcileResult(tuple(changed), tuple(unchanged), tuple(failed))


__all__ = ["VcPermissionReconcileResult", "reconcile_vc_verification_channel"]
