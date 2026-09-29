from __future__ import annotations

"""Capability-aware Roles & Profiles center and Discord role editor.

The product boundary is intentional:
- normal members only receive profile/cosmetic controls;
- recognized staff may use existing staff/member-role tools;
- only owner/Admin/Manage Roles actors may mutate server role definitions;
- every mutation re-checks live actor permission, bot permission, hierarchy,
  managed/default-role status, and known Dank Shield config dependencies.
"""

import asyncio
import re
import weakref
from collections.abc import Mapping, Sequence
from typing import Any, Optional

import discord

from stoney_verify.panel_lifecycle import PRIVATE_MENU_TTL_SECONDS
from stoney_verify.ui.picker import DankRoleSelect

_ROLE_EDITOR_PREFIX = "dank:roles:v1:"
_ROLE_ACTION_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")

_PERMISSION_GROUP_BASE: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "general",
        "General & Moderation",
        (
            "administrator",
            "view_audit_log",
            "view_guild_insights",
            "manage_guild",
            "manage_roles",
            "manage_channels",
            "kick_members",
            "ban_members",
            "moderate_members",
            "manage_nicknames",
            "change_nickname",
            "manage_webhooks",
        ),
    ),
    (
        "text",
        "Text & Threads",
        (
            "view_channel",
            "send_messages",
            "send_tts_messages",
            "manage_messages",
            "embed_links",
            "attach_files",
            "read_message_history",
            "mention_everyone",
            "use_external_emojis",
            "add_reactions",
            "create_public_threads",
            "create_private_threads",
            "send_messages_in_threads",
            "manage_threads",
            "use_external_stickers",
            "use_application_commands",
            "send_voice_messages",
            "send_polls",
        ),
    ),
    (
        "voice",
        "Voice",
        (
            "connect",
            "speak",
            "stream",
            "priority_speaker",
            "mute_members",
            "deafen_members",
            "move_members",
            "use_voice_activation",
            "request_to_speak",
            "use_embedded_activities",
            "use_soundboard",
            "use_external_sounds",
        ),
    ),
    (
        "events",
        "Events & Expressions",
        (
            "create_instant_invite",
            "manage_events",
            "create_events",
            "manage_emojis",
            "manage_emojis_and_stickers",
            "manage_expressions",
            "create_expressions",
        ),
    ),
)


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _actor_id(actor: Any) -> int:
    try:
        return int(getattr(actor, "id", 0) or 0)
    except Exception:
        return 0


def _is_guild_owner(guild: discord.Guild, actor: Any) -> bool:
    actor_id = _actor_id(actor)
    if actor_id <= 0:
        return False
    try:
        if int(getattr(guild, "owner_id", 0) or 0) == actor_id:
            return True
    except Exception:
        pass
    try:
        return int(getattr(getattr(guild, "owner", None), "id", 0) or 0) == actor_id
    except Exception:
        return False


def _role_reason(action: str, actor: Any) -> str:
    return _clip(
        f"Dank Shield Role Editor: {action} by {actor} ({_actor_id(actor)})",
        480,
    )


def _role_action_lock(guild_id: int, role_id: int, action: str) -> asyncio.Lock:
    key = f"{int(guild_id)}:{int(role_id)}:{str(action)}"
    lock = _ROLE_ACTION_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _ROLE_ACTION_LOCKS[key] = lock
    return lock


def _bool_text(value: bool) -> str:
    return "On" if bool(value) else "Off"


def _permission_label(name: str) -> str:
    special = {
        "tts": "TTS",
        "guild": "Server",
        "webhooks": "Webhooks",
    }
    words = []
    for raw in str(name or "").split("_"):
        words.append(special.get(raw, raw.capitalize()))
    return " ".join(words)[:100] or "Permission"


def _parse_bool(value: str, *, field: str) -> bool:
    clean = str(value or "").strip().casefold()
    if clean in {"yes", "y", "on", "true", "1"}:
        return True
    if clean in {"no", "n", "off", "false", "0"}:
        return False
    raise ValueError(f"{field} must be yes or no.")


def _parse_colour(value: str, *, current: discord.Colour) -> discord.Colour:
    clean = str(value or "").strip()
    if not clean:
        return current
    if clean.casefold() in {"default", "none", "clear"}:
        return discord.Colour.default()
    match = _HEX_RE.fullmatch(clean)
    if not match:
        raise ValueError("Colour must be a 6-digit hex value such as #5865F2, or `default`.")
    return discord.Colour(int(match.group(1), 16))


def _actor_can_manage_roles(guild: discord.Guild, actor: Any) -> bool:
    if _is_guild_owner(guild, actor):
        return True
    if not isinstance(actor, discord.Member):
        return False
    perms = actor.guild_permissions
    return bool(perms.administrator or perms.manage_roles)


def _bot_can_manage_roles(guild: discord.Guild) -> bool:
    me = guild.me
    if not isinstance(me, discord.Member):
        return False
    perms = me.guild_permissions
    return bool(perms.administrator or perms.manage_roles)


async def _reply(
    interaction: discord.Interaction,
    content: str,
    *,
    ephemeral: bool = True,
) -> None:
    kwargs = {
        "ephemeral": ephemeral,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if not interaction.response.is_done():
        await interac