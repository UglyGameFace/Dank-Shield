from __future__ import annotations

"""
Simplified /stoney setup flow.

The older setup UI exposed too many internals at once and used confusing labels
like "Ticket Menu Options" even when the live user-facing ticket flow is a
simple Create Ticket modal. This guard replaces the /stoney setup entrypoint
with a boring TicketTool-style guided panel:

- Home
- Ticket System
- ID Verification
- Voice Verification
- Logging
- Health Check

Each service page includes a clear "Create Missing ..." action. Creation is
fill-only: it reuses existing Discord items, creates only missing items, and
writes the selected IDs to guild config without renaming/deleting/moving owner
items.
"""

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import discord
from discord import app_commands

from ..config_new.service_gate import service_status
from ..config_new.setup_health import build_guild_setup_health, build_setup_health_embed
from ..globals import now_utc

try:
    from ..commands_ext.public_setup_config_writer import upsert_guild_config
except Exception:
    upsert_guild_config = None  # type: ignore

try:
    from ..guild_config import invalidate_guild_config
except Exception:
    def invalidate_guild_config(guild_id: int) -> None:  # type: ignore
        return None

_REGISTERED = False


# ============================================================
# Logging / tiny helpers
# ============================================================

def _log(message: str) -> None:
    try:
        print(f"🧭 setup_flow_simplify {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ setup_flow_simplify {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _yes(value: bool) -> str:
    return "✅" if value else "❌"


def _normalize_name(value: Any) -> str:
    text = _safe_str(value).lower()
    text = text.replace("・", "-").replace("│", "-").replace("┃", "-")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text


def _admin_or_manage_guild(member: Any) -> bool:
    try:
        perms = getattr(member, "guild_permissions", None)
        return bool(
            getattr(perms, "administrator", False)
            or getattr(perms, "manage_guild", False)
            or getattr(perms, "manage_channels", False)
        )
    except Exception:
        return False


async def _reply_or_edit(interaction: discord.Interaction, *, embed: discord.Embed, view: discord.ui.View) -> None:
    try:
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=view)
        else:
            await interaction.response.edit_message(embed=embed, view=view)
    except discord.InteractionResponded:
        await interaction.edit_original_response(embed=embed, view=view)
    except Exception:
        try:
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        except Exception:
            pass


async def _send_initial(interaction: discord.Interaction, *, embed: discord.Embed, view: discord.ui.View) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
    except Exception:
        pass


async def _save_config(guild_id: int, updates: Dict[str, Any]) -> None:
    if upsert_guild_config is None:
        raise RuntimeError("setup config writer unavailable")

    payload = {
        **updates,
        "__config_write_mode": "fill_missing",
        "__config_write_source": "setup_flow_simplify",
        "configured_at": now_utc().isoformat(),
    }
    await upsert_guild_config(guild_id, payload)  # type: ignore[misc]
    try:
        invalidate_guild_config(guild_id)
    except Exception:
        pass


# ============================================================
# Discord object discovery / creation
# ============================================================

def _find_category(guild: discord.Guild, keys: Sequence[str]) -> Optional[discord.CategoryChannel]:
    wanted = [_normalize_name(k) for k in keys if _safe_str(k)]
    for category in getattr(guild, "categories", []) or []:
        name = _normalize_name(getattr(category, "name", ""))
        if name in wanted or any(k and k in name for k in wanted):
            return category
    return None


def _find_text_channel(guild: discord.Guild, keys: Sequence[str], *, category: Optional[discord.CategoryChannel] = None) -> Optional[discord.TextChannel]:
    wanted = [_normalize_name(k) for k in keys if _safe_str(k)]
    channels = list(getattr(guild, "text_channels", []) or [])
    if category is not None:
        channels = [ch for ch in channels if getattr(ch, "category_id", None) == category.id] + channels
    for channel in channels:
        name = _normalize_name(getattr(channel, "name", ""))
        if name in wanted or any(k and k in name for k in wanted):
            return channel
    return None


