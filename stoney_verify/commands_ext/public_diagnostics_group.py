from __future__ import annotations

"""Public read-only diagnostics for Dank Shield.

This module intentionally exposes only safe startup and per-guild config health
through the normal /dank command surface. It does not reload guards, mutate
config, touch Discord channels, or inspect another guild's data.
"""

from typing import Any, Optional

import discord

from ..globals import now_utc
from ..guild_context import GuildContext, get_guild_context
from ..interaction_guard import run_guarded_interaction, safe_send_interaction
from ..members_new.activity_scope import ActivityScopeReport, audit_activity_scope, format_activity_scope_problems
from ..startup_diagnostics import build_startup_health_report
from .public_setup_group import dank_group

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


def _yes_no(value: bool) -> str:
    return "Yes" if bool(value) else "No"


def _ready_label(value: bool) -> str:
    return "✅ Ready" if bool(value) else "⚠️ Not ready"


def _missing_line(label: str, keys: tuple[str, ...]) -> str:
    if not keys:
        return f"{label}: ✅ none missing"
    return f"{label}: `{', '.join(keys)}`"


def _guild_context_field(context: GuildContext) -> str:
    return (
        f"Config source: `{context.source}`\n"
        f"Public config isolation: **{_yes_no(context.public_config_isolation)}**\n"
        f"Unsafe to run mutations: **{_yes_no(context.unsafe_to_act)}**\n"
        f"Tickets: {_ready_label(context.ticket_ready)}\n"
        f"Verification: {_ready_label(context.verify_ready)}\n"
        f"Logging: {_ready_label(context.logging_ready)}\n"
        f"{_missing_line('Ticket missing', context.missing_ticket_keys)}\n"
        f"{_missing_line('Verify missing', context.missing_verify_keys)}\n"
        f"{_missing_line('Log missing', context.missing_log_keys)}"
    )


def _activity_coverage_field(report: ActivityScopeReport) -> str:
    if not report.bot_member_resolved:
        return "🚫 Dank Shield could not resolve its bot member, so activity coverage cannot be verified."
    if report.complete:
        return (
            f"✅ **100% channel scope** — {report.accessible_channels}/{report.total_channels} "
            "message channels/active threads are inspectable for retained activity history."
        )

    details = format_activity_scope_problems(report, limit=8)
    return (
        f"⚠️ **{report.coverage_percent}% channel scope** — "
        f"{report.accessible_channels}/{report.total_channels} inspectable.\n"
        "Inactive-member cleanup remains fail-closed while authoritative activity coverage is incomplete.\n"
        + _field_text(details, empty="Permission gap detected but details were unavailable.", limit=850)
    )


def _startup_diagnostics_embed(
    *,
    guild_context: Optional[GuildContext] = None,
    guild_context_error: Optional[BaseException] = None,
    activity_scope: Optional[ActivityScopeReport] = None,
) -> discord.Embed:
    report = build_startup_health_report(load_missing=False)

    color = discord.Color.green()
    if report.status == "blocker":
        color = discord.Color.red()
    elif report.status == "warning":
        color = discord.Color.gold()

    if guild_context is not None and guild_context.unsafe_to_act:
        color = discord.Color.red()
    elif guild_context is not None and not (guild_context.ticket_ready and guild_context.verify_ready and guild_context.logging_ready):
        if color == discord.Color.green():
            color = discord.Color.gold()

    if activity_scope is not None and not activity_scope.complete and color == discord.Color.green():
        color = discord.Color.gold()

    embed = discord.Embed(
        title="🩺 Dank Shield Diagnostics",
        description=(
            "Read-only startup, guild-config, and activity-coverage health report. This does **not** reload guards, "
            "change setup, grant permissions, touch tickets, or mutate server config."
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

    if guild_context is not None:
        embed.add_field(name="Guild Config Safety", value=_guild_context_field(guild_context), inline=False)
    elif guild_context_error is not None:
        embed.add_field(
            name="Guild Config Safety",
            value=f"⚠️ Could not resolve centralized guild context: `{type(guild_context_error).__name__}: {str(guild_context_error)[:250]}`",
            inline=False,
        )
    else:
        embed.add_field(
            name="Guild Config Safety",
            value="⚠️ Guild context was not checked.",
            inline=False,
        )

    if activity_scope is not None:
        embed.add_field(name="Activity Tracking Coverage", value=_activity_coverage_field(activity_scope), inline=False)
    else:
        embed.add_field(name="Activity Tracking Coverage", value="⚠️ Activity channel scope was not checked.", inline=False)

    embed.add_field(name="Startup Blockers", value=_field_text(report.blockers, empty="✅ None"), inline=False)
    embed.add_field(name="Startup Warnings", value=_field_text(report.warnings, empty="✅ None"), inline=False)
    embed.add_field(
        name="What this means",
        value=(
            "**BLOCKER** means at least one startup guard failed and the bot may be unsafe to release.\n"
            "**Unsafe to run mutations** means this guild should refuse setup/ticket/protection actions instead of guessing config.\n"
            "**Incomplete activity coverage** means Dank Shield will not treat inactivity evidence as purge-safe until channel access is restored.\n"
            "**Not ready** means setup is incomplete, but diagnostics stayed read-only and did not change anything."
        ),
        inline=False,
    )
    embed.set_footer(text="Dank Shield • diagnostics are per-process, per-guild, and read-only")
    return embed


@dank_group.command(
    name="diagnostics",
    description="Show read-only Dank Shield startup and server-config diagnostics.",
)
async def diagnostics(interaction: discord.Interaction) -> None:
    if interaction.guild is None:
        await safe_send_interaction(
            interaction,
            content="❌ This command must be used inside a server.",
            ephemeral=True,
        )
        return

    if not _admin_or_manage_guild(interaction):
        await safe_send_interaction(
            interaction,
            content="❌ Diagnostics require **Manage Server** or **Administrator** permission.",
            ephemeral=True,
        )
        return

    async def _run() -> None:
        guild_context: Optional[GuildContext] = None
        guild_context_error: Optional[BaseException] = None

        try:
            guild_context = await get_guild_context(interaction.guild.id, refresh=True)
        except Exception as e:
            guild_context_error = e

        activity_scope = audit_activity_scope(interaction.guild)
        embed = _startup_diagnostics_embed(
            guild_context=guild_context,
            guild_context_error=guild_context_error,
            activity_scope=activity_scope,
        )
        sent = await safe_send_interaction(
            interaction,
            embed=embed,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        if not sent:
            raise RuntimeError("Diagnostics response could not be sent to Discord.")

    await run_guarded_interaction(
        interaction,
        _run,
        defer=True,
        ephemeral=True,
        error_title="❌ Diagnostics failed safely",
        error_guidance="Nothing was changed. Retry `/dank diagnostics`, then check bot logs if it keeps failing.",
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
