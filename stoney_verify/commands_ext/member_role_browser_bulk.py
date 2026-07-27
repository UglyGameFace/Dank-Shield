from __future__ import annotations

import discord

from .member_role_browser_common import (
    OwnedView,
    action_lock,
    display_name,
    load_protected_role_ids,
    record_member_action,
    reply_ephemeral,
    require_review,
    role_action_blockers,
    trim,
)


def _member_option_description(member: discord.Member) -> str:
    joined = getattr(member, "joined_at", None)
    joined_text = (
        joined.strftime("joined %Y-%m-%d")
        if joined is not None
        else "join date unknown"
    )
    status = (
        "bot"
        if member.bot
        else ("timed out" if member.is_timed_out() else "member")
    )
    return trim(f"{joined_text} • {status} • ID {member.id}", 100)


class BulkMemberSelect(discord.ui.Select):
    def __init__(self, browser: "RoleMemberBrowserView") -> None:
        members = browser.page_members()
        options = [
            discord.SelectOption(
                label=trim(str(member.display_name or member.name), 100),
                value=str(member.id),
                description=_member_option_description(member),
            )
            for member in members
        ]
        if not options:
            options = [
                discord.SelectOption(
                    label="No members on this page",
                    value="none",
                )
            ]
        super().__init__(
            placeholder="Select members for safe bulk actions…",
            min_values=1,
            max_values=max(1, len(members)),
            options=options,
            disabled=not members,
            custom_id="dank_members_browser:bulk_select",
            row=0,
        )
        self.browser = browser

    async def callback(self, interaction: discord.Interaction) -> None:
        ids = [int(value) for value in self.values if value != "none"]
        members = [interaction.guild.get_member(user_id) for user_id in ids]
        selected = [
            member
            for member in members
            if isinstance(member, discord.Member)
        ]
        if not selected:
            await reply_ephemeral(
                interaction,
                "❌ None of the selected members are still available.",
            )
            return
        view = BulkActionView(self.browser, selected)
        embed = discord.Embed(
            title="🧰 Safe Bulk Actions",
            description=(
                f"Selected **{len(selected)}** member(s) from "
                f"{self.browser.role.mention}.\n\n"
                "Bulk tools intentionally support reminders and role changes only. "
                "Kick, ban, and timeout remain individual confirmed actions."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Selected",
            value=trim(
                "\n".join(
                    f"• {display_name(member)} (`{member.id}`)"
                    for member in selected
                ),
                1024,
            ),
            inline=False,
        )
        await interaction.response.edit_message(embed=embed, view=view)


class BulkSelectView(OwnedView):
    def __init__(self, browser: "RoleMemberBrowserView") -> None:
        self.browser = browser
        super().__init__(browser.owner_id)
        self.add_item(BulkMemberSelect(browser))

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
        self.browser.rebuild()
        await interaction.response.edit_message(
            embed=self.browser.render_embed(),
            view=self.browser,
        )


class BulkActionView(OwnedView):
    def __init__(
        self,
        browser: "RoleMemberBrowserView",
        members: list[discord.Member],
    ) -> None:
        self.browser = browser
        self.members = members
        super().__init__(browser.owner_id)

    @discord.ui.button(
        label="Send Reminder",
        emoji="💬",
        style=discord.ButtonStyle.primary,
        row=0,
    )
    async def reminder(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await interaction.response.send_modal(BulkReminderModal(self))

    @discord.ui.button(
        label="Add Role",
        emoji="➕",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def add_role(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="➕ Bulk Add Role",
                description=(
                    f"Choose one role to add to **{len(self.members)}** selected "
                    "members. Protected-role rules and hierarchy are checked for "
                    "every member."
                ),
                color=discord.Color.blurple(),
            ),
            view=BulkRoleActionView(self, action="add_role"),
        )

    @discord.ui.button(
        label="Remove Role",
        emoji="➖",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def remove_role(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="➖ Bulk Remove Role",
                description=(
                    f"Choose one role to remove from **{len(self.members)}** selected "
                    "members. Protected-role rules and hierarchy are checked for "
                    "every member."
                ),
                color=discord.Color.blurple(),
            ),
            view=BulkRoleActionView(self, action="remove_role"),
        )

    @discord.ui.button(
        label="Back to Roster",
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
        self.browser.rebuild()
        await interaction.response.edit_message(
            embed=self.browser.render_embed(),
            view=self.browser,
        )


class BulkReminderModal(discord.ui.Modal, title="Send bulk reminder"):
    message = discord.ui.TextInput(
        label="Private message",
        style=discord.TextStyle.paragraph,
        max_length=1500,
        placeholder="Please complete verification or contact staff if you need help.",
    )

    def __init__(self, parent: BulkActionView) -> None:
        super().__init__(timeout=300)
        self.parent = parent

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await require_review(interaction):
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        sent = 0
        failed = 0
        text = str(self.message.value or "").strip()
        for member in self.parent.members:
            async with action_lock(
                interaction.guild.id,
                member.id,
                "bulk_dm",
            ):
                try:
                    await member.send(text)
                    sent += 1
                    await record_member_action(
                        guild_id=interaction.guild.id,
                        actor_id=interaction.user.id,
                        target_id=member.id,
                        action="bulk_dm",
                        reason="Staff reminder sent from role browser",
                    )
                except Exception:
                    failed += 1
        await interaction.followup.send(
            f"✅ Reminder delivery finished: **{sent} sent**, "
            f"**{failed} failed/blocked**.",
            ephemeral=True,
        )


class BulkRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent: "BulkRoleActionView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Choose a role…",
            min_values=1,
            max_values=1,
            custom_id=f"dank_members_browser:bulk_{parent.action}",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0] if self.values else None
        if not isinstance(role, discord.Role):
            await reply_ephemeral(
                interaction,
                "❌ Discord did not return a valid role.",
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        actor = interaction.user
        protected_role_ids = await load_protected_role_ids(
            int(interaction.guild.id)
        )
        succeeded = 0
        blocked = 0
        failed = 0
        for target in self.parent_view.parent.members:
            fresh_target = interaction.guild.get_member(int(target.id))
            if not isinstance(fresh_target, discord.Member):
                blocked += 1
                continue
            blockers = await role_action_blockers(
                interaction.guild,
                actor,
                fresh_target,
                role,
                self.parent_view.action,
                protected_role_ids=protected_role_ids,
            )
            if blockers:
                blocked += 1
                continue
            async with action_lock(
                interaction.guild.id,
                fresh_target.id,
                f"bulk_{self.parent_view.action}",
            ):
                try:
                    audit_reason = (
                        f"Dank Shield bulk role action by {actor} ({actor.id})"
                    )
                    if self.parent_view.action == "add_role":
                        if role not in fresh_target.roles:
                            await fresh_target.add_roles(
                                role,
                                reason=audit_reason,
                            )
                    else:
                        if role in fresh_target.roles:
                            await fresh_target.remove_roles(
                                role,
                                reason=audit_reason,
                            )
                    succeeded += 1
                    await record_member_action(
                        guild_id=interaction.guild.id,
                        actor_id=actor.id,
                        target_id=fresh_target.id,
                        action=f"bulk_{self.parent_view.action}",
                        reason=f"{role.name} ({role.id})",
                        metadata={
                            "role_id": str(role.id),
                            "role_name": role.name,
                        },
                    )
                except Exception:
                    failed += 1
        await interaction.followup.send(
            f"✅ Bulk role action finished: **{succeeded} succeeded**, "
            f"**{blocked} blocked by safety checks**, **{failed} failed**.",
            ephemeral=True,
        )


class BulkRoleActionView(OwnedView):
    def __init__(self, parent: BulkActionView, *, action: str) -> None:
        self.parent = parent
        self.action = action
        super().__init__(parent.owner_id)
        self.add_item(BulkRoleSelect(self))

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
        embed = discord.Embed(
            title="🧰 Safe Bulk Actions",
            description=f"Selected **{len(self.parent.members)}** member(s).",
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=self.parent)


__all__ = ["BulkSelectView"]
