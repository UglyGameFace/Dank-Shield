from __future__ import annotations

"""Join verification role assignment service.

Events should decide *when* a member joined or changed state. This service owns
verification setup checks and assigning the configured Unverified role.
"""

import asyncio
import traceback
from typing import Any, Dict, Optional, Tuple

import discord

from .. import role_truth

try:
    from ..guild_config import get_guild_config, public_config_isolation_enabled
except Exception:  # pragma: no cover - import-order fallback
    get_guild_config = None  # type: ignore

    def public_config_isolation_enabled() -> bool:  # type: ignore
        return True


def _log(message: str) -> None:
    try:
        print(f"🧩 join_verification_service {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ join_verification_service {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _global_role_id(name: str) -> int:
    try:
        from stoney_verify import globals as bot_globals

        return _safe_int(getattr(bot_globals, name, 0), 0)
    except Exception:
        return 0


async def resolve_bot_member(
    guild: discord.Guild,
    *,
    bot_user_id: Optional[int] = None,
) -> Optional[discord.Member]:
    try:
        me = getattr(guild, "me", None)
        if isinstance(me, discord.Member):
            return me
    except Exception:
        pass

    try:
        if bot_user_id:
            fetched = await guild.fetch_member(int(bot_user_id))
            if isinstance(fetched, discord.Member):
                return fetched
    except Exception:
        pass

    return None


async def verification_role_ids_for_guild(guild: discord.Guild) -> Dict[str, int]:
    """Resolve verification role IDs for this guild without leaking home-guild globals."""
    try:
        if callable(get_guild_config):
            cfg = await get_guild_config(guild.id, force_refresh=False)  # type: ignore[misc]
            return {
                "unverified": _safe_int(cfg.get("unverified_role_id"), 0),
                "verified": _safe_int(cfg.get("verified_role_id"), 0),
                "resident": _safe_int(cfg.get("resident_role_id"), 0),
                "staff": _safe_int(cfg.get("staff_role_id"), 0),
                "stoner": _safe_int(cfg.get("stoner_role_id"), 0),
                "drunken": _safe_int(cfg.get("drunken_role_id"), 0),
            }
    except Exception as e:
        _warn(f"per-guild role config lookup failed guild={getattr(guild, 'id', 'unknown')} error={e!r}")

    allow_global = True
    try:
        if public_config_isolation_enabled():
            home_gid = _global_role_id("GUILD_ID")
            guild_id = _safe_int(getattr(guild, "id", 0), 0)
            allow_global = bool(home_gid > 0 and guild_id == home_gid)
    except Exception:
        allow_global = False

    if not allow_global:
        return {
            "unverified": 0,
            "verified": 0,
            "resident": 0,
            "staff": 0,
            "stoner": 0,
            "drunken": 0,
        }

    return {
        "unverified": _global_role_id("UNVERIFIED_ROLE_ID"),
        "verified": _global_role_id("VERIFIED_ROLE_ID"),
        "resident": _global_role_id("RESIDENT_ROLE_ID"),
        "staff": _global_role_id("STAFF_ROLE_ID"),
        "stoner": _global_role_id("STONER_ROLE_ID"),
        "drunken": _global_role_id("DRUNKEN_ROLE_ID"),
    }


async def verification_config_ready_for_guild(guild: discord.Guild) -> Tuple[bool, str]:
    role_ids = await verification_role_ids_for_guild(guild)
    uv_id = int(role_ids.get("unverified") or 0)
    if uv_id <= 0:
        return False, "No per-guild Unverified role configured. Setup must finish before join enforcement."

    try:
        role = guild.get_role(uv_id)
        if role is None:
            return False, f"Configured Unverified role {uv_id} does not exist in this guild."
    except Exception:
        return False, "Could not validate this guild's Unverified role."

    return True, "Verification config ready."


async def ensure_unverified_on_join(
    member: discord.Member,
    *,
    bot_user_id: Optional[int] = None,
) -> bool:
    try:
        if getattr(member, "bot", False):
            return False

        guild = member.guild
        role_ids = await verification_role_ids_for_guild(guild)
        uv_id = int(role_ids.get("unverified") or 0)
        safe_role_ids = [
            int(role_ids.get("verified") or 0),
            int(role_ids.get("resident") or 0),
            int(role_ids.get("staff") or 0),
            int(role_ids.get("stoner") or 0),
            int(role_ids.get("drunken") or 0),
        ]

        if not uv_id:
            _warn(f"Unverified role missing for guild={guild.id}; setup required before join enforcement.")
            return False

        role = guild.get_role(uv_id)
        if not role:
            _warn(f"UNVERIFIED_ROLE_ID not found in guild: {uv_id}")
            return False

        bot_member = await resolve_bot_member(guild, bot_user_id=bot_user_id)
        if not bot_member:
            _warn("Could not resolve bot member in guild.")
            return False

        try:
            if not bot_member.guild_permissions.manage_roles:
                _warn("Bot is missing Manage Roles permission.")
                return False
        except Exception:
            _warn("Could not confirm Manage Roles permission.")
            return False

        try:
            if role.position >= bot_member.top_role.position:
                _warn(
                    f"Cannot assign Unverified because role hierarchy blocks it. "
                    f"unverified_role={role.name}({role.id}) bot_top={bot_member.top_role.name}({bot_member.top_role.id})"
                )
                return False
        except Exception:
            _warn("Failed hierarchy check for Unverified assignment.")
            return False

        last_error: Optional[Exception] = None

        for attempt in range(1, 4):
            try:
                await asyncio.sleep(1.5 if attempt == 1 else 1.0)

                try:
                    fresh_member = await guild.fetch_member(member.id)
                except Exception:
                    fresh_member = member

                if getattr(fresh_member, "bot", False):
                    return False

                for safe_id in safe_role_ids:
                    if safe_id and role_truth.member_has_role_id(fresh_member, safe_id):
                        _log(f"skip Unverified for {fresh_member.id}; already has safe role {safe_id}")
                        return False

                if role_truth.member_has_role_id(fresh_member, uv_id):
                    _log(f"member {fresh_member.id} already has Unverified")
                    return True

                await fresh_member.add_roles(
                    role,
                    reason="Auto-assign Unverified on join (not Verified)",
                )

                try:
                    confirm_member = await guild.fetch_member(member.id)
                except Exception:
                    confirm_member = fresh_member

                if role_truth.member_has_role_id(confirm_member, uv_id):
                    _log(f"assigned Unverified to {confirm_member} ({confirm_member.id}) on attempt {attempt}")
                    return True

            except discord.Forbidden as e:
                last_error = e
                _warn(
                    f"Forbidden assigning Unverified to {member.id}. "
                    f"Check role hierarchy + Manage Roles. attempt={attempt} error={e!r}"
                )
                break
            except discord.HTTPException as e:
                last_error = e
                _warn(f"HTTPException assigning Unverified to {member.id}. attempt={attempt} error={e!r}")
            except Exception as e:
                last_error = e
                _warn(f"Unexpected error assigning Unverified to {member.id}. attempt={attempt} error={e!r}")

        _warn(f"failed to assign Unverified to {member.id}. last_error={last_error!r}")
        return False
    except Exception as e:
        _warn(f"ensure_unverified_on_join fatal error: {e!r}")
        try:
            traceback.print_exc()
        except Exception:
            pass
        return False


__all__ = [
    "ensure_unverified_on_join",
    "resolve_bot_member",
    "verification_config_ready_for_guild",
    "verification_role_ids_for_guild",
]
