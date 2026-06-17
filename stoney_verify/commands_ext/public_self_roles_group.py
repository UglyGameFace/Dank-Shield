from __future__ import annotations

from typing import Any, Optional

import discord
from discord import app_commands

from .public_setup_group import _require_setup_permission, stoney_group

SELF_ROLE_PREFIX = "dank:selfrole:v1:"
ROLE_PICKER_PREFIX = "dank:rolepicker:v2:"
CUSTOM_IDENTITY_ROLE_NAME = "Identity: custom / ask staff"
CUSTOM_IDENTITY_ROLE_NAME = "Identity: custom / ask staff"
_ATTACHED = False
_LISTENER_ATTACHED = False

DEFAULT_PRONOUN_ROLE_NAMES: tuple[str, ...] = (
    "Pronouns: he/him",
    "Pronouns: she/her",
    "Pronouns: they/them",
    "Pronouns: he/they",
    "Pronouns: she/they",
    "Pronouns: it/its",
    "Pronouns: any pronouns",
    "Pronouns: no pronouns",
    "Pronouns: ask me",
    "Pronouns: custom",
)

DEFAULT_IDENTITY_ROLE_NAMES: tuple[str, ...] = (
    "Identity: man",
    "Identity: woman",
    "Identity: non-binary",
    "Identity: genderfluid",
    "Identity: agender",
    "Identity: trans",
    "Identity: questioning",
    "Identity: prefer not to say",
    "Identity: custom / ask staff",
)

DEFAULT_INTEREST_ROLE_NAMES: tuple[str, ...] = (
    "Interest: gaming",
    "Interest: memes",
    "Interest: music",
    "Interest: movies",
    "Interest: anime",
    "Interest: smoke lounge",
    "Interest: late-night chat",
)

DEFAULT_NOTIFICATION_ROLE_NAMES: tuple[str, ...] = (
    "Notify: announcements",
    "Notify: events",
    "Notify: giveaways",
    "Notify: bumps",
)

ROLE_PICKER_CATEGORIES: dict[str, tuple[str, str, tuple[str, ...], str]] = {
    "pronouns": ("🪪", "Pronouns", DEFAULT_PRONOUN_ROLE_NAMES, "Pick pronoun roles. Multiple choices are allowed."),
    "identity": ("🌈", "Identity", DEFAULT_IDENTITY_ROLE_NAMES, "Pick optional identity roles. Multiple choices are allowed."),
    "interests": ("🎮", "Interests", DEFAULT_INTEREST_ROLE_NAMES, "Pick interests so members can find similar people."),
    "notifications": ("🔔", "Notifications", DEFAULT_NOTIFICATION_ROLE_NAMES, "Pick optional pings you want."),
}

roles_group = app_commands.Group(
    name="roles",
    description="Post simple self-assignable role menus.",
)


def _can_manage(role: discord.Role, guild: discord.Guild) -> tuple[bool, str]:
    me = guild.me
    if not isinstance(me, discord.Member):
        return False, "Dank Shield could not resolve its bot member."
    try:
        if not me.guild_permissions.manage_roles and not me.guild_permissions.administrator:
            return False, "Dank Shield is missing Manage Roles."
        if role >= me.top_role:
            return False, f"Dank Shield's role must be above {role.mention}."
        if role.is_default() or role.managed:
            return False, f"{role.mention} cannot be self-assigned."
    except Exception:
        return False, "Discord role hierarchy could not be checked."
    return True, ""


def _bot_can_create_roles(guild: discord.Guild) -> tuple[bool, str]:
    me = guild.me
    if not isinstance(me, discord.Member):
        return False, "Dank Shield could not resolve its bot member."
    try:
        if not me.guild_permissions.manage_roles and not me.guild_permissions.administrator:
            return False, "Dank Shield is missing Manage Roles."
    except Exception:
        return False, "Discord role permissions could not be checked."
    return True, ""


def _role_name_key(name: str) -> str:
    return str(name or "").strip().casefold()


def _find_role_by_name(guild: discord.Guild, name: str) -> Optional[discord.Role]:
    target = _role_name_key(name)
    for role in list(getattr(guild, "roles", []) or []):
        if isinstance(role, discord.Role) and _role_name_key(role.name) == target:
            return role
    return None


