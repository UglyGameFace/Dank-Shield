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
from discord import app_commands

from stoney_verify.panel_lifecycle import PRIVATE_MENU_TTL_SECONDS
from stoney_verify.services import role_mutation_authority
from stoney_verify.ui.picker import DankRoleSelect

_ROLE_EDITOR_PREFIX = "dank:roles:v1:"
_ROLE_ACTION_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
_SELF_SERVICE_ROLE_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
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
    return role_mutation_authority.is_guild_owner(guild, actor)


def _role_reason(action: str, actor: Any) -> str:
    return _clip(
        f"Dank Shield Role Editor: {action} by {actor} ({_actor_id(actor)})",
        480,
    )


def _role_action_lock(guild_id: int, role_id: int, action: str) -> asyncio.Lock:
    # Serialize every mutation of the same role, even when different controls
    # (rename/permissions/move/delete) are pressed concurrently.
    key = (
        f"{int(guild_id)}:role:{int(role_id)}"
        if int(role_id) > 0
        else f"{int(guild_id)}:create:{str(action)}"
    )
    lock = _ROLE_ACTION_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _ROLE_ACTION_LOCKS[key] = lock
    return lock


def _self_service_role_lock(guild_id: int, member_id: int) -> asyncio.Lock:
    key = f"{int(guild_id)}:member:{int(member_id)}"
    lock = _SELF_SERVICE_ROLE_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _SELF_SERVICE_ROLE_LOCKS[key] = lock
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
    return role_mutation_authority.actor_can_manage_roles(guild, actor)


def _bot_can_manage_roles(guild: discord.Guild) -> bool:
    return role_mutation_authority.bot_can_manage_roles(guild)


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
    content: str = "",
) -> None:
    kwargs = {
        "content": content or None,
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
    return role_mutation_authority.role_mutation_blockers(guild, actor, role)


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

    def walk(value: Any, path: tuple[str, ...] = ()) -> None:
        if isinstance(value, Mapping):
            for raw_key, item in value.items():
                key = str(raw_key or "")
                next_path = (*path, key)
                if "role" in key.casefold() and _value_contains_role_id(item, role_id):
                    label = key.replace("_id", "").replace("_ids", "").replace("_", " ").strip().title()
                    labels.append(label or " / ".join(next_path))
                walk(item, next_path)
            return
        if isinstance(value, (list, tuple, set)):
            for item in value:
                walk(item, path)

    walk(dict(config or {}))
    return sorted(dict.fromkeys(labels))


async def _role_dependencies(guild: discord.Guild, role: discord.Role) -> list[str]:
    role_id = int(role.id)
    dependencies: list[str] = []

    try:
        from stoney_verify.guild_config import get_guild_config

        config = await get_guild_config(int(guild.id), refresh=True)
    except Exception:
        dependencies.append("Core Dank Shield role configuration could not be verified")
    else:
        dependencies.extend(_config_dependency_labels(config, role_id))

    # Spam Guard owns a separate guild_security_settings persistence surface.
    # Do not let Role Editor deletion strand its quarantine/exempt/invite-role
    # references merely because they are not part of guild_config.
    try:
        from stoney_verify import spam_guard

        spam_settings = await spam_guard.get_spam_settings(int(guild.id))
        spam_labels = _config_dependency_labels(spam_settings, role_id)
        dependencies.extend(f"Spam Guard · {label}" for label in spam_labels)

        diag = dict(spam_guard._SETTINGS_LAST_DIAG_BY_GUILD.get(int(guild.id)) or {})
        if str(diag.get("status") or "").strip().lower() in {"unavailable", "exception"}:
            dependencies.append("Spam Guard role settings could not be verified")
    except Exception:
        dependencies.append("Spam Guard role settings could not be verified")

    return sorted(dict.fromkeys(dependencies))


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


def _center_embed(*, staff: bool, role_manager: bool, setup_manager: bool) -> discord.Embed:
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
        value="🪪 **Member Setup** • 👤 **My Profile** • 🎭 **Profile Tags & Cosmetics** • 🌿 **Community & Pings**",
        inline=False,
    )
    if staff:
        staff_tools = ["👥 **Member Role Manager**"]
        if setup_manager:
            staff_tools.append("🧭 **Member Setup Manager**")
            staff_tools.append("🌿 **Profile Builder**")
        embed.add_field(
            name="Staff tools",
            value=" • ".join(staff_tools),
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
    return embed


def _role_health_embed(guild: discord.Guild, actor: Any) -> discord.Embed:
    me = guild.me
    roles = [role for role in guild.roles if not role.is_default()]
    managed = [role for role in roles if role.managed]
    bot_blocked = []
    actor_blocked = []
    if isinstance(me, discord.Member):
        bot_blocked = [role for role in roles if not role.managed and role >= me.top_role]
    if not _is_guild_owner(guild, actor) and isinstance(actor, discord.Member):
        actor_blocked = [role for role in roles if not role.managed and role >= actor.top_role]
    embed = discord.Embed(
        title="🩺 Role Health",
        description="Live hierarchy and Manage Roles readiness for this server.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Server roles", value=str(len(roles)), inline=True)
    embed.add_field(name="Managed/integration roles", value=str(len(managed)), inline=True)
    embed.add_field(name="Above/equal Dank Shield", value=str(len(bot_blocked)), inline=True)
    embed.add_field(name="Above/equal you", value=str(len(actor_blocked)), inline=True)
    embed.add_field(
        name="Dank Shield",
        value=(
            f"Manage Roles: **{_bool_text(_bot_can_manage_roles(guild))}**\n"
            f"Top role: {getattr(getattr(me, 'top_role', None), 'mention', 'Unknown')}"
        ),
        inline=False,
    )
    embed.set_footer(text="Role mutations are checked again at execution time.")
    return embed


class _OwnedView(discord.ui.View):
    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=PRIVATE_MENU_TTL_SECONDS)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(getattr(interaction.user, "id", 0) or 0) == self.owner_id:
            return True
        await _reply(interaction, "❌ Open your own `/dank home` panel to use these controls.")
        return False

    async def on_timeout(self) -> None:
        for item in self.children:
            try:
                item.disabled = True
            except Exception:
                pass


