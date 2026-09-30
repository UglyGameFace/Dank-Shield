from __future__ import annotations

"""Guild-scoped member setup, revision review, and optional access gating."""

import asyncio
import weakref
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

import discord

from stoney_verify.member_setup_service import (
    ACCESS_MODE_NORMAL,
    ACCESS_MODE_STRICT,
    SECTION_LABELS,
    SETUP_SECTIONS,
    SEVERITY_ACCESS_GATED,
    SEVERITY_MINOR,
    SEVERITY_RECOMMENDED,
    SEVERITY_REQUIRED,
    configure_guild_setup,
    confirm_current_choices,
    default_member_setup_state,
    latest_revision,
    load_guild_member_setup_states,
    load_guild_setup_state,
    load_member_setup_state,
    member_review_status,
    normalize_sections,
    publish_revision,
)
from stoney_verify.ui.resource_browser import DankGuildResourceBrowserView
from .public_setup_group import _require_setup_permission


_DANGEROUS_ACCESS_PERMS: tuple[str, ...] = (
    "administrator",
    "manage_guild",
    "manage_roles",
    "manage_channels",
    "kick_members",
    "ban_members",
    "moderate_members",
    "manage_messages",
    "mention_everyone",
    "manage_webhooks",
    "view_audit_log",
    "manage_nicknames",
    "manage_events",
    "manage_threads",
)

_RECONCILE_LOCKS: weakref.WeakValueDictionary[int, asyncio.Lock] = weakref.WeakValueDictionary()
_RUNTIME_INSTALLED = False


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _clip(value: Any, limit: int = 300) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


