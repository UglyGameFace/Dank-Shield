from __future__ import annotations

"""Confirmed member cleanup commands for /dank members.

This module attaches a small, explicit cleanup workflow to the existing
`/dank members` group without changing the scan/review console internals.
"""

from typing import Any

import discord
from discord import app_commands

from .common import reply_once
from .public_members_group import members_group
from stoney_verify.members_new.cleanup_service import (
    MemberCleanupRequest,
    execute_member_cleanup,
    validate_member_cleanup,
)

_REGISTERED = False


def _trim(text: str, limit: int = 3900) -> str:
    raw = str(text or "")
    return raw if len(raw) <= limit else raw[: max(0, limit - 1)] + "…"


def _can_cleanup_members(interaction: discord.Interaction) -> bool:
    try:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        perms = interaction.user.guild_permissions
        return bool(perms.administrator or perms.manage_guild or perms.kick_members)
    except Exception:
        return False


async def _require_cleanup_permission(interaction: discord.Interaction) -> bool:
    if interaction.guild is None:
        await reply_once(interaction, {"content": "❌ This must be used inside a server.", "ephemeral": True})
        return False
    if not _can_cleanup_members(interaction):
        await reply_once(
            interaction,
            {"content": "❌ Confirmed cleanup requires Administrator, Manage Server, or Kick Members.", "ephemeral": True},
        )
        return False
    return True


def _result_embed(title: str, description: str, *, ok: bool) -> discord.Embed:
    return discord.Embed(
        title=title,
        description=_trim(description, 3900),
        color=discord.Color.green() if ok else discord.Color.orange(),
    )


class ConfirmMemberCleanupView(discord.ui.View):
    def __init__(self, request: MemberCleanupRequest) -> None:
        super().__init__(timeout=180)
        self.request = request
        self.done = False

    @discord.ui.button(label="Confirm Remove", emoji="✅", style=discord.ButtonStyle.danger)
    async def confirm_remove(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.done:
            return await reply_once(interaction, {"content": "This cleanup request was already handled.", "ephemeral": True})
        if not await _require_cleanup_permission(interaction):
            return
        if interaction.guild is None:
            return
        if int(interaction.user.id) != int(self.request.actor_user_id):
            return await reply_once(interaction, {"content": "Only the staff member who opened this confirmation can confirm it.", "ephemeral": True})

        await interaction.response.defer(ephemeral=True)
        self.done = True
        for child in self.children:
            try:
                child.disabled = True
            except Exception:
                pass
        result = await execute_member_cleanup(interaction.guild, self.request)
        body = (
            f"Target: **{result.target_display_name}** (`{result.target_user_id}`)\n"
            f"Status: **{result.status}**\n\n"
            f"Why: {result.reason_text}"
        )
        if result.warnings:
            body += "\n\nWarnings:\n" + "\n".join(f"• {warning}" for warning in result.warnings[:5])
        await interaction.edit_original_response(embed=_result_embed("🧹 Cleanup Result", body, ok=result.ok), view=self)

    @discord.ui.button(label="Cancel", emoji="✋", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not await _require_cleanup_permission(interaction):
            return
        if int(interaction.user.id) != int(self.request.actor_user_id):
            return await reply_once(interaction, {"content": "Only the staff member who opened this confirmation can cancel it.", "ephemeral": True})
        self.done = True
        for child in self.children:
            try:
                child.disabled = True
            except Exception:
                pass
        await interaction.response.edit_message(
            embed=_result_embed("Cleanup Cancelled", "No action was taken.", ok=False),
            view=self,
        )


@members_group.command(name="cleanup-user", description="Confirm cleanup for one reviewed inactive verified/resident member.")
@app_commands.describe(
    user="The server member to review for confirmed cleanup.",
    reason="Reason stored in Discord audit log and Dank Shield activity history.",
)
async def members_cleanup_user(
    interaction: discord.Interaction,
    user: discord.Member,
    reason: str = "Confirmed inactive verified/resident cleanup",
) -> None:
    if not await _require_cleanup_permission(interaction):
        return
    if interaction.guild is None:
        return

    request = MemberCleanupRequest(
        guild_id=int(interaction.guild.id),
        target_user_id=int(user.id),
        actor_user_id=int(interaction.user.id),
        reason=reason,
    )
    await interaction.response.defer(ephemeral=True, thinking=True)
    validation = await validate_member_cleanup(interaction.guild, request)
    body = (
        f"Target: {user.mention} **{validation.target_display_name}** (`{validation.target_user_id}`)\n"
        f"Status: **{validation.status}**\n\n"
        f"Checks:\n" + "\n".join(f"• {item}" for item in validation.reasons[:8])
    )
    if validation.warnings:
        body += "\n\nWarnings:\n" + "\n".join(f"• {warning}" for warning in validation.warnings[:5])

    if not validation.ok:
        return await interaction.followup.send(
            embed=_result_embed("⛔ Cleanup Blocked", body, ok=False),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    body += (
        "\n\nPress **Confirm Remove** to remove this member from the server. "
        "This action is immediate and will be recorded."
    )
    await interaction.followup.send(
        embed=_result_embed("⚠️ Confirm Member Cleanup", body, ok=False),
        view=ConfirmMemberCleanupView(request),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


def register_public_members_cleanup_group_commands(bot: Any, tree: Any) -> None:
    global _REGISTERED
    _ = bot, tree
    if _REGISTERED:
        return
    try:
        if members_group.get_command("cleanup-user") is None:
            # The decorator already attached the command to members_group. This
            # check mainly keeps logs readable and protects future refactors.
            print("✅ public_members_cleanup_group: /dank members cleanup-user available")
        _REGISTERED = True
    except Exception as e:
        print(f"⚠️ public_members_cleanup_group failed: {repr(e)}")
        raise


__all__ = ["register_public_members_cleanup_group_commands"]
