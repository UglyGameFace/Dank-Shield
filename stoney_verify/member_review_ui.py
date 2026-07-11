from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import discord

from .member_review_feedback import (
    feedback_display_value,
    record_member_review_feedback,
)


async def _staff_allowed(interaction: discord.Interaction) -> bool:
    try:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
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
            from .guild_config import get_guild_config

            cfg = await get_guild_config(interaction.guild.id)
            staff_ids = {
                int(value)
                for value in (
                    cfg.get("staff_role_id"),
                    cfg.get("vc_staff_role_id"),
                )
                if str(value or "").isdigit()
            }
            return any(int(role.id) in staff_ids for role in interaction.user.roles)
        except Exception:
            return False
    except Exception:
        return False


def _set_field(
    embed: discord.Embed,
    *,
    name: str,
    value: str,
) -> None:
    for index, field in enumerate(embed.fields):
        if str(field.name) == name:
            embed.set_field_at(
                index,
                name=name,
                value=value[:1024],
                inline=False,
            )
            return

    if len(embed.fields) < 25:
        embed.add_field(name=name, value=value[:1024], inline=False)


class ReviewReasonModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        parent_view: "MemberReviewView",
        verdict: str,
        title: str,
    ) -> None:
        super().__init__(title=title[:45], timeout=600)
        self.parent_view = parent_view
        self.verdict = verdict

        self.reason = discord.ui.TextInput(
            label="Reason / evidence",
            placeholder="Explain why staff chose this verdict.",
            min_length=3,
            max_length=500,
            required=True,
            style=discord.TextStyle.paragraph,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.parent_view.submit_feedback(
            interaction,
            verdict=self.verdict,
            reason=str(self.reason.value),
        )


class AltReviewModal(discord.ui.Modal):
    def __init__(
        self,
        *,
        parent_view: "MemberReviewView",
        verdict: str,
        title: str,
    ) -> None:
        super().__init__(title=title[:45], timeout=600)
        self.parent_view = parent_view
        self.verdict = verdict

        self.related_user_id = discord.ui.TextInput(
            label="Related member ID",
            placeholder="Paste the other Discord user ID.",
            min_length=15,
            max_length=22,
            required=True,
        )
        self.reason = discord.ui.TextInput(
            label="Reason / evidence",
            placeholder="Explain the identity connection.",
            min_length=3,
            max_length=500,
            required=True,
            style=discord.TextStyle.paragraph,
        )

        self.add_item(self.related_user_id)
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw_id = str(self.related_user_id.value).strip().strip("<@!>")

        if not raw_id.isdigit():
            await interaction.response.send_message(
                "❌ Related member ID must be a numeric Discord user ID.",
                ephemeral=True,
            )
            return

        await self.parent_view.submit_feedback(
            interaction,
            verdict=self.verdict,
            reason=str(self.reason.value),
            related_user_id=raw_id,
        )


class MemberReviewView(discord.ui.View):
    def __init__(
        self,
        *,
        guild_id: int,
        target_user_id: int,
        target_is_bot: bool,
        source_key: str = "",
        evidence_snapshot: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(timeout=7 * 24 * 60 * 60)
        self.guild_id = int(guild_id)
        self.target_user_id = int(target_user_id)
        self.target_is_bot = bool(target_is_bot)
        self.source_key = str(source_key or "").strip()
        self.evidence_snapshot = dict(evidence_snapshot or {})

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await _staff_allowed(interaction):
            await interaction.response.send_message(
                "❌ Staff review requires Administrator, Manage Server, "
                "Moderate Members, Kick Members, or the configured staff role.",
                ephemeral=True,
            )
            return False

        if interaction.guild_id != self.guild_id:
            await interaction.response.send_message(
                "❌ This review panel belongs to another server.",
                ephemeral=True,
            )
            return False

        return True

    async def _open_reason(
        self,
        interaction: discord.Interaction,
        *,
        verdict: str,
        title: str,
    ) -> None:
        await interaction.response.send_modal(
            ReviewReasonModal(
                parent_view=self,
                verdict=verdict,
                title=title,
            )
        )

    async def _open_alt(
        self,
        interaction: discord.Interaction,
        *,
        verdict: str,
        title: str,
    ) -> None:
        await interaction.response.send_modal(
            AltReviewModal(
                parent_view=self,
                verdict=verdict,
                title=title,
            )
        )

    async def submit_feedback(
        self,
        interaction: discord.Interaction,
        *,
        verdict: str,
        reason: str,
        related_user_id: Optional[str] = None,
    ) -> None:
        if not await _staff_allowed(interaction):
            await interaction.response.send_message(
                "❌ Staff only.",
                ephemeral=True,
            )
            return

        if verdict in {"approved_bot", "suspicious_bot"} and not self.target_is_bot:
            await interaction.response.send_message(
                "❌ This member is not marked by Discord as an official bot. "
                "Use Watch Member or False Positive for human accounts.",
                ephemeral=True,
            )
            return

        if verdict in {"bad_invite_source", "clear_invite_source"} and not self.source_key:
            await interaction.response.send_message(
                "❌ This join has no known invite/source key to review.",
                ephemeral=True,
            )
            return

        evidence = {
            **self.evidence_snapshot,
            "source": "staff_join_audit_buttons",
            "guild_id": str(self.guild_id),
            "target_user_id": str(self.target_user_id),
            "message_id": str(getattr(interaction.message, "id", "") or ""),
            "channel_id": str(interaction.channel_id or ""),
        }

        try:
            result = await asyncio.to_thread(
                record_member_review_feedback,
                guild_id=str(self.guild_id),
                user_id=str(self.target_user_id),
                verdict=verdict,
                created_by=str(interaction.user.id),
                created_by_name=(
                    getattr(interaction.user, "display_name", None)
                    or str(interaction.user)
                ),
                reason=reason,
                evidence=evidence,
                related_user_id=related_user_id,
                source_key=self.source_key,
            )
        except Exception as exc:
            await interaction.response.send_message(
                f"❌ Could not save staff verdict: `{type(exc).__name__}: {exc}`",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"✅ Saved **{result.get('verdict_label', verdict)}** for "
            f"<@{self.target_user_id}>. This records staff context but does "
            "not automatically punish the member.",
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

        try:
            if interaction.message and interaction.message.embeds:
                embed = interaction.message.embeds[0]
                event_row = dict(result.get("member_event") or {})
                _set_field(
                    embed,
                    name="Staff Verdict",
                    value=feedback_display_value(event_row),
                )

                if verdict in {"bad_invite_source", "clear_invite_source"}:
                    _set_field(
                        embed,
                        name="Source Staff Verdict",
                        value=(
                            f"Source: `{self.source_key}`\n"
                            f"Verdict: **{result.get('verdict_label', verdict)}**\n"
                            f"Reason: {reason[:500]}"
                        ),
                    )

                await interaction.message.edit(embed=embed, view=self)
        except Exception:
            pass

    @discord.ui.button(
        label="Looks Safe",
        emoji="✅",
        style=discord.ButtonStyle.success,
        row=0,
    )
    async def looks_safe(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._open_reason(
            interaction,
            verdict="looks_safe",
            title="Mark Member Looks Safe",
        )

    @discord.ui.button(
        label="Watch",
        emoji="👁️",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def watch_member(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._open_reason(
            interaction,
            verdict="watch_member",
            title="Watch Member",
        )

    @discord.ui.button(
        label="False Positive",
        emoji="🧯",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def false_positive(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._open_reason(
            interaction,
            verdict="false_positive",
            title="Mark False Positive",
        )

    @discord.ui.button(
        label="Approved Bot",
        emoji="🤖",
        style=discord.ButtonStyle.success,
        row=0,
    )
    async def approved_bot(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._open_reason(
            interaction,
            verdict="approved_bot",
            title="Approve Official Bot",
        )

    @discord.ui.button(
        label="Suspicious Bot",
        emoji="⚠️",
        style=discord.ButtonStyle.danger,
        row=0,
    )
    async def suspicious_bot(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._open_reason(
            interaction,
            verdict="suspicious_bot",
            title="Flag Suspicious Bot",
        )

    @discord.ui.button(
        label="Bad Source",
        emoji="🚫",
        style=discord.ButtonStyle.danger,
        row=1,
    )
    async def bad_source(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._open_reason(
            interaction,
            verdict="bad_invite_source",
            title="Flag Bad Invite Source",
        )

    @discord.ui.button(
        label="Clear Source",
        emoji="🧼",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def clear_source(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._open_reason(
            interaction,
            verdict="clear_invite_source",
            title="Clear Invite Source",
        )

    @discord.ui.button(
        label="Likely Alt",
        emoji="🟠",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def likely_alt(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._open_alt(
            interaction,
            verdict="likely_alt",
            title="Likely Alt Link",
        )

    @discord.ui.button(
        label="Confirm Alt",
        emoji="🔴",
        style=discord.ButtonStyle.danger,
        row=1,
    )
    async def confirmed_alt(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._open_alt(
            interaction,
            verdict="confirmed_alt",
            title="Confirmed Alt Link",
        )

    @discord.ui.button(
        label="Reset",
        emoji="↩️",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def reset_verdict(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        await self._open_reason(
            interaction,
            verdict="reset",
            title="Reset Member Verdict",
        )


def build_member_review_view(
    *,
    guild_id: int,
    target_user_id: int,
    target_is_bot: bool,
    source_key: str = "",
    evidence_snapshot: Optional[Dict[str, Any]] = None,
) -> MemberReviewView:
    return MemberReviewView(
        guild_id=guild_id,
        target_user_id=target_user_id,
        target_is_bot=target_is_bot,
        source_key=source_key,
        evidence_snapshot=evidence_snapshot,
    )


__all__ = [
    "MemberReviewView",
    "build_member_review_view",
]
