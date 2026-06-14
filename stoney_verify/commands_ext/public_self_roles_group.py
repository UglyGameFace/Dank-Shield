from __future__ import annotations

from typing import Any, Optional

import discord
from discord import app_commands

from .public_setup_group import _require_setup_permission, stoney_group

SELF_ROLE_PREFIX = "dank:selfrole:v1:"
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
    embed.add_field(
        name="Safety note",
        value="These roles are optional and cosmetic only. They should not be used for verification, tickets, moderation, staff permissions, or server access.",
        inline=False,
    )
    embed.set_footer(text="Dank Shield self-role panel")
    try:
        await channel.send(embed=embed, view=SelfRolePanelView(roles), allowed_mentions=discord.AllowedMentions.none())
        await interaction.followup.send(f"✅ Self-role panel posted in {channel.mention}.", ephemeral=True)
    except Exception as exc:
        await interaction.followup.send(f"❌ Could not post self-role panel: `{type(exc).__name__}: {exc}`", ephemeral=True)


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
