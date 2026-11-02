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
            "Select a member to see one evidence report with buttons for history, "
            "connections, Safety Reports and External Sources. Historical accounts can "
            "be reviewed by ID. Missing records are not proof of a clean history, "
            "and reported connections are not proof of wrongdoing."
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
        # Exactly ONE account review panel: history and all investigation
        # actions stay together. A follow-up history message used to visually
        # bury the buttons shown by the first response on Discord mobile.
        view = (
            IntelligenceTargetView(owner_id=self.parent_view.owner_id, member=member)
            if member is not None else
            HistoricalIntelligenceTargetView(self.parent_view.owner_id, user_id)
        )
        await _show_stored_member_history(
            interaction, user_id, presence=status, view=view,
            live_member=member,
        )


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
        # Discord's fetch_member may exceed the 3-second interaction window.
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        # Keep the buttons on the same ephemeral review instead of opening
        # a second history-only message.
        member, status = await _resolve_intelligence_member(interaction.guild, self.member_id)
        await _show_stored_member_history(
            interaction, self.member_id, presence=status, view=self, live_member=member,
        )

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

    @discord.ui.button(label="Safety Reports", emoji="🛡️", style=discord.ButtonStyle.secondary, row=1)
    async def safety_reports(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _show_native_safety_reports(interaction, self.member_id)

    @discord.ui.button(label="External Sources", emoji="🔎", style=discord.ButtonStyle.secondary, row=2)
    async def external_sources(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _show_external_sources(interaction, self.member_id)

    @discord.ui.button(label="Back to Intelligence", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        view = IntelligenceMenuView(self.owner_id)
        await _replace_panel(interaction, embed=_intelligence_embed(), view=view)



class HistoricalIntelligenceTargetView(OwnedView):
    """Keep the same investigation actions available for departed/banned IDs."""

    def __init__(self, owner_id: int, target_id: int) -> None:
        self.target_id = int(target_id)
        super().__init__(owner_id)

    @discord.ui.button(label="Stored History", emoji="🧾", style=discord.ButtonStyle.secondary, row=0)
    async def history(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _show_stored_member_history(
            interaction, self.target_id, view=HistoricalIntelligenceTargetView(self.owner_id, self.target_id),
        )

    @discord.ui.button(label="Connections", emoji="🔗", style=discord.ButtonStyle.secondary, row=0)
    async def connections(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await require_review(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        await _show_connection_report(interaction, self.owner_id, self.target_id)

    @discord.ui.button(label="Safety Reports", emoji="🛡️", style=discord.ButtonStyle.secondary, row=1)
    async def safety_reports(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _show_native_safety_reports(interaction, self.target_id)

    @discord.ui.button(label="External Sources", emoji="🔎", style=discord.ButtonStyle.secondary, row=1)
    async def external_sources(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _show_external_sources(interaction, self.target_id)

    @discord.ui.button(label="Intelligence Menu", emoji="↩️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace_panel(interaction, embed=_intelligence_embed(), view=IntelligenceMenuView(self.owner_id))


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
    feed = snapshot.get("activity_feed_summary") or {}
    feed_rows = snapshot.get("recent_feed_events") or []
    activity_ledger = snapshot.get("activity_ledger") or {}
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
    def _plain(value: str, limit: int = 80) -> str:
        return discord.utils.escape_markdown(value[:limit]).replace("@", "@\u200b")
    status = snapshot.get("source_status") or {}
    sample_limits = snapshot.get("sample_limits") or {}
    event_count = (
        str(events.get("total", 0)) if status.get("events") != "unavailable"
        else "unavailable"
    )
    event_moderation = (
        str(events.get("moderation_count", 0)) if status.get("events") != "unavailable"
        else "unavailable"
    )
    ticket_count = (
        str(tickets.get("total", 0)) if status.get("tickets") != "unavailable"
        else "unavailable"
    )
    joins_available = status.get("joins") != "unavailable" and status.get("profile") != "unavailable"
    join_count = str(joins.get("times_joined", 0)) if joins_available else "unavailable"
    leave_count = str(joins.get("times_left", 0)) if joins_available else "unavailable"
    join_text = (
        f"Recorded joins (minimum): **{join_count}** • "
        f"Recorded departures (minimum): **{leave_count}**\\n"
        f"Latest recorded join: `{str(joins.get('latest_joined_at') or 'unknown')[:50]}`\\n"
        f"Earliest retained join: `{str(joins.get('first_joined_at') or 'unknown')[:50]}`\\n"
        f"Last recorded departure: `{str(joins.get('left_at') or 'not recorded')[:50]}`\\n"
        f"Last seen (stored): `{str(joins.get('last_seen_at') or 'unknown')[:50]}`"
    ).replace("\\n", "\n")
    all_names = names.get("all_names") or []
    if all_names:
        past_names = ", ".join(
            discord.utils.escape_mentions(discord.utils.escape_markdown(str(n)[:32]))
            for n in all_names[:5]
        )
        join_text += f"\\nRetained names: {past_names}"
    last_tracked = activity_ledger.get("last_activity_at")
    if last_tracked:
        tracked_type = _plain(str(activity_ledger.get("last_activity_type") or "unknown"), 32)
        join_text += (
            f"\\nTracked activity: `{str(last_tracked)[:32]}` ({tracked_type})"
        )
    embed.add_field(name="Live membership", value=_intelligence_presence_text(presence), inline=False)
    embed.add_field(name="Membership History", value=join_text.replace("\\n", "\n")[:1024], inline=False)
    recent = snapshot.get("recent_joins") or []
    latest = recent[0] if recent and isinstance(recent[0], dict) else {}
    relations = snapshot.get("relationships") or {}
    method = str(relations.get("entry_method") or latest.get("entry_method") or "").strip()
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
    # The relationship service already enforces same-visit timestamp matching.
    # Re-prioritizing raw profile/join fields here would undo that safeguard.
    code = str(relations.get("invite_code") or "").strip()
    inviter = str(relations.get("invited_by_name") or "").strip()
    quality = str(relations.get("entry_truth_quality") or "").strip().lower()
    conflicted = bool(relations.get("entry_conflict"))
    if conflicted:
        confidence = "Conflicting records; attribution not verified"
    elif quality in {"confirmed", "partial", "unknown"}:
        confidence = {"confirmed": "Confirmed in retained join record", "partial": "Partial / uncertain", "unknown": "Unknown"}[quality]
    else:
        confidence = "Not recorded"
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
        relations.get("entry_quality_reason") or ""
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
    event_detail_lines = []
    for event in (snapshot.get("recent_events") or [])[:3]:
        when = str(event.get("created_at") or "unknown")[:16]
        kind = _plain(str(event.get("event_type") or "unknown"), 32)
        title = _plain(str(event.get("title") or event.get("reason") or ""), 65)
        event_detail_lines.append(f"• `{when}` {kind}" + (f": {title}" if title else ""))
    event_details = "\\n".join(event_detail_lines)
    feed_detail_lines = []
    for record in feed_rows[:3]:
        when = str(record.get("created_at") or "unknown")[:16]
        family = _plain(str(record.get("event_family") or "unknown"), 26)
        kind = _plain(str(record.get("event_type") or "unknown"), 28)
        reason = _plain(str(record.get("title") or record.get("reason") or ""), 55)
        feed_detail_lines.append(
            f"• `{when}` [{family}] {kind}" + (f": {reason}" if reason else "")
        )
    feed_details = "\\n".join(feed_detail_lines)
    feed_line = ""
    if sample_limits.get("activity_feed"):
        feed_count = (
            str(feed.get("total", 0))
            if status.get("activity_feed") != "unavailable"
            else "unavailable"
        )
        feed_moderation = (
            str(feed.get("moderation_count", 0))
            if status.get("activity_feed") != "unavailable"
            else "unavailable"
        )
        feed_line = (
            f"\\nActivity feed (up to {sample_limits['activity_feed']}): "
            f"**{feed_count}** records, **{feed_moderation}** moderation."
        )
    embed.add_field(
        name="Activity & Moderation Evidence",
        value=(
            f"Member events (up to {sample_limits.get('events', 30)}): **{event_count}** • "
            f"Moderation: **{event_moderation}**\\n"
            f"Last member event: `{str(events.get('latest_event_type') or 'unknown')[:65]}`"
            + feed_line
            + (f"\\nRecent feed records:\\n{feed_details}" if feed_details else "")
            + (f"\\nMember-event sample:\\n{event_details}" if event_details and not feed_details else "")
            + (f"\\nTypes: {type_text}" if not feed_details else "")
        ).replace("\\n", "\n")[:1024],
        inline=False,
    )
    ticket_samples = []
    for ticket in (snapshot.get("recent_tickets") or [])[:3]:
        category = _plain(str(ticket.get("category") or "uncategorized"), 28)
        ticket_state = _plain(str(ticket.get("status") or "unknown"), 20)
        title = _plain(str(ticket.get("title") or ""), 62)
        ticket_samples.append(f"• {category} [{ticket_state}]" + (f": {title}" if title else ""))
    ticket_details = "\\n".join(ticket_samples)
    verdict_details = "\\n".join(
        f"• {str((row.get('metadata') or {}).get('verdict_label') or (row.get('metadata') or {}).get('verdict') or 'Decision')[:42]}"
        for row in verdicts[:5]
    )
    embed.add_field(
        name="Tickets & Staff Decisions",
        value=(
            f"Recent retained tickets (up to {sample_limits.get('tickets', 15)}): **{ticket_count}** • "
            f"Staff verdicts (last 10 queried): **{len(verdicts)}**"
            + (f"\\nVerdicts:\\n{verdict_details}" if verdict_details else "")
            + (f"\\nRecent tickets:\\n{ticket_details}" if ticket_details else "")
        ).replace("\\n", "\n")[:1024],
        inline=False,
    )
    unavailable = [label for label, state in status.items() if state == "unavailable"]
    if unavailable:
        note = (
            "⚠️ Could not read: **" + ", ".join(unavailable) + "**. "
            "Unknown data is not a clean result."
        )
    else:
        note = "Available readbacks are retained minimums, not lifetime totals."
    no_records = not (
        snapshot.get("recent_joins") or snapshot.get("recent_events")
        or snapshot.get("recent_tickets") or snapshot.get("recent_feed_events")
        or activity_ledger or verdicts
        or member.get("username") or member.get("joined_at")
    )
    if no_records:
        note += " No records returned; Discord cannot restore uncollected or deleted history."
    samples = (
        f"Readback limits: {sample_limits.get('joins', 15)} joins, "
        f"{sample_limits.get('events', 30)} member events, "
        f"{sample_limits.get('activity_feed', 0)} feed records, "
        f"{sample_limits.get('tickets', 15)} tickets."
    )
    synced = str(member.get("synced_at") or member.get("updated_at") or "unknown")
    embed.add_field(
        name="Evidence coverage & freshness",
        value=(f"{note}\\n{samples}\\nStored profile updated: `{synced[:45]}`").replace("\\n", "\n")[:1024],
        inline=False,
    )
    embed.set_footer(text="Server-local recorded evidence only • live membership checked separately")
    return embed


async def _show_stored_member_history(
    interaction: discord.Interaction, user_id: int, *, presence: Optional[str] = None,
    view: Optional[discord.ui.View] = None,
    live_member: Optional[discord.Member] = None,
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
                    ticket_limit=15, event_limit=30, join_limit=15,
                    include_extended_intelligence=True,
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
        if view is None:
            return await reply_ephemeral(
                interaction, "⚠️ Historical records timed out; no safety conclusion.",
            )
        timeout_embed = discord.Embed(
            title="🧠 Member Intelligence • History unavailable",
            description=(
                f"Account: `{user_id}`\\n"
                "Stored evidence timed out. This is NOT a clean history. "
                "Your account review actions remain available."
            ).replace("\\n", "\n"),
            color=discord.Color.orange(),
        )
        timeout_embed.add_field(name="Live membership", value=_intelligence_presence_text(presence), inline=False)
        return await _replace_panel(interaction, embed=timeout_embed, view=view)
    # Even a fully failed snapshot carries per-source outage diagnostics.
    # Keep its evidence status rather than rendering an apparent empty history
    # when unrelated staff-verdict records were recovered.
    snapshot = (
        context_result if isinstance(context_result, dict)
        and context_result.get("source_status") is not None else {}
    )
    verdicts = feedback_result if isinstance(feedback_result, list) else []
    if snapshot and not isinstance(feedback_result, list):
        snapshot = dict(snapshot)
        snapshot["source_status"] = {
            **(snapshot.get("source_status") or {}),
            "staff_verdicts": "unavailable",
        }
    if not snapshot and not verdicts:
        if view is None:
            return await reply_ephemeral(
                interaction,
                "⚠️ Stored member records could not be read. This does not mean their history was deleted.",
            )
        embed = discord.Embed(
            title="🧠 Member Intelligence • Data unavailable",
            description=(
                f"Account: `{user_id}`\\n"
                "Historical database read failed. This is not a clean-history "
                "result; the investigation buttons remain available."
            ).replace("\\n", "\n"),
            color=discord.Color.orange(),
        )
        embed.add_field(name="Live membership", value=_intelligence_presence_text(presence), inline=False)
    else:
        embed = _historical_member_embed(user_id, snapshot, verdicts, presence=presence)
    if live_member is not None and presence == "verified":
        joined = str(getattr(live_member, "joined_at", None) or "unknown")[:32]
        created = str(getattr(live_member, "created_at", None) or "unknown")[:32]
        display = discord.utils.escape_mentions(
            discord.utils.escape_markdown(str(getattr(live_member, "display_name", "unknown"))[:80])
        )
        role_names = [
            discord.utils.escape_mentions(discord.utils.escape_markdown(str(role.name)[:34]))
            for role in getattr(live_member, "roles", [])[1:]
        ]
        # Reuse the existing membership section instead of adding another
        # tall field to the phone screen.
        embed.set_field_at(
            0,
            name="Live Discord facts (checked now)",
            value=(
                _intelligence_presence_text(presence)
                + "\\n"
                + f"Display: **{display}**\\n"
                + f"Account created: `{created}`\\n"
                + f"Current server join: `{joined}`\\n"
                + f"Current roles ({len(role_names)}): "
                + (", ".join(role_names[:5]) or "none")
            ).replace("\\n", "\n")[:1024],
            inline=False,
        )
    if view is not None:
        await _replace_panel(interaction, embed=embed, view=view)
    else:
        await interaction.followup.send(
            embed=embed, ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )



_CONNECTION_PAGE_SIZE = 6


def _connection_embed(report: dict[str, Any], *, page: int = 0) -> discord.Embed:
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
    page_count = max(1, (len(matches) + _CONNECTION_PAGE_SIZE - 1) // _CONNECTION_PAGE_SIZE)
    page = max(0, min(page, page_count - 1))
    visible = matches[page * _CONNECTION_PAGE_SIZE:(page + 1) * _CONNECTION_PAGE_SIZE]
    coverage_note = (
        f"{report.get('total_matches', len(matches))} recorded account(s) • "
        f"Evidence: {report.get('coverage', 'unknown')}"
    )
    if page_count > 1:
        coverage_note += f" • Page {page + 1}/{page_count}"
    if report.get("recovered_join_events"):
        coverage_note += f" • Original join events with links: {report['recovered_join_events']}"
    if report.get("history_gap"):
        coverage_note += "\n" + str(report["history_gap"])[:680]
    embed.add_field(
        name="Connection results",
        value=coverage_note[:1024],
        inline=False,
    )
    for row in visible:
        evidence = row.get("evidence") or []
        summary = "\n".join(f"• {item['label']}" for item in evidence[:2])
        ban_status = {
            "banned": "Banned",
            "not_banned": "Not banned",
            "unavailable": "Could not verify",
            "unknown": "Not checked",
        }.get(row.get("ban_status"), "Not checked")
        summary += f"\n• Discord ban: {ban_status}"
        if row.get("identity_review") == "staff_marked_not_same_person":
            summary += "\n• Staff previously ruled out a same-person claim"
        embed.add_field(
            name=f"Account {row['user_id']} • {row['category']}"[:256],
            value=summary[:1024] or "Recorded relationship",
            inline=False,
        )
    if not matches:
        embed.add_field(
            name="No verified relationship in available records",
            value=(str(report.get("history_gap") or
                       "No supported link was found in the records checked. This is not proof the accounts are unrelated.")[:1024]),
            inline=False,
        )
    if report.get("truncated") or report.get("total_matches", len(matches)) > len(matches):
        embed.add_field(
            name="Coverage limit",
            value="All loaded matches can be paged, but some database results hit a search cap. This is not an exhaustive investigation.",
            inline=False,
        )
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
    from stoney_verify.member_connection_intelligence import analyze_member_connections, enrich_connection_ban_status

    if not await require_review(interaction):
        return
    try:
        report = await analyze_member_connections(interaction.guild.id, target_id)
        report = await enrich_connection_ban_status(interaction.guild, report)
    except Exception as exc:
        await reply_ephemeral(interaction, f"⚠️ Connection investigation unavailable ({type(exc).__name__}).")
        return
    await _replace_panel(
        interaction,
        embed=_connection_embed(report),
        view=ConnectionReportView(owner_id, target_id, report),
    )


class ConnectionReportView(OwnedView):
    def __init__(
        self, owner_id: int, target_id: int, report: dict[str, Any],
        page: int = 0,
    ) -> None:
        self.target_id = target_id
        self.report = report
        self.page_count = max(1, (len(report.get("matches") or []) + _CONNECTION_PAGE_SIZE - 1)
                              // _CONNECTION_PAGE_SIZE)
        self.page = max(0, min(page, self.page_count - 1))
        super().__init__(owner_id)
        self.previous.disabled = self.page == 0
        self.next_page.disabled = self.page + 1 >= self.page_count

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
        await _show_stored_member_history(interaction, self.target_id, view=self)

    async def _navigate(self, interaction: discord.Interaction, page: int) -> None:
        if not await require_review(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        await _replace_panel(
            interaction,
            embed=_connection_embed(self.report, page=page),
            view=ConnectionReportView(self.owner_id, self.target_id, self.report, page),
        )

    @discord.ui.button(label="Previous", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._navigate(interaction, self.page - 1)

    @discord.ui.button(label="Next", emoji="▶️", style=discord.ButtonStyle.secondary, row=1)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._navigate(interaction, self.page + 1)

    @discord.ui.button(label="Record Affiliate", emoji="🔖", style=discord.ButtonStyle.secondary, row=2)
    async def mark_affiliate(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _is_guild_owner_for_affiliations(interaction):
            return await reply_ephemeral(interaction, "Only this server's owner can record affiliate reviews.")
        embed = discord.Embed(
            title="🔖 Review Known Affiliates",
            description=(
                f"Selected account: `{self.target_id}`\\n"
                "Choose a relationship, then supply another account ID and the evidence. "
                "Former or banned accounts can be entered by ID. "
                "Owner reports are separate from alternate identities, bans and automated restrictions."
            ).replace("\\n", "\n"),
            color=discord.Color.blurple(),
        )
        await _replace_panel(
            interaction, embed=embed,
            view=OwnerAffiliationChoiceView(self.owner_id, self.target_id),
        )

    @discord.ui.button(label="Safety Reports", emoji="🛡️", style=discord.ButtonStyle.secondary, row=2)
    async def safety_reports(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _show_native_safety_reports(interaction, self.target_id)

    @discord.ui.button(label="External Sources", emoji="🔎", style=discord.ButtonStyle.secondary, row=2)
    async def external_sources(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _show_external_sources(interaction, self.target_id)

    @discord.ui.button(label="Intelligence Menu", emoji="↩️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await require_review(interaction):
            return
        await _replace_panel(interaction, embed=_intelligence_embed(), view=IntelligenceMenuView(self.owner_id))


def _is_guild_owner_for_affiliations(interaction: discord.Interaction) -> bool:
    guild = interaction.guild
    return bool(
        guild is not None
        and getattr(guild, "owner_id", None) is not None
        and int(interaction.user.id) == int(guild.owner_id)
    )


async def _show_native_safety_reports(
    interaction: discord.Interaction, target_id: int,
) -> None:
    """Owner-only read of Dank Shield's own opt-in incident network.

    This intentionally does not infer friendships, server memberships or
    alternate identities. Other guilds' reports remain explicit owner claims.
    """
    if not _is_guild_owner_for_affiliations(interaction):
        await reply_ephemeral(
            interaction, "Only the physical server owner can inspect shared safety reports."
        )
        return
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)
    from stoney_verify.network_safety import reports_for_owner
    from .public_network_safety import NetworkReportPages
    try:
        reports = await asyncio.wait_for(
            reports_for_owner(interaction.guild, target_id, interaction.client),
            timeout=8,
        )
    except Exception:
        await reply_ephemeral(
            interaction, "Safety reports are unavailable. No negative finding is implied."
        )
        return
    view = NetworkReportPages(target_id, reports)
    await _replace_panel(interaction, embed=view.embed(), view=view)


class ExternalLinkCheckModal(discord.ui.Modal, title="Check historical scam-domain list"):
    url = discord.ui.TextInput(
        label="Link or hostname to check",
        placeholder="example.com",
        min_length=4, max_length=300, required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _is_guild_owner_for_affiliations(interaction):
            return await reply_ephemeral(interaction, "Only the server owner can use external safety checks.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        from stoney_verify.member_external_safety_sources import check_historical_scam_domain
        try:
            finding = await asyncio.wait_for(
                check_historical_scam_domain(str(self.url.value)), timeout=10,
            )
            message = f"**{finding.source}** • {finding.status.replace('_', ' ').title()}\\n{finding.detail}\\n{finding.url}"
        except ValueError:
            message = "Enter a valid HTTP(S) link or hostname."
        except (asyncio.TimeoutError, Exception):
            message = "Historical domain feed is unavailable; no safety conclusion."
        await interaction.followup.send(message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none())


class ExternalSourcesView(OwnedView):
    def __init__(self, owner_id: int):
        super().__init__(owner_id)
        # The providers publish searchable pages but no confirmed read-only
        # account API. Open the official site so the owner can enter the ID;
        # do not scrape HTML or invent a user-ID endpoint.
        self.add_item(discord.ui.Button(
            label="Open Trustify", style=discord.ButtonStyle.link,
            url="https://trustify.fun/scammers", row=1,
        ))
        self.add_item(discord.ui.Button(
            label="Open Redacted", style=discord.ButtonStyle.link,
            url="https://project-redacted.org/", row=1,
        ))

    @discord.ui.button(label="Check Scam Domain", emoji="🔗", style=discord.ButtonStyle.secondary)
    async def check_link(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _is_guild_owner_for_affiliations(interaction):
            return await reply_ephemeral(interaction, "Only the server owner can use external safety checks.")
        await interaction.response.send_modal(ExternalLinkCheckModal())

    @discord.ui.button(label="Back to Intelligence", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await require_review(interaction):
            return
        await _replace_panel(interaction, embed=_intelligence_embed(), view=IntelligenceMenuView(self.owner_id))


async def _show_external_sources(interaction: discord.Interaction, target_id: int) -> None:
    """Never silently convert third-party accusations into enforcement evidence."""
    if not _is_guild_owner_for_affiliations(interaction):
        return await reply_ephemeral(interaction, "Only the server owner can inspect external safety checks.")
    await interaction.response.defer(ephemeral=True)
    from stoney_verify.member_external_safety_sources import inspect_external_sources
    # Fetch only this exact member. A failed fetch must not cause us to guess a
    # username from outdated guild history or connect unrelated account reviews.
    member, status = await _resolve_intelligence_member(interaction.guild, target_id)
    username = str(member.name) if status == "verified" and member is not None else None
    try:
        findings = await asyncio.wait_for(
            inspect_external_sources(target_id, username=username), timeout=9,
        )
    except Exception:
        return await reply_ephemeral(interaction, "External services unavailable. No negative finding is implied.")
    embed = discord.Embed(
        title="🔎 External Safety Sources",
        description=(
            f"Discord ID: `{target_id}`\n"
            "On-demand independent provider claims, **not Dank Shield verified incidents**. "
            "A missing report is not proof of safety. No automatic moderation, "
            "third-party bot installation or other-server membership profiling."
        ).replace("\\n", "\n"),
        color=discord.Color.blurple(),
    )
    for finding in findings:
        title = {
            "reported": "⚠️ Provider report (unverified)",
            "none_reported": "No provider report (not a clearance)",
            "unavailable": "Unavailable / not configured",
            "manual_review": "Manual search only",
        }.get(finding.status, "Unknown")
        detail = discord.utils.escape_mentions(discord.utils.escape_markdown(finding.detail[:240]))
        embed.add_field(
            name=f"{finding.source}: {title}",
            value=(detail + (f"\\nSource: {finding.url}" if finding.url else ""))[:1024],
            inline=False,
        )
    embed.set_footer(text="Private owner review • provider quotas apply • never punish on these entries alone")
    await _replace_panel(interaction, embed=embed, view=ExternalSourcesView(int(interaction.user.id)))


class OwnerAffiliationModal(discord.ui.Modal, title="Owner Affiliate Review"):
    other_account = discord.ui.TextInput(
        label="Other Discord account ID", placeholder="Enter ID, including a banned account",
        min_length=15, max_length=22, required=True,
    )
    reason = discord.ui.TextInput(
        label="Evidence or reason", style=discord.TextStyle.paragraph,
        placeholder="Explain what you know without assuming both accounts share an owner",
        min_length=8, max_length=500, required=True,
    )

    def __init__(self, target_id: int, mode: str, *, other_id: int = 0) -> None:
        super().__init__()
        self.target_id = target_id
        self.mode = mode
        if other_id > 0:
            self.other_account.default = str(other_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _is_guild_owner_for_affiliations(interaction):
            return await reply_ephemeral(interaction, "Only the server owner can save this review.")
        raw_id = str(self.other_account.value or "").strip()
        if not raw_id.isascii() or not raw_id.isdecimal() or not 15 <= len(raw_id) <= 22:
            return await reply_ephemeral(interaction, "Enter the other account's Discord ID (15–22 digits).")
        if int(raw_id) == self.target_id:
            return await reply_ephemeral(interaction, "Choose a different account.")
        from stoney_verify.member_connection_intelligence import save_owner_affiliation
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await asyncio.to_thread(
                save_owner_affiliation,
                guild_id=int(interaction.guild.id),
                target_id=self.target_id,
                other_id=int(raw_id),
                actor_id=int(interaction.user.id),
                mode=self.mode,
                reason=str(self.reason.value or ""),
            )
        except Exception as exc:
            return await reply_ephemeral(
                interaction,
                f"Could not store the association review ({type(exc).__name__}). No success is assumed.",
            )
        labels = {
            "known": "Known Affiliate (owner-reported)",
            "possible": "Possible Affiliate (unverified)",
            "cleared": "Association Cleared (owner review)",
        }
        await reply_ephemeral(
            interaction,
            f"Saved {labels[self.mode]} for account `{raw_id}`. "
            "Use Refresh Evidence to see the latest recorded review. "
            "This does not change bans, verification, roles or automatic risk scoring.",
        )


class OwnerAffiliationChoiceSelect(discord.ui.Select):
    def __init__(self, target_id: int) -> None:
        self.target_id = target_id
        super().__init__(
            placeholder="Choose relationship review",
            options=[
                discord.SelectOption(label="Known Affiliate", value="known",
                                     description="Owner-reported association, not an alt"),
                discord.SelectOption(label="Possible Affiliate", value="possible",
                                     description="Unverified relationship lead"),
                discord.SelectOption(label="Clear Association", value="cleared",
                                     description="Withdraw an earlier affiliate claim"),
            ],
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _is_guild_owner_for_affiliations(interaction):
            return await reply_ephemeral(interaction, "Only the server owner can review affiliates.")
        mode = str(self.values[0])
        embed = discord.Embed(
            title="🔖 Choose the Associated Account",
            description=(
                f"Account: `{self.target_id}`\\n"
                "Choose a current member from the Discord picker, or enter the ID "
                "of an account that left or was banned. You'll then record your evidence."
            ).replace("\\n", "\n"),
            color=discord.Color.blurple(),
        )
        await _replace_panel(
            interaction, embed=embed,
            view=OwnerAffiliationTargetView(int(interaction.user.id), self.target_id, mode),
        )


class OwnerAffiliationUserSelect(discord.ui.UserSelect):
    def __init__(self, target_id: int, mode: str) -> None:
        self.target_id = target_id
        self.mode = mode
        super().__init__(
            placeholder="Choose a current member",
            min_values=1, max_values=1, row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not _is_guild_owner_for_affiliations(interaction):
            return await reply_ephemeral(interaction, "Only the server owner can review affiliates.")
        chosen = self.values[0]
        if int(chosen.id) == self.target_id:
            return await reply_ephemeral(interaction, "Choose a different account.")
        await interaction.response.send_modal(
            OwnerAffiliationModal(self.target_id, self.mode, other_id=int(chosen.id)),
        )


class OwnerAffiliationTargetView(OwnedView):
    def __init__(self, owner_id: int, target_id: int, mode: str) -> None:
        super().__init__(owner_id)
        self.target_id = target_id
        self.mode = mode
        self.add_item(OwnerAffiliationUserSelect(target_id, mode))

    @discord.ui.button(label="Enter Account ID", emoji="🔢",
                       style=discord.ButtonStyle.secondary, row=1)
    async def by_id(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _is_guild_owner_for_affiliations(interaction):
            return await reply_ephemeral(interaction, "Only the server owner can review affiliates.")
        await interaction.response.send_modal(
            OwnerAffiliationModal(self.target_id, self.mode),
        )

    @discord.ui.button(label="Back to Relationship", emoji="↩️",
                       style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _is_guild_owner_for_affiliations(interaction):
            return await reply_ephemeral(interaction, "Only the server owner can review affiliates.")
        await _replace_panel(
            interaction, embed=discord.Embed(
                title="🔖 Review Known Affiliates",
                description="Choose known, possible, or clear an earlier owner-reported association.",
                color=discord.Color.blurple(),
            ),
            view=OwnerAffiliationChoiceView(self.owner_id, self.target_id),
        )


class OwnerAffiliationChoiceView(OwnedView):
    def __init__(self, owner_id: int, target_id: int) -> None:
        super().__init__(owner_id)
        self.target_id = target_id
        self.add_item(OwnerAffiliationChoiceSelect(target_id))

    @discord.ui.button(label="Back to Connections", emoji="↩️",
                       style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await require_review(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        await _show_connection_report(interaction, self.owner_id, self.target_id)


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
        target_id = int(raw)
        await _show_stored_member_history(
            interaction, target_id,
            view=HistoricalIntelligenceTargetView(int(interaction.user.id), target_id),
        )


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
