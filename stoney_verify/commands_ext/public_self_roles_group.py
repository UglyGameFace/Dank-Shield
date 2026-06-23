from __future__ import annotations

import asyncio
import re
from time import monotonic
from typing import Any, Optional

import discord
from discord import app_commands

from .public_setup_group import _require_setup_permission, dank_group


SELF_ROLE_PREFIX = "dank:selfrole:v1:"
PROFILE_PREFIX = "dank:profile:v1:"

_ATTACHED = False
_LISTENER_ATTACHED = False
_CONTEXT_MENU_ATTACHED = False
_PROFILE_PANEL_VIEW_REGISTERED = False

_PROFILE_PANEL_HARD_LOCKS: dict[tuple[int, int], asyncio.Lock] = {}
_PROFILE_PANEL_BLOCK_UNTIL: dict[tuple[int, int], float] = {}
_PROFILE_PANEL_LAST_NOTICE: dict[tuple[int, int], float] = {}
_PROFILE_PANEL_HARD_COOLDOWN_SECONDS = 2.5
_PROFILE_PANEL_NOTICE_SECONDS = 3.0
_PROFILE_PANEL_SESSIONS: dict[tuple[int, int, str], float] = {}
_PROFILE_PANEL_SESSION_TTL_SECONDS = 45.0

_PROFILE_LOCKS: dict[tuple[int, int], asyncio.Lock] = {}
_PROFILE_LAST_CLICK: dict[tuple[int, int], float] = {}
_PROFILE_COOLDOWN_SECONDS = 1.0


DEFAULT_PRONOUN_ROLE_NAMES: tuple[str, ...] = (
    "Pronouns: he/him",
    "Pronouns: she/her",
    "Pronouns: they/them",
    "Pronouns: he/they",
    "Pronouns: she/they",
    "Pronouns: it/its",
    "Pronouns: any pronouns",
    "Pronouns: no pronouns",
    "Pronouns: ask me",
)

DEFAULT_IDENTITY_ROLE_NAMES: tuple[str, ...] = (
    "Identity: man",
    "Identity: woman",
    "Identity: non-binary",
    "Identity: genderfluid",
    "Identity: agender",
    "Identity: trans",
    "Identity: questioning",
    "Identity: prefer not to say",
)

DEFAULT_INTEREST_ROLE_NAMES: tuple[str, ...] = (
    "Interest: gaming",
    "Interest: memes",
    "Interest: music",
    "Interest: movies",
    "Interest: anime",
    "Interest: smoke lounge",
    "Interest: late-night chat",
)

PROFILE_CATEGORIES: dict[str, tuple[str, str, tuple[str, ...], str]] = {
    "pronouns": (
        "🪪",
        "Pronouns",
        DEFAULT_PRONOUN_ROLE_NAMES,
        "Choose the pronoun roles you want shown. You can select more than one.",
    ),
    "identity": (
        "🌈",
        "Identity",
        DEFAULT_IDENTITY_ROLE_NAMES,
        "Choose optional identity roles. These are cosmetic only and never control access.",
    ),
    "interests": (
        "🎮",
        "Interests",
        DEFAULT_INTEREST_ROLE_NAMES,
        "Choose interests so other members know what you like talking about. These do not ping you or unlock access.",
    ),
}

profile_group = app_commands.Group(
    name="profile",
    description="Build and use member profile panels.",
)

roles_group = app_commands.Group(
    name="roles",
    description="Advanced role-panel tools.",
)


def _role_name_key(name: str) -> str:
    return str(name or "").strip().casefold()


def _find_role_by_name(guild: discord.Guild, name: str) -> Optional[discord.Role]:
    target = _role_name_key(name)
    for role in list(getattr(guild, "roles", []) or []):
        if isinstance(role, discord.Role) and _role_name_key(role.name) == target:
            return role
    return None


def _can_manage(role: discord.Role, guild: discord.Guild) -> tuple[bool, str]:
    me = guild.me
    if not isinstance(me, discord.Member):
        return False, "Dank Shield could not resolve its bot member."
    try:
        if not (me.guild_permissions.manage_roles or me.guild_permissions.administrator):
            return False, "Dank Shield is missing Manage Roles."
        if role >= me.top_role:
            return False, f"Move Dank Shield's role above {role.mention}."
        if role.is_default() or role.managed:
            return False, f"{role.mention} cannot be member-assigned."
    except Exception:
        return False, "Discord role hierarchy could not be checked."
    return True, ""


def _bot_can_create_roles(guild: discord.Guild) -> tuple[bool, str]:
    me = guild.me
    if not isinstance(me, discord.Member):
        return False, "Dank Shield could not resolve its bot member."
    try:
        if not (me.guild_permissions.manage_roles or me.guild_permissions.administrator):
            return False, "Dank Shield is missing Manage Roles."
    except Exception:
        return False, "Discord role permissions could not be checked."
    return True, ""


async def _ensure_role(guild: discord.Guild, name: str, *, reason: str) -> discord.Role:
    existing = _find_role_by_name(guild, name)
    if isinstance(existing, discord.Role):
        return existing
    return await guild.create_role(name=name[:100], mentionable=False, reason=reason)


async def _ack_profile_action(interaction: discord.Interaction) -> None:
    """Acknowledge slow profile/self-role actions before Discord times them out.

    Public panels stay permanent, but role edits/channel fixes can take longer
    than Discord's component response window. Defer before slow work so users do
    not see "interaction failed".
    """
    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass


