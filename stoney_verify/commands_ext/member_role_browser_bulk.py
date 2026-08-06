from __future__ import annotations

import asyncio
import weakref
from datetime import timedelta
from typing import Optional

import discord

from .member_role_browser_common import (
    OwnedView,
    action_blockers,
    action_lock,
    apply_staff_basic_verification,
    display_name,
    load_protected_role_ids,
    record_member_action,
    reply_ephemeral,
    require_review,
    role_action_blockers,
    trim,
)


_BULK_OPERATION_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = (
    weakref.WeakValueDictionary()
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


def bulk_confirmation_phrase(action: str, count: int) -> str:
    label = {
        "verify": "VERIFY",
        "timeout": "TIMEOUT",
        "clear_timeout": "CLEAR",
        "kick": "KICK",
        "ban": "BAN",
    }.get(str(action or "").strip().lower(), str(action or "ACTION").upper())
    return f"{label} {max(0, int(count))}"


def _confirmation_matches(value: object, action: str, count: int) -> bool:
    entered = " ".join(str(value or "").strip().upper().split())
    return entered == bulk_confirmation_phrase(action, count)


def _bulk_operation_lock(
    guild_id: int,
    actor_id: int,
    action: str,
    member_ids: list[int],
) -> asyncio.Lock:
    targets = ",".join(str(int(member_id)) for member_id in sorted(set(member_ids)))
    key = f"{int(guild_id)}:{int(actor_id)}:{str(action)}:{targets}"
    lock = _BULK_OPERATION_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _BULK_OPERATION_LOCKS[key] = lock
    return lock


async def _fresh_member(
    guild: discord.Guild,
    member_id: int,
) -> Optional[discord.Member]:
    member = guild.get_member(int(member_id))
    if isinstance(member, discord.Member):
        return member
    try:
        fetched = await guild.fetch_member(int(member_id))
        return fetched if isinstance(fetched, discord.Member) else None
    except Exception:
        return None


def _result_embed(
    *,
    action_label: str,
    succeeded: int,
    blocked: int,
    failed: int,
    details: list[str],
) -> discord.Embed:
    color = (
        discord.Color.green()
        if failed == 0 and blocked == 0
        else discord.Color.orange()
    )
    embed = discord.Embed(
        title=f"🧰 Bulk {action_label} Finished",
        description=(
            f"**{succeeded} succeeded** • **{blocked} blocked** • "
            f"**{failed} failed**"
        ),
        color=color,
        timestamp=discord.utils.utcnow(),
    )
    if details:
        embed.add_field(
            name="Results",
            value=trim("\n".join(details[:18]), 1024),
            inline=False,
        )
    embed.set_footer(
        text="Every target was re-fetched and permission-checked at execution time."
    )
    return embed


async def _record_blocked_action(
    *,
    guild_id: int,
    actor_id: int,
    target_id: int,
    action: str,
    blockers: list[str],
) -> None:
    await record_member_action(
        guild_id=guild_id,
        actor_id=actor_id,
        target_id=target_id,
        action=f"bulk_{action}",
        reason="Blocked: " + "; ".join(blockers),
        metadata={
            "ok": False,
            "blocked": True,
            "blockers": blockers[:8],
        },
    )


async def _execute_bulk_member_action(
    parent: "BulkActionView",
    interaction: discord.Interaction,
    *,
    action: str,
    reason: str,
    minutes: Optional[int] = None,
) -> None:
    guild = interaction.guild
    actor = interaction.user
    if not isinstance(guild, discord.Guild) or not isinstance(actor, discord.Member):
        await interaction.followup.send(
            "❌ Bulk moderation must be run inside a server by a current member.",
            ephemeral=True,
        )
        return

    member_ids = [int(member.id) for member in parent.members]
    operation_lock = _bulk_operation_lock(
        guild.id,
        actor.id,
        action,
        member_ids,
    )
    if operation_lock.locked():
        await interaction.followup.send(
            "⏳ This exact bulk operation is already running. Nothing was started twice.",
            ephemeral=True,
        )
        return

    succeeded = 0
    blocked = 0
    failed = 0
    details: list[str] = []
    protected_role_ids = await load_protected_role_ids(int(guild.id))
    guard_action = "timeout" if action == "clear_timeout" else action

    async with operation_lock:
        for original in parent.members:
            original_id = int(original.id)
            target = await _fresh_member(guild, original_id)
            if target is None:
                failed += 1
                details.append(f"❌ `{original_id}` is no longer in the server.")
                await record_member_action(
                    guild_id=guild.id,
                    actor_id=actor.id,
                    target_id=original_id,
                    action=f"bulk_{action}",
                    reason="Target was no longer available at execution time",
                    metadata={"ok": False, "missing": True},
                )
                continue

            target_label = display_name(target)
            async with action_lock(guild.id, target.id, f"bulk_{action}"):
                target = await _fresh_member(guild, original_id)
                if target is None:
                    failed += 1
                    details.append(f"❌ `{original_id}` left before execution.")
                    continue

                blockers = await action_blockers(
                    guild,
                    actor,
                    target,
                    guard_action,
                    protected_role_ids=protected_role_ids,
                )
                if blockers:
                    blocked += 1
                    details.append(
                        f"⛔ {target_label}: {trim('; '.join(blockers), 150)}"
                    )
                    await _record_blocked_action(
                        guild_id=guild.id,
                        actor_id=actor.id,
                        target_id=target.id,
                        action=action,
                        blockers=blockers,
                    )
                    continue

                ok = False
                result_message = ""
                metadata: dict[str, object] = {"ok": False}
                audit_reason = trim(
                    f"{reason} | Dank Shield bulk {action} by {actor} ({actor.id})",
                    500,
                )
                try:
                    if action == "verify":
                        ok, result_message = await apply_staff_basic_verification(
                            guild,
                            target,
                        )
                    elif action == "timeout":
                        if minutes is None:
                            raise ValueError("Timeout duration was not provided")
                        until = discord.utils.utcnow() + timedelta(minutes=int(minutes))
                        await target.timeout(until, reason=audit_reason)
                        ok = True
                        result_message = f"timed out for {int(minutes)} minute(s)"
                        metadata["minutes"] = int(minutes)
                    elif action == "clear_timeout":
                        if target.is_timed_out():
                            await target.timeout(None, reason=audit_reason)
                            result_message = "timeout removed"
                            metadata["changed"] = True
                        else:
                            result_message = "already had no active timeout"
                            metadata["changed"] = False
                        ok = True
                    elif action == "kick":
                        await target.kick(reason=audit_reason)
                        ok = True
                        result_message = "kicked"
                    elif action == "ban":
                        await guild.ban(
                            target,
                            reason=audit_reason,
                            delete_message_seconds=0,
                        )
                        ok = True
                        result_message = "banned"
                    else:
                        raise ValueError(f"Unsupported bulk action: {action}")
                except discord.Forbidden:
                    result_message = (
                        "Discord blocked the action; check Dank Shield permissions "
                        "and role hierarchy"
                    )
                except discord.HTTPException as exc:
                    result_message = f"Discord API failed: {type(exc).__name__}"
                except Exception as exc:
                    result_message = f"Action failed: {type(exc).__name__}"

                metadata["ok"] = bool(ok)
                await record_member_action(
                    guild_id=guild.id,
                    actor_id=actor.id,
                    target_id=target.id,
                    action=f"bulk_{action}",
                    reason=reason if reason else result_message,
                    metadata=metadata,
                )

                if ok:
                    succeeded += 1
                    details.append(f"✅ {target_label}: {result_message}")
                else:
                    failed += 1
                    details.append(f"❌ {target_label}: {result_message}")

    await interaction.followup.send(
        embed=_result_embed(
            action_label=action.replace("_", " ").title(),
            succeeded=succeeded,
            blocked=blocked,
            failed=failed,
            details=details,
        ),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


class BulkMemberSelect(discord.ui.Select):
    def __init__(self, browser: "RoleMemberBrowserView") -> None:
        members = browser.page_members()[:25]
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
            placeholder="Select members for bulk actions…",
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
            title="🧰 Confirmed Bulk Moderation",
            description=(
                f"Selected **{len(selected)}** member(s) from "
                f"{self.browser.role.mention}.\n\n"
                "Every action re-checks Discord permissions, role hierarchy, "
                "protected roles, and target availability. Verify, timeout, clear "
                "timeout, kick, and ban require an explicit confirmation phrase."
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
        embed.add_field(
            name="Destructive confirmation",
            value=(
                f"Kick requires `{bulk_confirmation_phrase('kick', len(selected))}` • "
                f"Ban requires `{bulk_confirmation_phrase('ban', len(selected))}`."
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
        label="Verify",
        emoji="✅",
        style=discord.ButtonStyle.success,
        row=0,
    )
    async def verify(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await interaction.response.send_modal(BulkVerifyModal(self))

    @discord.ui.button(
        label="Timeout",
        emoji="⏱️",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def timeout_members(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await interaction.response.send_modal(BulkTimeoutModal(self))

    @discord.ui.button(
        label="Clear Timeout",
        emoji="▶️",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def clear_timeout(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await interaction.response.send_modal(BulkClearTimeoutModal(self))

    @discord.ui.button(
        label="Kick",
        emoji="👢",
        style=discord.ButtonStyle.danger,
        row=1,
    )
    async def kick(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await interaction.response.send_modal(
            BulkDestructiveActionModal(self, action="kick")
        )

    @discord.ui.button(
        label="Ban",
        emoji="🔨",
        style=discord.ButtonStyle.danger,
        row=1,
    )
    async def ban(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await interaction.response.send_modal(
            BulkDestructiveActionModal(self, action="ban")
        )

    @discord.ui.button(
        label="Send Reminder",
        emoji="💬",
        style=discord.ButtonStyle.primary,
        row=2,
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
        row=2,
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
                    "every member at execution time."
                ),
                color=discord.Color.blurple(),
            ),
            view=BulkRoleActionView(self, action="add_role"),
        )

    @discord.ui.button(
        label="Remove Role",
        emoji="➖",
        style=discord.ButtonStyle.secondary,
        row=2,
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
                    "every member at execution time."
                ),
                color=discord.Color.blurple(),
            ),
            view=BulkRoleActionView(self, action="remove_role"),
        )

    @discord.ui.button(
        label="Back to Roster",
        emoji="◀️",
        style=discord.ButtonStyle.secondary,
        row=3,
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


class BulkVerifyModal(discord.ui.Modal):
    def __init__(self, parent: BulkActionView) -> None:
        self.parent = parent
        self.confirmation = discord.ui.TextInput(
            label="Type the exact confirmation",
            placeholder=bulk_confirmation_phrase("verify", len(parent.members)),
            max_length=20,
        )
        super().__init__(title="Confirm Bulk Verify", timeout=300)
        self.add_item(self.confirmation)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await require_review(interaction):
            return
        if not _confirmation_matches(
            self.confirmation.value,
            "verify",
            len(self.parent.members),
        ):
            await reply_ephemeral(
                interaction,
                f"❌ Type `{bulk_confirmation_phrase('verify', len(self.parent.members))}` exactly. Nothing changed.",
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _execute_bulk_member_action(
            self.parent,
            interaction,
            action="verify",
            reason="Confirmed bulk verification from member browser",
        )


class BulkTimeoutModal(discord.ui.Modal):
    def __init__(self, parent: BulkActionView) -> None:
        self.parent = parent
        self.minutes = discord.ui.TextInput(
            label="Duration in minutes",
            placeholder="60",
            max_length=6,
        )
        self.reason = discord.ui.TextInput(
            label="Reason",
            style=discord.TextStyle.paragraph,
            max_length=400,
            placeholder="Why are these members being timed out?",
        )
        self.confirmation = discord.ui.TextInput(
            label="Type the exact confirmation",
            placeholder=bulk_confirmation_phrase("timeout", len(parent.members)),
            max_length=20,
        )
        super().__init__(title="Confirm Bulk Timeout", timeout=300)
        self.add_item(self.minutes)
        self.add_item(self.reason)
        self.add_item(self.confirmation)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await require_review(interaction):
            return
        try:
            minutes = int(str(self.minutes.value or "").strip())
        except Exception:
            await reply_ephemeral(interaction, "❌ Duration must be a whole number of minutes.")
            return
        if minutes < 1 or minutes > 40320:
            await reply_ephemeral(
                interaction,
                "❌ Duration must be between 1 and 40,320 minutes (28 days).",
            )
            return
        if not _confirmation_matches(
            self.confirmation.value,
            "timeout",
            len(self.parent.members),
        ):
            await reply_ephemeral(
                interaction,
                f"❌ Type `{bulk_confirmation_phrase('timeout', len(self.parent.members))}` exactly. Nothing changed.",
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _execute_bulk_member_action(
            self.parent,
            interaction,
            action="timeout",
            reason=str(self.reason.value or "").strip(),
            minutes=minutes,
        )


class BulkClearTimeoutModal(discord.ui.Modal):
    def __init__(self, parent: BulkActionView) -> None:
        self.parent = parent
        self.reason = discord.ui.TextInput(
            label="Reason",
            style=discord.TextStyle.paragraph,
            max_length=400,
            placeholder="Why are these timeouts being removed?",
        )
        self.confirmation = discord.ui.TextInput(
            label="Type the exact confirmation",
            placeholder=bulk_confirmation_phrase("clear_timeout", len(parent.members)),
            max_length=20,
        )
        super().__init__(title="Confirm Clear Timeouts", timeout=300)
        self.add_item(self.reason)
        self.add_item(self.confirmation)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await require_review(interaction):
            return
        if not _confirmation_matches(
            self.confirmation.value,
            "clear_timeout",
            len(self.parent.members),
        ):
            await reply_ephemeral(
                interaction,
                f"❌ Type `{bulk_confirmation_phrase('clear_timeout', len(self.parent.members))}` exactly. Nothing changed.",
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _execute_bulk_member_action(
            self.parent,
            interaction,
            action="clear_timeout",
            reason=str(self.reason.value or "").strip(),
        )


class BulkDestructiveActionModal(discord.ui.Modal):
    def __init__(self, parent: BulkActionView, *, action: str) -> None:
        self.parent = parent
        self.action = action
        self.reason = discord.ui.TextInput(
            label="Required moderation reason",
            style=discord.TextStyle.paragraph,
            max_length=400,
            placeholder=f"Why should these members be {action}ed?",
        )
        self.confirmation = discord.ui.TextInput(
            label="Type the exact confirmation",
            placeholder=bulk_confirmation_phrase(action, len(parent.members)),
            max_length=20,
        )
        super().__init__(title=f"Confirm Bulk {action.title()}", timeout=300)
        self.add_item(self.reason)
        self.add_item(self.confirmation)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await require_review(interaction):
            return
        if not _confirmation_matches(
            self.confirmation.value,
            self.action,
            len(self.parent.members),
        ):
            await reply_ephemeral(
                interaction,
                f"❌ Type `{bulk_confirmation_phrase(self.action, len(self.parent.members))}` exactly. Nothing changed.",
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _execute_bulk_member_action(
            self.parent,
            interaction,
            action=self.action,
            reason=str(self.reason.value or "").strip(),
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
        guild = interaction.guild
        actor = interaction.user
        if not isinstance(guild, discord.Guild) or not isinstance(actor, discord.Member):
            await interaction.followup.send("❌ This must be used inside a server.", ephemeral=True)
            return

        member_ids = [int(member.id) for member in self.parent.members]
        operation_lock = _bulk_operation_lock(
            guild.id,
            actor.id,
            "dm",
            member_ids,
        )
        if operation_lock.locked():
            await interaction.followup.send(
                "⏳ This exact reminder operation is already running.",
                ephemeral=True,
            )
            return

        sent = 0
        failed = 0
        details: list[str] = []
        text = str(self.message.value or "").strip()
        async with operation_lock:
            for original in self.parent.members:
                member = await _fresh_member(guild, int(original.id))
                if member is None:
                    failed += 1
                    details.append(f"❌ `{original.id}` is no longer available.")
                    continue
                async with action_lock(guild.id, member.id, "bulk_dm"):
                    try:
                        await member.send(text)
                        sent += 1
                        details.append(f"✅ {display_name(member)}: reminder sent")
                        ok = True
                    except Exception:
                        failed += 1
                        details.append(f"❌ {display_name(member)}: DMs blocked or failed")
                        ok = False
                    await record_member_action(
                        guild_id=guild.id,
                        actor_id=actor.id,
                        target_id=member.id,
                        action="bulk_dm",
                        reason="Staff reminder sent from role browser",
                        metadata={"ok": ok},
                    )
        await interaction.followup.send(
            embed=_result_embed(
                action_label="Reminder",
                succeeded=sent,
                blocked=0,
                failed=failed,
                details=details,
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
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
        guild = interaction.guild
        actor = interaction.user
        if not isinstance(guild, discord.Guild) or not isinstance(actor, discord.Member):
            await interaction.followup.send("❌ This must be used inside a server.", ephemeral=True)
            return

        members = self.parent_view.parent.members
        member_ids = [int(member.id) for member in members]
        operation_lock = _bulk_operation_lock(
            guild.id,
            actor.id,
            self.parent_view.action,
            member_ids,
        )
        if operation_lock.locked():
            await interaction.followup.send(
                "⏳ This exact bulk role operation is already running.",
                ephemeral=True,
            )
            return

        protected_role_ids = await load_protected_role_ids(int(guild.id))
        succeeded = 0
        blocked = 0
        failed = 0
        details: list[str] = []

        async with operation_lock:
            for original in members:
                fresh_target = await _fresh_member(guild, int(original.id))
                if not isinstance(fresh_target, discord.Member):
                    failed += 1
                    details.append(f"❌ `{original.id}` is no longer available.")
                    continue
                target_label = display_name(fresh_target)
                async with action_lock(
                    guild.id,
                    fresh_target.id,
                    f"bulk_{self.parent_view.action}",
                ):
                    fresh_target = await _fresh_member(guild, int(original.id))
                    if not isinstance(fresh_target, discord.Member):
                        failed += 1
                        details.append(f"❌ `{original.id}` left before execution.")
                        continue
                    blockers = await role_action_blockers(
                        guild,
                        actor,
                        fresh_target,
                        role,
                        self.parent_view.action,
                        protected_role_ids=protected_role_ids,
                    )
                    if blockers:
                        blocked += 1
                        details.append(
                            f"⛔ {target_label}: {trim('; '.join(blockers), 150)}"
                        )
                        await _record_blocked_action(
                            guild_id=guild.id,
                            actor_id=actor.id,
                            target_id=fresh_target.id,
                            action=self.parent_view.action,
                            blockers=blockers,
                        )
                        continue

                    try:
                        audit_reason = (
                            f"Dank Shield bulk role action by {actor} ({actor.id})"
                        )
                        changed = False
                        if self.parent_view.action == "add_role":
                            if role not in fresh_target.roles:
                                await fresh_target.add_roles(
                                    role,
                                    reason=audit_reason,
                                )
                                changed = True
                        else:
                            if role in fresh_target.roles:
                                await fresh_target.remove_roles(
                                    role,
                                    reason=audit_reason,
                                )
                                changed = True
                        succeeded += 1
                        details.append(
                            f"✅ {target_label}: {'updated' if changed else 'already correct'}"
                        )
                        await record_member_action(
                            guild_id=guild.id,
                            actor_id=actor.id,
                            target_id=fresh_target.id,
                            action=f"bulk_{self.parent_view.action}",
                            reason=f"{role.name} ({role.id})",
                            metadata={
                                "ok": True,
                                "changed": changed,
                                "role_id": str(role.id),
                                "role_name": role.name,
                            },
                        )
                    except Exception as exc:
                        failed += 1
                        details.append(
                            f"❌ {target_label}: {type(exc).__name__}"
                        )
                        await record_member_action(
                            guild_id=guild.id,
                            actor_id=actor.id,
                            target_id=fresh_target.id,
                            action=f"bulk_{self.parent_view.action}",
                            reason=f"{role.name} ({role.id})",
                            metadata={
                                "ok": False,
                                "error": type(exc).__name__,
                                "role_id": str(role.id),
                                "role_name": role.name,
                            },
                        )

        await interaction.followup.send(
            embed=_result_embed(
                action_label=self.parent_view.action.replace("_", " ").title(),
                succeeded=succeeded,
                blocked=blocked,
                failed=failed,
                details=details,
            ),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
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
            title="🧰 Confirmed Bulk Moderation",
            description=f"Selected **{len(self.parent.members)}** member(s).",
            color=discord.Color.blurple(),
        )
        await interaction.response.edit_message(embed=embed, view=self.parent)


__all__ = [
    "BulkSelectView",
    "bulk_confirmation_phrase",
]
