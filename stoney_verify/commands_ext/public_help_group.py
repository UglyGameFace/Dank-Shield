from __future__ import annotations

"""Public /dank help and /dank commands catalog.

Dank Shield should teach server owners and staff what to do next.
The help surface is workflow-based on purpose: normal users should see the
short path, admins should see the setup/repair path, and advanced/debug tools
should be clearly called out as advanced instead of looking like normal tasks.
"""

from typing import Any, Iterable, Optional

import discord
from discord import app_commands

from ..globals import now_utc
from .common import reply_once
from .public_setup_group import stoney_group

_REGISTERED = False

HELP_SECTION_CHOICES = [
    app_commands.Choice(name="Start Here", value="overview"),
    app_commands.Choice(name="Setup / First Install", value="setup"),
    app_commands.Choice(name="Tickets", value="tickets"),
    app_commands.Choice(name="Ticket Panels", value="panels"),
    app_commands.Choice(name="Verification", value="verification"),
    app_commands.Choice(name="Moderation", value="moderation"),
    app_commands.Choice(name="Cleanup / Protection", value="utilities"),
    app_commands.Choice(name="Admin / Advanced Tools", value="advanced"),
]

STALE_TOP_LEVEL_MOVES = {
    "stoney": "/dank",
    "spam": "/dank protection",
    "automod": "/dank protection",
    "spam_guard": "/dank protection",
    "spam_guard_status": "/dank protection",
    "fix_unverified": "/verify repair-unverified",
    "set_verified": "/verify set-verified",
    "set_resident": "/verify set-resident",
    "grant_vr": "/verify grant-vr",
    "verify_diagnose": "/verify diagnose",
    "fix_unverified_member": "/verify fix-member",
    "verify_status": "/verify status",
    "channel_cleanup_status": "/dank cleanup status",
    "run_channel_cleanup": "/dank cleanup run",
    "purge_channel_messages": "/dank cleanup purge",
    "ticket_setup_status": "/dank setup",
    "ticket_setup_discover": "/dank setup",
    "ticket_setup_save_discovered": "/dank setup",
    "ticket_setup_set_channel": "/dank setup",
    "ticket_setup_set_role": "/dank setup",
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
    "dank",
    "mod",
    "ticket",
    "tickets",
    "ticket-intake",
    "ticket-category",
    "ticket-panel",
    "verify",
}

ADMIN_REPAIR_WORDS = (
    "repair",
    "backfill",
    "permission",
    "launch",
    "audit",
    "tickettool",
    "cache",
    "bootstrap",
    "runtime",
    "rules set",
    "bind-categories",
    "debug",
    "diagnose",
)

ADVANCED_HELP_PATTERNS = ADMIN_REPAIR_WORDS


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


def _is_admin_owner(interaction: discord.Interaction) -> bool:
    try:
        user = interaction.user
        if not isinstance(user, discord.Member):
            return False
        perms = user.guild_permissions
        return bool(perms.administrator or perms.manage_guild)
    except Exception:
        return False


def _add_field(embed: discord.Embed, name: str, value: str, *, inline: bool = False) -> None:
    try:
        embed.add_field(name=name, value=value[:1024] or "—", inline=inline)
    except Exception:
        pass


def _base_embed(section: str) -> discord.Embed:
    title_map = {
        "overview": "📚 Dank Shield Help",
        "setup": "🧭 Setup / First Install",
        "tickets": "🎫 Tickets",
        "panels": "🎛️ Ticket Panels",
        "verification": "✅ Verification",
        "moderation": "🛡️ Moderation",
        "utilities": "🧹 Cleanup / Protection",
        "advanced": "🧰 Admin / Advanced Tools",
    }
    embed = discord.Embed(
        title=title_map.get(section, "📚 Dank Shield Help"),
        color=discord.Color.blurple(),
        timestamp=now_utc(),
    )
    embed.set_footer(text="Dank Shield • Good vibes in. Bad actors out.")
    return embed


