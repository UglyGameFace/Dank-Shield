from __future__ import annotations

"""
TicketTool parity readiness check.

This is a focused audit for the product goal: make the public Stoney ticket
workflow at least as easy and reliable as TicketTool before expanding into
MEE6/ProBot-style features.

The command is read-only and intentionally practical. It checks the command
surface, setup health, core ticket lifecycle features, transcript/archive
coverage, staff workflow coverage, and dangerous friction points.
"""

from typing import Any, Iterable, Optional

import discord

from .common import safe_defer
from .public_setup_group import (
    _build_setup_health,
    _field_text,
    _require_setup_permission,
    _safe_int,
    _safe_str,
    stoney_group,
)
from ..guild_config import get_guild_config


_ATTACHED = False
_TREE: Any = None

_REQUIRED_TICKET_COMMANDS = {
    "info",
    "claim",
    "unclaim",
    "transfer",
    "priority",
    "close",
    "reopen",
    "transcript",
    "delete",
    "add",
    "remove",
    "lock",
    "unlock",
    "owner",
    "access",
}

_REQUIRED_QUEUE_COMMANDS = {
    "open",
    "unassigned",
    "mine",
    "recent-closed",
    "find",
    "for-user",
    "history",
    "activity",
}

_REQUIRED_INTAKE_COMMANDS = {
    "categories",
    "match",
    "status",
    "preview",
    "post-actions",
}

_REQUIRED_CATEGORY_COMMANDS = {
    "list",
    "create",
    "update",
    "delete",
    "set-default",
}


_PARITY_EXPECTATIONS: tuple[tuple[str, str], ...] = (
    ("Panel/button ticket creation", "Users should not need slash commands to open a ticket."),
    ("Private ticket channels", "Ticket channels must be private to owner + staff."),
    ("Staff claim/unclaim/transfer", "Staff need fast ownership handoff."),
    ("Close/reopen/delete lifecycle", "Closed tickets should archive cleanly; deletes should transcript first."),
    ("Transcript support", "Closed/deleted tickets need searchable proof/history."),
    ("Queue views", "Staff need open/unassigned/mine/recent lookup."),
    ("Category routing", "Support/verifications/general tickets should route without manual guessing."),
    ("Staff action panel", "Staff should have buttons, not command memorization only."),
    ("Setup picker", "Admins should configure with dropdowns instead of channel IDs."),
)


def _group_commands(group: Any) -> set[str]:
    names: set[str] = set()
    try:
        for cmd in list(getattr(group, "commands", []) or []):
            name = _safe_str(getattr(cmd, "name", ""))
            if name:
                names.add(name)
    except Exception:
        pass
    try:
        getter = getattr(group, "get_commands", None)
        if callable(getter):
            for cmd in list(getter() or []):
                name = _safe_str(getattr(cmd, "name", ""))
                if name:
                    names.add(name)
    except Exception:
        pass
    return names


def _missing(required: set[str], found: set[str]) -> list[str]:
    return sorted(required - found)


def _role_can_manage(guild: discord.Guild, role_id: int) -> bool:
    rid = _safe_int(role_id, 0)
    if rid <= 0:
        return False
    role = guild.get_role(rid)
    me = guild.me
    if role is None or me is None:
        return False
    try:
        return bool(me.guild_permissions.manage_roles and me.top_role > role)
    except Exception:
        return False


def _text_channel_writable(guild: discord.Guild, channel_id: int, *, embeds: bool = False, files: bool = False) -> bool:
    cid = _safe_int(channel_id, 0)
    if cid <= 0:
        return False
    channel = guild.get_channel(cid)
    if not isinstance(channel, discord.TextChannel):
        return False
    try:
        me = guild.me
        if me is None:
            return False
        perms = channel.permissions_for(me)
        if not (perms.view_channel and perms.send_messages and perms.read_message_history):
            return False
        if embeds and not perms.embed_links:
            return False
        if files and not perms.attach_files:
            return False
        return True
    except Exception:
        return False


