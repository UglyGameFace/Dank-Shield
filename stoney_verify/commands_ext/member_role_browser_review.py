from __future__ import annotations

import asyncio
from typing import Any, Optional

import discord

from .member_role_browser_common import reply_ephemeral
from stoney_verify.member_review_feedback import (
    feedback_display_value,
    get_latest_member_review_feedback,
    get_latest_source_review_feedback,
    infer_latest_source_key,
)
from stoney_verify.member_review_ui import build_member_review_view


def _add_context_fields(embed: discord.Embed, context_fields: list[tuple[str, str, bool]]) -> None:
    preferred = (
        "Join Intelligence",
        "Evidence & Source",
        "Identity Links",
        "Smart Join Intelligence",
        "Evidence Health",
        "Containment Posture",
    )
    added: set[str] = set()
    for wanted in preferred:
        for name, value, inline in context_fields:
            if name != wanted or name in added:
                continue
            embed.add_field(name=name, value=str(value)[:1024], inline=bool(inline))
            added.add(name)
            break
        if len(added) >= 3:
            break


def _review_embed(
    user: discord.User | discord.Member,
    *,
    context_fields: list[tuple[str, str, bool]],
    previous_feedback: Optional[dict[str, Any]],
    previous_source_feedback: Optional[dict[str, Any]],
    source_key: str,
) -> discord.Embed:
    embed = discord.Embed(
        title="🛡️ Member Intelligence Review",
        description=(
            f"Review {user.mention} (`{user.id}`) before recording a staff verdict.\n\n"
            "**No button on this panel automatically bans, kicks, times out, or changes roles.**"
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    try:
        embed.set_thumbnail(url=user.display_avatar.url)
    except Exception:
        pass
    _add_context_fields(embed, context_fields)

    previous_value = feedback_display_value(previous_feedback)
    if previous_value:
        embed.add_field(name="Current Staff Verdict", value=previous_value[:1024], inline=False)
    source_value = feedback_display_value(previous_source_feedback)
    if source_key and source_value:
        embed.add_field(name="Current Source Verdict", value=f"Source: `{source_key}`\n{source_value}"[:1024], inline=False)
    embed.add_field(
        name="How to Review",
        value=(
            "Use **Looks Safe**, **Watch**, or **False Positive** for common decisions.\n"
            "Use **More Staff Verdicts** for bots, invite sources, alt links, or resetting only the review verdict."
        ),
        inline=False,
    )
    embed.set_footer(text="Reset Review Verdict does not revoke an existing identity/alt link.")
    return embed


async def open_review_panel(interaction: discord.Interaction, member: discord.User | discord.Member) -> None:
    if interaction.guild is None:
        await reply_ephemeral(interaction, "❌ This must be used inside a server.")
        return
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)

    source_key = await asyncio.to_thread(
        infer_latest_source_key,
        guild_id=str(interaction.guild.id),
        user_id=str(member.id),
    )
    previous_feedback_task = asyncio.to_thread(
        get_latest_member_review_feedback,
        guild_id=str(interaction.guild.id),
        user_id=str(member.id),
    )
    if source_key:
        previous_source_task = asyncio.to_thread(
            get_latest_source_review_feedback,
            guild_id=str(interaction.guild.id),
            source_key=source_key,
        )
    else:
        previous_source_task = asyncio.sleep(0, result=None)
    previous_feedback, previous_source_feedback = await asyncio.gather(
        previous_feedback_task,
        previous_source_task,
    )

    context_fields: list[tuple[str, str, bool]] = []
    try:
        from stoney_verify.modlog import _build_member_context_fields

        context_fields = await _build_member_context_fields(interaction.guild, member)
    except Exception:
        context_fields = []

    view = build_member_review_view(
        guild_id=int(interaction.guild.id),
        target_user_id=int(member.id),
        target_is_bot=bool(member.bot),
        source_key=source_key,
        evidence_snapshot={
            "source": "dank_members_review_panel",
            "target_user_id": str(member.id),
            "target_is_bot": bool(member.bot),
            "source_key": source_key,
        },
    )
    await interaction.followup.send(
        embed=_review_embed(
            member,
            context_fields=context_fields,
            previous_feedback=previous_feedback,
            previous_source_feedback=previous_source_feedback,
            source_key=source_key,
        ),
        view=view,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


__all__ = ["open_review_panel"]