class RolesProfilesView(_OwnedView):
    def __init__(
        self,
        owner_id: int,
        *,
        staff: bool,
        role_manager: bool,
        setup_manager: bool,
    ) -> None:
        super().__init__(owner_id)
        self.staff = bool(staff)
        self.role_manager = bool(role_manager)
        self.setup_manager = bool(setup_manager)

        if not self.staff:
            self.remove_item(self.member_roles)
        if not (self.staff and self.setup_manager):
            self.remove_item(self.member_setup_admin)
            self.remove_item(self.profile_builder)
        if not self.role_manager:
            self.remove_item(self.server_roles)
            self.remove_item(self.create_role)
            self.remove_item(self.role_health)

    @discord.ui.button(label="Member Setup", emoji="🪪", style=discord.ButtonStyle.primary, row=0)
    async def member_setup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_member_setup import open_member_setup
        await open_member_setup(interaction)

    @discord.ui.button(label="My Profile", emoji="👤", style=discord.ButtonStyle.secondary, row=0)
    async def my_profile(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_command_hub import open_profile_entry

        await open_profile_entry(interaction)

    @discord.ui.button(label="Profile Tags & Cosmetics", emoji="🎭", style=discord.ButtonStyle.secondary, row=0)
    async def profile_tags(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild = interaction.guild
        member = interaction.user
        if guild is None or not isinstance(member, discord.Member):
            return await _reply(interaction, "❌ This only works inside a server.")
        from .public_self_roles_group import _open_profile_cosmetics

        await _open_profile_cosmetics(interaction, guild, member)

    @discord.ui.button(label="Community & Pings", emoji="🌿", style=discord.ButtonStyle.secondary, row=0)
    async def community_pings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_toke import open_member_community_pings
        await open_member_community_pings(interaction)

    @discord.ui.button(label="Member Role Manager", emoji="👥", style=discord.ButtonStyle.secondary, row=1)
    async def member_roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _recognized_staff(interaction):
            return await _reply(interaction, "❌ Staff only.")
        from .public_member_role_browser import _open_member_browser

        await _open_member_browser(interaction)

    @discord.ui.button(label="Member Setup Manager", emoji="🧭", style=discord.ButtonStyle.primary, row=1)
    async def member_setup_admin(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _recognized_staff(interaction) or not _can_manage_setup(interaction):
            return await _reply(interaction, "❌ Member Setup management requires authorized server management access.")
        from .public_member_setup import open_member_setup_admin
        await open_member_setup_admin(interaction)

    @discord.ui.button(label="Profile Builder", emoji="🌿", style=discord.ButtonStyle.secondary, row=1)
    async def profile_builder(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _recognized_staff(interaction) or not _can_manage_setup(interaction):
            return await _reply(interaction, "❌ Profile Builder setup requires authorized server management access.")
        from .public_self_roles_group import _post_profile_builder

        await _post_profile_builder(interaction, title="Profile Panel")

    @discord.ui.button(label="Server Role Editor", emoji="🛠️", style=discord.ButtonStyle.primary, row=2)
    async def server_roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild, actor = await _require_role_manager(interaction)
        if guild is None or actor is None:
            return
        await _replace(
            interaction,
            embed=_role_editor_home_embed(guild),
            view=RoleEditorHomeView(self.owner_id),
        )

    @discord.ui.button(label="Create Role", emoji="➕", style=discord.ButtonStyle.success, row=2)
    async def create_role(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild, actor = await _require_role_manager(interaction)
        if guild is None or actor is None:
            return
        await interaction.response.send_modal(CreateRoleModal(self.owner_id))

    @discord.ui.button(label="Role Health", emoji="🩺", style=discord.ButtonStyle.secondary, row=2)
    async def role_health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild, actor = await _require_role_manager(interaction)
        if guild is None or actor is None:
            return
        await _replace(
            interaction,
            embed=_role_health_embed(guild, actor),
            view=RoleHealthView(self.owner_id),
        )

    @discord.ui.button(label="Dank Shield Home", emoji="🏠", style=discord.ButtonStyle.secondary, row=4)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_command_surface_v2 import replace_with_compact_dank_home

        await replace_with_compact_dank_home(interaction)


class RoleHealthView(_OwnedView):
    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.primary)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild, actor = await _require_role_manager(interaction)
        if guild is None or actor is None:
            return
        await _replace(interaction, embed=_role_health_embed(guild, actor), view=self)

    @discord.ui.button(label="Role Editor", emoji="🛠️", style=discord.ButtonStyle.secondary)
    async def editor(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild, actor = await _require_role_manager(interaction)
        if guild is None or actor is None:
            return
        await _replace(interaction, embed=_role_editor_home_embed(guild), view=RoleEditorHomeView(self.owner_id))

    @discord.ui.button(label="Roles & Profiles", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_roles_profiles_center(interaction)


class RoleEditorHomeView(_OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)
        self.add_item(
            DankRoleSelect(
                author_id=owner_id,
                on_pick=self._picked,
                placeholder="Choose a server role to inspect or edit…",
                row=0,
            )
        )

    async def _picked(self, interaction: discord.Interaction, role: discord.Role) -> None:
        guild, actor = await _require_role_manager(interaction)
        if guild is None or actor is None:
            return
        blockers = _role_mutation_blockers(guild, actor, role)
        if blockers:
            return await _reply(interaction, "❌ " + "\n• ".join(["That role cannot be edited:", *blockers]))
        if not interaction.response.is_done():
            await interaction.response.defer()
        await _replace(
            interaction,
            embed=await _role_embed(guild, role),
            view=RoleDetailView(self.owner_id, role.id),
        )

    @discord.ui.button(label="Create Role", emoji="➕", style=discord.ButtonStyle.success, row=1)
    async def create_role(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild, actor = await _require_role_manager(interaction)
        if guild is None or actor is None:
            return
        await interaction.response.send_modal(CreateRoleModal(self.owner_id))

    @discord.ui.button(label="Role Health", emoji="🩺", style=discord.ButtonStyle.secondary, row=1)
    async def health(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild, actor = await _require_role_manager(interaction)
        if guild is None or actor is None:
            return
        await _replace(interaction, embed=_role_health_embed(guild, actor), view=RoleHealthView(self.owner_id))

    @discord.ui.button(label="Roles & Profiles", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_roles_profiles_center(interaction)


class RoleDetailView(_OwnedView):
    def __init__(self, owner_id: int, role_id: int) -> None:
        super().__init__(owner_id)
        self.role_id = int(role_id)

    def _role(self, guild: discord.Guild) -> Optional[discord.Role]:
        return guild.get_role(self.role_id)

    async def _fresh(
        self,
        interaction: discord.Interaction,
    ) -> tuple[Optional[discord.Guild], Optional[discord.Member], Optional[discord.Role]]:
        guild, actor = await _require_role_manager(interaction)
        if guild is None or actor is None:
            return None, None, None
        role = self._role(guild)
        if not isinstance(role, discord.Role):
            await _reply(interaction, "❌ That role no longer exists. Reopen the Role Editor.")
            return guild, actor, None
        blockers = _role_mutation_blockers(guild, actor, role)
        if blockers:
            await _reply(interaction, "❌ " + "\n• ".join(["That role is no longer editable:", *blockers]))
            return guild, actor, None
        return guild, actor, role

    @discord.ui.button(label="Appearance / Rename", emoji="✏️", style=discord.ButtonStyle.primary, row=0)
    async def appearance(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild, actor, role = await self._fresh(interaction)
        if guild is None or actor is None or role is None:
            return
        await interaction.response.send_modal(EditRoleAppearanceModal(self.owner_id, role))

    @discord.ui.button(label="Permissions", emoji="🔐", style=discord.ButtonStyle.primary, row=0)
    async def permissions(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild, actor, role = await self._fresh(interaction)
        if guild is None or actor is None or role is None:
            return
        view = PermissionGroupPickerView(self.owner_id, role.id).attach_for_role(role)
        await _replace(
            interaction,
            embed=discord.Embed(
                title=f"🔐 Permissions · {role.name}",
                description=(
                    "Choose a permission group. Saving a group changes only that group and preserves "
                    "every permission outside it. Permissions you cannot grant are rejected before Discord is called."
                ),
                color=role.colour if role.colour.value else discord.Color.blurple(),
            ),
            view=view,
        )

    @discord.ui.button(label="Move Up", emoji="⬆️", style=discord.ButtonStyle.secondary, row=1)
    async def move_up(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _move_role(interaction, owner_id=self.owner_id, role_id=self.role_id, direction=1)

    @discord.ui.button(label="Move Down", emoji="⬇️", style=discord.ButtonStyle.secondary, row=1)
    async def move_down(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _move_role(interaction, owner_id=self.owner_id, role_id=self.role_id, direction=-1)

    @discord.ui.button(label="Duplicate", emoji="🧬", style=discord.ButtonStyle.secondary, row=1)
    async def duplicate(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild, actor, role = await self._fresh(interaction)
        if guild is None or actor is None or role is None:
            return
        enabled = [name for name, value in role.permissions if value]
        grant_blockers = _permission_grant_blockers(guild, actor, enabled)
        if grant_blockers:
            return await _reply(interaction, "❌ " + "\n• ".join(grant_blockers))
        if not interaction.response.is_done():
            await interaction.response.defer()
        position_warning = ""
        try:
            async with _role_action_lock(guild.id, role.id, "duplicate"):
                fresh = guild.get_role(role.id)
                if not isinstance(fresh, discord.Role):
                    return await _reply(interaction, "❌ That role no longer exists.")
                fresh_blockers = _role_mutation_blockers(guild, actor, fresh)
                if fresh_blockers:
                    return await _reply(interaction, "❌ " + "\n• ".join(fresh_blockers))
                fresh_enabled = [name for name, value in fresh.permissions if value]
                fresh_grant_blockers = _permission_grant_blockers(guild, actor, fresh_enabled)
                if fresh_grant_blockers:
                    return await _reply(interaction, "❌ " + "\n• ".join(fresh_grant_blockers))
                create_fields: dict[str, Any] = {
                    "name": _clip(f"{fresh.name} Copy", 100),
                    "permissions": discord.Permissions(fresh.permissions.value),
                    "colour": fresh.colour,
                    "hoist": fresh.hoist,
                    "mentionable": fresh.mentionable,
                    "reason": _role_reason(f"duplicated role {fresh.id}", actor),
                }
                fresh_icon = fresh.display_icon if isinstance(fresh.display_icon, str) else None
                if fresh_icon and "ROLE_ICONS" in set(getattr(guild, "features", []) or []):
                    create_fields["display_icon"] = fresh_icon
                created = await guild.create_role(**create_fields)
                try:
                    await created.edit(
                        position=max(1, fresh.position - 1),
                        reason=_role_reason(f"positioned duplicate of {fresh.id}", actor),
                    )
                except (discord.Forbidden, discord.HTTPException):
                    position_warning = (
                        "Discord created the copy, but did not allow Dank Shield to place it beside the original. "
                        "The new role remains at Discord's default creation position."
                    )
        except discord.Forbidden:
            return await _reply(interaction, "❌ Discord denied the duplicate. Re-check Manage Roles and hierarchy.")
        except discord.HTTPException as exc:
            return await _reply(interaction, f"❌ Discord could not duplicate the role: {_clip(exc, 300)}")
        duplicate_embed = await _role_embed(guild, created)
        if position_warning:
            duplicate_embed.add_field(name="Position warning", value=position_warning, inline=False)
        await _replace(
            interaction,
            embed=duplicate_embed,
            view=RoleDetailView(self.owner_id, created.id),
        )

    @discord.ui.button(label="Delete Role", emoji="🗑️", style=discord.ButtonStyle.danger, row=2)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild, actor, role = await self._fresh(interaction)
        if guild is None or actor is None or role is None:
            return
        await interaction.response.send_modal(DeleteRoleModal(self.owner_id, role))

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=3)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild, actor, role = await self._fresh(interaction)
        if guild is None or actor is None or role is None:
            return
        if not interaction.response.is_done():
            await interaction.response.defer()
        await _replace(interaction, embed=await _role_embed(guild, role), view=self)

    @discord.ui.button(label="Choose Another Role", emoji="↩️", style=discord.ButtonStyle.secondary, row=3)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild, actor = await _require_role_manager(interaction)
        if guild is None or actor is None:
            return
        await _replace(interaction, embed=_role_editor_home_embed(guild), view=RoleEditorHomeView(self.owner_id))


def _adjacent_editable_role(
    guild: discord.Guild,
    role: discord.Role,
    direction: int,
) -> Optional[discord.Role]:
    candidates = [
        candidate
        for candidate in guild.roles
        if isinstance(candidate, discord.Role)
        and not candidate.is_default()
        and not candidate.managed
        and int(candidate.id) != int(role.id)
    ]
    if direction > 0:
        above = sorted(
            (candidate for candidate in candidates if candidate.position > role.position),
            key=lambda candidate: candidate.position,
        )
        return above[0] if above else None
    below = sorted(
        (candidate for candidate in candidates if candidate.position < role.position),
        key=lambda candidate: candidate.position,
        reverse=True,
    )
    return below[0] if below else None


def _move_target_blocker(
    guild: discord.Guild,
    actor: Any,
    target: discord.Role,
    direction: int,
) -> str:
    if direction <= 0:
        return ""
    if not _is_guild_owner(guild, actor):
        if not isinstance(actor, discord.Member):
            return "Your live role hierarchy could not be resolved."
        if target >= actor.top_role:
            return "Moving higher would cross your own highest role."
    me = guild.me
    if isinstance(me, discord.Member) and int(me.id) != int(guild.owner_id) and target >= me.top_role:
        return "Moving higher would cross Dank Shield's highest role."
    return ""


async def _move_role(
    interaction: discord.Interaction,
    *,
    owner_id: int,
    role_id: int,
    direction: int,
) -> None:
    guild, actor = await _require_role_manager(interaction)
    if guild is None or actor is None:
        return
    role = guild.get_role(int(role_id))
    if not isinstance(role, discord.Role):
        return await _reply(interaction, "❌ That role no longer exists.")
    blockers = _role_mutation_blockers(guild, actor, role)
    if blockers:
        return await _reply(interaction, "❌ " + "\n• ".join(blockers))

    target = _adjacent_editable_role(guild, role, direction)
    if not isinstance(target, discord.Role):
        return await _reply(interaction, "ℹ️ That role is already at the editable edge of the hierarchy.")
    crossing = _move_target_blocker(guild, actor, target, direction)
    if crossing:
        return await _reply(interaction, "❌ " + crossing)

    if not interaction.response.is_done():
        await interaction.response.defer()
    try:
        async with _role_action_lock(guild.id, role.id, "position"):
            fresh = guild.get_role(role.id)
            if not isinstance(fresh, discord.Role):
                return await _reply(interaction, "❌ That role no longer exists.")
            fresh_blockers = _role_mutation_blockers(guild, actor, fresh)
            if fresh_blockers:
                return await _reply(interaction, "❌ " + "\n• ".join(fresh_blockers))
            fresh_target = _adjacent_editable_role(guild, fresh, direction)
            if not isinstance(fresh_target, discord.Role):
                return await _reply(interaction, "ℹ️ The role hierarchy changed; this role is now at the editable edge.")
            crossing = _move_target_blocker(guild, actor, fresh_target, direction)
            if crossing:
                return await _reply(interaction, "❌ " + crossing)
            edited = await fresh.edit(
                position=fresh_target.position,
                reason=_role_reason(
                    f"moved role {fresh.id} {'up' if direction > 0 else 'down'}",
                    actor,
                ),
            )
            role = edited if isinstance(edited, discord.Role) else fresh
    except discord.Forbidden:
        return await _reply(interaction, "❌ Discord denied the hierarchy move.")
    except discord.HTTPException as exc:
        return await _reply(interaction, f"❌ Discord could not move the role: {_clip(exc, 300)}")

    fresh = guild.get_role(int(role.id)) or role
    await _replace(
        interaction,
        embed=await _role_embed(guild, fresh),
        view=RoleDetailView(owner_id, fresh.id),
    )


class CreateRoleModal(discord.ui.Modal):
    def __init__(self, owner_id: int) -> None:
        super().__init__(title="Create Server Role", timeout=300)
        self.owner_id = int(owner_id)
        self.name_input = discord.ui.TextInput(
            label="Role name",
            placeholder="Moderator",
            min_length=1,
            max_length=100,
            required=True,
        )
        self.colour_input = discord.ui.TextInput(
            label="Colour",
            placeholder="#5865F2 or default",
            max_length=7,
            required=False,
        )
        self.hoist_input = discord.ui.TextInput(
            label="Display separately? yes/no",
            default="no",
            max_length=5,
            required=True,
        )
        self.mentionable_input = discord.ui.TextInput(
            label="Mentionable? yes/no",
            default="no",
            max_length=5,
            required=True,
        )
        for item in (self.name_input, self.colour_input, self.hoist_input, self.mentionable_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild, actor = await _require_role_manager(interaction)
        if guild is None or actor is None:
            return
        try:
            colour = _parse_colour(str(self.colour_input.value), current=discord.Colour.default())
            hoist = _parse_bool(str(self.hoist_input.value), field="Display separately")
            mentionable = _parse_bool(str(self.mentionable_input.value), field="Mentionable")
        except ValueError as exc:
            return await _reply(interaction, f"❌ {exc}")
        name = str(self.name_input.value or "").strip()
        if not name:
            return await _reply(interaction, "❌ Role name cannot be empty.")

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with _role_action_lock(guild.id, 0, "create"):
                if not _actor_can_manage_roles(guild, actor) or not _bot_can_manage_roles(guild):
                    return await _reply(interaction, "❌ Manage Roles authority changed before creation. Nothing was created.")
                role = await guild.create_role(
                    name=name[:100],
                    permissions=discord.Permissions.none(),
                    colour=colour,
                    hoist=hoist,
                    mentionable=mentionable,
                    reason=_role_reason("created server role", actor),
                )
        except discord.Forbidden:
            return await _reply(interaction, "❌ Discord denied role creation. Re-check Manage Roles.")
        except discord.HTTPException as exc:
            return await _reply(interaction, f"❌ Discord could not create the role: {_clip(exc, 300)}")

        await _followup_panel(
            interaction,
            content="✅ Role created.",
            embed=await _role_embed(guild, role),
            view=RoleDetailView(self.owner_id, role.id),
        )


class EditRoleAppearanceModal(discord.ui.Modal):
    def __init__(self, owner_id: int, role: discord.Role) -> None:
        super().__init__(title="Edit Role Appearance", timeout=300)
        self.owner_id = int(owner_id)
        self.role_id = int(role.id)
        current_colour = f"#{role.colour.value:06X}" if role.colour.value else "default"
        current_icon = role.display_icon if isinstance(role.display_icon, str) else ""
        self.name_input = discord.ui.TextInput(
            label="Role name",
            default=str(role.name)[:100],
            min_length=1,
            max_length=100,
        )
        self.colour_input = discord.ui.TextInput(
            label="Colour",
            default=current_colour,
            placeholder="#5865F2 or default",
            max_length=7,
        )
        self.icon_input = discord.ui.TextInput(
            label="Unicode role icon (optional)",
            default=str(current_icon)[:20],
            placeholder="emoji, blank = keep, clear = remove",
            max_length=20,
            required=False,
        )
        self.hoist_input = discord.ui.TextInput(
            label="Display separately? yes/no",
            default="yes" if role.hoist else "no",
            max_length=5,
        )
        self.mentionable_input = discord.ui.TextInput(
            label="Mentionable? yes/no",
            default="yes" if role.mentionable else "no",
            max_length=5,
        )
        for item in (
            self.name_input,
            self.colour_input,
            self.icon_input,
            self.hoist_input,
            self.mentionable_input,
        ):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild, actor = await _require_role_manager(interaction)
        if guild is None or actor is None:
            return
        role = guild.get_role(self.role_id)
        if not isinstance(role, discord.Role):
            return await _reply(interaction, "❌ That role no longer exists.")
        blockers = _role_mutation_blockers(guild, actor, role)
        if blockers:
            return await _reply(interaction, "❌ " + "\n• ".join(blockers))

        try:
            colour = _parse_colour(str(self.colour_input.value), current=role.colour)
            hoist = _parse_bool(str(self.hoist_input.value), field="Display separately")
            mentionable = _parse_bool(str(self.mentionable_input.value), field="Mentionable")
        except ValueError as exc:
            return await _reply(interaction, f"❌ {exc}")

        name = str(self.name_input.value or "").strip()
        if not name:
            return await _reply(interaction, "❌ Role name cannot be empty.")

        icon_raw = str(self.icon_input.value or "").strip()
        fields: dict[str, Any] = {
            "name": name[:100],
            "colour": colour,
            "hoist": hoist,
            "mentionable": mentionable,
            "reason": _role_reason(f"edited role {role.id}", actor),
        }
        if icon_raw:
            if "ROLE_ICONS" not in set(getattr(guild, "features", []) or []):
                return await _reply(interaction, "❌ This server does not currently support custom role icons.")
            fields["display_icon"] = None if icon_raw.casefold() in {"clear", "none"} else icon_raw

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with _role_action_lock(guild.id, self.role_id, "appearance"):
                fresh = guild.get_role(self.role_id)
                if not isinstance(fresh, discord.Role):
                    return await _reply(interaction, "❌ That role no longer exists.")
                fresh_blockers = _role_mutation_blockers(guild, actor, fresh)
                if fresh_blockers:
                    return await _reply(interaction, "❌ " + "\n• ".join(fresh_blockers))
                edited = await fresh.edit(**fields)
                if not isinstance(edited, discord.Role):
                    edited = guild.get_role(self.role_id) or fresh
        except discord.Forbidden:
            return await _reply(interaction, "❌ Discord denied the role edit. Re-check permissions and hierarchy.")
        except (discord.HTTPException, ValueError) as exc:
            return await _reply(interaction, f"❌ Discord could not edit the role: {_clip(exc, 300)}")

        await _followup_panel(
            interaction,
            content="✅ Role updated.",
            embed=await _role_embed(guild, edited),
            view=RoleDetailView(self.owner_id, edited.id),
        )


class PermissionGroupSelect(discord.ui.Select):
    def __init__(
        self,
        parent: "PermissionGroupPickerView",
        groups: list[tuple[str, str, tuple[str, ...]]],
    ) -> None:
        self.parent_view = parent
        self.groups = {key: (label, names) for key, label, names in groups}
        super().__init__(
            placeholder="Choose a permission group…",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(
                    label=label[:100],
                    value=key[:100],
                    description=f"{len(names)} permissions",
                    emoji="🔐",
                )
                for key, label, names in groups[:25]
            ],
            custom_id=f"{_ROLE_EDITOR_PREFIX}permission_group",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        guild, actor = await _require_role_manager(interaction)
        if guild is None or actor is None:
            return
        role = guild.get_role(self.parent_view.role_id)
        if not isinstance(role, discord.Role):
            return await _reply(interaction, "❌ That role no longer exists.")
        blockers = _role_mutation_blockers(guild, actor, role)
        if blockers:
            return await _reply(interaction, "❌ " + "\n• ".join(blockers))
        key = str((self.values or [""])[0])
        group = self.groups.get(key)
        if group is None:
            return await _reply(interaction, "❌ That permission group is no longer available.")
        label, names = group
        view = PermissionToggleView(
            self.parent_view.owner_id,
            role.id,
            group_key=key,
            group_label=label,
            names=names,
        ).attach_for_role(role)
        await _replace(
            interaction,
            embed=discord.Embed(
                title=f"🔐 {label} · {role.name}",
                description=(
                    "Select every permission this role should have in this group, then save. "
                    "Permissions outside this group stay untouched."
                ),
                color=role.colour if role.colour.value else discord.Color.blurple(),
            ),
            view=view,
        )


class PermissionGroupPickerView(_OwnedView):
    def __init__(self, owner_id: int, role_id: int) -> None:
        super().__init__(owner_id)
        self.role_id = int(role_id)
        # The role object is resolved from the interaction guild again when used.
        self._select_added = False

    def attach_for_role(self, role: discord.Role) -> "PermissionGroupPickerView":
        if not self._select_added:
            self.add_item(PermissionGroupSelect(self, _permission_groups(role)))
            self._select_added = True
        return self

    @discord.ui.button(label="Back to Role", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild, actor = await _require_role_manager(interaction)
        if guild is None or actor is None:
            return
        role = guild.get_role(self.role_id)
        if not isinstance(role, discord.Role):
            return await _reply(interaction, "❌ That role no longer exists.")
        if not interaction.response.is_done():
            await interaction.response.defer()
        await _replace(
            interaction,
            embed=await _role_embed(guild, role),
            view=RoleDetailView(self.owner_id, role.id),
        )


class PermissionToggleSelect(discord.ui.Select):
    def __init__(
        self,
        parent: "PermissionToggleView",
        role: discord.Role,
    ) -> None:
        self.parent_view = parent
        options = [
            discord.SelectOption(
                label=_permission_label(name),
                value=name,
                default=bool(getattr(role.permissions, name, False)),
            )
            for name in parent.names
        ]
        super().__init__(
            placeholder="Select permissions to enable…",
            min_values=0,
            max_values=max(1, len(options)),
            options=options,
            custom_id=f"{_ROLE_EDITOR_PREFIX}permission_values",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await _apply_permission_selection(
            interaction,
            owner_id=self.parent_view.owner_id,
            role_id=self.parent_view.role_id,
            group_key=self.parent_view.group_key,
            selected=set(str(value) for value in self.values),
            allow_admin=False,
        )


class PermissionToggleView(_OwnedView):
    def __init__(
        self,
        owner_id: int,
        role_id: int,
        *,
        group_key: str,
        group_label: str,
        names: Sequence[str],
    ) -> None:
        super().__init__(owner_id)
        self.role_id = int(role_id)
        self.group_key = str(group_key)
        self.group_label = str(group_label)
        self.names = tuple(str(name) for name in names)[:25]

    def attach_for_role(self, role: discord.Role) -> "PermissionToggleView":
        self.add_item(PermissionToggleSelect(self, role))
        return self

    @discord.ui.button(label="Permission Groups", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild, actor = await _require_role_manager(interaction)
        if guild is None or actor is None:
            return
        role = guild.get_role(self.role_id)
        if not isinstance(role, discord.Role):
            return await _reply(interaction, "❌ That role no longer exists.")
        view = PermissionGroupPickerView(self.owner_id, role.id).attach_for_role(role)
        await _replace(
            interaction,
            embed=discord.Embed(
                title=f"🔐 Permissions · {role.name}",
                description="Choose another permission group.",
                color=role.colour if role.colour.value else discord.Color.blurple(),
            ),
            view=view,
        )


async def _apply_permission_selection(
    interaction: discord.Interaction,
    *,
    owner_id: int,
    role_id: int,
    group_key: str,
    selected: set[str],
    allow_admin: bool,
    from_modal: bool = False,
) -> None:
    guild, actor = await _require_role_manager(interaction)
    if guild is None or actor is None:
        return
    role = guild.get_role(int(role_id))
    if not isinstance(role, discord.Role):
        return await _reply(interaction, "❌ That role no longer exists.")
    blockers = _role_mutation_blockers(guild, actor, role)
    if blockers:
        return await _reply(interaction, "❌ " + "\n• ".join(blockers))

    groups = {key: names for key, _label, names in _permission_groups(role)}
    names = tuple(groups.get(str(group_key), ()))
    if not names:
        return await _reply(interaction, "❌ That permission group is no longer available.")

    selected &= set(names)
    newly_enabled = [
        name
        for name in names
        if name in selected and not bool(getattr(role.permissions, name, False))
    ]
    grant_blockers = _permission_grant_blockers(guild, actor, newly_enabled)
    if grant_blockers:
        return await _reply(interaction, "❌ " + "\n• ".join(grant_blockers))

    if "administrator" in newly_enabled and not allow_admin:
        return await interaction.response.send_modal(
            AdministratorPermissionConfirmModal(
                owner_id,
                role_id=role.id,
                group_key=group_key,
                selected=selected,
                role_name=role.name,
            )
        )

    permissions = discord.Permissions(role.permissions.value)
    permissions.update(**{name: name in selected for name in names})
    if not interaction.response.is_done():
        await interaction.response.defer()
    try:
        async with _role_action_lock(guild.id, role.id, "permissions"):
            fresh = guild.get_role(role.id)
            if not isinstance(fresh, discord.Role):
                return await _reply(interaction, "❌ That role no longer exists.")
            fresh_blockers = _role_mutation_blockers(guild, actor, fresh)
            if fresh_blockers:
                return await _reply(interaction, "❌ " + "\n• ".join(fresh_blockers))
            current_permissions = discord.Permissions(fresh.permissions.value)
            current_permissions.update(**{name: name in selected for name in names})
            edited = await fresh.edit(
                permissions=current_permissions,
                reason=_role_reason(f"edited permissions for role {fresh.id}", actor),
            )
            if not isinstance(edited, discord.Role):
                edited = guild.get_role(fresh.id) or fresh
    except discord.Forbidden:
        return await _reply(interaction, "❌ Discord denied the permission change.")
    except discord.HTTPException as exc:
        return await _reply(interaction, f"❌ Discord could not save permissions: {_clip(exc, 300)}")

    if from_modal:
        await _followup_panel(
            interaction,
            content="✅ Role permissions updated.",
            embed=await _role_embed(guild, edited),
            view=RoleDetailView(owner_id, edited.id),
        )
    else:
        await _replace(
            interaction,
            embed=await _role_embed(guild, edited),
            view=RoleDetailView(owner_id, edited.id),
        )


class AdministratorPermissionConfirmModal(discord.ui.Modal):
    def __init__(
        self,
        owner_id: int,
        *,
        role_id: int,
        group_key: str,
        selected: set[str],
        role_name: str,
    ) -> None:
        super().__init__(title="Confirm Administrator Permission", timeout=300)
        self.owner_id = int(owner_id)
        self.role_id = int(role_id)
        self.group_key = str(group_key)
        self.selected = set(selected)
        self.role_name = str(role_name)
        self.confirm = discord.ui.TextInput(
            label="Type the exact role name to confirm",
            placeholder=_clip(role_name, 100),
            max_length=100,
        )
        self.add_item(self.confirm)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild, actor = await _require_role_manager(interaction)
        if guild is None or actor is None:
            return
        role = guild.get_role(self.role_id)
        if not isinstance(role, discord.Role):
            return await _reply(interaction, "❌ That role no longer exists.")
        current_name = str(role.name)
        if current_name != self.role_name:
            return await _reply(
                interaction,
                "❌ That role was renamed after this confirmation opened. Reopen Permissions and review it again.",
            )
        if str(self.confirm.value or "").strip() != current_name:
            return await _reply(interaction, "❌ Confirmation did not match the current role name. Nothing changed.")
        await _apply_permission_selection(
            interaction,
            owner_id=self.owner_id,
            role_id=self.role_id,
            group_key=self.group_key,
            selected=self.selected,
            allow_admin=True,
            from_modal=True,
        )


class DeleteRoleModal(discord.ui.Modal):
    def __init__(self, owner_id: int, role: discord.Role) -> None:
        super().__init__(title="Confirm Role Deletion", timeout=300)
        self.owner_id = int(owner_id)
        self.role_id = int(role.id)
        self.role_name = str(role.name)
        self.confirm = discord.ui.TextInput(
            label="Type the exact role name",
            placeholder=_clip(role.name, 100),
            max_length=100,
        )
        self.add_item(self.confirm)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild, actor = await _require_role_manager(interaction)
        if guild is None or actor is None:
            return
        role = guild.get_role(self.role_id)
        if not isinstance(role, discord.Role):
            return await _reply(interaction, "❌ That role no longer exists.")
        blockers = _role_mutation_blockers(guild, actor, role)
        if blockers:
            return await _reply(interaction, "❌ " + "\n• ".join(blockers))
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        dependencies = await _role_dependencies(guild, role)
        if dependencies:
            return await _reply(
                interaction,
                "❌ This role became a Dank Shield dependency before confirmation. Remap it first.",
            )
        if str(self.confirm.value or "").strip() != str(role.name):
            return await _reply(interaction, "❌ Confirmation did not match the current role name. Nothing was deleted.")

        role_name = str(role.name)
        try:
            async with _role_action_lock(guild.id, role.id, "delete"):
                fresh = guild.get_role(role.id)
                if not isinstance(fresh, discord.Role):
                    return await _reply(interaction, "❌ That role no longer exists.")
                fresh_blockers = _role_mutation_blockers(guild, actor, fresh)
                if fresh_blockers:
                    return await _reply(interaction, "❌ " + "\n• ".join(fresh_blockers))
                fresh_dependencies = await _role_dependencies(guild, fresh)
                if fresh_dependencies:
                    return await _reply(
                        interaction,
                        "❌ This role became a Dank Shield dependency before deletion. Remap it first.",
                    )
                await fresh.delete(reason=_role_reason(f"deleted role {fresh.id}", actor))
        except discord.Forbidden:
            return await _reply(interaction, "❌ Discord denied the role deletion.")
        except discord.HTTPException as exc:
            return await _reply(interaction, f"❌ Discord could not delete the role: {_clip(exc, 300)}")

        await _followup_panel(
            interaction,
            content="✅ Role deletion completed.",
            embed=discord.Embed(
                title="🗑️ Role Deleted",
                description=f"Deleted **{discord.utils.escape_markdown(role_name)}**.",
                color=discord.Color.red(),
            ),
            view=RoleEditorHomeView(self.owner_id),
        )


async def _self_service_role_kind(
    guild: discord.Guild,
    role: discord.Role,
) -> tuple[str, str]:
    """Return (kind, blocker). Empty kind means the role is not self-service."""
    from stoney_verify.guild_config import get_guild_config
    from .public_self_roles_group import (
        PROFILE_CATEGORIES,
        PROFILE_COSMETIC_ROLE_IDS_KEY,
        _all_profile_role_names,
        _can_manage,
        _config_role_ids,
        _profile_cosmetic_role_blocker,
        _role_name_key,
    )
    from .public_toke import SESH_PING_ROLE_KEY, STONER_ROLE_KEY

    config = await get_guild_config(int(guild.id), refresh=True)
    blocker = _profile_cosmetic_role_blocker(guild, role, config)
    if blocker:
        return "", blocker
    manageable, why = _can_manage(role, guild)
    if not manageable:
        return "", why

    rid = int(role.id)
    raw_stoner = str(config.get(STONER_ROLE_KEY) or "0")
    raw_sesh = str(config.get(SESH_PING_ROLE_KEY) or "0")
    stoner_id = int(raw_stoner) if raw_stoner.isdigit() else 0
    sesh_id = int(raw_sesh) if raw_sesh.isdigit() else 0

    if rid == stoner_id and rid == sesh_id and rid > 0:
        return "Community + Notification", ""
    if rid == stoner_id and rid > 0:
        return "Community", ""
    if rid == sesh_id and rid > 0:
        return "Notification", ""

    cosmetic_ids = set(_config_role_ids(config, PROFILE_COSMETIC_ROLE_IDS_KEY))
    if rid in cosmetic_ids:
        return "Profile Tag / Cosmetic", ""

    role_key = _role_name_key(role.name)
    profile_name_keys = {_role_name_key(name) for name in _all_profile_role_names()}
    if role_key in profile_name_keys:
        for _key, (_emoji, label, names, _desc) in PROFILE_CATEGORIES.items():
            if role_key in {_role_name_key(name) for name in names}:
                return str(label), ""
        return "Profile Tag", ""

    return "", "That role is not configured as a member self-service role."


def _self_role_embed(
    member: discord.Member,
    role: discord.Role,
    kind: str,
) -> discord.Embed:
    selected = role in member.roles
    embed = discord.Embed(
        title=f"🏷️ {role.name}",
        description=(
            f"{role.mention} is an approved **{kind}** role in this server.\n\n"
            f"Current status: **{'Selected' if selected else 'Not selected'}**"
        ),
        color=role.colour if role.colour.value else discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="What this means",
        value=(
            "This shortcut uses the same live role mapping and safety checks as Roles & Profiles. "
            "It does not make arbitrary Discord roles self-assignable."
        ),
        inline=False,
    )
    embed.set_footer(text="/role • Dank Shield Roles & Profiles")
    return embed


class SelfServiceRoleView(_OwnedView):
    def __init__(self, owner_id: int, role_id: int) -> None:
        super().__init__(owner_id)
        self.role_id = int(role_id)

    @discord.ui.button(label="Add / Remove Role", emoji="🏷️", style=discord.ButtonStyle.primary, row=0)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if guild is None or member is None:
            return await _reply(interaction, "❌ This only works inside a server.")

        if not interaction.response.is_done():
            await interaction.response.defer()

        from stoney_verify.guild_config import get_guild_config
        from .public_toke import SESH_PING_ROLE_KEY, STONER_ROLE_KEY

        try:
            async with _self_service_role_lock(guild.id, member.id):
                role = guild.get_role(self.role_id)
                if not isinstance(role, discord.Role):
                    return await _reply(interaction, "❌ That role no longer exists.")

                kind, blocker = await _self_service_role_kind(guild, role)
                if not kind:
                    return await _reply(interaction, "❌ " + (blocker or "That role is no longer self-service."))

                config = await get_guild_config(int(guild.id), refresh=True)
                raw_stoner = str(config.get(STONER_ROLE_KEY) or "0")
                raw_sesh = str(config.get(SESH_PING_ROLE_KEY) or "0")
                stoner_id = int(raw_stoner) if raw_stoner.isdigit() else 0
                sesh_id = int(raw_sesh) if raw_sesh.isdigit() else 0

                if role in member.roles:
                    await member.remove_roles(role, reason="Dank Shield /role self-service toggle")
                    if int(role.id) == stoner_id and sesh_id > 0 and sesh_id != stoner_id:
                        sesh_role = guild.get_role(sesh_id)
                        if isinstance(sesh_role, discord.Role) and sesh_role in member.roles:
                            sesh_kind, sesh_blocker = await _self_service_role_kind(guild, sesh_role)
                            if sesh_kind and not sesh_blocker:
                                await member.remove_roles(sesh_role, reason="Dank Shield /role Stoner dependency")
                    result = f"Removed {role.mention}."
                else:
                    if int(role.id) == sesh_id and sesh_id != stoner_id:
                        stoner_role = guild.get_role(stoner_id)
                        if not isinstance(stoner_role, discord.Role) or stoner_role not in member.roles:
                            return await _reply(interaction, "❌ Select the configured Stoner role before enabling Sesh Pings.")
                    await member.add_roles(role, reason="Dank Shield /role self-service toggle")
                    result = f"Added {role.mention}."
        except discord.Forbidden:
            return await _reply(interaction, "❌ Dank Shield cannot manage that role. Staff should check role hierarchy.")
        except discord.HTTPException as exc:
            return await _reply(interaction, f"❌ Discord could not update that role: {_clip(exc, 300)}")

        try:
            from .public_profile_cards import invalidate_member_live_cards
            await invalidate_member_live_cards(interaction.client, guild, member.id)
        except Exception:
            pass

        refreshed = guild.get_role(self.role_id) or role
        await _replace(
            interaction,
            embed=_self_role_embed(member, refreshed, kind),
            view=SelfServiceRoleView(self.owner_id, self.role_id),
            content="✅ " + result,
        )

    @discord.ui.button(label="Roles & Profiles", emoji="↩️", style=discord.ButtonStyle.secondary, row=0)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_roles_profiles_center(interaction)


async def _open_direct_role(
    interaction: discord.Interaction,
    role: discord.Role,
) -> None:
    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if guild is None or member is None:
        return await _reply(interaction, "❌ Roles only work inside a server.")

    if role.is_default():
        return await _reply(interaction, "❌ @everyone cannot be managed from /role.")

    if _actor_can_manage_roles(guild, member):
        blockers = _role_mutation_blockers(guild, member, role)
        if blockers:
            return await _reply(interaction, "❌ " + "\n• ".join(["That role cannot be edited:", *blockers]))
        await _replace(
            interaction,
            embed=await _role_embed(guild, role),
            view=RoleDetailView(int(member.id), role.id),
        )
        return

    kind, blocker = await _self_service_role_kind(guild, role)
    if not kind:
        return await _reply(
            interaction,
            "❌ " + (blocker or "That role is not available for self-service. Open /role to see your approved role tools."),
        )

    await _replace(
        interaction,
        embed=_self_role_embed(member, role, kind),
        view=SelfServiceRoleView(int(member.id), role.id),
    )


async def _role_name_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    """Alias-aware role lookup owned by the shared naming-identity service."""
    from stoney_verify.services.naming_identity import role_autocomplete

    return await role_autocomplete(interaction, current)


@app_commands.describe(
    member="Staff shortcut: open this member in the existing guarded member-role panel.",
    role="Type a normal role name; styled names and saved previous names are searchable.",
)
@app_commands.autocomplete(role=_role_name_autocomplete)
async def open_role_command(
    interaction: discord.Interaction,
    member: Optional[discord.Member] = None,
    role: Optional[str] = None,
) -> None:
    """One smart doorway into the canonical Roles & Profiles runtime."""
    if interaction.guild is None:
        return await _reply(interaction, "❌ /role only works inside a server.")

    role_query = str(role or "").strip()
    if member is not None and role_query:
        return await _reply(interaction, "❌ Choose either a member or a role, not both.")

    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)

    if member is not None:
        from .member_command_center import open_member_target
        await open_member_target(interaction, member)
        return

    if role_query:
        from stoney_verify.services.naming_identity import resolve_role_query

        resolved, error = await resolve_role_query(interaction.guild, role_query)
        if not isinstance(resolved, discord.Role):
            return await _reply(
                interaction,
                "❌ " + (error or "No role matched that name or saved alias."),
            )
        await _open_direct_role(interaction, resolved)
        return

    await open_roles_profiles_center(interaction)


async def open_roles_profiles_center(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    if guild is None:
        return await _reply(interaction, "❌ Roles & Profiles only works inside a server.")

    staff = await _recognized_staff(interaction)
    role_manager = _actor_can_manage_roles(guild, interaction.user)
    setup_manager = _can_manage_setup(interaction)
    embed = _center_embed(
        staff=staff,
        role_manager=role_manager,
        setup_manager=setup_manager,
    )
    try:
        from stoney_verify.member_setup_service import (
            load_guild_setup_state,
            load_member_setup_state,
            member_review_status,
        )

        guild_setup, member_setup = await asyncio.gather(
            load_guild_setup_state(guild.id),
            load_member_setup_state(guild.id, interaction.user.id),
        )
        setup_status = member_review_status(guild_setup, member_setup)
        current_revision = int(setup_status.get("current_revision") or 0)
        if current_revision > 0:
            if setup_status.get("is_current"):
                setup_text = f"✅ Current on revision **{current_revision}**."
            else:
                pending = list(setup_status.get("pending_sections") or [])
                gate_note = " • **Member Access review required**" if setup_status.get("access_gated") else ""
                setup_text = (
                    f"⚠️ Revision **{current_revision}** needs review{gate_note}. "
                    f"Changed sections: **{len(pending)}**. Open **Member Setup**."
                )
            embed.add_field(name="Your Member Setup", value=setup_text, inline=False)
    except Exception:
        pass

    await _replace(
        interaction,
        embed=embed,
        view=RolesProfilesView(
            int(interaction.user.id),
            staff=staff,
            role_manager=role_manager,
            setup_manager=setup_manager,
        ),
    )


__all__ = [
    "RolesProfilesView",
    "RoleEditorHomeView",
    "RoleDetailView",
    "SelfServiceRoleView",
    "PermissionGroupPickerView",
    "PermissionToggleView",
    "open_roles_profiles_center",
    "open_role_command",
    "_actor_can_manage_roles",
    "_self_service_role_kind",
    "_config_dependency_labels",
    "_parse_bool",
    "_parse_colour",
    "_permission_groups",
    "_role_mutation_blockers",
]
