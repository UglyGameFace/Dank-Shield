from __future__ import annotations

"""Public /stoney help and /stoney commands catalog.

TicketTool-style command discovery without adding a new top-level /help command.
This keeps the public slash surface boring and small while still making the
feature set easy to understand.
"""

from typing import Any, Iterable, Optional

import discord
from discord import app_commands

from ..globals import now_utc
from .common import reply_once
from .public_setup_group import stoney_group


_REGISTERED = False

HELP_SECTION_CHOICES = [
    app_commands.Choice(name="Overview", value="overview"),
    app_commands.Choice(name="Setup", value="setup"),
    app_commands.Choice(name="Tickets", value="tickets"),
    app_commands.Choice(name="Panels", value="panels"),
    app_commands.Choice(name="Verification", value="verification"),
    app_commands.Choice(name="Moderation", value="moderation"),
    app_commands.Choice(name="Utilities", value="utilities"),
]

STALE_TOP_LEVEL_MOVES = {
    "spam_guard": "/stoney spam panel",
    "spam_guard_status": "/stoney spam status",
    "fix_unverified": "/verify repair-unverified",
    "set_verified": "/verify set-verified",
    "set_resident": "/verify set-resident",
    "grant_vr": "/verify grant-vr",
    "verify_diagnose": "/verify diagnose",
    "fix_unverified_member": "/verify fix-member",
    "verify_status": "/verify status",
    "channel_cleanup_status": "/stoney cleanup status",
    "run_channel_cleanup": "/stoney cleanup run",
    "purge_channel_messages": "/stoney cleanup purge",
    "ticket_setup_status": "/stoney setup-review",
    "ticket_setup_discover": "/stoney setup-find",
    "ticket_setup_save_discovered": "/stoney setup-picker",
    "ticket_setup_set_channel": "/stoney setup-tickets or /stoney setup-verify",
    "ticket_setup_set_role": "/stoney setup-tickets or /stoney setup-verify",
    "ticket_panel_list": "/ticket-panel list",
    "ticket_panel_show": "/ticket-panel show",
    "ticket_panel_bind_categories": "/ticket-panel bind-categories",
    "ticket_panel_rules": "/ticket-panel rules view",
    "ticket_panel_rules_set": "/ticket-panel rules set",
    "ticket_panel_runtime": "/ticket-panel runtime",
    "ticket_panel_bootstrap_status": "/ticket-panel bootstrap status",
    "ticket_panel_bootstrap_run": "/ticket-panel bootstrap run",
    "ticket_panel_bootstrap_all": "/ticket-panel bootstrap all",
    "ticket_panel_bootstrap_start": "/ticket-panel bootstrap start",
    "ticket_panel_bootstrap_once": "/ticket-panel bootstrap once",
    "ticket_panel_bootstrap_stop": "/ticket-panel bootstrap stop",
}

