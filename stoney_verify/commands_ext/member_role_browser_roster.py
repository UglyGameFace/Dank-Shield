from __future__ import annotations

from datetime import datetime, timedelta, timezone
from math import ceil
from typing import Any

import discord

from .member_role_browser_actions import MemberActionView, member_detail_embed
from .member_role_browser_common import (
    OwnedView,
    display_name,
    ensure_member_cache,
    reply_ephemeral,
    timestamp,
    trim,
)

_BROWSER_PAGE_SIZE = 20


def _role_member_sort_key(member: discord.Member, mode: str) -> tuple[Any, ...]:
    if mode == "joined_newest":
        joined = member.joined_at or datetime.min.replace(tzinfo=timezone.utc)
        return (-joined.timestamp(), member.id)
    if mode == "joined_oldest":
        joined = member.joined_at or datetime.max.replace(tzinfo=timezone.utc)
        return (joined.timestamp(), member.id)
    if mode == "account_newest":
        return (-member.created_at.timestamp(), member.id)
    if mode == "account_oldest":
        return (member.created_at.timestamp(), member.id)
    return (str(member.display_name or member.name).casefold(), member.id)


def _member_matches(member: discord.Member, query: str) -> bool:
    wanted = str(query or "").strip().casefold()
    if not wanted:
        return True
    values = (
        str(member.id),
        str(getattr(member, "name", "") or ""),
        str(getattr(member, "display_name", "") or ""),
        str(getattr(member, "global_name", "") or ""),
    )
    return any(wanted in value.casefold() for value in values)


def _member_option_description(member: discord.Member) -> str:
    joined = member.joined_at
    joined_text = "join date unknown"
    if isinstance(joined, datetime):
        now = discord.utils.utcnow()
        if joined.tzinfo is None:
            joined = joined.replace(tzinfo=timezone.utc)
        delta = max(timedelta(), now - joined)
        if delta.days:
            joined_text = f"joined {delta.days}d ago"
        elif delta.seconds >= 3600:
            joined_text = f"joined {delta.seconds // 3600}h ago"
        else:
            joined_text = f"joined {max(1, delta.seconds // 60)}m ago"
    status = "bot" if member.bot else "member"
    if member.is_timed_out():
        status = "timed out"
    return trim(f"{joined_text} • {status} • ID {member.id}", 100)


def role_browser_embed(view: "RoleMemberBrowserView") -> discord.Embed:
    role = view.role
    members = view.filtered_members()
    pages = max(1, ceil(len(members) / _BROWSER_PAGE_SIZE))
    view.page = max(0, min(view.page, pages - 1))
    start = view.page * _BROWSER_PAGE_SIZE
    end = min(start + _BROWSER_PAGE_SIZE, len(members))

    embed = discord.Embed(
        title=f"👥 Members with {role.name}" if role else "👥 Member Browser",
        description=(
            "Select a member below to open their moderation panel. "
            "This private browser is locked to the staff member who opened it."
        ),
        color=role.color if role and role.color.value else discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    if role is not None:
        embed.add_field(
            name="Role",
            value=f"{role.mention}\n`{role.id}`",
            inline=True,
        )
    embed.add_field(name="Matching members", value=str(len(members)), inline=True)
    embed.add_field(name="Page", value=f"{view.page + 1}/{pages}", inline=True)
    embed.add_field(
        name="Sort & filter",
        value=f"**Sort:** {view.sort_label}\n**Search:** {discord.utils.escape_markdown(view.query) if view.query else 'None'}",
        inline=False,
    )

    if not members:
        embed.add_field(
            name="Roster",
            value="No cached server members match this role and search.",
            inline=False,
        )
    else:
        lines = []
        for index, member in enumerate(members[start:end], start=start + 1):
            flags = []
            if member.bot:
                flags.append("bot")
            if member.is_timed_out():
                flags.append("timed out")
            flag_text = f" • {', '.join(flags)}" if flags else ""
            lines.append(
                f"`{index}.` **{display_name(member)}** • joined {timestamp(member.joined_at)}{flag_text}"
            )
        embed.add_field(
            name=f"Showing {start + 1}-{end}",
            value=trim("\n".join(lines), 1024),
            inline=False,
        )

    embed.set_footer(
        text="Use Refresh after role changes. Bulk tools intentionally exclude mass kick/ban."
    )
    return embed


class BrowserRoleSelect(discord.ui.RoleSelect):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Choose the server role to browse…",
            min_values=1,
            max_values=1,
            custom_id="dank_members_browser:role",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0] if self.values else None
        if not isinstance(role, discord.Role):
            await reply_ephemeral(interaction, "❌ Discord did not return a valid role.")
            return
        if role.is_default():
            await reply_ephemeral(interaction, "❌ Choose a specific role instead of @everyone.")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        warning = await ensure_member_cache(interaction.guild)
        view = RoleMemberBrowserView(
            owner_id=interaction.user.id,
            guild=interaction.guild,
            role=role,
        )
        await interaction.edit_original_response(embed=role_browser_embed(view), view=view)
        if warning:
            await interaction.followup.send(f"⚠️ {warning}", ephemeral=True)


