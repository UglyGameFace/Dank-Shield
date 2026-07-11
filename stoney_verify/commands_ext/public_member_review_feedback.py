from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Optional

import discord
from discord import app_commands

from .public_members_group import members_group
from stoney_verify.member_review_feedback import (
    feedback_display_value,
    get_latest_member_review_feedback,
    get_latest_source_review_feedback,
    get_member_review_history,
    infer_latest_source_key,
)
from stoney_verify.member_review_ui import build_member_review_view


_REGISTERED = False


def _cfg_role_id(cfg: Any, key: str) -> int:
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return int(str(value))
    except Exception:
        pass

    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return int(str(value))
    except Exception:
        pass

    return 0


async def _can_review(interaction: discord.Interaction) -> bool:
    try:
        if interaction.guild is None or not isinstance(
            interaction.user,
            discord.Member,
        ):
            return False

        perms = interaction.user.guild_permissions
        if (
            perms.administrator
            or perms.manage_guild
            or perms.moderate_members
            or perms.kick_members
        ):
            return True

        try:
            from stoney_verify.guild_config import get_guild_config

            cfg = await get_guild_config(interaction.guild.id)

            staff_ids = {
                role_id
                for role_id in (
                    _cfg_role_id(cfg, "staff_role_id"),
                    _cfg_role_id(cfg, "vc_staff_role_id"),
                )
                if role_id > 0
            }

            return any(
                int(role.id) in staff_ids
                for role in interaction.user.roles
            )
        except Exception:
            return False
    except Exception:
        return False


def _relative_timestamp(value: Any) -> str:
    try:
        raw = str(value or "").strip()
        if not raw:
            return "unknown time"

        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return f"<t:{int(parsed.timestamp())}:R>"
    except Exception:
        return "unknown time"


def _history_embed(
    user: discord.User | discord.Member,
    rows: list[dict[str, Any]],
) -> discord.Embed:
    embed = discord.Embed(
        title="🧠 Member Verdict History",
        description=(
            f"Staff decisions recorded for {user.mention} (`{user.id}`).\n"
            "Newest decision first. Reset entries preserve the audit trail."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )

    if not rows:
        embed.add_field(
            name="History",
            value="No staff verdicts have been recorded for this user.",
            inline=False,
        )
        return embed

    lines: list[str] = []

    for index, row in enumerate(rows[:10], start=1):
        metadata = dict(row.get("metadata") or {})
        label = str(
            metadata.get("verdict_label")
            or metadata.get("verdict")
            or "Unknown"
        )
        actor = str(
            row.get("actor_name")
            or row.get("actor_id")
            or "Unknown staff"
        )
        reason = str(row.get("reason") or "No reason supplied.").strip()
        reason = reason if len(reason) <= 120 else reason[:119] + "…"

        lines.append(
            f"`{index}.` **{label}** • {_relative_timestamp(row.get('created_at'))}\n"
            f"By **{discord.utils.escape_markdown(actor, as_needed=True)}** • {reason}"
        )

    text = "\n\n".join(lines)
    embed.add_field(
        name="Recent Decisions",
        value=text[:1024],
        inline=False,
    )
    embed.set_footer(
        text="Review verdicts are evidence context, not automatic punishment."
    )
    return embed


def _add_context_fields(
    embed: discord.Embed,
    context_fields: list[tuple[str, str, bool]],
) -> None:
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

            embed.add_field(
                name=name,
                value=str(value)[:1024],
                inline=bool(inline),
            )
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
            "**No button on this panel automatically bans, kicks, times out, "
            "or changes roles.**"
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
        embed.add_field(
            name="Current Staff Verdict",
            value=previous_value[:1024],
            inline=False,
        )

    source_value = feedback_display_value(previous_source_feedback)
    if source_key and source_value:
        embed.add_field(
            name="Current Source Verdict",
            value=f"Source: `{source_key}`\n{source_value}"[:1024],
            inline=False,
        )

    embed.add_field(
        name="How to Review",
        value=(
            "Use **Looks Safe**, **Watch**, or **False Positive** for common decisions.\n"
            "Use **More Staff Verdicts** for bots, invite sources, alt links, "
            "or resetting only the review verdict."
        ),
        inline=False,
    )

    embed.set_footer(
        text="Reset Review Verdict does not revoke an existing identity/alt link."
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

    # Remove the old long command name if an earlier module version added it.
    try:
        members_group.remove_command("review-history")
    except Exception:
        pass

    existing = {
        getattr(command, "name", "")
        for command in getattr(members_group, "commands", []) or []
    }

    if "review" not in existing:

        @members_group.command(
            name="review",
            description="Open a member intelligence panel and record a staff verdict.",
        )
        @app_commands.describe(
            member="Member or user to review",
        )
        async def review_member(
            interaction: discord.Interaction,
            member: discord.User,
        ) -> None:
            if not await _can_review(interaction):
                await interaction.response.send_message(
                    "❌ Member review requires a configured staff role or "
                    "Administrator, Manage Server, Moderate Members, or Kick Members.",
                    ephemeral=True,
                )
                return

            if interaction.guild is None:
                await interaction.response.send_message(
                    "❌ This command must be used inside a server.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(
                ephemeral=True,
                thinking=True,
            )

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
                previous_source_task = asyncio.sleep(
                    0,
                    result=None,
                )

            previous_feedback, previous_source_feedback = await asyncio.gather(
                previous_feedback_task,
                previous_source_task,
            )

            context_fields: list[tuple[str, str, bool]] = []

            try:
                from stoney_verify.modlog import _build_member_context_fields

                context_fields = await _build_member_context_fields(
                    interaction.guild,
                    member,
                )
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

    existing = {
        getattr(command, "name", "")
        for command in getattr(members_group, "commands", []) or []
    }

    if "history" not in existing:

        @members_group.command(
            name="history",
            description="View staff verdict history for a member or departed user.",
        )
        @app_commands.describe(
            member="Member or user whose verdict history to inspect",
        )
        async def review_history(
            interaction: discord.Interaction,
            member: discord.User,
        ) -> None:
            if not await _can_review(interaction):
                await interaction.response.send_message(
                    "❌ Staff only.",
                    ephemeral=True,
                )
                return

            await interaction.response.defer(
                ephemeral=True,
                thinking=True,
            )

            rows = await asyncio.to_thread(
                get_member_review_history,
                guild_id=str(interaction.guild_id or 0),
                user_id=str(member.id),
                limit=10,
            )

            await interaction.followup.send(
                embed=_history_embed(member, rows),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    _REGISTERED = True
    print(
        "✅ public_member_review_feedback: mobile member review panel registered"
    )


__all__ = ["register_public_member_review_feedback_commands"]
