from __future__ import annotations

from typing import Any, List

import discord

from .common import safe_defer
from .public_setup_group import (
    _build_setup_health,
    _config_embed,
    _field_text,
    _require_setup_permission,
    _safe_str,
    stoney_group,
)
from ..guild_config import get_guild_config


# ============================================================
# public_setup_review.py
# ------------------------------------------------------------
# Adds a read-only /stoney setup-review command to the public
# setup group.
#
# This module intentionally imports and extends the existing
# /stoney group instead of creating another top-level command.
# It adds production-friendly review output without increasing
# Discord's global top-level slash command surface.
# ============================================================


_REVIEW_COMMAND_ATTACHED = False


def _status_line(blockers: List[str], warnings: List[str]) -> str:
    if blockers:
        return "🚫 **Not ready** — fix blockers before beta/public use."
    if warnings:
        return "⚠️ **Usable with warnings** — safe to test, but review the warnings."
    return "✅ **Ready** — no setup blockers or warnings found."


def _next_steps(blockers: List[str], warnings: List[str]) -> list[str]:
    steps: list[str] = []

    joined = "\n".join(blockers + warnings).lower()

    if "ticket" in joined or "category" in joined or "staff" in joined or "transcript" in joined:
        steps.append("Run `/stoney setup-tickets` to fix ticket categories, staff role, or transcript channel.")
    if "verify" in joined or "role" in joined or "vc" in joined:
        steps.append("Run `/stoney setup-verify` to fix verification channels or role hierarchy.")
    if "modlog" in joined or "join/exit" in joined or "raid" in joined or "log" in joined:
        steps.append("Run `/stoney setup-logs` to fix modlog, raid/security log, or join/exit log channels.")
    if "env/default" in joined or "fallback" in joined:
        steps.append("Run `/stoney refresh-config`, then verify the config source says `supabase:guild_configs`.")

    if not steps and blockers:
        steps.append("Fix the blockers listed above, then run `/stoney setup-review` again.")
    if not steps and warnings:
        steps.append("Warnings are allowed, but review them before inviting the bot to public/beta servers.")
    if not steps:
        steps.append("Post or refresh your ticket panel, then test ticket create/close/reopen as a staff member.")

    # Preserve order while de-duping.
    out: list[str] = []
    seen: set[str] = set()
    for step in steps:
        if step not in seen:
            seen.add(step)
            out.append(step)
    return out


def _setup_review_embed(guild: discord.Guild, cfg: Any) -> discord.Embed:
    blockers, warnings, ok = _build_setup_health(guild, cfg)
    source = _safe_str(getattr(cfg, "source", "unknown"), "unknown")

    embed = discord.Embed(
        title="🧭 Stoney Setup Review",
        description=(
            f"{_status_line(blockers, warnings)}\n"
            f"Config source: `{source}`\n"
            f"Guild: `{guild.id}`"
        ),
        color=discord.Color.red() if blockers else discord.Color.gold() if warnings else discord.Color.green(),
    )

    embed.add_field(name="Blockers", value=_field_text(blockers, empty="✅ None"), inline=False)
    embed.add_field(name="Warnings", value=_field_text(warnings, empty="✅ None"), inline=False)
    embed.add_field(name="Passing Checks", value=_field_text(ok, empty="No passing checks reported."), inline=False)
    embed.add_field(name="Next Steps", value=_field_text(_next_steps(blockers, warnings), empty="✅ No setup actions needed."), inline=False)
    embed.set_footer(text="Read-only review. This command does not change server config.")
    return embed


async def _setup_review_callback(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)

    try:
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        return await interaction.followup.send(f"❌ Failed loading config for setup review: `{e}`", ephemeral=True)

    await interaction.followup.send(
        embeds=[
            _setup_review_embed(guild, cfg),
            _config_embed(guild, cfg, title="📌 Current Saved Config"),
        ],
        ephemeral=True,
    )


def _attach_setup_review_command() -> None:
    global _REVIEW_COMMAND_ATTACHED
    if _REVIEW_COMMAND_ATTACHED:
        return

    try:
        existing = stoney_group.get_command("setup-review")
    except Exception:
        existing = None

    if existing is not None:
        _REVIEW_COMMAND_ATTACHED = True
        return

    command = discord.app_commands.Command(
        name="setup-review",
        description="Review this server's Stoney setup without changing anything.",
        callback=_setup_review_callback,
    )
    stoney_group.add_command(command)
    _REVIEW_COMMAND_ATTACHED = True


_attach_setup_review_command()


def register_public_setup_review_commands(bot, tree) -> None:
    _ = bot
    _ = tree
    _attach_setup_review_command()
    try:
        print("✅ public_setup_review: attached /stoney setup-review command")
    except Exception:
        pass


__all__ = ["register_public_setup_review_commands"]
