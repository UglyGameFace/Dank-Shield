from __future__ import annotations

"""
Fresh-join removal safety guard.

This replaces the old root-level runtime_member_join_kick_safety_patch.py.

The actual business rules live in:
    stoney_verify.members_new.join_removal_safety

This startup guard patches only the Discord removal calls and stale timer hooks it
owns so a fresh member join cannot be kicked/banned by stale verification timers
or over-tightened join automation.
"""

import builtins
import re
import sys
from typing import Any, Optional

import discord

if not hasattr(builtins, "_stoney_true_original_import"):
    setattr(builtins, "_stoney_true_original_import", builtins.__import__)

_ORIGINAL_IMPORT = getattr(builtins, "_stoney_true_original_import")
_PATCHED_MODULES: set[str] = set()
_READY_LISTENER_ATTACHED = False
_GUILD_KICK_PATCHED = False
_MEMBER_KICK_PATCHED = False
_GUILD_BAN_PATCHED = False
_MEMBER_BAN_PATCHED = False
_EVENTS_REFERENCE_UPDATED = False
_PATCHING = False
_ORIGINAL_GUILD_KICK = None
_ORIGINAL_MEMBER_KICK = None
_ORIGINAL_GUILD_BAN = None
_ORIGINAL_MEMBER_BAN = None

_SETUP_MANAGER_ROLE_KEYS: set[str] = {
    "botmanager",
    "servermanager",
    "botadmin",
    "botowner",
    "admin",
    "administrator",
    "owner",
    "manager",
    "mod",
    "moderator",
    "staff",
    "staffteam",
    "support",
    "supportteam",
}


def _log(message: str) -> None:
    try:
        print(f"🛡️ member_join_removal_safety {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ member_join_removal_safety {message}")
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


def _role_key(value: Any) -> str:
    try:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().casefold())
    except Exception:
        return ""


def _member_role_ids(member: discord.Member) -> set[int]:
    ids: set[int] = set()
    try:
        for role in getattr(member, "roles", []) or []:
            rid = _safe_int(getattr(role, "id", 0), 0)
            if rid > 0:
                ids.add(rid)
    except Exception:
        pass
    return ids


def _member_has_named_setup_role(member: discord.Member) -> bool:
    try:
        for role in getattr(member, "roles", []) or []:
            if getattr(role, "is_default", lambda: False)():
                continue
            if _role_key(getattr(role, "name", "")) in _SETUP_MANAGER_ROLE_KEYS:
                return True
    except Exception:
        pass
    return False


def _member_has_configured_control_or_staff_role(member: discord.Member) -> bool:
    try:
        from stoney_verify.commands_ext.public_access_control import configured_control_role_ids_for_guild

        guild_id = _safe_int(getattr(getattr(member, "guild", None), "id", 0), 0)
        allowed_ids = set(configured_control_role_ids_for_guild(guild_id))
        try:
            from stoney_verify.commands_ext.public_access_control import _configured_staff_role_ids  # type: ignore[attr-defined]

            allowed_ids.update(_configured_staff_role_ids(member))
        except Exception:
            pass
        return bool(allowed_ids and _member_role_ids(member).intersection(allowed_ids))
    except Exception:
        return False


def _member_has_setup_permissions(member: discord.Member) -> bool:
    try:
        perms = getattr(member, "guild_permissions", None)
        if perms is None:
            return False
        return bool(
            getattr(perms, "administrator", False)
            or getattr(perms, "manage_guild", False)
            or getattr(perms, "manage_roles", False)
        )
    except Exception:
        return False


def _is_verification_privileged_member(member: Any) -> bool:
    """Members who can configure/manage the bot must never be verification-kicked.

    This covers server owners, admins, configured control/staff roles, and the
    starter roles created by Auto-Build such as Bot Manager / Support Team.
    """
    if not isinstance(member, discord.Member):
        return False
    try:
        if getattr(member, "bot", False):
            return False
    except Exception:
        pass

    try:
        guild = getattr(member, "guild", None)
        owner_id = _safe_int(getattr(guild, "owner_id", 0), 0)
        if owner_id > 0 and int(member.id) == owner_id:
            return True
    except Exception:
        pass

    if _member_has_setup_permissions(member):
        return True

    try:
        from stoney_verify.commands_ext.public_access_control import scoped_is_server_control, scoped_is_ticket_staff

        if scoped_is_server_control(member) or scoped_is_ticket_staff(member):
            return True
    except Exception:
        pass

    if _member_has_configured_control_or_staff_role(member):
        return True

    if _member_has_named_setup_role(member):
        return True

    return False


