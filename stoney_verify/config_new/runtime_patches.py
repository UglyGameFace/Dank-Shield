from __future__ import annotations

"""
Runtime bridge for moving legacy single-server modules onto shared guild config.

This module intentionally uses small monkey patches during the migration period
because several older files are large and still being refactored. The goal is to
make runtime behavior safe immediately while permanent file-by-file cleanup
continues.

Installed patches:
- tickets_new.transcript_service._get_transcripts_channel
- tickets_new.transcript_service.post_transcript_to_channel
- tickets_new.service.create_ticket_channel
- modlog._get_modlog_channel
- modlog._post_modlog

Both config patches use config_new.guild_config and validate that resolved
objects belong to the active guild. Owner/global env IDs remain owner-guild-only
through GuildConfig fallback rules.

Discord send wrappers use runtime_limits so transcript/modlog storms are bounded
per guild and globally. Service gates prevent disabled services from doing live
runtime work.
"""

import inspect
from typing import Any, Dict, Optional, Tuple

import discord

from ..runtime_limits import discord_guild_limit, jitter_sleep
from .guild_config import resolve_configured_text_channel
from .service_gate import (
    ServiceDisabled,
    disabled_service_message,
    is_service_enabled,
    send_disabled_service_interaction,
)

_PATCHED = False
_ORIGINAL_TRANSCRIPT_POST = None
_ORIGINAL_MODLOG_POST = None
_ORIGINAL_TICKET_CREATE = None


def _log(message: str) -> None:
    try:
        print(f"🧭 guild_config_runtime {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ guild_config_runtime {message}")
    except Exception:
        pass


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


