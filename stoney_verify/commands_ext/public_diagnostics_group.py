from __future__ import annotations

"""Public read-only diagnostics for Dank Shield.

This module intentionally exposes only safe startup health details through the
normal /dank command surface. It does not reload guards, mutate config, touch
Discord channels, or inspect another guild's data.
"""

from typing import Any

import discord

from ..globals import now_utc
from ..startup_diagnostics import build_startup_health_report
from .common import safe_defer
from .public_setup_group import stoney_group

_REGISTERED = False


def _admin_or_manage_guild(interaction: discord.Interaction) -> bool:
    try:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        perms = interaction.user.guild_permissions
        return bool(perms.administrator or perms.manage_guild)
    except Exception:
        return False


def _field_text(items: list[str], *, empty: str, limit: int = 1000) -> str:
    if not items:
        return empty

    out: list[str] = []
    total = 0

    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        extra = len(text) + 3
        if total + extra > limit:
            remaining = len(items) - len(out)
            out.append(f"…and {remaining} more")
            break
        out.append(f"• {text}")
        total += extra

    return "\n".join(out) or empty


def _startup_diagnostics_embed() -> discord.Embed:
    report = build_startup_health_report(load_missing=False)

    color = discord.Color.green()
    if report.status == "blocker":
        color = discord.Color.red()
    elif report.status == "warning":
        color = discord.Color.gold()

    embed = discord.Embed(
        title="🩺 Dank Shield Diagnostics",
        description=(
            "Read-only startup health report. This does **not** reload guards, "
            "change setup, touch tickets, or mutate server config."
        ),
        color=color,
        timestamp=now_utc(),
    )

    embed.add_field(
        name="Startup Status",
        value=(
            f"Status: **{report.status.upper()}**\n"
            f"Expected guards: `{report.expected_count}`\n"
            f"Loaded: `{report.loaded_count}`\n"
            f"Failed: `{report.failed_count}`\n"
            f"Missing/not loaded yet: `{report.missing_count}`"
        ),
        inline=False,
    )
    embed.add_field(name="Blockers", value=_field_text(report.blockers, empty="✅ None"), inline=False)
    embed.add_field(name="Warnings", value=_field_text(report.warnings, empty="✅ None"), inline=False)
    embed.add_field(
        name="What this means",
        value=(
            "**BLOCKER** means at least one startup guard failed and the bot may be unsafe to release.\n"
            "**WARNING** means expected guards are not loaded in this snapshot or optional checks need review.\n"
            "**OK** means the current startup guard snapshot has no failed or missing expected guards."
        ),
        inline=False,
    )
    embed.set_footer(text="Dank Shield • diagnostics are per-process and safe to run anytime")
    return embed


@stoney_group.command(
    name="diagnostics",
    description="Show read-only Dank Shield startup diagnostics for this bot process.",
)
async def diagnostics(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await interaction.response.send_message("❌ This command must be used inside a server.", ephemeral=True)
        return

    if not _admin_or_manage_guild(interaction):
        await interaction.response.send_message(
            "❌ Diagnostics require **Manage Server** or **Administrator** permission.",
            ephemeral=True,
        )
        return

    await safe_defer(interaction, ephemeral=True)

    try:
        embed = _startup_diagnostics_embed()
    except Exception as e:
        embed = discord.Embed(
            title="❌ Diagnostics Failed",
            description=f"`{type(e).__name__}: {str(e)[:350]}`",
            color=discord.Color.red(),
            timestamp=now_utc(),
        )

    await interaction.followup.send(
        embed=embed,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


def register_public_diagnostics_group_commands(bot: Any, tree: Any) -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    _REGISTERED = True
    try:
        print("✅ public_diagnostics_group active; /dank diagnostics attached")
    except Exception:
        pass
