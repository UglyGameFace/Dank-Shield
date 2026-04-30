from __future__ import annotations

import asyncio
import inspect
import sys
import traceback
from typing import Any, Dict, Optional, Tuple

import discord

from ..globals import bot
from .panel_runtime import (
    DEFAULT_PANEL_KEY,
    attach_panel_metadata_to_payload,
    build_panel_denial_message,
    evaluate_panel_ticket_request,
    normalize_category_slug,
    normalize_panel_key,
)
from .panel_repository import panel_creation_guard_scope


# ============================================================
# tickets_new/panel_creation_guard_runtime.py
# ------------------------------------------------------------
# Runtime guard that wraps ticket creation with DB-backed panel
# rules without rewriting the huge tickets_new/panel.py file.
#
# Why this exists:
# - panel.py is very large and currently working
# - rewriting it blindly is high risk
# - this wrapper protects the actual create_ticket_channel path
# - works even when panel.py imported create_ticket_channel directly
#
# What this enforces:
# - guild-scoped panel rules
# - per-owner/per-panel creation lock
# - per-guild creation semaphore
# - per-owner open ticket limit through panel_runtime
# - panel metadata snapshot attached to payload/meta/metadata
# - friendly denial handling for buttons/modals/commands
#
# Public-server posture:
# - no server-specific .env role/channel IDs required here
# - .env fallback only happens through guild_config/panel rules
# - no cross-guild mutable config
#
# Legal/privacy posture:
# - does not collect new private data
# - only attaches operational ticket metadata
# - denial messages never mention hidden moderation heuristics
# ============================================================


_PATCHED = False
_ERROR_PATCHED = False
_ORIGINAL_CREATE_TICKET_CHANNEL = None
_ORIGINAL_VIEW_ON_ERROR = None
_ORIGINAL_MODAL_ON_ERROR = None
_ORIGINAL_TREE_ON_ERROR = None

_CREATE_LOCK = asyncio.Lock()
_PATCHED_MODULE_ATTRS: set[Tuple[str, str]] = set()


class PanelTicketDenied(RuntimeError):
    def __init__(
        self,
        message: str,
        decision: Optional[Dict[str, Any]] = None,
        *,
        interaction_responded: bool = False,
    ):
        super().__init__(message)
        self.message = str(message or "Ticket creation denied by panel rules.")
        self.decision = dict(decision or {})
        self.interaction_responded = bool(interaction_responded)


# ============================================================
# Small helpers
# ============================================================

def _debug(message: str) -> None:
    try:
        print(f"🛡️ panel_creation_guard_runtime {message}")
    except Exception:
        pass


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        raw = str(value or "").strip().lower()
        if raw in {"1", "true", "yes", "y", "on"}:
            return True
        if raw in {"0", "false", "no", "n", "off"}:
            return False
        return default
    except Exception:
        return default


def _safe_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    return {}


def _is_discord_member(value: Any) -> bool:
    try:
        return isinstance(value, discord.Member)
    except Exception:
        return False


def _is_discord_guild(value: Any) -> bool:
    try:
        return isinstance(value, discord.Guild)
    except Exception:
        return False


def _is_discord_interaction(value: Any) -> bool:
    try:
        return isinstance(value, discord.Interaction)
    except Exception:
        return False


def _unwrap_panel_denial(error: BaseException) -> Optional[PanelTicketDenied]:
    cur: Optional[BaseException] = error

    for _ in range(8):
        if cur is None:
            return None
        if isinstance(cur, PanelTicketDenied):
            return cur

        cause = getattr(cur, "__cause__", None)
        context = getattr(cur, "__context__", None)

        if isinstance(cause, BaseException):
            cur = cause
            continue

        if isinstance(context, BaseException):
            cur = context
            continue

        return None

    return None


# ============================================================
# Interaction denial helpers
# ============================================================

