from __future__ import annotations

"""
One-click default server setup for fresh public guilds.

This command is intentionally conservative:
- It only runs for users allowed to perform setup.
- It creates/reuses sane default roles, categories, channels, and a VC verify room.
- It saves everything into the per-guild guild_configs row.
- It does not use or copy any private Stoney server role/channel IDs.

The goal is a TicketTool-style onboarding button for server owners who just made
an empty server and do not know what channels/roles the bot needs yet.
"""

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

DEFAULT_CONTROL_ROLE_NAME = "Stoney Control"
DEFAULT_STAFF_ROLE_NAME = "Stoney Staff"
DEFAULT_UNVERIFIED_ROLE_NAME = "Unverified"
DEFAULT_VERIFIED_ROLE_NAME = "Verified"
DEFAULT_RESIDENT_ROLE_NAME = "Resident"

VERIFY_CATEGORY_NAME = "📌 START HERE / VERIFY"
TOOLS_CATEGORY_NAME = "🛠 SUPPORT TOOLS"
TICKET_CATEGORY_NAME = "🎫 TICKETS"
ARCHIVE_CATEGORY_NAME = "📦 Archived Tickets"

VERIFY_CHANNEL_NAME = "verify-here"
VC_QUEUE_CHANNEL_NAME = "vc-verify-requests"
TRANSCRIPTS_CHANNEL_NAME = "transcripts"
MODLOG_CHANNEL_NAME = "mod-log"
WELCOME_EXIT_CHANNEL_NAME = "welcome-exit"
STATUS_CHANNEL_NAME = "bot-status"
VC_VERIFY_CHANNEL_NAME = "VC Verify"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _role_by_name(guild: discord.Guild, name: str) -> Optional[discord.Role]:
    target = str(name or "").strip().casefold()
    if not target:
        return None
    try:
        for role in guild.roles:
            if str(role.name or "").strip().casefold() == target:
                return role
    except Exception:
        pass
    return None


def _text_channel_by_name(guild: discord.Guild, name: str) -> Optional[discord.TextChannel]:
    target = str(name or "").strip().casefold()
    if not target:
        return None
    try:
        for channel in guild.text_channels:
            if str(channel.name or "").strip().casefold() == target:
                return channel
    except Exception:
        pass
    return None


def _voice_channel_by_name(guild: discord.Guild, name: str) -> Optional[discord.VoiceChannel]:
    target = str(name or "").strip().casefold()
    if not target:
        return None
    try:
        for channel in guild.voice_channels:
            if str(channel.name or "").strip().casefold() == target:
                return channel
    except Exception:
        pass
    return None


def _category_by_name(guild: discord.Guild, name: str) -> Optional[discord.CategoryChannel]:
    target = str(name or "").strip().casefold()
    if not target:
        return None
    try:
        for category in guild.categories:
            if str(category.name or "").strip().casefold() == target:
                return category
    except Exception:
        pass
    return None


def _can_manage_roles(guild: discord.Guild) -> tuple[bool, str]:
    try:
        me = guild.me
        if me is None:
            return False, "Bot member could not be resolved."
        if not me.guild_permissions.manage_roles:
            return False, "Bot is missing Manage Roles."
        return True, ""
    except Exception as e:
        return False, f"Role permission check failed: {type(e).__name__}."


def _can_manage_channels(guild: discord.Guild) -> tuple[bool, str]:
    try:
        me = guild.me
        if me is None:
            return False, "Bot member could not be resolved."
        if not me.guild_permissions.manage_channels:
            return False, "Bot is missing Manage Channels."
        return True, ""
    except Exception as e:
        return False, f"Channel permission check failed: {type(e).__name__}."


def _role_manageable_by_bot(guild: discord.Guild, role: discord.Role) -> bool:
    try:
        me = guild.me
        if me is None:
            return False
        if getattr(role, "managed", False):
            return False
        if me.guild_permissions.administrator:
            return bool(role < me.top_role or guild.owner_id == me.id)
        return bool(me.guild_permissions.manage_roles and (role < me.top_role or guild.owner_id == me.id))
    except Exception:
        return False


