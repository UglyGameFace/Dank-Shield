from __future__ import annotations

"""
Fresh-join removal safety guard.

The real business rules live in:
    stoney_verify.members_new.join_removal_safety

This runtime shim only patches the exact modules/calls it owns. It intentionally
does not scan on every import because that creates ugly log spam and import-hook
storms on Discloud.
"""

import builtins
import sys
from typing import Any, Optional

import discord

_ORIGINAL_IMPORT = builtins.__import__
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


def _log(message: str) -> None:
    try:
        print(f"🛡️ runtime_member_join_kick_safety {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ runtime_member_join_kick_safety {message}")
    except Exception:
        pass


async def _on_member_join_clear_stale_timers(member: discord.Member) -> None:
    try:
        from stoney_verify.members_new.join_removal_safety import clear_stale_timers_for_join

        await clear_stale_timers_for_join(member, reason="fresh member join")
    except Exception as e:
        _warn(f"join timer cleanup failed guild={getattr(getattr(member, 'guild', None), 'id', None)} user={getattr(member, 'id', None)}: {e!r}")


async def _block_or_run_removal(
    *,
    action: str,
    guild: discord.Guild,
    member: discord.Member,
    reason: Any,
    runner,
) -> Any:
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
        _log("patched discord.Guild.kick with native fresh-join removal guard")
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
        _log("patched discord.Member.kick with native fresh-join removal guard")
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
        _log("patched discord.Guild.ban with native fresh-join removal guard")
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
        _log("patched discord.Member.ban with native fresh-join removal guard")
    except Exception as e:
        _warn(f"failed patching discord.Member.ban: {e!r}")


def _patch_kick_timers(module: Any) -> None:
    module_name = getattr(module, "__name__", "")
    key = f"{module_name}:native_member_join_kick_safety_v2"
    if key in _PATCHED_MODULES:
        return

    original_start = getattr(module, "start_join_grace_then_kick_timer_for_member", None)
    if callable(original_start) and not getattr(original_start, "_native_fresh_join_safety_wrapped", False):
        async def _start_join_grace_then_kick_timer_for_member_patched(member: discord.Member, *args: Any, **kwargs: Any) -> Any:
            try:
                from stoney_verify.members_new.join_removal_safety import clear_stale_timers_for_join

                await clear_stale_timers_for_join(member, reason="starting fresh join grace")
            except Exception:
                pass
            return await original_start(member, *args, **kwargs)

        try:
            setattr(_start_join_grace_then_kick_timer_for_member_patched, "_native_fresh_join_safety_wrapped", True)
        except Exception:
            pass
        setattr(module, "start_join_grace_then_kick_timer_for_member", _start_join_grace_then_kick_timer_for_member_patched)
        _log(f"patched {module_name}; stale verification timers clear through native helper")

    _PATCHED_MODULES.add(key)


def _patch_events_module(module: Any) -> None:
    global _EVENTS_REFERENCE_UPDATED
    if _EVENTS_REFERENCE_UPDATED:
        return
    try:
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


def _attach_join_listener(bot: Any) -> None:
    global _READY_LISTENER_ATTACHED
    if _READY_LISTENER_ATTACHED or bot is None:
        return
    _READY_LISTENER_ATTACHED = True

    try:
        bot.add_listener(_on_member_join_clear_stale_timers, "on_member_join")
        _log("attached native stale verification timer cleanup listener on member join")
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
        # Only react to modules this shim owns. No more _patch_loaded on every import.
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
_log("loaded; native fresh-join bot kick/ban protection active")
