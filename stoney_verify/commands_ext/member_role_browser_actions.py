from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any, Optional

import discord

from .member_role_browser_common import (
    OwnedView,
    action_blockers,
    action_lock,
    apply_staff_basic_verification,
    display_name,
    record_member_action,
    reply_ephemeral,
    require_review,
    role_action_blockers,
    timestamp,
    trim,
)
from .member_role_browser_review import open_review_panel


_DESTRUCTIVE_PRECHECK_TIMEOUT_SECONDS = 8.0
_DESTRUCTIVE_DISCORD_TIMEOUT_SECONDS = 15.0
_MEMBER_CARD_ROLE_LIMIT = 12

# Timeout presets shown in the modal (label, minutes). Discord's maximum is 28 days.
TIMEOUT_PRESETS: tuple[tuple[str, int], ...] = (
    ("10 minutes", 10),
    ("1 hour", 60),
    ("6 hours", 360),
    ("1 day", 1440),
    ("1 week", 10080),
)
TIMEOUT_DEFAULT_MINUTES = 60
TIMEOUT_MAX_MINUTES = 40320


def resolve_timeout_minutes(preset: str, custom: str) -> tuple[Optional[int], str]:
    """Pick the timeout length: exact minutes win over the preset when given.

    Returns ``(minutes, "")`` or ``(None, error message)``.
    """

    raw = str(custom or "").strip()
    if raw:
        try:
            minutes = int(raw)
        except ValueError:
            return None, "❌ Exact minutes must be a whole number."
    else:
        try:
            minutes = int(str(preset or TIMEOUT_DEFAULT_MINUTES).strip())
        except ValueError:
            minutes = TIMEOUT_DEFAULT_MINUTES
    if minutes < 1 or minutes > TIMEOUT_MAX_MINUTES:
        return None, "❌ Duration must be between 1 and 40,320 minutes (28 days)."
    return minutes, ""


def describe_minutes(minutes: int) -> str:
    for label, value in TIMEOUT_PRESETS:
        if value == minutes:
            return label
    return f"{minutes} minute(s)"


def member_detail_embed(
    member: discord.Member,
    selected_role: Optional[discord.Role],
) -> discord.Embed:
    embed = discord.Embed(
        title=f"👤 {trim(display_name(member), 200)}",
        description=f"{member.mention} · `{member.id}`" + (" · 🤖 Bot" if member.bot else ""),
        color=member.color if member.color.value else discord.Color.blurple(),
    )
    try:
        embed.set_thumbnail(url=member.display_avatar.url)
    except Exception:
        pass

    # Three short side-by-side facts instead of a paragraph of status lines.
    embed.add_field(name="Joined", value=timestamp(member.joined_at), inline=True)
    embed.add_field(name="Account made", value=timestamp(member.created_at), inline=True)
    if member.is_timed_out():
        embed.add_field(
            name="Status",
            value=f"⏱️ Timed out until {timestamp(member.timed_out_until, 'f')}",
            inline=True,
        )
    else:
        embed.add_field(name="Status", value="Active", inline=True)

    roles = [
        role.mention
        for role in reversed(member.roles)
        if not role.is_default()
    ]
    shown = roles[:_MEMBER_CARD_ROLE_LIMIT]
    hidden = len(roles) - len(shown)
    embed.add_field(
        name=f"Roles ({len(roles)})",
        value=trim(
            (" ".join(shown) + (f" +{hidden} more" if hidden > 0 else ""))
            if roles
            else "No assigned roles.",
            1024,
        ),
        inline=False,
    )
    if selected_role is not None:
        embed.add_field(
            name="Browser context",
            value=f"Selected from {selected_role.mention}.",
            inline=False,
        )
    embed.set_footer(
        text=(
            "Every destructive action re-checks permissions and role hierarchy "
            "at click time."
        )
    )
    return embed


