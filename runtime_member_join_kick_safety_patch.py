from __future__ import annotations

"""
Fresh-join removal safety guard.

This guard exists because public/listing-site traffic can look noisy and a bot
must not remove a brand-new member instantly without a clear staff-visible reason.

Guarantees:
- every member join clears stale persisted verification wait timers
- bot-driven kick/ban calls are blocked during the fresh-join protection window
  unless explicitly allowed by env
- both discord.Guild.kick(...) and discord.Member.kick(...) paths are covered
- both discord.Guild.ban(...) and discord.Member.ban(...) paths are covered
- blocked/executed actions are logged to configured modlog/join-log channels

Important:
This does not weaken Discord-native server security or staff manual Discord UI
kicks. It only prevents this bot from instantly kicking/banning fresh joins due
to over-tight invite/risk/timer automation.
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
_MEMBER_KICK_PATCHED = False
_GUILD_BAN_PATCHED = False
_MEMBER_BAN_PATCHED = False
_ORIGINAL_GUILD_KICK = None
_ORIGINAL_MEMBER_KICK = None
_ORIGINAL_GUILD_BAN = None
_ORIGINAL_MEMBER_BAN = None

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


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
    except Exception:
        pass
    return default


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _fresh_join_safety_minutes() -> int:
    for key in ("FRESH_JOIN_REMOVAL_PROTECTION_MINUTES", "VERIFY_FRESH_JOIN_KICK_PROTECTION_MINUTES", "JOIN_KICK_PROTECTION_MINUTES"):
        value = _safe_int(os.getenv(key), 0)
        if value > 0:
            return value
    return 10


def _bot_fresh_join_removal_allowed() -> bool:
    # Keep false by default. Public listing-site servers should not have the bot
    # removing brand-new joins instantly. Staff can still use Discord native UI.
    return _safe_bool(os.getenv("ALLOW_BOT_FRESH_JOIN_REMOVAL"), False)


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
        # If Discord did not give joined_at, do not assume safe to remove.
        return True
    return age < (_fresh_join_safety_minutes() * 60)


def _removal_should_be_blocked(member: Any, *, reason: Any) -> bool:
    if _bot_fresh_join_removal_allowed():
        return False
    if not isinstance(member, discord.Member):
        return False
    if getattr(member, "bot", False):
        return False
    if not _is_fresh_join(member):
        return False

    # Explicit internal override for future staff-command code if we add a
    # confirmation flow. Existing automation will not contain these markers.
    reason_text = _safe_str(reason, "").lower()
    override_markers = (
        "manual_override:fresh_join_removal",
        "confirmed_staff_override:fresh_join_removal",
    )
    if any(marker in reason_text for marker in override_markers):
        return False

    return True


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
        "raid_log_channel_id",
        "join_log_channel_id",
        "join_exit_log_channel_id",
        "member_log_channel_id",
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

    return channels[:3]


async def _post_removal_safety_log(
    guild: discord.Guild,
    member: discord.Member,
    *,
    action: str,
    reason: Any,
    prevented: bool,
) -> None:
    try:
        age = _member_join_age_seconds(member)
        age_text = "unknown" if age is None else f"{int(age)}s"
        title = f"🛡️ Fresh Join {action.title()} Blocked" if prevented else f"👢 Fresh Join {action.title()} Executed"
        embed = discord.Embed(
            title=title,
            color=discord.Color.gold() if prevented else discord.Color.orange(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="User", value=f"{member.mention}\n`{member}`\n`{member.id}`", inline=False)
        embed.add_field(name="Bot Action", value=action.upper(), inline=True)
        embed.add_field(name="Joined Age", value=age_text, inline=True)
        embed.add_field(name="Protection Window", value=f"{_fresh_join_safety_minutes()} minute(s)", inline=True)
        embed.add_field(name="Reason", value=_safe_str(reason, "No reason provided")[:1024], inline=False)
        embed.add_field(
            name="Result",
            value=(
                "Blocked. This bot is not allowed to instantly remove fresh joins. This protects public invite/listing-site traffic from over-tight automation."
                if prevented
                else "Executed after protection checks."
            ),
            inline=False,
        )
        embed.set_footer(text=f"Guild {guild.id} • fresh join removal safety")

        sent = False
        for channel in await _configured_log_channels(guild):
            try:
                perms = channel.permissions_for(guild.me) if guild.me else None
                if perms is not None and (not perms.view_channel or not perms.send_messages or not perms.embed_links):
                    continue
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                sent = True
            except Exception:
                continue

        if not sent:
            _warn(
                f"fresh join {action} {'blocked' if prevented else 'executed'} but no writable log channel "
                f"guild={guild.id} user={member.id} reason={reason!r}"
            )
    except Exception:
        pass


async def _clear_persisted_member_wait_timers(guild_id: int, user_id: int, *, reason: str) -> None:
    try:
        from stoney_verify.commands_ext import kick_timers

        try:
            cancel_all = getattr(kick_timers, "cancel_verification_wait_timers_for_member", None)
            if callable(cancel_all):
                await cancel_all(int(guild_id), int(user_id))
        except Exception:
            pass

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


async def _block_or_run_removal(
    *,
    action: str,
    guild: discord.Guild,
    member: discord.Member,
    reason: Any,
    runner,
) -> Any:
    if _removal_should_be_blocked(member, reason=reason):
        await _clear_persisted_member_wait_timers(guild.id, member.id, reason=f"blocked fresh join bot {action}")
        _warn(
            f"blocked fresh-join bot {action} guild={guild.id} user={member.id} "
            f"age={_member_join_age_seconds(member)} reason={reason!r}"
        )
        await _post_removal_safety_log(guild, member, action=action, reason=reason, prevented=True)
        return None

    result = await runner()
    try:
        if isinstance(member, discord.Member) and _member_join_age_seconds(member) is not None and _member_join_age_seconds(member) <= (_fresh_join_safety_minutes() * 60):
            await _post_removal_safety_log(guild, member, action=action, reason=reason, prevented=False)
    except Exception:
        pass
    return result


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
        _log("patched discord.Guild.kick with fresh-join removal guard")
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
        _log("patched discord.Member.kick with fresh-join removal guard")
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
        _log("patched discord.Guild.ban with fresh-join removal guard")
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
        _log("patched discord.Member.ban with fresh-join removal guard")
    except Exception as e:
        _warn(f"failed patching discord.Member.ban: {e!r}")


def _patch_kick_timers(module: Any) -> None:
    module_name = getattr(module, "__name__", "")
    key = f"{module_name}:member_join_kick_safety_v2"
    if key in _PATCHED_MODULES:
        return

    original_start = getattr(module, "start_join_grace_then_kick_timer_for_member", None)
    if callable(original_start) and not getattr(original_start, "_fresh_join_safety_wrapped_v2", False):
        async def _start_join_grace_then_kick_timer_for_member_patched(member: discord.Member, *args: Any, **kwargs: Any) -> Any:
            try:
                if not getattr(member, "bot", False):
                    await _clear_persisted_member_wait_timers(member.guild.id, member.id, reason="starting fresh join grace")
            except Exception:
                pass
            return await original_start(member, *args, **kwargs)

        try:
            setattr(_start_join_grace_then_kick_timer_for_member_patched, "_fresh_join_safety_wrapped_v2", True)
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
    _patch_discord_member_kick()
    _patch_discord_guild_ban()
    _patch_discord_member_ban()
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
        _patch_loaded()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


builtins.__import__ = _safe_import
_patch_loaded()
_log("loaded; fresh-join bot kick/ban protection active")