async def _clear_member_verification_timers(member: discord.Member, *, reason: str) -> None:
    try:
        from stoney_verify.members_new.join_removal_safety import clear_persisted_member_wait_timers

        await clear_persisted_member_wait_timers(member.guild.id, member.id, reason=reason)
    except Exception:
        pass


async def _on_member_join_clear_stale_timers(member: discord.Member) -> None:
    try:
        from stoney_verify.members_new.join_removal_safety import clear_stale_timers_for_join

        await clear_stale_timers_for_join(member, reason="fresh member join")
    except Exception as e:
        _warn(
            f"join timer cleanup failed guild={getattr(getattr(member, 'guild', None), 'id', None)} "
            f"user={getattr(member, 'id', None)}: {e!r}"
        )


async def _block_or_run_removal(
    *,
    action: str,
    guild: discord.Guild,
    member: discord.Member,
    reason: Any,
    runner,
) -> Any:
    if _is_verification_privileged_member(member):
        await _clear_member_verification_timers(
            member,
            reason=f"blocked bot {action} against setup/admin member",
        )
        _warn(
            f"blocked bot {action} against setup/admin member "
            f"guild={getattr(guild, 'id', None)} user={getattr(member, 'id', None)} reason={reason!r}"
        )
        return None

    from stoney_verify.members_new.join_removal_safety import block_or_run_bot_removal

    return await block_or_run_bot_removal(
        action=action,
        guild=guild,
        member=member,
        reason=reason,
        runner=runner,
    )


def _patch_discord_guild_kick() -> None:
    global _GUILD_KICK_PATCHED, _ORIGINAL_GUILD_KICK
    if _GUILD_KICK_PATCHED:
        return
    original = getattr(discord.Guild, "kick", None)
    if not callable(original):
        return
    _ORIGINAL_GUILD_KICK = original

    async def _kick_with_fresh_join_guard(self: discord.Guild, user: Any, *, reason: Optional[str] = None) -> Any:
        if isinstance(user, discord.Member):
            return await _block_or_run_removal(
                action="kick",
                guild=self,
                member=user,
                reason=reason,
                runner=lambda: original(self, user, reason=reason),
            )
        return await original(self, user, reason=reason)

    try:
        discord.Guild.kick = _kick_with_fresh_join_guard  # type: ignore[method-assign]
        _GUILD_KICK_PATCHED = True
        _log("patched discord.Guild.kick with fresh-join/setup-admin removal guard")
    except Exception as e:
        _warn(f"failed patching discord.Guild.kick: {e!r}")


def _patch_discord_member_kick() -> None:
    global _MEMBER_KICK_PATCHED, _ORIGINAL_MEMBER_KICK
    if _MEMBER_KICK_PATCHED:
        return
    original = getattr(discord.Member, "kick", None)
    if not callable(original):
        return
    _ORIGINAL_MEMBER_KICK = original

    async def _member_kick_with_fresh_join_guard(self: discord.Member, *, reason: Optional[str] = None) -> Any:
        return await _block_or_run_removal(
            action="kick",
            guild=self.guild,
            member=self,
            reason=reason,
            runner=lambda: original(self, reason=reason),
        )

    try:
        discord.Member.kick = _member_kick_with_fresh_join_guard  # type: ignore[method-assign]
        _MEMBER_KICK_PATCHED = True
        _log("patched discord.Member.kick with fresh-join/setup-admin removal guard")
    except Exception as e:
        _warn(f"failed patching discord.Member.kick: {e!r}")


def _patch_discord_guild_ban() -> None:
    global _GUILD_BAN_PATCHED, _ORIGINAL_GUILD_BAN
    if _GUILD_BAN_PATCHED:
        return
    original = getattr(discord.Guild, "ban", None)
    if not callable(original):
        return
    _ORIGINAL_GUILD_BAN = original

    async def _ban_with_fresh_join_guard(self: discord.Guild, user: Any, *, reason: Optional[str] = None, **kwargs: Any) -> Any:
        if isinstance(user, discord.Member):
            return await _block_or_run_removal(
                action="ban",
                guild=self,
                member=user,
                reason=reason,
                runner=lambda: original(self, user, reason=reason, **kwargs),
            )
        return await original(self, user, reason=reason, **kwargs)

    try:
        discord.Guild.ban = _ban_with_fresh_join_guard  # type: ignore[method-assign]
        _GUILD_BAN_PATCHED = True
        _log("patched discord.Guild.ban with fresh-join/setup-admin removal guard")
    except Exception as e:
        _warn(f"failed patching discord.Guild.ban: {e!r}")