class MemberBrowserHomeView(OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)
        self.add_item(BrowserRoleSelect())


class MemberRosterSelect(discord.ui.Select):
    def __init__(self, browser: "RoleMemberBrowserView") -> None:
        members = browser.page_members()
        options = [
            discord.SelectOption(
                label=trim(str(member.display_name or member.name), 100),
                value=str(member.id),
                description=_member_option_description(member),
                emoji="🤖" if member.bot else ("⏱️" if member.is_timed_out() else "👤"),
            )
            for member in members
        ]
        if not options:
            options = [discord.SelectOption(label="No members on this page", value="none", description="Change the role, page, or search filter.")]
        super().__init__(
            placeholder="Select a member for moderation actions…",
            min_values=1,
            max_values=1,
            options=options,
            disabled=not members,
            custom_id="dank_members_browser:member",
            row=0,
        )
        self.browser = browser

    async def callback(self, interaction: discord.Interaction) -> None:
        if not self.values or self.values[0] == "none":
            return
        member = interaction.guild.get_member(int(self.values[0]))
        if not isinstance(member, discord.Member):
            await reply_ephemeral(interaction, "❌ That member is no longer in the server. Refresh the roster.")
            return
        detail = MemberActionView(
            owner_id=self.browser.owner_id,
            member=member,
            browser=self.browser,
        )
        await interaction.response.edit_message(embed=member_detail_embed(member, self.browser.role), view=detail)


class BrowserSortSelect(discord.ui.Select):
    SORTS = {
        "name": "Name A–Z",
        "joined_newest": "Newest server joins",
        "joined_oldest": "Oldest server joins",
        "account_newest": "Newest Discord accounts",
        "account_oldest": "Oldest Discord accounts",
    }

    def __init__(self, browser: "RoleMemberBrowserView") -> None:
        options = [
            discord.SelectOption(label=label, value=value, default=value == browser.sort_mode)
            for value, label in self.SORTS.items()
        ]
        super().__init__(
            placeholder="Sort members…",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="dank_members_browser:sort",
            row=1,
        )
        self.browser = browser

    async def callback(self, interaction: discord.Interaction) -> None:
        self.browser.sort_mode = self.values[0]
        self.browser.page = 0
        self.browser.rebuild()
        await interaction.response.edit_message(embed=role_browser_embed(self.browser), view=self.browser)


