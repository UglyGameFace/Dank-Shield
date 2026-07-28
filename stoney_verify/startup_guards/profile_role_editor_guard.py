from __future__ import annotations

"""Profile Tags guidance and review-only member suggestions.

The native Profile Builder owns the single Profile Tags & Cosmetics manager.
This guard only improves labels/guidance and adds a review-only suggestion
button. It never creates a second manager route or assigns a suggested role.
"""

import re
from typing import Any, Optional

import discord

_PATCHED = False
_ORIGINAL_HANDLE_PROFILE = None
_ORIGINAL_MANAGER_EMBED = None

_RESERVED_TAG_WORDS = {
    "admin",
    "administrator",
    "mod",
    "moderator",
    "staff",
    "owner",
    "manager",
    "verified",
    "unverified",
    "resident",
    "muted",
    "banned",
    "timeout",
    "ticket",
    "everyone",
    "here",
}

PROFILE_TAGS_LABEL = "Profile Tags & Cosmetics"


def _log(message: str) -> None:
    try:
        print(f"✅ profile_role_editor_guard {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ profile_role_editor_guard {message}")
    except Exception:
        pass


def _clean_profile_tag_suggestion(value: Any) -> tuple[str, Optional[str]]:
    raw = str(value or "").strip()
    raw = raw.replace("@everyone", "everyone").replace("@here", "here")
    raw = " ".join(raw.split())
    raw = re.sub(r"[^\w\s#+&/().'-]", "", raw, flags=re.UNICODE).strip(" .-/")
    raw = raw[:80]

    if len(raw) < 2:
        return "", "Profile tag name is too short."
    lowered = raw.casefold()
    if "http://" in lowered or "https://" in lowered or "discord.gg" in lowered:
        return "", "Links are not allowed in profile tag suggestions."
    words = {part.strip(" .-/()'\"") for part in re.split(r"\s+", lowered) if part.strip()}
    if words & _RESERVED_TAG_WORDS:
        return "", "That looks like a staff, access, or system role. Suggest optional community/profile tags only."
    return raw, None


def _safe_reason(value: Any) -> str:
    text = str(value or "").strip()
    text = text.replace("@everyone", "everyone").replace("@here", "here")
    return " ".join(text.split())[:500]


def _custom_id(interaction: discord.Interaction) -> str:
    try:
        data = interaction.data if isinstance(interaction.data, dict) else {}
        return str(data.get("custom_id") or "")
    except Exception:
        return ""


def _has_child(view: discord.ui.View, custom_id: str) -> bool:
    for child in list(getattr(view, "children", []) or []):
        if str(getattr(child, "custom_id", "") or "") == str(custom_id):
            return True
    return False


def _retitle_profile_tags_button(view: discord.ui.View, prefix: str) -> None:
    for child in list(getattr(view, "children", []) or []):
        try:
            custom_id = str(getattr(child, "custom_id", "") or "")
            if custom_id in {f"{prefix}cosmetics", f"{prefix}builder:cosmetics"}:
                child.label = PROFILE_TAGS_LABEL
                child.emoji = "🎭"
        except Exception:
            continue


def _suggest_button(prefix: str, *, row: int) -> discord.ui.Button:
    return discord.ui.Button(
        label="Suggest Profile Tag",
        emoji="💡",
        style=discord.ButtonStyle.secondary,
        custom_id=f"{prefix}suggest_role",
        row=row,
    )


