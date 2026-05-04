from __future__ import annotations

"""
Interactive setup assistant for fresh public guilds.

Public UX goals:
- Do not scare owners with a wall of slash commands.
- Detect missing roles/channels/categories from the server's saved config.
- Offer a safe one-click repair that creates ONLY missing defaults.
- Offer a custom-name repair before creating missing items.
- Let owners choose existing items through the setup picker when they already
  have a custom server layout.
"""

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional

import discord

from .common import safe_defer
from .public_setup_group import (
    _build_setup_health,
    _field_text,
    _health_embed,
    _require_setup_permission,
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
SUPPORT_CHANNEL_NAME = "🎫・support"
VC_VERIFY_CHANNEL_NAME = "🎙️ Voice Verification"
VC_QUEUE_CHANNEL_NAME = "🎙️・vc-verify-queue"
TRANSCRIPTS_CHANNEL_NAME = "📑・transcripts"
MODLOG_CHANNEL_NAME = "🛡️・mod-log"
JOIN_LEAVE_CHANNEL_NAME = "🚪・join-leave-log"
STATUS_CHANNEL_NAME = "📡・bot-status"


@dataclass(frozen=True)
class RepairSpec:
    key: str
    label: str
    kind: str
    default_name: str
    config_keys: tuple[str, ...]
    category_group: str = ""
    required: bool = False


REPAIR_SPECS: tuple[RepairSpec, ...] = (
    RepairSpec("staff_role", "Ticket staff role", "role", DEFAULT_STAFF_ROLE_NAME, ("staff_role_id", "vc_staff_role_id"), required=True),
    RepairSpec("unverified_role", "Unverified role", "role", DEFAULT_UNVERIFIED_ROLE_NAME, ("unverified_role_id",)),
    RepairSpec("verified_role", "Verified role", "role", DEFAULT_VERIFIED_ROLE_NAME, ("verified_role_id",)),
    RepairSpec("resident_role", "Member role", "role", DEFAULT_MEMBER_ROLE_NAME, ("resident_role_id",)),
    RepairSpec("ticket_category", "Open ticket category", "category", TICKET_CATEGORY_NAME, ("ticket_category_id",), required=True),
    RepairSpec("archive_category", "Ticket archive category", "category", ARCHIVE_CATEGORY_NAME, ("ticket_archive_category_id",)),
    RepairSpec("verify_channel", "Verify channel", "text", VERIFY_CHANNEL_NAME, ("verify_channel_id",), category_group="start"),
    RepairSpec("support_channel", "Support panel channel", "text", SUPPORT_CHANNEL_NAME, ("ticket_panel_channel_id", "support_channel_id"), category_group="start"),
    RepairSpec("vc_verify_channel", "VC verification room", "voice", VC_VERIFY_CHANNEL_NAME, ("vc_verify_channel_id",), category_group="start"),
    RepairSpec("vc_queue_channel", "VC verify queue channel", "text", VC_QUEUE_CHANNEL_NAME, ("vc_verify_queue_channel_id",), category_group="management"),
    RepairSpec("transcripts_channel", "Transcript channel", "text", TRANSCRIPTS_CHANNEL_NAME, ("transcripts_channel_id",), category_group="management"),
    RepairSpec("modlog_channel", "Modlog channel", "text", MODLOG_CHANNEL_NAME, ("modlog_channel_id", "raidlog_channel_id", "force_verify_log_channel_id"), category_group="management"),
    RepairSpec("join_log_channel", "Join/leave log channel", "text", JOIN_LEAVE_CHANNEL_NAME, ("join_log_channel_id",), category_group="management"),
    RepairSpec("status_channel", "Bot status channel", "text", STATUS_CHANNEL_NAME, ("status_channel_id", "bot_status_channel_id"), category_group="management"),
)


def _short_lines(lines: list[str], *, limit: int = 900, empty: str = "✅ Nothing missing") -> str:
    if not lines:
        return empty
    out: list[str] = []
    total = 0
    for line in lines:
        text = str(line).strip()
        if not text:
            continue
        extra = len(text) + 1
        if total + extra > limit:
            out.append(f"…and {len(lines) - len(out)} more")
            break
        out.append(text)
        total += extra
    return "\n".join(out) or empty


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _casefold(value: Any) -> str:
    try:
        return str(value or "").strip().casefold()
    except Exception:
        return ""


def _clean_name(value: Any, default: str) -> str:
    try:
        text = str(value or "").strip()
        return (text or str(default))[:90]
    except Exception:
        return str(default)


def _table_name() -> str:
    try:
        return (os.getenv("STONEY_GUILD_CONFIG_TABLE") or "guild_configs").strip() or "guild_configs"
    except Exception:
        return "guild_configs"


def _nested_settings(row: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    if not row:
        return {}
    merged: dict[str, Any] = {}
    try:
        for key in ("settings", "config", "metadata", "meta"):
            value = row.get(key)
            if isinstance(value, Mapping):
                merged.update(dict(value))
        merged.update(dict(row))
    except Exception:
        try:
            merged.update(dict(row))
        except Exception:
            pass
    return merged


def _fetch_config_row_sync(guild_id: int) -> Optional[dict[str, Any]]:
    try:
        from ..globals import get_supabase

        sb = get_supabase()
        if sb is None:
            return None
        res = (
            sb.table(_table_name())
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if not rows:
            return None
        row = rows[0]
        return dict(row) if isinstance(row, Mapping) else None
    except Exception:
        return None


async def _fetch_config_row(guild_id: int) -> Optional[dict[str, Any]]:
    return await asyncio.to_thread(_fetch_config_row_sync, int(guild_id))


def _current_id(spec: RepairSpec, cfg: Any, row: Optional[Mapping[str, Any]]) -> int:
    data = _nested_settings(row)
    for key in spec.config_keys:
        value = _safe_int(data.get(key), 0)
        if value > 0:
            return value
    for key in spec.config_keys:
        value = _safe_int(getattr(cfg, key, 0), 0)
        if value > 0:
            return value
    return 0


def _object_for_spec(guild: discord.Guild, spec: RepairSpec, object_id: int) -> Optional[Any]:
    if object_id <= 0:
        return None
    if spec.kind == "role":
        return guild.get_role(object_id)
    channel = guild.get_channel(object_id)
    if spec.kind == "category" and isinstance(channel, discord.CategoryChannel):
        return channel
    if spec.kind == "text" and isinstance(channel, discord.TextChannel):
        return channel
    if spec.kind == "voice" and isinstance(channel, discord.VoiceChannel):
        return channel
    stage_type = getattr(discord, "StageChannel", None)
    if spec.kind == "voice" and stage_type is not None and isinstance(channel, stage_type):
        return channel
    return None


def _role_by_name(guild: discord.Guild, name: str) -> Optional[discord.Role]:
    target = _casefold(name)
    try:
        return next((role for role in guild.roles if _casefold(role.name) == target), None)
    except Exception:
        return None


def _category_by_name(guild: discord.Guild, name: str) -> Optional[discord.CategoryChannel]:
    target = _casefold(name)
    try:
        return next((ch for ch in guild.categories if _casefold(ch.name) == target), None)
    except Exception:
        return None


def _text_channel_by_name(guild: discord.Guild, name: str) -> Optional[discord.TextChannel]:
    target = _casefold(name)
    try:
        return next((ch for ch in guild.text_channels if _casefold(ch.name) == target), None)
    except Exception:
        return None


def _voice_channel_by_name(guild: discord.Guild, name: str) -> Optional[discord.VoiceChannel]:
    target = _casefold(name)
    try:
        return next((ch for ch in guild.voice_channels if _casefold(ch.name) == target), None)
    except Exception:
        return None


def _control_role_id_from_row(row: Optional[Mapping[str, Any]]) -> int:
    data = _nested_settings(row)
    for key in ("server_control_role_id", "control_role_id", "perm_role_id", "admin_role_id"):
        value = _safe_int(data.get(key), 0)
        if value > 0:
            return value
    return 0


def _missing_repair_specs(guild: discord.Guild, cfg: Any, row: Optional[Mapping[str, Any]]) -> list[RepairSpec]:
    missing: list[RepairSpec] = []
    for spec in REPAIR_SPECS:
        object_id = _current_id(spec, cfg, row)
        if _object_for_spec(guild, spec, object_id) is None:
            missing.append(spec)
    return missing


def _status_channel_perms_missing(guild: discord.Guild, channel: discord.TextChannel) -> list[str]:
    missing: list[str] = []
    me = guild.me
    if me is None:
        return missing
    perms = channel.permissions_for(me)
    if not perms.view_channel:
        missing.append("View Channel")
    if not perms.send_messages:
        missing.append("Send Messages")
    if not perms.embed_links:
        missing.append("Embed Links")
    if not perms.read_message_history:
        missing.append("Read Message History")
    return missing


def _private_overwrites(guild: discord.Guild, *, staff_role: Optional[discord.Role], control_role: Optional[discord.Role]) -> dict[Any, discord.PermissionOverwrite]:
    overwrites: dict[Any, discord.PermissionOverwrite] = {guild.default_role: discord.PermissionOverwrite(view_channel=False)}
    me = guild.me
    if me is not None:
        overwrites[me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            embed_links=True,
            attach_files=True,
            manage_channels=True,
            manage_messages=True,
            manage_threads=True,
            create_public_threads=True,
            create_private_threads=True,
            send_messages_in_threads=True,
        )
    for role in (staff_role, control_role):
        if role is not None and not role.is_default():
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                embed_links=True,
                attach_files=True,
                manage_messages=True,
                manage_threads=True,
                send_messages_in_threads=True,
            )
    return overwrites


def _public_overwrites(guild: discord.Guild, *, staff_role: Optional[discord.Role], control_role: Optional[discord.Role], unverified_role: Optional[discord.Role]) -> dict[Any, discord.PermissionOverwrite]:
    overwrites: dict[Any, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)
    }
    me = guild.me
    if me is not None:
        overwrites[me] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, embed_links=True, attach_files=True, manage_messages=True)
    if unverified_role is not None and not unverified_role.is_default():
        overwrites[unverified_role] = discord.PermissionOverwrite(view_channel=True, send_messages=False, read_message_history=True)
    for role in (staff_role, control_role):
        if role is not None and not role.is_default():
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, embed_links=True, attach_files=True, manage_messages=True)
    return overwrites


