from __future__ import annotations

"""Capability-aware Roles & Profiles center and Discord role editor.

The product boundary is intentional:
- normal members only receive profile/cosmetic controls;
- recognized staff may use existing staff/member-role tools;
- only owner/Admin/Manage Roles actors may mutate server role definitions;
- every mutation re-checks live actor permission, bot permission, hierarchy,
  managed/default-role status, and known Dank Shield config dependencies.
"""

import asyncio
import re
import weakref
from collections.abc import Mapping, Sequence
from typing import Any, Optional

import discord

from stoney_verify.panel_lifecycle import PRIVATE_MENU_TTL_SECONDS
from stoney_verify.ui.picker import DankRoleSelect

_ROLE_EDITOR_PREFIX = "dank:roles:v1:"
_ROLE_ACTION_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6})$")

_PERMISSION_GROUP_BASE: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "general",
        "General & Moderation",
        (
            "administrator",
            "view_audit_log",
            "view_guild_insights",
            "manage_guild",
            "manage_roles",
            "manage_channels",
            "kick_members",
            "ban_members",
            "moderate_members",
            "manage_nicknames",
            "change_nickname",
            "manage_webhooks",
        ),
    ),
    (
        "text",
        "Text & Threads",
        (
            "view_channel",
            "send_messages",
            "send_tts_messages",
            "manage_messages",
            "embed_links",
            "attach_files",
            "read_message_history",
            "mention_everyone",
            "use_external_emojis",
            "add_reactions",
            "create_public_threads",
            "create_private_threads",
            "send_messages_in_threads",
            "manage_threads",
            "use_external_stickers",
            "use_application_commands",
            "send_voice_messages",
            "send_polls",
        ),
    ),
    (
        "voice",
        "Voice",
        (
            "connect",
            "speak",
            "stream",
            "priority_speaker",
            "mute_members",
            "deafen_members",
            "move_members",
            "use_voice_activation",
            "request_to_speak",
            "use_embedded_activities",
            "use_soundboard",
            "use_external_sounds",
        ),
    ),
    (
        "events",
        "Events & Expressions",
        (
            "create_instant_invite",
            "manage_events",
            "create_events",
            "manage_emojis",
            "manage_emojis_and_stickers",
            "manage_expressions",
            "create_expressions",
        ),
    ),
)


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _actor_id(actor: Any) -> int:
    try:
        return int(getattr(actor, "id", 0) or 0)
    except Exception:
        return 0


def _is_guild_owner(guild: discord.Guild, actor: Any) -> bool:
    actor_id = _actor_id(actor)
    if actor_id <= 0:
        return False
    try:
        if int(getattr(guild, "owner_id", 0) or 0) == actor_id:
            return True
    except Exception:
        pass
    try:
        return int(getattr(getattr(guild, "owner", None), "id", 0) or 0) == actor_id
    except Exception:
        return False


def _role_reason(action: str, actor: Any) -> str:
    return _clip(
        f"Dank Shield Role Editor: {action} by {actor} ({_actor_id(actor)})",
        480,
    )


def _role_action_lock(guild_id: int, role_id: int, action: str) -> asyncio.Lock:
    key = f"{int(guild_id)}:{int(role_id)}:{str(action)}"
    lock = _ROLE_ACTION_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _ROLE_ACTION_LOCKS[key] = lock
    return lock


def _bool_text(value: bool) -> str:
    return "On" if bool(value) else "Off"


def _permission_label(name: str) -> str:
    special = {
        "tts": "TTS",
        "guild": "Server",
        "webhooks": "Webhooks",
    }
    words = []
    for raw in str(name or "").split("_"):
        words.append(special.get(raw, raw.capitalize()))
    return " ".join(words)[:100] or "Permission"