async def _reply(interaction: discord.Interaction, content: str, *, ok: bool = True) -> None:
    prefix = "✅ " if ok else "❌ "
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                prefix + content,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.followup.send(
                prefix + content,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
    except Exception:
        pass


def _all_profile_role_names() -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for _key, payload in PROFILE_CATEGORIES.items():
        for name in payload[2]:
            clean = str(name or "").strip()
            if clean and clean.casefold() not in seen:
                seen.add(clean.casefold())
                out.append(clean)
    return tuple(out)


def _category_roles(guild: discord.Guild, category_key: str) -> list[discord.Role]:
    payload = PROFILE_CATEGORIES.get(str(category_key or "").lower())
    if not payload:
        return []
    roles: list[discord.Role] = []
    for name in payload[2]:
        role = _find_role_by_name(guild, name)
        if isinstance(role, discord.Role):
            roles.append(role)
    return roles


def _short_role_label(role_name: str) -> str:
    text = str(role_name or "").strip()
    for prefix in ("Pronouns: ", "Identity: ", "Interest: "):
        if text.casefold().startswith(prefix.casefold()):
            text = text[len(prefix):]
            break
    return text[:100] or "Role"


def _profile_channel_missing(guild: discord.Guild, channel: discord.TextChannel) -> list[str]:
    me = guild.me
    if not isinstance(me, discord.Member):
        return ["Resolve bot member"]

    perms = channel.permissions_for(me)
    missing: list[str] = []
    if not perms.view_channel:
        missing.append("View Channel")
    if not perms.send_messages:
        missing.append("Send Messages")
    if not perms.embed_links:
        missing.append("Embed Links")
    return missing


def _profile_manual_blockers(guild: discord.Guild) -> list[str]:
    me = guild.me
    if not isinstance(me, discord.Member):
        return ["Dank Shield bot member could not be resolved."]

    blockers: list[str] = []
    if not (me.guild_permissions.manage_roles or me.guild_permissions.administrator):
        blockers.append("Dank Shield is missing Manage Roles.")

    for role_name in _all_profile_role_names():
        role = _find_role_by_name(guild, role_name)
        if isinstance(role, discord.Role):
            ok, why = _can_manage(role, guild)
            if not ok:
                blockers.append(why)
                break

    return blockers


def _profile_can_fix_channel(guild: discord.Guild) -> bool:
    me = guild.me
    if not isinstance(me, discord.Member):
        return False
    return bool(me.guild_permissions.manage_channels or me.guild_permissions.administrator)


def _profile_builder_status(guild: discord.Guild, channel: discord.TextChannel) -> tuple[bool, list[str], list[str]]:
    manual = _profile_manual_blockers(guild)
    channel_missing = _profile_channel_missing(guild, channel)
    fixable = channel_missing if channel_missing and _profile_can_fix_channel(guild) else []

    if channel_missing and not fixable:
        manual.append(f"Missing in {channel.mention}: " + ", ".join(channel_missing))

    ready = not manual and not channel_missing
    return ready, fixable, manual


def _member_profile_roles(member: discord.Member, names: tuple[str, ...]) -> list[discord.Role]:
    keys = {_role_name_key(name) for name in names}
    return [role for role in member.roles if _role_name_key(role.name) in keys]


def _role_labels(roles: list[discord.Role]) -> str:
    if not roles:
        return "Not set"
    return ", ".join(_short_role_label(role.name) for role in roles)[:1024]


PROFILE_CARD_PAGE_SIZE = 8
PROFILE_CARD_FIELD_SOFT_LIMIT = 900


def _profile_role_detail_lines(roles: list[discord.Role]) -> str:
    if not roles:
        return "Not set"
    lines = []
    for role in roles:
        lines.append(f"• {_short_role_label(role.name)}")
    return "\n".join(lines)[:1024]


def _profile_role_entries(member: discord.Member) -> list[tuple[str, str, discord.Role]]:
    entries: list[tuple[str, str, discord.Role]] = []

    for role in _member_profile_roles(member, DEFAULT_PRONOUN_ROLE_NAMES):
        entries.append(("🪪", "Pronouns", role))

    for role in _member_profile_roles(member, DEFAULT_IDENTITY_ROLE_NAMES):
        entries.append(("🌈", "Identity", role))

    try:
        interest_names = DEFAULT_INTEREST_ROLE_NAMES
    except NameError:
        interest_names = tuple()

    for role in _member_profile_roles(member, interest_names):
        entries.append(("🎮", "Interests", role))

    return entries


def _profile_card_needs_pagination(member: discord.Member) -> bool:
    pronouns = _member_profile_roles(member, DEFAULT_PRONOUN_ROLE_NAMES)
    identity = _member_profile_roles(member, DEFAULT_IDENTITY_ROLE_NAMES)

    try:
        interest_names = DEFAULT_INTEREST_ROLE_NAMES
    except NameError:
        interest_names = tuple()

    interests = _member_profile_roles(member, interest_names)
    all_roles = pronouns + identity + interests

    if len(all_roles) > PROFILE_CARD_PAGE_SIZE:
        return True

    for role_group in (pronouns, identity, interests):
        if len(_profile_role_detail_lines(role_group)) > PROFILE_CARD_FIELD_SOFT_LIMIT:
            return True

    return False


def _profile_card_page_count(member: discord.Member) -> int:
    entries = _profile_role_entries(member)
    if not entries:
        return 1
    return max(1, (len(entries) + PROFILE_CARD_PAGE_SIZE - 1) // PROFILE_CARD_PAGE_SIZE)


def _profile_card(member: discord.Member, *, page: int = 0) -> discord.Embed:
    pronouns = _member_profile_roles(member, DEFAULT_PRONOUN_ROLE_NAMES)
    identity = _member_profile_roles(member, DEFAULT_IDENTITY_ROLE_NAMES)

    try:
        interest_names = DEFAULT_INTEREST_ROLE_NAMES
    except NameError:
        interest_names = tuple()

    interests = _member_profile_roles(member, interest_names)
    total_roles = len(pronouns) + len(identity) + len(interests)

    needs_pages = _profile_card_needs_pagination(member)
    page_count = _profile_card_page_count(member)
    page = max(0, min(int(page or 0), page_count - 1))

    embed = discord.Embed(
        title=f"{member.display_name}'s Profile",
        description="These profile labels are cosmetic only.",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )

    if not needs_pages:
        embed.add_field(name="🪪 Pronouns", value=_profile_role_detail_lines(pronouns), inline=False)
        embed.add_field(name="🌈 Identity", value=_profile_role_detail_lines(identity), inline=False)
        embed.add_field(name="🎮 Interests", value=_profile_role_detail_lines(interests), inline=False)
    else:
        entries = _profile_role_entries(member)
        start = page * PROFILE_CARD_PAGE_SIZE
        shown = entries[start:start + PROFILE_CARD_PAGE_SIZE]

        if shown:
            lines = [
                f"• {emoji} **{section}:** {_short_role_label(role.name)}"
                for emoji, section, role in shown
            ]
            embed.add_field(
                name=f"Profile roles {start + 1}-{start + len(shown)} of {len(entries)}",
                value="\n".join(lines)[:1024],
                inline=False,
            )
        else:
            embed.add_field(name="Profile roles", value="Not set", inline=False)

        embed.add_field(
            name="Pages",
            value=f"Page {page + 1}/{page_count}",
            inline=True,
        )

    embed.add_field(name="Profile roles", value=str(total_roles), inline=True)

    if member.joined_at:
        embed.add_field(name="Joined server", value=discord.utils.format_dt(member.joined_at, style="D"), inline=True)
    embed.add_field(name="Account created", value=discord.utils.format_dt(member.created_at, style="D"), inline=True)

    try:
        embed.set_thumbnail(url=member.display_avatar.url)
    except Exception:
        pass

    embed.set_footer(text="Dank Shield profile")
    return embed


class ProfileCardPageView(discord.ui.View):
    def __init__(self, *, member_id: int, page: int = 0) -> None:
        super().__init__(timeout=300)
        self.member_id = int(member_id)
        self.page = max(0, int(page or 0))

    async def _flip(self, interaction: discord.Interaction, delta: int) -> None:
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside the server.", ok=False)

        member = guild.get_member(self.member_id)
        if not isinstance(member, discord.Member):
            return await _reply(interaction, "That member is no longer available in this server.", ok=False)

        page_count = _profile_card_page_count(member)
        next_page = max(0, min(self.page + int(delta), page_count - 1))

        await interaction.response.edit_message(
            embed=_profile_card(member, page=next_page),
            view=_profile_card_view(member, page=next_page),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Previous", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank:profile:v1:profile_page_prev", row=0)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._flip(interaction, -1)

    @discord.ui.button(label="Next", emoji="➡️", style=discord.ButtonStyle.secondary, custom_id="dank:profile:v1:profile_page_next", row=0)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._flip(interaction, 1)


def _profile_card_view(member: discord.Member, *, page: int = 0) -> Optional[discord.ui.View]:
    if not _profile_card_needs_pagination(member):
        return None

    view = ProfileCardPageView(member_id=int(member.id), page=page)
    page_count = _profile_card_page_count(member)

    for child in view.children:
        try:
            if getattr(child, "custom_id", "") == "dank:profile:v1:profile_page_prev":
                child.disabled = int(page or 0) <= 0
            if getattr(child, "custom_id", "") == "dank:profile:v1:profile_page_next":
                child.disabled = int(page or 0) >= page_count - 1
        except Exception:
            pass

    return view


def _profile_panel_embed(guild: discord.Guild, *, title: str = "Profile Panel") -> discord.Embed:
    embed = discord.Embed(
        title=title[:256],
        description=(
            "Customize your server profile with optional pronoun, identity, and interest roles.\n\n"
            "These roles are cosmetic only. They never control verification, tickets, moderation, staff access, or server permissions."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Available profile sections",
        value="\n".join(
            f"{emoji} **{label}**"
            for _key, (emoji, label, _names, _desc) in PROFILE_CATEGORIES.items()
        ),
        inline=False,
    )
    embed.add_field(
        name="Missing Identity?",
        value=(
            "Use **Missing Identity?** only when your identity is not listed. "
            "This sends a staff review request and does not create or assign a role automatically."
        ),
        inline=False,
    )
    embed.set_footer(text="Dank Shield profile panel")
    return embed


def _profile_terms_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📘 Profile Terms",
        description=(
            "These labels are optional. Pick only what fits you, skip anything you do not want to share, "
            "and use Missing Identity if your label is not listed."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="🪪 Pronouns",
        value=(
            "**Pronouns** are the words people use when referring to you, like he/him, she/her, they/them, or no pronouns.\n"
            "Use the option that feels right for this server."
        ),
        inline=False,
    )
    embed.add_field(
        name="🌈 Identity",
        value=(
            "**Man** — someone who identifies as a man.\n"
            "**Woman** — someone who identifies as a woman.\n"
            "**Non-binary** — someone whose gender identity is not only man or woman.\n"
            "**Genderfluid** — someone whose gender identity may change over time.\n"
            "**Agender** — someone who does not identify with a gender, or has little/no gender connection.\n"
            "**Trans** — someone whose gender identity differs from what they were assigned at birth.\n"
            "**Questioning** — someone still exploring which label fits best.\n"
            "**Prefer not to say** — choose this if you do not want to share."
        ),
        inline=False,
    )
    embed.add_field(
        name="🎮 Interests",
        value=(
            "Interests are conversation tags. They help people find common topics. "
            "They do not ping you, unlock channels, verify you, or give permissions."
        ),
        inline=False,
    )
    embed.add_field(
        name="✍️ Missing Identity / ➕ Missing Interest",
        value=(
            "These send a staff review request. They do not create or assign roles automatically."
        ),
        inline=False,
    )
    embed.set_footer(text="Dank Shield profile help")
    return embed


def _profile_role_lines(member: discord.Member, names: tuple[str, ...]) -> str:
    roles = _member_profile_roles(member, names)
    if not roles:
        return "None"
    return "\n".join(f"• {_short_role_label(role.name)}" for role in roles)[:1024]


def _profile_full_roles_embed(member: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        title=f"📋 {member.display_name}'s Profile Roles",
        description="Exact Discord roles currently shown on this member's profile.",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="🪪 Pronoun roles", value=_profile_role_lines(member, DEFAULT_PRONOUN_ROLE_NAMES), inline=False)
    embed.add_field(name="🌈 Identity roles", value=_profile_role_lines(member, DEFAULT_IDENTITY_ROLE_NAMES), inline=False)

    try:
        interest_names = DEFAULT_INTEREST_ROLE_NAMES
    except NameError:
        interest_names = tuple()
    if interest_names:
        embed.add_field(name="🎮 Interest roles", value=_profile_role_lines(member, interest_names), inline=False)

    try:
        embed.set_thumbnail(url=member.display_avatar.url)
    except Exception:
        pass

    embed.set_footer(text="Dank Shield profile roles")
    return embed


class ProfileCardActionView(discord.ui.View):
    def __init__(self, *, member_id: int) -> None:
        super().__init__(timeout=300)
        self.member_id = int(member_id)

    @discord.ui.button(label="View Full Profile Roles", emoji="📋", style=discord.ButtonStyle.secondary, custom_id="dank:profile:v1:full_roles", row=0)
    async def full_roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside the server.", ok=False)

        member = guild.get_member(self.member_id)
        if not isinstance(member, discord.Member):
            return await _reply(interaction, "That member is no longer available in this server.", ok=False)

        await interaction.response.send_message(
            embed=_profile_full_roles_embed(member),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


def _profile_human_members(guild: discord.Guild) -> list[discord.Member]:
    members: list[discord.Member] = []
    for member in list(getattr(guild, "members", []) or []):
        if isinstance(member, discord.Member) and not member.bot:
            members.append(member)

    def sort_key(member: discord.Member) -> tuple[str, str]:
        return (
            str(getattr(member, "display_name", "") or "").casefold(),
            str(getattr(member, "name", "") or "").casefold(),
        )

    return sorted(members, key=sort_key)


def _profile_member_label(member: discord.Member) -> str:
    display = str(getattr(member, "display_name", "") or getattr(member, "name", "") or "Member")
    username = str(getattr(member, "name", "") or member)
    label = display if display.casefold() == username.casefold() else f"{display} (@{username})"
    return label[:100]


def _profile_member_description(member: discord.Member) -> str:
    joined = ""
    try:
        if member.joined_at:
            joined = f" • joined {member.joined_at.date().isoformat()}"
    except Exception:
        joined = ""
    return f"Human member • ID {member.id}{joined}"[:100]


def _profile_member_page_embed(guild: discord.Guild, *, page: int, per_page: int = 25) -> discord.Embed:
    members = _profile_human_members(guild)
    total = len(members)
    max_page = max(0, (total - 1) // per_page) if total else 0
    page = max(0, min(int(page or 0), max_page))

    start = page * per_page
    shown = members[start:start + per_page]

    embed = discord.Embed(
        title="👥 View Member Profile",
        description=(
            "Pick a human member from Dank Shield's list. Bots are hidden.\n"
            "Use Next / Previous if the member is not on this page."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )

    if not shown:
        embed.add_field(
            name="No human members found",
            value=(
                "Dank Shield could not see cached human members. "
                "Make sure the bot has Server Members Intent enabled and restart the bot."
            ),
            inline=False,
        )
    else:
        lines = []
        for idx, member in enumerate(shown, start=start + 1):
            lines.append(f"`{idx}.` {member.mention} — `{member.display_name}`")
        embed.add_field(name=f"Members {start + 1}-{start + len(shown)} of {total}", value="\n".join(lines)[:1024], inline=False)

    embed.set_footer(text=f"Page {page + 1}/{max_page + 1} • human members only")
    return embed


class ProfileMemberListSelect(discord.ui.Select):
    def __init__(self, members: list[discord.Member]) -> None:
        options = [
            discord.SelectOption(
                label=_profile_member_label(member),
                description=_profile_member_description(member),
                value=str(member.id),
            )
            for member in members[:25]
            if not member.bot
        ]

        if not options:
            options = [discord.SelectOption(label="No human members found", value="0", description="Check bot member intent/cache.")]

        super().__init__(
            placeholder="Pick a member from this page…",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="dank:profile:v1:member_list_select",
            disabled=options[0].value == "0",
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside the server.", ok=False)

        raw = self.values[0] if self.values else "0"
        member = guild.get_member(int(raw)) if str(raw).isdigit() else None

        if not isinstance(member, discord.Member) or member.bot:
            return await _reply(interaction, "That human member is no longer available.", ok=False)

        await interaction.response.send_message(
            embed=_profile_card(member),
            view=_profile_card_view(member),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class ProfileMemberListView(discord.ui.View):
    def __init__(self, guild: discord.Guild, *, page: int = 0, per_page: int = 25) -> None:
        super().__init__(timeout=300)
        self.page = max(0, int(page or 0))
        self.per_page = int(per_page or 25)

        members = _profile_human_members(guild)
        self.total = len(members)
        self.max_page = max(0, (self.total - 1) // self.per_page) if self.total else 0
        self.page = min(self.page, self.max_page)

        start = self.page * self.per_page
        shown = members[start:start + self.per_page]

        self.add_item(ProfileMemberListSelect(shown))

    async def _flip(self, interaction: discord.Interaction, delta: int) -> None:
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside the server.", ok=False)

        next_page = max(0, min(self.page + int(delta), self.max_page))
        await interaction.response.edit_message(
            embed=_profile_member_page_embed(guild, page=next_page, per_page=self.per_page),
            view=ProfileMemberListView(guild, page=next_page, per_page=self.per_page),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Previous", emoji="⬅️", style=discord.ButtonStyle.secondary, custom_id="dank:profile:v1:member_list_prev", row=1)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._flip(interaction, -1)

    @discord.ui.button(label="Next", emoji="➡️", style=discord.ButtonStyle.secondary, custom_id="dank:profile:v1:member_list_next", row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._flip(interaction, 1)

    @discord.ui.button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, custom_id="dank:profile:v1:member_list_refresh", row=1)
    async def refresh_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild = interaction.guild
        if guild is None:
            return await _reply(interaction, "This only works inside the server.", ok=False)
        await interaction.response.edit_message(
            embed=_profile_member_page_embed(guild, page=self.page, per_page=self.per_page),
            view=ProfileMemberListView(guild, page=self.page, per_page=self.per_page),
            allowed_mentions=discord.AllowedMentions.none(),
        )


class ProfileMemberPickView(discord.ui.View):
    """Deprecated compatibility wrapper."""

    def __init__(self) -> None:
        super().__init__(timeout=180)


class ProfilePanelView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(discord.ui.Button(label="View My Profile", emoji="👤", style=discord.ButtonStyle.primary, custom_id=f"{PROFILE_PREFIX}view", row=0))
        self.add_item(discord.ui.Button(label="View Member Profile", emoji="👥", style=discord.ButtonStyle.secondary, custom_id=f"{PROFILE_PREFIX}pick_member", row=0))
        self.add_item(discord.ui.Button(label="Learn Terms", emoji="📘", style=discord.ButtonStyle.secondary, custom_id=f"{PROFILE_PREFIX}learn", row=0))
        self.add_item(discord.ui.Button(label="Pronouns", emoji="🪪", style=discord.ButtonStyle.secondary, custom_id=f"{PROFILE_PREFIX}open:pronouns", row=1))
        self.add_item(discord.ui.Button(label="Identity", emoji="🌈", style=discord.ButtonStyle.secondary, custom_id=f"{PROFILE_PREFIX}open:identity", row=1))
        self.add_item(discord.ui.Button(label="Interests", emoji="🎮", style=discord.ButtonStyle.secondary, custom_id=f"{PROFILE_PREFIX}open:interests", row=2))
        self.add_item(discord.ui.Button(label="Suggest Missing Interest", emoji="➕", style=discord.ButtonStyle.secondary, custom_id=f"{PROFILE_PREFIX}missing_interest", row=2))
        self.add_item(discord.ui.Button(label="Clear Profile Roles", emoji="🧹", style=discord.ButtonStyle.danger, custom_id=f"{PROFILE_PREFIX}clear", row=3))
        self.add_item(discord.ui.Button(label="Missing Identity?", emoji="✍️", style=discord.ButtonStyle.secondary, custom_id=f"{PROFILE_PREFIX}missing", row=3))


def register_profile_panel_runtime(bot: Any) -> bool:
    """Register the public Profile Panel as persistent across bot restarts."""

    global _PROFILE_PANEL_VIEW_REGISTERED
    if _PROFILE_PANEL_VIEW_REGISTERED:
        return True

    try:
        add_view = getattr(bot, "add_view", None)
        if callable(add_view):
            add_view(ProfilePanelView())
            _PROFILE_PANEL_VIEW_REGISTERED = True
            print("✅ profile_panel: persistent ProfilePanelView registered")
            return True
    except Exception as exc:
        try:
            print(f"⚠️ profile_panel: persistent view registration failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass

    return False


class ProfileCategorySelectView(discord.ui.View):
    def __init__(self, guild: discord.Guild, member: discord.Member, category_key: str) -> None:
        super().__init__(timeout=300)

        options: list[discord.SelectOption] = []
        for role in _category_roles(guild, category_key)[:25]:
            options.append(
                discord.SelectOption(
                    label=_short_role_label(role.name),
                    value=str(role.id),
                    default=role in member.roles,
                )
            )

        if not options:
            options.append(discord.SelectOption(label="No roles available", value="0"))

        self.add_item(
            discord.ui.Select(
                placeholder="Pick your choices…",
                min_values=0,
                max_values=max(1, len(options)),
                options=options,
                custom_id=f"{PROFILE_PREFIX}select:{category_key}",
            )
        )


class MissingIdentityModal(discord.ui.Modal, title="Missing Identity?"):
    label = discord.ui.TextInput(
        label="Identity missing from the list",
        placeholder="Example: genderqueer, demigirl, Two-Spirit, questioning, etc.",
        min_length=2,
        max_length=80,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if guild is None or member is None:
            return await _reply(interaction, "This only works inside the server.", ok=False)

        clean = str(self.label.value or "").replace("@everyone", "everyone").replace("@here", "here").strip()
        clean = " ".join(clean.split())[:80]
        if not clean:
            return await _reply(interaction, "Missing identity label was empty.", ok=False)

        channel = await _staff_review_channel(guild)
        if not isinstance(channel, discord.TextChannel):
            return await _reply(
                interaction,
                "No staff/modlog channel found for Missing Identity requests. Set a modlog channel first.",
                ok=False,
            )

        embed = discord.Embed(
            title="🪪 Missing Identity Request",
            description="A member says their identity is missing from the profile list. Staff review is required.",
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Member", value=f"{member.mention}\n`{member}` (`{member.id}`)", inline=False)
        embed.add_field(name="Requested label", value=f"`{clean}`", inline=False)
        embed.add_field(
            name="Important",
            value="This request does not create or assign a role automatically. Approve manually only if appropriate for this server.",
            inline=False,
        )
        embed.set_footer(text="Dank Shield profile builder")

        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            await _reply(interaction, f"Missing Identity request sent to staff: `{clean}`", ok=True)
        except Exception as exc:
            await _reply(interaction, f"Could not send Missing Identity request: {type(exc).__name__}.", ok=False)


async def _staff_review_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    try:
        from stoney_verify.guild_config import get_guild_config
        from stoney_verify.commands_ext import public_modlog_group as modlog

        cfg = await get_guild_config(int(guild.id), refresh=True)
        channel = modlog._modlog_channel(guild, cfg)
        if isinstance(channel, discord.TextChannel):
            return channel
    except Exception:
        pass

    for name in ("mod-log", "modlog", "staff-log", "staff", "support"):
        for channel in list(getattr(guild, "text_channels", []) or []):
            raw = str(getattr(channel, "name", "") or "").lower().replace("_", "-").replace(" ", "-")
            if name in raw and isinstance(channel, discord.TextChannel):
                return channel
    return None


BANNED_INTEREST_WORDS: set[str] = {
    "admin", "mod", "moderator", "staff", "owner", "manager",
    "everyone", "here", "discord", "nitro",
}


def _clean_missing_interest(value: Any) -> tuple[str, str | None]:
    raw = str(value or "").strip().lower()
    raw = raw.replace("@everyone", "everyone").replace("@here", "here")
    raw = " ".join(raw.split())
    raw = re.sub(r"[^a-z0-9 #+&/.-]", "", raw).strip(" .-/")
    raw = raw[:32]

    if len(raw) < 2:
        return "", "Interest is too short."
    if "http://" in raw or "https://" in raw or "discord.gg" in raw:
        return "", "Links are not allowed."
    if any(word in raw.split() for word in BANNED_INTEREST_WORDS):
        return "", "That interest name is reserved."
    return raw, None


class MissingInterestModal(discord.ui.Modal, title="Suggest Missing Interest"):
    interest = discord.ui.TextInput(
        label="Interest missing from the list",
        placeholder="Example: cars, art, cooking, sports",
        min_length=2,
        max_length=32,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if guild is None or member is None:
            return await _reply(interaction, "This only works inside the server.", ok=False)

        clean, error = _clean_missing_interest(str(self.interest.value or ""))
        if error:
            return await _reply(interaction, error, ok=False)

        channel = await _staff_review_channel(guild)
        if not isinstance(channel, discord.TextChannel):
            return await _reply(interaction, "No staff/modlog channel found for missing interest requests.", ok=False)

        embed = discord.Embed(
            title="🎮 Missing Interest Request",
            description=(
                "A member says this interest is missing from the profile list.\n\n"
                "This does not create a role automatically."
            ),
            color=discord.Color.blurple(),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Member", value=f"{member.mention}\n`{member}` (`{member.id}`)", inline=False)
        embed.add_field(name="Requested interest", value=f"`{clean}`", inline=False)
        embed.add_field(
            name="Safe staff action",
            value=(
                "Approve manually only if appropriate. "
                "Recommended role name if approved: "
                f"`Interest: {clean}`"
            ),
            inline=False,
        )
        embed.set_footer(text="Dank Shield profile builder • staff-reviewed request")

        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        await _reply(interaction, f"Missing interest request sent to staff: `{clean}`", ok=True)


class ProfileBuilderView(discord.ui.View):
    def __init__(self, *, author_id: int, ready: bool, fixable: bool, title: str) -> None:
        super().__init__(timeout=300)
        self.author_id = int(author_id)
        self.title_text = str(title or "Profile Panel")[:80]

        if ready:
            self.add_item(discord.ui.Button(label="Create / Repair Roles + Post Panel", emoji="🌿", style=discord.ButtonStyle.success, custom_id=f"{PROFILE_PREFIX}builder:post", row=0))
            self.add_item(discord.ui.Button(label="Preview Panel", emoji="👀", style=discord.ButtonStyle.secondary, custom_id=f"{PROFILE_PREFIX}builder:preview", row=0))
        elif fixable:
            self.add_item(discord.ui.Button(label="Fix Channel Permissions", emoji="🛠️", style=discord.ButtonStyle.primary, custom_id=f"{PROFILE_PREFIX}builder:fix", row=0))

        self.add_item(discord.ui.Button(label="Health", emoji="🩺", style=discord.ButtonStyle.secondary, custom_id=f"{PROFILE_PREFIX}builder:health", row=1))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.author_id:
            await _reply(interaction, "Only the staff member who opened this builder can use it.", ok=False)
            return False
        return True


async def _create_profile_roles(interaction: discord.Interaction, guild: discord.Guild) -> tuple[list[discord.Role], list[str], list[str]]:
    roles: list[discord.Role] = []
    created: list[str] = []
    reused: list[str] = []

    for name in _all_profile_role_names():
        before = _find_role_by_name(guild, name)
        role = await _ensure_role(
            guild,
            name,
            reason=f"Dank Shield profile builder setup by {interaction.user} ({interaction.user.id})",
        )
        ok, why = _can_manage(role, guild)
        if not ok:
            raise RuntimeError(why)
        roles.append(role)
        (reused if before else created).append(role.name)

    return roles, created, reused


async def _post_profile_builder(interaction: discord.Interaction, *, title: str = "Profile Panel") -> None:
    guild = interaction.guild
    channel = interaction.channel

    if guild is None or not isinstance(channel, discord.TextChannel):
        return await _reply(interaction, "Run this inside the text channel where the profile panel should be posted.", ok=False)

    ready, fixable, manual = _profile_builder_status(guild, channel)

    embed = discord.Embed(
        title="🌿 Profile Builder",
        description=(
            "**Nothing has been created or posted yet.**\n\n"
            "This builder uses the current channel as the panel target. "
            "When ready, press **Create / Repair Roles + Post Panel**."
        ),
        color=discord.Color.green() if ready else discord.Color.gold(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Panel target", value=channel.mention, inline=False)
    embed.add_field(name="Default profile sections", value="🪪 Pronouns\n🌈 Identity\n🎮 Interests\n✍️ Missing Identity request\n➕ Missing Interest request", inline=False)
    embed.add_field(name="Status", value="✅ Ready" if ready else "⚠️ Not ready", inline=False)

    if fixable:
        embed.add_field(name="Bot can fix now", value="\n".join(f"• {x}" for x in fixable), inline=False)
    if manual:
        embed.add_field(name="Needs manual fix", value="\n".join(f"• {x}" for x in manual), inline=False)

    await interaction.response.send_message(
        embed=embed,
        view=ProfileBuilderView(
            author_id=int(interaction.user.id),
            ready=ready,
            fixable=bool(fixable),
            title=title,
        ),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


async def _handle_builder_action(interaction: discord.Interaction, action: str) -> bool:
    guild = interaction.guild
    channel = interaction.channel

    if guild is None or not isinstance(channel, discord.TextChannel):
        await _reply(interaction, "Run builder actions inside the panel target channel.", ok=False)
        return True

    ready, fixable, manual = _profile_builder_status(guild, channel)

    if action == "health":
        lines: list[str] = []
        if ready:
            lines.append("Ready.")
        if fixable:
            lines.append("Fixable: " + ", ".join(fixable))
        if manual:
            lines.append("Manual: " + " | ".join(manual))
        await _reply(interaction, "\n".join(lines) if lines else "No status found.", ok=ready)
        return True

    if action == "fix":
        await _ack_profile_action(interaction)
        if not fixable:
            await _reply(interaction, "No fixable channel permissions are missing right now.", ok=True)
            return True

        me = guild.me
        if not isinstance(me, discord.Member):
            await _reply(interaction, "Dank Shield bot member could not be resolved.", ok=False)
            return True

        try:
            await channel.set_permissions(
                me,
                view_channel=True,
                send_messages=True,
                embed_links=True,
                reason=f"Dank Shield profile builder permission repair by {interaction.user} ({interaction.user.id})",
            )
            await _reply(interaction, "Fixed channel permissions. Run `/dank profile builder` again.", ok=True)
        except Exception as exc:
            await _reply(interaction, f"Could not fix channel permissions: {type(exc).__name__}.", ok=False)
        return True

    if action == "preview":
        await interaction.response.send_message(
            embed=_profile_panel_embed(guild),
            view=ProfilePanelView(),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    if action == "post":
        if not ready:
            await _reply(interaction, "Builder is not ready. Use Health to see the exact blocker.", ok=False)
            return True

        try:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=True)
        except Exception:
            pass

        try:
            _roles, created, reused = await _create_profile_roles(interaction, guild)
            await channel.send(
                embed=_profile_panel_embed(guild),
                view=ProfilePanelView(),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await interaction.followup.send(
                f"✅ Profile panel posted in {channel.mention}. Roles created: {len(created)}. Reused: {len(reused)}.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except Exception as exc:
            await interaction.followup.send(
                f"❌ Could not create/post profile panel: `{type(exc).__name__}: {str(exc)[:250]}`",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        return True

    return False


def _profile_custom_id(interaction: discord.Interaction) -> str:
    try:
        data = interaction.data if isinstance(interaction.data, dict) else {}
        return str(data.get("custom_id") or "")
    except Exception:
        return ""


def _is_profile_interaction(custom_id: str) -> bool:
    return custom_id.startswith(PROFILE_PREFIX) or custom_id.startswith(SELF_ROLE_PREFIX)


async def _acquire_profile_gate(interaction: discord.Interaction, custom_id: str) -> Optional[tuple[int, int]]:
    if not _is_profile_interaction(custom_id):
        return None

    guild_id = int(interaction.guild.id) if interaction.guild else 0
    user_id = int(interaction.user.id)
    key = (guild_id, user_id)
    now = monotonic()

    if len(_PROFILE_LAST_CLICK) > 5000:
        stale = [k for k, ts in _PROFILE_LAST_CLICK.items() if now - ts > 120]
        for stale_key in stale[:1000]:
            _PROFILE_LAST_CLICK.pop(stale_key, None)
            lock = _PROFILE_LOCKS.get(stale_key)
            if lock is not None and not lock.locked():
                _PROFILE_LOCKS.pop(stale_key, None)

    lock = _PROFILE_LOCKS.setdefault(key, asyncio.Lock())

    if lock.locked():
        await _reply(interaction, "Already updating your profile. Wait a second.", ok=False)
        return None

    last = _PROFILE_LAST_CLICK.get(key, 0.0)
    if now - last < _PROFILE_COOLDOWN_SECONDS:
        await _reply(interaction, "One profile action at a time. Try again in a second.", ok=False)
        return None

    _PROFILE_LAST_CLICK[key] = now
    await lock.acquire()
    return key


def _release_profile_gate(key: Optional[tuple[int, int]]) -> None:
    if key is None:
        return
    lock = _PROFILE_LOCKS.get(key)
    if lock is not None and lock.locked():
        lock.release()


async def _handle_profile_interaction(interaction: discord.Interaction) -> bool:
    data = interaction.data if isinstance(interaction.data, dict) else {}
    custom_id = str(data.get("custom_id") or "")

    if not custom_id.startswith(PROFILE_PREFIX):
        return False

    suffix = custom_id[len(PROFILE_PREFIX):]

    if suffix.startswith("builder:"):
        return await _handle_builder_action(interaction, suffix.split(":", 1)[1])

    guild = interaction.guild
    member = interaction.user if isinstance(interaction.user, discord.Member) else None

    if guild is None or member is None:
        await _reply(interaction, "This only works inside the server.", ok=False)
        return True

    if suffix == "pick_member":
        await interaction.response.send_message(
            embed=_profile_member_page_embed(guild, page=0),
            view=ProfileMemberListView(guild, page=0),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    if suffix == "view":
        await interaction.response.send_message(
            embed=_profile_card(member),
            view=_profile_card_view(member),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    if suffix == "learn":
        await interaction.response.send_message(
            embed=_profile_terms_embed(),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    if suffix.startswith("open:"):
        category = suffix.split(":", 1)[1]
        payload = PROFILE_CATEGORIES.get(category)
        if not payload:
            await _reply(interaction, "That profile section no longer exists.", ok=False)
            return True
        emoji, label, _names, desc = payload
        await interaction.response.send_message(
            embed=discord.Embed(title=f"{emoji} {label}", description=desc, color=discord.Color.blurple()),
            view=ProfileCategorySelectView(guild, member, category),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    if suffix.startswith("select:"):
        await _ack_profile_action(interaction)
        category = suffix.split(":", 1)[1]
        raw_values = data.get("values") if isinstance(data.get("values"), list) else []
        selected = {int(value) for value in raw_values if str(value).isdigit() and int(value) > 0}
        category_roles = _category_roles(guild, category)

        to_add = [role for role in category_roles if int(role.id) in selected and role not in member.roles and _can_manage(role, guild)[0]]
        to_remove = [role for role in category_roles if int(role.id) not in selected and role in member.roles and _can_manage(role, guild)[0]]

        try:
            if to_add:
                await member.add_roles(*to_add, reason="Dank Shield profile picker")
            if to_remove:
                await member.remove_roles(*to_remove, reason="Dank Shield profile picker")
        except Exception as exc:
            await _reply(interaction, f"Could not update your profile roles: {type(exc).__name__}.", ok=False)
            return True

        changes: list[str] = []
        if to_add:
            changes.append("Added: " + ", ".join(role.mention for role in to_add))
        if to_remove:
            changes.append("Removed: " + ", ".join(role.mention for role in to_remove))

        await _reply(interaction, "\n".join(changes) if changes else "No profile changes needed.", ok=True)
        return True

    if suffix == "clear":
        await _ack_profile_action(interaction)
        roles: list[discord.Role] = []
        for category in PROFILE_CATEGORIES:
            roles.extend(
                role
                for role in _category_roles(guild, category)
                if role in member.roles and _can_manage(role, guild)[0]
            )
        if roles:
            try:
                await member.remove_roles(*roles, reason="Dank Shield profile clear")
                await _reply(interaction, "Removed your optional profile roles.", ok=True)
            except Exception as exc:
                await _reply(interaction, f"Could not clear your profile roles: {type(exc).__name__}.", ok=False)
        else:
            await _reply(interaction, "You do not have any optional profile roles from this panel.", ok=True)
        return True

    if suffix == "missing":
        await interaction.response.send_modal(MissingIdentityModal())
        return True

    if suffix == "missing_interest":
        await interaction.response.send_modal(MissingInterestModal())
        return True

    return True


async def _handle_self_role(interaction: discord.Interaction) -> bool:
    try:
        if interaction.type is not discord.InteractionType.component:
            return False
        data = interaction.data if isinstance(interaction.data, dict) else {}
        custom_id = str(data.get("custom_id") or "")
        if not custom_id.startswith(SELF_ROLE_PREFIX):
            return False
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await _reply(interaction, "This only works inside the server.", ok=False)
            return True

        role_id = int(custom_id[len(SELF_ROLE_PREFIX):].split(":", 1)[0])
        role = interaction.guild.get_role(role_id)
        if not isinstance(role, discord.Role):
            await _reply(interaction, "That role no longer exists.", ok=False)
            return True

        ok, why = _can_manage(role, interaction.guild)
        if not ok:
            await _reply(interaction, why, ok=False)
            return True

        await _ack_profile_action(interaction)

        if role in interaction.user.roles:
            await interaction.user.remove_roles(role, reason="Dank Shield advanced self-role toggle")
            await _reply(interaction, f"Removed {role.mention}.", ok=True)
        else:
            await interaction.user.add_roles(role, reason="Dank Shield advanced self-role toggle")
            await _reply(interaction, f"Added {role.mention}.", ok=True)
        return True
    except Exception as exc:
        await _reply(interaction, f"Self-role failed: {type(exc).__name__}.", ok=False)
        return True


def _panel_guard_custom_id(interaction: discord.Interaction) -> str:
    try:
        data = interaction.data if isinstance(interaction.data, dict) else {}
        return str(data.get("custom_id") or "")
    except Exception:
        return ""


def _panel_guard_is_profile_action(custom_id: str) -> bool:
    text = str(custom_id or "")
    return (
        text.startswith("dank:profile:v1:")
        or text.startswith("dank:rolepicker:v2:")
        or text.startswith("dank:selfrole:v1:")
    )


async def _panel_guard_quiet_reject(interaction: discord.Interaction, key: tuple[int, int]) -> None:
    now = monotonic()
    last_notice = float(_PROFILE_PANEL_LAST_NOTICE.get(key, 0.0) or 0.0)
    _PROFILE_PANEL_LAST_NOTICE[key] = now

    # Show one clear warning, then quietly acknowledge repeat spam so Discord does not stack errors.
    if now - last_notice >= _PROFILE_PANEL_NOTICE_SECONDS:
        await _reply(interaction, "Already updating your profile. Wait a couple seconds.", ok=False)
        return

    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=False)
    except Exception:
        pass


def _profile_action_key_from_custom_id(custom_id: str) -> str:
    text = str(custom_id or "")
    if text.startswith("dank:profile:v1:"):
        suffix = text[len("dank:profile:v1:"):]
        if suffix.startswith("open:"):
            return suffix
        if suffix in {"view", "clear", "missing", "missing_interest"}:
            return suffix
    if text.startswith("dank:rolepicker:v2:"):
        suffix = text[len("dank:rolepicker:v2:"):]
        if suffix.startswith("open:"):
            return suffix
        if suffix in {"clear", "custom"}:
            return suffix
    return text[:80]


async def _profile_session_gate(interaction: discord.Interaction, custom_id: str) -> bool:
    """Allow profile actions to reopen after a user dismisses an ephemeral message.

    Discord does not tell the bot when a user dismisses an ephemeral response.
    The old 45-second session suppression caused a button click to silently defer
    instead of reopening the panel. The hard interaction lock/cooldown still
    protects against double-click spam, so this gate should not suppress normal
    repeated clicks.
    """

    _ = interaction
    _ = custom_id
    return True



async def _panel_guard_acquire(interaction: discord.Interaction, custom_id: str) -> Optional[tuple[int, int]]:
    if not _panel_guard_is_profile_action(custom_id):
        return None

    guild_id = int(interaction.guild.id) if interaction.guild else 0
    user_id = int(interaction.user.id)
    key = (guild_id, user_id)
    now = monotonic()

    if len(_PROFILE_PANEL_BLOCK_UNTIL) > 5000:
        stale = [k for k, until in _PROFILE_PANEL_BLOCK_UNTIL.items() if now - float(until or 0.0) > 120]
        for stale_key in stale[:1000]:
            _PROFILE_PANEL_BLOCK_UNTIL.pop(stale_key, None)
            _PROFILE_PANEL_LAST_NOTICE.pop(stale_key, None)
            lock = _PROFILE_PANEL_HARD_LOCKS.get(stale_key)
            if lock is not None and not lock.locked():
                _PROFILE_PANEL_HARD_LOCKS.pop(stale_key, None)

    lock = _PROFILE_PANEL_HARD_LOCKS.setdefault(key, asyncio.Lock())
    blocked_until = float(_PROFILE_PANEL_BLOCK_UNTIL.get(key, 0.0) or 0.0)

    if lock.locked() or now < blocked_until:
        await _panel_guard_quiet_reject(interaction, key)
        return None

    _PROFILE_PANEL_BLOCK_UNTIL[key] = now + _PROFILE_PANEL_HARD_COOLDOWN_SECONDS
    await lock.acquire()
    return key


def _panel_guard_release(key: Optional[tuple[int, int]]) -> None:
    if key is None:
        return
    now = monotonic()
    _PROFILE_PANEL_BLOCK_UNTIL[key] = max(
        float(_PROFILE_PANEL_BLOCK_UNTIL.get(key, 0.0) or 0.0),
        now + _PROFILE_PANEL_HARD_COOLDOWN_SECONDS,
    )
    lock = _PROFILE_PANEL_HARD_LOCKS.get(key)
    if lock is not None and lock.locked():
        lock.release()


async def _interaction_listener(interaction: discord.Interaction) -> None:
    custom_id = _panel_guard_custom_id(interaction)
    gate_key: Optional[tuple[int, int]] = None

    if _panel_guard_is_profile_action(custom_id):
        try:
            print(
                "🌿 profile_interaction "
                f"guild={getattr(getattr(interaction, 'guild', None), 'id', 0)} "
                f"channel={getattr(getattr(interaction, 'channel', None), 'id', 0)} "
                f"user={getattr(getattr(interaction, 'user', None), 'id', 0)} "
                f"custom_id={custom_id} "
                f"response_done={getattr(interaction.response, 'is_done', lambda: False)()}"
            )
        except Exception:
            pass

        if not await _profile_session_gate(interaction, custom_id):
            return
        gate_key = await _panel_guard_acquire(interaction, custom_id)
        if gate_key is None:
            return

    try:
        if await _handle_profile_interaction(interaction):
            return
        await _handle_self_role(interaction)
    except Exception as exc:
        try:
            print(
                "⚠️ profile_interaction failed "
                f"guild={getattr(getattr(interaction, 'guild', None), 'id', 0)} "
                f"user={getattr(getattr(interaction, 'user', None), 'id', 0)} "
                f"custom_id={custom_id} "
                f"error={type(exc).__name__}: {exc}"
            )
        except Exception:
            pass

        try:
            await _reply(
                interaction,
                "That profile panel action failed safely. If this is an old panel, ask staff to repost the Profile Panel.",
                ok=False,
            )
        except Exception:
            pass
    finally:
        _panel_guard_release(gate_key)

class AdvancedSelfRolePanelView(discord.ui.View):
    def __init__(self, roles: list[discord.Role]) -> None:
        super().__init__(timeout=None)
        for index, role in enumerate(roles[:20]):
            self.add_item(
                discord.ui.Button(
                    label=role.name[:80],
                    style=discord.ButtonStyle.secondary,
                    custom_id=f"{SELF_ROLE_PREFIX}{int(role.id)}",
                    row=min(4, index // 4),
                )
            )


async def _post_advanced_panel(interaction: discord.Interaction, channel: discord.TextChannel, title: str, roles: list[discord.Role]) -> None:
    embed = discord.Embed(
        title=title[:256],
        description="Advanced existing-role panel. Staff picked these roles manually.",
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Roles", value="\n".join(f"• {role.mention}" for role in roles)[:1024], inline=False)
    embed.add_field(
        name="Safety note",
        value="Use this only for cosmetic roles. Do not use these buttons for verification, staff access, tickets, moderation, or permissions.",
        inline=False,
    )
    embed.set_footer(text="Dank Shield advanced role panel")
    try:
        await channel.send(embed=embed, view=AdvancedSelfRolePanelView(roles), allowed_mentions=discord.AllowedMentions.none())
        await interaction.followup.send(f"✅ Advanced role panel posted in {channel.mention}.", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())
    except Exception as exc:
        await interaction.followup.send(f"❌ Could not post advanced role panel: `{type(exc).__name__}: {str(exc)[:250]}`", ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


@profile_group.command(name="builder", description="Open the complete profile panel builder in this channel.")
@app_commands.describe(title="Optional panel title.")
async def profile_builder(
    interaction: discord.Interaction,
    title: str = "Profile Panel",
) -> None:
    if not await _require_setup_permission(interaction):
        return
    await _post_profile_builder(interaction, title=title)


@profile_group.command(name="view", description="View your profile card or another member's profile.")
@app_commands.describe(member="Member to view. Leave blank to view yourself.")
async def profile_view(
    interaction: discord.Interaction,
    member: Optional[discord.Member] = None,
) -> None:
    if interaction.guild is None:
        return await _reply(interaction, "This command must be used inside a server.", ok=False)

    target = member
    if target is None:
        target = interaction.user if isinstance(interaction.user, discord.Member) else None

    if not isinstance(target, discord.Member):
        return await _reply(interaction, "Could not resolve that member.", ok=False)

    await interaction.response.send_message(
        embed=_profile_card(target),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@roles_group.command(name="panel", description="Advanced: post a custom panel using roles that already exist.")
@app_commands.describe(
    channel="Where to post the advanced role panel.",
    title="Panel title.",
    role1="First existing role.",
    role2="Optional existing role.",
    role3="Optional existing role.",
    role4="Optional existing role.",
    role5="Optional existing role.",
)
async def roles_panel(
    interaction: discord.Interaction,
    channel: discord.TextChannel,
    title: str,
    role1: discord.Role,
    role2: Optional[discord.Role] = None,
    role3: Optional[discord.Role] = None,
    role4: Optional[discord.Role] = None,
    role5: Optional[discord.Role] = None,
) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _reply(interaction, "This command must be used inside a server.", ok=False)

    roles: list[discord.Role] = []
    seen: set[int] = set()
    for role in (role1, role2, role3, role4, role5):
        if not isinstance(role, discord.Role) or int(role.id) in seen:
            continue
        seen.add(int(role.id))
        ok, why = _can_manage(role, interaction.guild)
        if not ok:
            return await _reply(interaction, why, ok=False)
        roles.append(role)

    if not roles:
        return await _reply(interaction, "Pick at least one usable existing role.", ok=False)

    try:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
    except Exception:
        pass

    await _post_advanced_panel(interaction, channel, title, roles)


@roles_group.command(name="health", description="Advanced: check whether Dank Shield can manage one existing role.")
@app_commands.describe(role="Role to test.")
async def roles_health(interaction: discord.Interaction, role: discord.Role) -> None:
    if not await _require_setup_permission(interaction):
        return
    if interaction.guild is None:
        return await _reply(interaction, "This command must be used inside a server.", ok=False)
    ok, why = _can_manage(role, interaction.guild)
    await _reply(interaction, f"{role.mention} is ready for advanced role panels." if ok else why, ok=ok)


async def _view_dank_profile_context(interaction: discord.Interaction, member: discord.Member) -> None:
    guild = interaction.guild
    if guild is None:
        return await _reply(interaction, "This only works inside the server.", ok=False)

    target = member
    if not isinstance(target, discord.Member):
        resolved = guild.get_member(int(getattr(member, "id", 0) or 0))
        if not isinstance(resolved, discord.Member):
            return await _reply(interaction, "Could not resolve that member in this server.", ok=False)
        target = resolved

    await interaction.response.send_message(
        embed=_profile_card(target),
            view=_profile_card_view(target),
            ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


view_dank_profile_context_menu = app_commands.ContextMenu(
    name="View Dank Profile",
    callback=_view_dank_profile_context,
)


def _attach_groups() -> bool:
    global _ATTACHED
    if _ATTACHED:
        return True

    ok = True
    try:
        if dank_group.get_command("profile") is None:
            dank_group.add_command(profile_group)
    except Exception as exc:
        ok = False
        try:
            print(f"⚠️ public_self_roles_group failed attaching /dank profile: {type(exc).__name__}: {exc}")
        except Exception:
            pass

    try:
        if dank_group.get_command("roles") is None:
            dank_group.add_command(roles_group)
    except Exception as exc:
        ok = False
        try:
            print(f"⚠️ public_self_roles_group failed attaching /dank roles: {type(exc).__name__}: {exc}")
        except Exception:
            pass

    _ATTACHED = ok
    return ok


def register_public_self_roles_group_commands(bot: Any, tree: Any) -> None:
    global _LISTENER_ATTACHED, _CONTEXT_MENU_ATTACHED

    if bot is not None:
        register_profile_panel_runtime(bot)

    if tree is not None and not _CONTEXT_MENU_ATTACHED:
        try:
            existing = None
            try:
                existing = tree.get_command("View Dank Profile", type=discord.AppCommandType.user)
            except TypeError:
                existing = tree.get_command("View Dank Profile")
            if existing is None:
                tree.add_command(view_dank_profile_context_menu)
            _CONTEXT_MENU_ATTACHED = True
            print("✅ public_self_roles_group: attached View Dank Profile context menu")
        except Exception as exc:
            try:
                print(f"⚠️ public_self_roles_group context menu failed: {type(exc).__name__}: {exc}")
            except Exception:
                pass

    if bot is not None and not _LISTENER_ATTACHED:
        try:
            bot.add_listener(_interaction_listener, "on_interaction")
            _LISTENER_ATTACHED = True
        except Exception as exc:
            try:
                print(f"⚠️ public_self_roles_group listener failed: {type(exc).__name__}: {exc}")
            except Exception:
                pass

    if _attach_groups():
        try:
            print("✅ public_self_roles_group: attached /dank profile and advanced /dank roles commands")
        except Exception:
            pass


_attach_groups()

__all__ = ["register_public_self_roles_group_commands", "register_profile_panel_runtime", "profile_group", "roles_group", "view_dank_profile_context_menu"]
