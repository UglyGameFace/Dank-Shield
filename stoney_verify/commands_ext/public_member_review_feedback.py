from __future__ import annotations

import asyncio
from typing import Any, Optional

import discord
from discord import app_commands

from .public_members_group import members_group
from stoney_verify.member_review_feedback import (
    ALT_VERDICTS,
    BOT_VERDICTS,
    SOURCE_VERDICTS,
    VERDICT_LABELS,
    feedback_display_value,
    get_member_review_history,
    infer_latest_source_key,
    record_member_review_feedback,
)


_REGISTERED = False


def _can_review(interaction: discord.Interaction) -> bool:
    try:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return False
        perms = interaction.user.guild_permissions
        return bool(
            perms.administrator
            or perms.manage_guild
            or perms.moderate_members
            or perms.kick_members
        )
    except Exception:
        return False


def _history_embed(
    member: discord.Member,
    rows: list[dict[str, Any]],
) -> discord.Embed:
    embed = discord.Embed(
        title="🧠 Staff Verdict History",
        description=f"Review history for {member.mention} (`{member.id}`).",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )

    if not rows:
        embed.add_field(
            name="History",
            value="No staff verdicts have been recorded for this member.",
            inline=False,
        )
        return embed

    for index, row in enumerate(rows[:10], start=1):
        metadata = dict(row.get("metadata") or {})
        label = str(
            metadata.get("verdict_label")
            or metadata.get("verdict")
            or "Unknown"
        )
        value = feedback_display_value(row) or "No details."
        embed.add_field(
            name=f"{index}. {label}",
            value=value[:1024],
            inline=False,
        )

    return embed


def register_public_member_review_feedback_commands(
    bot: Any,
    tree: Any,
) -> None:
    global _REGISTERED
    _ = bot, tree

    if _REGISTERED:
        return

    existing = {
        getattr(command, "name", "")
        for command in getattr(members_group, "commands", []) or []
    }

    if "review" not in existing:

        @members_group.command(
            name="review",
            description="Record a reversible staff verdict for a member.",
        )
        @app_commands.describe(
            member="Member being reviewed",
            verdict="Staff verdict",
            reason="Why staff chose this verdict",
            related_member="Required for Likely Alt or Confirmed Alt",
        )
        @app_commands.choices(
            verdict=[
                app_commands.Choice(name=label, value=value)
                for value, label in VERDICT_LABELS.items()
            ]
        )
        async def review_member(
            interaction: discord.Interaction,
            member: discord.Member,
            verdict: app_commands.Choice[str],
            reason: str,
            related_member: Optional[discord.Member] = None,
        ) -> None:
            if not _can_review(interaction):
                await interaction.response.send_message(
                    "❌ Member review requires Administrator, Manage Server, "
                    "Moderate Members, or Kick Members.",
                    ephemeral=True,
                )
                return

            verdict_value = str(verdict.value)

            if verdict_value in ALT_VERDICTS and related_member is None:
                await interaction.response.send_message(
                    "❌ Choose a related member for an alt verdict.",
                    ephemeral=True,
                )
                return

            if related_member is not None and related_member.id == member.id:
                await interaction.response.send_message(
                    "❌ A member cannot be linked to themselves.",
                    ephemeral=True,
                )
                return

            if verdict_value in BOT_VERDICTS and not member.bot:
                await interaction.response.send_message(
                    "❌ Discord does not mark this member as an official bot.",
                    ephemeral=True,
                )
                return

            source_key = await asyncio.to_thread(
                infer_latest_source_key,
                guild_id=str(interaction.guild_id or 0),
                user_id=str(member.id),
            )

            if verdict_value in SOURCE_VERDICTS and not source_key:
                await interaction.response.send_message(
                    "❌ No known invite/source key exists for this member.",
                    ephemeral=True,
                )
                return

            try:
                result = await asyncio.to_thread(
                    record_member_review_feedback,
                    guild_id=str(interaction.guild_id or 0),
                    user_id=str(member.id),
                    verdict=verdict_value,
                    created_by=str(interaction.user.id),
                    created_by_name=(
                        getattr(interaction.user, "display_name", None)
                        or str(interaction.user)
                    ),
                    reason=reason,
                    evidence={
                        "source": "dank_members_review_command",
                        "member_is_bot": bool(member.bot),
                    },
                    related_user_id=(
                        str(related_member.id)
                        if related_member is not None
                        else None
                    ),
                    source_key=source_key,
                )
            except Exception as exc:
                await interaction.response.send_message(
                    f"❌ Could not save verdict: `{type(exc).__name__}: {exc}`",
                    ephemeral=True,
                )
                return

            embed = discord.Embed(
                title="✅ Staff Verdict Saved",
                description=(
                    "This records staff context and evidence. "
                    "It does not automatically punish the member."
                ),
                color=discord.Color.green(),
                timestamp=discord.utils.utcnow(),
            )
            embed.add_field(
                name="Member",
                value=f"{member.mention} (`{member.id}`)",
                inline=False,
            )
            embed.add_field(
                name="Verdict",
                value=f"**{result.get('verdict_label', verdict.name)}**",
                inline=False,
            )
            embed.add_field(name="Reason", value=reason[:1024], inline=False)

            if related_member is not None:
                embed.add_field(
                    name="Related Member",
                    value=f"{related_member.mention} (`{related_member.id}`)",
                    inline=False,
                )

            if source_key:
                embed.add_field(
                    name="Source Key",
                    value=f"`{source_key}`",
                    inline=False,
                )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    existing = {
        getattr(command, "name", "")
        for command in getattr(members_group, "commands", []) or []
    }

    if "review-history" not in existing:

        @members_group.command(
            name="review-history",
            description="View recorded staff verdict history for a member.",
        )
        @app_commands.describe(member="Member whose review history to inspect")
        async def review_history(
            interaction: discord.Interaction,
            member: discord.Member,
        ) -> None:
            if not _can_review(interaction):
                await interaction.response.send_message(
                    "❌ Staff only.",
                    ephemeral=True,
                )
                return

            rows = await asyncio.to_thread(
                get_member_review_history,
                guild_id=str(interaction.guild_id or 0),
                user_id=str(member.id),
                limit=10,
            )

            await interaction.response.send_message(
                embed=_history_embed(member, rows),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    _REGISTERED = True
    print("✅ public_member_review_feedback: staff verdict loop registered")


__all__ = ["register_public_member_review_feedback_commands"]