def _voice_overwrites(guild: discord.Guild, *, staff_role: Optional[discord.Role], control_role: Optional[discord.Role], unverified_role: Optional[discord.Role]) -> dict[Any, discord.PermissionOverwrite]:
    overwrites: dict[Any, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=True, connect=True, speak=False)
    }
    me = guild.me
    if me is not None:
        overwrites[me] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, move_members=True, manage_channels=True)
    if unverified_role is not None and not unverified_role.is_default():
        overwrites[unverified_role] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=False)
    for role in (staff_role, control_role):
        if role is not None and not role.is_default():
            overwrites[role] = discord.PermissionOverwrite(view_channel=True, connect=True, speak=True, move_members=True)
    return overwrites


async def _ensure_role(guild: discord.Guild, name: str, *, created: list[str], reused: list[str], notes: list[str]) -> Optional[discord.Role]:
    name = _clean_name(name, DEFAULT_STAFF_ROLE_NAME)
    existing = _role_by_name(guild, name)
    if existing is not None:
        reused.append(f"Role: {existing.mention}")
        return existing
    me = guild.me
    if me is None or not me.guild_permissions.manage_roles:
        notes.append(f"Could not create role `{name}` because the bot is missing Manage Roles.")
        return None
    try:
        role = await guild.create_role(name=name, permissions=discord.Permissions.none(), hoist=False, mentionable=False, reason="Stoney setup assistant missing-item repair")
        created.append(f"Role: {role.mention}")
        return role
    except Exception as e:
        notes.append(f"Could not create role `{name}`: {type(e).__name__}")
        return None


