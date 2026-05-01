from __future__ import annotations

"""
Ticket creation category guard.

Keeps ticket creation pointed at the configured per-guild Active Tickets category
while remaining compatible with older panel callers and newer service signatures.

Important compatibility rule:
Some panel/intake paths still call create_ticket_channel(..., parent_category=...).
The current service may not accept that keyword. This guard translates/removes
legacy category kwargs before calling the real service so public ticket creation
cannot fail with an unexpected-keyword TypeError.
"""

import builtins
import inspect
import sys
from typing import Any, Optional

import discord

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED: set[str] = set()
_PATCHING = False

_CATEGORY_OBJECT_KWARGS = ("parent_category", "category", "parent")
_CATEGORY_ID_KWARGS = ("parent_category_id", "explicit_parent_category_id", "ticket_category_id")


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
        kwargs.get("parent_category"),
        kwargs.get("category"),
        kwargs.get("parent"),
        *list(args),
    )


def _signature_parameters(fn: Any) -> dict[str, inspect.Parameter]:
    try:
        return dict(inspect.signature(fn).parameters)
    except Exception:
        return {}


def _signature_accepts(fn: Any, name: str) -> bool:
    try:
        params = _signature_parameters(fn)
        if name in params:
            return True
        return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    except Exception:
        return False


def _signature_accepts_kwargs(fn: Any) -> bool:
    try:
        return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in _signature_parameters(fn).values())
    except Exception:
        return False


def _category_from_kwargs(kwargs: dict[str, Any]) -> Optional[discord.CategoryChannel]:
    for name in _CATEGORY_OBJECT_KWARGS:
        value = kwargs.get(name)
        if isinstance(value, discord.CategoryChannel):
            return value
    return None


def _category_id_from_kwargs(kwargs: dict[str, Any]) -> int:
    for name in _CATEGORY_ID_KWARGS:
        cid = _safe_int(kwargs.get(name), 0)
        if cid > 0:
            return cid
    category = _category_from_kwargs(kwargs)
    return int(category.id) if category is not None else 0


async def _configured_active_category(guild: discord.Guild) -> discord.CategoryChannel:
    from .category_resolver import resolve_active_ticket_category

    resolved = await resolve_active_ticket_category(guild, refresh=True, require_manage_channels=True)
    return resolved.category


def _remove_unsupported_category_kwargs(fn: Any, kwargs: dict[str, Any]) -> None:
    """Drop legacy category kwargs the real service does not accept.

    The wrapper itself accepts every kwarg, so callers can still pass legacy names.
    The original service should only receive names its signature actually accepts.
    """
    if _signature_accepts_kwargs(fn):
        return
    for name in (*_CATEGORY_OBJECT_KWARGS, *_CATEGORY_ID_KWARGS):
        if not _signature_accepts(fn, name):
            kwargs.pop(name, None)


def _set_category_kwargs(fn: Any, kwargs: dict[str, Any], category: discord.CategoryChannel) -> None:
    """Translate the resolved Discord category into whatever the service accepts."""
    cid = int(category.id)

    existing_category = _category_from_kwargs(kwargs) or category
    existing_cid = _category_id_from_kwargs(kwargs) or cid

    # Prefer object kwargs when the service supports them.
    for name in _CATEGORY_OBJECT_KWARGS:
        if _signature_accepts(fn, name) and not kwargs.get(name):
            kwargs[name] = existing_category
            break

    # Also support ID-based service signatures.
    for name in _CATEGORY_ID_KWARGS:
        if _signature_accepts(fn, name) and not kwargs.get(name):
            kwargs[name] = existing_cid
            break

    _remove_unsupported_category_kwargs(fn, kwargs)


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
                _remove_unsupported_category_kwargs(original, kwargs)
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