async def _send_interaction_denial(
    interaction: Optional[discord.Interaction],
    message: str,
) -> bool:
    if interaction is None:
        return False

    content = _safe_str(message, "❌ You cannot open this ticket right now.")

    try:
        allowed = discord.AllowedMentions.none()

        if interaction.response.is_done():
            await interaction.followup.send(
                content,
                ephemeral=True,
                allowed_mentions=allowed,
            )
        else:
            await interaction.response.send_message(
                content,
                ephemeral=True,
                allowed_mentions=allowed,
            )

        return True
    except Exception as e:
        _debug(f"failed sending panel denial response: {repr(e)}")
        return False


def _extract_interaction_from_call(
    args: Tuple[Any, ...],
    kwargs: Dict[str, Any],
    mapped: Optional[Dict[str, Any]] = None,
) -> Optional[discord.Interaction]:
    try:
        for key in (
            "interaction",
            "ctx",
            "context",
            "source_interaction",
            "origin_interaction",
        ):
            if mapped and _is_discord_interaction(mapped.get(key)):
                return mapped.get(key)

            if _is_discord_interaction(kwargs.get(key)):
                return kwargs.get(key)

        for value in list(args) + list(kwargs.values()):
            if _is_discord_interaction(value):
                return value

        if mapped:
            for value in mapped.values():
                if _is_discord_interaction(value):
                    return value
    except Exception:
        pass

    return None


# ============================================================
# Call signature helpers
# ============================================================