async def _ensure_category(guild: discord.Guild, name: str, *, overwrites: Optional[dict[Any, discord.PermissionOverwrite]], created: list[str], reused: list[str], notes: list[str]) -> Optional[discord.CategoryChannel]:
    name = _clean_name(name, MANAGEMENT_CATEGORY_NAME)
    existing = _category_by_name(guild, name)
    if existing is not None:
        reused.append(f"Category: `{existing.name}`")
        return existing
    me = guild.me
    if me is None or not me.guild_permissions.manage_channels:
        notes.append(f"Could not create category `{name}` because the bot is missing Manage Channels.")
        return None
    try:
        category = await guild.create_category(name=name, overwrites=overwrites, reason="Stoney setup assistant missing-item repair")
        created.append(f"Category: `{category.name}`")
        return category
    except Exception as e:
        notes.append(f"Could not create category `{name}`: {type(e).__name__}")
        return None


async def _ensure_text_channel(guild: discord.Guild, name: str, *, category: Optional[discord.CategoryChannel], overwrites: Optional[dict[Any, discord.PermissionOverwrite]], topic: str, created: list[str], reused: list[str], notes: list[str]) -> Optional[discord.TextChannel]:
    name = _clean_name(name, STATUS_CHANNEL_NAME)
    existing = _text_channel_by_name(guild, name)
    if existing is not None:
        reused.append(f"Channel: {existing.mention}")
        try:
            kwargs: dict[str, Any] = {"reason": "Stoney setup assistant missing-item repair refresh"}
            if category is not None and existing.category_id != category.id:
                kwargs["category"] = category
            if overwrites is not None:
                kwargs["overwrites"] = overwrites
            if topic:
                kwargs["topic"] = topic[:1024]
            await existing.edit(**kwargs)
        except Exception as e:
            notes.append(f"Reused {existing.mention}, but could not refresh permissions/topic: {type(e).__name__}")
        return existing
    me = guild.me
    if me is None or not me.guild_permissions.manage_channels:
        notes.append(f"Could not create `#{name}` because the bot is missing Manage Channels.")
        return None
    try:
        channel = await guild.create_text_channel(name=name, category=category, overwrites=overwrites, topic=topic[:1024] if topic else None, reason="Stoney setup assistant missing-item repair")
        created.append(f"Channel: {channel.mention}")
        return channel
    except Exception as e:
        notes.append(f"Could not create `#{name}`: {type(e).__name__}")
        return None