def _extract_kwargs_from_signature(func: Any, args: Tuple[Any, ...], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(kwargs or {})
    try:
        sig = inspect.signature(func)
        params = list(sig.parameters.keys())
        for idx, value in enumerate(args):
            if idx < len(params):
                out.setdefault(params[idx], value)
    except Exception:
        pass
    return out


def _is_discord_guild(value: Any) -> bool:
    try:
        return isinstance(value, discord.Guild)
    except Exception:
        return False


def _is_discord_member(value: Any) -> bool:
    try:
        return isinstance(value, discord.Member)
    except Exception:
        return False


def _is_discord_interaction(value: Any) -> bool:
    try:
        return isinstance(value, discord.Interaction)
    except Exception:
        return False


def _extract_ticket_create_context(original_func: Any, args: Tuple[Any, ...], kwargs: Dict[str, Any]):
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
    interaction = mapped.get("interaction") or mapped.get("ctx") or mapped.get("context")

    for value in list(args) + list(kwargs.values()) + list(mapped.values()):
        if not _is_discord_guild(guild) and _is_discord_guild(value):
            guild = value
        if not _is_discord_member(owner) and _is_discord_member(value):
            owner = value
        if not _is_discord_interaction(interaction) and _is_discord_interaction(value):
            interaction = value

    if not _is_discord_guild(guild) and _is_discord_member(owner):
        try:
            guild = owner.guild
        except Exception:
            guild = None

    return (
        guild if _is_discord_guild(guild) else None,
        owner if _is_discord_member(owner) else None,
        interaction if _is_discord_interaction(interaction) else None,
        mapped,
    )


def _call_should_bypass_service_gate(mapped: Dict[str, Any]) -> bool:
    try:
        if _safe_bool(mapped.get("bypass_service_gate"), False):
            return True
        meta = mapped.get("metadata") if isinstance(mapped.get("metadata"), dict) else {}
        if _safe_bool(meta.get("bypass_service_gate"), False):
            return True
        source = str(mapped.get("source") or meta.get("source") or "").strip().lower()
        return source in {"migration", "backfill", "startup_sync", "repair", "admin_force"}
    except Exception:
        return False


async def _resolve_transcripts_channel(
    guild: discord.Guild,
    ticket_row: Optional[dict] = None,
) -> Optional[discord.TextChannel]:
    try:
        ch = await resolve_configured_text_channel(
            guild,
            "transcripts_channel_id",
            "transcript_channel_id",
            fallback_names=(
                "transcripts",
                "ticket-transcripts",
                "ticket_transcripts",
                "support-transcripts",
                "archive-transcripts",
            ),
            fallback_contains=(
                "transcript",
                "tickets-log",
                "ticket-log",
            ),
            label="transcripts_channel",
        )
        return ch
    except Exception as e:
        _warn(f"failed resolving transcripts channel guild={getattr(guild, 'id', 'unknown')}: {repr(e)}")
        return None


def _resolve_modlog_channel_sync(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """
    Synchronous compatibility wrapper for older modlog call sites.

    Full async resolver usage should replace this in the permanent modlog
    refactor. The live post wrapper below enforces moderation_enabled before any
    modlog send happens.
    """
    try:
        exact_names = {
            "mod-log",
            "modlog",
            "mod_log",
            "moderation-log",
            "staff-log",
            "modlogs",
        }
        contains_terms = (
            "mod-log",
            "modlog",
            "moderation",
            "staff-log",
        )

        for ch in getattr(guild, "text_channels", []) or []:
            name = str(getattr(ch, "name", "") or "").strip().lower()
            if name in exact_names:
                return ch

        for ch in getattr(guild, "text_channels", []) or []:
            name = str(getattr(ch, "name", "") or "").strip().lower()
            if any(term in name for term in contains_terms):
                return ch
    except Exception:
        pass

    _warn(f"modlog channel not resolved synchronously guild={getattr(guild, 'id', 'unknown')}")
    return None


async def _patched_post_transcript_to_channel(*args, **kwargs):
    if _ORIGINAL_TRANSCRIPT_POST is None:
        return None, None

    ticket_channel = kwargs.get("ticket_channel")
    if ticket_channel is None and args:
        ticket_channel = args[0]

    guild_id = getattr(getattr(ticket_channel, "guild", None), "id", "0")

    if not await is_service_enabled(guild_id, "tickets"):
        _log(f"skipping transcript post; tickets disabled guild={guild_id}")
        return None, None

    await jitter_sleep(base_seconds=0.0, max_jitter_seconds=0.25, guild_id=guild_id)

    async with discord_guild_limit(guild_id, label="transcript_post"):
        return await _ORIGINAL_TRANSCRIPT_POST(*args, **kwargs)


async def _patched_post_modlog(guild: discord.Guild, embed: discord.Embed, view: Optional[discord.ui.View] = None):
    if _ORIGINAL_MODLOG_POST is None:
        return None

    guild_id = getattr(guild, "id", "0")

    if not await is_service_enabled(guild_id, "moderation"):
        _log(f"skipping modlog post; moderation disabled guild={guild_id}")
        return None

    await jitter_sleep(base_seconds=0.0, max_jitter_seconds=0.15, guild_id=guild_id)

    async with discord_guild_limit(guild_id, label="modlog_send"):
        return await _ORIGINAL_MODLOG_POST(guild, embed, view=view)


async def _patched_create_ticket_channel(*args, **kwargs):
    if _ORIGINAL_TICKET_CREATE is None:
        from ..tickets_new import service as service_mod
        original = getattr(service_mod, "_sv_original_create_ticket_channel", None) or getattr(service_mod, "create_ticket_channel", None)
    else:
        original = _ORIGINAL_TICKET_CREATE

    if original is None:
        raise RuntimeError("Ticket create original function unavailable")

    guild, _owner, interaction, mapped = _extract_ticket_create_context(original, args, kwargs)
    if guild is None or _call_should_bypass_service_gate(mapped):
        return await original(*args, **kwargs)

    if not await is_service_enabled(guild.id, "tickets"):
        responded = await send_disabled_service_interaction(interaction, "tickets")
        _log(f"blocked ticket create; tickets disabled guild={guild.id} responded={responded}")
        raise ServiceDisabled("tickets", responded=responded)

    return await original(*args, **kwargs)


def _patch_ticket_creation_service() -> None:
    global _ORIGINAL_TICKET_CREATE
    try:
        from ..tickets_new import service as service_mod

        current = getattr(service_mod, "create_ticket_channel", None)
        if not callable(current):
            _warn("ticket service create_ticket_channel not found")
            return

        if getattr(current, "_sv_service_gate", False):
            return

        _ORIGINAL_TICKET_CREATE = current

        async def _wrapper(*args, **kwargs):
            return await _patched_create_ticket_channel(*args, **kwargs)

        _wrapper.__name__ = "create_ticket_channel"
        _wrapper.__qualname__ = "create_ticket_channel"
        _wrapper.__doc__ = getattr(current, "__doc__", None)
        setattr(_wrapper, "_sv_service_gate", True)

        setattr(service_mod, "create_ticket_channel", _wrapper)

        # If panel.py imported create_ticket_channel directly, patch that alias too.
        try:
            from ..tickets_new import panel as panel_mod
            if getattr(panel_mod, "create_ticket_channel", None) is current:
                setattr(panel_mod, "create_ticket_channel", _wrapper)
        except Exception:
            pass

        _log("patched ticket create service gate")
    except Exception as e:
        _warn(f"failed patching ticket service gate: {repr(e)}")


def install_runtime_config_patches() -> None:
    global _PATCHED, _ORIGINAL_TRANSCRIPT_POST, _ORIGINAL_MODLOG_POST
    if _PATCHED:
        return
    _PATCHED = True

    try:
        from ..tickets_new import transcript_service

        async def _patched_get_transcripts_channel(
            guild: discord.Guild,
            ticket_row: Optional[dict] = None,
        ) -> Optional[discord.TextChannel]:
            return await _resolve_transcripts_channel(guild, ticket_row)

        transcript_service._get_transcripts_channel = _patched_get_transcripts_channel  # type: ignore[attr-defined]

        original_post = getattr(transcript_service, "post_transcript_to_channel", None)
        if callable(original_post):
            _ORIGINAL_TRANSCRIPT_POST = original_post
            transcript_service.post_transcript_to_channel = _patched_post_transcript_to_channel  # type: ignore[attr-defined]
            _log("patched transcript destination + tickets service gate + throttled post_transcript_to_channel")
        else:
            _log("patched transcript destination only; post function not found")
    except Exception as e:
        _warn(f"failed patching transcript service: {repr(e)}")

    try:
        from .. import modlog

        modlog._get_modlog_channel = _resolve_modlog_channel_sync  # type: ignore[attr-defined]

        original_modlog_post = getattr(modlog, "_post_modlog", None)
        if callable(original_modlog_post):
            _ORIGINAL_MODLOG_POST = original_modlog_post
            modlog._post_modlog = _patched_post_modlog  # type: ignore[attr-defined]
            _log("patched modlog resolver + moderation service gate + throttled _post_modlog")
        else:
            _log("patched modlog resolver only; _post_modlog not found")
    except Exception as e:
        _warn(f"failed patching modlog resolver: {repr(e)}")

    _patch_ticket_creation_service()


install_runtime_config_patches()


__all__ = ["install_runtime_config_patches"]