def _overview_embed() -> discord.Embed:
    embed = _base_embed("overview")
    embed.description = (
        "**Dank Shield keeps your server organized, verified, and protected without killing the vibe.**\n\n"
        "This menu is written like an in-app guide. Start with the first matching situation below."
    )
    _add_field(
        embed,
        "I am setting up the bot",
        "Use `/dank setup`, then `/dank overview`. Setup handles build/repair; overview shows what is still missing.",
    )
    _add_field(
        embed,
        "I am securing the server",
        "Use `/dank protection`. That is the production safety center for Automod content filters and Spam Guard behavior protection.",
    )
    _add_field(
        embed,
        "I am a member and need help",
        "Use the server's public buttons. Press **Create Ticket** for support or use the verification panel if you need to verify. You should not need slash commands.",
    )
    _add_field(
        embed,
        "I am staff working a ticket",
        "Use `/ticket` inside the ticket channel. Example: `/ticket close`, `/ticket reopen`, or `/ticket delete`.",
    )
    _add_field(
        embed,
        "What the command groups mean",
        "`/dank` setup, overview, protection, help, cleanup, members, welcome, roles, modlog, embed\n"
        "`/ticket` actions for the current ticket\n"
        "`/tickets` server-wide ticket management\n"
        "`/ticket-panel` public Create Ticket panel tools\n"
        "`/ticket-category` ticket menu/routing options\n"
        "`/verify` verification repair and approval tools\n"
        "`/mod` moderation tools",
    )
    return embed


def _setup_embed() -> discord.Embed:
    embed = _base_embed("setup")
    embed.description = "`/dank setup` is the owner/admin setup hub. Most servers should never need old setup commands."
    _add_field(
        embed,
        "Fresh server path",
        "1. Run `/dank setup`\n"
        "2. Press **Auto-Build Missing Items**\n"
        "3. Press **Fix Server Layout**\n"
        "4. Press **Run Health Check**\n"
        "5. Run `/dank overview` to see what is still missing",
    )
    _add_field(
        embed,
        "Existing server path",
        "1. Run `/dank setup`\n"
        "2. Press **Use My Existing Server**\n"
        "3. Pick your real roles/channels/categories\n"
        "4. Press **Fix Server Layout** if things look messy\n"
        "5. Run `/dank overview`",
    )
    _add_field(
        embed,
        "Setup buttons in plain English",
        "**Auto-Build Missing Items** — creates missing default roles/channels/categories\n"
        "**Name Items Before Build** — choose names before creating anything\n"
        "**Use My Existing Server** — map Dank Shield to your existing roles/channels/categories\n"
        "**Ticket Menu Options** — edit what users see when they create tickets\n"
        "**Fix Server Layout** — move configured channels/categories into a clean order\n"
        "**Run Health Check** — show blockers, warnings, and passing checks",
    )
    _add_field(
        embed,
        "Recommended layout",
        "👋 START HERE — welcome, verify, support, Voice Verification\n"
        "🎫 ACTIVE TICKETS — open ticket channels\n"
        "📦 TICKET ARCHIVE — closed ticket channels\n"
        "🛠️ STAFF TOOLS — vc queue, transcripts, mod-log, join/leave-log, bot-status",
    )
    return embed


def _tickets_embed() -> discord.Embed:
    embed = _base_embed("tickets")
    embed.description = "Users should press buttons. Staff can use commands when they need direct control."
    _add_field(embed, "For users", "Press **Create Ticket** on the public ticket panel, choose a reason, then explain the issue in the new private ticket.")
    _add_field(embed, "For staff inside a ticket", "`/ticket close` — close this ticket\n`/ticket reopen` — reopen this ticket\n`/ticket delete` — safely delete this ticket channel")
    _add_field(embed, "For staff managing tickets", "`/tickets` — queue/list tools\n`/ticket-category` — edit routing/menu choices\n`/ticket-panel post` — post a public ticket panel")
    return embed