def _parse_bool(value: str, *, field: str) -> bool:
    clean = str(value or "").strip().casefold()
    if clean in {"yes", "y", "on", "true", "1"}:
        return True
    if clean in {"no", "n", "off", "false", "0"}:
        return False
    raise ValueError(f"{field} must be yes or no.")


def _parse_colour(value: str, *, current: discord.Colour) -> discord.Colour:
    clean = str(value or "").strip()
    if not clean:
        return current
    if clean.casefold() in {"default", "none", "clear"}:
        return discord.Colour.default()
    match = _HEX_RE.fullmatch(clean)
    if not match:
        raise ValueError("Colour must be a 6-digit hex value such as #5865F2, or `default`.")
    return discord.Colour(int(match.group(1), 16))


def _actor_can_manage_roles(guild: discord.Guild, actor: Any) -> bool:
    if _is_guild_owner(guild, actor):
        return True
    if not isinstance(actor, discord.Member):
        return False
    perms = actor.guild_permissions
    return bool(perms.administrator or perms.manage_roles)


def _bot_can_manage_roles(guild: discord.Guild) -> bool:
    me = guild.me
    if not isinstance(me, discord.Member):
        return False
    perms = me.guild_permissions
    return bool(perms.administrator or perms.manage_roles)


async def _reply(
    interaction: discord.Interaction,
    content: str,
    *,
    ephemeral: bool = True,
) -> None:
    kwargs = {
        "ephemeral": ephemeral,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if not interaction.response.is_done():
        await interaction.response.send_message(content, **kwargs)
    else:
        await interaction.followup.send(content, **kwargs)


async def _replace(
    interaction: discord.Interaction,
    *,
    embed: discord.Embed,
    view: discord.ui.View,
) -> None:
    kwargs = {
        "embed": embed,
        "view": view,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if not interaction.response.is_done():
        if interaction.message is not None:
            await interaction.response.edit_message(**kwargs)
        else:
            await interaction.response.send_message(**kwargs, ephemeral=True)
    else:
        await interaction.edit_original_response(**kwargs)


async def _followup_panel(
    interaction: discord.Interaction,
    *,
    embed: discord.Embed,
    view: discord.ui.View,
    content: str = "",
) -> None:
    await interaction.followup.send(
        content=content or None,
        embed=embed,
        view=view,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _recognized_staff(interaction: discord.Interaction) -> bool:
    try:
        from .member_role_browser_common import _can_review

        return bool(await _can_review(interaction))
    except Exception:
        return False


def _can_manage_setup(interaction: discord.Interaction) -> bool:
    try:
        from .public_owner_authority import interaction_has_manage_guild_authority

        return bool(interaction_has_manage_guild_authority(interaction))
    except Exception:
        return False


async def _require_role_manager(
    interaction: discord.Interaction,
) -> tuple[Optional[discord.Guild], Optional[Any]]:
    guild = interaction.guild
    actor = interaction.user
    if guild is None or actor is None:
        await _reply(interaction, "❌ Role administration only works inside a server.")
        return None, None
    if not _actor_can_manage_roles(guild, actor):
        await _reply(
            interaction,
            "❌ Server role editing requires the server owner, Administrator, or the live Discord **Manage Roles** permission.",
        )
        return None, None
    if not _bot_can_manage_roles(guild):
        await _reply(
            interaction,
            "❌ Dank Shield is missing **Manage Roles**, so it cannot edit server roles.",
        )
        return None, None
    return guild, actor


def _role_mutation_blockers(
    guild: discord.Guild,
    actor: Any,
    role: discord.Role,
) -> list[str]:
    blockers: list[str] = []
    me = guild.me
    if role.is_default():
        blockers.append("@everyone cannot be edited by this tool.")
    if role.managed:
        blockers.append("Discord/integration-managed roles cannot be edited manually.")
    if not _actor_can_manage_roles(guild, actor):
        blockers.append("You no longer have Manage Roles.")
    if not isinstance(me, discord.Member):
        blockers.append("Dank Shield could not resolve its server member.")
        return blockers
    if not _bot_can_manage_roles(guild):
        blockers.append("Dank Shield is missing Manage Roles.")
    try:
        if not _is_guild_owner(guild, actor):
            if not isinstance(actor, discord.Member):
                blockers.append("Your live server-member role hierarchy could not be resolved.")
            elif role >= actor.top_role:
                blockers.append("Your highest role must stay above the role you edit.")
    except Exception:
        blockers.append("Your role hierarchy could not be verified.")
    try:
        if int(me.id) != int(guild.owner_id) and role >= me.top_role:
            blockers.append("Dank Shield's highest role must stay above the role it edits.")
    except Exception:
        blockers.append("Dank Shield's role hierarchy could not be verified.")
    return blockers


def _permission_grant_blockers(
    guild: discord.Guild,
    actor: Any,
    names: Sequence[str],
) -> list[str]:
    blockers: list[str] = []
    me = guild.me
    actor_perms = (
        actor.guild_permissions
        if isinstance(actor, discord.Member)
        else discord.Permissions.none()
    )
    bot_perms = me.guild_permissions if isinstance(me, discord.Member) else discord.Permissions.none()

    if not _is_guild_owner(guild, actor) and not actor_perms.administrator:
        missing = [name for name in names if not bool(getattr(actor_perms, name, False))]
        if missing:
            blockers.append(
                "You cannot grant permissions you do not have: "
                + ", ".join(_permission_label(name) for name in missing[:6])
                + ("…" if len(missing) > 6 else "")
            )

    if not bot_perms.administrator:
        missing = [name for name in names if not bool(getattr(bot_perms, name, False))]
        if missing:
            blockers.append(
                "Dank Shield cannot grant permissions it does not have: "
                + ", ".join(_permission_label(name) for name in missing[:6])
                + ("…" if len(missing) > 6 else "")
            )
    return blockers


def _value_contains_role_id(value: Any, role_id: int) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, Mapping):
        return any(_value_contains_role_id(item, role_id) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_value_contains_role_id(item, role_id) for item in value)
    try:
        if int(value) == int(role_id):
            return True
    except Exception:
        pass
    try:
        return str(role_id) in re.findall(r"\d{5,25}", str(value))
    except Exception:
        return False


def _config_dependency_labels(config: Mapping[str, Any], role_id: int) -> list[str]:
    labels: list[str] = []
    for key, value in dict(config or {}).items():
        clean_key = str(key or "")
        if "role" not in clean_key.casefold():
            continue
        if not _value_contains_role_id(value, role_id):
            continue
        label = clean_key.replace("_id", "").replace("_ids", "").replace("_", " ").strip().title()
        labels.append(label or clean_key)
    return sorted(dict.fromkeys(labels))


async def _role_dependencies(guild: discord.Guild, role: discord.Role) -> list[str]:
    try:
        from stoney_verify.guild_config import get_guild_config

        config = await get_guild_config(int(guild.id), refresh=True)
    except Exception:
        return ["Dank Shield configuration could not be verified"]
    return _config_dependency_labels(config, int(role.id))


def _permission_groups(role: discord.Role) -> list[tuple[str, str, tuple[str, ...]]]:
    available = [str(name) for name, _enabled in role.permissions]
    available_set = set(available)
    used: set[str] = set()
    groups: list[tuple[str, str, tuple[str, ...]]] = []

    for key, label, desired in _PERMISSION_GROUP_BASE:
        names = tuple(name for name in desired if name in available_set and name not in used)
        if not names:
            continue
        used.update(names)
        groups.append((key, label, names))

    remaining = [name for name in available if name not in used]
    for index in range(0, len(remaining), 25):
        chunk = tuple(remaining[index : index + 25])
        if not chunk:
            continue
        number = index // 25 + 1
        label = "Other Permissions" if len(remaining) <= 25 else f"Other Permissions {number}"
        groups.append((f"other_{number}", label, chunk))
    return groups


def _role_icon_text(role: discord.Role) -> str:
    icon = getattr(role, "display_icon", None)
    if icon is None:
        return "None"
    if isinstance(icon, str):
        return icon
    return "Custom image"


async def _role_embed(guild: discord.Guild, role: discord.Role) -> discord.Embed:
    dependencies = await _role_dependencies(guild, role)
    perms_enabled = [name for name, enabled in role.permissions if enabled]
    colour = f"#{role.colour.value:06X}" if role.colour.value else "Default"
    embed = discord.Embed(
        title=f"🎭 Role Editor · {role.name}",
        description=(
            "Every action re-checks your live Discord permissions, Dank Shield's permissions, "
            "and both role hierarchies before changing anything."
        ),
        color=role.colour if role.colour.value else discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Role", value=f"{role.mention}\n`{role.id}`", inline=True)
    embed.add_field(name="Position", value=f"`{role.position}`", inline=True)
    embed.add_field(name="Members", value=str(len(role.members)), inline=True)
    embed.add_field(
        name="Appearance",
        value=(
            f"Colour: **{colour}**\n"
            f"Hoisted: **{_bool_text(role.hoist)}**\n"
            f"Mentionable: **{_bool_text(role.mentionable)}**\n"
            f"Icon: **{_role_icon_text(role)}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Permissions",
        value=f"**{len(perms_enabled)}** enabled",
        inline=True,
    )
    embed.add_field(
        name="Dank Shield dependencies",
        value=(
            "\n".join(f"• {item}" for item in dependencies)[:1024]
            if dependencies
            else "None detected"
        ),
        inline=False,
    )
    embed.set_footer(
        text=(
            "Managed roles and @everyone are immutable here. "
            "Configured Dank Shield roles must be remapped before deletion."
        )
    )
    return embed


def _center_embed(*, staff: bool, role_manager: bool) -> discord.Embed:
    embed = discord.Embed(
        title="🎭 Roles & Profiles",
        description=(
            "Profiles and cosmetic roles stay member-safe. Server role administration only appears "
            "for people who currently have Discord's Manage Roles authority."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Your profile",
        value="🪪 **My Profile** • 🎭 **Profile Tags & Cosmetics**",
        inline=False,
    )
    if staff:
        embed.add_field(
            name="Staff tools",
            value="👥 **Member Role Manager** • 🌿 **Profile Builder**",
            inline=False,
        )
    if role_manager:
        embed.add_field(
            name="Server role administration",
            value=(
                "🛠️ **Server Role Editor** • ➕ **Create Role** • 🩺 **Role Health**\n"
                "Rename, colours/icons, permissions, hierarchy, duplication, and confirmed deletion."
            ),
            inline=False,
        )
    else:
        embed.add_field(
            name="Server role administration",
            value="Hidden unless your account currently has **Manage Roles**, Administrator, or server-owner authority.",
            inline=False,
        )
    return embed


def _role_editor_home_embed(guild: discord.Guild) -> discord.Embed:
    me = guild.me
    bot_top = getattr(getattr(me, "top_role", None), "mention", "Unknown")
    manageable = 0
    for role in guild.roles:
        if role.is_default() or role.managed:
            continue
        if isinstance(me, discord.Member) and role < me.top_role:
            manageable += 1
    embed = discord.Embed(
        title="🛠️ Server Role Editor",
        description=(
            "Choose a role below. Creation and every mutation are staff-only and permission-gated; "
            "member profile roles are still managed through the existing profile tools."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Manageable by Dank Shield", value=str(manageable), inline=True)
    embed.add_field(name="Dank Shield top role", value=str(bot_top), inline=True)
    embed.add_field(
        name="Safety",
        value="@everyone, integration-managed roles, roles above you, and roles above Dank Shield are blocked.",
        inline=False,
    )