def _patch_discord_member_ban() -> None:
    global _MEMBER_BAN_PATCHED, _ORIGINAL_MEMBER_BAN
    if _MEMBER_BAN_PATCHED:
        return
    original = getattr(discord.Member, "ban", None)
    if not callable(original):
        return
    _ORIGINAL_MEMBER_BAN = original

    async def _member_ban_with_fresh_join_guard(self: discord.Member, *, reason: Optional[str] = None, **kwargs: Any) -> Any:
        return await _block_or_run_removal(
            action="ban",
            guild=self.guild,
            member=self,
            reason=reason,
            runner=lambda: original(self, reason=reason, **kwargs),
        )

    try:
        discord.Member.ban = _member_ban_with_fresh_join_guard  # type: ignore[method-assign]
        _MEMBER_BAN_PATCHED = True
        _log("patched discord.Member.ban with fresh-join/setup-admin removal guard")
    except Exception as e:
        _warn(f"failed patching discord.Member.ban: {e!r}")


def _patch_kick_timers(module: Any) -> None:
    module_name = getattr(module, "__name__", "")
    key = f"{module_name}:member_join_removal_safety_v1"
    if key in _PATCHED_MODULES:
        return

    original_start = getattr(module, "start_join_grace_then_kick_timer_for_member", None)
    if callable(original_start) and not getattr(original_start, "_member_join_removal_safety_wrapped", False):
        async def _start_join_grace_then_kick_timer_for_member_patched(member: discord.Member, *args: Any, **kwargs: Any) -> Any:
            try:
                from stoney_verify.members_new.join_removal_safety import clear_stale_timers_for_join

                await clear_stale_timers_for_join(member, reason="starting fresh join grace")
            except Exception:
                pass
            return await original_start(member, *args, **kwargs)

        try:
            setattr(_start_join_grace_then_kick_timer_for_member_patched, "_member_join_removal_safety_wrapped", True)
        except Exception:
            pass
        setattr(module, "start_join_grace_then_kick_timer_for_member", _start_join_grace_then_kick_timer_for_member_patched)
        _log(f"patched {module_name}; stale verification timers clear through native helper")

    _PATCHED_MODULES.add(key)


def _patch_events_module(module: Any) -> None:
    global _EVENTS_REFERENCE_UPDATED

    try:
        if not _EVENTS_REFERENCE_UPDATED:
            from stoney_verify.commands_ext import kick_timers

            patched_start = getattr(kick_timers, "start_join_grace_then_kick_timer_for_member", None)
            if callable(patched_start):
                current = getattr(module, "start_join_grace_then_kick_timer_for_member", None)
                if current is not patched_start:
                    setattr(module, "start_join_grace_then_kick_timer_for_member", patched_start)
                _EVENTS_REFERENCE_UPDATED = True
                _log("updated events.start_join_grace_then_kick_timer_for_member reference")
    except Exception:
        pass

    module_name = getattr(module, "__name__", "stoney_verify.events")
    key = f"{module_name}:setup_admin_verification_safety_v1"
    if key in _PATCHED_MODULES:
        return

    original_has_safe = getattr(module, "_member_has_any_safe_access_role", None)
    if callable(original_has_safe) and not getattr(original_has_safe, "_setup_admin_safety_wrapped", False):
        def _member_has_any_safe_access_role_patched(member: discord.Member, *args: Any, **kwargs: Any) -> bool:
            if _is_verification_privileged_member(member):
                return True
            return bool(original_has_safe(member, *args, **kwargs))

        try:
            setattr(_member_has_any_safe_access_role_patched, "_setup_admin_safety_wrapped", True)
        except Exception:
            pass
        setattr(module, "_member_has_any_safe_access_role", _member_has_any_safe_access_role_patched)

    original_ensure = getattr(module, "_ensure_member_verification_safe_state", None)
    if callable(original_ensure) and not getattr(original_ensure, "_setup_admin_safety_wrapped", False):
        async def _ensure_member_verification_safe_state_patched(member: discord.Member, *args: Any, **kwargs: Any) -> bool:
            if _is_verification_privileged_member(member):
                await _clear_member_verification_timers(member, reason="setup/admin member is verification-safe")
                return True
            return bool(await original_ensure(member, *args, **kwargs))

        try:
            setattr(_ensure_member_verification_safe_state_patched, "_setup_admin_safety_wrapped", True)
        except Exception:
            pass
        setattr(module, "_ensure_member_verification_safe_state", _ensure_member_verification_safe_state_patched)

    original_schedule = getattr(module, "_schedule_join_verification_watchdog", None)
    if callable(original_schedule) and not getattr(original_schedule, "_setup_admin_safety_wrapped", False):
        def _schedule_join_verification_watchdog_patched(member: discord.Member) -> Any:
            if _is_verification_privileged_member(member):
                _log(
                    f"skipped join watchdog for setup/admin member "
                    f"guild={getattr(getattr(member, 'guild', None), 'id', None)} user={getattr(member, 'id', None)}"
                )
                return None
            try:
                from stoney_verify.members_new.join_removal_safety import is_fresh_join

                if not is_fresh_join(member):
                    _log(
                        f"skipped join watchdog for established member "
                        f"guild={getattr(getattr(member, 'guild', None), 'id', None)} user={getattr(member, 'id', None)}"
                    )
                    return None
            except Exception:
                pass
            return original_schedule(member)

        try:
            setattr(_schedule_join_verification_watchdog_patched, "_setup_admin_safety_wrapped", True)
        except Exception:
            pass
        setattr(module, "_schedule_join_verification_watchdog", _schedule_join_verification_watchdog_patched)

    original_failure = getattr(module, "_handle_join_verification_failure", None)
    if callable(original_failure) and not getattr(original_failure, "_setup_admin_safety_wrapped", False):
        async def _handle_join_verification_failure_patched(member: discord.Member, reason: str) -> Any:
            if _is_verification_privileged_member(member):
                await _clear_member_verification_timers(member, reason="blocked fail-closed for setup/admin member")
                _warn(
                    f"blocked verification fail-closed for setup/admin member "
                    f"guild={getattr(getattr(member, 'guild', None), 'id', None)} user={getattr(member, 'id', None)} reason={reason!r}"
                )
                return None
            try:
                from stoney_verify.members_new.join_removal_safety import is_fresh_join

                if not is_fresh_join(member):
                    await _clear_member_verification_timers(member, reason="blocked fail-closed for established member")
                    _warn(
                        f"blocked verification fail-closed for established member "
                        f"guild={getattr(getattr(member, 'guild', None), 'id', None)} user={getattr(member, 'id', None)} reason={reason!r}"
                    )
                    return None
            except Exception:
                pass
            return await original_failure(member, reason)

        try:
            setattr(_handle_join_verification_failure_patched, "_setup_admin_safety_wrapped", True)
        except Exception:
            pass
        setattr(module, "_handle_join_verification_failure", _handle_join_verification_failure_patched)

    _PATCHED_MODULES.add(key)
    _log("patched events verification watchdog/fail-closed setup-admin safety")