def _panels_embed() -> discord.Embed:
    embed = _base_embed("panels")
    embed.description = "The ticket panel is the public message users press to open tickets."
    _add_field(embed, "Normal panel path", "1. Finish `/dank setup`\n2. Run `/dank overview`\n3. Run `/ticket-panel post`\n4. Users press **Create Ticket**")
    _add_field(embed, "Useful panel commands", "`/ticket-panel post` — post a fresh public panel\n`/ticket-panel list` — list known panels\n`/ticket-panel show` — inspect a panel")
    _add_field(embed, "If a panel button fails", "Run `/dank setup` → **Run Health Check**. If healthy, repost the panel with `/ticket-panel post`. Persistent panel buttons should survive reboot.")
    return embed


def _verification_embed() -> discord.Embed:
    embed = _base_embed("verification")
    embed.description = "Verification commands are mostly staff repair tools. New members should use buttons/panels."
    _add_field(embed, "Role meanings", "**Pending / Unverified** — not verified yet\n**Verified** — passed verification\n**Member / Resident** — full-access member role, if your server uses one")
    _add_field(embed, "Staff verification commands", "`/verify status` — check a member\n`/verify diagnose` — deeper troubleshooting\n`/verify grant-vr` — approve a member\n`/verify fix-member` — repair one member\n`/verify repair-unverified` — bulk repair pending users")
    _add_field(embed, "VC verification needs", "1. A voice channel for verification sessions\n2. A staff queue/request text channel\n3. Health check passing in `/dank setup`")
    return embed


def _moderation_embed() -> discord.Embed:
    embed = _base_embed("moderation")
    embed.description = "Moderation tools stay under `/mod` so normal setup stays clean."
    _add_field(embed, "Moderation tools", "`/mod` — grouped moderation commands\n`/dank modlog` — configure/check logging\nRaid/security logs — suspicious joins and security events")
    _add_field(embed, "Protection Center", "`/dank protection` — one safety surface for Automod content filters and Spam Guard behavior protection")
    _add_field(embed, "When moderation feels broken", "Run `/dank overview`, then `/dank setup` → **Run Health Check**. Most mod issues are missing staff roles, missing log channels, or missing bot permissions.")
    return embed


def _utilities_embed() -> discord.Embed:
    embed = _base_embed("utilities")
    embed.description = "Cleanup and protection tools are staff/admin utilities. They are separate from normal member workflows."
    _add_field(embed, "Protection", "`/dank protection` — Safe/Strict/Off presets, add content filters, privately test bypass attempts, and see Spam Guard status")
    _add_field(embed, "Cleanup", "`/dank cleanup status` — show cleanup worker/config status\n`/dank cleanup run` — run configured cleanup\n`/dank cleanup purge` — purge selected channel messages")
    _add_field(embed, "Health / config", "`/dank overview` — all-in-one setup checklist\n`/dank setup` — setup, repair, and health check\n`/dank commands` — staff-only slash command audit\n`/dank help` — workflow guide")
    return embed


