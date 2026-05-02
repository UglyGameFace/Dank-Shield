from __future__ import annotations

"""Idempotent recommended setup for public guilds.

Auto-build must be boring and safe:
- reuse saved config IDs first
- reuse existing default-looking Discord items second
- create only truly missing items
- never move, rename, delete, or permission-overwrite existing items
- never overwrite owner-picked setup values; auto-build fills blanks only
"""

import re
import unicodedata
from typing import Any, Optional

import discord

from .common import safe_defer
from .public_setup_group import (
    _config_embed,
    _require_setup_permission,
    _role_value,
    _upsert_config,
    _utc_iso,
    stoney_group,
)
from ..guild_config import get_guild_config, invalidate_guild_config

_ATTACHED = False

DEFAULT_CONTROL_ROLE_NAME = "Bot Manager"
DEFAULT_STAFF_ROLE_NAME = "Support Team"
DEFAULT_UNVERIFIED_ROLE_NAME = "Unverified"
DEFAULT_VERIFIED_ROLE_NAME = "Verified"
DEFAULT_MEMBER_ROLE_NAME = "Member"

START_CATEGORY_NAME = "👋 START HERE"
TICKET_CATEGORY_NAME = "🎫 ACTIVE TICKETS"
ARCHIVE_CATEGORY_NAME = "📦 TICKET ARCHIVE"
MANAGEMENT_CATEGORY_NAME = "🛠️ STAFF TOOLS"

WELCOME_CHANNEL_NAME = "👋・welcome"
VERIFY_CHANNEL_NAME = "✅・verify"
TICKET_PANEL_CHANNEL_NAME = "🎫・support"
VC_VERIFY_CHANNEL_NAME = "🎙️ Voice Verification"
VC_QUEUE_CHANNEL_NAME = "🎙️・vc-verify-queue"
TRANSCRIPTS_CHANNEL_NAME = "📑・transcripts"
MODLOG_CHANNEL_NAME = "🛡️・mod-log"
JOIN_LEAVE_CHANNEL_NAME = "🚪・join-leave-log"
STATUS_CHANNEL_NAME = "📡・bot-status"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _key(value: Any) -> str:
    try:
        text = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("&", " and ")
        return re.sub(r"[^a-z0-9]+", "", text)
    except Exception:
        return ""


def _find_named(items: list[Any], name: str) -> Any:
    exact = str(name or "").strip().casefold()
    fuzzy = _key(name)
    for item in items:
        if str(getattr(item, "name", "") or "").strip().casefold() == exact:
            return item
    for item in items:
        if fuzzy and _key(getattr(item, "name", "")) == fuzzy:
            return item
    return None


def _bot_member(guild: discord.Guild) -> Optional[discord.Member]:
    try:
        return guild.me
    except Exception:
        return None


def _unique(lines: list[str], line: str) -> None:
    if line and line not in lines:
        lines.append(line)


def _line_list(lines: list[str], *, empty: str = "None", limit: int = 1000) -> str:
    if not lines:
        return empty
    out: list[str] = []
    total = 0
    for line in lines:
        text = str(line)
        if total + len(text) + 1 > limit:
            out.append(f"…and {len(lines) - len(out)} more")
            break
        out.append(text)
        total += len(text) + 1
    return "\n".join(out) or empty


def _role_from_config(guild: discord.Guild, cfg: Any, *attrs: str) -> Optional[discord.Role]:
    if cfg is None:
        return None
    for attr in attrs:
        role = guild.get_role(_safe_int(getattr(cfg, attr, 0), 0))
        if role is not None:
            return role
    return None


def _channel_from_config(guild: discord.Guild, cfg: Any, cls: type, *attrs: str) -> Any:
    if cfg is None:
        return None
    for attr in attrs:
        channel = guild.get_channel(_safe_int(getattr(cfg, attr, 0), 0))
        if isinstance(channel, cls):
            return channel
    return None


def _role_by_name(guild: discord.Guild, name: str) -> Optional[discord.Role]:
    return _find_named(list(guild.roles), name)


def _category_by_name(guild: discord.Guild, name: str) -> Optional[discord.CategoryChannel]:
    return _find_named(list(guild.categories), name)


