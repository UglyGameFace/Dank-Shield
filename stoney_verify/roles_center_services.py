from __future__ import annotations

"""Owned service entrypoints for Dank Shield Roles Center.

This lets /dank setup Feature Centers post pronoun/identity self-role panels
without requiring admins to remember separate slash-command syntax.
"""

from typing import Any

import discord


async def _send_ephemeral(interaction: discord.Interaction, content: str = "", **kwargs: Any) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(content, ephemeral=True, **kwargs)
        else:
            await interaction.followup.send(content, ephemeral=True, **kwargs)
    except Exception:
        pass


async def _defer(interaction: discord.Interaction) -> None:
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass


async def _post_default_panel(interaction: discord.Interaction, channel: discord.TextChannel, *, kind: str) -> None:
    from stoney_verify.commands_ext import public_self_roles_group as roles
    from stoney_verify.commands_ext.public_setup_group import _require_setup_permission

    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _send_ephemeral(interaction, "❌ This must be used inside a server.")
    ok, why = roles._bot_can_create_roles(guild)
    if not ok:
        return await _send_ephemeral(interaction, f"❌ {why}")
    await _defer(interaction)

    if kind == "identity":
        names = roles.DEFAULT_IDENTITY_ROLE_NAMES
        title = "Optional Identity Roles"
        reason_label = "identity"
        description = (
            "Pick only the optional identity role or roles you want shown in this server. "
            "These are cosmetic, member-controlled, and should never be required for access."
        )
    else:
        names = roles.DEFAULT_PRONOUN_ROLE_NAMES
        title = "Pronoun Roles"
        reason_label = "pronoun"
        description = (
            "Pick the pronoun role or roles you want shown on your profile in this server. "
            "These are optional, member-controlled roles. Tap again to remove a role."
        )

    try:
        panel_roles, created, reused = await roles._create_reuse_roles(interaction, guild, names, reason_label=reason_label)
    except Exception as exc:
        return await interaction.followup.send(
            f"❌ Could not create/reuse {reason_label} roles: `{type(exc).__name__}: {exc}`",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    await roles._post_panel(interaction, channel, title, panel_roles, description=description)
    await roles._send_creation_notes(interaction, created=created, reused=reused)


class RolePanelChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, *, kind: str) -> None:
        self.kind = kind
        label = "pronoun" if kind != "identity" else "optional identity"
        super().__init__(
            placeholder=f"Choose channel for {label} self-role panel...",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text],
            custom_id=f"dank_setup_roles:{kind}:channel",
        )

    async def callback(self, interaction: discord.Interaction) -> None:  # type: ignore[override]
        from stoney_verify.commands_ext.public_setup_group import _require_setup_permission

        if not await _require_setup_permission(interaction):
            return
        channel = self.values[0] if self.values else None
        if not isinstance(channel, discord.TextChannel):
            return await _send_ephemeral(interaction, "❌ Pick a normal text channel.")
        await _post_default_panel(interaction, channel, kind=self.kind)


class RolesCenterView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=900)
        self.add_item(RolePanelChannelSelect(kind="pronouns"))
        self.add_item(RolePanelChannelSelect(kind="identity"))


async def open_roles_center(interaction: discord.Interaction) -> None:
    from stoney_verify.commands_ext.public_setup_group import _require_setup_permission

    if not await _require_setup_permission(interaction):
        return
    embed = discord.Embed(
        title="🎭 Roles Center",
        description=(
            "Create/reuse optional self-role panels from setup.\n\n"
            "Recommended default: post **Pronoun Roles** after verification, not before access. "
            "Identity roles are optional and should only be used if your community wants them."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Safety rules",
        value=(
            "• Self roles are cosmetic only.\n"
            "• Do not use pronoun/identity roles for verification, tickets, moderation, staff permissions, or access gates.\n"
            "• The bot role must be above any role it creates or manages."
        ),
        inline=False,
    )
    embed.add_field(name="Actions", value="Use the selectors below to post a Pronoun panel or Optional Identity panel.", inline=False)
    embed.set_footer(text="/dank setup • Feature Centers • Roles Center")
    await _send_ephemeral(interaction, embed=embed, view=RolesCenterView(), allowed_mentions=discord.AllowedMentions.none())


__all__ = ["open_roles_center", "RolesCenterView", "RolePanelChannelSelect"]