def _attach_join_listener(bot: Any) -> None:
    global _READY_LISTENER_ATTACHED
    if _READY_LISTENER_ATTACHED or bot is None:
        return
    _READY_LISTENER_ATTACHED = True

    try:
        bot.add_listener(_on_member_join_clear_stale_timers, "on_member_join")
        _log("attached stale verification timer cleanup listener on member join")
    except Exception as e:
        _READY_LISTENER_ATTACHED = False
        _warn(f"failed attaching member join timer cleanup listener: {e!r}")


def _maybe_attach_bot() -> None:
    try:
        for module_name in ("stoney_verify.globals", "stoney_verify.app"):
            module = sys.modules.get(module_name)
            if module is None:
                continue
            bot = getattr(module, "bot", None)
            if bot is not None:
                _attach_join_listener(bot)
                return
    except Exception:
        pass


def _patch_core_once() -> None:
    global _PATCHING
    if _PATCHING:
        return
    _PATCHING = True
    try:
        _patch_discord_guild_kick()
        _patch_discord_member_kick()
        _patch_discord_guild_ban()
        _patch_discord_member_ban()
        _maybe_attach_bot()
    finally:
        _PATCHING = False


def _patch_loaded_once() -> None:
    _patch_core_once()
    try:
        module = sys.modules.get("stoney_verify.commands_ext.kick_timers")
        if module is not None:
            _patch_kick_timers(module)
    except Exception:
        pass
    try:
        module = sys.modules.get("stoney_verify.events")
        if module is not None:
            _patch_events_module(module)
    except Exception:
        pass


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.commands_ext.kick_timers" or name.endswith("commands_ext.kick_timers"):
            target = sys.modules.get("stoney_verify.commands_ext.kick_timers") or sys.modules.get(name)
            if target is not None:
                _patch_kick_timers(target)
        elif name == "stoney_verify.events" or name.endswith("stoney_verify.events"):
            target = sys.modules.get("stoney_verify.events") or sys.modules.get(name)
            if target is not None:
                _patch_events_module(target)
        elif name in {"stoney_verify.globals", "stoney_verify.app"} or name.endswith("stoney_verify.globals") or name.endswith("stoney_verify.app"):
            _maybe_attach_bot()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


builtins.__import__ = _safe_import
_patch_loaded_once()
_log("loaded; fresh-join/setup-admin bot kick/ban protection active")


__all__ = []