async def _ensure_role(
    guild: discord.Guild,
    name: str,
    *,
    create_missing_roles: bool,
    notes: list[str],
    created: list[str],
    reused: list[str],
) -> Optional[discord.Role]:
    role = _role_by_name(guild, name)
    if role is not None:
        reused.append(f"Role: {role.mention}")
        return role

    if not create_missing_roles:
        notes.append(f"Role `{name}` was not found and role creation is disabled.")
        return None

    ok, reason = _can_manage_roles(guild)
    if not ok:
        notes.append(f"Could not create role `{name}`: {reason}")
        return None

    try:
        role = await guild.create_role(
            name=name,
            permissions=discord.Permissions.none(),
            hoist=False,
            mentionable=False,
            reason="Stoney Verify default setup",
        )
        created.append(f"Role: {role.mention}")
        return role
    except Exception as e:
        notes.append(f"Could not create role `{name}`: {type(e).__name__}")
        return None


def _private_overwrites(
    guild: discord.Guild,
    *,
    staff_role: Optional[discord.Role],
    control_role: Optional[discord.Role],
) -> dict[Any, discord.PermissionOverwrite]:
    overwrites: dict[Any, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
    }

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


def _verify_overwrites(
    guild: discord.Guild,
    *,
    staff_role: Optional[discord.Role],
    control_role: Optional[discord.Role],
    unverified_role: Optional[discord.Role],
) -> dict[Any, discord.PermissionOverwrite]:
    overwrites: dict[Any, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            read_message_history=True,
        )
    }

    me = guild.me
    if me is not None:
        overwrites[me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            embed_links=True,
            attach_files=True,
            manage_messages=True,
        )

    if unverified_role is not None and not unverified_role.is_default():
        overwrites[unverified_role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            read_message_history=True,
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
            )

    return overwrites


def _vc_overwrites(
    guild: discord.Guild,
    *,
    staff_role: Optional[discord.Role],
    control_role: Optional[discord.Role],
    unverified_role: Optional[discord.Role],
) -> dict[Any, discord.PermissionOverwrite]:
    overwrites: dict[Any, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=False,
        )
    }

    me = guild.me
    if me is not None:
        overwrites[me] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=True,
            move_members=True,
            manage_channels=True,
        )

    if unverified_role is not None and not unverified_role.is_default():
        overwrites[unverified_role] = discord.PermissionOverwrite(
            view_channel=True,
            connect=True,
            speak=False,
        )

    for role in (staff_role, control_role):
        if role is not None and not role.is_default():
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                connect=True,
                speak=True,
                move_members=True,
            )

    return overwrites


async def _ensure_category(
    guild: discord.Guild,
    name: str,
    *,
    overwrites: Optional[dict[Any, discord.PermissionOverwrite]],
    apply_channel_permissions: bool,
    notes: list[str],
    created: list[str],
    reused: list[str],
) -> Optional[discord.CategoryChannel]:
    category = _category_by_name(guild, name)
    if category is not None:
        reused.append(f"Category: `{category.name}`")
        if apply_channel_permissions and overwrites is not None:
            try:
                await category.edit(overwrites=overwrites, reason="Stoney Verify default setup permission refresh")
            except Exception as e:
                notes.append(f"Could not refresh permissions for category `{name}`: {type(e).__name__}")
        return category

    ok, reason = _can_manage_channels(guild)
    if not ok:
        notes.append(f"Could not create category `{name}`: {reason}")
        return None

    try:
        category = await guild.create_category(
            name=name,
            overwrites=overwrites,
            reason="Stoney Verify default setup",
        )
        created.append(f"Category: `{category.name}`")
        return category
    except Exception as e:
        notes.append(f"Could not create category `{name}`: {type(e).__name__}")
        return None


async def _ensure_text_channel(
    guild: discord.Guild,
    name: str,
    *,
    category: Optional[discord.CategoryChannel],
    overwrites: Optional[dict[Any, discord.PermissionOverwrite]],
    topic: str,
    apply_channel_permissions: bool,
    notes: list[str],
    created: list[str],
    reused: list[str],
) -> Optional[discord.TextChannel]:
    channel = _text_channel_by_name(guild, name)
    if channel is not None:
        reused.append(f"Channel: {channel.mention}")
        if apply_channel_permissions:
            try:
                kwargs: dict[str, Any] = {"reason": "Stoney Verify default setup permission refresh"}
                if category is not None and channel.category_id != category.id:
                    kwargs["category"] = category
                if overwrites is not None:
                    kwargs["overwrites"] = overwrites
                if topic:
                    kwargs["topic"] = topic[:1024]
                await channel.edit(**kwargs)
            except Exception as e:
                notes.append(f"Could not refresh channel `{name}`: {type(e).__name__}")
        return channel

    ok, reason = _can_manage_channels(guild)
    if not ok:
        notes.append(f"Could not create channel `#{name}`: {reason}")
        return None

    try:
        channel = await guild.create_text_channel(
            name=name,
            category=category,
            overwrites=overwrites,
            topic=topic[:1024] if topic else None,
            reason="Stoney Verify default setup",
        )
        created.append(f"Channel: {channel.mention}")
        return channel
    except Exception as e:
        notes.append(f"Could not create channel `#{name}`: {type(e).__name__}")
        return None


