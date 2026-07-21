from __future__ import annotations

"""Idempotent recommended setup for public guilds.

Auto-build must be boring and safe:
- reuse saved config IDs first
- reuse existing default-looking Discord items second
- create only truly missing items
- never move, rename, or delete existing items
- never overwrite owner-picked setup values; auto-build fills blanks only
- when enabled, repair only the safe bot/staff/control overwrites needed for Dank Shield to function
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
    dank_group,
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


def _config_value(
    cfg: Any,
    key: str,
    default: Any = None,
) -> Any:
    if cfg is None:
        return default

    try:
        value = getattr(cfg, key)

        if value is not None:
            return value
    except Exception:
        pass

    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)

            if value is not None:
                return value
    except Exception:
        pass

    return default


def _config_bool(
    value: Any,
    *,
    default: bool = False,
) -> bool:
    if isinstance(value, bool):
        return value

    if value is None:
        return bool(default)

    clean = str(value).strip().lower()

    if clean in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }:
        return True

    if clean in {
        "0",
        "false",
        "no",
        "off",
        "disabled",
        "",
    }:
        return False

    return bool(default)


def _first_config_bool(
    cfg: Any,
    keys: tuple[str, ...],
    *,
    default: bool,
) -> bool:
    for key in keys:
        value = _config_value(cfg, key, None)

        if value is not None:
            return _config_bool(
                value,
                default=default,
            )

    return bool(default)


def _service_scope_from_config(
    cfg: Any,
) -> dict[str, bool]:
    """Return exactly what Make Missing Things may create."""

    choice = str(
        _config_value(cfg, "setup_choice", "") or ""
    ).strip().lower()

    tickets_default = choice in {
        "basic_server",
        "help_desk",
        "voice_check",
        "id_check",
        "id_voice_check",
    }

    basic_verify_default = choice == "basic_verify"

    voice_default = choice in {
        "voice_check",
        "id_voice_check",
    }

    id_default = choice in {
        "id_check",
        "id_voice_check",
    }

    logs_default = choice in {
        "basic_server",
        "help_desk",
        "voice_check",
        "id_check",
        "id_voice_check",
    }

    tickets = _first_config_bool(
        cfg,
        (
            "tickets_enabled",
            "ticket_service_enabled",
        ),
        default=tickets_default,
    )

    basic_verify = _first_config_bool(
        cfg,
        (
            "basic_verify_enabled",
            "basic_button_verify_enabled",
            "verification_enabled",
        ),
        default=basic_verify_default,
    )

    voice = _first_config_bool(
        cfg,
        (
            "voice_verification_enabled",
            "vc_verify_enabled",
            "voice_verify_enabled",
            "verification_allows_voice",
        ),
        default=voice_default,
    )

    id_verify = _first_config_bool(
        cfg,
        (
            "id_verify_enabled",
            "web_verify_enabled",
            "id_web_verify_enabled",
            "verification_requires_id",
        ),
        default=id_default,
    )

    spam_guard = _first_config_bool(
        cfg,
        ("spam_guard_enabled",),
        default=False,
    )

    logs = _first_config_bool(
        cfg,
        (
            "logs_enabled",
            "moderation_enabled",
        ),
        default=logs_default,
    )

    if voice or id_verify:
        tickets = True
        basic_verify = True
        logs = True

    if spam_guard:
        logs = True

    resident_role = _first_config_bool(
        cfg,
        ("verification_resident_role_enabled",),
        default=(choice == "id_voice_check"),
    )

    return {
        "tickets": bool(tickets),
        "verify": bool(
            basic_verify
            or voice
            or id_verify
        ),
        "basic_verify": bool(basic_verify),
        "voice": bool(voice),
        "id": bool(id_verify),
        "spam_guard": bool(spam_guard),
        "logs": bool(logs),
        "welcome": bool(choice == "basic_server"),
        "resident_role": bool(resident_role),
    }


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


def _target_label(target: Any) -> str:
    try:
        mention = getattr(target, "mention", None)
        if mention:
            return str(mention)
        name = getattr(target, "name", None)
        if name:
            return f"`{name}`"
    except Exception:
        pass
    return "`unknown target`"


def _overwrite_allows_staff_like_access(expected: discord.PermissionOverwrite) -> bool:
    try:
        allow, _deny = expected.pair()
        return bool(
            allow.manage_channels
            or allow.manage_messages
            or allow.manage_threads
            or allow.send_messages
            or allow.move_members
        )
    except Exception:
        return False


def _is_safe_repair_target(
    guild: discord.Guild,
    target: Any,
    expected: discord.PermissionOverwrite,
) -> bool:
    """Only repair Dank Shield's bot/staff/control access, not member-facing defaults."""

    try:
        if target == guild.default_role:
            return False

        me = _bot_member(guild)
        if me is not None and getattr(target, "id", None) == getattr(me, "id", None):
            return True

        if isinstance(target, discord.Role):
            if target.is_default():
                return False
            return _overwrite_allows_staff_like_access(expected)
    except Exception:
        return False

    return False


