from __future__ import annotations

"""
Public setup readiness gate.

This keeps the production command surface friendly and safe:

- Setup commands stay available to Administrators / Manage Server users.
- Ticket/staff workflow commands refuse to run until the guild has its own
  usable runtime config.
- Missing setup never falls through into beta env IDs.

The module patches the public grouped command modules through their shared
`_staff_only` helper. The callbacks resolve that helper at runtime, so this is a
small, contained integration point instead of rewriting every command.
"""

from functools import wraps
from typing import Any, Awaitable, Callable, Optional

import discord

from .common import reply_once
from ..guild_config import GuildRuntimeConfig, get_guild_config

_PATCHED = False

StaffOnly = Callable[[discord.Interaction], Awaitable[bool]]


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _channel_exists(guild: discord.Guild, channel_id: int, kind: str = "any") -> bool:
    cid = _safe_int(channel_id, 0)
    if cid <= 0:
        return False
    try:
        channel = guild.get_channel(cid)
    except Exception:
        channel = None
    if channel is None:
        return False
    if kind == "category":
        return isinstance(channel, discord.CategoryChannel)
    if kind == "text":
        return isinstance(channel, discord.TextChannel)
    return True


def _role_exists(guild: discord.Guild, role_id: int) -> bool:
    rid = _safe_int(role_id, 0)
    if rid <= 0:
        return False
    try:
        return guild.get_role(rid) is not None
    except Exception:
        return False


def _setup_missing_lines(guild: discord.Guild, cfg: GuildRuntimeConfig, feature: str) -> list[str]:
    missing: list[str] = []

    # All public staff workflows need a scoped staff role after setup. Admins can
    # still use /dank setup-* before this exists.
    if not _role_exists(guild, cfg.staff_role_id):
        missing.append("staff role")

    if feature in {"tickets", "ticket_categories", "ticket_intake"}:
        if not _channel_exists(guild, cfg.ticket_category_id, "category"):
            missing.append("open ticket category")

    if feature in {"tickets", "ticket_intake"}:
        # Archive/transcripts are not hard requirements, but if they are set they
        # must not point at missing objects. This catches stale copied configs.
        if _safe_int(cfg.ticket_archive_category_id, 0) > 0 and not _channel_exists(guild, cfg.ticket_archive_category_id, "category"):
            missing.append("valid archive category")
        if _safe_int(cfg.transcripts_channel_id, 0) > 0 and not _channel_exists(guild, cfg.transcripts_channel_id, "text"):
            missing.append("valid transcripts channel")

    return missing


async def require_setup_ready(
    interaction: discord.Interaction,
    *,
    feature: str,
    action_label: str,
) -> bool:
    guild = interaction.guild
    if guild is None:
        await reply_once(interaction, {"content": "❌ This command must be used inside a server.", "ephemeral": True})
        return False

    try:
        cfg = await get_guild_config(guild.id)
    except Exception as e:
        await reply_once(
            interaction,
            {
                "content": (
                    "❌ I could not load this server's setup config.\n"
                    f"`{type(e).__name__}: {e}`"
                ),
                "ephemeral": True,
            },
        )
        return False

    if getattr(cfg, "is_unconfigured", False):
        await reply_once(
            interaction,
            {
                "content": (
                    f"🚧 **{action_label} is not available yet.**\n"
                    "This server has not completed Dank Shield setup, so I will not use another server's channels/roles.\n\n"
                    "Start with `/dank setup-picker`, then run `/dank permission-check`."
                ),
                "ephemeral": True,
            },
        )
        return False

    missing = _setup_missing_lines(guild, cfg, feature)
    if missing:
        rendered = ", ".join(missing[:6])
        await reply_once(
            interaction,
            {
                "content": (
                    f"🚧 **{action_label} is not ready yet.**\n"
                    f"Missing or invalid setup: **{rendered}**.\n\n"
                    "Use `/dank setup-picker` or the specific `/dank setup-*` commands, then run `/dank permission-check`."
                ),
                "ephemeral": True,
            },
        )
        return False

    return True


def _wrap_staff_only(original: StaffOnly, *, feature: str, action_label: str) -> StaffOnly:
    @wraps(original)
    async def wrapped(interaction: discord.Interaction) -> bool:
        if not await original(interaction):
            return False
        return await require_setup_ready(interaction, feature=feature, action_label=action_label)

    return wrapped


def _patch_module(module_name: str, *, feature: str, action_label: str) -> bool:
    try:
        module = __import__(f"{__package__}.{module_name}", fromlist=["_staff_only"])
        original: Optional[StaffOnly] = getattr(module, "_staff_only", None)
        if original is None or not callable(original):
            return False
        if getattr(original, "_stoney_setup_gate", False):
            return True
        wrapped = _wrap_staff_only(original, feature=feature, action_label=action_label)
        setattr(wrapped, "_stoney_setup_gate", True)
        setattr(module, "_staff_only", wrapped)
        return True
    except Exception as e:
        try:
            print(f"⚠️ public_setup_gate: failed patching {module_name}: {repr(e)}")
        except Exception:
            pass
        return False


def _patch_all() -> int:
    patched = 0
    targets = (
        ("public_ticket_group", "tickets", "Ticket actions"),
        ("public_tickets_group", "tickets", "Ticket queue/history"),
        ("public_ticket_intake_group", "ticket_intake", "Ticket intake"),
        ("public_ticket_category_group", "ticket_categories", "Ticket category management"),
    )
    for module_name, feature, action_label in targets:
        if _patch_module(module_name, feature=feature, action_label=action_label):
            patched += 1
    return patched


def register_public_setup_gate(bot, tree) -> None:
    _ = bot, tree
    global _PATCHED
    count = _patch_all()
    _PATCHED = count > 0
    try:
        print(f"✅ public_setup_gate: setup readiness gate active patched_modules={count}")
    except Exception:
        pass


__all__ = ["register_public_setup_gate", "require_setup_ready"]