def _text_by_name(guild: discord.Guild, name: str) -> Optional[discord.TextChannel]:
    return _find_named(list(guild.text_channels), name)


def _voice_by_name(guild: discord.Guild, name: str) -> Optional[discord.VoiceChannel]:
    return _find_named(list(guild.voice_channels), name)


def _can_manage_roles(guild: discord.Guild) -> tuple[bool, str]:
    me = _bot_member(guild)
    if me is None:
        return False, "Bot member could not be resolved."
    if not me.guild_permissions.manage_roles:
        return False, "Bot is missing Manage Roles."
    return True, ""


def _can_manage_channels(guild: discord.Guild) -> tuple[bool, str]:
    me = _bot_member(guild)
    if me is None:
        return False, "Bot member could not be resolved."
    if not me.guild_permissions.manage_channels:
        return False, "Bot is missing Manage Channels."
    return True, ""


def _role_manageable_by_bot(guild: discord.Guild, role: discord.Role) -> bool:
    try:
        me = _bot_member(guild)
        return bool(me and me.guild_permissions.manage_roles and not role.managed and (role < me.top_role or guild.owner_id == me.id))
    except Exception:
        return False


def _public_overwrites(guild: discord.Guild, staff_role: Optional[discord.Role], control_role: Optional[discord.Role], unverified_role: Optional[discord.Role]) -> dict[Any, discord.PermissionOverwrite]:
    ow: dict[Any, discord.PermissionOverwrite] = {guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)}
    me = _bot_member(guild)
    if me:
        ow[me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, embed_links=True, attach_files=True, manage_messages=True)
    for role in (staff_role, control_role):
        if role and not role.is_default():
            ow[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, embed_links=True, attach_files=True, manage_messages=True)
    if unverified_role and not unverified_role.is_default():
        ow[unverified_role] = discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)
    return ow


def _staff_overwrites(guild: discord.Guild, staff_role: Optional[discord.Role], control_role: Optional[discord.Role]) -> dict[Any, discord.PermissionOverwrite]:
    ow: dict[Any, discord.PermissionOverwrite] = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    me = _bot_member(guild)
    if me:
        ow[me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, embed_links=True, attach_files=True, manage_channels=True, manage_messages=True, manage_threads=True, send_messages_in_threads=True)
    for role in (staff_role, control_role):
        if role and not role.is_default():
            ow[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, embed_links=True, attach_files=True, manage_messages=True, manage_threads=True, send_messages_in_threads=True)
    return ow


def _voice_overwrites(guild: discord.Guild, staff_role: Optional[discord.Role], control_role: Optional[discord.Role], unverified_role: Optional[discord.Role]) -> dict[Any, discord.PermissionOverwrite]:
    ow: dict[Any, discord.PermissionOverwrite] = {guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=True, speak=False)}
    me = _bot_member(guild)
    if me:
        ow[me] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, move_members=True, manage_channels=True)
    if unverified_role and not unverified_role.is_default():
        ow[unverified_role] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=False)
    for role in (staff_role, control_role):
        if role and not role.is_default():
            ow[role] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, move_members=True)
    return ow


async def _ensure_role(guild: discord.Guild, name: str, *, create_missing_roles: bool, notes: list[str], created: list[str], reused: list[str]) -> Optional[discord.Role]:
    role = _role_by_name(guild, name)
    if role:
        _unique(reused, f"Role: {role.mention}")
        return role
    if not create_missing_roles:
        notes.append(f"Role `{name}` was missing and role creation is disabled.")
        return None
    ok, reason = _can_manage_roles(guild)
    if not ok:
        notes.append(f"Could not create role `{name}`: {reason}")
        return None
    try:
        role = await guild.create_role(name=name, permissions=discord.Permissions.none(), hoist=False, mentionable=False, reason="Stoney auto-build missing recommended role")
        created.append(f"Role: {role.mention}")
        return role
    except Exception as e:
        notes.append(f"Could not create role `{name}`: {type(e).__name__}")
        return None