def _find_voice_channel(guild: discord.Guild, keys: Sequence[str]) -> Optional[discord.VoiceChannel]:
    wanted = [_normalize_name(k) for k in keys if _safe_str(k)]
    for channel in getattr(guild, "voice_channels", []) or []:
        name = _normalize_name(getattr(channel, "name", ""))
        if name in wanted or any(k and k in name for k in wanted):
            return channel
    return None


def _find_role(guild: discord.Guild, keys: Sequence[str]) -> Optional[discord.Role]:
    wanted = [_normalize_name(k) for k in keys if _safe_str(k)]
    for role in getattr(guild, "roles", []) or []:
        if role.is_default():
            continue
        name = _normalize_name(getattr(role, "name", ""))
        if name in wanted or any(k and k in name for k in wanted):
            return role
    return None


async def _ensure_category(guild: discord.Guild, display_name: str, keys: Sequence[str], notes: List[str]) -> discord.CategoryChannel:
    existing = _find_category(guild, keys)
    if existing is not None:
        notes.append(f"Reused category: **{existing.name}**")
        return existing
    created = await guild.create_category(display_name, reason="Stoney Verify setup: create missing category")
    notes.append(f"Created category: **{created.name}**")
    return created


async def _ensure_text_channel(
    guild: discord.Guild,
    display_name: str,
    keys: Sequence[str],
    notes: List[str],
    *,
    category: Optional[discord.CategoryChannel] = None,
) -> discord.TextChannel:
    existing = _find_text_channel(guild, keys, category=category)
    if existing is not None:
        notes.append(f"Reused channel: {existing.mention}")
        return existing
    created = await guild.create_text_channel(
        display_name,
        category=category,
        reason="Stoney Verify setup: create missing text channel",
    )
    notes.append(f"Created channel: {created.mention}")
    return created


async def _ensure_voice_channel(
    guild: discord.Guild,
    display_name: str,
    keys: Sequence[str],
    notes: List[str],
    *,
    category: Optional[discord.CategoryChannel] = None,
) -> discord.VoiceChannel:
    existing = _find_voice_channel(guild, keys)
    if existing is not None:
        notes.append(f"Reused voice channel: {existing.mention}")
        return existing
    created = await guild.create_voice_channel(
        display_name,
        category=category,
        reason="Stoney Verify setup: create missing voice channel",
    )
    notes.append(f"Created voice channel: {created.mention}")
    return created


async def _ensure_role(guild: discord.Guild, display_name: str, keys: Sequence[str], notes: List[str]) -> discord.Role:
    existing = _find_role(guild, keys)
    if existing is not None:
        notes.append(f"Reused role: {existing.mention}")
        return existing
    created = await guild.create_role(name=display_name, reason="Stoney Verify setup: create missing role")
    notes.append(f"Created role: {created.mention}")
    return created


async def _create_ticket_items(guild: discord.Guild) -> List[str]:
    notes: List[str] = []
    active = await _ensure_category(guild, "🎫 ACTIVE TICKETS", ("active tickets", "tickets", "support tickets"), notes)
    archive = await _ensure_category(guild, "📦 TICKET ARCHIVE", ("ticket archive", "archived tickets", "closed tickets"), notes)
    tools = await _ensure_category(guild, "🛠 STAFF TOOLS", ("staff tools", "staff", "support tools"), notes)
    support = await _ensure_text_channel(guild, "🎫・support", ("support", "ticket panel", "tickets"), notes)
    transcripts = await _ensure_text_channel(guild, "📄・transcripts", ("transcripts", "ticket transcripts"), notes, category=tools)
    staff = await _ensure_role(guild, "Ticket Staff", ("ticket staff", "staff", "moderator", "mods"), notes)

    await _save_config(
        guild.id,
        {
            "ticket_category_id": str(active.id),
            "ticket_archive_category_id": str(archive.id),
            "staff_tools_category_id": str(tools.id),
            "ticket_panel_channel_id": str(support.id),
            "support_channel_id": str(support.id),
            "transcripts_channel_id": str(transcripts.id),
            "staff_role_id": str(staff.id),
            "vc_staff_role_id": str(staff.id),
            "ticket_prefix": "ticket",
        },
    )
    notes.append("Saved ticket system targets to this server's config.")
    return notes


