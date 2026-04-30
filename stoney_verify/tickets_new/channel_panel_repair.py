from __future__ import annotations

"""
Ticket channel control-panel repair.

This replaces the old root-level runtime_ticket_channel_panel_repair_patch.py.

A ticket channel can exist without its in-channel controls if Discord permissions
blocked the original send, or if a broken/manual move happened during testing.
This module keeps the behavior in the ticket package instead of the project root:
- repairs the in-ticket control panel after ticket creation
- sweeps existing open ticket channels on ready
- avoids duplicate panels when one already exists
"""

import asyncio
import builtins
import importlib
import inspect
import re
import sys
from datetime import datetime, timezone
from typing import Any, Optional

import discord

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED_MODULES: set[str] = set()
_READY_LISTENER_ATTACHED = False
_STARTUP_SWEEP_RAN = False
_PATCHING = False

_OPEN_TICKET_RE = re.compile(r"^ticket-\d{1,8}$", re.I)
_RECENT_SCAN_LIMIT = 30


def _log(message: str) -> None:
    try:
        print(f"🧰 ticket_channel_panel_repair {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_channel_panel_repair {message}")
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


def _looks_like_open_ticket(channel: Any) -> bool:
    try:
        return isinstance(channel, discord.TextChannel) and bool(_OPEN_TICKET_RE.match(str(channel.name or "")))
    except Exception:
        return False


def _topic_owner_id(channel: discord.TextChannel) -> int:
    try:
        topic = str(channel.topic or "")
        m = re.search(r"(?:^|;)owner_id=(\d+)(?:;|$)", topic)
        return _safe_int(m.group(1), 0) if m else 0
    except Exception:
        return 0


def _topic_ticket_number(channel: discord.TextChannel) -> int:
    try:
        topic = str(channel.topic or "")
        m = re.search(r"(?:^|;)ticket_number=(\d+)(?:;|$)", topic)
        if m:
            return _safe_int(m.group(1), 0)
        m2 = re.search(r"(\d+)", str(channel.name or ""))
        return _safe_int(m2.group(1), 0) if m2 else 0
    except Exception:
        return 0


def _topic_category(channel: discord.TextChannel) -> str:
    try:
        topic = str(channel.topic or "")
        m = re.search(r"(?:^|;)category=([^;]+)(?:;|$)", topic)
        return str(m.group(1)).strip() if m else "support"
    except Exception:
        return "support"


def _permission_problem(channel: discord.TextChannel) -> str:
    try:
        me = channel.guild.me
        if me is None:
            return "bot member unavailable"
        perms = channel.permissions_for(me)
        missing: list[str] = []
        for label, ok in (
            ("View Channel", getattr(perms, "view_channel", False)),
            ("Read Message History", getattr(perms, "read_message_history", False)),
            ("Send Messages", getattr(perms, "send_messages", False)),
            ("Embed Links", getattr(perms, "embed_links", False)),
        ):
            if not ok:
                missing.append(label)
        return ", ".join(missing) if missing else "none"
    except Exception as e:
        return f"snapshot failed: {type(e).__name__}"


async def _has_existing_control_message(channel: discord.TextChannel) -> bool:
    try:
        me = channel.guild.me
        async for msg in channel.history(limit=_RECENT_SCAN_LIMIT, oldest_first=False):
            try:
                if me is not None and int(getattr(msg.author, "id", 0) or 0) != int(me.id):
                    continue
                if getattr(msg, "components", None):
                    return True
                for embed in getattr(msg, "embeds", []) or []:
                    title = str(getattr(embed, "title", "") or "").lower()
                    desc = str(getattr(embed, "description", "") or "").lower()
                    if "ticket" in title and ("close" in desc or "claim" in desc or "staff" in desc):
                        return True
            except Exception:
                continue
    except discord.Forbidden as e:
        _warn(f"cannot scan ticket controls channel={channel.id} missing={_permission_problem(channel)} raw={e!r}")
        return True
    except Exception as e:
        _warn(f"cannot scan ticket controls channel={channel.id}: {e!r}")
        return True
    return False


def _instantiate_view(panel_mod: Any, channel: discord.TextChannel) -> Optional[discord.ui.View]:
    cls = getattr(panel_mod, "TicketChannelActionsView", None)
    if cls is None:
        return None

    owner_id = _topic_owner_id(channel)
    ticket_number = _topic_ticket_number(channel)
    category = _topic_category(channel)

    attempts = [
        {},
        {"channel_id": int(channel.id)},
        {"ticket_channel_id": int(channel.id)},
        {"guild_id": int(channel.guild.id), "channel_id": int(channel.id)},
        {"owner_id": int(owner_id), "ticket_number": int(ticket_number), "category": category},
        {"guild_id": int(channel.guild.id), "owner_id": int(owner_id), "ticket_number": int(ticket_number), "category": category},
    ]

    for kwargs in attempts:
        try:
            sig = inspect.signature(cls)
            filtered = {k: v for k, v in kwargs.items() if k in sig.parameters}
            return cls(**filtered)
        except TypeError:
            continue
        except Exception:
            continue

    try:
        return cls()
    except Exception:
        return None


