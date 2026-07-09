from __future__ import annotations

"""Profile Builder role editor + member role suggestions.

Extends the existing profile/self-role module without creating a second role
system. Staff can keep using the Profile Builder to add existing safe server
roles to the member profile panel. Members can suggest roles for staff/owner
review, but suggestions never create or assign roles automatically.
"""

import re
from typing import Any, Optional

import discord

_PATCHED = False
_ORIGINAL_HANDLE_PROFILE = None
_ORIGINAL_HANDLE_BUILDER = None
_ORIGINAL_MANAGER_EMBED = None

_RESERVERED_ROLE_WORDS_TYPING_HELPER = ""
_RESERVED_ROLE_WORDS = {
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

PROFILE_ROLES_COSMETICS_LABEL = "Server Roles / Cosmetics"
PROFILE_ROLE_EDITOR_LABEL = "Profile Roles / Cosmetics"


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


def _clean_role_suggestion(value: Any) -> tuple[str, Optional[str]]:
    raw = str(value or "").strip()
    raw = raw.replace("@everyone", "everyone").replace("@here", "here")
    raw = " ".join(raw.split())
    raw = re.sub(r"[^\w\s#+&/().'-]", "", raw, flags=re.UNICODE).strip(" .-/")
    raw = raw[:80]

    if len(raw) < 2:
        return "", "Role name is too short."
    lowered = raw.casefold()
    if "http://" in lowered or "https://" in lowered or "discord.gg" in lowered:
        return "", "Links are not allowed in role suggestions."
    words = {part.strip(" .-/()'\"") for part in re.split(r"\s+", lowered) if part.strip()}
    if words & _RESERVED_ROLE_WORDS:
        return "", "That role name looks like a staff/access/system role. Suggest cosmetic/community roles only."
    return raw, None


def _custom_id(interaction: discord.Interaction) -> str:
    try:
        data = interaction.data if isinstance(interaction.data, dict) else {}
        return str(data.get("custom_id") or "")
    except Exception:
        return ""


def _has_child(view: discord.ui.View, custom_id: str) -> bool:
    for child in list(getattr(view, "children", []) or []):
        try:
            if str(getattr(child, "custom_id", "") or "") == str(custom_id):
                return True
        except Exception:
            continue
    return False


def _button(*, label: str, emoji: str, custom_id: str, row: int, style: discord.ButtonStyle = discord.ButtonStyle.secondary) -> discord.ui.Button:
    return discord.ui.Button(label=label, emoji=emoji, style=style, custom_id=custom_id, row=row)


def _retitle_profile_roles_button(view: discord.ui.View, prefix: str) -> None:
    """Make the old Server Cosmetics button obvious to normal users."""

    for child in list(getattr(view, "children", []) or []):
        try:
            custom_id = str(getattr(child, "custom_id", "") or "")
            if custom_id == f"{prefix}cosmetics":
                child.label = PROFILE_ROLES_COSMETICS_LABEL
                child.emoji = "🧩"
            elif custom_id == f"{prefix}builder:cosmetics":
                child.label = PROFILE_ROLE_EDITOR_LABEL
                child.emoji = "🧩"
        except Exception:
            continue


def _safe_reason(value: Any) -> str:
    text = str(value or "").strip()
    text = text.replace("@everyone", "everyone").replace("@here", "here")
    text = " ".join(text.split())
    return text[:500]


def _role_suggestion_embed(profile: Any, guild: discord.Guild, member: discord.Member, role_name: str, reason: str) -> discord.Embed:
    existing = None
    try:
        existing = profile._find_role_by_name(guild, role_name)
    except Exception:
        existing = None

    embed = discord.Embed(
        title="💡 Profile Role Suggestion",
        description="A member suggested a role for the Profile Builder. Staff/owner review is required.",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Member", value=f"{member.mention}\n`{member}` (`{member.id}`)", inline=False)
    if isinstance(existing, discord.Role):
        embed.add_field(name="Existing role found", value=f"{existing.mention}\n`{existing.name}` (`{existing.id}`)", inline=False)
        action = f"Open `/dank profile builder` → **{PROFILE_ROLE_EDITOR_LABEL}**, then add this existing role if it is safe/cosmetic."
    else:
        embed.add_field(name="Requested role", value=f"`{role_name}`", inline=False)
        action = f"Create the role manually only if appropriate, then open `/dank profile builder` → **{PROFILE_ROLE_EDITOR_LABEL}** and add it as an existing role."
    embed.add_field(name="Reason", value=reason or "No reason provided.", inline=False)
    embed.add_field(
        name="Safety",
        value=(
            "This request does **not** create, assign, or approve a role automatically. "
            "Only add safe cosmetic/community roles. Do not use this for staff, access, verification, moderation, or ticket permissions."
        ),
        inline=False,
    )
    embed.add_field(name="Owner/staff action", value=action, inline=False)
    embed.set_footer(text="Dank Shield Profile Builder • role suggestion review")
    return embed


def _patch_panel_views(profile: Any) -> None:
    original_panel = profile.ProfilePanelView
    original_edit = profile.ProfileEditView
    prefix = profile.PROFILE_PREFIX

    class ProfilePanelViewWithRoleSuggestions(original_panel):
        def __init__(self) -> None:
            super().__init__()
            _retitle_profile_roles_button(self, prefix)
            cid = f"{prefix}suggest_role"
            if not _has_child(self, cid):
                self.add_item(_button(label="Suggest Role", emoji="💡", custom_id=cid, row=2))

    class ProfileEditViewWithRoleSuggestions(original_edit):
        def __init__(self) -> None:
            super().__init__()
            _retitle_profile_roles_button(self, prefix)
            cosmetics_cid = f"{prefix}cosmetics"
            if not _has_child(self, cosmetics_cid):
                self.add_item(_button(label=PROFILE_ROLES_COSMETICS_LABEL, emoji="🧩", custom_id=cosmetics_cid, row=2))
            cid = f"{prefix}suggest_role"
            if not _has_child(self, cid):
                self.add_item(_button(label="Suggest Role", emoji="💡", custom_id=cid, row=2))

    profile.ProfilePanelView = ProfilePanelViewWithRoleSuggestions
    profile.ProfileEditView = ProfileEditViewWithRoleSuggestions


def _patch_builder_view(profile: Any) -> None:
    original_builder = profile.ProfileBuilderView
    prefix = profile.PROFILE_PREFIX

    class ProfileBuilderViewWithRoleEditor(original_builder):
        def __init__(self, *, author_id: int, ready: bool, fixable: bool, title: str) -> None:
            super().__init__(author_id=author_id, ready=ready, fixable=fixable, title=title)
            _retitle_profile_roles_button(self, prefix)
            cid = f"{prefix}builder:role_editor"
            if not _has_child(self, cid):
                self.add_item(_button(label=PROFILE_ROLE_EDITOR_LABEL, emoji="🧩", custom_id=cid, row=1, style=discord.ButtonStyle.primary))

    profile.ProfileBuilderView = ProfileBuilderViewWithRoleEditor


def _patch_embeds(profile: Any) -> None:
    global _ORIGINAL_MANAGER_EMBED
    if _ORIGINAL_MANAGER_EMBED is None:
        _ORIGINAL_MANAGER_EMBED = getattr(profile, "_profile_cosmetic_manager_embed", None)

    original_panel_embed = getattr(profile, "_profile_panel_embed", None)
    original_edit_embed = getattr(profile, "_profile_edit_embed", None)

    async def _profile_role_editor_embed(guild: discord.Guild) -> discord.Embed:
        if callable(_ORIGINAL_MANAGER_EMBED):
            embed = await _ORIGINAL_MANAGER_EMBED(guild)
        else:
            embed = discord.Embed(color=discord.Color.blurple(), timestamp=discord.utils.utcnow())
        embed.title = f"🧩 {PROFILE_ROLE_EDITOR_LABEL}"
        embed.description = (
            "Add existing safe server roles that members may self-select from the Profile Panel. "
            "Members can also suggest roles, but staff/owner must review and add them here."
        )
        embed.add_field(
            name="What this controls",
            value="These are profile/server cosmetic roles members can choose for themselves. They are still real Discord roles, just safety-checked before being offered.",
            inline=False,
        )
        embed.add_field(
            name="Member suggestions",
            value="Suggestions are review-only. They never create or assign roles automatically.",
            inline=False,
        )
        embed.set_footer(text="Dank Shield Profile Builder • existing-role editor")
        return embed

    def _panel_embed_with_role_suggestions(guild: discord.Guild, *args: Any, **kwargs: Any) -> discord.Embed:
        embed = original_panel_embed(guild, *args, **kwargs) if callable(original_panel_embed) else discord.Embed()
        embed.add_field(
            name=PROFILE_ROLES_COSMETICS_LABEL,
            value="Use **Server Roles / Cosmetics** to pick optional server roles offered through the Profile Builder.",
            inline=False,
        )
        embed.add_field(
            name="Suggest a role",
            value="Use **Suggest Role** if a safe community/profile role is missing. Staff/owner reviews it first.",
            inline=False,
        )
        return embed

    def _edit_embed_with_role_suggestions(member: discord.Member, *args: Any, **kwargs: Any) -> discord.Embed:
        embed = original_edit_embed(member, *args, **kwargs) if callable(original_edit_embed) else discord.Embed()
        embed.add_field(
            name=PROFILE_ROLES_COSMETICS_LABEL,
            value="Pick optional server roles/cosmetics, or suggest one the owner should add.",
            inline=False,
        )
        embed.add_field(
            name="Missing role?",
            value="Use **Suggest Role** for profile roles you think the server owner should add.",
            inline=False,
        )
        return embed

    profile._profile_cosmetic_manager_embed = _profile_role_editor_embed
    if callable(original_panel_embed):
        profile._profile_panel_embed = _panel_embed_with_role_suggestions
    if callable(original_edit_embed):
        profile._profile_edit_embed = _edit_embed_with_role_suggestions


def _patch_handlers(profile: Any) -> None:
    global _ORIGINAL_HANDLE_PROFILE, _ORIGINAL_HANDLE_BUILDER
    if _ORIGINAL_HANDLE_PROFILE is None:
        _ORIGINAL_HANDLE_PROFILE = getattr(profile, "_handle_profile_interaction", None)
    if _ORIGINAL_HANDLE_BUILDER is None:
        _ORIGINAL_HANDLE_BUILDER = getattr(profile, "_handle_builder_action", None)

    class ProfileRoleSuggestionModal(discord.ui.Modal, title="Suggest Profile Role"):
        role_name = discord.ui.TextInput(
            label="Role you want added",
            placeholder="Example: Artist, Night Owl, D&D, Horror Fans",
            min_length=2,
            max_length=80,
            required=True,
        )
        reason = discord.ui.TextInput(
            label="Why should this role exist?",
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

            clean, error = _clean_role_suggestion(str(self.role_name.value or ""))
            if error:
                return await profile._reply(interaction, error, ok=False)

            channel = await profile._staff_review_channel(guild)
            if not isinstance(channel, discord.TextChannel):
                return await profile._reply(interaction, "No staff/modlog channel found for role suggestions. Set a modlog channel first.", ok=False)

            try:
                await channel.send(
                    embed=_role_suggestion_embed(profile, guild, member, clean, _safe_reason(self.reason.value)),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                await profile._reply(interaction, f"Role suggestion sent to staff: `{clean}`", ok=True)
            except Exception as exc:
                await profile._reply(interaction, f"Could not send role suggestion: {type(exc).__name__}.", ok=False)

    async def _open_role_editor(interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return await profile._reply(interaction, "This only works inside the server.", ok=False)
        await interaction.response.send_message(
            embed=await profile._profile_cosmetic_manager_embed(guild),
            view=profile.ProfileCosmeticRoleManagerView(author_id=int(interaction.user.id)),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _handle_builder_action_patched(interaction: discord.Interaction, action: str) -> bool:
        if str(action or "") == "role_editor":
            await _open_role_editor(interaction)
            return True
        if callable(_ORIGINAL_HANDLE_BUILDER):
            return await _ORIGINAL_HANDLE_BUILDER(interaction, action)
        return False

    async def _handle_profile_interaction_patched(interaction: discord.Interaction) -> bool:
        custom_id = _custom_id(interaction)
        prefix = profile.PROFILE_PREFIX
        if custom_id == f"{prefix}suggest_role":
            await interaction.response.send_modal(ProfileRoleSuggestionModal())
            return True
        if callable(_ORIGINAL_HANDLE_PROFILE):
            return await _ORIGINAL_HANDLE_PROFILE(interaction)
        return False

    profile._handle_builder_action = _handle_builder_action_patched
    profile._handle_profile_interaction = _handle_profile_interaction_patched


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    try:
        from stoney_verify.commands_ext import public_self_roles_group as profile

        _patch_panel_views(profile)
        _patch_builder_view(profile)
        _patch_embeds(profile)
        _patch_handlers(profile)
        _PATCHED = True
        _log("active; Profile Builder has server roles/cosmetics editor and member role suggestions")
        return True
    except Exception as exc:
        _warn(f"failed: {type(exc).__name__}: {exc}")
        return False


apply()

__all__ = ["apply"]