async def _create_verification_items(guild: discord.Guild) -> List[str]:
    notes: List[str] = []
    start = await _ensure_category(guild, "👋 START HERE", ("start here", "verify", "verification"), notes)
    verify = await _ensure_text_channel(guild, "✅・verify", ("verify", "verification"), notes, category=start)
    unverified = await _ensure_role(guild, "Unverified", ("unverified", "pending"), notes)
    verified = await _ensure_role(guild, "Verified", ("verified", "member"), notes)
    resident = await _ensure_role(guild, "Resident", ("resident",), notes)

    await _save_config(
        guild.id,
        {
            "verify_channel_id": str(verify.id),
            "unverified_role_id": str(unverified.id),
            "verified_role_id": str(verified.id),
            "resident_role_id": str(resident.id),
        },
    )
    notes.append("Saved ID verification targets to this server's config.")
    return notes


async def _create_voice_items(guild: discord.Guild) -> List[str]:
    notes: List[str] = []
    start = await _ensure_category(guild, "👋 START HERE", ("start here", "verify", "verification"), notes)
    tools = await _ensure_category(guild, "🛠 STAFF TOOLS", ("staff tools", "staff", "support tools"), notes)
    vc = await _ensure_voice_channel(guild, "🎙 Voice Verification", ("voice verification", "vc verify"), notes, category=start)
    queue = await _ensure_text_channel(guild, "🎙・vc-verify-queue", ("vc verify queue", "voice verify queue", "verify queue"), notes, category=tools)

    await _save_config(
        guild.id,
        {
            "vc_verify_channel_id": str(vc.id),
            "vc_verify_queue_channel_id": str(queue.id),
        },
    )
    notes.append("Saved voice verification targets to this server's config.")
    return notes


async def _create_logging_items(guild: discord.Guild) -> List[str]:
    notes: List[str] = []
    tools = await _ensure_category(guild, "🛠 STAFF TOOLS", ("staff tools", "staff", "support tools"), notes)
    modlog = await _ensure_text_channel(guild, "🛡・mod-log", ("mod-log", "modlog", "moderation log"), notes, category=tools)
    joinlog = await _ensure_text_channel(guild, "🚪・join-leave-log", ("join leave log", "join-log", "join leave", "welcome exit"), notes, category=tools)
    status = await _ensure_text_channel(guild, "🤖・bot-status", ("bot status", "status", "stoney status"), notes, category=tools)

    await _save_config(
        guild.id,
        {
            "modlog_channel_id": str(modlog.id),
            "raidlog_channel_id": str(modlog.id),
            "force_verify_log_channel_id": str(modlog.id),
            "join_log_channel_id": str(joinlog.id),
            "status_channel_id": str(status.id),
            "bot_status_channel_id": str(status.id),
        },
    )
    notes.append("Saved logging/status targets to this server's config.")
    return notes


# ============================================================
# Embeds / View
# ============================================================

async def _service_line(guild_id: int) -> str:
    try:
        status = await service_status(guild_id)
    except Exception:
        status = {"tickets": True, "verification": False, "voice_verification": False, "moderation": False}
    return (
        f"{_yes(status.get('tickets', False))} Tickets\n"
        f"{_yes(status.get('verification', False))} ID verification\n"
        f"{_yes(status.get('voice_verification', False))} Voice verification\n"
        f"{_yes(status.get('moderation', False))} Logging/moderation"
    )


