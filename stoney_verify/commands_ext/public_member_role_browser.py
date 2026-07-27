from __future__ import annotations

from typing import Any, Optional

import discord
from discord import app_commands

from .public_members_group import members_group
from .member_role_browser_common import (
    ensure_member_cache,
    reply_ephemeral,
    require_review,
)
from .member_role_browser_roster import (
    MemberBrowserHomeView,
    RoleMemberBrowserView,
    role_browser_embed,
)

_REGISTERED = False


async def _load_quick_roles(guild: discord.Guild) -> list[discord.Role]:
    try:
        from stoney_verify.guild_config import get_guild_config
        from stoney_verify.setup_engine.loader import snapshot_from_config

        cfg = await get_guild_config(int(guild.id))
        snapshot = snapshot_from_config(int(guild.id), cfg)
    except Exception:
        return []

    out: list[discord.Role] = []
    seen: set[int] = set()
    for role_id in (
        snapshot.unverified_role_id,
        snapshot.verified_role_id,
        snapshot.resident_role_id,
        snapshot.effective_member_role_id,
    ):
        role = guild.get_role(int(role_id or 0))
        if not isinstance(role, discord.Role) or role.is_default():
            continue
        if int(role.id) in seen:
            continue
        seen.add(int(role.id))
        out.append(role)
        if len(out) >= 4:
            break
    return out


async def _open_member_browser(
    interaction: discord.Interaction,
    role: Optional[discord.Role] = None,
) -> None:
    if not await require_review(interaction):
        return
    if role is not None and role.is_default():
        await reply_ephemeral(
            interaction,
            "❌ Choose a specific role instead of @everyone.",
        )
        return

    await interaction.response.defer(ephemeral=True, thinking=True)
    quick_roles = await _load_quick_roles(interaction.guild)
    if role is None:
        quick_text = (
            "\n".join(f"• {quick_role.mention}" for quick_role in quick_roles)
            if quick_roles
            else (
                "No configured verification/member roles were resolved. "
                "Use the role picker."
            )
        )
        embed = discord.Embed(
            title="👥 Member Browser",
            description=(
                "Choose any server role to see its members in a private, paginated "
                "moderation roster.\n\n"
                "For example, choose **Unverified** to review everyone still waiting "
                "for verification."
            ),
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(
            name="Configured quick roles",
            value=quick_text[:1024],
            inline=False,
        )
        embed.add_field(
            name="Available tools",
            value=(
                "• Search, filter, and sort role members\n"
                "• Verify, review, message, timeout, kick, or ban one member\n"
                "• Add or remove roles with protected-role checks\n"
                "• Safe bulk reminders and role changes"
            ),
            inline=False,
        )
        embed.set_footer(text="The panel is ephemeral and locked to you.")
        await interaction.edit_original_response(
            embed=embed,
            view=MemberBrowserHomeView(
                interaction.user.id,
                quick_roles=quick_roles,
            ),
        )
        return

    warning = await ensure_member_cache(interaction.guild)
    view = RoleMemberBrowserView(
        owner_id=interaction.user.id,
        guild=interaction.guild,
        role=role,
        quick_roles=quick_roles,
    )
    await interaction.edit_original_response(
        embed=role_browser_embed(view),
        view=view,
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
    print("✅ public_member_role_browser: /dank members browse registered")


__all__ = [
    "MemberBrowserHomeView",
    "RoleMemberBrowserView",
    "register_public_member_role_browser_commands",
]