def _overwrite_changed(
    current: discord.PermissionOverwrite,
    expected: discord.PermissionOverwrite,
) -> bool:
    try:
        return current.pair() != expected.pair()
    except Exception:
        return True


async def _repair_existing_permissions(
    channel: Optional[Any],
    overwrites: dict[Any, discord.PermissionOverwrite],
    *,
    label: str,
    notes: list[str],
    ok: list[str],
) -> None:
    """Repair only the access required for the bot and staff/control roles to work.

    This intentionally skips @everyone and normal member-facing role overwrites so
    auto-build remains safe on existing servers.
    """

    if channel is None or not overwrites:
        return

    guild = getattr(channel, "guild", None)
    if not isinstance(guild, discord.Guild):
        return

    repair_items = [
        (target, expected)
        for target, expected in overwrites.items()
        if _is_safe_repair_target(guild, target, expected)
    ]
    if not repair_items:
        return

    can_manage, reason = _can_manage_channels(guild)
    if not can_manage:
        notes.append(f"Could not repair permissions on {label}: {reason}")
        return

    repaired: list[str] = []
    for target, expected in repair_items:
        try:
            current = channel.overwrites_for(target)
        except Exception:
            current = discord.PermissionOverwrite()

        if not _overwrite_changed(current, expected):
            continue

        try:
            await channel.set_permissions(
                target,
                overwrite=expected,
                reason="Dank Shield setup -> repair required bot/staff access",
            )
            repaired.append(_target_label(target))
        except Exception as e:
            notes.append(f"Could not repair {label} permissions for {_target_label(target)}: {type(e).__name__}")

    if repaired:
        shown = ", ".join(repaired[:5])
        if len(repaired) > 5:
            shown += f", …and {len(repaired) - 5} more"
        ok.append(f"Repaired {label} access for {shown}.")


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
        role = await guild.create_role(name=name, permissions=discord.Permissions.none(), hoist=False, mentionable=False, reason="Dank Shield auto-build missing recommended role")
        created.append(f"Role: {role.mention}")
        return role
    except Exception as e:
        notes.append(f"Could not create role `{name}`: {type(e).__name__}")
        return None


async def _ensure_category(guild: discord.Guild, name: str, *, overwrites: dict[Any, discord.PermissionOverwrite], notes: list[str], created: list[str], reused: list[str]) -> Optional[discord.CategoryChannel]:
    category = _category_by_name(guild, name)
    if category:
        _unique(reused, f"Category: `{category.name}`")
        notes.append(f"Reused existing category `{category.name}`; safe bot/staff permission repair will be checked.")
        return category
    ok, reason = _can_manage_channels(guild)
    if not ok:
        notes.append(f"Could not create category `{name}`: {reason}")
        return None
    try:
        category = await guild.create_category(name=name, overwrites=overwrites, reason="Dank Shield auto-build missing recommended category")
        created.append(f"Category: `{category.name}`")
        return category
    except Exception as e:
        notes.append(f"Could not create category `{name}`: {type(e).__name__}")
        return None


