from __future__ import annotations

from typing import Any, Optional

import discord

from .member_role_browser_actions import MemberActionView, member_detail_embed
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
    embed = discord.Embed(
        title="👥 Member Command Center",
        description=(
            "One staff command for member browsing, moderation, intelligence, "
            "activity review, cleanup, locks, notices, and safety settings.\n\n"
            "Choose a category below. The entire center is private and locked "
            "to the staff member who opened it."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="👤 Live Members",
        value="Browse any role, search a member, and open guarded moderation actions.",
        inline=False,
    )
    embed.add_field(
        name="📊 Activity & Cleanup",
        value="Run inactivity reviews, reopen results, and use confirmed cleanup flows.",
        inline=False,
    )
    embed.add_field(
        name="🧠 Intelligence",
        value="Review join intelligence and inspect reversible staff-verdict history.",
        inline=False,
    )
    embed.add_field(
        name="🔒 Operations & Safety",
        value="Manage scan locks, notice results, evidence coverage, and cleanup settings.",
        inline=False,
    )
    embed.set_footer(text="/dank members • one command, feature menus inside")
    return embed


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
            "Review verified/resident activity using server evidence—not presence. "
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
            "Select a current member to review join evidence, staff verdicts, and "
            "their guarded moderation panel. Departed-user verdict history can be "
            "looked up by Discord user ID."
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
        member = interaction.guild.get_member(int(getattr(selected, "id", 0) or 0))
        if not isinstance(member, discord.Member):
            await reply_ephemeral(
                interaction,
                "❌ That user is not currently a server member.",
            )
            return
        view = IntelligenceTargetView(
            owner_id=self.parent_view.owner_id,
            member=member,
        )
        await interaction.response.edit_message(
            embed=member_detail_embed(member, None),
            view=view,
        )


class IntelligenceMenuView(OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)
        self.add_item(IntelligenceMemberSelect(self))

    @discord.ui.button(label="History by User ID", emoji="🧾", style=discord.ButtonStyle.secondary, row=1)
    async def history_by_id(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(MemberHistoryByIdModal())

    @discord.ui.button(label="Command Center", emoji="🏠", style=discord.ButtonStyle.secondary, row=2)
    async def command_center(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        view = MemberCommandCenterView(self.owner_id)
        await _replace_panel(interaction, embed=_center_embed(), view=view)


class IntelligenceTargetView(OwnedView):
    def __init__(self, *, owner_id: int, member: discord.Member) -> None:
        self.member_id = int(member.id)
        super().__init__(owner_id)

    def _member(self, interaction: discord.Interaction) -> Optional[discord.Member]:
        member = interaction.guild.get_member(self.member_id)
        return member if isinstance(member, discord.Member) else None

    @discord.ui.button(label="Intelligence Review", emoji="🧠", style=discord.ButtonStyle.primary, row=0)
    async def review(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        member = self._member(interaction)
        if member is None:
            await reply_ephemeral(interaction, "❌ That member left the server.")
            return
        await open_review_panel(interaction, member)

    @discord.ui.button(label="Verdict History", emoji="🧾", style=discord.ButtonStyle.secondary, row=0)
    async def history(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        member = self._member(interaction)
        if member is None:
            await reply_ephemeral(interaction, "❌ That member left the server.")
            return
        from .public_member_review_feedback import review_history

        await _invoke_command(review_history, interaction, member)

    @discord.ui.button(label="Moderation Panel", emoji="🛡️", style=discord.ButtonStyle.secondary, row=0)
    async def moderation(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        member = self._member(interaction)
        if member is None:
            await reply_ephemeral(interaction, "❌ That member left the server.")
            return
        from .public_member_role_browser import _load_quick_roles

        quick_roles = await _load_quick_roles(interaction.guild)
        back_view = LiveMembersMenuView(self.owner_id, quick_roles=quick_roles)
        view = MemberActionView(
            owner_id=self.owner_id,
            member=member,
            browser=back_view,
        )
        await interaction.response.edit_message(
            embed=member_detail_embed(member, None),
            view=view,
        )

    @discord.ui.button(label="Back to Intelligence", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        view = IntelligenceMenuView(self.owner_id)
        await _replace_panel(interaction, embed=_intelligence_embed(), view=view)


class MemberHistoryByIdModal(discord.ui.Modal, title="Member verdict history"):
    user_id = discord.ui.TextInput(
        label="Discord user ID",
        placeholder="629459300854661120",
        max_length=20,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            user_id = int(str(self.user_id.value).strip())
        except Exception:
            await reply_ephemeral(interaction, "❌ Enter a valid numeric Discord user ID.")
            return
        try:
            user = interaction.guild.get_member(user_id) or await interaction.client.fetch_user(user_id)
        except Exception:
            await reply_ephemeral(interaction, "❌ Discord could not resolve that user ID.")
            return
        from .public_member_review_feedback import review_history

        await _invoke_command(review_history, interaction, user)


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
                "• Activity cleanup uses post-verification server evidence, not online status.\n"
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


async def open_member_command_center(interaction: discord.Interaction) -> None:
    if not await require_review(interaction):
        return
    view = MemberCommandCenterView(int(interaction.user.id))
    await _replace_panel(interaction, embed=_center_embed(), view=view)


__all__ = [
    "MemberCommandCenterView",
    "open_member_command_center",
]