async def _ensure_category(guild: discord.Guild, name: str, *, overwrites: dict[Any, discord.PermissionOverwrite], notes: list[str], created: list[str], reused: list[str]) -> Optional[discord.CategoryChannel]:
    category = _category_by_name(guild, name)
    if category:
        _unique(reused, f"Category: `{category.name}`")
        notes.append(f"Reused existing category `{category.name}` without overwriting permissions.")
        return category
    ok, reason = _can_manage_channels(guild)
    if not ok:
        notes.append(f"Could not create category `{name}`: {reason}")
        return None
    try:
        category = await guild.create_category(name=name, overwrites=overwrites, reason="Stoney auto-build missing recommended category")
        created.append(f"Category: `{category.name}`")
        return category
    except Exception as e:
        notes.append(f"Could not create category `{name}`: {type(e).__name__}")
        return None


async def _ensure_text(guild: discord.Guild, name: str, *, category: Optional[discord.CategoryChannel], overwrites: dict[Any, discord.PermissionOverwrite], topic: str, notes: list[str], created: list[str], reused: list[str]) -> Optional[discord.TextChannel]:
    channel = _text_by_name(guild, name)
    if channel:
        _unique(reused, f"Channel: {channel.mention}")
        notes.append(f"Reused existing channel {channel.mention} without moving it or overwriting permissions.")
        return channel
    ok, reason = _can_manage_channels(guild)
    if not ok:
        notes.append(f"Could not create channel `#{name}`: {reason}")
        return None
    try:
        channel = await guild.create_text_channel(name=name, category=category, overwrites=overwrites, topic=topic[:1024] if topic else None, reason="Stoney auto-build missing recommended channel")
        created.append(f"Channel: {channel.mention}")
        return channel
    except Exception as e:
        notes.append(f"Could not create channel `#{name}`: {type(e).__name__}")
        return None


async def _ensure_voice(guild: discord.Guild, name: str, *, category: Optional[discord.CategoryChannel], overwrites: dict[Any, discord.PermissionOverwrite], notes: list[str], created: list[str], reused: list[str]) -> Optional[discord.VoiceChannel]:
    channel = _voice_by_name(guild, name)
    if channel:
        _unique(reused, f"Voice: {channel.mention}")
        notes.append(f"Reused existing voice channel {channel.mention} without moving it or overwriting permissions.")
        return channel
    ok, reason = _can_manage_channels(guild)
    if not ok:
        notes.append(f"Could not create voice channel `{name}`: {reason}")
        return None
    try:
        channel = await guild.create_voice_channel(name=name, category=category, overwrites=overwrites, reason="Stoney auto-build missing recommended voice channel")
        created.append(f"Voice: {channel.mention}")
        return channel
    except Exception as e:
        notes.append(f"Could not create voice channel `{name}`: {type(e).__name__}")
        return None


async def _resolve_existing_control_role(guild: discord.Guild) -> Optional[discord.Role]:
    try:
        from .public_access_control import configured_control_role_ids_for_guild
        for rid in sorted(configured_control_role_ids_for_guild(guild.id)):
            role = guild.get_role(int(rid))
            if role:
                return role
    except Exception:
        return None
    return None


async def _assign_control_role_to_runner(interaction: discord.Interaction, role: Optional[discord.Role], notes: list[str], ok: list[str]) -> None:
    if role is None or interaction.guild is None or not isinstance(interaction.user, discord.Member):
        return
    try:
        if role in interaction.user.roles:
            return
    except Exception:
        return
    if not _role_manageable_by_bot(interaction.guild, role):
        notes.append(f"Could not auto-assign {role.mention} to you because of bot role hierarchy.")
        return
    try:
        await interaction.user.add_roles(role, reason="Stoney auto-build bootstrap")
        ok.append(f"Assigned {role.mention} to you for future bot setup access.")
    except Exception as e:
        notes.append(f"Could not auto-assign {role.mention} to you: {type(e).__name__}")


