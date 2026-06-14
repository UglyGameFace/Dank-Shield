from __future__ import annotations

"""Make permission repair lock staff-controlled VC verification.

Health already treats free @everyone/Unverified connect access as unsafe. This
small guard aligns the repair baseline with that health rule without rewriting
the whole permission repair system.
"""

from typing import Optional

import discord

_DONE = False


def _bot_member(guild: discord.Guild) -> Optional[discord.Member]:
    try:
        return guild.me
    except Exception:
        return None


def _locked_voice_verify_overwrites(
    guild: discord.Guild,
    *,
    staff_role: Optional[discord.Role],
    control_role: Optional[discord.Role],
    unverified_role: Optional[discord.Role],
    verified_role: Optional[discord.Role],
    resident_role: Optional[discord.Role],
) -> dict[object, discord.PermissionOverwrite]:
    ow: dict[object, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=False, speak=False),
    }
    me = _bot_member(guild)
    if me:
        ow[me] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, move_members=True, manage_channels=True)
    for role in (unverified_role, verified_role, resident_role):
        if role and not role.is_default():
            ow[role] = discord.PermissionOverwrite(view_channel=True, connect=False, speak=False)
    for role in (staff_role, control_role):
        if role and not role.is_default():
            ow[role] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, move_members=True)
    return ow


def apply() -> bool:
    global _DONE
    if _DONE:
        return True
    try:
        from stoney_verify.startup_guards import setup_permission_repair_guard as repair
        repair._voice_verify_overwrites = _locked_voice_verify_overwrites
        _DONE = True
        print("🛠️ setup_vc_lock_repair_guard active; permission repair locks VC verification connect access")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ setup_vc_lock_repair_guard failed: {exc!r}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