async def _ensure_voice_channel(guild: discord.Guild, name: str, *, category: Optional[discord.CategoryChannel], overwrites: Optional[dict[Any, discord.PermissionOverwrite]], created: list[str], reused: list[str], notes: list[str]) -> Optional[discord.VoiceChannel]:
    name = _clean_name(name, VC_VERIFY_CHANNEL_NAME)
    existing = _voice_channel_by_name(guild, name)
    if existing is not None:
        reused.append(f"Voice: {existing.mention}")
        try:
            kwargs: dict[str, Any] = {"reason": "Stoney setup assistant missing-item repair refresh"}
            if category is not None and existing.category_id != category.id:
                kwargs["category"] = category
            if overwrites is not None:
                kwargs["overwrites"] = overwrites
            await existing.edit(**kwargs)
        except Exception as e:
            notes.append(f"Reused {existing.mention}, but could not refresh permissions: {type(e).__name__}")
        return existing
    me = guild.me
    if me is None or not me.guild_permissions.manage_channels:
        notes.append(f"Could not create voice channel `{name}` because the bot is missing Manage Channels.")
        return None
    try:
        channel = await guild.create_voice_channel(name=name, category=category, overwrites=overwrites, reason="Stoney setup assistant missing-item repair")
        created.append(f"Voice: {channel.mention}")
        return channel
    except Exception as e:
        notes.append(f"Could not create voice channel `{name}`: {type(e).__name__}")
        return None


