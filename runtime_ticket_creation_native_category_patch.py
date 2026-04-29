from __future__ import annotations

"""
Native ticket creation category patch.

The post-create enforcer is only a safety net. This patch fixes the source path:
create_ticket_channel now receives a resolved configured active category before
creating the channel. If the configured category is missing/unusable, ticket
creation fails loudly instead of creating ticket-#### in the wrong place.

It also updates tickets_new.panel.create_ticket_channel because that module
imports the function directly at import time.
"""

import asyncio
import builtins
import inspect
import sys
from typing import Any, Optional

import discord

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED: set[str] = set()


def _log(message: str) -> None:
    try:
        print(f"🎫 runtime_ticket_creation_native_category {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ runtime_ticket_creation_native_category {message}")
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


def _signature_accepts(fn: Any, name: str) -> bool:
    try:
        sig = inspect.signature(fn)
        if name in sig.parameters:
            return True
        return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    except Exception:
        return False


async def _configured_active_category(guild: discord.Guild) -> discord.CategoryChannel:
    try:
        from stoney_verify.guild_config import get_guild_config

        cfg = await asyncio.wait_for(get_guild_config(guild.id, refresh=True), timeout=5.0)
    except Exception as e:
        raise RuntimeError(f"Could not load this server's saved ticket setup: {type(e).__name__}: {e}")

    category_id = _safe_int(getattr(cfg, "ticket_category_id", 0), 0)
    if category_id <= 0:
        raise RuntimeError("Open ticket category is not configured. Run `/stoney setup-tickets` or `/stoney setup-defaults`.")

    channel = guild.get_channel(category_id)
    if not isinstance(channel, discord.CategoryChannel):
        raise RuntimeError(f"Configured open ticket category `{category_id}` no longer exists or is not a category.")

    me = guild.me
    if me is not None:
        perms = channel.permissions_for(me)
        missing: list[str] = []
        if not perms.view_channel:
            missing.append("View Channel")
        if not perms.manage_channels:
            missing.append("Manage Channels")
        if missing:
            raise RuntimeError(f"I cannot create tickets in {channel.name}. Missing: {', '.join(missing)}.")

    return channel


def _set_category_kwargs(fn: Any, kwargs: dict[str, Any], category: discord.CategoryChannel) -> None:
    cid = int(category.id)
    for name in ("parent_category", "category"):
        if _signature_accepts(fn, name) and not kwargs.get(name):
            kwargs[name] = category
    for name in ("parent",):
        if _signature_accepts(fn, name) and not kwargs.get(name):
            kwargs[name] = category
    for name in ("parent_category_id", "explicit_parent_category_id", "ticket_category_id"):
        if _signature_accepts(fn, name) and not kwargs.get(name):
            kwargs[name] = cid


def _patch_service(module: Any) -> None:
    key = "service:create_ticket_native_category"
    if key in _PATCHED:
        return

    original = getattr(module, "create_ticket_channel", None)
    if not callable(original):
        return
    if getattr(original, "_native_category_wrapped", False):
        _PATCHED.add(key)
        return

    async def create_ticket_channel_native_category(*args: Any, **kwargs: Any) -> Any:
        guild = _guild_from_call(args, kwargs)
        if guild is None:
            return await original(*args, **kwargs)

        category = await _configured_active_category(guild)
        _set_category_kwargs(original, kwargs, category)

        result = await original(*args, **kwargs)

        # Verify the source behavior. This is not the normal placement mechanism;
        # it is a strict sanity check so bad creation paths are visible.
        try:
            channel = None
            if isinstance(result, discord.TextChannel):
                channel = result
            elif isinstance(result, dict):
                cid = _safe_int(result.get("channel_id") or result.get("discord_thread_id"), 0)
                maybe = guild.get_channel(cid) if cid > 0 else None
                if isinstance(maybe, discord.TextChannel):
                    channel = maybe
            elif isinstance(result, (tuple, list)):
                for item in result:
                    if isinstance(item, discord.TextChannel):
                        channel = item
                        break
                    if isinstance(item, dict):
                        cid = _safe_int(item.get("channel_id") or item.get("discord_thread_id"), 0)
                        maybe = guild.get_channel(cid) if cid > 0 else None
                        if isinstance(maybe, discord.TextChannel):
                            channel = maybe
                            break
            if isinstance(channel, discord.TextChannel) and int(getattr(channel.category, "id", 0) or 0) != int(category.id):
                raise RuntimeError(
                    f"Ticket channel {channel.name} was created outside configured category {category.name}; source placement failed."
                )
        except RuntimeError:
            raise
        except Exception:
            pass

        _log(f"ticket create native category source enforced guild={guild.id} category={category.id}")
        return result

    try:
        setattr(create_ticket_channel_native_category, "_native_category_wrapped", True)
    except Exception:
        pass
    setattr(module, "create_ticket_channel", create_ticket_channel_native_category)
    _PATCHED.add(key)
    _log("patched tickets_new.service.create_ticket_channel to require configured category before create")

    panel = sys.modules.get("stoney_verify.tickets_new.panel")
    if panel is not None:
        try:
            setattr(panel, "create_ticket_channel", create_ticket_channel_native_category)
            _log("updated tickets_new.panel.create_ticket_channel direct reference")
        except Exception:
            pass


def _patch_panel(module: Any) -> None:
    try:
        service = sys.modules.get("stoney_verify.tickets_new.service")
        if service is not None and callable(getattr(service, "create_ticket_channel", None)):
            setattr(module, "create_ticket_channel", getattr(service, "create_ticket_channel"))
            _log("refreshed panel create_ticket_channel reference")
    except Exception:
        pass


def _patch_loaded() -> None:
    try:
        service = sys.modules.get("stoney_verify.tickets_new.service")
        if service is not None:
            _patch_service(service)
    except Exception as e:
        _warn(f"service patch failed: {e!r}")
    try:
        panel = sys.modules.get("stoney_verify.tickets_new.panel")
        if panel is not None:
            _patch_panel(panel)
    except Exception:
        pass


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if name == "stoney_verify.tickets_new.service" or name.endswith("tickets_new.service"):
            target = sys.modules.get("stoney_verify.tickets_new.service") or sys.modules.get(name)
            if target is not None:
                _patch_service(target)
        elif name == "stoney_verify.tickets_new.panel" or name.endswith("tickets_new.panel"):
            target = sys.modules.get("stoney_verify.tickets_new.panel") or sys.modules.get(name)
            if target is not None:
                _patch_panel(target)
        else:
            _patch_loaded()
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


builtins.__import__ = _safe_import
_patch_loaded()
_log("loaded; native ticket category creation enforcement active")