async def _setup_defaults_callback(
    interaction: discord.Interaction,
    control_role: Optional[discord.Role] = None,
    staff_role: Optional[discord.Role] = None,
    create_missing_roles: bool = True,
    apply_channel_permissions: bool = True,
) -> None:
    if not await _require_setup_permission(interaction):
        return
    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)

    created: list[str] = []
    reused: list[str] = []
    notes: list[str] = []
    ok: list[str] = []

    try:
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception:
        cfg = None

    control_role = control_role or _role_from_config(guild, cfg, "server_control_role_id", "control_role_id", "perm_role_id") or await _resolve_existing_control_role(guild)
    if control_role:
        _unique(reused, f"Server-control role: {control_role.mention}")
    else:
        control_role = await _ensure_role(guild, DEFAULT_CONTROL_ROLE_NAME, create_missing_roles=create_missing_roles, notes=notes, created=created, reused=reused)

    staff_role = staff_role or _role_from_config(guild, cfg, "staff_role_id", "vc_staff_role_id")
    if staff_role:
        _unique(reused, f"Ticket staff role: {staff_role.mention}")
    else:
        staff_role = await _ensure_role(guild, DEFAULT_STAFF_ROLE_NAME, create_missing_roles=create_missing_roles, notes=notes, created=created, reused=reused)

    unverified_role = _role_from_config(guild, cfg, "unverified_role_id") or await _ensure_role(guild, DEFAULT_UNVERIFIED_ROLE_NAME, create_missing_roles=create_missing_roles, notes=notes, created=created, reused=reused)
    verified_role = _role_from_config(guild, cfg, "verified_role_id") or await _ensure_role(guild, DEFAULT_VERIFIED_ROLE_NAME, create_missing_roles=create_missing_roles, notes=notes, created=created, reused=reused)
    member_role = _role_from_config(guild, cfg, "resident_role_id", "member_role_id") or await _ensure_role(guild, DEFAULT_MEMBER_ROLE_NAME, create_missing_roles=create_missing_roles, notes=notes, created=created, reused=reused)

    for label, role in (("Pending / Unverified role", unverified_role), ("Verified role", verified_role), ("Member / Resident role", member_role)):
        if role:
            _unique(reused, f"{label}: {role.mention}")

    await _assign_control_role_to_runner(interaction, control_role, notes, ok)

    public_ow = _public_overwrites(guild, staff_role, control_role, unverified_role) if apply_channel_permissions else {}
    staff_ow = _staff_overwrites(guild, staff_role, control_role) if apply_channel_permissions else {}
    voice_ow = _voice_overwrites(guild, staff_role, control_role, unverified_role) if apply_channel_permissions else {}

    start_category = _channel_from_config(guild, cfg, discord.CategoryChannel, "start_category_id", "welcome_category_id") or await _ensure_category(guild, START_CATEGORY_NAME, overwrites=public_ow, notes=notes, created=created, reused=reused)
    ticket_category = _channel_from_config(guild, cfg, discord.CategoryChannel, "ticket_category_id") or await _ensure_category(guild, TICKET_CATEGORY_NAME, overwrites=staff_ow, notes=notes, created=created, reused=reused)
    archive_category = _channel_from_config(guild, cfg, discord.CategoryChannel, "ticket_archive_category_id") or await _ensure_category(guild, ARCHIVE_CATEGORY_NAME, overwrites=staff_ow, notes=notes, created=created, reused=reused)
    management_category = _channel_from_config(guild, cfg, discord.CategoryChannel, "management_category_id", "staff_tools_category_id") or await _ensure_category(guild, MANAGEMENT_CATEGORY_NAME, overwrites=staff_ow, notes=notes, created=created, reused=reused)

    for label, channel in (("Start category", start_category), ("Ticket category", ticket_category), ("Archive category", archive_category), ("Staff tools category", management_category)):
        if channel:
            _unique(reused, f"{label}: `{channel.name}`")

    welcome_channel = _channel_from_config(guild, cfg, discord.TextChannel, "welcome_channel_id") or await _ensure_text(guild, WELCOME_CHANNEL_NAME, category=start_category, overwrites=public_ow, topic="Welcome information for new members.", notes=notes, created=created, reused=reused)
    verify_channel = _channel_from_config(guild, cfg, discord.TextChannel, "verify_channel_id") or await _ensure_text(guild, VERIFY_CHANNEL_NAME, category=start_category, overwrites=public_ow, topic="Start server verification here.", notes=notes, created=created, reused=reused)
    ticket_panel_channel = _channel_from_config(guild, cfg, discord.TextChannel, "ticket_panel_channel_id", "support_channel_id") or await _ensure_text(guild, TICKET_PANEL_CHANNEL_NAME, category=start_category, overwrites=public_ow, topic="Open a private support ticket here.", notes=notes, created=created, reused=reused)
    vc_verify_channel = _channel_from_config(guild, cfg, discord.VoiceChannel, "vc_verify_channel_id") or await _ensure_voice(guild, VC_VERIFY_CHANNEL_NAME, category=start_category, overwrites=voice_ow, notes=notes, created=created, reused=reused)
    vc_queue_channel = _channel_from_config(guild, cfg, discord.TextChannel, "vc_verify_queue_channel_id") or await _ensure_text(guild, VC_QUEUE_CHANNEL_NAME, category=management_category, overwrites=staff_ow, topic="Staff queue and status channel for voice verification requests.", notes=notes, created=created, reused=reused)
    transcripts_channel = _channel_from_config(guild, cfg, discord.TextChannel, "transcripts_channel_id") or await _ensure_text(guild, TRANSCRIPTS_CHANNEL_NAME, category=management_category, overwrites=staff_ow, topic="Ticket transcripts are posted here.", notes=notes, created=created, reused=reused)
    modlog_channel = _channel_from_config(guild, cfg, discord.TextChannel, "modlog_channel_id", "raidlog_channel_id", "force_verify_log_channel_id") or await _ensure_text(guild, MODLOG_CHANNEL_NAME, category=management_category, overwrites=staff_ow, topic="Moderation, ticket, and security logs are posted here.", notes=notes, created=created, reused=reused)
    join_leave_channel = _channel_from_config(guild, cfg, discord.TextChannel, "join_log_channel_id") or await _ensure_text(guild, JOIN_LEAVE_CHANNEL_NAME, category=management_category, overwrites=staff_ow, topic="Join and leave events are posted here.", notes=notes, created=created, reused=reused)
    status_channel = _channel_from_config(guild, cfg, discord.TextChannel, "status_channel_id", "bot_status_channel_id") or await _ensure_text(guild, STATUS_CHANNEL_NAME, category=management_category, overwrites=staff_ow, topic="Bot status and restored-service notices are posted here.", notes=notes, created=created, reused=reused)

    required = [
        ("server-control role", control_role), ("ticket staff role", staff_role), ("pending/unverified role", unverified_role), ("verified role", verified_role),
        ("start category", start_category), ("ticket category", ticket_category), ("archive category", archive_category), ("management category", management_category),
        ("welcome channel", welcome_channel), ("verify channel", verify_channel), ("support/ticket panel channel", ticket_panel_channel),
        ("transcripts channel", transcripts_channel), ("modlog channel", modlog_channel),
    ]
    blockers = [f"Missing {label}." for label, value in required if value is None]
    if blockers:
        embed = discord.Embed(title="🚫 Recommended Layout Incomplete", description="Fix the blockers below, then run `/stoney setup` again.", color=discord.Color.red())
        embed.add_field(name="Blockers", value=_line_list(blockers), inline=False)
        if created:
            embed.add_field(name="Created Before Stopping", value=_line_list(created), inline=False)
        if reused:
            embed.add_field(name="Reused", value=_line_list(reused), inline=False)
        if notes:
            embed.add_field(name="Notes", value=_line_list(notes), inline=False)
        embed.add_field(name="What To Press Next", value="Press `/stoney setup` → **Choose Existing Items** if you already have custom roles/channels, or fix bot permissions and run Auto-Build again.", inline=False)
        return await interaction.followup.send(embed=embed, ephemeral=True)

    updates = {
        "__config_write_mode": "auto_create",
        "__config_write_source": "/stoney setup auto-build recommended layout",
        "server_control_role_id": _role_value(control_role), "control_role_id": _role_value(control_role), "perm_role_id": _role_value(control_role),
        "staff_role_id": _role_value(staff_role), "vc_staff_role_id": _role_value(staff_role),
        "unverified_role_id": _role_value(unverified_role), "verified_role_id": _role_value(verified_role), "resident_role_id": _role_value(member_role),
        "ticket_category_id": str(int(ticket_category.id)) if ticket_category else None,
        "ticket_archive_category_id": str(int(archive_category.id)) if archive_category else None,
        "transcripts_channel_id": str(int(transcripts_channel.id)) if transcripts_channel else None,
        "verify_channel_id": str(int(verify_channel.id)) if verify_channel else None,
        "ticket_panel_channel_id": str(int(ticket_panel_channel.id)) if ticket_panel_channel else None,
        "support_channel_id": str(int(ticket_panel_channel.id)) if ticket_panel_channel else None,
        "welcome_channel_id": str(int(welcome_channel.id)) if welcome_channel else None,
        "vc_verify_channel_id": str(int(vc_verify_channel.id)) if vc_verify_channel else None,
        "vc_verify_queue_channel_id": str(int(vc_queue_channel.id)) if vc_queue_channel else None,
        "modlog_channel_id": str(int(modlog_channel.id)) if modlog_channel else None,
        "raidlog_channel_id": str(int(modlog_channel.id)) if modlog_channel else None,
        "force_verify_log_channel_id": str(int(modlog_channel.id)) if modlog_channel else None,
        "join_log_channel_id": str(int(join_leave_channel.id)) if join_leave_channel else None,
        "status_channel_id": str(int(status_channel.id)) if status_channel else None,
        "bot_status_channel_id": str(int(status_channel.id)) if status_channel else None,
        "ticket_prefix": "ticket",
        "configured_by_id": str(interaction.user.id), "configured_by_name": str(interaction.user), "configured_at": _utc_iso(),
        "default_setup_version": "5_fill_only_idempotent_recommended_layout",
    }

    try:
        await _upsert_config(guild.id, updates)
        invalidate_guild_config(guild.id)
        cfg_after = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        return await interaction.followup.send(f"❌ Recommended layout handled Discord items but failed saving config: `{e}`\n\nPress `/stoney setup` → **Choose Existing Items** to save your roles/channels manually.", ephemeral=True)

    embed = _config_embed(guild, cfg_after, title="✅ Recommended Layout Handled")
    embed.add_field(name="Created", value=_line_list(created, empty="Nothing new created."), inline=False)
    embed.add_field(name="Reused", value=_line_list(reused, empty="Nothing reused."), inline=False)
    if ok:
        embed.add_field(name="Passing Checks", value=_line_list(ok), inline=False)
    if notes:
        embed.add_field(name="Notes", value=_line_list(notes), inline=False)
    embed.add_field(name="Auto-Build Safety", value="Auto-Build fills missing setup only. It does not replace owner-picked roles, channels, categories, or logs. Existing Discord items are reused without moving, renaming, deleting, or permission-overwriting them.", inline=False)
    embed.add_field(name="Next Step", value="Press `/stoney setup` → **Run Health Check**. If anything is wrong, press **Choose Existing Items** and pick the exact role/channel/category you want.", inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


def _attach() -> None:
    global _ATTACHED
    if _ATTACHED:
        return
    try:
        existing = stoney_group.get_command("setup-defaults")
    except Exception:
        existing = None
    if existing is not None:
        _ATTACHED = True
        return
    command = discord.app_commands.Command(
        name="setup-defaults",
        description="Create only missing recommended roles/channels/categories for a fresh server.",
        callback=_setup_defaults_callback,
    )
    try:
        command._params["control_role"].description = "Optional server-control role. Leave blank to reuse/create Bot Manager."
        command._params["staff_role"].description = "Optional ticket staff role. Leave blank to reuse/create Support Team."
        command._params["create_missing_roles"].description = "Create missing recommended roles only when no saved/custom role exists."
        command._params["apply_channel_permissions"].description = "Applies permissions only to newly created setup items. Existing items are reused safely."
    except Exception:
        pass
    stoney_group.add_command(command)
    _ATTACHED = True


_attach()


def register_public_setup_defaults_commands(bot, tree) -> None:
    _ = bot, tree
    _attach()
    try:
        print("✅ public_setup_defaults: attached fill-only /stoney setup-defaults command")
    except Exception:
        pass


__all__ = ["register_public_setup_defaults_commands", "_setup_defaults_callback"]