BORING_PUBLIC_TARGET = {
    "stoney",
    "mod",
    "ticket",
    "tickets",
    "ticket-intake",
    "ticket-category",
    "ticket-panel",
    "verify",
}


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _truncate(value: Any, limit: int = 1000) -> str:
    text = _safe_str(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _is_staff_or_admin(interaction: discord.Interaction) -> bool:
    try:
        user = interaction.user
        if not isinstance(user, discord.Member):
            return False
        perms = user.guild_permissions
        if perms.administrator or perms.manage_guild:
            return True
        if perms.manage_messages or perms.manage_channels or perms.moderate_members:
            return True
        return False
    except Exception:
        return False


def _add_field(embed: discord.Embed, name: str, value: str, *, inline: bool = False) -> None:
    try:
        embed.add_field(name=name, value=value[:1024] or "—", inline=inline)
    except Exception:
        pass


def _base_embed(section: str) -> discord.Embed:
    title_map = {
        "overview": "📚 Stoney Command Help",
        "setup": "🧭 Stoney Setup Commands",
        "tickets": "🎫 Ticket Commands",
        "panels": "🎛️ Ticket Panel Commands",
        "verification": "✅ Verification Commands",
        "moderation": "🛡️ Moderation Commands",
        "utilities": "🧹 Utility Commands",
    }
    embed = discord.Embed(
        title=title_map.get(section, "📚 Stoney Command Help"),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.set_footer(text="Boring command layout: few top-level commands, many organized subcommands.")
    return embed


def _overview_embed() -> discord.Embed:
    embed = _base_embed("overview")
    embed.description = (
        "Stoney uses a TicketTool-style command layout: **small public surface, grouped tools underneath**.\n\n"
        "Use `/stoney help section:<category>` to drill into a specific area."
    )
    _add_field(
        embed,
        "Top-level command families",
        "`/stoney` setup, help, command audit, cleanup, spam\n"
        "`/ticket` close/reopen/delete and current-ticket actions\n"
        "`/tickets` queues, lists, searching, history\n"
        "`/ticket-panel` panel posting/config/rules/bootstrap\n"
        "`/ticket-category` category/routing tools\n"
        "`/ticket-intake` intake setup and panel alias\n"
        "`/verify` verification/resident role tools\n"
        "`/mod` moderation tools",
    )
    _add_field(
        embed,
        "Where old commands went",
        "`/spam_guard` → `/stoney spam panel`\n"
        "`/spam_guard_status` → `/stoney spam status`\n"
        "`/grant_vr` → `/verify grant-vr`\n"
        "`/fix_unverified` → `/verify repair-unverified`\n"
        "`/ticket_panel_*` → `/ticket-panel ...`\n"
        "`/channel_cleanup_*` → `/stoney cleanup ...`",
    )
    _add_field(
        embed,
        "Command audit",
        "Run `/stoney commands` to see the current top-level command count and stale alias status.",
    )
    return embed


def _setup_embed() -> discord.Embed:
    embed = _base_embed("setup")
    embed.description = "Server owner/admin setup lives under `/stoney` so every server stays isolated and DB-backed."
    _add_field(
        embed,
        "Recommended setup flow",
        "`/stoney setup` — guided quick-start\n"
        "`/stoney setup-defaults` — create safe default categories/roles/channels\n"
        "`/stoney setup-assistant` — interactive setup wizard\n"
        "`/stoney setup-review` — review saved per-server config\n"
        "`/stoney permission-check` — verify bot permissions/hierarchy\n"
        "`/stoney launch-check` — production readiness check\n"
        "`/stoney tickettool-check` — TicketTool parity audit\n"
        "`/stoney production-audit` — brutal public launch audit",
    )
    _add_field(
        embed,
        "Manual setup helpers",
        "`/stoney setup-tickets` — ticket category/staff/transcripts\n"
        "`/stoney setup-verify` — verification channel/roles\n"
        "`/stoney setup-logs` — modlog/join/security logs\n"
        "`/stoney setup-find` — search for matching channels/roles\n"
        "`/stoney setup-picker` — dropdown setup picker\n"
        "`/stoney setup-verify-ids` — ID fallback setup",
    )
    return embed


def _tickets_embed() -> discord.Embed:
    embed = _base_embed("tickets")
    embed.description = "Ticket actions are split between current-ticket actions and ticket queue/history actions."
    _add_field(
        embed,
        "Current ticket actions",
        "`/ticket close` — close the current ticket\n"
        "`/ticket reopen` — reopen a closed ticket\n"
        "`/ticket delete` — delete a ticket channel safely",
    )
    _add_field(
        embed,
        "Queue/history actions",
        "`/tickets` — grouped ticket queue/history tools\n"
        "`/ticket-category` — category and routing tools\n"
        "`/ticket-intake` — intake configuration tools\n"
        "`/ticket-intake post-panel` — compatibility alias for posting the public panel",
    )
    return embed


def _panels_embed() -> discord.Embed:
    embed = _base_embed("panels")
    embed.description = "Panel tools are grouped under one command family so the bot can have many panel features without command spam."
    _add_field(
        embed,
        "Panel commands",
        "`/ticket-panel post` — post the Create Ticket panel\n"
        "`/ticket-panel list` — list DB-backed panels\n"
        "`/ticket-panel show` — inspect a panel\n"
        "`/ticket-panel bind-categories` — bind allowed category slugs\n"
        "`/ticket-panel runtime` — show effective runtime config\n"
        "`/ticket-panel rules view` — view behavior rules\n"
        "`/ticket-panel rules set` — update common behavior rules",
    )
    _add_field(
        embed,
        "Bootstrap/self-heal",
        "`/ticket-panel bootstrap status`\n"
        "`/ticket-panel bootstrap run`\n"
        "`/ticket-panel bootstrap all`\n"
        "`/ticket-panel bootstrap start`\n"
        "`/ticket-panel bootstrap once`\n"
        "`/ticket-panel bootstrap stop`",
    )
    return embed


def _verification_embed() -> discord.Embed:
    embed = _base_embed("verification")
    embed.description = "Verification commands use per-server role config and do not rely on deployment `.env` IDs."
    _add_field(
        embed,
        "Verification tools",
        "`/verify status` — show verification/resident status\n"
        "`/verify diagnose` — deep verification diagnostics\n"
        "`/verify set-verified` — add/remove Verified\n"
        "`/verify set-resident` — add/remove Resident\n"
        "`/verify grant-vr` — grant Verified + Resident and remove Unverified\n"
        "`/verify fix-member` — repair one member's Unverified role\n"
        "`/verify repair-unverified` — bulk repair missing Unverified roles",
    )
    return embed


def _moderation_embed() -> discord.Embed:
    embed = _base_embed("moderation")
    embed.description = "Moderation tools and safety checks stay under `/mod` and are protected by per-server staff scope."
    _add_field(
        embed,
        "Moderation family",
        "`/mod` — grouped moderation commands\n"
        "Quick-mod buttons appear on risk/modlog panels when available.\n"
        "Supplemental modlog listeners cover role/nickname/timeout, joins/leaves, voice, webhooks, emojis, stickers, scheduled events, and automod events.",
    )
    _add_field(
        embed,
        "Spam guard",
        "`/stoney spam panel` — interactive spam guard controls\n"
        "`/stoney spam status` — status and persistence diagnostics",
    )
    return embed


def _utilities_embed() -> discord.Embed:
    embed = _base_embed("utilities")
    embed.description = "Utilities are tucked under `/stoney` so they do not clutter the public slash command list."
    _add_field(
        embed,
        "Cleanup",
        "`/stoney cleanup status` — worker/config status\n"
        "`/stoney cleanup run` — run configured channel cleanup\n"
        "`/stoney cleanup purge` — purge selected channel messages",
    )
    _add_field(
        embed,
        "Health/config",
        "`/stoney db-check` — DB/config diagnostics\n"
        "`/stoney archive-backfill` — repair/archive ticket history\n"
        "`/stoney setup-review` — config review\n"
        "`/stoney commands` — command surface audit\n"
        "`/stoney help` — command catalog",
    )
    return embed


def _embed_for_section(section: Optional[str]) -> discord.Embed:
    selected = _safe_str(section, "overview").lower()
    if selected == "setup":
        return _setup_embed()
    if selected == "tickets":
        return _tickets_embed()
    if selected == "panels":
        return _panels_embed()
    if selected == "verification":
        return _verification_embed()
    if selected == "moderation":
        return _moderation_embed()
    if selected == "utilities":
        return _utilities_embed()
    return _overview_embed()


def _command_name(command: Any) -> str:
    return _safe_str(getattr(command, "name", ""))


def _iter_subcommands(command: Any) -> Iterable[str]:
    try:
        for child in list(getattr(command, "commands", []) or []):
            child_name = _command_name(child)
            if not child_name:
                continue
            grandchildren = list(getattr(child, "commands", []) or [])
            if grandchildren:
                for grandchild in grandchildren:
                    grand_name = _command_name(grandchild)
                    if grand_name:
                        yield f"{child_name} {grand_name}"
            else:
                yield child_name
    except Exception:
        return


def _local_top_level_commands(interaction: discord.Interaction) -> list[Any]:
    try:
        tree = interaction.client.tree
        return list(tree.get_commands(guild=None) or [])
    except Exception:
        return []


def _command_surface_embed(interaction: discord.Interaction) -> discord.Embed:
    commands = _local_top_level_commands(interaction)
    names = sorted(_command_name(cmd) for cmd in commands if _command_name(cmd))
    stale_present = [name for name in names if name in STALE_TOP_LEVEL_MOVES]
    unexpected = [name for name in names if name not in BORING_PUBLIC_TARGET]
    missing_target = [name for name in sorted(BORING_PUBLIC_TARGET) if name not in names]

    count = len(names)
    color = discord.Color.green() if count <= 15 and not stale_present else discord.Color.gold() if count <= 25 else discord.Color.red()
    embed = discord.Embed(
        title="🧾 Stoney Command Surface Audit",
        color=color,
        timestamp=now_utc(),
        description=(
            f"Top-level global commands currently loaded locally: **{count}**\n"
            "Target style: boring TicketTool-style grouped commands."
        ),
    )

    _add_field(embed, "Top-level Commands", _truncate("\n".join(f"• `/{name}`" for name in names), 1024) or "None")

    if stale_present:
        moved_lines = [f"• `/{name}` → `{STALE_TOP_LEVEL_MOVES.get(name, 'grouped command')}`" for name in stale_present]
        _add_field(embed, "⚠️ Stale Top-level Aliases Still Present", _truncate("\n".join(moved_lines), 1024))
    else:
        _add_field(embed, "Stale Alias Status", "✅ No known stale top-level aliases are loaded locally.")

    if unexpected:
        _add_field(embed, "Unexpected Top-level Commands", _truncate("\n".join(f"• `/{name}`" for name in unexpected), 1024))
    else:
        _add_field(embed, "Unexpected Top-level Commands", "✅ None outside the expected boring public surface.")

    if missing_target:
        _add_field(embed, "Missing Expected Families", _truncate("\n".join(f"• `/{name}`" for name in missing_target), 1024))

    grouped_lines: list[str] = []
    for cmd in commands:
        name = _command_name(cmd)
        if not name:
            continue
        children = list(_iter_subcommands(cmd))
        if children:
            grouped_lines.append(f"`/{name}` → {len(children)} visible subcommand path(s)")
    if grouped_lines:
        _add_field(embed, "Grouped Command Depth", _truncate("\n".join(grouped_lines), 1024))

    embed.set_footer(text="If stale commands still show in Discord UI, wait for the next successful global sync/propagation.")
    return embed


@app_commands.describe(section="Optional help category to view.")
@app_commands.choices(section=HELP_SECTION_CHOICES)
async def stoney_help_callback(interaction: discord.Interaction, section: Optional[app_commands.Choice[str]] = None) -> None:
    value = section.value if section is not None else "overview"
    embed = _embed_for_section(value)
    if not _is_staff_or_admin(interaction):
        embed.description = (embed.description or "") + "\n\nSome listed tools are staff/admin-only."
    await reply_once(interaction, {"embed": embed, "ephemeral": True})


async def stoney_commands_callback(interaction: discord.Interaction) -> None:
    if not _is_staff_or_admin(interaction):
        return await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
    await reply_once(interaction, {"embed": _command_surface_embed(interaction), "ephemeral": True})


def _attach_command_once(name: str, description: str, callback: Any) -> bool:
    try:
        if stoney_group.get_command(name) is not None:
            return False
        stoney_group.add_command(app_commands.Command(name=name, description=description, callback=callback))
        return True
    except Exception:
        raise


def register_public_help_group_commands(bot: Any, tree: Any) -> None:
    global _REGISTERED
    _ = bot, tree
    if _REGISTERED:
        return

    added: list[str] = []
    try:
        if _attach_command_once("help", "Show the Stoney command catalog.", stoney_help_callback):
            added.append("/stoney help")
        if _attach_command_once("commands", "Audit the current top-level slash command surface.", stoney_commands_callback):
            added.append("/stoney commands")
        print("✅ public_help_group: attached " + (", ".join(added) if added else "existing help/catalog commands"))
    except Exception as e:
        print(f"⚠️ public_help_group failed attaching help/catalog commands: {repr(e)}")
        raise

    _REGISTERED = True


__all__ = ["register_public_help_group_commands"]
