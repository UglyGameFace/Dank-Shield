from __future__ import annotations

from typing import Any, Optional

import discord
from discord import app_commands

from .public_setup_group import _require_setup_permission, stoney_group

SELF_ROLE_PREFIX = "dank:selfrole:v1:"
_ATTACHED = False
_LISTENER_ATTACHED = False

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
    embed = discord.Embed(
        title=title[:256],
        description="Tap a button to toggle the matching role. Tap again to remove it.",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Roles", value="\n".join(f"• {role.mention}" for role in roles), inline=False)
    embed.set_footer(text="Dank Shield self-role panel")
    try:
        await channel.send(embed=embed, view=SelfRolePanelView(roles), allowed_mentions=discord.AllowedMentions.none())
        await interaction.followup.send(f"✅ Self-role panel posted in {channel.mention}.", ephemeral=True)
    except Exception as exc:
        await interaction.followup.send(f"❌ Could not post self-role panel: `{type(exc).__name__}: {exc}`", ephemeral=True)


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
    if not _LISTENER_ATTACHED:
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