class MemberActionView(OwnedView):
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

    def _member(self, guild: discord.Guild) -> Optional[discord.Member]:
        member = guild.get_member(self.member_id)
        return member if isinstance(member, discord.Member) else None

    async def _fresh_target(
        self,
        interaction: discord.Interaction,
    ) -> Optional[discord.Member]:
        target = self._member(interaction.guild)
        if target is not None:
            return target
        try:
            fetched = await interaction.guild.fetch_member(self.member_id)
            return fetched if isinstance(fetched, discord.Member) else None
        except Exception:
            await reply_ephemeral(
                interaction,
                "❌ That member is no longer in the server.",
            )
            return None

    @discord.ui.button(
        label="Verify",
        emoji="✅",
        style=discord.ButtonStyle.success,
        row=1,
    )
    async def verify(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        target = await self._fresh_target(interaction)
        if target is None:
            return
        blockers = await action_blockers(
            interaction.guild,
            interaction.user,
            target,
            "verify",
        )
        if blockers:
            await reply_ephemeral(
                interaction,
                "❌ Verification blocked:\n• " + "\n• ".join(blockers),
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with action_lock(interaction.guild.id, target.id, "verify"):
            ok, message = await apply_staff_basic_verification(
                interaction.guild,
                target,
            )
            await record_member_action(
                guild_id=interaction.guild.id,
                actor_id=interaction.user.id,
                target_id=target.id,
                action="verify",
                reason=message,
                metadata={"ok": bool(ok)},
            )
        await interaction.followup.send(
            ("✅ " if ok else "❌ ") + message,
            ephemeral=True,
        )

    @discord.ui.button(
        label="Review history",
        emoji="🧠",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def review(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        target = await self._fresh_target(interaction)
        if target is not None:
            await open_review_panel(interaction, target)

    @discord.ui.button(
        label="Message",
        emoji="💬",
        style=discord.ButtonStyle.secondary,
        row=1,
    )
    async def message(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await interaction.response.send_modal(MemberMessageModal(self))

    @discord.ui.button(
        label="Timeout",
        emoji="⏱️",
        style=discord.ButtonStyle.secondary,
        row=0,
    )
    async def timeout_member(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await interaction.response.send_modal(MemberTimeoutModal(self))

    @discord.ui.button(
        label="Kick",
        emoji="👢",
        style=discord.ButtonStyle.danger,
        row=0,
    )
    async def kick(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await interaction.response.send_modal(
            MemberDestructiveActionModal(self, action="kick")
        )

    @discord.ui.button(
        label="Ban",
        emoji="🔨",
        style=discord.ButtonStyle.danger,
        row=0,
    )
    async def ban(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        await interaction.response.send_modal(
            MemberDestructiveActionModal(self, action="ban")
        )

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
                title="➕ Add Role",
                description=(
                    "Choose one role to add. Permissions, protected-role rules, "
                    "and hierarchy will be re-checked."
                ),
                color=discord.Color.blurple(),
            ),
            view=MemberRoleActionView(self, action="add_role"),
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
                title="➖ Remove Role",
                description=(
                    "Choose one role to remove. Permissions, protected-role rules, "
                    "and hierarchy will be re-checked."
                ),
                color=discord.Color.blurple(),
            ),
            view=MemberRoleActionView(self, action="remove_role"),
        )

    @discord.ui.button(
        label="Refresh",
        emoji="🔄",
        style=discord.ButtonStyle.secondary,
        row=3,
    )
    async def refresh(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        _ = button
        target = await self._fresh_target(interaction)
        if target is not None:
            await interaction.response.edit_message(
                embed=member_detail_embed(target, self.browser.role),
                view=self,
            )

    @discord.ui.button(
        label="Back",
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


class MemberRoleSelect(discord.ui.RoleSelect):
    def __init__(self, parent: "MemberRoleActionView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Choose a role…",
            min_values=1,
            max_values=1,
            custom_id=f"dank_members_browser:{parent.action}",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        role = self.values[0] if self.values else None
        target = await self.parent_view.parent._fresh_target(interaction)
        if not isinstance(role, discord.Role) or target is None:
            return
        blockers = await role_action_blockers(
            interaction.guild,
            interaction.user,
            target,
            role,
            self.parent_view.action,
        )
        if blockers:
            await reply_ephemeral(
                interaction,
                "❌ Role action blocked:\n• " + "\n• ".join(blockers),
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        action_label = (
            "added" if self.parent_view.action == "add_role" else "removed"
        )
        async with action_lock(
            interaction.guild.id,
            target.id,
            self.parent_view.action,
        ):
            try:
                audit_reason = (
                    f"Dank Shield member browser by {interaction.user} "
                    f"({interaction.user.id})"
                )
                if self.parent_view.action == "add_role":
                    if role not in target.roles:
                        await target.add_roles(role, reason=audit_reason)
                else:
                    if role in target.roles:
                        await target.remove_roles(role, reason=audit_reason)
                ok = True
                message = (
                    f"{role.mention} was {action_label} for {target.mention}."
                )
            except discord.Forbidden:
                ok = False
                message = (
                    "Discord blocked the role update. Check Manage Roles and "
                    "role hierarchy."
                )
            except Exception as exc:
                ok = False
                message = f"Role update failed: {type(exc).__name__}."
            await record_member_action(
                guild_id=interaction.guild.id,
                actor_id=interaction.user.id,
                target_id=target.id,
                action=self.parent_view.action,
                reason=f"{role.name} ({role.id})",
                metadata={
                    "ok": ok,
                    "role_id": str(role.id),
                    "role_name": role.name,
                },
            )
        await interaction.followup.send(
            ("✅ " if ok else "❌ ") + message,
            ephemeral=True,
        )


class MemberRoleActionView(OwnedView):
    def __init__(self, parent: MemberActionView, *, action: str) -> None:
        self.parent = parent
        self.action = action
        super().__init__(parent.owner_id)
        self.add_item(MemberRoleSelect(self))

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
                embed=member_detail_embed(target, self.parent.browser.role),
                view=self.parent,
            )


class MemberMessageModal(discord.ui.Modal, title="Message member"):
    message = discord.ui.TextInput(
        label="Private message",
        style=discord.TextStyle.paragraph,
        max_length=1500,
        placeholder="Please complete verification or contact staff if you need help.",
    )

    def __init__(self, parent: MemberActionView) -> None:
        super().__init__(timeout=300)
        self.parent = parent

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await require_review(interaction):
            return
        target = await self.parent._fresh_target(interaction)
        if target is None:
            return
        blockers = await action_blockers(
            interaction.guild,
            interaction.user,
            target,
            "dm",
        )
        if blockers:
            await reply_ephemeral(
                interaction,
                "❌ Message blocked:\n• " + "\n• ".join(blockers),
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        text = str(self.message.value or "").strip()
        try:
            await target.send(text)
            ok = True
            result = f"Private message sent to {target.mention}."
        except discord.Forbidden:
            ok = False
            result = "That member has DMs disabled or blocked the bot."
        except Exception as exc:
            ok = False
            result = f"Message failed: {type(exc).__name__}."
        await record_member_action(
            guild_id=interaction.guild.id,
            actor_id=interaction.user.id,
            target_id=target.id,
            action="dm",
            reason="Staff message sent from role browser" if ok else result,
            metadata={"ok": ok},
        )
        await interaction.followup.send(
            ("✅ " if ok else "❌ ") + result,
            ephemeral=True,
        )


def _timeout_duration_select() -> discord.ui.Select:
    return discord.ui.Select(
        options=[
            discord.SelectOption(
                label=label,
                value=str(minutes),
                default=minutes == TIMEOUT_DEFAULT_MINUTES,
            )
            for label, minutes in TIMEOUT_PRESETS
        ],
        min_values=1,
        max_values=1,
    )


class MemberTimeoutModal(discord.ui.Modal, title="Timeout member"):
    """One step: pick a length, give a reason. Exact minutes stay available."""

    duration = discord.ui.Label(
        text="How long",
        description="They can still read, but can't chat or join voice.",
        component=_timeout_duration_select(),
    )
    exact_minutes = discord.ui.Label(
        text="Or exact minutes (optional)",
        component=discord.ui.TextInput(
            placeholder="Leave empty to use the length above",
            max_length=6,
            required=False,
        ),
    )
    reason = discord.ui.Label(
        text="Reason",
        description="Saved to the moderation log.",
        component=discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            max_length=400,
            placeholder="Why is this timeout needed?",
        ),
    )

    def __init__(self, parent: MemberActionView) -> None:
        super().__init__(timeout=300)
        self.parent = parent

    def _chosen_minutes(self) -> tuple[Optional[int], str]:
        select = self.duration.component
        values = list(getattr(select, "values", []) or [])
        preset = values[0] if values else str(TIMEOUT_DEFAULT_MINUTES)
        custom = str(getattr(self.exact_minutes.component, "value", "") or "")
        return resolve_timeout_minutes(preset, custom)

    def _reason_text(self) -> str:
        return str(getattr(self.reason.component, "value", "") or "").strip()

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await require_review(interaction):
            return
        target = await self.parent._fresh_target(interaction)
        if target is None:
            return
        minutes, error = self._chosen_minutes()
        if minutes is None:
            await reply_ephemeral(interaction, error)
            return
        blockers = await action_blockers(
            interaction.guild,
            interaction.user,
            target,
            "timeout",
        )
        if blockers:
            await reply_ephemeral(
                interaction,
                "❌ Timeout blocked:\n• " + "\n• ".join(blockers),
            )
            return
        reason = self._reason_text()
        await interaction.response.defer(ephemeral=True, thinking=True)
        async with action_lock(interaction.guild.id, target.id, "timeout"):
            try:
                until = discord.utils.utcnow() + timedelta(minutes=minutes)
                await target.timeout(
                    until,
                    reason=(
                        f"{reason} | By {interaction.user} "
                        f"({interaction.user.id})"
                    ),
                )
                ok = True
                result = (
                    f"{target.mention} was timed out for "
                    f"**{describe_minutes(minutes)}**."
                )
            except discord.Forbidden:
                ok = False
                result = (
                    "Discord blocked the timeout. Check Moderate Members and "
                    "role hierarchy."
                )
            except Exception as exc:
                ok = False
                result = f"Timeout failed: {type(exc).__name__}."
            await record_member_action(
                guild_id=interaction.guild.id,
                actor_id=interaction.user.id,
                target_id=target.id,
                action="timeout",
                reason=reason,
                metadata={"ok": ok, "minutes": minutes},
            )
        await interaction.followup.send(
            ("✅ " if ok else "❌ ") + result,
            ephemeral=True,
        )


class MemberDestructiveActionModal(discord.ui.Modal):
    reason = discord.ui.TextInput(
        label="Reason",
        style=discord.TextStyle.paragraph,
        max_length=400,
        placeholder="Required moderation reason",
    )
    confirmation = discord.ui.TextInput(
        label="Type the action to confirm",
        placeholder="KICK or BAN",
        max_length=4,
    )

    def __init__(self, parent: MemberActionView, *, action: str) -> None:
        self.parent = parent
        self.action = action
        super().__init__(title=f"Confirm {action.title()}", timeout=300)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)

        if not await require_review(interaction):
            return

        try:
            target = await asyncio.wait_for(
                self.parent._fresh_target(interaction),
                timeout=_DESTRUCTIVE_PRECHECK_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            await reply_ephemeral(
                interaction,
                "❌ Member refresh timed out. Nothing happened; try the action again.",
            )
            return
        if target is None:
            return

        if (
            str(self.confirmation.value or "").strip().casefold()
            != self.action.casefold()
        ):
            await reply_ephemeral(
                interaction,
                f"❌ Confirmation did not match `{self.action.upper()}`. "
                "Nothing happened.",
            )
            return

        try:
            blockers = await asyncio.wait_for(
                action_blockers(
                    interaction.guild,
                    interaction.user,
                    target,
                    self.action,
                ),
                timeout=_DESTRUCTIVE_PRECHECK_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            await reply_ephemeral(
                interaction,
                "❌ Moderation safety checks timed out. Nothing happened; try again.",
            )
            return
        if blockers:
            await reply_ephemeral(
                interaction,
                f"❌ {self.action.title()} blocked:\n• "
                + "\n• ".join(blockers),
            )
            return

        reason = str(self.reason.value or "").strip()
        audit_reason = (
            f"{reason} | By {interaction.user} ({interaction.user.id})"
        )

        async with action_lock(
            interaction.guild.id,
            target.id,
            self.action,
        ):
            try:
                if self.action == "kick":
                    await asyncio.wait_for(
                        target.kick(reason=audit_reason),
                        timeout=_DESTRUCTIVE_DISCORD_TIMEOUT_SECONDS,
                    )
                else:
                    await asyncio.wait_for(
                        interaction.guild.ban(
                            target,
                            reason=audit_reason,
                            delete_message_seconds=0,
                        ),
                        timeout=_DESTRUCTIVE_DISCORD_TIMEOUT_SECONDS,
                    )
                ok = True
                verb = "kicked" if self.action == "kick" else "banned"
                result = f"{display_name(target)} was {verb}."
            except asyncio.TimeoutError:
                ok = False
                result = (
                    f"Discord did not confirm the {self.action} in time. "
                    "Check the server state before retrying so the action is not duplicated."
                )
            except discord.Forbidden:
                ok = False
                result = (
                    f"Discord blocked the {self.action}. Check permissions and "
                    "role hierarchy."
                )
            except Exception as exc:
                ok = False
                result = f"{self.action.title()} failed: {type(exc).__name__}."

        try:
            await interaction.followup.send(
                ("✅ " if ok else "❌ ") + result,
                ephemeral=True,
            )
        finally:
            await record_member_action(
                guild_id=interaction.guild.id,
                actor_id=interaction.user.id,
                target_id=target.id,
                action=self.action,
                reason=reason,
                metadata={"ok": ok},
            )


__all__ = ["MemberActionView", "member_detail_embed"]
