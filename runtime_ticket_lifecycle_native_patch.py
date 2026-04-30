from __future__ import annotations

"""
Native ticket lifecycle wiring patch.

The clean helpers live in:
    stoney_verify.tickets_new.lifecycle_categories

This shim wires BOTH of these call paths into the native helpers:
- stoney_verify.tickets_new.service
- stoney_verify.commands_ext.ticket_admin

Why both matter:
The public /ticket close command imports ticket_admin through a relative
`from . import ticket_admin as legacy`. Python reports that import as
`stoney_verify.commands_ext` with fromlist=("ticket_admin", ...), not always as
`stoney_verify.commands_ext.ticket_admin`. The previous patch missed that path,
so /ticket close could still say "Closed" while never moving the channel.
"""

import builtins
import sys
from typing import Any

import discord

_ORIGINAL_IMPORT = builtins.__import__
_PATCHED: set[str] = set()
_PATCHING = False
_TARGET_MODULES = (
    "stoney_verify.tickets_new.service",
    "stoney_verify.commands_ext.ticket_admin",
)


def _log(message: str) -> None:
    try:
        print(f"🎫 runtime_ticket_lifecycle_native {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ runtime_ticket_lifecycle_native {message}")
    except Exception:
        pass


def _permission_snapshot(channel: discord.TextChannel) -> str:
    try:
        me = channel.guild.me
        if me is None:
            return "bot member unavailable"
        perms = channel.permissions_for(me)
        missing: list[str] = []
        for label, ok in (
            ("View Channel", getattr(perms, "view_channel", False)),
            ("Manage Channels", getattr(perms, "manage_channels", False)),
            ("Read Message History", getattr(perms, "read_message_history", False)),
            ("Send Messages", getattr(perms, "send_messages", False)),
        ):
            if not ok:
                missing.append(label)
        return "missing=" + ", ".join(missing) if missing else "permissions=ok"
    except Exception as e:
        return f"permission_snapshot_failed={type(e).__name__}"


async def _move_archive(channel: discord.TextChannel) -> bool:
    try:
        from stoney_verify.tickets_new.lifecycle_categories import move_ticket_to_archive_category

        result = await move_ticket_to_archive_category(channel)
        if result.already_correct:
            _log(
                "archive move already correct "
                f"guild={channel.guild.id} channel={channel.id} category={result.target_category_name} source={result.source}"
            )
        else:
            _log(
                "archive move complete "
                f"guild={channel.guild.id} channel={channel.id} category={result.target_category_name} source={result.source}"
            )
        return True
    except discord.Forbidden as e:
        _warn(
            "archive lifecycle move forbidden "
            f"guild={getattr(getattr(channel, 'guild', None), 'id', None)} channel={getattr(channel, 'id', None)} "
            f"category={getattr(getattr(channel, 'category', None), 'name', None)!r} {_permission_snapshot(channel)} raw={e!r}"
        )
        return False
    except Exception as e:
        _warn(
            "archive lifecycle move failed "
            f"guild={getattr(getattr(channel, 'guild', None), 'id', None)} channel={getattr(channel, 'id', None)} "
            f"category={getattr(getattr(channel, 'category', None), 'name', None)!r} error={e!r}"
        )
        return False


async def _move_active(channel: discord.TextChannel) -> bool:
    try:
        from stoney_verify.tickets_new.lifecycle_categories import move_ticket_to_active_category

        result = await move_ticket_to_active_category(channel)
        if result.already_correct:
            _log(
                "active move already correct "
                f"guild={channel.guild.id} channel={channel.id} category={result.target_category_name} source={result.source}"
            )
        else:
            _log(
                "active move complete "
                f"guild={channel.guild.id} channel={channel.id} category={result.target_category_name} source={result.source}"
            )
        return True
    except discord.Forbidden as e:
        _warn(
            "active lifecycle move forbidden "
            f"guild={getattr(getattr(channel, 'guild', None), 'id', None)} channel={getattr(channel, 'id', None)} "
            f"category={getattr(getattr(channel, 'category', None), 'name', None)!r} {_permission_snapshot(channel)} raw={e!r}"
        )
        return False
    except Exception as e:
        _warn(
            "active lifecycle move failed "
            f"guild={getattr(getattr(channel, 'guild', None), 'id', None)} channel={getattr(channel, 'id', None)} "
            f"category={getattr(getattr(channel, 'category', None), 'name', None)!r} error={e!r}"
        )
        return False


def _verify_module_patched(module: Any) -> None:
    try:
        module_name = getattr(module, "__name__", "unknown")
        archive_fn = getattr(module, "_move_ticket_to_archive_if_configured", None)
        active_fn = getattr(module, "_move_ticket_to_active_if_configured", None)
        if callable(archive_fn) and not getattr(archive_fn, "_native_lifecycle_wrapped", False):
            _warn(f"{module_name} archive move helper is still unpatched after patch attempt")
        if callable(active_fn) and not getattr(active_fn, "_native_lifecycle_wrapped", False):
            _warn(f"{module_name} active move helper is still unpatched after patch attempt")
    except Exception:
        pass


