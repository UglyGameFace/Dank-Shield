from __future__ import annotations

"""
Runtime bridge for moving legacy single-server modules onto shared guild config.

This module intentionally uses small monkey patches during the migration period
because several older files are large and still being refactored. The goal is to
make runtime behavior safe immediately while permanent file-by-file cleanup
continues.

Installed patches:
- tickets_new.transcript_service._get_transcripts_channel
- modlog._get_modlog_channel

Both patches use config_new.guild_config and validate that resolved objects
belong to the active guild. Owner/global env IDs remain owner-guild-only through
GuildConfig fallback rules.
"""

from typing import Optional

import discord

from .guild_config import resolve_configured_text_channel

_PATCHED = False


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


async def _resolve_transcripts_channel(
    guild: discord.Guild,
    ticket_row: Optional[dict] = None,
) -> Optional[discord.TextChannel]:
    try:
        # Row-level transcript_channel_id is intentionally not handled here as a
        # config key because it represents an already-posted transcript
        # destination, not the active configured destination for new transcripts.
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

    modlog._get_modlog_channel is currently sync in several paths. We cannot
    await DB config there without a larger refactor, so this sync patch keeps
    owner/global leaks blocked by checking guild-local cache/name fallbacks.

    Full async resolver usage should replace this in the permanent modlog
    refactor.
    """
    try:
        # First, exact/fuzzy same-guild name fallback. This catches the common
        # public-server case safely without touching global IDs.
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


def install_runtime_config_patches() -> None:
    global _PATCHED
    if _PATCHED:
        return
    _PATCHED = True

    # Patch ticket transcript destination to shared async resolver.
    try:
        from ..tickets_new import transcript_service

        async def _patched_get_transcripts_channel(
            guild: discord.Guild,
            ticket_row: Optional[dict] = None,
        ) -> Optional[discord.TextChannel]:
            return await _resolve_transcripts_channel(guild, ticket_row)

        transcript_service._get_transcripts_channel = _patched_get_transcripts_channel  # type: ignore[attr-defined]
        _log("patched tickets_new.transcript_service._get_transcripts_channel")
    except Exception as e:
        _warn(f"failed patching transcript service: {repr(e)}")

    # Patch sync modlog resolver for immediate cross-guild safety.
    try:
        from .. import modlog

        modlog._get_modlog_channel = _resolve_modlog_channel_sync  # type: ignore[attr-defined]
        _log("patched modlog._get_modlog_channel sync safety resolver")
    except Exception as e:
        _warn(f"failed patching modlog resolver: {repr(e)}")


install_runtime_config_patches()


__all__ = ["install_runtime_config_patches"]
