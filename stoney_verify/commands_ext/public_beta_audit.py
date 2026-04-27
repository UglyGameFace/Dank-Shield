from __future__ import annotations

from typing import Any, List, Sequence

import discord

from .common import safe_defer
from .public_setup_group import (
    _build_setup_health,
    _field_text,
    _require_setup_permission,
    _safe_str,
    stoney_group,
)
from ..guild_config import get_guild_config


# ============================================================
# public_beta_audit.py
# ------------------------------------------------------------
# Adds read-only /stoney audit-list and /stoney beta-checklist
# commands to the public /stoney setup group.
#
# These commands are intentionally diagnostic/checklist only.
# They do not write guild config, mutate roles, create channels,
# or touch verification state. The goal is to keep public beta
# rollout professional: admins get a concrete gate-by-gate test
# plan instead of guessing whether a green setup screen means the
# whole bot is production ready.
# ============================================================


_AUDIT_LIST_ATTACHED = False
_BETA_CHECKLIST_ATTACHED = False


def _bullet_lines(lines: Sequence[str]) -> str:
    return _field_text([f"• {line}" for line in lines], empty="✅ None")


def _numbered_lines(lines: Sequence[str]) -> str:
    out: list[str] = []
    for idx, line in enumerate(lines, start=1):
        out.append(f"`{idx}.` {line}")
    return _field_text(out, empty="✅ None")


def _status_from_setup(blockers: List[str], warnings: List[str]) -> tuple[str, discord.Color]:
    if blockers:
        return "🚫 **Setup is blocked. Fix `/stoney health` before flow testing.**", discord.Color.red()
    if warnings:
        return "⚠️ **Setup has warnings. You can test, but review warnings first.**", discord.Color.gold()
    return "✅ **Setup health is clean. Begin private beta flow testing.**", discord.Color.green()


def _public_readiness_line(blockers: List[str], warnings: List[str]) -> str:
    if blockers:
        return "Not beta-ready yet because setup has blockers."
    if warnings:
        return "Private beta only after reviewing warnings. Not public-production ready."
    return "Private beta ready. Public production still needs full flow tests, privacy docs, and multi-server testing."


def _audit_summary_embed(guild: discord.Guild, cfg: Any) -> discord.Embed:
    blockers, warnings, ok = _build_setup_health(guild, cfg)
    status, color = _status_from_setup(blockers, warnings)
    source = _safe_str(getattr(cfg, "source", "unknown"), "unknown")

    embed = discord.Embed(
        title="📋 Stoney Public/Beta Audit List",
        description=(
            f"{status}\n"
            f"Config source: `{source}`\n"
            f"Guild: `{guild.id}`\n"
            f"Readiness: **{_public_readiness_line(blockers, warnings)}**"
        ),
        color=color,
    )
    embed.add_field(name="Setup Blockers", value=_field_text(blockers, empty="✅ None"), inline=False)
    embed.add_field(name="Setup Warnings", value=_field_text(warnings, empty="✅ None"), inline=False)
    embed.add_field(
        name="What This Check Does Prove",
        value=_bullet_lines(
            [
                "Per-server `guild_configs` can be read by the bot.",
                "Configured ticket categories/channels/roles are present enough for beta testing.",
                "Role hierarchy and key bot permissions are valid enough to test real flows.",
            ]
        ),
        inline=False,
    )
    embed.add_field(
        name="What This Check Does Not Prove Yet",
        value=_bullet_lines(
            [
                "Approval/deny verification flow works end-to-end with a real test user.",
                "Ticket close/archive/transcript paths work under staff pressure.",
                "The bot is ready for unknown public servers at scale.",
                "Privacy/legal/data-retention paperwork is complete.",
            ]
        ),
        inline=False,
    )
    embed.set_footer(text="Read-only audit helper. No config or member data is changed.")
    return embed


def _verification_flow_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🧪 Required Verification Flow Tests",
        description="Run these with a test account before calling the bot public-ready.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Approve Path",
        value=_numbered_lines(
            [
                "Test account joins the server and receives Unverified.",
                "Test account can see the configured verify/support path and create a verification ticket.",
                "Ticket opens in the configured open ticket category.",
                "Only the ticket owner and staff can view the ticket.",
                "Test account submits a clearly fake/watermarked test image, not a real government ID.",
                "Staff approves the ticket.",
                "Verified role is added, Unverified is removed, and Resident is added only if intended.",
                "Ticket closes or archives into the archive category.",
                "Transcript posts to the transcript channel.",
                "Modlog records the approval with clear actor/target context.",
            ]
        ),
        inline=False,
    )
    embed.add_field(
        name="Deny Path",
        value=_numbered_lines(
            [
                "Create a second test verification ticket.",
                "Staff denies/rejects the verification.",
                "User remains Unverified and does not receive Verified/Resident.",
                "Deny reason is visible enough for staff/user follow-up.",
                "Ticket can be closed/archived cleanly.",
                "Transcript and modlog are produced without duplicate spam.",
            ]
        ),
        inline=False,
    )
    embed.set_footer(text="Do not use real ID images during beta QA. Use fake/watermarked test media only.")
    return embed