async def _ensure_voice_channel(
    guild: discord.Guild,
    name: str,
    *,
    category: Optional[discord.CategoryChannel],
    overwrites: Optional[dict[Any, discord.PermissionOverwrite]],
    apply_channel_permissions: bool,
    notes: list[str],
    created: list[str],
    reused: list[str],
) -> Optional[discord.VoiceChannel]:
    channel = _voice_channel_by_name(guild, name)
    if channel is not None:
        reused.append(f"Voice: {channel.mention}")
        if apply_channel_permissions:
            try:
                kwargs: dict[str, Any] = {"reason": "Stoney Verify default setup permission refresh"}
                if category is not None and channel.category_id != category.id:
                    kwargs["category"] = category
                if overwrites is not None:
                    kwargs["overwrites"] = overwrites
                await channel.edit(**kwargs)
            except Exception as e:
                notes.append(f"Could not refresh voice channel `{name}`: {type(e).__name__}")
        return channel

    ok, reason = _can_manage_channels(guild)
    if not ok:
        notes.append(f"Could not create voice channel `{name}`: {reason}")
        return None

    try:
        channel = await guild.create_voice_channel(
            name=name,
            category=category,
            overwrites=overwrites,
            reason="Stoney Verify default setup",
        )
        created.append(f"Voice: {channel.mention}")
        return channel
    except Exception as e:
        notes.append(f"Could not create voice channel `{name}`: {type(e).__name__}")
        return None


def _line_list(lines: list[str], *, empty: str = "None", limit: int = 1000) -> str:
    if not lines:
        return empty
    out: list[str] = []
    total = 0
    for line in lines:
        text = str(line)
        extra = len(text) + 1
        if total + extra > limit:
            out.append(f"…and {len(lines) - len(out)} more")
            break
        out.append(text)
        total += extra
    return "\n".join(out) or empty