async def _ensure_text(guild: discord.Guild, name: str, *, category: Optional[discord.CategoryChannel], overwrites: dict[Any, discord.PermissionOverwrite], topic: str, notes: list[str], created: list[str], reused: list[str]) -> Optional[discord.TextChannel]:
    channel = _text_by_name(guild, name)
    if channel:
        _unique(reused, f"Channel: {channel.mention}")
        notes.append(f"Reused existing channel {channel.mention}; safe bot/staff permission repair will be checked.")
        return channel
    ok, reason = _can_manage_channels(guild)
    if not ok:
        notes.append(f"Could not create channel `#{name}`: {reason}")
        return None
    try:
        channel = await guild.create_text_channel(name=name, category=category, overwrites=overwrites, topic=topic[:1024] if topic else None, reason="Dank Shield auto-build missing recommended channel")
        created.append(f"Channel: {channel.mention}")
        return channel
    except Exception as e:
        notes.append(f"Could not create channel `#{name}`: {type(e).__name__}")
        return None


async def _ensure_voice(guild: discord.Guild, name: str, *, category: Optional[discord.CategoryChannel], overwrites: dict[Any, discord.PermissionOverwrite], notes: list[str], created: list[str], reused: list[str]) -> Optional[discord.VoiceChannel]:
    channel = _voice_by_name(guild, name)
    if channel:
        _unique(reused, f"Voice: {channel.mention}")
        notes.append(f"Reused existing voice channel {channel.mention}; safe bot/staff permission repair will be checked.")
        return channel
    ok, reason = _can_manage_channels(guild)
    if not ok:
        notes.append(f"Could not create voice channel `{name}`: {reason}")
        return None
    try:
        channel = await guild.create_voice_channel(name=name, category=category, overwrites=overwrites, reason="Dank Shield auto-build missing recommended voice channel")
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
        await interaction.user.add_roles(role, reason="Dank Shield auto-build bootstrap")
        ok.append(f"Assigned {role.mention} to you for future bot setup access.")
    except Exception as e:
        notes.append(f"Could not auto-assign {role.mention} to you: {type(e).__name__}")