class RoleMemberBrowserView(OwnedView):
    def __init__(
        self,
        *,
        owner_id: int,
        guild: discord.Guild,
        role: discord.Role,
        page: int = 0,
        sort_mode: str = "name",
        query: str = "",
    ) -> None:
        self.guild = guild
        self.role = role
        self.page = max(0, int(page))
        self.sort_mode = sort_mode if sort_mode in BrowserSortSelect.SORTS else "name"
        self.query = str(query or "").strip()
        super().__init__(owner_id)
        self.rebuild()

    @property
    def sort_label(self) -> str:
        return BrowserSortSelect.SORTS.get(self.sort_mode, "Name A–Z")

    def render_embed(self) -> discord.Embed:
        return role_browser_embed(self)

    def filtered_members(self) -> list[discord.Member]:
        members = [member for member in list(self.role.members or []) if _member_matches(member, self.query)]
        return sorted(members, key=lambda member: _role_member_sort_key(member, self.sort_mode))

    def page_members(self) -> list[discord.Member]:
        members = self.filtered_members()
        pages = max(1, ceil(len(members) / _BROWSER_PAGE_SIZE))
        self.page = max(0, min(self.page, pages - 1))
        start = self.page * _BROWSER_PAGE_SIZE
        return members[start : start + _BROWSER_PAGE_SIZE]

    def rebuild(self) -> None:
        self.clear_items()
        self.add_item(MemberRosterSelect(self))
        self.add_item(BrowserSortSelect(self))

        previous = discord.ui.Button(label="Previous", emoji="◀️", style=discord.ButtonStyle.secondary, row=2, disabled=self.page <= 0)
        previous.callback = self._previous  # type: ignore[assignment]
        self.add_item(previous)

        members = self.filtered_members()
        pages = max(1, ceil(len(members) / _BROWSER_PAGE_SIZE))
        next_button = discord.ui.Button(label="Next", emoji="▶️", style=discord.ButtonStyle.secondary, row=2, disabled=self.page >= pages - 1)
        next_button.callback = self._next  # type: ignore[assignment]
        self.add_item(next_button)

        refresh = discord.ui.Button(label="Refresh", emoji="🔄", style=discord.ButtonStyle.secondary, row=2)
        refresh.callback = self._refresh  # type: ignore[assignment]
        self.add_item(refresh)

        search = discord.ui.Button(label="Search", emoji="🔎", style=discord.ButtonStyle.primary, row=3)
        search.callback = self._search  # type: ignore[assignment]
        self.add_item(search)

        bulk = discord.ui.Button(label="Bulk Actions", emoji="🧰", style=discord.ButtonStyle.secondary, row=3, disabled=not self.page_members())
        bulk.callback = self._bulk  # type: ignore[assignment]
        self.add_item(bulk)

        change = discord.ui.Button(label="Change Role", emoji="🎭", style=discord.ButtonStyle.secondary, row=3)
        change.callback = self._change_role  # type: ignore[assignment]
        self.add_item(change)

        clear = discord.ui.Button(label="Clear Search", emoji="✖️", style=discord.ButtonStyle.secondary, row=4, disabled=not self.query)
        clear.callback = self._clear_search  # type: ignore[assignment]
        self.add_item(clear)

    async def _previous(self, interaction: discord.Interaction) -> None:
        self.page = max(0, self.page - 1)
        self.rebuild()
        await interaction.response.edit_message(embed=role_browser_embed(self), view=self)

    async def _next(self, interaction: discord.Interaction) -> None:
        self.page += 1
        self.rebuild()
        await interaction.response.edit_message(embed=role_browser_embed(self), view=self)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        warning = await ensure_member_cache(self.guild)
        self.page = 0
        self.rebuild()
        await interaction.edit_original_response(embed=role_browser_embed(self), view=self)
        if warning:
            await interaction.followup.send(f"⚠️ {warning}", ephemeral=True)

    async def _search(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(MemberSearchModal(self))

    async def _bulk(self, interaction: discord.Interaction) -> None:
        from .member_role_browser_bulk import BulkSelectView

        view = BulkSelectView(self)
        embed = discord.Embed(
            title="🧰 Select Members",
            description=(
                "Choose one or more members from the current page. "
                "Only safe bulk reminders and role changes are offered."
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=view)

    async def _change_role(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="🎭 Change Browser Role",
                description="Choose another server role to browse.",
                color=discord.Color.blurple(),
            ),
            view=MemberBrowserHomeView(self.owner_id),
        )

    async def _clear_search(self, interaction: discord.Interaction) -> None:
        self.query = ""
        self.page = 0
        self.rebuild()
        await interaction.response.edit_message(embed=role_browser_embed(self), view=self)


class MemberSearchModal(discord.ui.Modal, title="Search role members"):
    search = discord.ui.TextInput(
        label="Username, display name, or user ID",
        placeholder="Example: UglyGameFace or 629459300854661120",
        max_length=100,
        required=True,
    )

    def __init__(self, browser: RoleMemberBrowserView) -> None:
        super().__init__(timeout=300)
        self.browser = browser

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.browser.query = str(self.search.value or "").strip()
        self.browser.page = 0
        self.browser.rebuild()
        await interaction.response.edit_message(embed=role_browser_embed(self.browser), view=self.browser)


__all__ = [
    "MemberBrowserHomeView",
    "RoleMemberBrowserView",
    "role_browser_embed",
]
