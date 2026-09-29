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
            self.remove_item(self.profile_builder)
        if not self.role_manager:
            self.remove_item(self.server_roles)
            self.remove_item(self.create_role)
            self.remove_item(self.role_health)

    @discord.ui.button(label="My Profile", emoji="🪪", style=discord.ButtonStyle.primary, row=0)
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

    @discord.ui.button(label="Member Role Manager", emoji="👥", style=discord.ButtonStyle.secondary, row=1)
    async def member_roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _recognized_staff(interaction):
            return await _reply(interaction, "❌ Staff only.")
        from .public_member_role_browser import _open_member_browser

        await _open_member_browser(interaction)

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
    