async def _setup_defaults_callback(
    interaction: discord.Interaction,
    control_role: Optional[discord.Role] = None,
    staff_role: Optional[discord.Role] = None,
    create_missing_roles: bool = True,
    apply_channel_permissions: bool = True,
) -> bool:
    """Create only items required by this guild's enabled features."""

    if not await _require_setup_permission(interaction):
        return False

    await safe_defer(
        interaction,
        ephemeral=True,
    )

    guild = interaction.guild

    if guild is None:
        await interaction.followup.send(
            "❌ This must be used inside a server.",
            ephemeral=True,
        )
        return False

    try:
        cfg = await get_guild_config(
            guild.id,
            refresh=True,
        )
    except Exception as exc:
        await interaction.followup.send(
            (
                "❌ I could not read this server's setup.\n"
                f"`{type(exc).__name__}: {str(exc)[:180]}`"
            ),
            ephemeral=True,
        )
        return False

    services = _service_scope_from_config(cfg)

    enabled_labels: list[str] = []

    if services["tickets"]:
        enabled_labels.append("Tickets")

    if services["basic_verify"]:
        enabled_labels.append("Basic Verify")

    if services["voice"]:
        enabled_labels.append("Voice Verify")

    if services["spam_guard"]:
        enabled_labels.append("SpamGuard")

    if services["logs"]:
        enabled_labels.append("Logs")

    if not enabled_labels:
        embed = discord.Embed(
            title="🧭 Choose Features First",
            description=(
                "Nothing was created because this server has "
                "no enabled features yet."
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="What to do",
            value=(
                "Open `/dank setup`, press **Start / Continue Setup**, "
                "and choose a setup type or turn features on."
            ),
            inline=False,
        )

        await interaction.followup.send(
            embed=embed,
            ephemeral=True,
        )
        return False

    created: list[str] = []
    reused: list[str] = []
    notes: list[str] = []
    ok: list[str] = []

    needs_staff = bool(
        services["tickets"]
        or services["voice"]
        or services["logs"]
    )

    needs_public_area = bool(
        services["welcome"]
        or services["tickets"]
        or services["verify"]
    )

    needs_staff_area = bool(
        services["tickets"]
        or services["voice"]
        or services["logs"]
    )

    control_role = (
        control_role
        or _role_from_config(
            guild,
            cfg,
            "server_control_role_id",
            "control_role_id",
            "perm_role_id",
        )
        or await _resolve_existing_control_role(guild)
    )

    if control_role is not None:
        _unique(
            reused,
            f"Setup manager role: {control_role.mention}",
        )

    if needs_staff:
        staff_role = (
            staff_role
            or _role_from_config(
                guild,
                cfg,
                "staff_role_id",
                "vc_staff_role_id",
            )
        )

        if staff_role is None:
            staff_role = await _ensure_role(
                guild,
                DEFAULT_STAFF_ROLE_NAME,
                create_missing_roles=create_missing_roles,
                notes=notes,
                created=created,
                reused=reused,
            )
        else:
            _unique(
                reused,
                f"Staff role: {staff_role.mention}",
            )
    else:
        staff_role = None

    unverified_role: Optional[discord.Role] = None
    verified_role: Optional[discord.Role] = None
    member_role: Optional[discord.Role] = None

    if services["verify"]:
        unverified_role = (
            _role_from_config(
                guild,
                cfg,
                "unverified_role_id",
            )
            or await _ensure_role(
                guild,
                DEFAULT_UNVERIFIED_ROLE_NAME,
                create_missing_roles=create_missing_roles,
                notes=notes,
                created=created,
                reused=reused,
            )
        )

        verified_role = (
            _role_from_config(
                guild,
                cfg,
                "verified_role_id",
            )
            or await _ensure_role(
                guild,
                DEFAULT_VERIFIED_ROLE_NAME,
                create_missing_roles=create_missing_roles,
                notes=notes,
                created=created,
                reused=reused,
            )
        )

    if services["resident_role"]:
        member_role = (
            _role_from_config(
                guild,
                cfg,
                "resident_role_id",
                "member_role_id",
            )
            or await _ensure_role(
                guild,
                DEFAULT_MEMBER_ROLE_NAME,
                create_missing_roles=create_missing_roles,
                notes=notes,
                created=created,
                reused=reused,
            )
        )

    await _assign_control_role_to_runner(
        interaction,
        control_role,
        notes,
        ok,
    )

    public_ow = (
        _public_overwrites(
            guild,
            staff_role,
            control_role,
            unverified_role,
        )
        if apply_channel_permissions
        else {}
    )

    staff_ow = (
        _staff_overwrites(
            guild,
            staff_role,
            control_role,
        )
        if apply_channel_permissions
        else {}
    )

    voice_ow = (
        _voice_overwrites(
            guild,
            staff_role,
            control_role,
            unverified_role,
        )
        if apply_channel_permissions
        else {}
    )

    start_category: Optional[discord.CategoryChannel] = None
    ticket_category: Optional[discord.CategoryChannel] = None
    archive_category: Optional[discord.CategoryChannel] = None
    management_category: Optional[discord.CategoryChannel] = None

    if needs_public_area:
        start_category = (
            _channel_from_config(
                guild,
                cfg,
                discord.CategoryChannel,
                "start_category_id",
                "welcome_category_id",
            )
            or await _ensure_category(
                guild,
                START_CATEGORY_NAME,
                overwrites=public_ow,
                notes=notes,
                created=created,
                reused=reused,
            )
        )

    if services["tickets"]:
        ticket_category = (
            _channel_from_config(
                guild,
                cfg,
                discord.CategoryChannel,
                "ticket_category_id",
            )
            or await _ensure_category(
                guild,
                TICKET_CATEGORY_NAME,
                overwrites=staff_ow,
                notes=notes,
                created=created,
                reused=reused,
            )
        )

        archive_category = (
            _channel_from_config(
                guild,
                cfg,
                discord.CategoryChannel,
                "ticket_archive_category_id",
            )
            or await _ensure_category(
                guild,
                ARCHIVE_CATEGORY_NAME,
                overwrites=staff_ow,
                notes=notes,
                created=created,
                reused=reused,
            )
        )

    if needs_staff_area:
        management_category = (
            _channel_from_config(
                guild,
                cfg,
                discord.CategoryChannel,
                "management_category_id",
                "staff_tools_category_id",
            )
            or await _ensure_category(
                guild,
                MANAGEMENT_CATEGORY_NAME,
                overwrites=staff_ow,
                notes=notes,
                created=created,
                reused=reused,
            )
        )

    welcome_channel: Optional[discord.TextChannel] = None
    verify_channel: Optional[discord.TextChannel] = None
    ticket_panel_channel: Optional[discord.TextChannel] = None
    vc_verify_channel: Optional[discord.VoiceChannel] = None
    vc_queue_channel: Optional[discord.TextChannel] = None
    vc_verify_preexisting = False
    vc_queue_preexisting = False
    transcripts_channel: Optional[discord.TextChannel] = None
    modlog_channel: Optional[discord.TextChannel] = None
    join_leave_channel: Optional[discord.TextChannel] = None
    status_channel: Optional[discord.TextChannel] = None

    if services["welcome"]:
        welcome_channel = (
            _channel_from_config(
                guild,
                cfg,
                discord.TextChannel,
                "welcome_channel_id",
            )
            or await _ensure_text(
                guild,
                WELCOME_CHANNEL_NAME,
                category=start_category,
                overwrites=public_ow,
                topic="Welcome information for new members.",
                notes=notes,
                created=created,
                reused=reused,
            )
        )

    if services["verify"]:
        verify_channel = (
            _channel_from_config(
                guild,
                cfg,
                discord.TextChannel,
                "verify_channel_id",
                "verification_channel_id",
            )
            or await _ensure_text(
                guild,
                VERIFY_CHANNEL_NAME,
                category=start_category,
                overwrites=public_ow,
                topic="Press Verify here to receive server access.",
                notes=notes,
                created=created,
                reused=reused,
            )
        )

    if services["tickets"]:
        ticket_panel_channel = (
            _channel_from_config(
                guild,
                cfg,
                discord.TextChannel,
                "ticket_panel_channel_id",
                "support_channel_id",
            )
            or await _ensure_text(
                guild,
                TICKET_PANEL_CHANNEL_NAME,
                category=start_category,
                overwrites=public_ow,
                topic="Open a private support ticket here.",
                notes=notes,
                created=created,
                reused=reused,
            )
        )

        transcripts_channel = (
            _channel_from_config(
                guild,
                cfg,
                discord.TextChannel,
                "transcripts_channel_id",
            )
            or await _ensure_text(
                guild,
                TRANSCRIPTS_CHANNEL_NAME,
                category=management_category,
                overwrites=staff_ow,
                topic="Ticket transcripts are posted here.",
                notes=notes,
                created=created,
                reused=reused,
            )
        )

    if services["voice"]:
        configured_vc_verify = _channel_from_config(
            guild,
            cfg,
            discord.VoiceChannel,
            "vc_verify_channel_id",
        )
        vc_verify_preexisting = bool(
            configured_vc_verify
            or _voice_by_name(guild, VC_VERIFY_CHANNEL_NAME)
        )
        vc_verify_channel = (
            configured_vc_verify
            or await _ensure_voice(
                guild,
                VC_VERIFY_CHANNEL_NAME,
                category=start_category,
                overwrites=voice_ow,
                notes=notes,
                created=created,
                reused=reused,
            )
        )

        configured_vc_queue = _channel_from_config(
            guild,
            cfg,
            discord.TextChannel,
            "vc_verify_queue_channel_id",
            "vc_queue_channel_id",
            "vc_request_channel_id",
        )
        vc_queue_preexisting = bool(
            configured_vc_queue
            or _text_by_name(guild, VC_QUEUE_CHANNEL_NAME)
        )
        vc_queue_channel = (
            configured_vc_queue
            or await _ensure_text(
                guild,
                VC_QUEUE_CHANNEL_NAME,
                category=management_category,
                overwrites=staff_ow,
                topic=(
                    "Staff requests and updates for Voice Verify."
                ),
                notes=notes,
                created=created,
                reused=reused,
            )
        )

    if services["logs"]:
        modlog_channel = (
            _channel_from_config(
                guild,
                cfg,
                discord.TextChannel,
                "modlog_channel_id",
                "raidlog_channel_id",
                "force_verify_log_channel_id",
            )
            or await _ensure_text(
                guild,
                MODLOG_CHANNEL_NAME,
                category=management_category,
                overwrites=staff_ow,
                topic="Moderation and security logs are posted here.",
                notes=notes,
                created=created,
                reused=reused,
            )
        )

        join_leave_channel = (
            _channel_from_config(
                guild,
                cfg,
                discord.TextChannel,
                "join_leave_log_channel_id",
                "join_log_channel_id",
                "leave_log_channel_id",
            )
            or await _ensure_text(
                guild,
                JOIN_LEAVE_CHANNEL_NAME,
                category=management_category,
                overwrites=staff_ow,
                topic="Member joins and leaves are posted here.",
                notes=notes,
                created=created,
                reused=reused,
            )
        )

        status_channel = (
            _channel_from_config(
                guild,
                cfg,
                discord.TextChannel,
                "status_channel_id",
                "bot_status_channel_id",
                "uptime_channel_id",
            )
            or await _ensure_text(
                guild,
                STATUS_CHANNEL_NAME,
                category=management_category,
                overwrites=staff_ow,
                topic="Bot status and restored services are posted here.",
                notes=notes,
                created=created,
                reused=reused,
            )
        )

    if apply_channel_permissions:
        repair_targets = (
            ("start folder", start_category, public_ow),
            ("new-ticket folder", ticket_category, staff_ow),
            ("closed-ticket folder", archive_category, staff_ow),
            ("staff-tools folder", management_category, staff_ow),
            ("welcome channel", welcome_channel, public_ow),
            ("verification channel", verify_channel, public_ow),
            ("ticket panel channel", ticket_panel_channel, public_ow),
            ("Voice Verify channel", vc_verify_channel, voice_ow),
            ("Voice Verify request channel", vc_queue_channel, staff_ow),
            ("transcript channel", transcripts_channel, staff_ow),
            ("moderation log channel", modlog_channel, staff_ow),
            ("join/leave log channel", join_leave_channel, staff_ow),
            ("bot status channel", status_channel, staff_ow),
        )

        for label, channel, overwrites in repair_targets:
            if channel is None:
                continue

            await _repair_existing_permissions(
                channel,
                overwrites,
                label=label,
                notes=notes,
                ok=ok,
            )

    required: list[tuple[str, Any]] = []

    if needs_staff:
        required.append(
            ("staff role", staff_role)
        )

    if services["tickets"]:
        required.extend(
            [
                ("new-ticket folder", ticket_category),
                ("Create Ticket panel channel", ticket_panel_channel),
            ]
        )

    if services["verify"]:
        required.extend(
            [
                ("approved-member role", verified_role),
                ("verification channel", verify_channel),
            ]
        )

    if services["voice"]:
        required.extend(
            [
                ("Voice Verify channel", vc_verify_channel),
                (
                    "Voice Verify staff request channel",
                    vc_queue_channel,
                ),
            ]
        )

    if services["logs"]:
        required.append(
            ("moderation/security log channel", modlog_channel)
        )

    blockers = [
        f"Missing {label}."
        for label, value in required
        if value is None
    ]

    if blockers:
        embed = discord.Embed(
            title="🚫 Some Required Items Could Not Be Made",
            description=(
                "Nothing unrelated was created. Fix the items below "
                "and run this step again."
            ),
            color=discord.Color.red(),
        )
        embed.add_field(
            name="Required fixes",
            value=_line_list(blockers),
            inline=False,
        )

        if created:
            embed.add_field(
                name="Created safely",
                value=_line_list(created),
                inline=False,
            )

        if reused:
            embed.add_field(
                name="Already existed",
                value=_line_list(reused),
                inline=False,
            )

        if notes:
            embed.add_field(
                name="Notes",
                value=_line_list(notes),
                inline=False,
            )

        await interaction.followup.send(
            embed=embed,
            ephemeral=True,
        )
        return False

    def item_id(item: Any) -> Optional[str]:
        if item is None:
            return None

        try:
            return str(int(item.id))
        except Exception:
            return None

    updates: dict[str, Any] = {
        "__config_write_mode": "auto_create",
        "__config_write_source": (
            "/dank setup make missing things"
        ),
        "configured_by_id": str(interaction.user.id),
        "configured_by_name": str(interaction.user),
        "configured_at": _utc_iso(),
        "default_setup_version": (
            "7_enabled_features_only"
        ),
    }

    if control_role is not None:
        updates.update(
            {
                "server_control_role_id": _role_value(control_role),
                "control_role_id": _role_value(control_role),
                "perm_role_id": _role_value(control_role),
            }
        )

    if staff_role is not None:
        updates.update(
            {
                "staff_role_id": _role_value(staff_role),
                "vc_staff_role_id": _role_value(staff_role),
            }
        )

    if services["verify"]:
        updates.update(
            {
                "unverified_role_id": _role_value(unverified_role),
                "verified_role_id": _role_value(verified_role),
                "verify_channel_id": item_id(verify_channel),
                "verification_channel_id": item_id(verify_channel),
            }
        )

    if member_role is not None:
        updates["resident_role_id"] = _role_value(member_role)

    if services["tickets"]:
        updates.update(
            {
                "ticket_category_id": item_id(ticket_category),
                "ticket_archive_category_id": item_id(
                    archive_category
                ),
                "ticket_panel_channel_id": item_id(
                    ticket_panel_channel
                ),
                "support_channel_id": item_id(
                    ticket_panel_channel
                ),
                "transcripts_channel_id": item_id(
                    transcripts_channel
                ),
                "ticket_prefix": "ticket",
            }
        )

    if services["voice"]:
        updates.update(
            {
                "vc_verify_channel_id": item_id(
                    vc_verify_channel
                ),
                "vc_verify_queue_channel_id": item_id(
                    vc_queue_channel
                ),
            }
        )
        if vc_verify_channel is not None and not vc_verify_preexisting:
            updates["vc_verify_channel_managed_id"] = item_id(
                vc_verify_channel
            )
        if vc_queue_channel is not None and not vc_queue_preexisting:
            updates["vc_verify_queue_channel_managed_id"] = item_id(
                vc_queue_channel
            )

    if services["welcome"]:
        updates["welcome_channel_id"] = item_id(
            welcome_channel
        )

    if services["logs"]:
        updates.update(
            {
                "modlog_channel_id": item_id(modlog_channel),
                "raidlog_channel_id": item_id(modlog_channel),
                "force_verify_log_channel_id": item_id(
                    modlog_channel
                ),
                "join_leave_log_channel_id": item_id(
                    join_leave_channel
                ),
                "join_log_channel_id": item_id(
                    join_leave_channel
                ),
                "leave_log_channel_id": item_id(
                    join_leave_channel
                ),
                "status_channel_id": item_id(status_channel),
                "bot_status_channel_id": item_id(
                    status_channel
                ),
                "uptime_channel_id": item_id(status_channel),
            }
        )

    if start_category is not None:
        updates["start_category_id"] = item_id(
            start_category
        )

    if management_category is not None:
        updates["management_category_id"] = item_id(
            management_category
        )

    updates = {
        key: value
        for key, value in updates.items()
        if value is not None
    }

    try:
        await _upsert_config(
            guild.id,
            updates,
        )
        invalidate_guild_config(guild.id)

        cfg_after = await get_guild_config(
            guild.id,
            refresh=True,
        )
    except Exception as exc:
        await interaction.followup.send(
            (
                "❌ The Discord items were handled, but saving "
                "the setup failed.\n"
                f"`{type(exc).__name__}: {str(exc)[:180]}`"
            ),
            ephemeral=True,
        )
        return False

    embed = _config_embed(
        guild,
        cfg_after,
        title="✅ Missing Items Handled",
    )

    embed.description = (
        "Dank Shield created or reused only the items needed by "
        "the features currently turned on."
    )

    embed.add_field(
        name="Enabled Features",
        value=", ".join(enabled_labels),
        inline=False,
    )

    embed.add_field(
        name="Created",
        value=_line_list(
            created,
            empty="Nothing new was needed.",
        ),
        inline=False,
    )

    embed.add_field(
        name="Already Existed",
        value=_line_list(
            reused,
            empty="No existing items were reused.",
        ),
        inline=False,
    )

    if ok:
        embed.add_field(
            name="Safe Repairs",
            value=_line_list(ok),
            inline=False,
        )

    if notes:
        embed.add_field(
            name="Notes",
            value=_line_list(notes),
            inline=False,
        )

    embed.add_field(
        name="Nothing Else Was Touched",
        value=(
            "Disabled features created nothing. Existing items "
            "were not renamed, moved, or deleted. Member-facing "
            "permissions were not rewritten."
        ),
        inline=False,
    )

    embed.add_field(
        name="Next Step",
        value=(
            "Return to `/dank setup` and press **Setup Check**."
        ),
        inline=False,
    )

    await interaction.followup.send(
        embed=embed,
        ephemeral=True,
    )

    return True


def _attach() -> None:
    global _ATTACHED
    if _ATTACHED:
        return
    try:
        existing = dank_group.get_command("setup-defaults")
    except Exception:
        existing = None
    if existing is not None:
        _ATTACHED = True
        return
    command = discord.app_commands.Command(
        name="setup-defaults",
        description="Create missing recommended roles/channels/categories and repair safe bot/staff access.",
        callback=_setup_defaults_callback,
    )
    try:
        command._params["control_role"].description = "Optional server-control role. Leave blank to reuse/create Bot Manager."
        command._params["staff_role"].description = "Optional ticket staff role. Leave blank to reuse/create Support Team."
        command._params["create_missing_roles"].description = "Create missing recommended roles only when no saved/custom role exists."
        command._params["apply_channel_permissions"].description = "Also repairs safe bot/staff/control overwrites on reused setup items."
    except Exception:
        pass
    dank_group.add_command(command)
    _ATTACHED = True


_attach()


def register_public_setup_defaults_commands(bot, tree) -> None:
    _ = bot, tree
    _attach()
    try:
        print("✅ public_setup_defaults: attached fill-only /dank setup-defaults command with safe permission repair")
    except Exception:
        pass


__all__ = ["register_public_setup_defaults_commands", "_setup_defaults_callback"]