def _extract_kwargs_from_signature(
    func: Any,
    args: Tuple[Any, ...],
    kwargs: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Best-effort mapping of positional args to parameter names.

    This keeps the wrapper compatible if create_ticket_channel is called
    with either positional or keyword args.
    """
    out = dict(kwargs or {})

    try:
        sig = inspect.signature(func)
        params = list(sig.parameters.keys())
        for idx, value in enumerate(args):
            if idx >= len(params):
                continue
            out.setdefault(params[idx], value)
    except Exception:
        pass

    return out


def _extract_guild_owner_from_call(
    original_func: Any,
    args: Tuple[Any, ...],
    kwargs: Dict[str, Any],
) -> Tuple[Optional[discord.Guild], Optional[discord.Member], Dict[str, Any]]:
    mapped = _extract_kwargs_from_signature(original_func, args, kwargs)

    guild = mapped.get("guild")
    owner = (
        mapped.get("owner")
        or mapped.get("member")
        or mapped.get("user")
        or mapped.get("requester")
        or mapped.get("target")
        or mapped.get("ticket_owner")
    )

    if not _is_discord_guild(guild):
        for value in list(args) + list(kwargs.values()):
            if _is_discord_guild(value):
                guild = value
                break

    if not _is_discord_member(owner):
        for value in list(args) + list(kwargs.values()):
            if _is_discord_member(value):
                owner = value
                break

    if not _is_discord_guild(guild) and _is_discord_member(owner):
        try:
            guild = owner.guild
        except Exception:
            guild = None

    if not _is_discord_member(owner):
        owner = None

    if not _is_discord_guild(guild):
        guild = None

    return guild, owner, mapped


def _extract_meta_from_call(mapped: Dict[str, Any]) -> Dict[str, Any]:
    meta = _safe_dict(mapped.get("metadata"))
    meta.update(_safe_dict(mapped.get("meta")))

    for key in ("extra", "details", "ticket_metadata"):
        extra = _safe_dict(mapped.get(key))
        if extra:
            meta.update(extra)

    return meta


def _extract_panel_key(mapped: Dict[str, Any]) -> str:
    meta = _extract_meta_from_call(mapped)

    for key in (
        "panel_key",
        "source_panel_key",
        "ticket_panel_key",
        "panel",
    ):
        value = mapped.get(key)
        if value:
            return normalize_panel_key(value)

    for key in (
        "panel_key",
        "source_panel_key",
        "ticket_panel_key",
        "panel",
    ):
        value = meta.get(key)
        if value:
            return normalize_panel_key(value)

    return DEFAULT_PANEL_KEY


def _extract_category_slug(mapped: Dict[str, Any]) -> str:
    meta = _extract_meta_from_call(mapped)

    for key in (
        "category_slug",
        "category",
        "ticket_category",
        "matched_category_slug",
    ):
        value = mapped.get(key)
        if value:
            return normalize_category_slug(value, default="support")

    for key in (
        "category_slug",
        "category",
        "ticket_category",
        "matched_category_slug",
    ):
        value = meta.get(key)
        if value:
            return normalize_category_slug(value, default="support")

    return "support"


def _extract_is_ghost(mapped: Dict[str, Any]) -> bool:
    meta = _extract_meta_from_call(mapped)

    for key in (
        "is_ghost",
        "ghost",
        "ghost_ticket",
    ):
        if key in mapped:
            return _safe_bool(mapped.get(key), False)

    for key in (
        "is_ghost",
        "ghost",
        "ghost_ticket",
    ):
        if key in meta:
            return _safe_bool(meta.get(key), False)

    return False


def _call_should_bypass_guard(mapped: Dict[str, Any]) -> bool:
    """
    Staff/system flows can explicitly bypass the guard.

    This should be rare. It exists for migrations/backfills/tests where
    create_ticket_channel may be used internally.
    """
    if _safe_bool(mapped.get("bypass_panel_rules"), False):
        return True

    meta = _extract_meta_from_call(mapped)
    if _safe_bool(meta.get("bypass_panel_rules"), False):
        return True

    source = _safe_str(mapped.get("source") or meta.get("source")).lower()
    if source in {
        "migration",
        "backfill",
        "startup_sync",
        "repair",
        "admin_force",
    }:
        return True

    return False


# ============================================================
# Metadata injection
# ============================================================

def _inject_metadata_into_kwargs(
    *,
    original_func: Any,
    args: Tuple[Any, ...],
    kwargs: Dict[str, Any],
    mapped: Dict[str, Any],
    panel_key: str,
    category_slug: str,
    decision: Dict[str, Any],
) -> Tuple[Tuple[Any, ...], Dict[str, Any]]:
    """
    Add panel metadata in the safest compatible way.

    Most code paths pass ticket metadata through metadata/meta kwargs.
    If the current signature does not support those names, we only add
    them when **kwargs exists.
    """
    out_kwargs = dict(kwargs or {})

    existing_payload = {
        "metadata": _safe_dict(mapped.get("metadata")),
        "meta": _safe_dict(mapped.get("meta")),
        "panel_key": panel_key,
        "category": category_slug,
    }

    enriched = attach_panel_metadata_to_payload(
        existing_payload,
        panel_key=panel_key,
        category_slug=category_slug,
        runtime=decision.get("panel"),
        decision=decision,
    )

    try:
        sig = inspect.signature(original_func)
        param_names = set(sig.parameters.keys())
        has_var_kwargs = any(
            p.kind == inspect.Parameter.VAR_KEYWORD
            for p in sig.parameters.values()
        )
    except Exception:
        param_names = set()
        has_var_kwargs = True

    if "metadata" in param_names or has_var_kwargs:
        merged = _safe_dict(mapped.get("metadata"))
        merged.update(_safe_dict(enriched.get("metadata")))
        out_kwargs["metadata"] = merged

    if "meta" in param_names or has_var_kwargs:
        merged = _safe_dict(mapped.get("meta"))
        merged.update(_safe_dict(enriched.get("meta")))
        out_kwargs["meta"] = merged

    if ("panel_key" in param_names or has_var_kwargs) and "panel_key" not in out_kwargs:
        out_kwargs["panel_key"] = panel_key

    return args, out_kwargs


# ============================================================
# Module patch helpers
# ============================================================

def _patch_module_attr(module_name: str, attr_name: str, wrapper: Any, original: Any) -> bool:
    try:
        module = sys.modules.get(module_name)
        if module is None:
            return False

        current = getattr(module, attr_name, None)
        if current is None:
            return False

        if current is original or getattr(current, "__name__", "") == getattr(original, "__name__", ""):
            setattr(module, attr_name, wrapper)
            _PATCHED_MODULE_ATTRS.add((module_name, attr_name))
            return True
    except Exception:
        return False

    return False


# ============================================================
# Error handling patches
# ============================================================

async def _handle_panel_denial_error(
    interaction: Optional[discord.Interaction],
    error: BaseException,
) -> bool:
    denial = _unwrap_panel_denial(error)
    if denial is None:
        return False

    if not denial.interaction_responded:
        sent = await _send_interaction_denial(interaction, denial.message)
        denial.interaction_responded = sent

    _debug(
        "suppressed panel denial error "
        f"responded={denial.interaction_responded} "
        f"source={denial.decision.get('source') if denial.decision else ''}"
    )
    return True


def install_panel_creation_error_handlers() -> bool:
    global _ERROR_PATCHED
    global _ORIGINAL_VIEW_ON_ERROR
    global _ORIGINAL_MODAL_ON_ERROR
    global _ORIGINAL_TREE_ON_ERROR

    if _ERROR_PATCHED:
        return True

    try:
        _ORIGINAL_VIEW_ON_ERROR = getattr(discord.ui.View, "on_error", None)
        _ORIGINAL_MODAL_ON_ERROR = getattr(discord.ui.Modal, "on_error", None)

        async def _sv_view_on_error(self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item[Any]) -> None:
            if await _handle_panel_denial_error(interaction, error):
                return

            original = _ORIGINAL_VIEW_ON_ERROR
            if callable(original):
                return await original(self, interaction, error, item)

            try:
                traceback.print_exception(type(error), error, error.__traceback__)
            except Exception:
                pass

        async def _sv_modal_on_error(self, interaction: discord.Interaction, error: Exception) -> None:
            if await _handle_panel_denial_error(interaction, error):
                return

            original = _ORIGINAL_MODAL_ON_ERROR
            if callable(original):
                return await original(self, interaction, error)

            try:
                traceback.print_exception(type(error), error, error.__traceback__)
            except Exception:
                pass

        discord.ui.View.on_error = _sv_view_on_error
        discord.ui.Modal.on_error = _sv_modal_on_error

        tree = getattr(bot, "tree", None)
        if tree is not None:
            _ORIGINAL_TREE_ON_ERROR = getattr(tree, "on_error", None)

            async def _sv_tree_on_error(interaction: discord.Interaction, error: app_commands.AppCommandError):  # type: ignore[name-defined]
                if await _handle_panel_denial_error(interaction, error):
                    return

                original = _ORIGINAL_TREE_ON_ERROR
                if callable(original):
                    return await original(interaction, error)

                try:
                    traceback.print_exception(type(error), error, error.__traceback__)
                except Exception:
                    pass

            try:
                from discord import app_commands  # noqa: F401
                tree.on_error = _sv_tree_on_error
            except Exception:
                pass

        _ERROR_PATCHED = True
        _debug("panel denial error handlers installed")
        return True
    except Exception as e:
        print("⚠️ Failed installing panel creation error handlers:", repr(e))
        try:
            traceback.print_exc()
        except Exception:
            pass
        return False


# ============================================================
# Guarded create_ticket_channel wrapper
# ============================================================

async def _guarded_create_ticket_channel(*args: Any, **kwargs: Any):
    global _ORIGINAL_CREATE_TICKET_CHANNEL

    original = _ORIGINAL_CREATE_TICKET_CHANNEL
    if original is None:
        from . import service as service_mod

        original = getattr(service_mod, "_sv_original_create_ticket_channel", None)
        if original is None:
            original = getattr(service_mod, "create_ticket_channel")
        _ORIGINAL_CREATE_TICKET_CHANNEL = original

    guild, owner, mapped = _extract_guild_owner_from_call(original, args, kwargs)
    interaction = _extract_interaction_from_call(args, kwargs, mapped)

    if _call_should_bypass_guard(mapped):
        return await original(*args, **kwargs)

    if guild is None or owner is None:
        # Do not break legacy/system calls we cannot understand.
        return await original(*args, **kwargs)

    if getattr(owner, "bot", False):
        return await original(*args, **kwargs)

    panel_key = _extract_panel_key(mapped)
    category_slug = _extract_category_slug(mapped)
    is_ghost = _extract_is_ghost(mapped)

    sem, lock = await panel_creation_guard_scope(
        guild_id=int(guild.id),
        owner_id=int(owner.id),
        panel_key=panel_key,
        semaphore_limit=8,
    )

    async with sem:
        async with lock:
            decision = await evaluate_panel_ticket_request(
                member=owner,
                panel_key=panel_key,
                category_slug=category_slug,
                is_ghost=is_ghost,
                enforce_owner_limit=True,
            )

            if not _safe_bool(decision.get("ok"), False):
                message = build_panel_denial_message(decision)

                sent = await _send_interaction_denial(interaction, message)

                _debug(
                    "blocked ticket creation "
                    f"guild={guild.id} owner={owner.id} panel={panel_key} "
                    f"category={category_slug} source={decision.get('source')} "
                    f"sent={sent} reason={decision.get('reason')}"
                )

                raise PanelTicketDenied(
                    message,
                    decision=decision,
                    interaction_responded=sent,
                )

            patched_args, patched_kwargs = _inject_metadata_into_kwargs(
                original_func=original,
                args=args,
                kwargs=kwargs,
                mapped=mapped,
                panel_key=panel_key,
                category_slug=category_slug,
                decision=decision,
            )

            return await original(*patched_args, **patched_kwargs)


# ============================================================
# Install / refresh
# ============================================================

def install_panel_creation_guard_runtime() -> bool:
    """
    Install the ticket creation wrapper.

    Safe to call repeatedly. Patches:
    - tickets_new.service.create_ticket_channel
    - tickets_new.panel.create_ticket_channel if panel.py already imported it
    - View/Modal/app-command error handlers for friendly denials
    """
    global _PATCHED
    global _ORIGINAL_CREATE_TICKET_CHANNEL

    install_panel_creation_error_handlers()

    if _PATCHED:
        refresh_panel_creation_guard_patch_targets()
        return True

    try:
        from . import service as service_mod

        current = getattr(service_mod, "create_ticket_channel", None)
        if current is None:
            _debug("service.create_ticket_channel missing; guard not installed")
            return False

        if getattr(current, "_sv_panel_creation_guard", False):
            _PATCHED = True
            refresh_panel_creation_guard_patch_targets()
            return True

        _ORIGINAL_CREATE_TICKET_CHANNEL = current
        setattr(service_mod, "_sv_original_create_ticket_channel", current)

        async def _wrapper(*args: Any, **kwargs: Any):
            return await _guarded_create_ticket_channel(*args, **kwargs)

        _wrapper.__name__ = "create_ticket_channel"
        _wrapper.__qualname__ = "create_ticket_channel"
        _wrapper.__doc__ = getattr(current, "__doc__", None)
        setattr(_wrapper, "_sv_panel_creation_guard", True)

        setattr(service_mod, "create_ticket_channel", _wrapper)
        _PATCHED_MODULE_ATTRS.add(("stoney_verify.tickets_new.service", "create_ticket_channel"))

        refresh_panel_creation_guard_patch_targets()

        _PATCHED = True
        _debug("ticket creation guard installed")
        return True
    except Exception as e:
        print("⚠️ Failed installing panel creation guard runtime:", repr(e))
        try:
            traceback.print_exc()
        except Exception:
            pass
        return False


def refresh_panel_creation_guard_patch_targets() -> None:
    """
    Call this after modules load if needed.

    It catches late imports where panel.py imported create_ticket_channel
    after this runtime module was installed.
    """
    try:
        from . import service as service_mod

        wrapper = getattr(service_mod, "create_ticket_channel", None)
        original = getattr(service_mod, "_sv_original_create_ticket_channel", None)

        if wrapper is None or original is None:
            return

        if not getattr(wrapper, "_sv_panel_creation_guard", False):
            return

        _patch_module_attr(
            "stoney_verify.tickets_new.panel",
            "create_ticket_channel",
            wrapper,
            original,
        )
    except Exception:
        pass


def panel_creation_guard_runtime_status() -> Dict[str, Any]:
    return {
        "patched": bool(_PATCHED),
        "error_handlers_patched": bool(_ERROR_PATCHED),
        "original_present": _ORIGINAL_CREATE_TICKET_CHANNEL is not None,
        "patched_module_attrs": sorted([f"{m}.{a}" for m, a in _PATCHED_MODULE_ATTRS]),
    }


install_panel_creation_guard_runtime()
