from __future__ import annotations

"""
Member join / verification-kick safety guard.

Fixes a dangerous production behavior:
A returning/new member can be removed immediately if an old persisted verification
wait timer is still around from a previous join. The member appears as Joined ->
Left with no useful explanation.

Guarantees:
- every member join clears stale persisted verification timers for that user
- verification-timer kicks cannot remove a member during the fresh-join safety
  window
- prevented/executed verification kicks are posted to the server's configured
  modlog/join-log when possible
- normal staff moderation kicks are not blocked unless their reason clearly looks
  like an automatic verification timer
"""

import asyncio
import builtins
import os
import sys
from datetime import datetime, timezone
from typing import Any, Optional

import discord

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED_MODULES: set[str] = set()
_READY_LISTENER_ATTACHED = False
_GUILD_KICK_PATCHED = False
_ORIGINAL_GUILD_KICK = None

_TIMER_TYPES = ("join_grace", "member_no_ticket")


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


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _fresh_join_safety_minutes() -> int:
    for key in ("VERIFY_FRESH_JOIN_KICK_PROTECTION_MINUTES", "JOIN_KICK_PROTECTION_MINUTES"):
        value = _safe_int(os.getenv(key), 0)
        if value > 0:
            return value
    return 10


def _member_join_age_seconds(member: discord.Member) -> Optional[float]:
    try:
        joined_at = getattr(member, "joined_at", None)
        if joined_at is None:
            return None
        if joined_at.tzinfo is None:
            joined_at = joined_at.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - joined_at.astimezone(timezone.utc)).total_seconds())
    except Exception:
        return None


def _is_fresh_join(member: discord.Member) -> bool:
    age = _member_join_age_seconds(member)
    if age is None:
        return False
    return age < (_fresh_join_safety_minutes() * 60)


def _looks_like_auto_verification_kick(reason: Any) -> bool:
    text = _safe_str(reason, "").lower()
    if not text:
        return False
    verification_markers = ("verification", "verify", "unverified")
    timer_markers = (
        "timer",
        "expired",
        "no-response",
        "no response",
        "no ticket",
        "no verification progress",
        "failed to respond",
        "wait time",
    )
    return any(m in text for m in verification_markers) and any(m in text for m in timer_markers)


async def _get_cfg(guild: discord.Guild) -> Any:
    try:
        from stoney_verify.guild_config import get_guild_config

        return await asyncio.wait_for(get_guild_config(guild.id), timeout=3.0)
    except Exception:
        return None


async def _configured_log_channels(guild: discord.Guild) -> list[discord.TextChannel]:
    channels: list[discord.TextChannel] = []
    seen: set[int] = set()
    cfg = await _get_cfg(guild)

    for attr in (
        "modlog_channel_id",
        "join_log_channel_id",
        "join_exit_log_channel_id",
        "member_log_channel_id",
        "raid_log_channel_id",
    ):
        cid = _safe_int(getattr(cfg, attr, 0), 0) if cfg is not None else 0
        if cid <= 0 or cid in seen:
            continue
        try:
            ch = guild.get_channel(cid)
            if isinstance(ch, discord.TextChannel):
                channels.append(ch)
                seen.add(cid)
        except Exception:
            continue

    return channels[:2]


