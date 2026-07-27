from __future__ import annotations

from typing import Any, Optional

import discord
from discord import app_commands

from .public_members_group import members_group
from .member_role_browser_common import ensure_member_cache, reply_ephemeral, require_review
from .member_role_browser_roster import (
    MemberBrowserHomeView,
    RoleMemberBrowserView,
    role_browser_embed,
)

_REGISTERED = False


async def _open_member_browser(
    interaction: discord.Interaction,
    role: Optional[discord.Role] = None,
) -> None:
    if not await require_review(interaction):
        return
    if role is not None and role.is_default():
        await reply_ephemeral(interaction, "❌ Choose a specific role instead of @everyone.")
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    if role is None:
        embed = discord.Embed(
            title="👥 Member Browser",
            description=(
                "Choose any server role to see its members in a private, paginated moderation roster.\n\n"
                "For example, choose **Unverified** to review everyone still waiting for verification."
            ),
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Available tools",
            value=(
                "• Search and sort role members\n"
                "• Verify, review, message, timeout, kick, or ban one member\n"
                "• Add or remove roles\n"
                "• Safe bulk reminders and role changes"
            ),
            inline=False,
        )
        embed.set_footer(text="The panel is ephemeral and locked to you.")
        await interaction.followup.send(embed=embed, view=MemberBrowserHomeView(interaction.user.id), ephemeral=True)
        return

    warning = await ensure_member_cache(interaction.guild)
    view = RoleMemberBrowserView(owner_id=interaction.user.id, guild=interaction.guild, role=role)
    await interaction.followup.send(
        embed=role_browser_embed(view),
        view=view,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    if warning:
        await interaction.followup.send(f"⚠️ {warning}", ephemeral=True)


def register_public_member_role_browser_commands(bot: Any, tree: Any) -> None:
    global _REGISTERED
    _ = bot, tree
    if _REGISTERED:
        return

    existing = {
        getattr(command, "name", "")
        for command in getattr(members_group, "commands", []) or []
    }
    if "browse" not in existing:
        @members_group.command(
            name="browse",
            description="Browse all members with a role and open staff actions.",
        )
        @app_commands.describe(
            role="Optional role to open immediately, such as Unverified",
        )
        async def browse_members(
            interaction: discord.Interaction,
            role: Optional[discord.Role] = None,
        ) -> None:
            await _open_member_browser(interaction, role)

    _REGISTERED = True
    print(
        "✅ public_member_role_browser: /dank members browse registered"
    )


__all__ = [
    "MemberBrowserHomeView",
    "RoleMemberBrowserView",
    "register_public_member_role_browser_commands",
]