def _suggestion_embed(
    profile: Any,
    guild: discord.Guild,
    member: discord.Member,
    tag_name: str,
    reason: str,
) -> discord.Embed:
    try:
        existing = profile._find_role_by_name(guild, tag_name)
    except Exception:
        existing = None

    embed = discord.Embed(
        title="💡 Profile Tag Suggestion",
        description=(
            "A member suggested an optional profile tag. Staff or the server owner must review it. "
            "This request never creates or assigns a Discord role automatically."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Member", value=f"{member.mention}\n`{member}` (`{member.id}`)", inline=False)
    if isinstance(existing, discord.Role):
        embed.add_field(name="Existing role found", value=f"{existing.mention}\n`{existing.name}` (`{existing.id}`)", inline=False)
        action = f"Open `/dank profile builder` → **{PROFILE_TAGS_LABEL}**, then add it only if it is safe and cosmetic."
    else:
        embed.add_field(name="Requested profile tag", value=f"`{tag_name}`", inline=False)
        action = (
            "Create the role manually only when appropriate, then open "
            f"`/dank profile builder` → **{PROFILE_TAGS_LABEL}** and add the existing role."
        )
    embed.add_field(name="Reason", value=reason or "No reason provided.", inline=False)
    embed.add_field(
        name="Safety",
        value=(
            "Do not use this for staff, access, verification, moderation, ticket, or other permission-bearing roles. "
            "Suggestions are review-only and never approve themselves."
        ),
        inline=False,
    )
    embed.add_field(name="Owner/staff action", value=action, inline=False)
    embed.set_footer(text="Dank Shield Profile Tags • staff-reviewed suggestion")
    return embed


def _patch_panel_views(profile: Any) -> None:
    original_panel = profile.ProfilePanelView
    original_edit = profile.ProfileEditView
    prefix = profile.PROFILE_PREFIX

    class ProfilePanelViewWithTagSuggestions(original_panel):
        def __init__(self) -> None:
            super().__init__()
            _retitle_profile_tags_button(self, prefix)
            custom_id = f"{prefix}suggest_role"
            if not _has_child(self, custom_id):
                self.add_item(_suggest_button(prefix, row=2))

    class ProfileEditViewWithTagSuggestions(original_edit):
        def __init__(self) -> None:
            super().__init__()
            _retitle_profile_tags_button(self, prefix)
            cosmetics_id = f"{prefix}cosmetics"
            if not _has_child(self, cosmetics_id):
                self.add_item(
                    discord.ui.Button(
                        label=PROFILE_TAGS_LABEL,
                        emoji="🎭",
                        style=discord.ButtonStyle.secondary,
                        custom_id=cosmetics_id,
                        row=2,
                    )
                )
            suggest_id = f"{prefix}suggest_role"
            if not _has_child(self, suggest_id):
                self.add_item(_suggest_button(prefix, row=2))

    profile.ProfilePanelView = ProfilePanelViewWithTagSuggestions
    profile.ProfileEditView = ProfileEditViewWithTagSuggestions


def _patch_embeds(profile: Any) -> None:
    global _ORIGINAL_MANAGER_EMBED
    if _ORIGINAL_MANAGER_EMBED is None:
        _ORIGINAL_MANAGER_EMBED = getattr(profile, "_profile_cosmetic_manager_embed", None)

    original_panel_embed = getattr(profile, "_profile_panel_embed", None)
    original_edit_embed = getattr(profile, "_profile_edit_embed", None)

    async def _profile_tags_manager_embed(guild: discord.Guild) -> discord.Embed:
        if callable(_ORIGINAL_MANAGER_EMBED):
            embed = await _ORIGINAL_MANAGER_EMBED(guild)
        else:
            embed = discord.Embed(color=discord.Color.blurple(), timestamp=discord.utils.utcnow())
        embed.title = f"🎭 {PROFILE_TAGS_LABEL}"
        embed.description = (
            "Choose existing safe Discord roles that members may self-select as optional profile tags. "
            "This is separate from the member's ordinary server-role visibility setting."
        )
        embed.add_field(
            name="What belongs here",
            value="Pronouns, identity, interests, community labels, and harmless cosmetics.",
            inline=False,
        )
        embed.add_field(
            name="What never belongs here",
            value="Staff, access, verification, moderation, ticket, or permission-bearing roles.",
            inline=False,
        )
        embed.set_footer(text="Dank Shield Profile Tags • one native manager")
        return embed

    def _panel_embed_with_suggestions(guild: discord.Guild, *args: Any, **kwargs: Any) -> discord.Embed:
        embed = original_panel_embed(guild, *args, **kwargs) if callable(original_panel_embed) else discord.Embed()
        embed.add_field(
            name="Missing profile tag?",
            value="Use **Suggest Profile Tag**. Staff or the owner reviews it before anything is created or offered.",
            inline=False,
        )
        return embed

    def _edit_embed_with_suggestions(member: discord.Member, *args: Any, **kwargs: Any) -> discord.Embed:
        embed = original_edit_embed(member, *args, **kwargs) if callable(original_edit_embed) else discord.Embed()
        embed.add_field(
            name=PROFILE_TAGS_LABEL,
            value="Pick optional self-selected tags and cosmetics, or suggest a missing safe profile tag.",
            inline=False,
        )
        return embed

    profile._profile_cosmetic_manager_embed = _profile_tags_manager_embed
    if callable(original_panel_embed):
        profile._profile_panel_embed = _panel_embed_with_suggestions
    if callable(original_edit_embed):
        profile._profile_edit_embed = _edit_embed_with_suggestions


def _patch_handlers(profile: Any) -> None:
    global _ORIGINAL_HANDLE_PROFILE
    if _ORIGINAL_HANDLE_PROFILE is None:
        _ORIGINAL_HANDLE_PROFILE = getattr(profile, "_handle_profile_interaction", None)

    class ProfileTagSuggestionModal(discord.ui.Modal, title="Suggest Profile Tag"):
        tag_name = discord.ui.TextInput(
            label="Profile tag you want added",
            placeholder="Example: Artist, Night Owl, D&D, Horror Fans",
            min_length=2,
            max_length=80,
            required=True,
        )
        reason = discord.ui.TextInput(
            label="Why should this profile tag exist?",
            placeholder="Optional: who would use it or where it fits",
            max_length=500,
            required=False,
            style=discord.TextStyle.paragraph,
        )

        async def on_submit(self, interaction: discord.Interaction) -> None:
            guild = interaction.guild
            member = interaction.user if isinstance(interaction.user, discord.Member) else None
            if guild is None or member is None:
                return await profile._reply(interaction, "This only works inside the server.", ok=False)

            clean, error = _clean_profile_tag_suggestion(self.tag_name.value)
            if error:
                return await profile._reply(interaction, error, ok=False)

            channel = await profile._staff_review_channel(guild)
            if not isinstance(channel, discord.TextChannel):
                return await profile._reply(
                    interaction,
                    "No staff/modlog channel was found for profile tag suggestions. Set a modlog channel first.",
                    ok=False,
                )

            try:
                await channel.send(
                    embed=_suggestion_embed(profile, guild, member, clean, _safe_reason(self.reason.value)),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                await profile._reply(interaction, f"Profile tag suggestion sent to staff: `{clean}`", ok=True)
            except Exception as exc:
                await profile._reply(interaction, f"Could not send profile tag suggestion: {type(exc).__name__}.", ok=False)

    async def _handle_profile_interaction_patched(interaction: discord.Interaction) -> bool:
        if _custom_id(interaction) == f"{profile.PROFILE_PREFIX}suggest_role":
            await interaction.response.send_modal(ProfileTagSuggestionModal())
            return True
        if callable(_ORIGINAL_HANDLE_PROFILE):
            return await _ORIGINAL_HANDLE_PROFILE(interaction)
        return False

    profile._handle_profile_interaction = _handle_profile_interaction_patched


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.commands_ext import public_self_roles_group as profile

        _patch_panel_views(profile)
        _patch_embeds(profile)
        _patch_handlers(profile)
        _PATCHED = True
        _log("active; native Profile Tags manager retained and review-only suggestions enabled")
        return True
    except Exception as exc:
        _warn(f"failed: {type(exc).__name__}: {exc}")
        return False


apply()

__all__ = ["apply"]
