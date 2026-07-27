from __future__ import annotations

from typing import Any, Optional

import discord
from discord import app_commands

from .public_setup_group import dank_group
from .member_role_browser_common import (
    ensure_member_cache,
    reply_ephemeral,
    require_review,
)

_REGISTERED = False
_MEMBERS_COMMAND: Optional[app_commands.Command] = None


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
    """Open the Live Members category without exposing another slash subcommand."""
    if not await require_review(interaction):
        return
    if role is not None and role.is_default():
        await reply_ephemeral(
            interaction,
            "❌ Choose a specific role instead of @everyone.",
        )
        return

    from .member_command_center import (
        CenterRoleBrowserView,
        LiveMembersMenuView,
    )
    from .member_role_browser_roster import role_browser_embed

    if not interaction.response.is_done():
        if interaction.message is not None:
            await interaction.response.defer()
        else:
            await interaction.response.defer(ephemeral=True, thinking=True)

    quick_roles = await _load_quick_roles(interaction.guild)
    if role is None:
        view = LiveMembersMenuView(
            int(interaction.user.id),
            quick_roles=quick_roles,
        )
        await interaction.edit_original_response(
            embed=view.render_embed(),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return

    warning = await ensure_member_cache(interaction.guild)
    view = CenterRoleBrowserView(
        owner_id=int(interaction.user.id),
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
    """Replace the old members subgroup with one UI-first /dank members command."""
    global _REGISTERED, _MEMBERS_COMMAND
    _ = bot, tree
    if _REGISTERED:
        return

    existing = dank_group.get_command("members")
    if isinstance(existing, app_commands.Group):
        dank_group.remove_command("members")
        existing = None

    if isinstance(existing, app_commands.Command):
        _MEMBERS_COMMAND = existing
    else:

        @dank_group.command(
            name="members",
            description="Open the complete member management command center.",
        )
        async def members_command_center(
            interaction: discord.Interaction,
        ) -> None:
            from .member_command_center import open_member_command_center

            await open_member_command_center(interaction)

        _MEMBERS_COMMAND = members_command_center

    _REGISTERED = True
    print(
        "✅ public_member_role_browser: single /dank members command center registered"
    )


def restore_member_command_after_compaction(bot: Any, tree: Any) -> bool:
    """Restore the proven single Members entry after the global /dank compactor."""
    _ = bot
    command = _MEMBERS_COMMAND
    if not isinstance(command, app_commands.Command):
        return False

    current = dank_group.get_command("members")
    if current is command:
        return True
    if current is not None:
        dank_group.remove_command("members")
    dank_group.add_command(command)

    from .public_command_hub import DANK_PAYLOAD_SAFETY_LIMIT, dank_payload_size

    size = dank_payload_size(tree)
    if size > DANK_PAYLOAD_SAFETY_LIMIT:
        dank_group.remove_command("members")
        raise RuntimeError(
            f"/dank members pushed the compact payload to {size}/{DANK_PAYLOAD_SAFETY_LIMIT}"
        )
    print(
        "✅ public_member_role_browser: /dank members preserved after compaction "
        f"payload={size}/{DANK_PAYLOAD_SAFETY_LIMIT}"
    )
    return True


__all__ = [
    "_load_quick_roles",
    "_open_member_browser",
    "register_public_member_role_browser_commands",
    "restore_member_command_after_compaction",
]
