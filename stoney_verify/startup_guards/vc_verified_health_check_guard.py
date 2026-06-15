from __future__ import annotations

"""Report whether the saved verified role can use the saved VC channel."""

from typing import Any

import discord

_PATCHED = False
_ORIGINAL_HEALTH: Any = None


def _num(value: Any) -> int:
    try:
        return int(str(value or "0").strip() or 0)
    except Exception:
        return 0


def _get(cfg: Any, *keys: str) -> Any:
    for key in keys:
        try:
            if hasattr(cfg, "get"):
                value = cfg.get(key)
                if value not in (None, "", 0, "0"):
                    return value
        except Exception:
            pass
        try:
            value = getattr(cfg, key, None)
            if value not in (None, "", 0, "0"):
                return value
        except Exception:
            pass
        for bucket in ("settings", "config", "metadata", "meta"):
            try:
                nested = getattr(cfg, bucket, None)
                if isinstance(nested, dict) and nested.get(key) not in (None, "", 0, "0"):
                    return nested.get(key)
            except Exception:
                pass
    return None


async def _line(guild: discord.Guild) -> tuple[bool, str]:
    try:
        from stoney_verify.guild_config import get_guild_config
        cfg = await get_guild_config(int(guild.id), refresh=True)
        role = guild.get_role(_num(_get(cfg, "verified_role_id", "member_role_id", "approved_role_id")))
        channel = guild.get_channel(_num(_get(cfg, "vc_verify_channel_id", "vc_verify_vc_id", "voice_verify_channel_id")))
        if role is None:
            return False, "Verified role is missing or not saved."
        if not isinstance(channel, discord.VoiceChannel):
            return False, "VC verification channel is missing or not a voice channel."
        perms = channel.permissions_for(role)
        missing = []
        if not perms.view_channel:
            missing.append("View Channel")
        if not perms.connect:
            missing.append("Connect")
        if not perms.speak:
            missing.append("Speak")
        if missing:
            return False, f"Verified role {role.mention} is missing in {channel.mention}: {', '.join(missing)}."
        return True, f"Verified role {role.mention} can use {channel.mention}."
    except Exception as exc:
        return False, f"Verified VC check failed: `{type(exc).__name__}: {str(exc)[:220]}`"


def apply() -> bool:
    global _PATCHED, _ORIGINAL_HEALTH
    if _PATCHED:
        return True
    try:
        from stoney_verify.commands_ext import public_setup_solid
        _ORIGINAL_HEALTH = public_setup_solid._build_health_embed

        async def patched_health(guild: discord.Guild) -> discord.Embed:
            embed = await _ORIGINAL_HEALTH(guild)
            ok, text = await _line(guild)
            embed.add_field(name="✅ Verified VC Access" if ok else "⚠️ Verified VC Access", value=text[:1024], inline=False)
            return embed

        public_setup_solid._build_health_embed = patched_health
        _PATCHED = True
        print("✅ vc_verified_health_check_guard active; setup health reports verified VC access")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ vc_verified_health_check_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]