async def _reply(interaction: discord.Interaction, content: str, *, ok: Optional[bool] = None) -> None:
    prefix = ""
    if ok is True:
        prefix = "✅ "
    elif ok is False:
        prefix = "❌ "
    kwargs = {
        "content": prefix + str(content),
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if not interaction.response.is_done():
        await interaction.response.send_message(**kwargs)
    else:
        await interaction.followup.send(**kwargs)


async def _replace(
    interaction: discord.Interaction,
    *,
    embed: discord.Embed,
    view: discord.ui.View,
    content: str = "",
) -> None:
    kwargs = {
        "content": content or None,
        "embed": embed,
        "view": view,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if not interaction.response.is_done():
        if interaction.message is not None:
            await interaction.response.edit_message(**kwargs)
        else:
            await interaction.response.send_message(**kwargs, ephemeral=True)
    else:
        await interaction.edit_original_response(**kwargs)


async def _defer(interaction: discord.Interaction) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)


async def _defer_panel_update(interaction: discord.Interaction) -> None:
    """Acknowledge an admin panel action without creating a second response panel."""

    if interaction.response.is_done():
        return
    if interaction.message is not None:
        await interaction.response.defer(thinking=False)
    else:
        await interaction.response.defer(ephemeral=True, thinking=True)


def _resolve_channel(guild: discord.Guild, value: Any) -> Optional[discord.TextChannel]:
    channel_id = _safe_int(value, 0)
    channel = guild.get_channel(channel_id) if channel_id > 0 else None
    return channel if isinstance(channel, discord.TextChannel) else None


def _resolve_role(guild: discord.Guild, value: Any) -> Optional[discord.Role]:
    role_id = _safe_int(value, 0)
    role = guild.get_role(role_id) if role_id > 0 else None
    return role if isinstance(role, discord.Role) else None


def _access_role_blocker(guild: discord.Guild, role: discord.Role) -> str:
    if role.is_default():
        return "@everyone cannot be the Member Access role."
    if role.managed:
        return "Integration-managed roles cannot be used as the Member Access role."

    me = guild.me
    if not isinstance(me, discord.Member):
        return "Dank Shield could not resolve its bot member."
    perms = me.guild_permissions
    if not (perms.manage_roles or perms.administrator):
        return "Dank Shield needs Manage Roles."
    if role >= me.top_role:
        return f"Move Dank Shield's role above {role.mention}."

    dangerous = [
        name.replace("_", " ").title()
        for name in _DANGEROUS_ACCESS_PERMS
        if bool(getattr(role.permissions, name, False))
    ]
    if dangerous:
        return "Access role has dangerous server permissions: " + ", ".join(dangerous[:6])
    return ""


def _member_exempt(member: discord.Member) -> bool:
    guild = member.guild
    if member.bot or int(member.id) == int(getattr(guild, "owner_id", 0) or 0):
        return True
    try:
        # Administrator bypasses channel permission overwrites. Manage Guild and
        # Manage Roles do not, so those staff members still need Member Access.
        return bool(member.guild_permissions.administrator)
    except Exception:
        return False


def _prerequisite_ready(member: discord.Member, state: Mapping[str, Any]) -> bool:
    role_id = _safe_int(state.get("prerequisite_role_id"), 0)
    if role_id <= 0:
        return True
    return any(int(getattr(role, "id", 0) or 0) == role_id for role in member.roles)


def _tri_state(value: Any) -> str:
    if value is True:
        return "allow"
    if value is False:
        return "deny"
    return "inherit"


def _from_tri_state(value: Any) -> Optional[bool]:
    clean = str(value or "").strip().lower()
    if clean == "allow":
        return True
    if clean == "deny":
        return False
    return None


def _category_snapshot(
    category: discord.CategoryChannel,
    *,
    everyone: discord.Role,
    access_role: discord.Role,
) -> dict[str, str]:
    everyone_ow = category.overwrites_for(everyone)
    access_ow = category.overwrites_for(access_role)
    return {
        "everyone_view_channel": _tri_state(everyone_ow.view_channel),
        "access_view_channel": _tri_state(access_ow.view_channel),
    }


async def _set_category_gate(
    category: discord.CategoryChannel,
    *,
    everyone: discord.Role,
    access_role: discord.Role,
    reason: str,
) -> None:
    everyone_ow = category.overwrites_for(everyone)
    everyone_ow.view_channel = False
    await category.set_permissions(everyone, overwrite=everyone_ow, reason=reason)

    access_ow = category.overwrites_for(access_role)
    access_ow.view_channel = True
    await category.set_permissions(access_role, overwrite=access_ow, reason=reason)


async def _restore_category_gate(
    category: discord.CategoryChannel,
    *,
    everyone: discord.Role,
    access_role: Optional[discord.Role],
    snapshot: Mapping[str, Any],
    reason: str,
) -> None:
    everyone_ow = category.overwrites_for(everyone)
    everyone_ow.view_channel = _from_tri_state(snapshot.get("everyone_view_channel"))
    await category.set_permissions(everyone, overwrite=everyone_ow, reason=reason)

    if isinstance(access_role, discord.Role):
        access_ow = category.overwrites_for(access_role)
        access_ow.view_channel = _from_tri_state(snapshot.get("access_view_channel"))
        await category.set_permissions(access_role, overwrite=access_ow, reason=reason)


def gate_health(guild: discord.Guild, state: Mapping[str, Any]) -> dict[str, Any]:
    blockers: list[str] = []
    warnings: list[str] = []

    channel = _resolve_channel(guild, state.get("setup_channel_id"))
    access_role = _resolve_role(guild, state.get("access_role_id"))
    prerequisite = _resolve_role(guild, state.get("prerequisite_role_id"))

    if channel is None:
        blockers.append("Choose the permanent Member Setup text channel.")
    else:
        me = guild.me
        if not isinstance(me, discord.Member):
            blockers.append("Dank Shield bot member could not be resolved.")
        else:
            perms = channel.permissions_for(me)
            if not (perms.view_channel and perms.send_messages and perms.embed_links):
                blockers.append("Dank Shield needs View Channel, Send Messages, and Embed Links in the setup channel.")
        default_perms = channel.permissions_for(guild.default_role)
        if not default_perms.view_channel:
            blockers.append("The setup channel must remain visible without Member Access so gated members can recover.")

    if access_role is None:
        blockers.append("Choose or create a dedicated Member Access role.")
    else:
        blocker = _access_role_blocker(guild, access_role)
        if blocker:
            blockers.append(blocker)

    if state.get("prerequisite_role_id") and prerequisite is None:
        blockers.append("The configured prerequisite role no longer exists.")

    if (
        isinstance(access_role, discord.Role)
        and isinstance(prerequisite, discord.Role)
        and int(access_role.id) == int(prerequisite.id)
    ):
        blockers.append(
            "Member Access and Eligibility Prerequisite must be different roles. "
            "Otherwise members would need the Access role before Dank Shield can grant it."
        )

    protected: list[discord.CategoryChannel] = []
    for raw_id in list(state.get("protected_category_ids") or []):
        channel_obj = guild.get_channel(_safe_int(raw_id, 0))
        if isinstance(channel_obj, discord.CategoryChannel):
            protected.append(channel_obj)
        else:
            warnings.append(f"Protected category {raw_id} no longer exists.")

    if not protected:
        blockers.append("Choose at least one member category for the access gate.")

    if not state.get("gate_active") and str(state.get("gate_transition") or "") != "suspending":
        for category in protected:
            try:
                visible_to_everyone = bool(category.permissions_for(guild.default_role).view_channel)
            except Exception:
                visible_to_everyone = False
            try:
                visible_to_prerequisite = bool(
                    isinstance(prerequisite, discord.Role)
                    and category.permissions_for(prerequisite).view_channel
                )
            except Exception:
                visible_to_prerequisite = False
            if not (visible_to_everyone or visible_to_prerequisite):
                blockers.append(
                    f"{category.name} is currently private from both @everyone"
                    + (
                        f" and {prerequisite.name}"
                        if isinstance(prerequisite, discord.Role)
                        else ""
                    )
                    + ". Granting Member Access there would widen visibility; choose a normal member category or configure its existing member prerequisite first."
                )

    if channel is not None and channel.category is not None:
        if any(int(category.id) == int(channel.category.id) for category in protected):
            blockers.append("The Member Setup channel cannot live inside a protected category.")

    unsynced_children: list[str] = []
    for category in protected:
        for child in list(getattr(category, "channels", []) or []):
            try:
                if not bool(getattr(child, "permissions_synced", False)):
                    unsynced_children.append(f"{category.name}/{getattr(child, 'name', child.id)}")
            except Exception:
                unsynced_children.append(f"{category.name}/{getattr(child, 'name', 'unknown')}")
    if unsynced_children:
        blockers.append(
            "Protected categories contain unsynced child permissions: "
            + ", ".join(unsynced_children[:5])
            + ("…" if len(unsynced_children) > 5 else "")
        )

    me = guild.me
    if isinstance(me, discord.Member):
        if not (me.guild_permissions.manage_channels or me.guild_permissions.administrator):
            blockers.append("Dank Shield needs Manage Channels to apply or restore category gate permissions.")

    bypass_count = 0
    if access_role is not None:
        for category in protected:
            for target, overwrite in category.overwrites.items():
                if not isinstance(target, discord.Role):
                    continue
                if target.id in {guild.default_role.id, access_role.id}:
                    continue
                if overwrite.view_channel is True:
                    bypass_count += 1
    if bypass_count:
        warnings.append(
            f"{bypass_count} role-specific category allow(s) can intentionally bypass Member Access for their holders."
        )

    return {
        "ready": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "setup_channel": channel,
        "access_role": access_role,
        "prerequisite_role": prerequisite,
        "protected_categories": protected,
    }


def _parse_utc(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _effective_member_state(
    member: discord.Member,
    guild_state: Mapping[str, Any],
    member_state: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply the durable pre-gate grandfather checkpoint without mass DB writes."""
    effective = dict(member_state or default_member_setup_state())
    effective["section_revisions"] = dict(effective.get("section_revisions") or {})

    baseline_revision = _safe_int(guild_state.get("grandfather_revision"), 0)
    grandfather_before = _parse_utc(guild_state.get("grandfather_before"))
    joined_at = getattr(member, "joined_at", None)
    if joined_at is not None and getattr(joined_at, "tzinfo", None) is None:
        joined_at = joined_at.replace(tzinfo=timezone.utc)

    if (
        baseline_revision > 0
        and str(guild_state.get("gate_transition") or "") != "activating"
        and grandfather_before is not None
        and isinstance(joined_at, datetime)
        and joined_at.astimezone(timezone.utc) <= grandfather_before
        and _safe_int(effective.get("completed_revision"), 0) < baseline_revision
    ):
        effective["completed_revision"] = baseline_revision
        section_revisions = dict(effective.get("section_revisions") or {})
        for section in SETUP_SECTIONS:
            section_revisions[section] = max(
                _safe_int(section_revisions.get(section), 0),
                baseline_revision,
            )
        effective["section_revisions"] = section_revisions
    return effective


async def _member_payload(
    member: discord.Member,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    guild_state, member_state = await asyncio.gather(
        load_guild_setup_state(member.guild.id, refresh=True),
        load_member_setup_state(member.guild.id, member.id, refresh=True),
    )
    effective_state = _effective_member_state(member, guild_state, member_state)
    status = member_review_status(guild_state, effective_state)
    if _member_exempt(member):
        status["access_gated"] = False
    return guild_state, effective_state, status


def _severity_label(value: str) -> str:
    return {
        SEVERITY_MINOR: "Minor",
        SEVERITY_RECOMMENDED: "Recommended",
        SEVERITY_REQUIRED: "Required",
        SEVERITY_ACCESS_GATED: "Access-Gated",
    }.get(str(value), "Required")


def _member_embed(
    member: discord.Member,
    guild_state: Mapping[str, Any],
    status: Mapping[str, Any],
) -> discord.Embed:
    current = int(status.get("current_revision") or 0)
    completed = int(status.get("completed_revision") or 0)
    pending = list(status.get("pending_sections") or [])
    severity = str(status.get("severity") or SEVERITY_MINOR)
    latest = latest_revision(guild_state)

    if current <= 0:
        title = "🪪 Member Setup"
        description = "This server has not published a member-setup revision yet. Your existing profile and roles still work normally."
        color = discord.Color.blurple()
    elif status.get("is_current"):
        title = "✅ Member Setup Current"
        if completed < current:
            description = (
                f"Revision **{current}** requires no new action from you. "
                f"Your last explicit setup confirmation was revision **{completed}**, and your saved choices remain valid."
            )
        else:
            description = f"You're current on **revision {current}**. Your saved choices remain yours until you change them."
        color = discord.Color.green()
    elif status.get("access_gated"):
        title = "🔒 Member Setup Required"
        description = (
            f"This server is on **revision {current}** and your saved setup is on **revision {completed}**. "
            "Review the changed sections below, then confirm your current choices to restore Member Access."
        )
        color = discord.Color.red()
    else:
        title = "⚠️ Member Setup Review"
        description = (
            f"This server is on **revision {current}** and your saved setup is on **revision {completed}**. "
            "Your existing valid selections are preserved; only the changed sections need attention."
        )
        color = discord.Color.gold()

    embed = discord.Embed(title=title, description=description, color=color, timestamp=discord.utils.utcnow())
    embed.add_field(name="Current revision", value=str(current or "Not published"), inline=True)
    embed.add_field(name="Your revision", value=str(completed or "Not completed"), inline=True)
    embed.add_field(name="Review level", value=_severity_label(severity), inline=True)

    if pending:
        embed.add_field(
            name="Sections to review",
            value="\n".join(f"• **{SECTION_LABELS.get(section, section.title())}**" for section in pending),
            inline=False,
        )
    if latest:
        embed.add_field(
            name="Latest update",
            value=_clip(latest.get("summary") or f"Revision {current}", 1024),
            inline=False,
        )

    prerequisite = _resolve_role(member.guild, guild_state.get("prerequisite_role_id"))
    if isinstance(prerequisite, discord.Role) and prerequisite not in member.roles:
        embed.add_field(
            name="Access prerequisite",
            value=f"Finish the server's {prerequisite.mention} step too. Member Access is granted only after both are complete.",
            inline=False,
        )

    embed.set_footer(text="Existing valid choices are preserved across setup revisions.")
    return embed


class MemberSetupView(discord.ui.View):
    def __init__(self, owner_id: int, pending_sections: list[str], *, current: bool) -> None:
        super().__init__(timeout=900)
        self.owner_id = int(owner_id)
        self.pending_sections = list(pending_sections)
        self.current = bool(current)

        if not any(section in self.pending_sections for section in ("community", "notifications")):
            self.remove_item(self.community)
        if "profile" not in self.pending_sections:
            self.remove_item(self.profile)
        if "interests" not in self.pending_sections:
            self.remove_item(self.interests)
        if self.current or not self.pending_sections:
            self.remove_item(self.confirm)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id:
            return True
        await _reply(interaction, "Open your own Member Setup panel.", ok=False)
        return False

    @discord.ui.button(label="Community & Pings", emoji="🌿", style=discord.ButtonStyle.secondary, row=0)
    async def community(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_toke import open_member_community_pings
        await open_member_community_pings(interaction)

    @discord.ui.button(label="Profile & Cosmetics", emoji="🎭", style=discord.ButtonStyle.secondary, row=0)
    async def profile(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_command_hub import open_profile_entry
        await open_profile_entry(interaction)

    @discord.ui.button(label="Interests", emoji="🎮", style=discord.ButtonStyle.secondary, row=0)
    async def interests(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if guild is None or member is None:
            return await _reply(interaction, "This only works inside a server.", ok=False)
        from .public_self_roles_group import PROFILE_CATEGORIES, ProfileCategorySelectView
        payload = PROFILE_CATEGORIES.get("interests")
        if not payload:
            return await _reply(interaction, "The Interests section is unavailable.", ok=False)
        emoji, label, _names, description = payload
        await interaction.response.send_message(
            embed=discord.Embed(title=f"{emoji} {label}", description=description, color=discord.Color.blurple()),
            view=ProfileCategorySelectView(guild, member, "interests"),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Confirm Current Choices", emoji="✅", style=discord.ButtonStyle.success, row=1)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if member is None:
            return await _reply(interaction, "This only works inside a server.", ok=False)
        await _defer(interaction)
        _saved, status = await confirm_current_choices(member.guild.id, member.id)
        gate_result = await reconcile_member_access(member)
        guild_state = await load_guild_setup_state(member.guild.id, refresh=True)
        note = "Your setup is current."
        if gate_result == "granted":
            note += " Member Access was restored."
        elif gate_result == "waiting_prerequisite":
            note += " Member Access will unlock when the configured prerequisite role is complete."
        await _replace(
            interaction,
            content="✅ " + note,
            embed=_member_embed(member, guild_state, status),
            view=MemberSetupView(member.id, list(status.get("pending_sections") or []), current=bool(status.get("is_current"))),
        )

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=1)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_member_setup(interaction)


async def open_member_setup(interaction: discord.Interaction) -> None:
    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    if guild is None or member is None:
        return await _reply(interaction, "Member Setup only works inside a server.", ok=False)
    await _defer(interaction)
    guild_state, _member_state, status = await _member_payload(member)
    await _replace(
        interaction,
        embed=_member_embed(member, guild_state, status),
        view=MemberSetupView(member.id, list(status.get("pending_sections") or []), current=bool(status.get("is_current"))),
    )


def _message_component_ids(message: discord.Message) -> set[str]:
    out: set[str] = set()
    for row in list(getattr(message, "components", []) or []):
        children = list(getattr(row, "children", []) or [])
        if not children:
            children = [row]
        for child in children:
            custom_id = str(getattr(child, "custom_id", "") or "")
            if custom_id:
                out.add(custom_id)
    return out


def _looks_like_member_setup_panel(message: discord.Message, guild: discord.Guild) -> bool:
    try:
        author_id = int(getattr(getattr(message, "author", None), "id", 0) or 0)
        bot_id = int(getattr(getattr(guild, "me", None), "id", 0) or 0)
        if author_id <= 0 or author_id != bot_id:
            return False
        component_ids = _message_component_ids(message)
        if not any(custom_id.startswith("dank:profile:v1:") for custom_id in component_ids):
            return False
        titles = {
            str(getattr(embed, "title", "") or "").strip()
            for embed in list(getattr(message, "embeds", []) or [])
        }
        return bool(
            {"Profile Panel", "Member Setup & Profile"}.intersection(titles)
            or "dank:profile:v1:edit" in component_ids
        )
    except Exception:
        return False


async def _find_or_fetch_member_setup_panel(
    guild: discord.Guild,
    state: Mapping[str, Any],
) -> tuple[Optional[discord.Message], str]:
    channel = _resolve_channel(guild, state.get("setup_channel_id"))
    if channel is None:
        return None, "Choose the permanent Member Setup channel first."

    saved_id = _safe_int(state.get("panel_message_id"), 0)
    if saved_id > 0:
        try:
            message = await channel.fetch_message(saved_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            message = None
        if isinstance(message, discord.Message) and _looks_like_member_setup_panel(message, guild):
            return message, ""

    me = guild.me
    if not isinstance(me, discord.Member):
        return None, "Dank Shield could not resolve its bot member."
    perms = channel.permissions_for(me)
    if not (perms.view_channel and perms.read_message_history):
        return None, "Dank Shield needs View Channel and Read Message History to adopt the existing panel."

    matches: list[discord.Message] = []
    try:
        async for message in channel.history(limit=100):
            if _looks_like_member_setup_panel(message, guild):
                matches.append(message)
                if len(matches) > 1:
                    break
    except (discord.Forbidden, discord.HTTPException) as exc:
        return None, f"Could not inspect the Member Setup channel history: {type(exc).__name__}."

    if not matches:
        return None, (
            "No Dank Shield Member Setup & Profile panel was found in the configured channel. "
            "Use Profile Builder to post one."
        )
    if len(matches) > 1:
        return None, (
            "More than one Member Setup & Profile panel exists in the configured channel. "
            "Delete the obsolete duplicate before refreshing so Dank Shield cannot edit the wrong panel."
        )
    return matches[0], ""


async def refresh_public_member_setup_panel(
    guild: discord.Guild,
    *,
    actor_id: int,
) -> tuple[Optional[discord.Message], str]:
    state = await load_guild_setup_state(guild.id, refresh=True)
    message, error = await _find_or_fetch_member_setup_panel(guild, state)
    if message is None:
        return None, error

    from .public_self_roles_group import ProfilePanelView, _profile_panel_embed

    public_embed = _profile_panel_embed(guild)
    current_revision = int(state.get("current_revision") or 0)
    latest = latest_revision(state)
    if current_revision > 0:
        public_embed.add_field(
            name=f"Published setup revision {current_revision}",
            value=(
                f"**{_severity_label(str((latest or {}).get('severity') or SEVERITY_MINOR))}**"
                + (f" — {_clip((latest or {}).get('summary'), 850)}" if _clip((latest or {}).get("summary"), 850) else "")
                + "\nOpen **Member Setup / Review** to see whether this revision needs anything from you."
            ),
            inline=False,
        )

    try:
        await message.edit(
            embed=public_embed,
            view=ProfilePanelView(),
            allowed_mentions=discord.AllowedMentions.none(),
        )
    except discord.Forbidden:
        return None, "Dank Shield cannot edit that panel message."
    except discord.HTTPException as exc:
        return None, f"Discord could not refresh the panel: {type(exc).__name__}."

    await configure_guild_setup(
        guild.id,
        setup_channel_id=message.channel.id,
        panel_message_id=message.id,
        actor_id=actor_id,
    )
    return message, ""


def _strict_gate_flow_value(
    guild: discord.Guild,
    state: Mapping[str, Any],
) -> str:
    prerequisite = _resolve_role(guild, state.get("prerequisite_role_id"))
    access_role = _resolve_role(guild, state.get("access_role_id"))
    protected = [
        guild.get_channel(_safe_int(raw_id, 0))
        for raw_id in list(state.get("protected_category_ids") or [])
    ]
    protected = [
        item for item in protected if isinstance(item, discord.CategoryChannel)
    ]

    eligibility = (
        prerequisite.mention
        if isinstance(prerequisite, discord.Role)
        else "No prerequisite — any non-exempt member is eligible"
    )
    access = (
        access_role.mention
        if isinstance(access_role, discord.Role)
        else "Member Access role not configured"
    )
    destination = (
        f"{len(protected)} protected categor{'y' if len(protected) == 1 else 'ies'}"
        if protected
        else "No protected categories configured"
    )
    prerequisite_note = (
        "Dank Shield **does not grant** the eligibility prerequisite. The member must already have it."
        if isinstance(prerequisite, discord.Role)
        else "There is no separate eligibility role to earn first."
    )
    return (
        f"**1. Eligibility:** {eligibility}\n"
        "**2. Setup:** member completes/confirms the current Member Setup revision\n"
        f"**3. Access:** Dank Shield automatically grants/removes {access}\n"
        f"**4. Visibility:** that Member Access role unlocks {destination}\n"
        f"{prerequisite_note}"
    )[:1024]


def _admin_embed(guild: discord.Guild, state: Mapping[str, Any]) -> discord.Embed:
    health = gate_health(guild, state)
    channel = health["setup_channel"]
    access_role = health["access_role"]
    prerequisite = health["prerequisite_role"]
    categories = list(health["protected_categories"])
    latest = latest_revision(state)

    embed = discord.Embed(
        title="🪪 Member Setup Manager",
        description=(
            "Publish versioned member-role/profile changes without wiping existing choices. "
            "Strict Gate is separate and cannot activate until its role/channel/category health checks pass."
        ),
        color=discord.Color.green() if state.get("gate_active") else discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Published setup",
        value=(
            f"Revision: **{int(state.get('current_revision') or 0)}**\n"
            f"Latest: **{_severity_label(str((latest or {}).get('severity') or SEVERITY_MINOR))}**\n"
            f"Gate: **{'ACTIVE' if state.get('gate_active') else 'Not active'}**"
        ),
        inline=True,
    )
    embed.add_field(
        name="Member Setup channel",
        value=channel.mention if isinstance(channel, discord.TextChannel) else "Not selected",
        inline=True,
    )
    embed.add_field(
        name="Member Access role (automatic)",
        value=(
            f"{access_role.mention}\nDank Shield grants/removes this role after setup."
            if isinstance(access_role, discord.Role)
            else "Not selected — choose the role Dank Shield should grant/remove automatically."
        ),
        inline=True,
    )
    panel_message_id = _safe_int(state.get("panel_message_id"), 0)
    embed.add_field(
        name="Public setup panel",
        value=(
            "Connected — **Refresh Public Panel** updates it after setup changes."
            if panel_message_id > 0
            else "Needs refresh — use **Refresh Public Panel** to connect the live member setup panel."
        ),
        inline=False,
    )
    embed.add_field(
        name="Eligibility prerequisite (optional)",
        value=(
            f"{prerequisite.mention}\nMember must already have this role; Dank Shield does not grant it."
            if isinstance(prerequisite, discord.Role)
            else "None — setup completion alone can qualify a member for Member Access."
        ),
        inline=True,
    )
    embed.add_field(
        name="Protected categories",
        value=(
            "\n".join(f"• {category.name}" for category in categories[:12])
            if categories else "None selected"
        ),
        inline=False,
    )
    embed.add_field(
        name="How Strict Gate works",
        value=_strict_gate_flow_value(guild, state),
        inline=False,
    )
    if health["blockers"]:
        embed.add_field(
            name="Strict Gate blockers",
            value="\n".join(f"• {item}" for item in health["blockers"][:8]),
            inline=False,
        )
    else:
        embed.add_field(name="Strict Gate health", value="✅ Ready to activate.", inline=False)
    if health["warnings"]:
        embed.add_field(
            name="Warnings",
            value="\n".join(f"• {item}" for item in health["warnings"][:6]),
            inline=False,
        )
    if latest:
        embed.add_field(name="Latest published change", value=_clip(latest.get("summary"), 1024), inline=False)
    embed.set_footer(text="Publishing a revision and activating the Discord access gate are separate actions.")
    return embed


async def _staff_authorized(interaction: discord.Interaction) -> bool:
    return bool(await _require_setup_permission(interaction))


class MemberSetupResourceBrowserView(DankGuildResourceBrowserView):
    """Single-message Search-Safe browser for Member Setup admin resources."""

    _MODES = {
        "setup_channel",
        "access_role",
        "prerequisite_role",
        "protected_category",
    }

    def __init__(
        self,
        owner_id: int,
        guild: discord.Guild,
        *,
        mode: str,
        query: str = "",
        page: int = 0,
        alias_index: Optional[Mapping[str, tuple[str, ...]]] = None,
        conflicting_role_id: int = 0,
    ) -> None:
        clean_mode = str(mode or "").strip().lower()
        if clean_mode not in self._MODES:
            raise ValueError(f"Unsupported Member Setup resource mode: {clean_mode}")

        self.owner_id = int(owner_id)
        self.member_setup_guild = guild
        self.mode = clean_mode
        self.conflicting_role_id = int(conflicting_role_id or 0)

        resource_kinds: tuple[str, ...]
        title: str
        placeholder: str
        empty_message: str
        predicate = None

        if clean_mode == "setup_channel":
            resource_kinds = ("text",)
            title = "🪪 Choose Member Setup Channel"
            placeholder = "Choose the permanent Member Setup text channel…"
            empty_message = "No text channels matched. Use 🔎 Search with a current/styled name, previous name, ID, or mention."
        elif clean_mode == "access_role":
            resource_kinds = ("role",)
            title = "🔑 Choose Member Access Role"
            placeholder = "Choose the role Dank Shield should grant/remove…"
            empty_message = "No eligible roles matched. Use 🔎 Search with a current/styled name, previous name, ID, or mention."

            def predicate(resource: Any) -> bool:
                try:
                    if bool(resource.is_default()):
                        return False
                except Exception:
                    pass
                if int(getattr(resource, "id", 0) or 0) == self.conflicting_role_id:
                    return False
                return not bool(getattr(resource, "managed", False))
        elif clean_mode == "prerequisite_role":
            resource_kinds = ("role",)
            title = "✅ Choose Eligibility Prerequisite"
            placeholder = "Choose the optional role members must already have…"
            empty_message = "No roles matched. Use 🔎 Search with a current/styled name, previous name, ID, or mention."

            def predicate(resource: Any) -> bool:
                if int(getattr(resource, "id", 0) or 0) == self.conflicting_role_id:
                    return False
                try:
                    return not bool(resource.is_default())
                except Exception:
                    return True
        else:
            resource_kinds = ("category",)
            title = "🔒 Add Protected Category"
            placeholder = "Choose one member category to protect…"
            empty_message = "No categories matched. Use 🔎 Search with a current/styled name, previous name, ID, or mention."

        async def picked(interaction: discord.Interaction, resource: Any) -> None:
            await self._picked(interaction, resource)

        async def back(interaction: discord.Interaction) -> None:
            if not await _staff_authorized(interaction):
                return
            await _defer_panel_update(interaction)
            state = await load_guild_setup_state(guild.id, refresh=True)
            await _replace(
                interaction,
                embed=_admin_embed(guild, state),
                view=MemberSetupAdminView(self.owner_id),
            )

        super().__init__(
            guild=guild,
            author_id=self.owner_id,
            resource_kinds=resource_kinds,
            on_pick=picked,
            custom_id=f"dank:member_setup:{clean_mode}",
            title=title,
            placeholder=placeholder,
            query=query,
            page=page,
            predicate=predicate,
            alias_index=alias_index,
            on_home=back,
            home_label="Back to Member Setup",
            cancel_label="Close",
            include_cancel=True,
            empty_message=empty_message,
        )

    def clone(
        self,
        *,
        query: Optional[str] = None,
        page: Optional[int] = None,
        alias_index: Optional[Mapping[str, tuple[str, ...]]] = None,
    ) -> "MemberSetupResourceBrowserView":
        return MemberSetupResourceBrowserView(
            self.owner_id,
            self.member_setup_guild,
            mode=self.mode,
            query=self.query if query is None else query,
            page=self.page if page is None else page,
            alias_index=self.alias_index if alias_index is None else alias_index,
            conflicting_role_id=self.conflicting_role_id,
        )

    def embed(self) -> discord.Embed:
        embed = super().embed()
        if self.mode == "access_role":
            guidance = (
                "Dank Shield **grants/removes** this role automatically after an eligible member completes setup. "
                "This is the role that unlocks protected categories."
            )
        elif self.mode == "prerequisite_role":
            guidance = (
                "Optional eligibility check only. The member must **already have** this role; "
                "Dank Shield does **not** grant it."
            )
        elif self.mode == "setup_channel":
            guidance = (
                "This is the permanent Member Setup channel. It must remain visible while members are gated."
            )
        else:
            guidance = (
                "This category becomes part of Strict Gate. Add categories one at a time; repeat this picker for more."
            )
        embed.add_field(name="What this selection does", value=guidance, inline=False)
        return embed

    async def _picked(self, interaction: discord.Interaction, resource: Any) -> None:
        if not await _staff_authorized(interaction):
            return

        guild = interaction.guild
        if guild is None or int(guild.id) != int(self.member_setup_guild.id):
            return await _reply(interaction, "This picker no longer matches the current server.", ok=False)

        if self.mode == "setup_channel" and not isinstance(resource, discord.TextChannel):
            return await _reply(interaction, "Choose a text channel.", ok=False)
        if self.mode in {"access_role", "prerequisite_role"} and not isinstance(resource, discord.Role):
            return await _reply(interaction, "Choose a server role.", ok=False)
        if self.mode == "protected_category" and not isinstance(resource, discord.CategoryChannel):
            return await _reply(interaction, "Choose a category.", ok=False)

        if self.mode == "access_role":
            blocker = _access_role_blocker(guild, resource)
            if blocker:
                return await _reply(interaction, blocker, ok=False)

        await _defer_panel_update(interaction)
        state = await load_guild_setup_state(guild.id, refresh=True)
        if state.get("gate_active"):
            await _replace(
                interaction,
                content="❌ Suspend Strict Gate before changing Member Setup resource mappings.",
                embed=_admin_embed(guild, state),
                view=MemberSetupAdminView(self.owner_id),
            )
            return

        if self.mode == "access_role":
            prerequisite_id = _safe_int(state.get("prerequisite_role_id"), 0)
            if prerequisite_id > 0 and int(resource.id) == prerequisite_id:
                await _replace(
                    interaction,
                    content=(
                        "❌ Member Access and Eligibility Prerequisite must be different roles. "
                        "Choose a dedicated Member Access role."
                    ),
                    embed=_admin_embed(guild, state),
                    view=MemberSetupAdminView(self.owner_id),
                )
                return
        elif self.mode == "prerequisite_role":
            access_role_id = _safe_int(state.get("access_role_id"), 0)
            if access_role_id > 0 and int(resource.id) == access_role_id:
                await _replace(
                    interaction,
                    content=(
                        "❌ Eligibility Prerequisite cannot be the Member Access role. "
                        "Members must already have the prerequisite before Dank Shield grants Member Access."
                    ),
                    embed=_admin_embed(guild, state),
                    view=MemberSetupAdminView(self.owner_id),
                )
                return

        if self.mode == "setup_channel":
            state = await configure_guild_setup(
                guild.id,
                setup_channel_id=resource.id,
                actor_id=interaction.user.id,
            )
            confirmation = f"✅ Member Setup channel set to {resource.mention}."
        elif self.mode == "access_role":
            state = await configure_guild_setup(
                guild.id,
                access_role_id=resource.id,
                actor_id=interaction.user.id,
            )
            confirmation = f"✅ Member Access role set to {resource.mention}."
        elif self.mode == "prerequisite_role":
            state = await configure_guild_setup(
                guild.id,
                prerequisite_role_id=resource.id,
                actor_id=interaction.user.id,
            )
            confirmation = (
                f"✅ Eligibility prerequisite set to {resource.mention}. "
                "Members must already have it; Dank Shield will not grant it."
            )
        else:
            ids = [_safe_int(value, 0) for value in state.get("protected_category_ids", [])]
            if resource.id not in ids:
                ids.append(resource.id)
            state = await configure_guild_setup(
                guild.id,
                protected_category_ids=ids,
                actor_id=interaction.user.id,
            )
            confirmation = f"✅ Added **{resource.name}** to Strict Gate protected categories."

        await _replace(
            interaction,
            content=confirmation,
            embed=_admin_embed(guild, state),
            view=MemberSetupAdminView(self.owner_id),
        )


async def _open_member_setup_resource_browser(
    interaction: discord.Interaction,
    *,
    owner_id: int,
    mode: str,
) -> None:
    if not await _staff_authorized(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _reply(interaction, "Member Setup only works inside a server.", ok=False)

    state = await load_guild_setup_state(guild.id, refresh=True)
    conflicting_role_id = 0
    if mode == "access_role":
        conflicting_role_id = _safe_int(state.get("prerequisite_role_id"), 0)
    elif mode == "prerequisite_role":
        conflicting_role_id = _safe_int(state.get("access_role_id"), 0)

    browser = MemberSetupResourceBrowserView(
        owner_id,
        guild,
        mode=mode,
        conflicting_role_id=conflicting_role_id,
    )
    await _replace(
        interaction,
        embed=browser.embed(),
        view=browser,
    )


class PublishRevisionModal(discord.ui.Modal):
    def __init__(self, severity: str) -> None:
        super().__init__(title=f"Publish {_severity_label(severity)} Setup Update", timeout=300)
        self.severity = str(severity)
        self.summary = discord.ui.TextInput(
            label="What changed?",
            placeholder="Example: Added new notification and community role choices",
            min_length=3,
            max_length=300,
        )
        self.sections = discord.ui.TextInput(
            label="Changed sections",
            placeholder="community, notifications, profile, interests",
            min_length=2,
            max_length=120,
        )
        self.add_item(self.summary)
        self.add_item(self.sections)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside a server.", ok=False)

        selected = normalize_sections(str(self.sections.value or ""))
        if not selected and self.severity != SEVERITY_MINOR:
            return await _reply(
                interaction,
                "Use one or more of: community, notifications, profile, interests.",
                ok=False,
            )

        state = await load_guild_setup_state(guild.id, refresh=True)
        if self.severity == SEVERITY_ACCESS_GATED and not state.get("gate_active"):
            return await _reply(
                interaction,
                "Strict Gate is not active. Activate it first, or publish this as Required instead.",
                ok=False,
            )

        await _defer_panel_update(interaction)
        state = await publish_revision(
            guild.id,
            severity=self.severity,
            changed_sections=selected,
            summary=str(self.summary.value or ""),
            actor_id=interaction.user.id,
        )
        if self.severity == SEVERITY_ACCESS_GATED:
            _schedule_guild_reconcile(guild)

        panel_message, panel_error = await refresh_public_member_setup_panel(
            guild,
            actor_id=interaction.user.id,
        )
        panel_note = ""
        if panel_message is not None:
            panel_note = f" Public panel refreshed in {panel_message.channel.mention}."
        elif panel_error:
            panel_note = f" Public panel was not refreshed: {_clip(panel_error, 220)}"

        state = await load_guild_setup_state(guild.id, refresh=True)
        await _replace(
            interaction,
            content=f"✅ Published member setup revision {state.get('current_revision')}.{panel_note}",
            embed=_admin_embed(guild, state),
            view=MemberSetupAdminView(interaction.user.id),
        )


class GateActivationConfirmModal(discord.ui.Modal):
    def __init__(self, guild: discord.Guild) -> None:
        super().__init__(title="Activate Strict Member Setup Gate", timeout=300)
        self.guild_id = int(guild.id)
        self.guild_name = str(guild.name)
        self.confirm = discord.ui.TextInput(
            label="Type the exact server name",
            placeholder=_clip(guild.name, 100),
            min_length=1,
            max_length=100,
        )
        self.add_item(self.confirm)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        if guild is None or guild.id != self.guild_id:
            return await _reply(interaction, "This confirmation no longer matches the server.", ok=False)
        if str(self.confirm.value or "").strip() != self.guild_name:
            return await _reply(interaction, "Server-name confirmation did not match. Nothing changed.", ok=False)
        await _defer_panel_update(interaction)
        try:
            state = await activate_strict_gate(guild, actor_id=interaction.user.id)
        except Exception as exc:
            return await _reply(interaction, f"Strict Gate was not activated: {_clip(exc, 350)}", ok=False)
        await _replace(
            interaction,
            content="✅ Strict Member Setup Gate is active. Existing eligible members were grandfathered before category permissions changed.",
            embed=_admin_embed(guild, state),
            view=MemberSetupAdminView(interaction.user.id),
        )


class GateSuspendConfirmModal(discord.ui.Modal):
    def __init__(self, guild: discord.Guild) -> None:
        super().__init__(title="Suspend Strict Member Setup Gate", timeout=300)
        self.guild_id = int(guild.id)
        self.guild_name = str(guild.name)
        self.confirm = discord.ui.TextInput(
            label="Type the exact server name",
            placeholder=_clip(guild.name, 100),
            min_length=1,
            max_length=100,
        )
        self.add_item(self.confirm)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        if guild is None or guild.id != self.guild_id:
            return await _reply(interaction, "This confirmation no longer matches the server.", ok=False)
        if str(self.confirm.value or "").strip() != self.guild_name:
            return await _reply(interaction, "Server-name confirmation did not match. Nothing changed.", ok=False)
        await _defer_panel_update(interaction)
        try:
            state = await suspend_strict_gate(guild, actor_id=interaction.user.id)
        except Exception as exc:
            return await _reply(interaction, f"Strict Gate could not be suspended safely: {_clip(exc, 350)}", ok=False)
        await _replace(
            interaction,
            content="✅ Strict Gate suspended and the recorded category visibility state was restored.",
            embed=_admin_embed(guild, state),
            view=MemberSetupAdminView(interaction.user.id),
        )


class MemberSetupAdminView(discord.ui.View):
    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=900)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.owner_id:
            await _reply(interaction, "Only the manager who opened this setup panel can use it.", ok=False)
            return False
        return True

    @discord.ui.button(label="Setup Channel", emoji="🪪", style=discord.ButtonStyle.secondary, row=0)
    async def setup_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_member_setup_resource_browser(
            interaction,
            owner_id=self.owner_id,
            mode="setup_channel",
        )

    @discord.ui.button(label="Use This Channel", emoji="📍", style=discord.ButtonStyle.secondary, row=0)
    async def use_current_channel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or not isinstance(channel, discord.TextChannel):
            return await _reply(interaction, "Run this inside the text channel you want to use for Member Setup.", ok=False)
        state = await load_guild_setup_state(guild.id, refresh=True)
        if state.get("gate_active"):
            return await _reply(interaction, "Suspend Strict Gate before changing its setup channel.", ok=False)
        await _defer_panel_update(interaction)
        state = await configure_guild_setup(
            guild.id,
            setup_channel_id=channel.id,
            actor_id=interaction.user.id,
        )
        await _replace(interaction, embed=_admin_embed(guild, state), view=MemberSetupAdminView(self.owner_id))

    @discord.ui.button(label="Member Access Role", emoji="🔑", style=discord.ButtonStyle.secondary, row=0)
    async def access_role(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_member_setup_resource_browser(
            interaction,
            owner_id=self.owner_id,
            mode="access_role",
        )

    @discord.ui.button(label="Create Member Access", emoji="➕", style=discord.ButtonStyle.secondary, row=0)
    async def create_access(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        actor = interaction.user if isinstance(interaction.user, discord.Member) else None
        if guild is None or actor is None:
            return await _reply(interaction, "This only works inside a server.", ok=False)
        if not (actor.id == guild.owner_id or actor.guild_permissions.administrator or actor.guild_permissions.manage_roles):
            return await _reply(interaction, "Creating an access role requires Manage Roles.", ok=False)
        state = await load_guild_setup_state(guild.id, refresh=True)
        if state.get("gate_active"):
            return await _reply(interaction, "Suspend Strict Gate before replacing its access role.", ok=False)
        me = guild.me
        if not isinstance(me, discord.Member) or not (me.guild_permissions.manage_roles or me.guild_permissions.administrator):
            return await _reply(interaction, "Dank Shield needs Manage Roles.", ok=False)
        await _defer_panel_update(interaction)
        existing = discord.utils.find(lambda role: role.name.casefold() == "member access", guild.roles)
        try:
            role = existing if isinstance(existing, discord.Role) else await guild.create_role(
                name="Member Access",
                permissions=discord.Permissions.none(),
                mentionable=False,
                hoist=False,
                reason=f"Dank Shield Member Setup access role created by {interaction.user} ({interaction.user.id})",
            )
        except discord.HTTPException as exc:
            return await _reply(interaction, f"Discord could not create Member Access: {type(exc).__name__}.", ok=False)
        blocker = _access_role_blocker(guild, role)
        if blocker:
            return await _reply(interaction, blocker, ok=False)

        state = await load_guild_setup_state(guild.id, refresh=True)
        prerequisite_id = _safe_int(state.get("prerequisite_role_id"), 0)
        if prerequisite_id > 0 and int(role.id) == prerequisite_id:
            await _replace(
                interaction,
                content=(
                    "❌ The existing **Member Access** role is currently the Eligibility Prerequisite. "
                    "Choose a different prerequisite or a different dedicated Member Access role."
                ),
                embed=_admin_embed(guild, state),
                view=MemberSetupAdminView(self.owner_id),
            )
            return

        state = await configure_guild_setup(guild.id, access_role_id=role.id, actor_id=interaction.user.id)
        await _replace(interaction, embed=_admin_embed(guild, state), view=MemberSetupAdminView(self.owner_id))

    @discord.ui.button(label="Eligibility Prerequisite", emoji="✅", style=discord.ButtonStyle.secondary, row=1)
    async def prerequisite(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_member_setup_resource_browser(
            interaction,
            owner_id=self.owner_id,
            mode="prerequisite_role",
        )

    @discord.ui.button(label="Clear Eligibility Rule", emoji="🧹", style=discord.ButtonStyle.secondary, row=1)
    async def clear_prerequisite(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside a server.", ok=False)
        state = await load_guild_setup_state(guild.id, refresh=True)
        if state.get("gate_active"):
            return await _reply(interaction, "Suspend Strict Gate before changing its prerequisite.", ok=False)
        await _defer_panel_update(interaction)
        state = await configure_guild_setup(
            guild.id,
            prerequisite_role_id=0,
            actor_id=interaction.user.id,
        )
        await _replace(interaction, embed=_admin_embed(guild, state), view=MemberSetupAdminView(self.owner_id))

    @discord.ui.button(label="Add Protected Category", emoji="🔒", style=discord.ButtonStyle.secondary, row=1)
    async def add_category(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_member_setup_resource_browser(
            interaction,
            owner_id=self.owner_id,
            mode="protected_category",
        )

    @discord.ui.button(label="Clear Protected Categories", emoji="🧹", style=discord.ButtonStyle.secondary, row=1)
    async def clear_categories(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside a server.", ok=False)
        state = await load_guild_setup_state(guild.id, refresh=True)
        if state.get("gate_active"):
            return await _reply(interaction, "Suspend Strict Gate before changing protected categories.", ok=False)
        await _defer_panel_update(interaction)
        state = await configure_guild_setup(guild.id, protected_category_ids=[], actor_id=interaction.user.id)
        await _replace(interaction, embed=_admin_embed(guild, state), view=MemberSetupAdminView(self.owner_id))

    @discord.ui.button(label="Publish Minor", emoji="📝", style=discord.ButtonStyle.secondary, row=2)
    async def publish_minor(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(PublishRevisionModal(SEVERITY_MINOR))

    @discord.ui.button(label="Publish Recommended", emoji="🆕", style=discord.ButtonStyle.secondary, row=2)
    async def publish_recommended(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(PublishRevisionModal(SEVERITY_RECOMMENDED))

    @discord.ui.button(label="Publish Required", emoji="⚠️", style=discord.ButtonStyle.primary, row=2)
    async def publish_required(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(PublishRevisionModal(SEVERITY_REQUIRED))

    @discord.ui.button(label="Publish Access-Gated", emoji="🔐", style=discord.ButtonStyle.danger, row=2)
    async def publish_gated(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(PublishRevisionModal(SEVERITY_ACCESS_GATED))

    @discord.ui.button(label="Refresh Public Panel", emoji="♻️", style=discord.ButtonStyle.primary, row=3)
    async def refresh_public_panel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside a server.", ok=False)
        await _defer_panel_update(interaction)
        message, error = await refresh_public_member_setup_panel(
            guild,
            actor_id=interaction.user.id,
        )
        if message is None:
            return await _reply(interaction, error or "The public panel could not be refreshed.", ok=False)
        state = await load_guild_setup_state(guild.id, refresh=True)
        await _replace(
            interaction,
            content=f"✅ Refreshed the Member Setup & Profile panel in {message.channel.mention}.",
            embed=_admin_embed(guild, state),
            view=MemberSetupAdminView(self.owner_id),
        )

    @discord.ui.button(label="Activate Strict Gate", emoji="🛡️", style=discord.ButtonStyle.danger, row=3)
    async def activate(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside a server.", ok=False)
        state = await load_guild_setup_state(guild.id, refresh=True)
        if state.get("gate_active"):
            return await _reply(interaction, "Strict Gate is already active.", ok=True)
        health = gate_health(guild, state)
        if not health["ready"]:
            return await _reply(interaction, "Fix Strict Gate blockers first:\n" + "\n".join(f"• {x}" for x in health["blockers"][:8]), ok=False)
        # The manager embed immediately above this button shows the exact
        # Eligibility → Setup → Member Access → Protected Categories chain.
        await interaction.response.send_modal(GateActivationConfirmModal(guild))

    @discord.ui.button(label="Suspend Strict Gate", emoji="🧯", style=discord.ButtonStyle.secondary, row=3)
    async def suspend(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _staff_authorized(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside a server.", ok=False)
        state = await load_guild_setup_state(guild.id, refresh=True)
        if not state.get("gate_active"):
            return await _reply(interaction, "Strict Gate is not active.", ok=True)
        await interaction.response.send_modal(GateSuspendConfirmModal(guild))

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=3)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_member_setup_admin(interaction)


    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.secondary, row=4)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.edit_message(
            content="Member Setup Manager closed.",
            embed=None,
            view=None,
        )


async def open_member_setup_admin(interaction: discord.Interaction) -> None:
    if not await _staff_authorized(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _reply(interaction, "Member Setup only works inside a server.", ok=False)
    await _defer_panel_update(interaction)
    state = await load_guild_setup_state(guild.id, refresh=True)
    await _replace(
        interaction,
        embed=_admin_embed(guild, state),
        view=MemberSetupAdminView(interaction.user.id),
    )


async def _restore_gate_snapshot(
    guild: discord.Guild,
    state: Mapping[str, Any],
    *,
    reason: str,
) -> list[str]:
    snapshot = state.get("gate_snapshot") if isinstance(state.get("gate_snapshot"), Mapping) else {}
    access_role = _resolve_role(guild, state.get("access_role_id"))
    failures: list[str] = []

    for raw_id, saved in snapshot.items():
        category = guild.get_channel(_safe_int(raw_id, 0))
        if not isinstance(category, discord.CategoryChannel):
            # A deleted category no longer needs an overwrite restored.
            continue
        try:
            await _restore_category_gate(
                category,
                everyone=guild.default_role,
                access_role=access_role,
                snapshot=saved if isinstance(saved, Mapping) else {},
                reason=reason,
            )
        except Exception as exc:
            failures.append(f"{category.name}: {type(exc).__name__}")
    return failures


async def recover_interrupted_gate_transition(guild: discord.Guild) -> dict[str, Any]:
    """Fail open after a process crash during activation/suspension."""
    state = await load_guild_setup_state(guild.id, refresh=True)
    transition = str(state.get("gate_transition") or "")
    if transition not in {"activating", "suspending"}:
        return state

    failures = await _restore_gate_snapshot(
        guild,
        state,
        reason=f"Dank Shield Member Setup recovery from interrupted {transition}",
    )
    if failures:
        print(
            "❌ member_setup transition recovery incomplete "
            f"guild={guild.id} transition={transition} failures={' | '.join(failures[:6])}"
        )
        return state

    state = await configure_guild_setup(
        guild.id,
        access_mode=ACCESS_MODE_NORMAL,
        gate_active=False,
        gate_transition="",
        gate_snapshot={},
        grandfather_revision=(
            0 if transition == "activating" else _safe_int(state.get("grandfather_revision"), 0)
        ),
        grandfather_before=(
            "" if transition == "activating" else str(state.get("grandfather_before") or "")
        ),
        actor_id=0,
    )
    print(
        "✅ member_setup recovered interrupted transition "
        f"guild={guild.id} transition={transition} gate_active=False"
    )
    return state


async def activate_strict_gate(guild: discord.Guild, *, actor_id: int) -> dict[str, Any]:
    state = await recover_interrupted_gate_transition(guild)
    if state.get("gate_transition"):
        raise RuntimeError(
            "A previous Strict Gate transition could not be recovered automatically. "
            "Repair the protected category permissions before trying again."
        )

    if state.get("gate_active"):
        return state
    health = gate_health(guild, state)
    if not health["ready"]:
        raise RuntimeError(" | ".join(health["blockers"][:8]))

    if int(state.get("current_revision") or 0) <= 0:
        state = await publish_revision(
            guild.id,
            severity=SEVERITY_REQUIRED,
            changed_sections=list(SETUP_SECTIONS),
            summary="Initial member setup",
            actor_id=actor_id,
        )
        health = gate_health(guild, state)

    access_role = health["access_role"]
    prerequisite = health["prerequisite_role"]
    categories = list(health["protected_categories"])
    if not isinstance(access_role, discord.Role):
        raise RuntimeError("Member Access role is unavailable.")

    current_revision = int(state.get("current_revision") or 0)
    grandfather_before = discord.utils.utcnow().isoformat()

    # Snapshot every category BEFORE any permission mutation and persist the
    # transition first. A host crash can then safely restore the old visibility.
    snapshot: dict[str, Any] = {
        str(category.id): _category_snapshot(
            category,
            everyone=guild.default_role,
            access_role=access_role,
        )
        for category in categories
    }
    state = await configure_guild_setup(
        guild.id,
        enabled=True,
        access_mode=ACCESS_MODE_STRICT,
        gate_active=False,
        gate_transition="activating",
        gate_snapshot=snapshot,
        grandfather_revision=current_revision,
        grandfather_before=grandfather_before,
        actor_id=actor_id,
    )

    try:
        # Existing members are grandfathered by the guild checkpoint above.
        # Only Discord role assignment is needed here; there is no per-member
        # database write during activation.
        for index, member in enumerate(list(guild.members)):
            if not isinstance(member, discord.Member) or member.bot or _member_exempt(member):
                continue
            if isinstance(prerequisite, discord.Role) and prerequisite not in member.roles:
                continue
            if access_role not in member.roles:
                await member.add_roles(
                    access_role,
                    reason="Dank Shield Member Setup strict-gate activation baseline",
                )
            if index and index % 25 == 0:
                await asyncio.sleep(0)

        reason = f"Dank Shield Member Setup strict gate activated by {actor_id}"
        for category in categories:
            await _set_category_gate(
                category,
                everyone=guild.default_role,
                access_role=access_role,
                reason=reason,
            )
    except Exception as exc:
        rollback_state = await load_guild_setup_state(guild.id, refresh=True)
        failures = await _restore_gate_snapshot(
            guild,
            rollback_state,
            reason="Dank Shield Member Setup activation rollback",
        )
        if not failures:
            await configure_guild_setup(
                guild.id,
                access_mode=ACCESS_MODE_NORMAL,
                gate_active=False,
                gate_transition="",
                gate_snapshot={},
                grandfather_revision=0,
                grandfather_before="",
                actor_id=actor_id,
            )
        raise RuntimeError(
            "Strict Gate activation failed"
            + (f"; rollback incomplete: {' | '.join(failures[:6])}" if failures else "")
            + f": {type(exc).__name__}"
        ) from exc

    return await configure_guild_setup(
        guild.id,
        enabled=True,
        access_mode=ACCESS_MODE_STRICT,
        gate_active=True,
        gate_transition="",
        gate_snapshot=snapshot,
        grandfather_revision=current_revision,
        grandfather_before=grandfather_before,
        actor_id=actor_id,
    )


async def suspend_strict_gate(guild: discord.Guild, *, actor_id: int) -> dict[str, Any]:
    state = await recover_interrupted_gate_transition(guild)
    if state.get("gate_transition"):
        raise RuntimeError(
            "A previous Strict Gate transition could not be recovered automatically. "
            "Repair the protected category permissions before trying again."
        )
    if not state.get("gate_active"):
        return state

    # Persist the suspending state before the first restore call. If the process
    # exits midway, guild-available recovery can continue restoring the snapshot.
    state = await configure_guild_setup(
        guild.id,
        gate_transition="suspending",
        actor_id=actor_id,
    )
    failures = await _restore_gate_snapshot(
        guild,
        state,
        reason=f"Dank Shield Member Setup strict gate suspended by {actor_id}",
    )
    if failures:
        raise RuntimeError(
            "Could not restore every protected category: " + " | ".join(failures[:6])
        )

    return await configure_guild_setup(
        guild.id,
        access_mode=ACCESS_MODE_NORMAL,
        gate_active=False,
        gate_transition="",
        gate_snapshot={},
        actor_id=actor_id,
    )

async def reconcile_member_access(
    member: discord.Member,
    *,
    guild_state: Optional[Mapping[str, Any]] = None,
    member_state: Optional[Mapping[str, Any]] = None,
) -> str:
    guild = member.guild
    state = dict(guild_state or await load_guild_setup_state(guild.id))
    if not state.get("gate_active") or str(state.get("access_mode")) != ACCESS_MODE_STRICT:
        return "gate_inactive"
    if _member_exempt(member):
        return "exempt"

    access_role = _resolve_role(guild, state.get("access_role_id"))
    if not isinstance(access_role, discord.Role):
        return "access_role_missing"
    blocker = _access_role_blocker(guild, access_role)
    if blocker:
        return "access_role_blocked"

    current_member_state = dict(member_state or await load_member_setup_state(guild.id, member.id))
    effective_member_state = _effective_member_state(member, state, current_member_state)
    status = member_review_status(state, effective_member_state)

    if status.get("access_gated"):
        if access_role in member.roles:
            try:
                await member.remove_roles(access_role, reason="Dank Shield Member Setup review required")
                return "removed"
            except discord.HTTPException:
                return "remove_failed"
        return "gated"

    if status.get("is_current"):
        if not _prerequisite_ready(member, state):
            if access_role in member.roles:
                try:
                    await member.remove_roles(access_role, reason="Dank Shield Member Setup prerequisite missing")
                except discord.HTTPException:
                    return "remove_failed"
            return "waiting_prerequisite"
        if access_role not in member.roles:
            try:
                await member.add_roles(access_role, reason="Dank Shield Member Setup current")
                return "granted"
            except discord.HTTPException:
                return "grant_failed"
        return "current"

    return "unchanged"


async def reconcile_guild_access_gate(guild: discord.Guild) -> dict[str, int]:
    lock = _RECONCILE_LOCKS.get(int(guild.id))
    if lock is None:
        lock = asyncio.Lock()
        _RECONCILE_LOCKS[int(guild.id)] = lock
    if lock.locked():
        return {"skipped_busy": 1}

    async with lock:
        state = await load_guild_setup_state(guild.id, refresh=True)
        if not state.get("gate_active") or str(state.get("access_mode")) != ACCESS_MODE_STRICT:
            return {"gate_inactive": 1}
        snapshots = await load_guild_member_setup_states(guild.id)
        counts: dict[str, int] = {}
        for index, member in enumerate(list(guild.members)):
            if not isinstance(member, discord.Member) or member.bot:
                continue
            result = await reconcile_member_access(
                member,
                guild_state=state,
                member_state=snapshots.get(member.id, default_member_setup_state()),
            )
            counts[result] = counts.get(result, 0) + 1
            if index and index % 25 == 0:
                await asyncio.sleep(0)
        return counts


def _schedule_guild_reconcile(guild: discord.Guild) -> None:
    try:
        asyncio.create_task(reconcile_guild_access_gate(guild))
    except Exception:
        pass


async def _on_member_setup_join(member: discord.Member) -> None:
    try:
        await reconcile_member_access(member)
    except Exception:
        pass


async def _on_member_setup_update(before: discord.Member, after: discord.Member) -> None:
    try:
        before_ids = {int(role.id) for role in before.roles}
        after_ids = {int(role.id) for role in after.roles}
        if before_ids == after_ids:
            return
        state = await load_guild_setup_state(after.guild.id)
        watched = {
            _safe_int(state.get("access_role_id"), 0),
            _safe_int(state.get("prerequisite_role_id"), 0),
        }
        changed = before_ids.symmetric_difference(after_ids)
        if watched.intersection(changed):
            await reconcile_member_access(after, guild_state=state)
    except Exception:
        pass


async def _on_member_setup_guild_available(guild: discord.Guild) -> None:
    try:
        state = await recover_interrupted_gate_transition(guild)
        if state.get("gate_transition"):
            print(
                "❌ member_setup gate transition still requires recovery "
                f"guild={guild.id} transition={state.get('gate_transition')}"
            )
            return
        if state.get("gate_active"):
            _schedule_guild_reconcile(guild)
    except Exception as exc:
        print(f"⚠️ member_setup guild-available recovery failed guild={guild.id}: {type(exc).__name__}: {exc}")


def install_member_setup_runtime(bot: Any, *, strict: bool = False) -> bool:
    global _RUNTIME_INSTALLED
    if _RUNTIME_INSTALLED:
        return True
    try:
        add_listener = getattr(bot, "add_listener", None)
        if not callable(add_listener):
            raise RuntimeError("Discord client has no callable add_listener")
        add_listener(_on_member_setup_join, "on_member_join")
        add_listener(_on_member_setup_update, "on_member_update")
        add_listener(_on_member_setup_guild_available, "on_guild_available")
        _RUNTIME_INSTALLED = True
        print("✅ member_setup runtime ready join/update/guild_available listeners=True")
        return True
    except Exception as exc:
        print(f"❌ member_setup runtime unavailable: {type(exc).__name__}: {exc}")
        if strict:
            raise
        return False


__all__ = [
    "MemberSetupAdminView",
    "MemberSetupView",
    "activate_strict_gate",
    "gate_health",
    "install_member_setup_runtime",
    "open_member_setup",
    "open_member_setup_admin",
    "refresh_public_member_setup_panel",
    "recover_interrupted_gate_transition",
    "reconcile_guild_access_gate",
    "reconcile_member_access",
    "suspend_strict_gate",
]
