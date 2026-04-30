from __future__ import annotations

"""Public /stoney help command.

TicketTool-style command discovery without adding a new top-level /help command.
This keeps the public slash surface boring and small while still making the
feature set easy to understand.
"""

from typing import Any, Optional

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


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


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
        "`/stoney` setup, help, health, cleanup, spam\n"
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
        "`/stoney help` — this command catalog",
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


@app_commands.describe(section="Optional help category to view.")
@app_commands.choices(section=HELP_SECTION_CHOICES)
async def stoney_help_callback(interaction: discord.Interaction, section: Optional[app_commands.Choice[str]] = None) -> None:
    value = section.value if section is not None else "overview"
    embed = _embed_for_section(value)
    if not _is_staff_or_admin(interaction):
        embed.description = (embed.description or "") + "\n\nSome listed tools are staff/admin-only."
    await reply_once(interaction, {"embed": embed, "ephemeral": True})


def register_public_help_group_commands(bot: Any, tree: Any) -> None:
    global _REGISTERED
    _ = bot, tree
    if _REGISTERED:
        return

    try:
        if stoney_group.get_command("help") is None:
            stoney_group.add_command(
                app_commands.Command(
                    name="help",
                    description="Show the Stoney command catalog.",
                    callback=stoney_help_callback,
                )
            )
            print("✅ public_help_group: attached /stoney help command catalog")
        else:
            print("✅ public_help_group: /stoney help already attached")
    except Exception as e:
        print(f"⚠️ public_help_group failed attaching /stoney help: {repr(e)}")
        raise

    _REGISTERED = True


__all__ = ["register_public_help_group_commands"]