def _category_usable(guild: discord.Guild, category_id: int) -> bool:
    cid = _safe_int(category_id, 0)
    if cid <= 0:
        return False
    category = guild.get_channel(cid)
    if not isinstance(category, discord.CategoryChannel):
        return False
    try:
        me = guild.me
        if me is None:
            return False
        perms = category.permissions_for(me)
        return bool(perms.view_channel and perms.manage_channels)
    except Exception:
        return False


def _configured_channel_label(guild: discord.Guild, channel_id: int) -> str:
    cid = _safe_int(channel_id, 0)
    channel = guild.get_channel(cid) if cid > 0 else None
    if channel is None:
        return f"missing `{cid}`" if cid else "not set"
    mention = getattr(channel, "mention", None)
    return f"{mention or channel.name} (`{cid}`)"


def _configured_role_label(guild: discord.Guild, role_id: int) -> str:
    rid = _safe_int(role_id, 0)
    role = guild.get_role(rid) if rid > 0 else None
    if role is None:
        return f"missing `{rid}`" if rid else "not set"
    return f"{role.mention} (`{rid}`)"


def _command_surface_checks() -> tuple[list[str], list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []

    try:
        from .public_ticket_group import ticket_group
        from . import public_ticket_delete  # noqa: F401 - ensures delete is attached
        ticket_commands = _group_commands(ticket_group)
    except Exception as e:
        blockers.append(f"Could not inspect `/ticket` group: `{repr(e)[:160]}`")
        ticket_commands = set()

    try:
        from .public_tickets_group import tickets_group
        queue_commands = _group_commands(tickets_group)
    except Exception as e:
        blockers.append(f"Could not inspect `/tickets` group: `{repr(e)[:160]}`")
        queue_commands = set()

    try:
        from .public_ticket_intake_group import ticket_intake_group
        intake_commands = _group_commands(ticket_intake_group)
    except Exception as e:
        blockers.append(f"Could not inspect `/ticket-intake` group: `{repr(e)[:160]}`")
        intake_commands = set()

    try:
        from .public_ticket_category_group import ticket_category_group
        category_commands = _group_commands(ticket_category_group)
    except Exception as e:
        blockers.append(f"Could not inspect `/ticket-category` group: `{repr(e)[:160]}`")
        category_commands = set()

    missing_ticket = _missing(_REQUIRED_TICKET_COMMANDS, ticket_commands)
    missing_queue = _missing(_REQUIRED_QUEUE_COMMANDS, queue_commands)
    missing_intake = _missing(_REQUIRED_INTAKE_COMMANDS, intake_commands)
    missing_category = _missing(_REQUIRED_CATEGORY_COMMANDS, category_commands)

    if missing_ticket:
        blockers.append(f"Missing `/ticket` subcommands: {', '.join(f'`{x}`' for x in missing_ticket)}")
    else:
        ok.append(f"`/ticket` has core lifecycle/staff subcommands ({len(ticket_commands)} found).")

    if missing_queue:
        warnings.append(f"Missing `/tickets` queue/history subcommands: {', '.join(f'`{x}`' for x in missing_queue)}")
    else:
        ok.append(f"`/tickets` has queue/history lookup subcommands ({len(queue_commands)} found).")

    if missing_intake:
        warnings.append(f"Missing `/ticket-intake` routing/action subcommands: {', '.join(f'`{x}`' for x in missing_intake)}")
    else:
        ok.append(f"`/ticket-intake` has routing/status/actions subcommands ({len(intake_commands)} found).")

    if missing_category:
        warnings.append(f"Missing `/ticket-category` setup subcommands: {', '.join(f'`{x}`' for x in missing_category)}")
    else:
        ok.append(f"`/ticket-category` has category management subcommands ({len(category_commands)} found).")

    return blockers, warnings, ok


def _service_checks() -> tuple[list[str], list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []

    try:
        from . import ticket_admin as legacy
    except Exception as e:
        return [f"Could not import ticket service helpers: `{repr(e)[:180]}`"], warnings, ok

    required_services: tuple[tuple[str, str], ...] = (
        ("assign ticket", "service_assign_ticket"),
        ("unclaim ticket", "service_unclaim_ticket"),
        ("transfer ticket", "service_transfer_ticket"),
        ("set priority", "service_set_ticket_priority"),
        ("mark closed", "service_mark_ticket_closed"),
        ("reopen channel", "service_reopen_ticket_channel"),
        ("mark deleted", "service_mark_ticket_deleted"),
        ("internal notes", "service_list_internal_notes"),
    )

    for label, attr in required_services:
        if getattr(legacy, attr, None) is None:
            blockers.append(f"Ticket service unavailable: **{label}** (`{attr}`).")
        else:
            ok.append(f"Ticket service available: {label}.")

    try:
        if getattr(legacy, "TicketChannelActionsView", None) is None:
            warnings.append("Ticket staff action panel view is unavailable.")
        else:
            ok.append("Ticket staff action panel view is importable.")
    except Exception:
        warnings.append("Could not verify ticket staff action panel view.")

    return blockers, warnings, ok


def _runtime_ticket_checks(guild: discord.Guild, cfg: Any) -> tuple[list[str], list[str], list[str]]:
    blockers: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []

    if getattr(cfg, "is_unconfigured", False):
        blockers.append("This guild is marked unconfigured. Run `/stoney setup-picker` before TicketTool parity testing.")
        return blockers, warnings, ok

    if _category_usable(guild, getattr(cfg, "ticket_category_id", 0)):
        ok.append(f"Open ticket category is usable: {_configured_channel_label(guild, getattr(cfg, 'ticket_category_id', 0))}.")
    else:
        blockers.append("Open ticket category is missing/unusable. Users cannot reliably create tickets.")

    if _category_usable(guild, getattr(cfg, "ticket_archive_category_id", 0)):
        ok.append(f"Archive category is usable: {_configured_channel_label(guild, getattr(cfg, 'ticket_archive_category_id', 0))}.")
    else:
        warnings.append("Archive category is missing/unusable. Closed-ticket organization will feel worse than TicketTool.")

    if _text_channel_writable(guild, getattr(cfg, "transcripts_channel_id", 0), embeds=True, files=True):
        ok.append(f"Transcript channel supports messages/embeds/files: {_configured_channel_label(guild, getattr(cfg, 'transcripts_channel_id', 0))}.")
    else:
        warnings.append("Transcript channel is missing or lacks Send/Embed/Attach permissions. Close/delete proof may be weak.")

    if _text_channel_writable(guild, getattr(cfg, "modlog_channel_id", 0), embeds=True):
        ok.append(f"Modlog channel is writable: {_configured_channel_label(guild, getattr(cfg, 'modlog_channel_id', 0))}.")
    else:
        warnings.append("Modlog channel is missing or not embed-writable. Ticket actions/mod actions may feel opaque.")

    if _role_can_manage(guild, getattr(cfg, "staff_role_id", 0)):
        ok.append(f"Configured staff role is present and scoped: {_configured_role_label(guild, getattr(cfg, 'staff_role_id', 0))}.")
    else:
        blockers.append("Configured staff role is missing or cannot be safely used for scoped staff access.")

    me = guild.me
    if me is None:
        blockers.append("Bot member object is unavailable; cannot verify ticket permissions.")
    else:
        perms = guild.me.guild_permissions
        required_perm_labels = {
            "manage_channels": "Manage Channels",
            "manage_roles": "Manage Roles",
            "send_messages": "Send Messages",
            "read_message_history": "Read Message History",
            "attach_files": "Attach Files",
            "embed_links": "Embed Links",
        }
        missing = [label for attr, label in required_perm_labels.items() if not bool(getattr(perms, attr, False))]
        if missing:
            blockers.append(f"Bot is missing server-level permissions needed for smooth ticket handling: {', '.join(missing)}.")
        else:
            ok.append("Bot has core ticket/channel/message permissions.")

    return blockers, warnings, ok


def _overall(blockers: list[str], warnings: list[str]) -> tuple[str, discord.Color, str]:
    if blockers:
        return "blocked", discord.Color.red(), "🚫 Not TicketTool-ready yet. Fix blockers first."
    if warnings:
        return "beta-ready", discord.Color.gold(), "⚠️ Core flow works, but polish gaps remain before claiming better-than-TicketTool."
    return "tickettool-ready", discord.Color.green(), "✅ Ticket workflow is ready for controlled TicketTool-parity testing."


def _expectation_lines(blockers: list[str], warnings: list[str]) -> list[str]:
    lines: list[str] = []
    rough_status = "✅" if not blockers and not warnings else "⚠️" if not blockers else "🚫"
    for label, note in _PARITY_EXPECTATIONS:
        lines.append(f"{rough_status} **{label}** — {note}")
    return lines


def _parity_embed(guild: discord.Guild, cfg: Any, blockers: list[str], warnings: list[str], ok: list[str]) -> discord.Embed:
    status, color, desc = _overall(blockers, warnings)
    embed = discord.Embed(
        title="🎫 Stoney TicketTool Parity Check",
        description=(
            f"{desc}\n\n"
            f"Status: `{status}`\n"
            f"Guild: `{guild.id}`\n"
            f"Config source: `{_safe_str(getattr(cfg, 'source', 'unknown'), 'unknown')}`"
        ),
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Blockers", value=_field_text(blockers, empty="✅ None", limit=1000), inline=False)
    embed.add_field(name="Warnings", value=_field_text(warnings, empty="✅ None", limit=1000), inline=False)
    embed.add_field(name="Passing Checks", value=_field_text(ok, empty="No passing checks reported.", limit=1000), inline=False)
    embed.add_field(
        name="TicketTool Parity Expectations",
        value=_field_text(_expectation_lines(blockers, warnings), empty="No checklist generated.", limit=1000),
        inline=False,
    )
    embed.add_field(
        name="Next step",
        value=(
            "Fix blockers, then run `/stoney tickettool-check` again."
            if blockers
            else "Run a live test: create ticket → claim → transfer → close → transcript → delete."
            if warnings
            else "Run the live end-to-end test. If it passes, move to UX polish and dashboard consistency."
        ),
        inline=False,
    )
    embed.set_footer(text="Read-only check. No server config was changed.")
    return embed


async def _tickettool_check_callback(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)

    try:
        cfg = await get_guild_config(guild.id, refresh=True)
        setup_blockers, setup_warnings, setup_ok = _build_setup_health(guild, cfg)
        command_blockers, command_warnings, command_ok = _command_surface_checks()
        service_blockers, service_warnings, service_ok = _service_checks()
        runtime_blockers, runtime_warnings, runtime_ok = _runtime_ticket_checks(guild, cfg)

        blockers = setup_blockers + command_blockers + service_blockers + runtime_blockers
        warnings = setup_warnings + command_warnings + service_warnings + runtime_warnings
        ok = setup_ok + command_ok + service_ok + runtime_ok

        await interaction.followup.send(
            embed=_parity_embed(guild, cfg, blockers, warnings, ok),
            ephemeral=True,
        )
    except Exception as e:
        await interaction.followup.send(f"❌ TicketTool parity check failed: `{repr(e)[:300]}`", ephemeral=True)


def _attach_tickettool_check_command() -> None:
    global _ATTACHED
    if _ATTACHED:
        return

    try:
        existing = stoney_group.get_command("tickettool-check")
    except Exception:
        existing = None
    if existing is not None:
        _ATTACHED = True
        return

    command = discord.app_commands.Command(
        name="tickettool-check",
        description="Audit whether the ticket workflow is ready to compete with TicketTool.",
        callback=_tickettool_check_callback,
    )
    stoney_group.add_command(command)
    _ATTACHED = True


_attach_tickettool_check_command()


def register_public_tickettool_check_commands(bot: Any, tree: Any) -> None:
    global _TREE
    _ = bot
    _TREE = tree
    _attach_tickettool_check_command()
    try:
        print("✅ public_tickettool_check: attached /stoney tickettool-check parity audit command")
    except Exception:
        pass


__all__ = ["register_public_tickettool_check_commands"]