def _ticket_ops_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎫 Required Ticket System Tests",
        description="These are the TicketTool-parity smoke tests before wider beta.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Core Ticket Controls",
        value=_numbered_lines(
            [
                "Create a normal support ticket from the public panel.",
                "Claim/unclaim works and shows the correct staff member.",
                "Add/remove user access works without exposing other tickets.",
                "Rename works and preserves ticket metadata.",
                "Lock/unlock prevents and restores non-staff messaging.",
                "Transfer/owner change keeps permissions correct.",
                "Close, reopen, and delete respect staff permissions.",
                "Transcript generation works after close and before delete.",
            ]
        ),
        inline=False,
    )
    embed.add_field(
        name="Failure/Abuse Tests",
        value=_numbered_lines(
            [
                "A normal member cannot run staff ticket commands.",
                "A normal member cannot see another member's ticket.",
                "Cooldown/limit behavior prevents ticket spam if enabled.",
                "Bot missing permissions produces a clear error instead of silently failing.",
                "Restarting the bot keeps persistent buttons and ticket state usable.",
            ]
        ),
        inline=False,
    )
    return embed


def _production_gaps_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🚧 Still Missing Before Public Production",
        description="Private beta can start after flow tests pass. These remain before broad public rollout.",
        color=discord.Color.gold(),
    )
    embed.add_field(
        name="Product / UX",
        value=_bullet_lines(
            [
                "Guided `/stoney setup-wizard` with buttons/selects/search fallback in one flow.",
                "Public grouped controls for macros, SLA, auto-close, ticket forms, panels, and guardrails.",
                "Cleaner onboarding copy for server owners and staff.",
                "Second-server fresh install test from zero config.",
            ]
        ),
        inline=False,
    )
    embed.add_field(
        name="Privacy / Legal / Trust",
        value=_bullet_lines(
            [
                "Privacy Policy and Terms of Service published before public invites.",
                "Data retention rules for verification submissions and transcripts.",
                "User/admin data deletion process.",
                "Staff access policy for sensitive verification material.",
            ]
        ),
        inline=False,
    )
    embed.add_field(
        name="Engineering / Scale",
        value=_bullet_lines(
            [
                "Replace remaining event-loop sync database calls with async/job-backed flows.",
                "Run heartbeat/latency soak tests during joins, voice events, and ticket bursts.",
                "Remove production dependence on one env `GUILD_ID` for multi-server logic.",
                "Enable AutoShardedBot before serious 100+ server scaling.",
            ]
        ),
        inline=False,
    )
    return embed


async def _send_audit(interaction: discord.Interaction, *, include_ticket_ops: bool) -> None:
    if not await _require_setup_permission(interaction):
        return
    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)

    try:
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        return await interaction.followup.send(f"❌ Failed loading guild config for audit: `{e}`", ephemeral=True)

    embeds: list[discord.Embed] = [
        _audit_summary_embed(guild, cfg),
        _verification_flow_embed(),
    ]
    if include_ticket_ops:
        embeds.append(_ticket_ops_embed())
    embeds.append(_production_gaps_embed())

    await interaction.followup.send(embeds=embeds[:10], ephemeral=True)


async def _audit_list_callback(interaction: discord.Interaction) -> None:
    await _send_audit(interaction, include_ticket_ops=True)


async def _beta_checklist_callback(interaction: discord.Interaction) -> None:
    await _send_audit(interaction, include_ticket_ops=False)


def _attach_audit_list_command() -> None:
    global _AUDIT_LIST_ATTACHED
    if _AUDIT_LIST_ATTACHED:
        return

    try:
        existing = stoney_group.get_command("audit-list")
    except Exception:
        existing = None

    if existing is not None:
        _AUDIT_LIST_ATTACHED = True
        return

    command = discord.app_commands.Command(
        name="audit-list",
        description="Show the remaining private-beta and public-production audit checklist.",
        callback=_audit_list_callback,
    )
    stoney_group.add_command(command)
    _AUDIT_LIST_ATTACHED = True


def _attach_beta_checklist_command() -> None:
    global _BETA_CHECKLIST_ATTACHED
    if _BETA_CHECKLIST_ATTACHED:
        return

    try:
        existing = stoney_group.get_command("beta-checklist")
    except Exception:
        existing = None

    if existing is not None:
        _BETA_CHECKLIST_ATTACHED = True
        return

    command = discord.app_commands.Command(
        name="beta-checklist",
        description="Show the verification-flow checklist required before private beta.",
        callback=_beta_checklist_callback,
    )
    stoney_group.add_command(command)
    _BETA_CHECKLIST_ATTACHED = True


_attach_audit_list_command()
_attach_beta_checklist_command()


def register_public_beta_audit_commands(bot, tree) -> None:
    _ = bot
    _ = tree
    _attach_audit_list_command()
    _attach_beta_checklist_command()
    try:
        print("✅ public_beta_audit: attached /stoney audit-list and /stoney beta-checklist commands")
    except Exception:
        pass


__all__ = ["register_public_beta_audit_commands"]
