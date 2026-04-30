from __future__ import annotations

"""
Ticket category enforcer.

Emergency repair net for open ticket channels that somehow end up outside the
configured Active Tickets category.

The real category-resolution rules live in:
    stoney_verify.tickets_new.category_resolver

This guard should become less important as ticket creation/close/reopen flows are
folded natively into tickets_new.service.

Important safety detail:
Only react to modules this shim owns. Do not patch-scan on every import.
"""

import asyncio
import builtins
import re
import sys
import time
from typing import Any, Optional

import discord

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED_MODULES: set[str] = set()
_READY_LISTENER_ATTACHED = False
_STARTUP_SWEEP_RAN = False
_PATCHING = False

_TICKET_OPEN_RE = re.compile(r"^ticket-\d{1,8}$", re.I)


def _log(message: str) -> None:
    try:
        print(f"🎯 runtime_ticket_category_enforcer {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ runtime_ticket_category_enforcer {message}")
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


def _guild_from_any(*values: Any) -> Optional[discord.Guild]:
    for value in values:
        try:
            if isinstance(value, discord.Guild):
                return value
            guild = getattr(value, "guild", None)
            if isinstance(guild, discord.Guild):
                return guild
            channel = getattr(value, "channel", None)
            guild = getattr(channel, "guild", None)
            if isinstance(guild, discord.Guild):
                return guild
        except Exception:
            continue
    return None


def _guild_from_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Optional[discord.Guild]:
    return _guild_from_any(
        kwargs.get("guild"),
        kwargs.get("owner"),
        kwargs.get("member"),
        kwargs.get("interaction"),
        kwargs.get("channel"),
        *list(args),
    )


async def _active_category_for_guild(guild: discord.Guild) -> Optional[discord.CategoryChannel]:
    try:
        from stoney_verify.tickets_new.category_resolver import resolve_active_ticket_category

        resolved = await resolve_active_ticket_category(guild, refresh=True, require_manage_channels=True)
        return resolved.category
    except Exception as e:
        _warn(f"active category lookup failed guild={getattr(guild, 'id', None)}: {e!r}")
    return None


def _is_open_ticket_channel(channel: Any) -> bool:
    try:
        return isinstance(channel, discord.TextChannel) and bool(_TICKET_OPEN_RE.match(str(channel.name or "")))
    except Exception:
        return False


def _channel_in_category(channel: discord.TextChannel, category: discord.CategoryChannel) -> bool:
    try:
        from stoney_verify.tickets_new.category_resolver import channel_is_in_category

        return bool(channel_is_in_category(channel, category))
    except Exception:
        try:
            return int(getattr(channel.category, "id", 0) or 0) == int(category.id)
        except Exception:
            return False


def _permission_snapshot(channel: discord.TextChannel) -> str:
    try:
        me = channel.guild.me
        if me is None:
            return "bot member unavailable"
        perms = channel.permissions_for(me)
        missing: list[str] = []
        checks = [
            ("View Channel", getattr(perms, "view_channel", False)),
            ("Read Message History", getattr(perms, "read_message_history", False)),
            ("Send Messages", getattr(perms, "send_messages", False)),
            ("Manage Channels", getattr(perms, "manage_channels", False)),
            ("Manage Permissions", getattr(perms, "manage_permissions", False)),
        ]
        for label, ok in checks:
            if not ok:
                missing.append(label)
        if not missing:
            return "channel perms look OK from cache"
        return "missing on current ticket channel: " + ", ".join(missing)
    except Exception as e:
        return f"permission snapshot failed: {type(e).__name__}"