async def _assign_control_role_to_runner(
    interaction: discord.Interaction,
    control_role: Optional[discord.Role],
    *,
    notes: list[str],
    ok: list[str],
) -> None:
    if control_role is None or interaction.guild is None:
        return
    if not isinstance(interaction.user, discord.Member):
        return

    try:
        if control_role in interaction.user.roles:
            return
    except Exception:
        return

    if not _role_manageable_by_bot(interaction.guild, control_role):
        notes.append(f"Could not auto-assign {control_role.mention} to you because of bot role hierarchy.")
        return

    try:
        await interaction.user.add_roles(control_role, reason="Stoney Verify default setup bootstrap")
        ok.append(f"Assigned {control_role.mention} to the setup runner for future bot-control access.")
    except Exception as e:
        notes.append(f"Could not auto-assign {control_role.mention} to you: {type(e).__name__}")


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
        cfg_before = await get_guild_config(guild.id, refresh=True)
    except Exception:
        cfg_before = None

    if control_role is None:
        try:
            from .public_access_control import configured_control_role_ids_for_guild

            for rid in sorted(configured_control_role_ids_for_guild(guild.id)):
                role = guild.get_role(int(rid))
                if role is not None:
                    control_role = role
                    reused.append(f"Server-control role: {role.mention}")
                    break
        except Exception:
            control_role = None

    if control_role is None:
        control_role = await _ensure_role(
            guild,
            DEFAULT_CONTROL_ROLE_NAME,
            create_missing_roles=create_missing_roles,
            notes=notes,
            created=created,
            reused=reused,
        )

    if staff_role is None:
        try:
            existing_staff_id = _safe_int(getattr(cfg_before, "staff_role_id", 0), 0)
            if existing_staff_id > 0:
                maybe = guild.get_role(existing_staff_id)
                if maybe is not None:
                    staff_role = maybe
                    reused.append(f"Ticket staff role: {maybe.mention}")
        except Exception:
            staff_role = None

    if staff_role is None:
        staff_role = await _ensure_role(
            guild,
            DEFAULT_STAFF_ROLE_NAME,
            create_missing_roles=create_missing_roles,
            notes=notes,
            created=created,
            reused=reused,
        )

    unverified_role = await _ensure_role(
        guild,
        DEFAULT_UNVERIFIED_ROLE_NAME,
        create_missing_roles=create_missing_roles,
        notes=notes,
        created=created,
        reused=reused,
    )
    verified_role = await _ensure_role(
        guild,
        DEFAULT_VERIFIED_ROLE_NAME,
        create_missing_roles=create_missing_roles,
        notes=notes,
        created=created,
        reused=reused,
    )
    resident_role = await _ensure_role(
        guild,
        DEFAULT_RESIDENT_ROLE_NAME,
        create_missing_roles=create_missing_roles,
        notes=notes,
        created=created,
        reused=reused,
    )

    if control_role is not None:
        await _assign_control_role_to_runner(interaction, control_role, notes=notes, ok=ok)

    private_overwrites = _private_overwrites(guild, staff_role=staff_role, control_role=control_role)
    verify_overwrites = _verify_overwrites(
        guild,
        staff_role=staff_role,
        control_role=control_role,
        unverified_role=unverified_role,
    )
    vc_overwrites = _vc_overwrites(
        guild,
        staff_role=staff_role,
        control_role=control_role,
        unverified_role=unverified_role,
    )

    verify_category = await _ensure_category(
        guild,
        VERIFY_CATEGORY_NAME,
        overwrites=verify_overwrites,
        apply_channel_permissions=apply_channel_permissions,
        notes=notes,
        created=created,
        reused=reused,
    )
    tools_category = await _ensure_category(
        guild,
        TOOLS_CATEGORY_NAME,
        overwrites=private_overwrites,
        apply_channel_permissions=apply_channel_permissions,
        notes=notes,
        created=created,
        reused=reused,
    )
    ticket_category = await _ensure_category(
        guild,
        TICKET_CATEGORY_NAME,
        overwrites=private_overwrites,
        apply_channel_permissions=apply_channel_permissions,
        notes=notes,
        created=created,
        reused=reused,
    )
    archive_category = await _ensure_category(
        guild,
        ARCHIVE_CATEGORY_NAME,
        overwrites=private_overwrites,
        apply_channel_permissions=apply_channel_permissions,
        notes=notes,
        created=created,
        reused=reused,
    )

    verify_channel = await _ensure_text_channel(
        guild,
        VERIFY_CHANNEL_NAME,
        category=verify_category,
        overwrites=verify_overwrites,
        topic="Start Stoney Verify here. Server owners can rename this channel later.",
        apply_channel_permissions=apply_channel_permissions,
        notes=notes,
        created=created,
        reused=reused,
    )
    vc_queue_channel = await _ensure_text_channel(
        guild,
        VC_QUEUE_CHANNEL_NAME,
        category=verify_category,
        overwrites=private_overwrites,
        topic="Staff queue/status channel for voice verification requests.",
        apply_channel_permissions=apply_channel_permissions,
        notes=notes,
        created=created,
        reused=reused,
    )
    vc_verify_channel = await _ensure_voice_channel(
        guild,
        VC_VERIFY_CHANNEL_NAME,
        category=verify_category,
        overwrites=vc_overwrites,
        apply_channel_permissions=apply_channel_permissions,
        notes=notes,
        created=created,
        reused=reused,
    )
    transcripts_channel = await _ensure_text_channel(
        guild,
        TRANSCRIPTS_CHANNEL_NAME,
        category=tools_category,
        overwrites=private_overwrites,
        topic="Ticket transcripts created by Stoney Verify.",
        apply_channel_permissions=apply_channel_permissions,
        notes=notes,
        created=created,
        reused=reused,
    )
    modlog_channel = await _ensure_text_channel(
        guild,
        MODLOG_CHANNEL_NAME,
        category=tools_category,
        overwrites=private_overwrites,
        topic="Moderation, ticket, and security logs from Stoney Verify.",
        apply_channel_permissions=apply_channel_permissions,
        notes=notes,
        created=created,
        reused=reused,
    )
    welcome_exit_channel = await _ensure_text_channel(
        guild,
        WELCOME_EXIT_CHANNEL_NAME,
        category=tools_category,
        overwrites=private_overwrites,
        topic="Join and leave logs from Stoney Verify.",
        apply_channel_permissions=apply_channel_permissions,
        notes=notes,
        created=created,
        reused=reused,
    )
    status_channel = await _ensure_text_channel(
        guild,
        STATUS_CHANNEL_NAME,
        category=tools_category,
        overwrites=private_overwrites,
        topic="Bot status/restored notices from Stoney Verify.",
        apply_channel_permissions=apply_channel_permissions,
        notes=notes,
        created=created,
        reused=reused,
    )

    blockers: list[str] = []
    required_pairs = [
        ("server-control role", control_role),
        ("ticket staff role", staff_role),
        ("unverified role", unverified_role),
        ("verified role", verified_role),
        ("ticket category", ticket_category),
        ("archive category", archive_category),
        ("verify channel", verify_channel),
        ("transcripts channel", transcripts_channel),
        ("modlog channel", modlog_channel),
    ]
    for label, value in required_pairs:
        if value is None:
            blockers.append(f"Missing {label}.")

    if blockers:
        embed = discord.Embed(
            title="🚫 Default Setup Incomplete",
            description="I could not create or resolve every required default item. Fix the blockers below, then run this again.",
            color=discord.Color.red(),
        )
        embed.add_field(name="Blockers", value=_line_list(blockers), inline=False)
        if notes:
            embed.add_field(name="Notes", value=_line_list(notes), inline=False)
        if created:
            embed.add_field(name="Created Before Stopping", value=_line_list(created), inline=False)
        if reused:
            embed.add_field(name="Reused", value=_line_list(reused), inline=False)
        return await interaction.followup.send(embed=embed, ephemeral=True)

    updates = {
        "server_control_role_id": _role_value(control_role),
        "control_role_id": _role_value(control_role),
        "perm_role_id": _role_value(control_role),
        "staff_role_id": _role_value(staff_role),
        "vc_staff_role_id": _role_value(staff_role),
        "unverified_role_id": _role_value(unverified_role),
        "verified_role_id": _role_value(verified_role),
        "resident_role_id": _role_value(resident_role),
        "ticket_category_id": str(int(ticket_category.id)) if ticket_category else None,
        "ticket_archive_category_id": str(int(archive_category.id)) if archive_category else None,
        "transcripts_channel_id": str(int(transcripts_channel.id)) if transcripts_channel else None,
        "verify_channel_id": str(int(verify_channel.id)) if verify_channel else None,
        "vc_verify_channel_id": str(int(vc_verify_channel.id)) if vc_verify_channel else None,
        "vc_verify_queue_channel_id": str(int(vc_queue_channel.id)) if vc_queue_channel else None,
        "modlog_channel_id": str(int(modlog_channel.id)) if modlog_channel else None,
        "raidlog_channel_id": str(int(modlog_channel.id)) if modlog_channel else None,
        "force_verify_log_channel_id": str(int(modlog_channel.id)) if modlog_channel else None,
        "join_log_channel_id": str(int(welcome_exit_channel.id)) if welcome_exit_channel else None,
        "status_channel_id": str(int(status_channel.id)) if status_channel else None,
        "ticket_prefix": "ticket",
        "configured_by_id": str(interaction.user.id),
        "configured_by_name": str(interaction.user),
        "configured_at": _utc_iso(),
        "default_setup_version": "1",
    }

    try:
        await _upsert_config(guild.id, updates)
        invalidate_guild_config(guild.id)
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        return await interaction.followup.send(f"❌ Default setup created Discord items but failed saving config: `{e}`", ephemeral=True)

    embed = _config_embed(guild, cfg, title="✅ Default Server Setup Complete")
    embed.add_field(name="Created", value=_line_list(created, empty="Nothing new created."), inline=False)
    embed.add_field(name="Reused", value=_line_list(reused, empty="Nothing reused."), inline=False)
    if ok:
        embed.add_field(name="Passing Checks", value=_line_list(ok), inline=False)
    if notes:
        embed.add_field(name="Notes", value=_line_list(notes), inline=False)
    embed.add_field(
        name="Next Step",
        value="Run `/stoney health`. If it passes, post your ticket/verify panels and test ticket create/close.",
        inline=False,
    )
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
        description="Auto-create default roles/channels/categories for a fresh server.",
        callback=_setup_defaults_callback,
    )

    try:
        command._params["control_role"].description = "Optional server-control role. Leave blank to reuse/create Stoney Control."
        command._params["staff_role"].description = "Optional ticket staff role. Leave blank to reuse/create Stoney Staff."
        command._params["create_missing_roles"].description = "Create missing default roles when needed."
        command._params["apply_channel_permissions"].description = "Apply safe default channel/category permissions."
    except Exception:
        pass

    stoney_group.add_command(command)
    _ATTACHED = True


_attach()


def register_public_setup_defaults_commands(bot, tree) -> None:
    _ = bot, tree
    _attach()
    try:
        print("✅ public_setup_defaults: attached /stoney setup-defaults command")
    except Exception:
        pass


__all__ = ["register_public_setup_defaults_commands"]