async def _home_embed(guild: discord.Guild) -> discord.Embed:
    embed = discord.Embed(
        title="Stoney Setup",
        description=(
            "Simple setup. Pick a section, create missing items, then run the health check.\n\n"
            "Nothing here renames, deletes, moves, or overwrites your existing Discord items."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Enabled services", value=await _service_line(guild.id), inline=True)
    embed.add_field(
        name="Setup flow",
        value=(
            "1. Pick services with `/setup-services`\n"
            "2. Use this panel to create/fill missing Discord items\n"
            "3. Run **Health Check**\n"
            "4. Run `/setup-finish` when critical blockers are gone"
        ),
        inline=False,
    )
    embed.set_footer(text="Boring on purpose. TicketTool-style setup beats chaos.")
    return embed


def _ticket_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Ticket System",
        description="Actual Discord objects the ticket system needs.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="What this creates/fills",
        value=(
            "• Open ticket category\n"
            "• Closed/archive ticket category\n"
            "• Support/ticket panel channel\n"
            "• Transcript channel\n"
            "• Ticket staff role"
        ),
        inline=False,
    )
    embed.add_field(
        name="User-facing ticket flow",
        value=(
            "Right now, users press **Create Ticket** and get a short form asking what they need.\n"
            "Ticket menu presets are internal/default routing helpers unless you later switch to a true dropdown menu flow."
        ),
        inline=False,
    )
    return embed


def _verification_embed() -> discord.Embed:
    embed = discord.Embed(
        title="ID Verification",
        description="Discord objects for website/ID verification.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="What this creates/fills",
        value="• Verify/start channel\n• Unverified role\n• Verified role\n• Resident role",
        inline=False,
    )
    return embed


def _voice_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Voice Verification",
        description="Discord objects for voice verification requests and staff queue.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="What this creates/fills",
        value="• Voice verification voice channel\n• VC verify queue/status text channel",
        inline=False,
    )
    return embed


def _logging_embed() -> discord.Embed:
    embed = discord.Embed(
        title="Logging",
        description="Channels for logs and setup/status messages.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="What this creates/fills",
        value="• Mod-log channel\n• Join/leave log channel\n• Bot status channel",
        inline=False,
    )
    return embed


def _result_embed(title: str, notes: Sequence[str]) -> discord.Embed:
    embed = discord.Embed(title=title, color=discord.Color.green())
    if notes:
        text = "\n".join(f"• {note}" for note in notes)
        embed.description = text[:3900]
    else:
        embed.description = "Nothing changed."
    embed.set_footer(text="Run Health Check next.")
    return embed


class SimpleSetupView(discord.ui.View):
    def __init__(self, *, guild_id: int, owner_id: int, section: str = "home") -> None:
        super().__init__(timeout=900)
        self.guild_id = int(guild_id)
        self.owner_id = int(owner_id)
        self.section = section
        self._build_items()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or int(interaction.guild.id) != self.guild_id:
            return False
        if not _admin_or_manage_guild(interaction.user):
            await interaction.response.send_message("❌ Setup requires Manage Server, Manage Channels, or Administrator.", ephemeral=True)
            return False
        return True

    def _build_items(self) -> None:
        self.clear_items()
        self.add_item(SetupSectionSelect(self.section))

        if self.section == "tickets":
            self.add_item(CreateMissingButton("Create Missing Ticket Items", "tickets", discord.ButtonStyle.success, "🎫"))
        elif self.section == "verification":
            self.add_item(CreateMissingButton("Create Missing ID Verify Items", "verification", discord.ButtonStyle.success, "✅"))
        elif self.section == "voice":
            self.add_item(CreateMissingButton("Create Missing Voice Verify Items", "voice", discord.ButtonStyle.success, "🎙"))
        elif self.section == "logging":
            self.add_item(CreateMissingButton("Create Missing Logging Items", "logging", discord.ButtonStyle.success, "🛡"))

        self.add_item(HealthButton())
        self.add_item(CloseButton())