def _patch_lifecycle_module(module: Any) -> None:
    global _PATCHING

    module_name = getattr(module, "__name__", "")
    key = f"{module_name}:native_lifecycle_categories_v3"
    if key in _PATCHED or _PATCHING:
        return

    _PATCHING = True
    try:
        patched_any = False

        original_archive = getattr(module, "_move_ticket_to_archive_if_configured", None)
        if callable(original_archive) and not getattr(original_archive, "_native_lifecycle_wrapped", False):
            async def _move_ticket_to_archive_if_configured_native(channel: discord.TextChannel) -> bool:
                moved = await _move_archive(channel)
                if moved:
                    return True
                try:
                    legacy_moved = bool(await original_archive(channel))
                    if not legacy_moved:
                        _warn(
                            "archive move returned false after native+legacy attempts "
                            f"module={module_name} guild={channel.guild.id} channel={channel.id} current_category={getattr(getattr(channel, 'category', None), 'name', None)!r}"
                        )
                    return legacy_moved
                except Exception as e:
                    _warn(f"legacy archive fallback failed module={module_name} channel={getattr(channel, 'id', None)}: {e!r}")
                    return False

            try:
                setattr(_move_ticket_to_archive_if_configured_native, "_native_lifecycle_wrapped", True)
            except Exception:
                pass
            setattr(module, "_move_ticket_to_archive_if_configured", _move_ticket_to_archive_if_configured_native)
            patched_any = True

        original_active = getattr(module, "_move_ticket_to_active_if_configured", None)
        if callable(original_active) and not getattr(original_active, "_native_lifecycle_wrapped", False):
            async def _move_ticket_to_active_if_configured_native(channel: discord.TextChannel) -> bool:
                moved = await _move_active(channel)
                if moved:
                    return True
                try:
                    legacy_moved = bool(await original_active(channel))
                    if not legacy_moved:
                        _warn(
                            "active move returned false after native+legacy attempts "
                            f"module={module_name} guild={channel.guild.id} channel={channel.id} current_category={getattr(getattr(channel, 'category', None), 'name', None)!r}"
                        )
                    return legacy_moved
                except Exception as e:
                    _warn(f"legacy active fallback failed module={module_name} channel={getattr(channel, 'id', None)}: {e!r}")
                    return False

            try:
                setattr(_move_ticket_to_active_if_configured_native, "_native_lifecycle_wrapped", True)
            except Exception:
                pass
            setattr(module, "_move_ticket_to_active_if_configured", _move_ticket_to_active_if_configured_native)
            patched_any = True

        if patched_any:
            _PATCHED.add(key)
            _log(f"patched {module_name}; close/reopen category movement now uses native lifecycle helpers")
        _verify_module_patched(module)
    finally:
        _PATCHING = False


def _patch_loaded() -> None:
    for module_name in _TARGET_MODULES:
        try:
            module = sys.modules.get(module_name)
            if module is not None:
                _patch_lifecycle_module(module)
        except Exception as e:
            _warn(f"loaded lifecycle patch failed module={module_name}: {e!r}")


def _fromlist_names(fromlist: Any) -> set[str]:
    try:
        return {str(x) for x in (fromlist or ())}
    except Exception:
        return set()


def _should_patch_after_import(name: str, fromlist: Any) -> bool:
    names = _fromlist_names(fromlist)
    if name in _TARGET_MODULES:
        return True
    if name.endswith("tickets_new.service") or name.endswith("commands_ext.ticket_admin"):
        return True
    if name == "stoney_verify.commands_ext" and ("ticket_admin" in names or "public_ticket_group" in names):
        return True
    if name.endswith("commands_ext") and ("ticket_admin" in names or "public_ticket_group" in names):
        return True
    return False


def _safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    module = _ORIGINAL_IMPORT(name, globals, locals, fromlist, level)
    try:
        if _should_patch_after_import(str(name), fromlist):
            _patch_loaded()
            # If the public ticket group imported `legacy = ticket_admin`, this
            # verifies the module object it points at is patched too.
            public_group = sys.modules.get("stoney_verify.commands_ext.public_ticket_group")
            if public_group is not None:
                legacy = getattr(public_group, "legacy", None)
                if legacy is not None:
                    _patch_lifecycle_module(legacy)
    except Exception as e:
        _warn(f"post-import patch failed for {name}: {e!r}")
    return module


builtins.__import__ = _safe_import
_patch_loaded()
_log("loaded; native ticket lifecycle movement wiring active")
