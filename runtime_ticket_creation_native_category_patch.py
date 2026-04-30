from __future__ import annotations

"""
Native ticket creation category patch.

This shim keeps old direct imports protected while ticket creation is being folded
into tickets_new.service natively.

Important safety detail:
Do not patch-scan on every import. That creates ugly log spam and can cause
import-hook recursion on hosts like Discloud. Only react when the target ticket
modules import, and only log reference refreshes once.
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
    from stoney_verify.tickets_new.category_resolver import resolve_active_ticket_category

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

            # Strict sanity check so bad creation paths are visible.
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
                    from stoney_verify.tickets_new.category_resolver import TicketCategoryResolutionError

                    raise TicketCategoryResolutionError(
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
        # Only react to the two modules this shim owns. Do not run _patch_loaded
        # on every import; that was the source of the console spam.
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
_log("loaded; native ticket category creation enforcement active")