def _advanced_embed() -> discord.Embed:
    embed = _base_embed("advanced")
    embed.description = (
        "These are **admin/developer repair tools**, not normal daily commands. "
        "Ticket Tool-style design means the normal path stays simple and advanced tools stay clearly labeled."
    )
    _add_field(
        embed,
        "Normal admins should start here instead",
        "Use `/dank overview`, `/dank setup` → **Run Health Check**, and `/dank protection` before touching advanced tools.",
    )
    _add_field(
        embed,
        "Admin repair tools",
        "**Permission checks** — diagnose missing Discord permissions\n"
        "**Archive/backfill tools** — reconnect old ticket channels to records\n"
        "**TicketTool/readiness checks** — compare production readiness\n"
        "**Launch/production audits** — final release safety checks",
    )
    _add_field(
        embed,
        "Developer/debug tools",
        "**Config cache/debug** — inspect cached setup data\n"
        "**Bootstrap/runtime tools** — repair persistent panels/workers\n"
        "**Rules/bind-category tools** — advanced panel routing customization\n"
        "**Legacy safety tools** — old spam/automod commands are public-admin only; normal servers use `/dank protection`",
    )
    _add_field(
        embed,
        "Rule of thumb",
        "If a command says **audit**, **debug**, **cache**, **backfill**, **bootstrap**, **runtime**, or **production**, treat it as advanced. Normal owners should not need it day to day.",
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
    if selected == "advanced":
        return _advanced_embed()
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


def _is_advanced_subcommand(name: str) -> bool:
    lowered = name.lower()
    return any(pattern in lowered for pattern in ADVANCED_HELP_PATTERNS)


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
        title="🧾 Dank Shield Command Audit",
        color=color,
        timestamp=now_utc(),
        description=(
            f"Top-level slash commands loaded locally: **{count}**\n"
            "Goal: a clean Ticket Tool-style command surface. Normal tasks should be obvious; advanced tools should be labeled or hidden."
        ),
    )
    _add_field(embed, "Top-level Commands", _truncate("\n".join(f"• `/{name}`" for name in names), 1024) or "None")

    if stale_present:
        moved_lines = [f"• `/{name}` → `{STALE_TOP_LEVEL_MOVES.get(name, 'grouped command')}`" for name in stale_present]
        _add_field(embed, "⚠️ Old Commands Still Showing", _truncate("\n".join(moved_lines), 1024))
    else:
        _add_field(embed, "Old Command Status", "✅ No known old top-level commands are loaded locally.")

    if unexpected:
        _add_field(embed, "Unexpected Top-level Commands", _truncate("\n".join(f"• `/{name}`" for name in unexpected), 1024))
    else:
        _add_field(embed, "Unexpected Top-level Commands", "✅ None outside the intended clean public surface.")

    if missing_target:
        _add_field(embed, "Missing Expected Command Groups", _truncate("\n".join(f"• `/{name}`" for name in missing_target), 1024))

    grouped_lines: list[str] = []
    advanced_hidden = 0
    for cmd in commands:
        name = _command_name(cmd)
        if not name:
            continue
        children = list(_iter_subcommands(cmd))
        visible_children = [child for child in children if not _is_advanced_subcommand(child)]
        hidden = max(0, len(children) - len(visible_children))
        advanced_hidden += hidden
        if children:
            suffix = f" + {hidden} advanced/debug" if hidden else ""
            grouped_lines.append(f"`/{name}` → {len(visible_children)} normal path(s){suffix}")
    if grouped_lines:
        _add_field(embed, "Grouped Command Depth", _truncate("\n".join(grouped_lines), 1024))
    if advanced_hidden:
        _add_field(embed, "Advanced Noise Hidden", f"{advanced_hidden} advanced/debug subcommand path(s) are intentionally omitted from normal help screens.")

    embed.set_footer(text="If old commands still appear in Discord, wait for a successful sync or restart Discord.")
    return embed


@app_commands.describe(section="Pick the topic you need help with.")
@app_commands.choices(section=HELP_SECTION_CHOICES)
async def dank_help_callback(interaction: discord.Interaction, section: Optional[app_commands.Choice[str]] = None) -> None:
    value = section.value if section is not None else "overview"
    embed = _embed_for_section(value)
    if not _is_staff_or_admin(interaction):
        embed.description = (embed.description or "") + "\n\nSome listed tools are staff/admin-only."
    await reply_once(interaction, {"embed": embed, "ephemeral": True})


async def dank_commands_callback(interaction: discord.Interaction) -> None:
    if not _is_staff_or_admin(interaction):
        return await reply_once(interaction, {"content": "❌ Staff only. Ask a server admin or staff member to run this.", "ephemeral": True})
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
        if _attach_command_once(
            "help",
            "Open the clear guide for setup, tickets, verification, moderation, cleanup, and advanced tools.",
            dank_help_callback,
        ):
            added.append("/dank help")
        if _attach_command_once(
            "commands",
            "Staff-only: show loaded slash commands and flag old, confusing, or advanced command noise.",
            dank_commands_callback,
        ):
            added.append("/dank commands")
        print("✅ public_help_group: attached " + (", ".join(added) if added else "existing help/catalog commands"))
    except Exception as e:
        print(f"⚠️ public_help_group failed attaching help/catalog commands: {repr(e)}")
        raise

    _REGISTERED = True


__all__ = ["register_public_help_group_commands"]