async def _post_kick_safety_log(
    guild: discord.Guild,
    member: discord.Member,
    *,
    title: str,
    reason: Any,
    prevented: bool,
) -> None:
    try:
        age = _member_join_age_seconds(member)
        age_text = "unknown" if age is None else f"{int(age)}s"
        embed = discord.Embed(
            title=title,
            color=discord.Color.gold() if prevented else discord.Color.orange(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="User", value=f"{member.mention}\n`{member}`\n`{member.id}`", inline=False)
        embed.add_field(name="Reason", value=_safe_str(reason, "No reason provided")[:1024], inline=False)
        embed.add_field(name="Joined Age", value=age_text, inline=True)
        embed.add_field(name="Fresh-Join Protection", value=f"{_fresh_join_safety_minutes()} minute(s)", inline=True)
        embed.add_field(
            name="Result",
            value=(
                "Kick blocked because this looked like an old/stale verification timer."
                if prevented
                else "Kick executed after passing safety checks."
            ),
            inline=False,
        )
        embed.set_footer(text=f"Guild {guild.id} • verification kick safety")

        for channel in await _configured_log_channels(guild):
            try:
                perms = channel.permissions_for(guild.me) if guild.me else None
                if perms is not None and (not perms.view_channel or not perms.send_messages or not perms.embed_links):
                    continue
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            except Exception:
                continue
    except Exception:
        pass


async def _clear_persisted_member_wait_timers(guild_id: int, user_id: int, *, reason: str) -> None:
    try:
        from stoney_verify.commands_ext import kick_timers

        # Cancel in-memory tasks first.
        try:
            cancel_all = getattr(kick_timers, "cancel_verification_wait_timers_for_member", None)
            if callable(cancel_all):
                await cancel_all(int(guild_id), int(user_id))
        except Exception:
            pass

        # Then remove persisted rows by type. This handles old timers from a
        # previous join that could otherwise fire immediately after a rejoin.
        delete_fn = getattr(kick_timers, "member_wait_timer_persist_delete", None)
        if callable(delete_fn):
            for timer_type in _TIMER_TYPES:
                try:
                    await delete_fn(int(guild_id), int(user_id), timer_type=timer_type)
                except TypeError:
                    try:
                        await delete_fn(int(guild_id), int(user_id), timer_type)
                    except Exception:
                        pass
                except Exception:
                    pass
        _log(f"cleared stale verification wait timers guild={guild_id} user={user_id} reason={reason}")
    except Exception as e:
        _warn(f"failed clearing stale verification timers guild={guild_id} user={user_id}: {e!r}")


async def _on_member_join_clear_stale_timers(member: discord.Member) -> None:
    try:
        if getattr(member, "bot", False):
            return
        await _clear_persisted_member_wait_timers(member.guild.id, member.id, reason="fresh member join")
    except Exception as e:
        _warn(f"join timer cleanup failed guild={getattr(getattr(member, 'guild', None), 'id', None)} user={getattr(member, 'id', None)}: {e!r}")


def _patch_discord_guild_kick() -> None:
    global _GUILD_KICK_PATCHED, _ORIGINAL_GUILD_KICK
    if _GUILD_KICK_PATCHED:
        return

    original = getattr(discord.Guild, "kick", None)
    if not callable(original):
        return

    _ORIGINAL_GUILD_KICK = original

    async def _kick_with_fresh_join_guard(self: discord.Guild, user: Any, *, reason: Optional[str] = None) -> Any:
        if isinstance(user, discord.Member) and _looks_like_auto_verification_kick(reason):
            if _is_fresh_join(user):
                await _clear_persisted_member_wait_timers(self.id, user.id, reason="blocked stale verification kick")
                _warn(
                    f"blocked fresh-join verification kick guild={self.id} user={user.id} "
                    f"age={_member_join_age_seconds(user)} reason={reason!r}"
                )
                await _post_kick_safety_log(
                    self,
                    user,
                    title="🛡️ Verification Kick Blocked",
                    reason=reason,
                    prevented=True,
                )
                return None

            result = await original(self, user, reason=reason)
            try:
                await _post_kick_safety_log(
                    self,
                    user,
                    title="👢 Verification Kick Executed",
                    reason=reason,
                    prevented=False,
                )
            except Exception:
                pass
            return result

        return await original(self, user, reason=reason)

    try:
        discord.Guild.kick = _kick_with_fresh_join_guard  # type: ignore[method-assign]
        _GUILD_KICK_PATCHED = True
        _log("patched discord.Guild.kick with fresh-join verification timer guard")
    except Exception as e:
        _warn(f"failed patching discord.Guild.kick: {e!r}")


def _patch_kick_timers(module: Any) -> None:
    module_name = getattr(module, "__name__", "")
    key = f"{module_name}:member_join_kick_safety_v1"
    if key in _PATCHED_MODULES:
        return

    original_start = getattr(module, "start_join_grace_then_kick_timer_for_member", None)
    if callable(original_start) and not getattr(original_start, "_fresh_join_safety_wrapped", False):
        async def _start_join_grace_then_kick_timer_for_member_patched(member: discord.Member, *args: Any, **kwargs: Any) -> Any:
            try:
                if not getattr(member, "bot", False):
                    await _clear_persisted_member_wait_timers(member.guild.id, member.id, reason="starting fresh join grace")
            except Exception:
                pass
            return await original_start(member, *args, **kwargs)

        try:
            setattr(_start_join_grace_then_kick_timer_for_member_patched, "_fresh_join_safety_wrapped", True)
        except Exception:
            pass
        setattr(module, "start_join_grace_then_kick_timer_for_member", _start_join_grace_then_kick_timer_for_member_patched)

    _PATCHED_MODULES.add(key)
    _log(f"patched {module_name}; stale verification timers are cleared on fresh joins")


def _patch_events_module(module: Any) -> None:
    try:
        from stoney_verify.commands_ext import kick_timers

        patched_start = getattr(kick_timers, "start_join_grace_then_kick_timer_for_member", None)
        if callable(patched_start):
            setattr(module, "start_join_grace_then_kick_timer_for_member", patched_start)
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


def _patch_loaded() -> None:
    _patch_discord_guild_kick()
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
    _maybe_attach_bot()


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
        else:
            _patch_loaded()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


builtins.__import__ = _safe_import
_patch_loaded()
_log("loaded; member join stale-timer kick safety active")