def _ticket_embed(channel: discord.TextChannel) -> discord.Embed:
    owner_id = _topic_owner_id(channel)
    ticket_number = _topic_ticket_number(channel)
    category = _topic_category(channel).replace("_", " ").title()

    desc = (
        "Thanks — your ticket is open. A staff member will review it here.\n\n"
        "**Staff tools**\n"
        "Use the buttons below to claim, manage, close, or move this ticket."
    )
    if owner_id:
        desc = f"<@{owner_id}>\n\n" + desc

    embed = discord.Embed(
        title=f"🎫 Ticket #{ticket_number or '?'} — {category}",
        description=desc,
        color=discord.Color.blurple(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=f"Guild {channel.guild.id} • channel {channel.id}")
    return embed


async def ensure_ticket_channel_panel(channel: discord.TextChannel, *, reason: str = "repair") -> bool:
    if not _looks_like_open_ticket(channel):
        return False

    if await _has_existing_control_message(channel):
        return False

    try:
        panel_mod = importlib.import_module("stoney_verify.tickets_new.panel")
        view = _instantiate_view(panel_mod, channel)
        embed = _ticket_embed(channel)
        kwargs: dict[str, Any] = {"embed": embed, "allowed_mentions": discord.AllowedMentions.none()}
        if view is not None:
            kwargs["view"] = view
        await channel.send(**kwargs)
        _log(f"posted missing ticket control panel channel={channel.id} reason={reason}")
        return True
    except discord.Forbidden as e:
        _warn(f"cannot post ticket control panel channel={channel.id} missing={_permission_problem(channel)} raw={e!r}")
    except Exception as e:
        _warn(f"failed posting ticket control panel channel={channel.id}: {e!r}")
    return False


def _extract_channel_from_result(guild: Optional[discord.Guild], result: Any) -> Optional[discord.TextChannel]:
    try:
        if isinstance(result, discord.TextChannel):
            return result
        if isinstance(result, dict):
            for key in ("channel", "ticket_channel"):
                value = result.get(key)
                if isinstance(value, discord.TextChannel):
                    return value
            for key in ("channel_id", "discord_thread_id", "id"):
                cid = _safe_int(result.get(key), 0)
                if cid > 0 and guild is not None:
                    maybe = guild.get_channel(cid)
                    if isinstance(maybe, discord.TextChannel):
                        return maybe
        if isinstance(result, (tuple, list)):
            for item in result:
                found = _extract_channel_from_result(guild, item)
                if found is not None:
                    return found
    except Exception:
        pass
    return None


def _guild_from_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Optional[discord.Guild]:
    for value in (kwargs.get("guild"), kwargs.get("owner"), kwargs.get("member"), kwargs.get("interaction"), *list(args)):
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


def _patch_service(module: Any) -> None:
    global _PATCHING
    key = "service:create_ticket_panel_repair_v1"
    if key in _PATCHED_MODULES or _PATCHING:
        return

    original = getattr(module, "create_ticket_channel", None)
    if not callable(original) or getattr(original, "_ticket_panel_repair_wrapped", False):
        _PATCHED_MODULES.add(key)
        return

    _PATCHING = True
    try:
        async def _create_ticket_channel_with_panel_repair(*args: Any, **kwargs: Any) -> Any:
            guild = _guild_from_call(args, kwargs)
            result = await original(*args, **kwargs)
            try:
                channel = _extract_channel_from_result(guild, result)
                if isinstance(channel, discord.TextChannel):
                    await ensure_ticket_channel_panel(channel, reason="post-create")
            except Exception as e:
                _warn(f"post-create ticket panel repair failed: {e!r}")
            return result

        try:
            setattr(_create_ticket_channel_with_panel_repair, "_ticket_panel_repair_wrapped", True)
        except Exception:
            pass
        setattr(module, "create_ticket_channel", _create_ticket_channel_with_panel_repair)
        _PATCHED_MODULES.add(key)
        _log("patched tickets_new.service.create_ticket_channel with missing panel repair")
    finally:
        _PATCHING = False


async def _startup_sweep(bot: Any) -> None:
    global _STARTUP_SWEEP_RAN
    if _STARTUP_SWEEP_RAN:
        return
    _STARTUP_SWEEP_RAN = True
    try:
        await asyncio.sleep(12)
    except Exception:
        pass

    checked = 0
    repaired = 0
    try:
        for guild in list(getattr(bot, "guilds", []) or []):
            for channel in list(getattr(guild, "text_channels", []) or []):
                if not _looks_like_open_ticket(channel):
                    continue
                checked += 1
                if await ensure_ticket_channel_panel(channel, reason="startup-sweep"):
                    repaired += 1
        _log(f"startup ticket panel repair sweep complete checked={checked} repaired={repaired}")
    except Exception as e:
        _warn(f"startup ticket panel repair sweep failed: {e!r}")


def _attach_ready_listener(bot: Any) -> None:
    global _READY_LISTENER_ATTACHED
    if _READY_LISTENER_ATTACHED or bot is None:
        return
    _READY_LISTENER_ATTACHED = True

    async def _on_ready_ticket_panel_repair() -> None:
        await _startup_sweep(bot)

    try:
        bot.add_listener(_on_ready_ticket_panel_repair, "on_ready")
        _log("attached on_ready ticket panel repair sweep listener")
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


def _patch_loaded_once() -> None:
    try:
        service = sys.modules.get("stoney_verify.tickets_new.service")
        if service is not None:
            _patch_service(service)
    except Exception:
        pass
    _maybe_attach_bot()


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.tickets_new.service" or name.endswith("tickets_new.service"):
            target = sys.modules.get("stoney_verify.tickets_new.service") or sys.modules.get(name)
            if target is not None:
                _patch_service(target)
        elif name in {"stoney_verify.app", "stoney_verify.globals"} or name.endswith("stoney_verify.app") or name.endswith("stoney_verify.globals"):
            _maybe_attach_bot()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


builtins.__import__ = _safe_import
_patch_loaded_once()
_log("loaded; missing in-ticket control panel repair active")


__all__ = ["ensure_ticket_channel_panel"]
