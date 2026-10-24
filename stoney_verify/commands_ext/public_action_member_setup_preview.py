from __future__ import annotations

"""Private staff-only, non-sending Member Setup candidate preview."""

import discord

from stoney_verify.action_member_setup_pipeline import (
    CandidatePreviewPage, CandidatePreviewUnavailable,
    preview_member_setup_opt_in_page,
)
from .public_action_builder import _OwnedStaffView, _defer, _msg, _replace
from .public_staff_scope import scoped_interaction_is_staff


def _preview_embed(page: CandidatePreviewPage) -> discord.Embed:
    embed = discord.Embed(
        title="🧭 Member Setup Reminders • Dry Run",
        description=(
            "**Existing opt-in profiles only** · **No delivery enabled**\n"
            "One bounded page, cached members only, authoritative Member Setup "
            "completion/consent and actual PostgreSQL claim history.\n"
            "**This is not a queue, member roster, or permission to send DMs.**"
        ),
        color=discord.Color.blurple(),
    )
    if page.source_status == "no_published_revision":
        embed.add_field(
            name="No active published Member Setup revision",
            value="Nothing was inspected. The canonical Member Setup owner has no active published revision.",
            inline=False,
        )
    else:
        embed.add_field(
            name="Page summary",
            value=(
                f"Profiles inspected: **{page.scanned}** (maximum 5 per page)\n"
                f"Published revision: **{page.revision}**\n"
                "Additional pages: **"
                + ("possible" if page.next_after_user_id is not None else "none on this read")
                + "**"
            ),
            inline=False,
        )
        titles = {
            "preview_due": "🟠 Timing & ledger suggest due (no send)",
            "waiting": "⏳ Still waiting",
            "quiet": "🌙 Quiet hours",
            "complete": "✅ Completed",
            "exempt": "🛡️ Exempt",
            "opted_out": "🔕 Opted out",
            "excluded": "➖ Draft excludes",
            "stale": "🔁 Revision changed",
            "capped": "📊 Guild daily cap",
            "exhausted": "🔒 Attempt limit",
            "blocked": "⛔ Blocked",
            "ledger_unavailable": "⚠️ Claim history unavailable",
            "unavailable": "⚠️ Condition unavailable",
            "not_cached": "❔ Not in member cache",
            "bot": "🤖 Bot account",
        }
        details = "\n".join(
            f"{titles.get(status, '⚠️ Not verified')}: **{count}**"
            for status, count in page.counts
        ) or "No opted-in candidates in this page."
        embed.add_field(name="Read-only outcomes", value=details, inline=False)
    embed.set_footer(
        text="Guild-scoped • No user IDs shown • No claims • No Discord member fetches • No DMs"
    )
    return embed


class CandidatePreviewView(_OwnedStaffView):
    def __init__(self, user_id: int, guild_id: int, page: CandidatePreviewPage):
        super().__init__(user_id, guild_id)
        self.next_after_user_id = page.next_after_user_id
        self.next_page.disabled = self.next_after_user_id is None

    @discord.ui.button(label="Next opt-in page", emoji="➡️", style=discord.ButtonStyle.secondary, row=0)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.next_after_user_id is None:
            return await _msg(interaction, "No additional opt-in page is available.")
        await open_member_setup_candidate_preview(
            interaction, after_user_id=self.next_after_user_id,
        )

    @discord.ui.button(label="Restart preview", emoji="🔄", style=discord.ButtonStyle.secondary, row=0)
    async def restart(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await open_member_setup_candidate_preview(interaction)

    @discord.ui.button(label="Back to Schedule", emoji="↩️", style=discord.ButtonStyle.secondary, row=0)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .public_action_schedule import open_schedule_panel
        await open_schedule_panel(interaction)


async def open_member_setup_candidate_preview(
    interaction: discord.Interaction, *, after_user_id: int = 0,
) -> None:
    if interaction.guild is None or not scoped_interaction_is_staff(interaction):
        return await _msg(interaction, "Server staff access required for this private dry run.")
    if type(after_user_id) is not int or after_user_id < 0:
        return await _msg(interaction, "Invalid page position. Start the preview again.")
    await _defer(interaction)
    try:
        page = await preview_member_setup_opt_in_page(
            interaction.guild, after_user_id=after_user_id,
        )
    except CandidatePreviewUnavailable:
        return await _msg(interaction, "The opted-in candidate or Member Setup source is unavailable. Nothing was sent.")
    except Exception:
        return await _msg(interaction, "The private preview could not be verified. Nothing was sent.")
    await _replace(
        interaction, embed=_preview_embed(page),
        view=CandidatePreviewView(interaction.user.id, interaction.guild.id, page),
    )