async def _move_ticket_channel_to_active(channel: discord.TextChannel, *, reason_suffix: str) -> bool:
    category = await _active_category_for_guild(channel.guild)
    if category is None:
        _warn(f"cannot enforce category for channel={channel.id}; active ticket category missing/unresolved")
        return False

    if _channel_in_category(channel, category):
        return True

    try:
        await channel.edit(
            category=category,
            sync_permissions=False,
            reason=f"Ticket category enforcement: {reason_suffix}",
        )
        _log(f"moved {channel.mention} ({channel.id}) into active category {category.name} ({category.id})")
        return True
    except discord.Forbidden as e:
        _warn(
            "cannot repair orphan ticket channel automatically "
            f"guild={channel.guild.id} channel={channel.id} name={channel.name!r} "
            f"target_category={category.id} reason=Missing Access; {_permission_snapshot(channel)}. "
            "Fix by granting the bot View Channel + Manage Channels on that ticket/channel or delete/recreate the stale ticket. "
            f"raw={e!r}"
        )
        return False
    except Exception as e:
        _warn(f"failed moving ticket channel={channel.id} into active category={category.id}: {e!r}")
        return False


def _extract_channel_from_result(result: Any) -> Optional[discord.TextChannel]:
    try:
        if isinstance(result, discord.TextChannel):
            return result
        if isinstance(result, dict):
            for key in ("channel", "ticket_channel"):
                value = result.get(key)
                if isinstance(value, discord.TextChannel):
                    return value
        if isinstance(result, (tuple, list)):
            for item in result:
                found = _extract_channel_from_result(item)
                if found is not None:
                    return found
    except Exception:
        pass
    return None


def _extract_channel_id_from_result(result: Any) -> int:
    try:
        if isinstance(result, dict):
            for key in ("channel_id", "discord_thread_id", "id"):
                cid = _safe_int(result.get(key), 0)
                if cid > 0:
                    return cid
        if isinstance(result, (tuple, list)):
            for item in result:
                cid = _extract_channel_id_from_result(item)
                if cid > 0:
                    return cid
    except Exception:
        pass
    return 0


def _newest_open_ticket_channel(guild: discord.Guild, *, started_at: float) -> Optional[discord.TextChannel]:
    newest: Optional[discord.TextChannel] = None
    newest_ts = 0.0
    try:
        for channel in list(guild.text_channels):
            if not _is_open_ticket_channel(channel):
                continue
            created_at = getattr(channel, "created_at", None)
            created_ts = created_at.timestamp() if created_at else 0.0
            if created_ts and created_ts < (time.time() - 120):
                continue
            if created_ts >= newest_ts:
                newest = channel
                newest_ts = created_ts
    except Exception:
        return None
    return newest


async def _resolve_created_ticket_channel(guild: Optional[discord.Guild], result: Any, *, started_at: float) -> Optional[discord.TextChannel]:
    found = _extract_channel_from_result(result)
    if found is not None:
        return found

    if guild is None:
        return None

    cid = _extract_channel_id_from_result(result)
    if cid > 0:
        try:
            maybe = guild.get_channel(cid)
            if isinstance(maybe, discord.TextChannel):
                return maybe
            fetched = await guild.fetch_channel(cid)
            if isinstance(fetched, discord.TextChannel):
                return fetched
        except Exception:
            pass

    return _newest_open_ticket_channel(guild, started_at=started_at)


async def _enforce_after_create(guild: Optional[discord.Guild], result: Any, *, started_at: float) -> None:
    channel = await _resolve_created_ticket_channel(guild, result, started_at=started_at)
    if channel is None:
        _warn("ticket created but created channel could not be resolved for category enforcement")
        return
    if not _is_open_ticket_channel(channel):
        return
    await _move_ticket_channel_to_active(channel, reason_suffix="post-create placement")


async def _sweep_guild_open_tickets(guild: discord.Guild) -> tuple[int, int]:
    moved = 0
    checked = 0
    category = await _active_category_for_guild(guild)
    if category is None:
        return checked, moved

    for channel in list(getattr(guild, "text_channels", []) or []):
        if not _is_open_ticket_channel(channel):
            continue
        checked += 1
        if _channel_in_category(channel, category):
            continue
        if await _move_ticket_channel_to_active(channel, reason_suffix="startup open-ticket repair"):
            moved += 1
    return checked, moved


