from __future__ import annotations

import asyncio
from typing import Any, Optional

import discord

from .member_role_browser_actions import MemberActionView, MemberRoleActionView, member_detail_embed
from .member_role_browser_common import (
    OwnedView,
    ensure_member_cache,
    reply_ephemeral,
    require_review,
)
from .member_role_browser_review import open_review_panel
from .member_role_browser_roster import RoleMemberBrowserView, role_browser_embed


async def _replace_panel(
    interaction: discord.Interaction,
    *,
    embed: discord.Embed,
    view: discord.ui.View,
) -> None:
    kwargs = {
        "embed": embed,
        "view": view,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if interaction.response.is_done():
        await interaction.edit_original_response(**kwargs)
    elif interaction.message is not None:
        await interaction.response.edit_message(**kwargs)
    else:
        await interaction.response.send_message(
            **kwargs,
            ephemeral=True,
        )


async def _invoke_command(
    command: Any,
    interaction: discord.Interaction,
    *args: Any,
    **kwargs: Any,
) -> Any:
    callback = getattr(command, "callback", command)
    if not callable(callback):
        raise RuntimeError("Member Center target is not callable")
    return await callback(interaction, *args, **kwargs)


def _center_embed() -> discord.Embed:
    return discord.Embed(
        title="👥 Member Command Center",
        description=(
            "**Choose what you're working on:** members, activity, "
            "intelligence, or staff safety. Each opens its own tools.\n\n"
            "Moderation actions still require permission checks and confirmation."
        ),
        color=discord.Color.blurple(),
    )


def _live_members_embed(quick_roles: list[discord.Role]) -> discord.Embed:
    quick_text = (
        "\n".join(f"• {role.mention}" for role in quick_roles)
        if quick_roles
        else "No configured member roles were resolved. Use the role picker."
    )
    embed = discord.Embed(
        title="👤 Live Members",
        description=(
            "Browse members by role or select one member directly. Role rosters "
            "include pagination, search, sorting, filters, individual actions, "
            "and safe bulk reminders/role changes."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Configured quick roles",
        value=quick_text[:1024],
        inline=False,
    )
    embed.add_field(
        name="Guardrails",
        value=(
            "Every moderation or role action re-checks permissions, hierarchy, "
            "protected roles, and current member state."
        ),
        inline=False,
    )
    return embed


def _activity_embed() -> discord.Embed:
    embed = discord.Embed(
        title="📊 Activity & Cleanup",
        description=(
            "Review activity across current member types using server evidence, not presence. "
            "Cleanup remains preview-first and destructive actions keep confirmation gates."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Review presets",
        value="Use 30, 90, or 180 days, or enter custom thresholds.",
        inline=False,
    )
    embed.add_field(
        name="Cleanup tools",
        value=(
            "Select one member for validated cleanup, build a safe queue, or preview "
            "all strictly eligible members. Nothing is removed from this menu alone."
        ),
        inline=False,
    )
    return embed


def _intelligence_embed() -> discord.Embed:
    return discord.Embed(
        title="🧠 Member Intelligence",
        description=(
            "Select a member, review recent bans, or inspect a historical account. "
            "Connection evidence, join history and three simple server controls share this screen. "
            "Links do not prove leaked information, friendship, or ban evasion."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )


def _operations_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🔒 Operations & Safety",
        description=(
            "Review members intentionally skipped from activity scans, inspect notice "
            "delivery, verify evidence coverage, and manage cleanup defaults."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Important",
        value=(
            "Scan locks only control inactivity-review eligibility. They do not grant "
            "roles, immunity from normal moderation, or automatic verification."
        ),
        inline=False,
    )
    return embed


class MemberCommandCenterView(OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)

    @discord.ui.button(
        label="Live Members",
        emoji="👤",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def live_members(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        # Role configuration may require a DB read; acknowledge on mobile
        # before that wait so Discord doesn't mark the button failed.
        await interaction.response.defer(ephemeral=True)
        from .public_member_role_browser import _load_quick_roles

        quick_roles = await _load_quick_roles(interaction.guild)
        view = LiveMembersMenuView(self.owner_id, quick_roles=quick_roles)
        await _replace_panel(
            interaction,
            embed=view.render_embed(),
            view=view,
        )

    @discord.ui.button(
        label="Activity & Cleanup",
        emoji="📊",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def activity_cleanup(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        view = ActivityCleanupMenuView(self.owner_id)
        await _replace_panel(interaction, embed=_activity_embed(), view=view)

    @discord.ui.button(
        label="Intelligence",
        emoji="🧠",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def intelligence(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        view = IntelligenceMenuView(self.owner_id)
        await _replace_panel(interaction, embed=_intelligence_embed(), view=view)

    @discord.ui.button(
        label="Operations & Safety",
        emoji="🔒",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def operations(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        view = OperationsSafetyMenuView(self.owner_id)
        await _replace_panel(interaction, embed=_operations_embed(), view=view)


class CenterRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent: "LiveMembersMenuView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Choose a server role to browse…",
            min_values=1,
            max_values=1,
            custom_id="dank_members_center:role",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0] if self.values else None
        if not isinstance(role, discord.Role) or role.is_default():
            await reply_ephemeral(
                interaction,
                "❌ Choose a specific server role instead of @everyone.",
            )
            return
        await interaction.response.defer()
        warning = await ensure_member_cache(interaction.guild)
        view = CenterRoleBrowserView(
            owner_id=self.parent_view.owner_id,
            guild=interaction.guild,
            role=role,
            quick_roles=self.parent_view.quick_roles,
        )
        await interaction.edit_original_response(
            embed=role_browser_embed(view),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        if warning:
            await interaction.followup.send(f"⚠️ {warning}", ephemeral=True)


class CenterQuickRoleButton(discord.ui.Button):
    def __init__(
        self,
        parent: "LiveMembersMenuView",
        role: discord.Role,
    ) -> None:
        self.parent_view = parent
        self.role_id = int(role.id)
        super().__init__(
            label=str(role.name)[:80],
            emoji="👥",
            style=discord.ButtonStyle.secondary,
            custom_id=f"dank_members_center:quick:{role.id}",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        role = interaction.guild.get_role(self.role_id)
        if not isinstance(role, discord.Role):
            await reply_ephemeral(
                interaction,
                "❌ That configured role no longer exists. Run setup repair.",
            )
            return
        await interaction.response.defer()
        warning = await ensure_member_cache(interaction.guild)
        view = CenterRoleBrowserView(
            owner_id=self.parent_view.owner_id,
            guild=interaction.guild,
            role=role,
            quick_roles=self.parent_view.quick_roles,
        )
        await interaction.edit_original_response(
            embed=role_browser_embed(view),
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        if warning:
            await interaction.followup.send(f"⚠️ {warning}", ephemeral=True)


class DirectMemberSelect(discord.ui.UserSelect):
    def __init__(self, parent: "LiveMembersMenuView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Or select one member directly…",
            min_values=1,
            max_values=1,
            custom_id="dank_members_center:direct_member",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0] if self.values else None
        member = interaction.guild.get_member(int(getattr(selected, "id", 0) or 0))
        if not isinstance(member, discord.Member):
            await reply_ephemeral(
                interaction,
                "❌ That user is not currently a server member.",
            )
            return
        view = MemberActionView(
            owner_id=self.parent_view.owner_id,
            member=member,
            browser=self.parent_view,
        )
        await interaction.response.edit_message(
            embed=member_detail_embed(member, None),
            view=view,
        )


class LiveMembersMenuView(OwnedView):
    role: Optional[discord.Role] = None

    def __init__(
        self,
        owner_id: int,
        *,
        quick_roles: Optional[list[discord.Role]] = None,
    ) -> None:
        self.quick_roles = list(quick_roles or [])[:4]
        super().__init__(owner_id)
        self.add_item(CenterRoleSelect(self))
        self.add_item(DirectMemberSelect(self))
        for role in self.quick_roles:
            self.add_item(CenterQuickRoleButton(self, role))

    def rebuild(self) -> None:
        return

    def render_embed(self) -> discord.Embed:
        return _live_members_embed(self.quick_roles)

    @discord.ui.button(
        label="Command Center",
        emoji="🏠",
        style=discord.ButtonStyle.secondary,
        row=3,
    )
    async def command_center(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        view = MemberCommandCenterView(self.owner_id)
        await _replace_panel(interaction, embed=_center_embed(), view=view)


class CenterRoleBrowserView(RoleMemberBrowserView):
    def rebuild(self) -> None:
        super().rebuild()
        center = discord.ui.Button(
            label="Command Center",
            emoji="🏠",
            style=discord.ButtonStyle.secondary,
            row=4,
        )
        center.callback = self._center  # type: ignore[assignment]
        self.add_item(center)

    async def _change_role(self, interaction: discord.Interaction) -> None:
        view = LiveMembersMenuView(
            self.owner_id,
            quick_roles=self.quick_roles,
        )
        await _replace_panel(interaction, embed=view.render_embed(), view=view)

    async def _center(self, interaction: discord.Interaction) -> None:
        view = MemberCommandCenterView(self.owner_id)
        await _replace_panel(interaction, embed=_center_embed(), view=view)


class CleanupMemberSelect(discord.ui.UserSelect):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Validate cleanup for one current member…",
            min_values=1,
            max_values=1,
            custom_id="dank_members_center:cleanup_member",
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0] if self.values else None
        member = interaction.guild.get_member(int(getattr(selected, "id", 0) or 0))
        if not isinstance(member, discord.Member):
            await reply_ephemeral(
                interaction,
                "❌ That user is not currently a server member.",
            )
            return
        from .public_members_cleanup_group import members_cleanup_user

        await _invoke_command(members_cleanup_user, interaction, member)


class ActivityCleanupMenuView(OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)
        self.add_item(CleanupMemberSelect())

    async def _scan(self, interaction: discord.Interaction, days: int) -> None:
        from .public_members_group import _run_activity_scan

        await _run_activity_scan(interaction, inactive_days=days)

    @discord.ui.button(label="30d Review", emoji="⚡", style=discord.ButtonStyle.secondary, row=0)
    async def scan_30(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._scan(interaction, 30)

    @discord.ui.button(label="90d Review", emoji="🎯", style=discord.ButtonStyle.primary, row=0)
    async def scan_90(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._scan(interaction, 90)

    @discord.ui.button(label="180d Review", emoji="🗓️", style=discord.ButtonStyle.secondary, row=0)
    async def scan_180(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._scan(interaction, 180)

    @discord.ui.button(label="Custom Review", emoji="🛠️", style=discord.ButtonStyle.secondary, row=0)
    async def custom_scan(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(CustomActivityScanModal())

    @discord.ui.button(label="Last Review", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def last_review(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_members_group import (
            MemberActivityReviewView,
            _build_report_embed,
            get_last_scan,
        )

        report = get_last_scan(interaction.guild.id)
        if report is None:
            await reply_ephemeral(
                interaction,
                "No activity review has been run since the bot restarted. Use a review button in this menu first.",
            )
            return
        await interaction.response.send_message(
            embed=_build_report_embed(report, page=0),
            view=MemberActivityReviewView(report, page=0),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Cleanup Queue", emoji="🧹", style=discord.ButtonStyle.secondary, row=1)
    async def cleanup_queue(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_members_cleanup_group import members_cleanup_queue

        await _invoke_command(members_cleanup_queue, interaction)

    @discord.ui.button(label="Purge Eligible", emoji="⚠️", style=discord.ButtonStyle.danger, row=1)
    async def purge_eligible(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_members_cleanup_group import members_purge_all

        await _invoke_command(members_purge_all, interaction)

    @discord.ui.button(label="Evidence Coverage", emoji="📡", style=discord.ButtonStyle.secondary, row=1)
    async def coverage(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_members_group import members_coverage

        await _invoke_command(members_coverage, interaction)

    @discord.ui.button(label="Command Center", emoji="🏠", style=discord.ButtonStyle.secondary, row=3)
    async def command_center(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        view = MemberCommandCenterView(self.owner_id)
        await _replace_panel(interaction, embed=_center_embed(), view=view)


class CustomActivityScanModal(discord.ui.Modal, title="Custom activity review"):
    inactive_days = discord.ui.TextInput(
        label="Inactive days (7-730)",
        placeholder="90",
        max_length=3,
    )
    grace_days = discord.ui.TextInput(
        label="New-member grace days (1-90)",
        placeholder="14",
        max_length=2,
    )
    low_confidence = discord.ui.TextInput(
        label="Include low confidence? yes/no",
        placeholder="yes",
        max_length=3,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            inactive_days = int(str(self.inactive_days.value).strip())
            grace_days = int(str(self.grace_days.value).strip())
        except Exception:
            await reply_ephemeral(interaction, "❌ Days must be whole numbers.")
            return
        low_text = str(self.low_confidence.value or "").strip().casefold()
        if low_text not in {"yes", "y", "true", "no", "n", "false"}:
            await reply_ephemeral(
                interaction,
                "❌ Include low confidence must be yes or no.",
            )
            return
        from .public_members_group import _run_activity_scan

        await _run_activity_scan(
            interaction,
            inactive_days=inactive_days,
            grace_days=grace_days,
            include_low_confidence=low_text in {"yes", "y", "true"},
        )


async def _resolve_intelligence_member(
    guild: Optional[discord.Guild], user_id: int,
) -> tuple[Optional[discord.Member], str]:
    """Live Discord REST result is authoritative; cache absence is not a departure."""
    if guild is None or user_id <= 0:
        return None, "unavailable"
    try:
        fetched = await guild.fetch_member(user_id)
    except discord.NotFound:
        return None, "absent"
    except (discord.Forbidden, discord.HTTPException):
        return None, "unavailable"
    except Exception:
        return None, "unavailable"
    return (fetched, "verified") if isinstance(fetched, discord.Member) else (None, "unavailable")


def _intelligence_presence_text(status: str) -> str:
    return {
        "verified": "✅ Confirmed in this server by a live Discord member lookup.",
        "absent": "ℹ️ Discord did not find this user in the server at lookup time.",
        "unavailable": "⚠️ Live membership could not be verified. This does NOT mean the user left.",
    }.get(status, "⚠️ Current membership not checked.")


class IntelligenceMemberSelect(discord.ui.UserSelect):
    def __init__(self, parent: "IntelligenceMenuView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Select a current member to inspect…",
            min_values=1,
            max_values=1,
            custom_id="dank_members_center:intelligence_member",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0] if self.values else None
        user_id = int(getattr(selected, "id", 0) or 0)
        if user_id <= 0:
            await reply_ephemeral(interaction, "❌ Select a Discord user first.")
            return
        # REST verification can exceed Discord's 3-second response window.
        await interaction.response.defer(ephemeral=True)
        if not await require_review(interaction):
            return
        member, status = await _resolve_intelligence_member(interaction.guild, user_id)
        if member is None:
            # Historical evidence is useful even when Discord returns 404 or fails.
            await _show_stored_member_history(interaction, user_id, presence=status)
            return
        view = IntelligenceTargetView(
            owner_id=self.parent_view.owner_id,
            member=member,
        )
        embed = member_detail_embed(member, None)
        embed.add_field(name="Live membership", value=_intelligence_presence_text(status), inline=False)
        await _replace_panel(interaction, embed=embed, view=view)
        # Show the retained join/source report on selection, not behind another menu.
        await _show_stored_member_history(interaction, user_id, presence=status)


class IntelligenceMenuView(OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)
        self.add_item(IntelligenceMemberSelect(self))

    @discord.ui.button(label="Stored History by ID", emoji="🧾", style=discord.ButtonStyle.secondary, row=1)
    async def history_by_id(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(MemberHistoryByIdModal())

    @discord.ui.button(label="Recent Ban Connections", emoji="🔗", style=discord.ButtonStyle.primary, row=1)
    async def recent_bans(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await require_review(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            bans = [entry async for entry in interaction.guild.bans(limit=None)]
        except (discord.Forbidden, discord.HTTPException):
            await reply_ephemeral(interaction, "⚠️ Cannot list bans. Check Dank Shield's Ban Members permission.")
            return
        if not bans:
            await reply_ephemeral(interaction, "No ban records are available for this server.")
            return
        view = RecentBanConnectionView(self.owner_id, bans)
        embed = discord.Embed(
            title="🔗 Recent Ban Connections",
            description=(f"Showing all **{len(bans)}** Discord ban records, 25 per page. "
                         "Choose a banned user to review retained server evidence."),
            color=discord.Color.blurple(),
        )
        await _replace_panel(interaction, embed=embed, view=view)

    @discord.ui.button(label="Connections by ID", emoji="🔍", style=discord.ButtonStyle.secondary, row=1)
    async def connections_by_id(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(ConnectionByIdModal())

    @discord.ui.button(label="Connection Controls", emoji="⚙️", style=discord.ButtonStyle.secondary, row=2)
    async def connection_controls(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .member_connection_controls_ui import open_connection_controls
        await open_connection_controls(interaction, self.owner_id)

    @discord.ui.button(label="Command Center", emoji="🏠", style=discord.ButtonStyle.secondary, row=2)
    async def command_center(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        view = MemberCommandCenterView(self.owner_id)
        await _replace_panel(interaction, embed=_center_embed(), view=view)


class IntelligenceTargetView(OwnedView):
    def __init__(self, *, owner_id: int, member: discord.Member) -> None:
        self.member_id = int(member.id)
        super().__init__(owner_id)

    async def _verified_member(self, interaction: discord.Interaction) -> Optional[discord.Member]:
        member, status = await _resolve_intelligence_member(interaction.guild, self.member_id)
        if status != "verified":
            await reply_ephemeral(interaction, _intelligence_presence_text(status))
            return None
        return member

    @discord.ui.button(label="Intelligence Review", emoji="🧠", style=discord.ButtonStyle.primary, row=0)
    async def review(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.defer(ephemeral=True)
        member = await self._verified_member(interaction)
        if member is None:
            return
        await open_review_panel(interaction, member)

    @discord.ui.button(label="Stored History", emoji="🧾", style=discord.ButtonStyle.secondary, row=0)
    async def history(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _show_stored_member_history(interaction, self.member_id)

    @discord.ui.button(label="Moderation Panel", emoji="🛡️", style=discord.ButtonStyle.secondary, row=0)
    async def moderation(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.defer(ephemeral=True)
        member = await self._verified_member(interaction)
        if member is None:
            return
        from .public_member_role_browser import _load_quick_roles

        quick_roles = await _load_quick_roles(interaction.guild)
        back_view = LiveMembersMenuView(self.owner_id, quick_roles=quick_roles)
        view = MemberActionView(
            owner_id=self.owner_id,
            member=member,
            browser=back_view,
        )
        await _replace_panel(
            interaction, embed=member_detail_embed(member, None), view=view,
        )

    @discord.ui.button(label="Connections", emoji="🔗", style=discord.ButtonStyle.secondary, row=1)
    async def connections(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await require_review(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        await _show_connection_report(interaction, self.owner_id, self.member_id)

    @discord.ui.button(label="Back to Intelligence", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        view = IntelligenceMenuView(self.owner_id)
        await _replace_panel(interaction, embed=_intelligence_embed(), view=view)



def _historical_member_embed(
    user_id: int, snapshot: dict[str, Any], verdicts: list[dict[str, Any]],
    *, presence: str = "unchecked",
) -> discord.Embed:
    """Display guild-scoped stored evidence, not Discord membership guesses."""
    member = snapshot.get("member") or {}
    joins = snapshot.get("join_summary") or {}
    events = snapshot.get("event_summary") or {}
    tickets = snapshot.get("ticket_summary") or {}
    names = snapshot.get("name_history") or {}
    label = (
        member.get("display_name") or member.get("username")
        or next(iter(names.get("all_names") or []), None)
        or "Former / uncached user"
    )
    label = discord.utils.escape_markdown(str(label)[:90])
    embed = discord.Embed(
        title="🧠 Stored Member Intelligence",
        description=(
            f"**{label}** `{user_id}`\\n"
            "Retained server records only. A user ID can be looked up even when "
            "Discord no longer resolves the user. This is not an automatic punishment."
        ).replace("\\n", "\n"),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    join_text = (
        f"Joins: **{joins.get('times_joined', 0)}** • "
        f"Departures: **{joins.get('times_left', 0)}**\\n"
        f"Last joined: `{str(joins.get('latest_joined_at') or 'unknown')[:50]}`\\n"
        f"Left: `{str(joins.get('left_at') or 'not recorded')[:50]}`\\n"
        f"Last seen: `{str(joins.get('last_seen_at') or 'unknown')[:50]}`"
    ).replace("\\n", "\n")
    embed.add_field(name="Live membership", value=_intelligence_presence_text(presence), inline=False)
    embed.add_field(name="Membership History", value=join_text[:1024], inline=False)
    recent = snapshot.get("recent_joins") or []
    latest = recent[0] if recent and isinstance(recent[0], dict) else {}
    relations = snapshot.get("relationships") or {}
    method = str(latest.get("entry_method") or joins.get("latest_entry_method") or "").strip()
    method_names = {
        "invite": "Server invite",
        "vanity_invite": "Vanity invitation",
        "vouched": "Member vouched for entry",
        "manual_verification": "Staff verification",
        "ticket_verification": "Ticket-based verification",
        "invite_cache_warming": "Tracking started after this join",
        "invite_tracking_unavailable": "Invite tracking was unavailable",
        "invite_unresolved": "Invite usage could not be matched",
    }
    source = method_names.get(method, "Unknown")
    code = str(latest.get("invite_code") or relations.get("invite_code") or "").strip()
    inviter = str(latest.get("invited_by_name") or relations.get("invited_by_name") or "").strip()
    quality = str(latest.get("entry_truth_quality") or relations.get("entry_truth_quality") or member.get("entry_truth_quality") or "").strip().lower()
    conflicted = bool(latest.get("entry_conflict") or relations.get("entry_conflict") or member.get("entry_conflict"))
    if conflicted:
        confidence = "Conflicting records; attribution not verified"
    elif quality in {"confirmed", "partial", "unknown"}:
        confidence = {"confirmed": "Confirmed in retained join record", "partial": "Partial / uncertain", "unknown": "Unknown"}[quality]
    else:
        confidence = "Not recorded"
    def _plain(value: str, limit: int = 80) -> str:
        return discord.utils.escape_markdown(value[:limit]).replace("@", "@\u200b")
    source_lines = [f"Source: **{_plain(source)}**", f"Confidence: **{confidence}**"]
    if code:
        source_lines.append(f"Invite code (retained): `{_plain(code, 32)}`")
    if inviter:
        source_lines.append(f"Invite creator (retained): **{_plain(inviter)}**")
    if relations.get("attribution_record") == "member_joined_event":
        source_lines.append("Record: **Original member-joined event** (matched to this join)")
        if quality == "confirmed" and not conflicted:
            confidence = "Confirmed in retained join event"
            source_lines[1] = f"Confidence: **{confidence}**"
    reason = str(
        latest.get("entry_quality_reason")
        or relations.get("entry_quality_reason")
        or member.get("entry_quality_reason")
        or ""
    ).strip()
    if reason:
        source_lines.append(f"Tracking details: {_plain(reason, 220)}")
    if not code and not inviter:
        source_lines.append("The exact invite and creator could not be established for this join.")
    embed.add_field(name="How they joined", value="\n".join(source_lines)[:1024], inline=False)
    types = events.get("type_counts") or {}
    type_text = ", ".join(
        f"{str(k)[:36]}: {v}" for k, v in list(types.items())[:8]
    ) or "No retained event types"
    embed.add_field(
        name="Activity & Moderation Evidence",
        value=(
            f"Retained events: **{events.get('total', 0)}** • "
            f"Moderation: **{events.get('moderation_count', 0)}**\\n"
            f"Last event: `{str(events.get('latest_event_type') or 'unknown')[:65]}`\\n"
            f"Types: {type_text}"
        ).replace("\\n", "\n")[:1024],
        inline=False,
    )
    embed.add_field(
        name="Tickets & Staff Decisions",
        value=(
            f"Retained tickets: **{tickets.get('total', 0)}** • "
            f"Staff verdicts: **{len(verdicts)}** (last 10 queried)\\n"
            + "\\n".join(
                f"• {str((row.get('metadata') or {}).get('verdict_label') or (row.get('metadata') or {}).get('verdict') or 'Decision')[:42]}"
                for row in verdicts[:5]
            )
        ).replace("\\n", "\n")[:1024],
        inline=False,
    )
    if not (snapshot.get("recent_joins") or snapshot.get("recent_events") or snapshot.get("recent_tickets") or verdicts
            or member.get("username") or member.get("joined_at")):
        embed.add_field(
            name="Evidence Availability",
            value="No retained records found for this ID in this server. Discord does not supply deleted history retroactively.",
            inline=False,
        )
    embed.set_footer(text="Staff-only historical lookup • activity and join data can be incomplete")
    return embed


async def _show_stored_member_history(
    interaction: discord.Interaction, user_id: int, *, presence: Optional[str] = None,
) -> None:
    """Read historical guild records without fetch_user() or a live member."""
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True, thinking=True)
    if not await require_review(interaction):
        return
    if interaction.guild is None:
        return await reply_ephemeral(interaction, "❌ Choose a server first.")
    from stoney_verify.tickets_new.member_context_service import get_member_context_snapshot
    from stoney_verify.member_review_feedback import get_member_review_history
    guild_id = int(interaction.guild.id)
    if presence is None:
        _, presence = await _resolve_intelligence_member(interaction.guild, user_id)
    try:
        context_result, feedback_result = await asyncio.wait_for(
            asyncio.gather(
                get_member_context_snapshot(
                    guild_id=guild_id, user_id=user_id,
                    ticket_limit=10, event_limit=12, join_limit=8,
                ),
                asyncio.to_thread(
                    get_member_review_history,
                    guild_id=str(guild_id), user_id=str(user_id), limit=10,
                ),
                return_exceptions=True,
            ),
            timeout=20,
        )
    except asyncio.TimeoutError:
        return await interaction.followup.send(
            "⚠️ Historical records timed out. Nothing was modified; try again shortly.",
            ephemeral=True,
        )
    snapshot = context_result if isinstance(context_result, dict) and context_result.get("ok") else {}
    verdicts = feedback_result if isinstance(feedback_result, list) else []
    if not snapshot and not verdicts:
        return await interaction.followup.send(
            "⚠️ Stored member records could not be read. This does not mean the user's history was deleted.",
            ephemeral=True,
        )
    await interaction.followup.send(
        embed=_historical_member_embed(user_id, snapshot, verdicts, presence=presence),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )



def _connection_embed(report: dict[str, Any]) -> discord.Embed:
    target = report["target_user_id"]
    embed = discord.Embed(
        title="🔗 Member Connection Intelligence",
        description=(
            f"Account: `{target}`\n"
            "Only relationships recorded inside this server are considered. "
            "**No result proves that anyone leaked information or owns another account.**"
        ).replace("\n", "\n"),
        color=discord.Color.blurple(),
    )
    matches = report.get("matches") or []
    embed.add_field(
        name="Connection results",
        value=(f"{report.get('total_matches', len(matches))} recorded account(s) • "
               f"Evidence: {report.get('coverage', 'unknown')}"),
        inline=False,
    )
    for row in matches[:8]:
        evidence = row.get("evidence") or []
        summary = "\n".join(f"• {item['label']}" for item in evidence[:3])
        if row.get("identity_review") == "staff_marked_not_same_person":
            summary += "\n• Staff previously ruled out a same-person claim"
        embed.add_field(
            name=f"Account {row['user_id']} • {row['category']}"[:256],
            value=summary[:1024] or "Recorded relationship",
            inline=False,
        )
    if report.get("history_gap"):
        embed.add_field(
            name="Missing evidence",
            value=str(report["history_gap"])[:1024],
            inline=False,
        )
    if report.get("history_status") and report["history_status"] != "unknown":
        embed.add_field(
            name="Retained history",
            value=f"Account record: {report['history_status']} • Member events checked: {report.get('history_events', 0)}",
            inline=False,
        )
    if not matches:
        embed.add_field(
            name="No verified relationship in available records",
            value="No supported link was found in the records checked. This is not proof the accounts are unrelated.",
            inline=False,
        )
    if report.get("truncated") or len(matches) > 8:
        embed.add_field(name="Coverage limit", value="Only the first matching records are displayed; this is not an exhaustive investigation.", inline=False)
    if report.get("errors"):
        embed.add_field(
            name="Incomplete evidence",
            value="; ".join(report["errors"])[:1024] +
                  "\nMissing records cannot be interpreted as a clean result.",
            inline=False,
        )
    embed.set_footer(text="Staff-only • No automatic punishment • Discord DMs and outside servers are not visible")
    return embed


async def _show_connection_report(
    interaction: discord.Interaction, owner_id: int, target_id: int
) -> None:
    from stoney_verify.member_connection_intelligence import analyze_member_connections

    if not await require_review(interaction):
        return
    try:
        report = await analyze_member_connections(interaction.guild.id, target_id)
    except Exception as exc:
        await reply_ephemeral(interaction, f"⚠️ Connection investigation unavailable ({type(exc).__name__}).")
        return
    await _replace_panel(
        interaction,
        embed=_connection_embed(report),
        view=ConnectionReportView(owner_id, target_id),
    )


class ConnectionReportView(OwnedView):
    def __init__(self, owner_id: int, target_id: int) -> None:
        self.target_id = target_id
        super().__init__(owner_id)

    @discord.ui.button(label="Refresh Evidence", emoji="🔄", style=discord.ButtonStyle.primary, row=0)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.defer(ephemeral=True)
        await _show_connection_report(interaction, self.owner_id, self.target_id)

    @discord.ui.button(label="Stored History", emoji="🧾", style=discord.ButtonStyle.secondary, row=0)
    async def history(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await require_review(interaction):
            return
        await _show_stored_member_history(interaction, self.target_id)

    @discord.ui.button(label="Intelligence Menu", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace_panel(interaction, embed=_intelligence_embed(), view=IntelligenceMenuView(self.owner_id))


class RecentBanConnectionSelect(discord.ui.Select):
    def __init__(self, owner_id: int, bans: list[Any], page: int = 0) -> None:
        self.owner_id = owner_id
        page_bans = bans[page * 25:(page + 1) * 25]
        options = [
            discord.SelectOption(
                label=str(entry.user)[:90],
                value=str(entry.user.id),
                description="Review stored links to this banned account",
            ) for entry in page_bans
        ]
        super().__init__(
            placeholder=f"Choose a banned account (page {page + 1})…",
            options=options,
            min_values=1,
            max_values=1,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await require_review(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        await _show_connection_report(interaction, self.owner_id, int(self.values[0]))


class RecentBanConnectionView(OwnedView):
    def __init__(self, owner_id: int, bans: list[Any], page: int = 0) -> None:
        super().__init__(owner_id)
        self.bans = bans
        self.page = max(0, min(page, (len(bans) - 1) // 25))
        self.add_item(RecentBanConnectionSelect(owner_id, bans, self.page))
        self.previous.disabled = self.page == 0
        self.next_page.disabled = (self.page + 1) * 25 >= len(bans)

    async def _navigate(self, interaction: discord.Interaction, page: int) -> None:
        if not await require_review(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        embed = discord.Embed(
            title="🔗 Recent Ban Connections",
            description=(
                f"**{len(self.bans)} banned accounts** · Page "
                f"**{page + 1}/{(len(self.bans) + 24) // 25}**. "
                "Choose an account to review stored server evidence."
            ),
            color=discord.Color.blurple(),
        )
        await _replace_panel(
            interaction, embed=embed,
            view=RecentBanConnectionView(self.owner_id, self.bans, page),
        )

    @discord.ui.button(label="Previous", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._navigate(interaction, max(0, self.page - 1))

    @discord.ui.button(label="Next", emoji="▶️", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self._navigate(interaction, min((len(self.bans) - 1) // 25, self.page + 1))

    @discord.ui.button(label="Intelligence Menu", emoji="◀️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await _replace_panel(interaction, embed=_intelligence_embed(), view=IntelligenceMenuView(self.owner_id))


class ConnectionByIdModal(discord.ui.Modal, title="Connection Intelligence"):
    user_id = discord.ui.TextInput(
        label="Discord user ID (even if banned)",
        placeholder="Enter a user ID, not a username",
        max_length=22,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.user_id.value or "").strip()
        if not raw.isascii() or not raw.isdecimal() or not 15 <= len(raw) <= 22:
            await reply_ephemeral(interaction, "❌ Enter a valid Discord user ID (15–22 digits).")
            return
        if not await require_review(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _show_connection_report(interaction, interaction.user.id, int(raw))



class MemberHistoryByIdModal(discord.ui.Modal, title="Stored Member Intelligence"):
    user_id = discord.ui.TextInput(
        label="Discord user ID",
        placeholder="629459300854661120",
        max_length=22,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = str(self.user_id.value or "").strip()
        if not raw.isascii() or not raw.isdecimal() or not 15 <= len(raw) <= 22:
            return await reply_ephemeral(interaction, "❌ Enter a valid Discord user ID (15–22 digits).")
        await _show_stored_member_history(interaction, int(raw))


class OperationsSafetyMenuView(OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)

    @discord.ui.button(label="Locked Users", emoji="🔒", style=discord.ButtonStyle.secondary, row=0)
    async def locked_users(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_members_group import members_locked

        await _invoke_command(members_locked, interaction)

    @discord.ui.button(label="Notice Results", emoji="📨", style=discord.ButtonStyle.secondary, row=0)
    async def notices(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_members_group import members_notices

        await _invoke_command(members_notices, interaction)

    @discord.ui.button(label="Evidence Coverage", emoji="📡", style=discord.ButtonStyle.secondary, row=0)
    async def coverage(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_members_group import members_coverage

        await _invoke_command(members_coverage, interaction)

    @discord.ui.button(label="View Cleanup Settings", emoji="⚙️", style=discord.ButtonStyle.secondary, row=1)
    async def view_settings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_members_cleanup_group import members_cleanup_settings

        await _invoke_command(members_cleanup_settings, interaction)

    @discord.ui.button(label="Change Cleanup Settings", emoji="🛠️", style=discord.ButtonStyle.secondary, row=1)
    async def change_settings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(CleanupSettingsModal())

    @discord.ui.button(label="Safety Guide", emoji="🛡️", style=discord.ButtonStyle.primary, row=1)
    async def safety_guide(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        embed = discord.Embed(
            title="🛡️ Member Center Safety Guide",
            description=(
                "• Activity cleanup considers all current member types, not online status.\n"
                "• Low-confidence evidence remains manual-review by default.\n"
                "• Locked users stay out of future inactivity scans until unlocked.\n"
                "• Kick and ban require a reason plus typed confirmation.\n"
                "• Bulk tools never offer mass kick, ban, or timeout.\n"
                "• Staff/control roles and hierarchy are re-checked at action time."
            ),
            color=discord.Color.green(),
        )
        await _replace_panel(
            interaction,
            embed=embed,
            view=SafetyGuideView(self.owner_id),
        )

    @discord.ui.button(label="Command Center", emoji="🏠", style=discord.ButtonStyle.secondary, row=2)
    async def command_center(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        view = MemberCommandCenterView(self.owner_id)
        await _replace_panel(interaction, embed=_center_embed(), view=view)


class CleanupSettingsModal(discord.ui.Modal, title="Change cleanup settings"):
    confirmation = discord.ui.TextInput(
        label="Require queue confirmation? yes/no",
        required=False,
        max_length=3,
    )
    low_confidence = discord.ui.TextInput(
        label="Allow low confidence in queue? yes/no",
        required=False,
        max_length=3,
    )
    queue_limit = discord.ui.TextInput(
        label="Default queue size (1-20)",
        required=False,
        max_length=2,
    )

    @staticmethod
    def _optional_bool(value: Any) -> Optional[bool]:
        text = str(value or "").strip().casefold()
        if not text:
            return None
        if text in {"yes", "y", "true"}:
            return True
        if text in {"no", "n", "false"}:
            return False
        raise ValueError("expected yes or no")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            confirmation = self._optional_bool(self.confirmation.value)
            low_confidence = self._optional_bool(self.low_confidence.value)
            limit_text = str(self.queue_limit.value or "").strip()
            queue_limit = int(limit_text) if limit_text else None
        except Exception:
            await reply_ephemeral(
                interaction,
                "❌ Use yes/no for switches and a whole number for queue size.",
            )
            return
        from .public_members_cleanup_group import members_cleanup_settings

        await _invoke_command(
            members_cleanup_settings,
            interaction,
            require_queue_confirmation=confirmation,
            allow_low_confidence_queue=low_confidence,
            default_queue_limit=queue_limit,
        )


class SafetyGuideView(OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)

    @discord.ui.button(label="Back", emoji="◀️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        view = OperationsSafetyMenuView(self.owner_id)
        await _replace_panel(interaction, embed=_operations_embed(), view=view)


def _member_role_shortcut_embed(member: discord.Member) -> discord.Embed:
    roles = [
        role.mention
        for role in reversed(list(getattr(member, "roles", []) or []))
        if isinstance(role, discord.Role) and not role.is_default()
    ]
    embed = discord.Embed(
        title="👥 Member Role Manager",
        description=f"{member.mention}\n`{member.id}`",
        color=member.color if member.color.value else discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name=f"Current roles ({len(roles)})",
        value=(" ".join(roles) if roles else "No assigned roles.")[:1024],
        inline=False,
    )
    embed.add_field(
        name="Role safety",
        value=(
            "Add/Remove reuses the existing guarded member-role engine. "
            "Protected roles, actor/bot hierarchy, and current permissions are checked again at selection time."
        ),
        inline=False,
    )
    embed.set_footer(text="/role member • Dank Shield Roles & Profiles")
    return embed


class DirectMemberRoleActionView(MemberRoleActionView):
    @discord.ui.button(
        label="Back",
        emoji="◀️",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def back(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        target = await self.parent._fresh_target(interaction)
        if target is not None:
            await interaction.response.edit_message(
                embed=_member_role_shortcut_embed(target),
                view=self.parent,
                allowed_mentions=discord.AllowedMentions.none(),
            )


class DirectMemberRoleView(OwnedView):
    role: Optional[discord.Role] = None

    def __init__(
        self,
        *,
        owner_id: int,
        member: discord.Member,
        browser: Any,
    ) -> None:
        self.member_id = int(member.id)
        self.browser = browser
        super().__init__(owner_id)

    async def _fresh_target(self, interaction: discord.Interaction) -> Optional[discord.Member]:
        member = interaction.guild.get_member(self.member_id)
        if isinstance(member, discord.Member):
            return member
        try:
            fetched = await interaction.guild.fetch_member(self.member_id)
            return fetched if isinstance(fetched, discord.Member) else None
        except Exception:
            await reply_ephemeral(interaction, "❌ That member is no longer in the server.")
            return None

    async def _open_role_action(self, interaction: discord.Interaction, action: str) -> None:
        target = await self._fresh_target(interaction)
        if target is None:
            return
        label = "Add Role" if action == "add_role" else "Remove Role"
        await interaction.response.edit_message(
            embed=discord.Embed(
                title=("➕ " if action == "add_role" else "➖ ") + label,
                description=(
                    f"Target: {target.mention}\n\n"
                    "Choose one role. The existing member-role guard will re-check "
                    "permissions, protected-role rules, and hierarchy before changing anything."
                ),
                color=discord.Color.blurple(),
            ),
            view=DirectMemberRoleActionView(self, action=action),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Add Role", emoji="➕", style=discord.ButtonStyle.primary, row=0)
    async def add_role(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._open_role_action(interaction, "add_role")

    @discord.ui.button(label="Remove Role", emoji="➖", style=discord.ButtonStyle.secondary, row=0)
    async def remove_role(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._open_role_action(interaction, "remove_role")

    @discord.ui.button(label="View Profile", emoji="🪪", style=discord.ButtonStyle.secondary, row=1)
    async def profile(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        target = await self._fresh_target(interaction)
        if target is None:
            return
        from .public_profile_cards import send_privacy_aware_profile
        await send_privacy_aware_profile(interaction, target)

    @discord.ui.button(label="Full Member Panel", emoji="🛡️", style=discord.ButtonStyle.secondary, row=1)
    async def full_panel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        target = await self._fresh_target(interaction)
        if target is None:
            return
        await interaction.response.edit_message(
            embed=member_detail_embed(target, None),
            view=MemberActionView(
                owner_id=self.owner_id,
                member=target,
                browser=self.browser,
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @discord.ui.button(label="Roles & Profiles", emoji="↩️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_role_center import open_roles_profiles_center
        await open_roles_profiles_center(interaction)


async def open_member_target(
    interaction: discord.Interaction,
    member: discord.Member,
) -> None:
    """Open a role-focused staff shortcut using the existing guarded role engine."""
    if not await require_review(interaction):
        return
    guild = interaction.guild
    if guild is None or int(getattr(member.guild, "id", 0) or 0) != int(guild.id):
        return await reply_ephemeral(interaction, "❌ That user is not a member of this server.")

    from .public_member_role_browser import _load_quick_roles

    quick_roles = await _load_quick_roles(guild)
    back_view = LiveMembersMenuView(int(interaction.user.id), quick_roles=quick_roles)
    view = DirectMemberRoleView(
        owner_id=int(interaction.user.id),
        member=member,
        browser=back_view,
    )
    await _replace_panel(
        interaction,
        embed=_member_role_shortcut_embed(member),
        view=view,
    )

async def open_member_command_center(interaction: discord.Interaction) -> None:
    # Staff-role lookup can use guild config; defer before remote reads.
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    if not await require_review(interaction):
        return
    view = MemberCommandCenterView(int(interaction.user.id))
    await _replace_panel(interaction, embed=_center_embed(), view=view)


__all__ = [
    "DirectMemberRoleView",
    "MemberCommandCenterView",
    "open_member_command_center",
    "open_member_target",
]