async def _ensure_role(guild: discord.Guild, name: str, *, reason: str) -> discord.Role:
    existing = _find_role_by_name(guild, name)
    if isinstance(existing, discord.Role):
        return existing
    return await guild.create_role(name=name[:100], mentionable=False, reason=reason)


async def _reply(interaction: discord.Interaction, content: str, *, ok: bool = True) -> None:
    prefix = "✅ " if ok else "❌ "
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(prefix + content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        else:
            await interaction.followup.send(prefix + content, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception:
        pass


def _is_custom_identity_role(role: discord.Role) -> bool:
    try:
        return _role_name_key(role.name) == _role_name_key(CUSTOM_IDENTITY_ROLE_NAME)
    except Exception:
        return False


def _clean_custom_identity_label(value: Any) -> str:
    text = str(value or "").strip()
    text = text.replace("@everyone", "everyone").replace("@here", "here")
    text = " ".join(text.split())
    return text[:80]


async def _custom_identity_staff_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    try:
        from stoney_verify.guild_config import get_guild_config
        from stoney_verify.commands_ext import public_modlog_group as modlog

        cfg = await get_guild_config(int(guild.id), refresh=True)
        channel = modlog._modlog_channel(guild, cfg)
        if isinstance(channel, discord.TextChannel):
            return channel
    except Exception:
        pass

    try:
        for name in ("mod-log", "modlog", "staff-log", "staff", "support"):
            for channel in list(getattr(guild, "text_channels", []) or []):
                raw = str(getattr(channel, "name", "") or "").lower().replace("_", "-").replace(" ", "-")
                if name in raw and isinstance(channel, discord.TextChannel):
                    return channel
    except Exception:
        pass

    return None


async def _send_custom_identity_request(
    interaction: discord.Interaction,
    *,
    requested_label: str,
    role_id: int,
) -> None:
    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None

    if guild is None or member is None:
        await _reply(interaction, "This only works inside the server.", ok=False)
        return

    label = _clean_custom_identity_label(requested_label)
    if not label:
        await _reply(interaction, "Enter the custom identity label you want staff to review.", ok=False)
        return

    channel = await _custom_identity_staff_channel(guild)
    if not isinstance(channel, discord.TextChannel):
        await _reply(
            interaction,
            "I could not find a staff/modlog channel for custom identity requests. Ask staff directly, or set a modlog channel with `/dank modlog pick-channel`.",
            ok=False,
        )
        return

    try:
        me = guild.me
        perms = channel.permissions_for(me) if isinstance(me, discord.Member) else None
        if perms is None or not perms.view_channel or not perms.send_messages or not perms.embed_links:
            await _reply(
                interaction,
                f"I found {channel.mention}, but I am missing View Channel, Send Messages, or Embed Links there. Staff request was not posted.",
                ok=False,
            )
            return
    except Exception:
        pass

    embed = discord.Embed(
        title="🪪 Custom Identity Role Request",
        description=(
            "A member tapped **Identity: custom / ask staff** and requested a custom identity label.\\n\\n"
            "Staff should review this manually before creating or assigning a new role."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Member", value=f"{member.mention}\\n`{member}` (`{member.id}`)", inline=False)
    embed.add_field(name="Requested label", value=f"`{label}`", inline=False)
    embed.add_field(
        name="Staff action",
        value=(
            "If approved, create a normal cosmetic role and add it to the role panel later. "
            "Do not use custom identity roles for staff, tickets, verification, permissions, or access."
        ),
        inline=False,
    )
    embed.set_footer(text=f"Dank Shield self-role custom identity request • role_id={int(role_id)}")

    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        await _reply(interaction, f"Custom identity request sent to staff: `{label}`", ok=True)
    except Exception as exc:
        await _reply(interaction, f"Could not send the custom identity request: {type(exc).__name__}.", ok=False)


class CustomIdentityRequestModal(discord.ui.Modal, title="Request Custom Identity"):
    requested_label = discord.ui.TextInput(
        label="Custom identity label",
        placeholder="Example: genderqueer, demigirl, Two-Spirit, questioning, etc.",
        min_length=2,
        max_length=80,
        required=True,
    )

    def __init__(self, *, role_id: int) -> None:
        super().__init__(timeout=300)
        self.role_id = int(role_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _send_custom_identity_request(
            interaction,
            requested_label=str(self.requested_label.value or ""),
            role_id=self.role_id,
        )


def _all_builder_role_names() -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for _key, payload in ROLE_PICKER_CATEGORIES.items():
        for name in payload[2]:
            clean = str(name or "").strip()
            if clean and clean.casefold() not in seen:
                seen.add(clean.casefold())
                out.append(clean)
    return tuple(out)


def _is_custom_identity_role(role: discord.Role) -> bool:
    return _role_name_key(role.name) == _role_name_key(CUSTOM_IDENTITY_ROLE_NAME)


def _category_roles(guild: discord.Guild, category_key: str) -> list[discord.Role]:
    payload = ROLE_PICKER_CATEGORIES.get(str(category_key or "").lower())
    if not payload:
        return []
    roles: list[discord.Role] = []
    for name in payload[2]:
        role = _find_role_by_name(guild, name)
        if isinstance(role, discord.Role):
            roles.append(role)
    return roles


def _short_role_label(role_name: str) -> str:
    text = str(role_name or "").strip()
    for prefix in ("Pronouns: ", "Identity: ", "Interest: ", "Notify: "):
        if text.casefold().startswith(prefix.casefold()):
            text = text[len(prefix):]
            break
    return text[:100] or "Role"


def _builder_channel_missing(guild: discord.Guild, channel: discord.TextChannel) -> list[str]:
    me = guild.me
    if not isinstance(me, discord.Member):
        return ["Resolve bot member"]
    perms = channel.permissions_for(me)
    missing: list[str] = []
    if not perms.view_channel:
        missing.append("View Channel")
    if not perms.send_messages:
        missing.append("Send Messages")
    if not perms.embed_links:
        missing.append("Embed Links")
    return missing


def _builder_manual_blockers(guild: discord.Guild) -> list[str]:
    me = guild.me
    if not isinstance(me, discord.Member):
        return ["Dank Shield bot member could not be resolved."]
    blockers: list[str] = []
    if not (me.guild_permissions.manage_roles or me.guild_permissions.administrator):
        blockers.append("Dank Shield is missing Manage Roles.")
    for role_name in _all_builder_role_names():
        role = _find_role_by_name(guild, role_name)
        if isinstance(role, discord.Role):
            ok, why = _can_manage(role, guild)
            if not ok:
                blockers.append(why)
                break
    return blockers


def _builder_can_fix_channel(guild: discord.Guild) -> bool:
    me = guild.me
    if not isinstance(me, discord.Member):
        return False
    return bool(me.guild_permissions.manage_channels or me.guild_permissions.administrator)


def _builder_status(guild: discord.Guild, channel: discord.TextChannel) -> tuple[bool, list[str], list[str]]:
    manual = _builder_manual_blockers(guild)
    channel_missing = _builder_channel_missing(guild, channel)
    fixable = channel_missing if channel_missing and _builder_can_fix_channel(guild) else []
    if channel_missing and not fixable:
        manual.append(f"Missing in {channel.mention}: " + ", ".join(channel_missing))
    ready = not manual and not channel_missing
    return ready, fixable, manual


def _role_picker_embed(guild: discord.Guild, *, title: str = "Role Picker") -> discord.Embed:
    embed = discord.Embed(
        title=title[:256],
        description=(
            "Pick optional profile roles using the buttons below. "
            "These roles are cosmetic only and never control server access."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Categories",
        value="\n".join(f"{emoji} **{label}**" for _k, (emoji, label, _names, _desc) in ROLE_PICKER_CATEGORIES.items()),
        inline=False,
    )
    embed.add_field(name="Custom identity", value="Use **Request Custom Identity** if your label is not listed. Staff reviews it manually.", inline=False)
    embed.set_footer(text="Dank Shield role picker")
    return embed


class RolePickerHomeView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="Pronouns", emoji="🪪", custom_id=f"{ROLE_PICKER_PREFIX}open:pronouns", row=0))
        self.add_item(discord.ui.Button(label="Identity", emoji="🌈", custom_id=f"{ROLE_PICKER_PREFIX}open:identity", row=0))
        self.add_item(discord.ui.Button(label="Interests", emoji="🎮", custom_id=f"{ROLE_PICKER_PREFIX}open:interests", row=1))
        self.add_item(discord.ui.Button(label="Notifications", emoji="🔔", custom_id=f"{ROLE_PICKER_PREFIX}open:notifications", row=1))
        self.add_item(discord.ui.Button(label="Clear My Roles", emoji="🧹", style=discord.ButtonStyle.danger, custom_id=f"{ROLE_PICKER_PREFIX}clear", row=2))
        self.add_item(discord.ui.Button(label="Request Custom Identity", emoji="✍️", style=discord.ButtonStyle.primary, custom_id=f"{ROLE_PICKER_PREFIX}custom", row=2))


class RoleCategorySelectView(discord.ui.View):
    def __init__(self, guild: discord.Guild, member: discord.Member, category_key: str) -> None:
        super().__init__(timeout=300)
        options: list[discord.SelectOption] = []
        for role in _category_roles(guild, category_key)[:25]:
            if _is_custom_identity_role(role):
                continue
            options.append(discord.SelectOption(label=_short_role_label(role.name), value=str(role.id), default=role in member.roles))
        if not options:
            options.append(discord.SelectOption(label="No roles available", value="0"))
        self.add_item(discord.ui.Select(
            placeholder="Pick your choices…",
            min_values=0,
            max_values=max(1, len(options)),
            options=options,
            custom_id=f"{ROLE_PICKER_PREFIX}select:{category_key}",
        ))


class CustomIdentityRequestModal(discord.ui.Modal, title="Request Custom Identity"):
    label = discord.ui.TextInput(label="Custom identity label", min_length=2, max_length=80, required=True)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if guild is None or member is None:
            return await _reply(interaction, "This only works inside the server.", ok=False)

        clean = str(self.label.value or "").replace("@everyone", "everyone").replace("@here", "here").strip()[:80]
        if not clean:
            return await _reply(interaction, "Custom identity label was empty.", ok=False)

        try:
            from stoney_verify.guild_config import get_guild_config
            from stoney_verify.commands_ext import public_modlog_group as modlog
            cfg = await get_guild_config(int(guild.id), refresh=True)
            ch = modlog._modlog_channel(guild, cfg)
        except Exception:
            ch = None

        if not isinstance(ch, discord.TextChannel):
            return await _reply(interaction, "No staff/modlog channel found for custom identity requests.", ok=False)

        embed = discord.Embed(title="🪪 Custom Identity Request", color=discord.Color.blurple(), timestamp=discord.utils.utcnow())
        embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=False)
        embed.add_field(name="Requested label", value=f"`{clean}`", inline=False)
        embed.set_footer(text="Review manually before creating/assigning custom roles.")
        await ch.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        await _reply(interaction, f"Custom identity request sent to staff: `{clean}`", ok=True)


class RoleBuilderView(discord.ui.View):
    def __init__(self, *, author_id: int, channel_id: int, ready: bool, fixable: bool, title: str) -> None:
        super().__init__(timeout=300)
        self.author_id = int(author_id)
        self.channel_id = int(channel_id)
        self.title_text = str(title or "Role Picker")[:80]

        if ready:
            self.add_item(discord.ui.Button(label="Post Panel", emoji="🌿", style=discord.ButtonStyle.success, custom_id=f"{ROLE_PICKER_PREFIX}builder_post", row=0))
            self.add_item(discord.ui.Button(label="Preview", emoji="👀", style=discord.ButtonStyle.secondary, custom_id=f"{ROLE_PICKER_PREFIX}builder_preview", row=0))
        elif fixable:
            self.add_item(discord.ui.Button(label="Fix Permissions", emoji="🛠️", style=discord.ButtonStyle.primary, custom_id=f"{ROLE_PICKER_PREFIX}builder_fix", row=0))

        self.add_item(discord.ui.Button(label="Health", emoji="🩺", style=discord.ButtonStyle.secondary, custom_id=f"{ROLE_PICKER_PREFIX}builder_health", row=1))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.author_id:
            await _reply(interaction, "Only the staff member who opened this builder can use it.", ok=False)
            return False
        return True


async def _post_builder(interaction: discord.Interaction, *, title: str = "Role Picker") -> None:
    guild = interaction.guild
    channel = interaction.channel
    if guild is None or not isinstance(channel, discord.TextChannel):
        return await _reply(interaction, "Run this inside the text channel where the panel should be posted.", ok=False)

    ready, fixable, manual = _builder_status(guild, channel)
    embed = discord.Embed(title="🌿 Role Picker Builder", color=discord.Color.green() if ready else discord.Color.gold(), timestamp=discord.utils.utcnow())
    embed.add_field(name="Target channel", value=channel.mention, inline=False)
    embed.add_field(name="Status", value="✅ Ready" if ready else "⚠️ Not ready", inline=False)

    if fixable:
        embed.add_field(name="Fixable now", value="\n".join(f"• {x}" for x in fixable), inline=False)
    if manual:
        embed.add_field(name="Needs manual fix", value="\n".join(f"• {x}" for x in manual), inline=False)

    await interaction.response.send_message(
        embed=embed,
        view=RoleBuilderView(author_id=int(interaction.user.id), channel_id=int(channel.id), ready=ready, fixable=bool(fixable), title=title),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _handle_builder_action(interaction: discord.Interaction, action: str) -> bool:
    guild = interaction.guild
    channel = interaction.channel
    if guild is None or not isinstance(channel, discord.TextChannel):
        await _reply(interaction, "Run builder actions inside the target text channel.", ok=False)
        return True

    ready, fixable, manual = _builder_status(guild, channel)

    if action == "builder_health":
        lines = ["✅ Ready"] if ready else []
        if fixable:
            lines.append("Fixable: " + ", ".join(fixable))
        if manual:
            lines.append("Manual: " + " | ".join(manual))
        if not lines:
            lines.append("No status available.")
        await _reply(interaction, "\n".join(lines), ok=ready)
        return True

    if action == "builder_fix":
        if not fixable:
            await _reply(interaction, "Nothing fixable is missing right now.", ok=True)
            return True
        me = guild.me
        if not isinstance(me, discord.Member):
            await _reply(interaction, "Dank Shield bot member could not be resolved.", ok=False)
            return True
        try:
            await channel.set_permissions(
                me,
                view_channel=True,
                send_messages=True,
                embed_links=True,
                reason=f"Dank Shield role builder permission repair by {interaction.user} ({interaction.user.id})",
            )
            await _reply(interaction, "Fixed channel permissions. Reopen `/dank roles builder`.", ok=True)
        except Exception as exc:
            await _reply(interaction, f"Could not fix permissions: {type(exc).__name__}.", ok=False)
        return True

    if action == "builder_preview":
        await interaction.response.send_message(embed=_role_picker_embed(guild), view=RolePickerHomeView(), ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        return True

    if action == "builder_post":
        if not ready:
            await _reply(interaction, "Builder is not ready. Use Health to see the exact blocker.", ok=False)
            return True
        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=True)
        except Exception:
            pass
        roles, created, reused = await _create_reuse_roles(interaction, guild, _all_builder_role_names(), reason_label="interactive role picker")
        await channel.send(embed=_role_picker_embed(guild), view=RolePickerHomeView(), allowed_mentions=discord.AllowedMentions.none())
        await interaction.followup.send(f"✅ Role picker posted in {channel.mention}.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        await _send_creation_notes(interaction, created=created, reused=reused)
        return True

    return False


async def _handle_role_picker_interaction(interaction: discord.Interaction) -> bool:
    data = interaction.data if isinstance(interaction.data, dict) else {}
    custom_id = str(data.get("custom_id") or "")
    if not custom_id.startswith(ROLE_PICKER_PREFIX):
        return False
    suffix = custom_id[len(ROLE_PICKER_PREFIX):]
    if suffix.startswith("builder_"):
        return await _handle_builder_action(interaction, suffix)

    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if guild is None or member is None:
        await _reply(interaction, "This only works inside the server.", ok=False)
        return True

    if suffix.startswith("open:"):
        category = suffix.split(":", 1)[1]
        payload = ROLE_PICKER_CATEGORIES.get(category)
        title = payload[1] if payload else "Roles"
        desc = payload[3] if payload else "Pick roles."
        await interaction.response.send_message(
            embed=discord.Embed(title=title, description=desc, color=discord.Color.blurple()),
            view=RoleCategorySelectView(guild, member, category),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    if suffix.startswith("select:"):
        category = suffix.split(":", 1)[1]
        values = data.get("values") if isinstance(data.get("values"), list) else []
        selected = {int(v) for v in values if str(v).isdigit() and int(v) > 0}
        category_roles = [r for r in _category_roles(guild, category) if not _is_custom_identity_role(r)]
        add = [r for r in category_roles if int(r.id) in selected and r not in member.roles and _can_manage(r, guild)[0]]
        remove = [r for r in category_roles if int(r.id) not in selected and r in member.roles and _can_manage(r, guild)[0]]
        if add:
            await member.add_roles(*add, reason="Dank Shield role picker")
        if remove:
            await member.remove_roles(*remove, reason="Dank Shield role picker")
        msg = []
        if add:
            msg.append("Added: " + ", ".join(r.mention for r in add))
        if remove:
            msg.append("Removed: " + ", ".join(r.mention for r in remove))
        await _reply(interaction, "\n".join(msg) if msg else "No changes needed.", ok=True)
        return True

    if suffix == "clear":
        roles = []
        for key in ROLE_PICKER_CATEGORIES:
            roles.extend([r for r in _category_roles(guild, key) if r in member.roles and _can_manage(r, guild)[0]])
        if roles:
            await member.remove_roles(*roles, reason="Dank Shield role picker clear")
            await _reply(interaction, "Removed your optional role-picker roles.", ok=True)
        else:
            await _reply(interaction, "No optional role-picker roles to remove.", ok=True)
        return True

    if suffix == "custom":
        await interaction.response.send_modal(CustomIdentityRequestModal())
        return True

    return True


async def _handle_self_role(interaction: discord.Interaction) -> bool:
    try:
        if interaction.type is not discord.InteractionType.component:
            return False
        data = interaction.data if isinstance(interaction.data, dict) else {}
        custom_id = str(data.get("custom_id") or "")
        if not custom_id.startswith(SELF_ROLE_PREFIX):
            return False
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await _reply(interaction, "This only works inside the server.", ok=False)
            return True
        role_id = int(custom_id[len(SELF_ROLE_PREFIX):].split(":", 1)[0])
        role = interaction.guild.get_role(role_id)
        if not isinstance(role, discord.Role):
            await _reply(interaction, "That role no longer exists.", ok=False)
            return True
        if _is_custom_identity_role(role):
            try:
                if not interaction.response.is_done():
                    await interaction.response.send_modal(CustomIdentityRequestModal(role_id=int(role.id)))
                else:
                    await _reply(interaction, "Open the role panel again and tap the custom identity button.", ok=False)
            except Exception as exc:
                await _reply(interaction, f"Could not open the custom identity form: {type(exc).__name__}.", ok=False)
            return True

        if _is_custom_identity_role(role):
            await interaction.response.send_modal(CustomIdentityRequestModal())
            return True

        ok, why = _can_manage(role, interaction.guild)
        if not ok:
            await _reply(interaction, why, ok=False)
            return True
        if role in interaction.user.roles:
            await interaction.user.remove_roles(role, reason="Dank Shield self-role toggle")
            await _reply(interaction, f"Removed {role.mention}.", ok=True)
        else:
            await interaction.user.add_roles(role, reason="Dank Shield self-role toggle")
            await _reply(interaction, f"Added {role.mention}.", ok=True)
        return True
    except Exception as exc:
        try:
            print(f"⚠️ self_roles interaction failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        try:
            await _reply(interaction, f"Self-role failed: {type(exc).__name__}.", ok=False)
        except Exception:
            pass
        return True


async def _self_role_listener(interaction: discord.Interaction) -> None:
    if await _handle_role_picker_interaction(interaction):
        return
    await _handle_self_role(interaction)


class SelfRolePanelView(discord.ui.View):
    def __init__(self, roles: list[discord.Role]) -> None:
        super().__init__(timeout=None)
        for index, role in enumerate(roles[:20]):
            button = discord.ui.Button(
                label=role.name[:80],
                style=discord.ButtonStyle.secondary,
                custom_id=f"{SELF_ROLE_PREFIX}{int(role.id)}",
                row=min(4, index // 4),
            )

            async def callback(interaction: discord.Interaction, _button: discord.ui.Button, rid: int = int(role.id)) -> None:
                _ = _button, rid
                await _handle_self_role(interaction)

            button.callback = callback  # type: ignore[assignment]
            self.add_item(button)


async def _post_panel(interaction: discord.Interaction, channel: discord.TextChannel, title: str, roles: list[discord.Role], *, description: str) -> None:
    embed = discord.Embed(
        title=title[:256],
        description=description[:4000],
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Roles", value="\n".join(f"• {role.mention}" for role in roles)[:1024], inline=False)
    if any(_is_custom_identity_role(role) for role in roles):
        embed.add_field(
            name="Custom identity",
            value=(
                "Tap **Identity: custom / ask staff** to privately request a label that is not listed. "
                "Staff review custom identity requests before adding new roles."
            ),
            inline=False,
        )
    embed.add_field(
        name="Safety note",
        value="These roles are optional and cosmetic only. They should not be used for verification, tickets, moderation, staff permissions, or server access.",
        inline=False,
    )
    embed.set_footer(text="Dank Shield self-role panel")
    try:
        await channel.send(embed=embed, view=SelfRolePanelView(roles), allowed_mentions=discord.AllowedMentions.none())
        await interaction.followup.send(f"✅ Self-role panel posted in {channel.mention}.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception as exc:
        await interaction.followup.send(f"❌ Could not post self-role panel: `{type(exc).__name__}: {str(exc)[:250]}`", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


async def _create_reuse_roles(
    interaction: discord.Interaction,
    guild: discord.Guild,
    names: tuple[str, ...],
    *,
    reason_label: str,
) -> tuple[list[discord.Role], list[str], list[str]]:
    roles: list[discord.Role] = []
    created: list[str] = []
    reused: list[str] = []
    for name in names:
        before = _find_role_by_name(guild, name)
        role = await _ensure_role(guild, name, reason=f"Dank Shield {reason_label} self-role setup by {interaction.user} ({interaction.user.id})")
        check_ok, check_msg = _can_manage(role, guild)
        if not check_ok:
            raise RuntimeError(check_msg)
        roles.append(role)
        (reused if before else created).append(role.name)
    return roles, created, reused


async def _send_creation_notes(interaction: discord.Interaction, *, created: list[str], reused: list[str]) -> None:
    notes: list[str] = []
    if created:
        notes.append("Created: " + ", ".join(f"`{x}`" for x in created))
    if reused:
        notes.append("Reused: " + ", ".join(f"`{x}`" for x in reused))
    if notes:
        try:
            await interaction.followup.send("\n".join(notes)[:1900], ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            pass


@roles_group.command(name="panel", description="Post a simple toggle-role panel with up to five roles.")
@app_commands.describe(
    channel="Where to post the role panel.",
    title="Panel title.",
    role1="First self-assignable role.",
    role2="Optional role.",
    role3="Optional role.",
    role4="Optional role.",
    role5="Optional role.",
)
async def roles_panel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    title: str,
    role1: discord.Role,
    role2: Optional[discord.Role] = None,
    role3: Optional[discord.Role] = None,
    role4: Optional[discord.Role] = None,
    role5: Optional[discord.Role] = None,
) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _reply(interaction, "This command must be used inside a server.", ok=False)
    roles = []
    seen: set[int] = set()
    for role in (role1, role2, role3, role4, role5):
        if not isinstance(role, discord.Role) or int(role.id) in seen:
            continue
        seen.add(int(role.id))
        ok, why = _can_manage(role, interaction.guild)
        if not ok:
            return await _reply(interaction, why, ok=False)
        roles.append(role)
    if not roles:
        return await _reply(interaction, "Pick at least one usable role.", ok=False)
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass
    await _post_panel(
        interaction,
        channel,
        title,
        roles,
        description="Tap a button to toggle the matching role. Tap again to remove it.",
    )


@roles_group.command(name="pronouns", description="Create/reuse pronoun roles and post a pronoun self-role panel.")
@app_commands.describe(
    channel="Where to post the pronoun role panel.",
    title="Optional panel title.",
)
async def roles_pronouns(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    title: str = "Pronoun Roles",
) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _reply(interaction, "This command must be used inside a server.", ok=False)
    ok, why = _bot_can_create_roles(guild)
    if not ok:
        return await _reply(interaction, why, ok=False)
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass

    try:
        roles, created, reused = await _create_reuse_roles(interaction, guild, DEFAULT_PRONOUN_ROLE_NAMES, reason_label="pronoun")
    except Exception as exc:
        return await interaction.followup.send(f"❌ Could not create/reuse pronoun roles: `{type(exc).__name__}: {exc}`", ephemeral=True)

    await _post_panel(
        interaction,
        channel,
        title,
        roles,
        description=(
            "Pick the pronoun role or roles you want shown on your profile in this server. "
            "These are optional, member-controlled roles. Tap again to remove a role."
        ),
    )
    await _send_creation_notes(interaction, created=created, reused=reused)


@roles_group.command(name="identity", description="Create/reuse optional identity roles and post a separate self-role panel.")
@app_commands.describe(
    channel="Where to post the optional identity role panel.",
    title="Optional panel title.",
)
async def roles_identity(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    title: str = "Optional Identity Roles",
) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _reply(interaction, "This command must be used inside a server.", ok=False)
    ok, why = _bot_can_create_roles(guild)
    if not ok:
        return await _reply(interaction, why, ok=False)
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass

    try:
        roles, created, reused = await _create_reuse_roles(interaction, guild, DEFAULT_IDENTITY_ROLE_NAMES, reason_label="identity")
    except Exception as exc:
        return await interaction.followup.send(f"❌ Could not create/reuse identity roles: `{type(exc).__name__}: {exc}`", ephemeral=True)

    await _post_panel(
        interaction,
        channel,
        title,
        roles,
        description=(
            "Optional identity roles are for self-expression only. You can skip this panel entirely, "
            "pick one, pick multiple if they fit, or use custom / ask staff if your label is not listed."
        ),
    )
    await _send_creation_notes(interaction, created=created, reused=reused)

@roles_group.command(name="builder", description="Open the full role picker builder in this channel.")
@app_commands.describe(title="Optional panel title.")
async def roles_builder(
    interaction: discord.Interaction,
    title: str = "Role Picker",
) -> None:
    if not await _require_setup_permission(interaction):
        return
    await _post_builder(interaction, title=title)


@roles_group.command(name="health", description="Check whether Dank Shield can manage a self-assignable role.")
@app_commands.describe(role="Role to test.")
async def roles_health(interaction: discord.Interaction, role: discord.Role) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _reply(interaction, "This command must be used inside a server.", ok=False)
    ok, why = _can_manage(role, interaction.guild)
    await _reply(interaction, f"{role.mention} is ready for self-role panels." if ok else why, ok=ok)


def _attach_group() -> bool:
    global _ATTACHED
    if _ATTACHED:
        return True
    try:
        if stoney_group.get_command("roles") is not None:
            _ATTACHED = True
            return True
    except Exception:
        pass
    try:
        stoney_group.add_command(roles_group)
        _ATTACHED = True
        return True
    except Exception as exc:
        try:
            print(f"⚠️ public_self_roles_group failed attaching /dank roles: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


def register_public_self_roles_group_commands(bot: Any, tree: Any) -> None:
    global _LISTENER_ATTACHED
    _ = tree
    if bot is not None and not _LISTENER_ATTACHED:
        try:
            bot.add_listener(_self_role_listener, "on_interaction")
            _LISTENER_ATTACHED = True
        except Exception as exc:
            try:
                print(f"⚠️ public_self_roles_group listener failed: {type(exc).__name__}: {exc}")
            except Exception:
                pass
    if _attach_group():
        try:
            print("✅ public_self_roles_group: attached /dank roles self-role commands")
        except Exception:
            pass


_attach_group()

__all__ = ["register_public_self_roles_group_commands", "roles_group"]