def _updates_for_spec(spec: RepairSpec, obj: Any) -> dict[str, str]:
    obj_id = str(int(obj.id))
    if spec.key == "staff_role":
        return {"staff_role_id": obj_id, "vc_staff_role_id": obj_id}
    if spec.key == "modlog_channel":
        return {"modlog_channel_id": obj_id, "raidlog_channel_id": obj_id, "force_verify_log_channel_id": obj_id}
    return {key: obj_id for key in spec.config_keys}


def _topic_for_spec(spec: RepairSpec) -> str:
    return {
        "verify_channel": "Start server verification here.",
        "support_channel": "Open a private support ticket here.",
        "vc_queue_channel": "Staff queue and status channel for voice verification requests.",
        "transcripts_channel": "Ticket transcripts are posted here.",
        "modlog_channel": "Moderation, ticket, and security logs are posted here.",
        "join_log_channel": "Join and leave events are posted here.",
        "status_channel": "Bot status and restored-service notices are posted here.",
    }.get(spec.key, "Created by Dank Shield setup assistant.")


async def _repair_specs(interaction: discord.Interaction, specs: list[RepairSpec], *, custom_names: Optional[dict[str, str]] = None) -> None:
    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This must be used inside a server.", ephemeral=True)

    cfg = await get_guild_config(guild.id, refresh=True)
    row = await _fetch_config_row(guild.id)
    missing_now = {spec.key for spec in _missing_repair_specs(guild, cfg, row)}
    specs = [spec for spec in specs if spec.key in missing_now]

    if not specs:
        embed, view = await _build_assistant_payload(guild)
        return await interaction.followup.send("✅ Nothing missing anymore.", embed=embed, view=view, ephemeral=True)

    custom_names = custom_names or {}
    created: list[str] = []
    reused: list[str] = []
    notes: list[str] = []
    updates: dict[str, Any] = {}

    control_role_id = _control_role_id_from_row(row)
    control_role = guild.get_role(control_role_id) if control_role_id > 0 else None
    staff_role_id = _safe_int(getattr(cfg, "staff_role_id", 0), 0)
    staff_role = guild.get_role(staff_role_id) if staff_role_id > 0 else None
    unverified_role_id = _safe_int(getattr(cfg, "unverified_role_id", 0), 0)
    unverified_role = guild.get_role(unverified_role_id) if unverified_role_id > 0 else None

    for spec in [s for s in specs if s.kind == "role"]:
        role = await _ensure_role(guild, custom_names.get(spec.key, spec.default_name), created=created, reused=reused, notes=notes)
        if role is None:
            continue
        updates.update(_updates_for_spec(spec, role))
        if spec.key == "staff_role":
            staff_role = role
        elif spec.key == "unverified_role":
            unverified_role = role

    staff_overwrites = _private_overwrites(guild, staff_role=staff_role, control_role=control_role)
    public_overwrites = _public_overwrites(guild, staff_role=staff_role, control_role=control_role, unverified_role=unverified_role)
    voice_overwrites = _voice_overwrites(guild, staff_role=staff_role, control_role=control_role, unverified_role=unverified_role)

    for spec in [s for s in specs if s.kind == "category"]:
        category = await _ensure_category(guild, custom_names.get(spec.key, spec.default_name), overwrites=staff_overwrites, created=created, reused=reused, notes=notes)
        if category is not None:
            updates.update(_updates_for_spec(spec, category))

    start_category = _category_by_name(guild, START_CATEGORY_NAME)
    management_category = _category_by_name(guild, MANAGEMENT_CATEGORY_NAME)

    if any(s.category_group == "start" for s in specs):
        start_category = await _ensure_category(guild, START_CATEGORY_NAME, overwrites=public_overwrites, created=created, reused=reused, notes=notes)
    if any(s.category_group == "management" for s in specs):
        modlog_id = _safe_int(getattr(cfg, "modlog_channel_id", 0), 0)
        modlog_channel = guild.get_channel(modlog_id) if modlog_id > 0 else None
        if isinstance(modlog_channel, discord.TextChannel) and modlog_channel.category is not None:
            management_category = modlog_channel.category
        if management_category is None:
            management_category = await _ensure_category(guild, MANAGEMENT_CATEGORY_NAME, overwrites=staff_overwrites, created=created, reused=reused, notes=notes)

    for spec in [s for s in specs if s.kind in {"text", "voice"}]:
        name = custom_names.get(spec.key, spec.default_name)
        category = start_category if spec.category_group == "start" else management_category
        overwrites = voice_overwrites if spec.kind == "voice" and spec.category_group == "start" else public_overwrites if spec.category_group == "start" else staff_overwrites
        if spec.kind == "text":
            obj = await _ensure_text_channel(guild, name, category=category, overwrites=overwrites, topic=_topic_for_spec(spec), created=created, reused=reused, notes=notes)
        else:
            obj = await _ensure_voice_channel(guild, name, category=category, overwrites=overwrites, created=created, reused=reused, notes=notes)
        if obj is not None:
            updates.update(_updates_for_spec(spec, obj))

    if not updates:
        embed = discord.Embed(title="🚫 Could Not Repair Missing Items", description=_short_lines(notes, empty="No items could be created or reused."), color=discord.Color.red())
        return await interaction.followup.send(embed=embed, ephemeral=True)

    updates.update({"ticket_prefix": "ticket", "configured_by_id": str(interaction.user.id), "configured_by_name": str(interaction.user), "configured_at": _utc_iso(), "setup_assistant_last_repair": _utc_iso()})

    try:
        await _upsert_config(guild.id, updates)
        invalidate_guild_config(guild.id)
    except Exception as e:
        return await interaction.followup.send(f"❌ Items were created/reused but saving config failed: `{e}`", ephemeral=True)

    embed, view = await _build_assistant_payload(guild)
    summary = discord.Embed(title="✅ Missing Items Repaired", description="I only created/reused the missing setup pieces. Existing custom setup was left alone.", color=discord.Color.green())
    summary.add_field(name="Created", value=_short_lines(created, empty="Nothing new created."), inline=False)
    summary.add_field(name="Reused", value=_short_lines(reused, empty="Nothing reused."), inline=False)
    if notes:
        summary.add_field(name="Notes", value=_short_lines(notes, empty="None"), inline=False)
    await interaction.followup.send(embed=summary, ephemeral=True)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


