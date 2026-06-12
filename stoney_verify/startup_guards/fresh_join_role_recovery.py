from __future__ import annotations

"""Fresh join Unverified-role recovery guard.

This guard has one job: when a new human member joins, make sure the configured
per-guild Unverified role is applied as early as possible.

Fail-closed kick/removal decisions are owned by
stoney_verify.members_new.join_removal_safety and routed from events.py by
startup_guards.event_safety. Do not patch removal handlers here.
"""

import builtins
import sys
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

import discord

if not hasattr(builtins, "_stoney_true_original_import"):
    setattr(builtins, "_stoney_true_original_import", builtins.__import__)

_ORIGINAL_IMPORT = getattr(builtins, "_stoney_true_original_import")
_ATTACHED = False
_PATCHING = False


def _log(message: str) -> None:
    try:
        print(f"🧷 fresh_join_role_recovery {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ fresh_join_role_recovery {message}")
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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _member_has_role(member: discord.Member, role_id: int) -> bool:
    if role_id <= 0:
        return False
    try:
        return any(int(getattr(role, "id", 0) or 0) == int(role_id) for role in (member.roles or []))
    except Exception:
        return False


def _safe_role_state(member: discord.Member, cfg: Any) -> bool:
    try:
        for attr in (
            "unverified_role_id",
            "verified_role_id",
            "resident_role_id",
            "staff_role_id",
            "stoner_role_id",
            "drunken_role_id",
        ):
            rid = _safe_int(getattr(cfg, attr, 0), 0)
            if rid > 0 and _member_has_role(member, rid):
                return True
    except Exception:
        pass
    return False


def _can_assign_role(guild: discord.Guild, role: discord.Role) -> Tuple[bool, str]:
    me = guild.me
    if me is None:
        return False, "bot member is unavailable"
    try:
        if not me.guild_permissions.manage_roles:
            return False, "bot is missing Manage Roles"
    except Exception:
        return False, "could not inspect bot Manage Roles permission"

    try:
        if role >= me.top_role:
            return False, f"Unverified role @{role.name} is above or equal to the bot top role @{me.top_role.name}"
    except Exception:
        return False, "could not inspect role hierarchy"

    return True, "ok"


async def _configured_staff_log_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    try:
        from stoney_verify.guild_config import get_guild_config

        cfg = await get_guild_config(guild.id)
        for attr in (
            "modlog_channel_id",
            "raidlog_channel_id",
            "force_verify_log_channel_id",
            "join_log_channel_id",
        ):
            cid = _safe_int(getattr(cfg, attr, 0), 0)
            if cid <= 0:
                continue
            channel = guild.get_channel(cid)
            if not isinstance(channel, discord.TextChannel):
                continue
            me = guild.me
            if me is None:
                continue
            perms = channel.permissions_for(me)
            if perms.view_channel and perms.send_messages and perms.embed_links:
                return channel
    except Exception:
        pass
    return None


async def _post_recovery_log(member: discord.Member, *, title: str, detail: str, success: bool) -> None:
    try:
        channel = await _configured_staff_log_channel(member.guild)
        if channel is None:
            return
        embed = discord.Embed(
            title=title,
            description=detail[:3500],
            color=discord.Color.green() if success else discord.Color.gold(),
            timestamp=_utcnow(),
        )
        embed.add_field(name="User", value=f"{member.mention}\n`{member}`\n`{member.id}`", inline=False)
        embed.set_footer(text=f"Guild {member.guild.id} • fresh join role recovery")
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except Exception as e:
        _warn(
            "failed sending recovery log "
            f"guild={getattr(getattr(member, 'guild', None), 'id', None)} "
            f"user={getattr(member, 'id', None)}: {e!r}"
        )


async def ensure_fresh_join_unverified_role(
    member: discord.Member,
    *,
    source: str = "fresh_join_role_recovery",
    log_success: bool = False,
) -> Tuple[bool, str]:
    """Ensure a new human member has a safe verification role state."""
    try:
        if getattr(member, "bot", False):
            return True, "bot member skipped"

        from stoney_verify.guild_config import get_guild_config

        cfg = await get_guild_config(member.guild.id)
        if _safe_role_state(member, cfg):
            return True, "member already has a safe verification role state"

        unverified_role_id = _safe_int(getattr(cfg, "unverified_role_id", 0), 0)
        if unverified_role_id <= 0:
            return False, "no unverified_role_id configured for this guild"

        role = member.guild.get_role(unverified_role_id)
        if role is None:
            return False, f"configured unverified role does not exist: {unverified_role_id}"

        allowed, reason = _can_assign_role(member.guild, role)
        if not allowed:
            return False, reason

        await member.add_roles(role, reason=f"Dank Shield fresh join recovery: {source}"[:512])

        detail = f"Assigned {role.mention} to a fresh join before verification automation could fail closed."
        _log(f"assigned unverified role guild={member.guild.id} user={member.id} role={role.id} source={source}")
        if log_success:
            await _post_recovery_log(member, title="🧷 Fresh Join Role Recovered", detail=detail, success=True)
        return True, detail
    except discord.Forbidden:
        return False, "Discord refused role assignment: missing permission or role hierarchy"
    except Exception as e:
        return False, f"role recovery failed: {type(e).__name__}: {e}"


async def _on_member_join_recover_role(member: discord.Member) -> None:
    ok, detail = await ensure_fresh_join_unverified_role(member, source="on_member_join", log_success=False)
    if ok:
        return
    _warn(f"fresh join role recovery failed guild={member.guild.id} user={member.id}: {detail}")
    await _post_recovery_log(
        member,
        title="⚠️ Fresh Join Role Recovery Failed",
        detail=(
            "A member joined without a safe verification role state, and I could not assign the configured Unverified role.\n\n"
            f"Reason: {detail}\n\n"
            "Fix setup/role hierarchy instead of relying on instant kick safety."
        ),
        success=False,
    )


def _attach_bot_listener(bot: Any) -> None:
    global _ATTACHED
    if _ATTACHED or bot is None:
        return
    try:
        bot.add_listener(_on_member_join_recover_role, "on_member_join")
        _ATTACHED = True
        _log("attached fresh join unverified-role recovery listener")
    except Exception as e:
        _warn(f"failed attaching fresh join role recovery listener: {e!r}")


def _maybe_attach_bot() -> None:
    for module_name in ("stoney_verify.globals", "stoney_verify.app"):
        try:
            module = sys.modules.get(module_name)
            bot = getattr(module, "bot", None) if module is not None else None
            if bot is not None:
                _attach_bot_listener(bot)
                return
        except Exception:
            continue


def _patch_loaded_once() -> None:
    global _PATCHING
    if _PATCHING:
        return
    _PATCHING = True
    try:
        _maybe_attach_bot()
    finally:
        _PATCHING = False


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if (
            name in {"stoney_verify.globals", "stoney_verify.app"}
            or name.endswith("stoney_verify.globals")
            or name.endswith("stoney_verify.app")
        ):
            _maybe_attach_bot()
    except Exception as e:
        _warn(f"post-import attach failed for {name}: {e!r}")
    return module


builtins.__import__ = _safe_import
_patch_loaded_once()
_log("loaded; fresh joins recover Unverified role on member join")


__all__ = ["ensure_fresh_join_unverified_role"]
