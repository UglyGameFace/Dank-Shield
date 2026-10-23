from __future__ import annotations

"""Staff-only mobile Action Builder: CAS-saved drafts, preview, no delivery."""

from typing import Mapping

import discord

from stoney_verify.action_builder_policy import (
    ACTION_BUILDER_KEY, CONDITIONS, ActionBuilderDraft, edit_rule,
    load_action_builder, preview_rules, save_action_builder,
)
from stoney_verify.action_center_service import load_member_actions
from stoney_verify.panel_lifecycle import PRIVATE_MENU_TTL_SECONDS
from .public_staff_scope import scoped_interaction_is_staff


async def _msg(interaction: discord.Interaction, content: str) -> None:
    payload = {"content": content, "ephemeral": True,
               "allowed_mentions": discord.AllowedMentions.none()}
    if interaction.response.is_done():
        await interaction.followup.send(**payload)
    else:
        await interaction.response.send_message(**payload)


async def _defer(interaction: discord.Interaction) -> None:
    if not interaction.response.is_done():
        if interaction.message is None:
            await interaction.response.defer(ephemeral=True, thinking=True)
        else:
            await interaction.response.defer()


async def _replace(interaction: discord.Interaction, *, embed: discord.Embed, view: discord.ui.View) -> None:
    payload = {
        "content": None, "embed": embed, "view": view,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if interaction.response.is_done():
        await interaction.edit_original_response(**payload)
    elif interaction.message is not None:
        await interaction.response.edit_message(**payload)
    else:
        await interaction.response.send_message(**payload, ephemeral=True)


def _label(condition: str) -> str:
    return {"pending_verification": "Verification Pending",
            "member_setup_revision": "Member Setup Revision"}.get(condition, "Unknown")


def _embed(draft: ActionBuilderDraft, *, guild_name: str) -> discord.Embed:
    embed = discord.Embed(
        title="🛠️ Action & Reminder Builder",
        description=(
            f"**{discord.utils.escape_markdown(guild_name[:80])}** • Draft revision {draft.revision}\n"
            "Create safe action rules using **existing completion owners**. "
            "Nothing here sends reminders, changes roles or automatically targets members. "
            "No new scheduler is enabled."
        ),
        color=discord.Color.blurple(),
    )
    for condition in CONDITIONS:
        rule = draft.rule_for(condition)
        embed.add_field(
            name=f"{'🟢 In draft' if rule.included else '⚪ Excluded'} • {_label(condition)}",
            value=(
                f"Audience: **members pending this condition**\n"
                f"Priority: **{rule.severity.title()}**\n"
                f"Schedule: **manual preview** • Delivery: **none**"
                + (f"\nNote: {discord.utils.escape_mentions(discord.utils.escape_markdown(rule.note))}"
                   if rule.note else "")
            ),
            inline=False,
        )
    embed.set_footer(text="Draft only • Cross-server isolated • No automatic messages")
    return embed


def _preview_embed(draft: ActionBuilderDraft, snapshot: object) -> discord.Embed:
    embed = discord.Embed(
        title="🔎 Action Rules • Private Preview",
        description=(
            "**Previewing only your own account in this server.** "
            "No members are scanned or messaged. This does not activate the rules."
        ),
        color=discord.Color.blurple(),
    )
    for item in preview_rules(draft, snapshot):
        name = _label(item.condition)
        status = {
            "pending": "🟠 Condition pending",
            "complete": "✅ Condition complete",
            "unavailable": "⚠️ Source unavailable, cannot determine status",
        }[item.status]
        embed.add_field(
            name=f"{'✅ Draft included' if item.included else '➖ Draft excluded'} • {name}",
            value=f"{status}\nPriority: **{item.severity.title()}**\n"
                  + (f"Draft note: {discord.utils.escape_mentions(discord.utils.escape_markdown(item.note))}"
                     if item.note else "No custom note"),
            inline=False,
        )
    embed.set_footer(text="No delivery • Source truth is rechecked on every preview")
    return embed


class _OwnedStaffView(discord.ui.View):
    def __init__(self, user_id: int, guild_id: int) -> None:
        super().__init__(timeout=PRIVATE_MENU_TTL_SECONDS)
        self.user_id = int(user_id)
        self.guild_id = int(guild_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (
            interaction.guild_id != self.guild_id
            or interaction.user.id != self.user_id
            or not scoped_interaction_is_staff(interaction)
        ):
            await _msg(interaction, "Only authorized server staff can use this Action Builder.")
            return False
        return True


class EditDraftRuleModal(discord.ui.Modal, title="Edit Draft Action Rule"):
    included = discord.ui.TextInput(
        label="Include in draft? on / off", placeholder="off", max_length=3,
    )
    severity = discord.ui.TextInput(
        label="Priority: required / recommended", placeholder="recommended", max_length=11,
    )
    note = discord.ui.TextInput(
        label="Optional reminder text (not sent)", style=discord.TextStyle.paragraph,
        placeholder="Short, useful note for the action", required=False, max_length=160,
    )

    def __init__(self, owner_id: int, guild_id: int, condition: str, draft: ActionBuilderDraft) -> None:
        super().__init__(timeout=300)
        if condition not in CONDITIONS:
            raise ValueError("Unknown builder condition")
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.condition = condition
        self.revision = draft.revision
        current = draft.rule_for(condition)
        self.included.default = "on" if current.included else "off"
        self.severity.default = current.severity
        self.note.default = current.note

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if (
            interaction.user.id != self.owner_id
            or interaction.guild_id != self.guild_id
            or not scoped_interaction_is_staff(interaction)
        ):
            return await _msg(interaction, "Manage Server permission is required.")
        state = str(self.included.value or "").strip().casefold()
        if state not in ("on", "off"):
            return await _msg(interaction, "Use **on** or **off** for inclusion.")
        level = str(self.severity.value or "").strip().casefold()
        try:
            await _defer(interaction)
            source, draft = await load_action_builder(self.guild_id, refresh=True)
            if draft.revision != self.revision:
                return await _msg(interaction, "The draft changed. Reopen Action Builder before editing again.")
            updated = edit_rule(
                draft, condition=self.condition,
                included=state == "on", severity=level,
                note=str(self.note.value or "").strip(),
            )
            applied, _saved = await save_action_builder(
                self.guild_id, expected_config=source, draft=updated,
            )
            if not applied:
                return await _msg(interaction, "Someone else changed the rule. Reopen Action Builder to avoid overwriting it.")
        except (ValueError, TypeError) as exc:
            return await _msg(interaction, str(exc)[:180])
        except Exception:
            return await _msg(interaction, "Couldn't safely save the draft. The existing rules were not changed.")
        await open_action_builder(interaction)


class ActionBuilderView(_OwnedStaffView):
    def __init__(self, owner_id: int, guild_id: int, draft: ActionBuilderDraft) -> None:
        super().__init__(owner_id, guild_id)
        self.draft = draft

    @discord.ui.button(label="Verification Rule", emoji="✅", style=discord.ButtonStyle.primary, row=0)
    async def verification(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(EditDraftRuleModal(
            self.user_id, self.guild_id, "pending_verification", self.draft,
        ))

    @discord.ui.button(label="Member Setup Rule", emoji="🧭", style=discord.ButtonStyle.primary, row=0)
    async def setup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(EditDraftRuleModal(
            self.user_id, self.guild_id, "member_setup_revision", self.draft,
        ))

    @discord.ui.button(label="Preview Conditions", emoji="🔎", style=discord.ButtonStyle.secondary, row=1)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if not isinstance(interaction.user, discord.Member):
            return await _msg(interaction, "Preview is only available in your server.")
        await _defer(interaction)
        try:
            _, draft = await load_action_builder(self.guild_id, refresh=True)
            snapshot = await load_member_actions(interaction.user)
        except Exception:
            return await _msg(interaction, "Unable to preview safely. Try again when the server is available.")
        await _replace(
            interaction, embed=_preview_embed(draft, snapshot),
            view=ActionBuilderPreviewView(self.user_id, self.guild_id),
        )

    @discord.ui.button(label="Refresh Draft", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await open_action_builder(interaction)

    @discord.ui.button(label="Back to Action Inbox", emoji="📋", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        from .public_action_center import open_action_center
        await open_action_center(interaction)


class ActionBuilderPreviewView(_OwnedStaffView):
    @discord.ui.button(label="Back to Draft", emoji="⚙️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await open_action_builder(interaction)


async def open_action_builder(interaction: discord.Interaction) -> None:
    if interaction.guild is None or not scoped_interaction_is_staff(interaction):
        return await _msg(interaction, "Action Builder is for authorized server staff.")
    await _defer(interaction)
    try:
        _, draft = await load_action_builder(interaction.guild.id, refresh=True)
    except Exception:
        return await _msg(interaction, "Action Builder configuration is unavailable. No rules were changed.")
    await _replace(
        interaction, embed=_embed(draft, guild_name=interaction.guild.name),
        view=ActionBuilderView(interaction.user.id, interaction.guild.id, draft),
    )