def _custom_setup_summary() -> str:
    return "You do **not** need to memorize a pile of commands.\n\n**Best custom path:** run `/stoney setup-picker` and choose your existing channels/roles from dropdowns.\n\nUse manual setup commands only if a dropdown is annoying or Discord does not show what you need."


async def _build_assistant_payload(guild: discord.Guild) -> tuple[discord.Embed, "SetupAssistantView"]:
    cfg = await get_guild_config(guild.id, refresh=True)
    row = await _fetch_config_row(guild.id)
    blockers, warnings, ok = _build_setup_health(guild, cfg)
    missing_specs = _missing_repair_specs(guild, cfg, row)

    missing_lines = [f"• **{spec.label}** → default `{spec.default_name}`" for spec in missing_specs]
    ready_core = not blockers
    ready_full = ready_core and not warnings and not missing_specs

    if ready_full:
        description = "✅ **Everything important looks ready.** You can re-check anytime."
        color = discord.Color.green()
    elif missing_specs:
        description = "I found setup pieces that are missing or point to deleted items. You can auto-create clean defaults, customize names first, or choose existing items."
        color = discord.Color.gold() if ready_core else discord.Color.blurple()
    elif ready_core:
        description = "✅ **Core setup is ready**, but there are warnings worth reviewing."
        color = discord.Color.gold()
    else:
        description = "I found setup blockers. Use the repair buttons below or choose your existing channels/roles."
        color = discord.Color.blurple()

    embed = discord.Embed(title="🧭 Stoney Setup Assistant", description=description, color=color)
    embed.add_field(name="Missing Items I Can Create", value=_short_lines(missing_lines, empty="✅ None"), inline=False)
    embed.add_field(name="Health Blockers", value=_short_lines(blockers, empty="✅ None"), inline=False)
    embed.add_field(name="Warnings / Optional Fixes", value=_short_lines(warnings, empty="✅ None"), inline=False)
    if ok:
        embed.add_field(name="Already Working", value=_field_text(ok[:8], empty="Nothing checked yet."), inline=False)

    if missing_specs:
        recommended = "Press **Auto-Fix Missing Defaults** to create only the missing default items.\nPress **Customize Missing Names** if you want to name them before they are created."
    elif not ready_core:
        recommended = "Use **Choose Existing Items** if you already made the channels/roles, or auto-fix if the missing items are listed above."
    else:
        recommended = "No required action. Use **Run Health Check** after changing roles/channels."
    embed.add_field(name="Recommended Next Step", value=recommended, inline=False)
    embed.set_footer(text=f"Guild {guild.id} • setup assistant")
    return embed, SetupAssistantView(has_missing=bool(missing_specs))


