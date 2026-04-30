from __future__ import annotations

"""
Ticket creation category guard.

This replaces the old root-level runtime_ticket_creation_native_category_patch.py.
It keeps ticket creation pointed at the configured per-guild Active Tickets
category while the larger ticket service continues being simplified.

Kept inside stoney_verify/tickets_new so the behavior lives with ticket code,
not as root-folder duct tape.
"""

import builtins
import inspect
import sys
from typing import Any, Optional

import discord

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED: set[str] = set()
_PATCHING = False


def _log(message: str) -> None:
    try:
        print(f"🎫 ticket_creation_category_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ ticket_creation_category_guard {message}")
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
    from .category_resolver import resolve_active_ticket_category

    resolved = await resolve_active_ticket_category(guild, refresh=True, require_manage_channels=True)
    return resolved.category


def _set_category_kwargs(fn: Any, kwargs: dict[str, Any], category: discord.CategoryChannel) -> None:
    cid = int(category.id)
    for name in ("parent_category", "category"):
        if _signature_accepts(fn, name) and not kwargs.get(name):
            kwargs[name] = category
    if _signature_accepts(fn, "parent") and not kwargs.get("parent"):
        kwargs["parent"] = category
    for name in ("parent_category_id", "explicit_parent_category_id", "ticket_category_id"):
        if _signature_accepts(fn, name) and not kwargs.get(name):
            kwargs[name] = cid


def _result_channel(result: Any, guild: discord.Guild) -> Optional[discord.TextChannel]:
    try:
        if isinstance(result, discord.TextChannel):
            return result
        if isinstance(result, dict):
            cid = _safe_int(result.get("channel_id") or result.get("discord_thread_id"), 0)
            maybe = guild.get_channel(cid) if cid > 0 else None
            return maybe if isinstance(maybe, discord.TextChannel) else None
        if isinstance(result, (tuple, list)):
            for item in result:
                found = _result_channel(item, guild)
                if found is not None:
                    return found
    except Exception:
        return None
    return None


def _patch_service(module: Any) -> None:
    global _PATCHING
    key = "service:create_ticket_native_category"
    if key in _PATCHED or _PATCHING:
        return

    original = getattr(module, "create_ticket_channel", None)
    if not callable(original):
        return
    if getattr(original, "_native_category_wrapped", False):
        _PATCHED.add(key)
        return

    _PATCHING = True
    try:
        async def create_ticket_channel_native_category(*args: Any, **kwargs: Any) -> Any:
            guild = _guild_from_call(args, kwargs)
            if guild is None:
                return await original(*args, **kwargs)

            category = await _configured_active_category(guild)
            _set_category_kwargs(original, kwargs, category)

            result = await original(*args, **kwargs)

            # Strict sanity check: ticket creation must land in the configured
            # active category. If the original service ignores the category
            # argument, move it immediately rather than leaving an orphan.
            channel = _result_channel(result, guild)
            if isinstance(channel, discord.TextChannel):
                current_id = int(getattr(channel.category, "id", 0) or 0)
                if current_id != int(category.id):
                    await channel.edit(
                        category=category,
                        sync_permissions=False,
                        reason="Ticket creation guard -> move to configured active category",
                    )
                    _warn(
                        f"ticket channel was corrected after creation guild={guild.id} "
                        f"channel={channel.id} category={category.id}"
                    )

            _log(f"ticket create active category enforced guild={guild.id} category={category.id}")
            return result

        try:
            setattr(create_ticket_channel_native_category, "_native_category_wrapped", True)
        except Exception:
            pass
        setattr(module, "create_ticket_channel", create_ticket_channel_native_category)
        _PATCHED.add(key)
        _log("patched tickets_new.service.create_ticket_channel with active category guard")

        panel = sys.modules.get("stoney_verify.tickets_new.panel")
        if panel is not None:
            _patch_panel(panel)
    finally:
        _PATCHING = False


def _patch_panel(module: Any) -> None:
    key = "panel:create_ticket_channel_reference"
    if key in _PATCHED:
        return
    try:
        service = sys.modules.get("stoney_verify.tickets_new.service")
        if service is not None and callable(getattr(service, "create_ticket_channel", None)):
            setattr(module, "create_ticket_channel", getattr(service, "create_ticket_channel"))
            _PATCHED.add(key)
            _log("updated tickets_new.panel.create_ticket_channel direct reference")
    except Exception:
        pass


def _patch_loaded_once() -> None:
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
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


builtins.__import__ = _safe_import
_patch_loaded_once()
_log("loaded; ticket creation active category guard enabled")