class SetupSectionSelect(discord.ui.Select):
    def __init__(self, current: str) -> None:
        options = [
            discord.SelectOption(label="Home", value="home", emoji="🏠", default=current == "home"),
            discord.SelectOption(label="Ticket System", value="tickets", emoji="🎫", default=current == "tickets"),
            discord.SelectOption(label="ID Verification", value="verification", emoji="✅", default=current == "verification"),
            discord.SelectOption(label="Voice Verification", value="voice", emoji="🎙", default=current == "voice"),
            discord.SelectOption(label="Logging", value="logging", emoji="🛡", default=current == "logging"),
        ]
        super().__init__(placeholder="Choose setup section", min_values=1, max_values=1, options=options, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return
        section = str(self.values[0])
        embed = await _embed_for_section(guild, section)
        await _reply_or_edit(interaction, embed=embed, view=SimpleSetupView(guild_id=guild.id, owner_id=interaction.user.id, section=section))


class CreateMissingButton(discord.ui.Button):
    def __init__(self, label: str, target: str, style: discord.ButtonStyle, emoji: str) -> None:
        super().__init__(label=label, style=style, emoji=emoji, row=1)
        self.target = target

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass

        try:
            if self.target == "tickets":
                notes = await _create_ticket_items(guild)
                section = "tickets"
                title = "Ticket Items Ready"
            elif self.target == "verification":
                notes = await _create_verification_items(guild)
                section = "verification"
                title = "ID Verification Items Ready"
            elif self.target == "voice":
                notes = await _create_voice_items(guild)
                section = "voice"
                title = "Voice Verification Items Ready"
            else:
                notes = await _create_logging_items(guild)
                section = "logging"
                title = "Logging Items Ready"

            await interaction.edit_original_response(
                embed=_result_embed(title, notes),
                view=SimpleSetupView(guild_id=guild.id, owner_id=interaction.user.id, section=section),
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Create missing items failed: `{type(e).__name__}: {e}`",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )


class HealthButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Health Check", style=discord.ButtonStyle.secondary, emoji="🩺", row=2)

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return
        try:
            await interaction.response.defer(ephemeral=True)
        except Exception:
            pass
        report = await build_guild_setup_health(guild)
        embed = build_setup_health_embed(report)
        await interaction.edit_original_response(
            embed=embed,
            view=SimpleSetupView(guild_id=guild.id, owner_id=interaction.user.id, section="home"),
        )


class CloseButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Close", style=discord.ButtonStyle.danger, emoji="✖️", row=2)

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.response.edit_message(content="Setup closed.", embed=None, view=None)
        except Exception:
            pass


async def _embed_for_section(guild: discord.Guild, section: str) -> discord.Embed:
    if section == "tickets":
        return _ticket_embed()
    if section == "verification":
        return _verification_embed()
    if section == "voice":
        return _voice_embed()
    if section == "logging":
        return _logging_embed()
    return await _home_embed(guild)


async def simple_setup_command(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        await interaction.response.send_message("❌ This command must be used inside a server.", ephemeral=True)
        return
    if not _admin_or_manage_guild(interaction.user):
        await interaction.response.send_message("❌ Setup requires Manage Server, Manage Channels, or Administrator.", ephemeral=True)
        return

    embed = await _home_embed(guild)
    await _send_initial(interaction, embed=embed, view=SimpleSetupView(guild_id=guild.id, owner_id=interaction.user.id))


def install_simplified_setup_flow() -> None:
    global _REGISTERED
    if _REGISTERED:
        return
    _REGISTERED = True

    try:
        from ..commands_ext import public_setup_group as setup_mod

        group = getattr(setup_mod, "stoney_group", None)
        if group is None:
            _warn("stoney_group not found; simplified setup not installed")
            return

        try:
            group.remove_command("setup")
        except Exception:
            pass

        command = app_commands.Command(
            name="setup",
            description="Open the simple Stoney Verify setup panel.",
            callback=simple_setup_command,
        )
        group.add_command(command)
        _log("installed simplified /stoney setup panel")
    except Exception as e:
        _warn(f"failed installing simplified /stoney setup: {repr(e)}")


install_simplified_setup_flow()


__all__ = ["install_simplified_setup_flow"]