async def _startup_sweep(bot: Any) -> None:
    global _STARTUP_SWEEP_RAN
    if _STARTUP_SWEEP_RAN:
        return
    _STARTUP_SWEEP_RAN = True
    try:
        await asyncio.sleep(8)
    except Exception:
        pass

    total_checked = 0
    total_moved = 0
    try:
        for guild in list(getattr(bot, "guilds", []) or []):
            checked, moved = await _sweep_guild_open_tickets(guild)
            total_checked += checked
            total_moved += moved
        _log(f"startup open-ticket category sweep complete checked={total_checked} moved={total_moved}")
    except Exception as e:
        _warn(f"startup open-ticket category sweep failed: {e!r}")


def _attach_ready_listener(bot: Any) -> None:
    global _READY_LISTENER_ATTACHED
    if _READY_LISTENER_ATTACHED or bot is None:
        return
    _READY_LISTENER_ATTACHED = True

    async def _on_ready_ticket_category_enforcer() -> None:
        await _startup_sweep(bot)

    try:
        bot.add_listener(_on_ready_ticket_category_enforcer, "on_ready")
        _log("attached on_ready open-ticket category sweep listener")
    except Exception as e:
        _READY_LISTENER_ATTACHED = False
        _warn(f"failed attaching on_ready listener: {e!r}")


def _maybe_attach_bot() -> None:
    try:
        for module_name in ("stoney_verify.app", "stoney_verify.globals"):
            module = sys.modules.get(module_name)
            if module is None:
                continue
            bot = getattr(module, "bot", None)
            if bot is not None:
                _attach_ready_listener(bot)
                return
    except Exception:
        pass


def _patch_ticket_service(module: Any) -> None:
    global _PATCHING
    module_name = getattr(module, "__name__", "")
    patch_key = f"{module_name}:ticket_category_enforcer_v3"
    if patch_key in _PATCHED_MODULES or _PATCHING:
        return

    _PATCHING = True
    try:
        original_create = getattr(module, "create_ticket_channel", None)
        if callable(original_create) and not getattr(original_create, "_category_enforcer_wrapped", False):
            async def _create_ticket_channel_category_enforced(*args: Any, **kwargs: Any) -> Any:
                started_at = time.monotonic()
                guild = _guild_from_call(args, kwargs)
                result = await original_create(*args, **kwargs)
                try:
                    await _enforce_after_create(guild, result, started_at=started_at)
                except Exception as e:
                    _warn(f"post-create category enforcement failed guild={getattr(guild, 'id', None)}: {e!r}")
                return result

            try:
                setattr(_create_ticket_channel_category_enforced, "_category_enforcer_wrapped", True)
            except Exception:
                pass
            setattr(module, "create_ticket_channel", _create_ticket_channel_category_enforced)

        _PATCHED_MODULES.add(patch_key)
        _log(f"patched {module_name}; emergency open-ticket category repair uses native resolver")
    finally:
        _PATCHING = False


def _patch_loaded_once() -> None:
    try:
        module = sys.modules.get("stoney_verify.tickets_new.service")
        if module is not None:
            _patch_ticket_service(module)
    except Exception:
        pass
    _maybe_attach_bot()


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        # Only react to modules this shim owns. No broad patch scans.
        if name == "stoney_verify.tickets_new.service" or name.endswith("tickets_new.service"):
            target = sys.modules.get("stoney_verify.tickets_new.service") or sys.modules.get(name)
            if target is not None:
                _patch_ticket_service(target)
        elif name in {"stoney_verify.app", "stoney_verify.globals"} or name.endswith("stoney_verify.app") or name.endswith("stoney_verify.globals"):
            _maybe_attach_bot()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


builtins.__import__ = _safe_import
_patch_loaded_once()
_log("loaded; active ticket category emergency repair active")