class CustomMissingNamesModal(discord.ui.Modal):
    def __init__(self, specs: list[RepairSpec]) -> None:
        super().__init__(title="Customize Missing Names")
        self.specs = specs[:5]
        self.inputs: dict[str, discord.ui.TextInput] = {}
        for spec in self.specs:
            field = discord.ui.TextInput(label=spec.label[:45], default=spec.default_name[:90], placeholder=spec.default_name[:90], required=True, max_length=90)
            self.inputs[spec.key] = field
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _require_setup_permission(interaction):
            return
        await safe_defer(interaction, ephemeral=True)
        custom_names = {key: str(field.value or "").strip() for key, field in self.inputs.items()}
        await _repair_specs(interaction, self.specs, custom_names=custom_names)


class SetupAssistantView(discord.ui.View):
    def __init__(self, *, has_missing: bool = False) -> None:
        super().__init__(timeout=300)
        self.has_missing = bool(has_missing)
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                if child.custom_id and any(part in child.custom_id for part in ("auto_fix", "custom_names")) and not self.has_missing:
                    child.disabled = True

    async def _require(self, interaction: discord.Interaction) -> bool:
        return await _require_setup_permission(interaction)

    async def _current_missing_specs(self, interaction: discord.Interaction) -> list[RepairSpec]:
        guild = interaction.guild
        if guild is None:
            return []
        cfg = await get_guild_config(guild.id, refresh=True)
        row = await _fetch_config_row(guild.id)
        return _missing_repair_specs(guild, cfg, row)

    @discord.ui.button(label="Auto-Fix Missing Defaults", emoji="✨", style=discord.ButtonStyle.success, custom_id="stoney_setup_assistant:auto_fix", row=0)
    async def auto_fix(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._require(interaction):
            return
        await safe_defer(interaction, ephemeral=True)
        specs = await self._current_missing_specs(interaction)
        await _repair_specs(interaction, specs)

    @discord.ui.button(label="Customize Missing Names", emoji="✏️", style=discord.ButtonStyle.primary, custom_id="stoney_setup_assistant:custom_names", row=0)
    async def custom_names(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._require(interaction):
            return
        specs = await self._current_missing_specs(interaction)
        if not specs:
            return await interaction.response.send_message("✅ Nothing missing right now.", ephemeral=True)
        await interaction.response.send_modal(CustomMissingNamesModal(specs))

    @discord.ui.button(label="Choose Existing Items", emoji="🧩", style=discord.ButtonStyle.secondary, custom_id="stoney_setup_assistant:choose_existing", row=1)
    async def choose_existing(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._require(interaction):
            return
        embed = discord.Embed(title="🧩 Choose Existing Items", description="Use this when the server already has channels/roles and you do **not** want the bot to create new ones.\n\nStart with `/stoney setup-picker`. It uses dropdowns so you can pick existing roles, categories, and channels.", color=discord.Color.blurple())
        embed.add_field(name="Specific manual setup, only when needed", value="`/stoney setup-access` → control/staff roles\n`/stoney setup-tickets` → ticket categories/staff/transcripts\n`/stoney setup-verify` → verify channel/roles/VC verify\n`/stoney setup-logs` → logs\n`/stoney setup-status` → bot status channel", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="Use This Channel for Status", emoji="📌", style=discord.ButtonStyle.secondary, custom_id="stoney_setup_assistant:use_current_status", row=1)
    async def use_current_status(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._require(interaction):
            return
        await safe_defer(interaction, ephemeral=True)
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            return await interaction.followup.send("❌ Use this button inside the text channel you want as the bot-status channel.", ephemeral=True)
        missing = _status_channel_perms_missing(guild, channel)
        if missing:
            return await interaction.followup.send(f"🚫 I cannot use {channel.mention} for status yet. Missing: {', '.join(missing)}.", ephemeral=True)
        try:
            await _upsert_config(guild.id, {"status_channel_id": str(int(channel.id)), "configured_by_id": str(interaction.user.id), "configured_by_name": str(interaction.user), "configured_at": _utc_iso()})
            invalidate_guild_config(guild.id)
        except Exception as e:
            return await interaction.followup.send(f"❌ Failed saving bot status channel: `{e}`", ephemeral=True)
        embed, view = await _build_assistant_payload(guild)
        await interaction.followup.send(f"✅ Bot status reports will use {channel.mention}.", embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="Run Health Check", emoji="🩺", style=discord.ButtonStyle.secondary, custom_id="stoney_setup_assistant:run_health", row=1)
    async def run_health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await self._require(interaction):
            return
        await safe_defer(interaction, ephemeral=True)
        guild = interaction.guild
        if guild is None:
            return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)
        cfg = await get_guild_config(guild.id, refresh=True)
        health = _health_embed(guild, cfg)
        embed, view = await _build_assistant_payload(guild)
        health.add_field(name="Assistant Notes", value="Use the assistant buttons below to create defaults, customize missing names, or choose existing items.", inline=False)
        await interaction.followup.send(embed=health, view=view, ephemeral=True)


async def _setup_assistant_callback(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    await safe_defer(interaction, ephemeral=True)
    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)
    embed, view = await _build_assistant_payload(guild)
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


def _attach() -> None:
    global _ATTACHED
    if _ATTACHED:
        return
    try:
        existing = stoney_group.get_command("setup-assistant")
    except Exception:
        existing = None
    if existing is not None:
        _ATTACHED = True
        return
    command = discord.app_commands.Command(name="setup-assistant", description="Show missing setup pieces and choose automatic, custom, or existing setup.", callback=_setup_assistant_callback)
    stoney_group.add_command(command)
    _ATTACHED = True


_attach()


def register_public_setup_assistant_commands(bot: Any, tree: Any) -> None:
    _ = bot, tree
    _attach()
    try:
        print("✅ public_setup_assistant: attached /stoney setup-assistant command")
    except Exception:
        pass


__all__ = ["register_public_setup_assistant_commands"]
