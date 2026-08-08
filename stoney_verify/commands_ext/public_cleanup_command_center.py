from __future__ import annotations

"""Menu-first home for the former /dank cleanup command family.

All cleanup work delegates to the existing public_cleanup_group callbacks so the
same native Discord permission checks, preview-first user purge confirmation,
invite policy engine, cleanup worker behavior, and audit logging stay canonical.
"""

from typing import Any, Optional

import discord
from discord import app_commands

from .common import _staff_check


async def _private(
    interaction: discord.Interaction,
    content: str = "",
    *,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    payload: dict[str, Any] = {
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if content:
        payload["content"] = content
    if embed is not None:
        payload["embed"] = embed
    if view is not None:
        payload["view"] = view
    if interaction.response.is_done():
        await interaction.followup.send(**payload)
    else:
        await interaction.response.send_message(**payload)


async def _replace(
    interaction: discord.Interaction,
    *,
    embed: discord.Embed,
    view: discord.ui.View,
) -> None:
    payload = {
        "embed": embed,
        "view": view,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if interaction.response.is_done():
        await interaction.edit_original_response(**payload)
    elif interaction.message is not None:
        await interaction.response.edit_message(**payload)
    else:
        await interaction.response.send_message(**payload, ephemeral=True)


async def _invoke(command: Any, interaction: discord.Interaction, /, *args: Any, **kwargs: Any) -> Any:
    callback = getattr(command, "callback", command)
    if not callable(callback):
        raise RuntimeError("Cleanup action is unavailable")
    return await callback(interaction, *args, **kwargs)


async def _require_staff(interaction: discord.Interaction) -> bool:
    try:
        if _staff_check(interaction):
            return True
    except Exception:
        pass
    await _private(interaction, "❌ Staff only.")
    return False


class _OwnedView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: float = 900) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id:
            return True
        await _private(interaction, "❌ Open your own Cleanup Center to use these controls.")
        return False


def _optional_int(value: Any, *, minimum: int, maximum: int) -> Optional[int]:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = int(text)
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"must be between {minimum} and {maximum}")
    return parsed


def _bool_text(value: Any, *, default: Optional[bool] = None) -> Optional[bool]:
    text = str(value or "").strip().lower()
    if not text and default is not None:
        return default
    if not text:
        return None
    if text in {"yes", "y", "true", "1", "on", "delete"}:
        return True
    if text in {"no", "n", "false", "0", "off", "preview"}:
        return False
    raise ValueError("use YES or NO")


def _center_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🧹 Cleanup Center",
        description=(
            "Channel cleanup, targeted user-message cleanup, blocked-invite history cleanup, "
            "and DM-spam reporting are all here. User-target purge remains **preview first** "
            "with its existing confirmation button."
        ),
        color=discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Safety",
        value=(
            "The original cleanup callbacks still enforce Staff access, Discord **Manage Messages** "
            "where required, channel permissions, pinned-message choices, and dry-run behavior."
        ),
        inline=False,
    )
    embed.set_footer(text="Moderation Center → Cleanup Tools")
    return embed


class CleanupCenterView(_OwnedView):
    @discord.ui.button(label="Cleanup Status", emoji="📊", style=discord.ButtonStyle.secondary, row=0)
    async def status(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_cleanup_group import cleanup_status
        await _invoke(cleanup_status, interaction)

    @discord.ui.button(label="Run Configured Cleanup", emoji="▶️", style=discord.ButtonStyle.primary, row=0)
    async def run(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(CleanupRunModal(self.owner_id))

    @discord.ui.button(label="Channel Purge", emoji="🧽", style=discord.ButtonStyle.secondary, row=0)
    async def channel_purge(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        view = ChannelPurgeView(self.owner_id)
        await _replace(interaction, embed=view.embed(interaction), view=view)

    @discord.ui.button(label="User Message Purge", emoji="👤", style=discord.ButtonStyle.secondary, row=0)
    async def user_purge(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        view = UserPurgeView(self.owner_id)
        await _replace(interaction, embed=view.embed(interaction), view=view)

    @discord.ui.button(label="Blocked Invite Cleanup", emoji="🔗", style=discord.ButtonStyle.secondary, row=0)
    async def invites(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        view = InviteCleanupView(self.owner_id)
        await _replace(interaction, embed=view.embed(interaction), view=view)

    @discord.ui.button(label="Report DM Spam", emoji="🚩", style=discord.ButtonStyle.secondary, row=1)
    async def dm_report(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(DmSpamReportModal(self.owner_id))

    @discord.ui.button(label="Moderation Center", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_mod_command_center import open_mod_command_center
        await open_mod_command_center(interaction)


class CleanupRunModal(discord.ui.Modal, title="Run Configured Cleanup"):
    hours = discord.ui.TextInput(label="Override age hours (blank = configured)", required=False, max_length=4)
    limit = discord.ui.TextInput(label="Max per channel (blank = configured)", required=False, max_length=4)
    pins = discord.ui.TextInput(label="Include pinned? YES / NO / blank=default", required=False, max_length=7)
    mode = discord.ui.TextInput(label="PREVIEW or DELETE", default="PREVIEW", max_length=7)
    worker = discord.ui.TextInput(label="Ensure worker started? YES / NO", default="NO", max_length=3)

    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=600)
        self.owner_id = int(owner_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "❌ Only the staff member who opened this form can submit it.")
        try:
            hours = _optional_int(self.hours.value, minimum=1, maximum=8760)
            limit = _optional_int(self.limit.value, minimum=1, maximum=5000)
            include_pinned = _bool_text(self.pins.value, default=None)
            dry_run = not bool(_bool_text(self.mode.value, default=False))
            start_worker = bool(_bool_text(self.worker.value, default=False))
        except (TypeError, ValueError) as exc:
            return await _private(interaction, f"❌ Invalid cleanup option: {exc}.")
        from .public_cleanup_group import cleanup_run
        await _invoke(
            cleanup_run,
            interaction,
            older_than_hours=hours,
            limit_per_channel=limit,
            include_pinned=include_pinned,
            dry_run=dry_run,
            start_worker=start_worker,
        )


class CleanupChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: "_ChannelStateView", *, custom_id: str, placeholder: str) -> None:
        self.parent_view = parent
        super().__init__(
            placeholder=placeholder,
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text],
            custom_id=custom_id,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0] if self.values else None
        channel = interaction.guild.get_channel(int(getattr(selected, "id", 0) or 0)) if interaction.guild else None
        if not isinstance(channel, discord.TextChannel):
            return await _private(interaction, "❌ Choose a text channel from this server.")
        self.parent_view.channel_id = int(channel.id)
        await interaction.response.edit_message(
            embed=self.parent_view.embed(interaction),
            view=self.parent_view,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class _ChannelStateView(_OwnedView):
    channel_id: int = 0

    def channel(self, interaction: discord.Interaction) -> Optional[discord.TextChannel]:
        if interaction.guild and self.channel_id:
            channel = interaction.guild.get_channel(self.channel_id)
            if isinstance(channel, discord.TextChannel):
                return channel
        return interaction.channel if isinstance(interaction.channel, discord.TextChannel) else None


class ChannelPurgeView(_ChannelStateView):
    def __init__(self, owner_id: int) -> None:
        self.channel_id = 0
        super().__init__(owner_id)
        self.add_item(
            CleanupChannelSelect(
                self,
                custom_id="dank:cleanup:channel_purge:channel:v1",
                placeholder="Optional: choose a different text channel…",
            )
        )

    def embed(self, interaction: discord.Interaction) -> discord.Embed:
        channel = self.channel(interaction)
        return discord.Embed(
            title="🧽 Channel Purge",
            description=(
                f"Target: {channel.mention if channel else 'No text channel selected'}\n"
                "Open options to choose amount, age cutoff, pinned-message behavior, and **PREVIEW or DELETE**."
            ),
            color=discord.Color.blurple(),
        )

    @discord.ui.button(label="Purge Options", emoji="⚙️", style=discord.ButtonStyle.primary, row=1)
    async def options(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        channel = self.channel(interaction)
        if channel is None:
            return await _private(interaction, "❌ Choose a text channel first.")
        await interaction.response.send_modal(ChannelPurgeModal(self.owner_id, channel.id))

    @discord.ui.button(label="Cleanup Center", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, embed=_center_embed(), view=CleanupCenterView(self.owner_id))


class ChannelPurgeModal(discord.ui.Modal, title="Channel Purge Options"):
    amount = discord.ui.TextInput(label="Max messages (blank = configured default)", required=False, max_length=6)
    hours = discord.ui.TextInput(label="Only older than hours (blank = no override)", required=False, max_length=4)
    pins = discord.ui.TextInput(label="Include pinned? YES / NO", default="NO", max_length=3)
    mode = discord.ui.TextInput(label="PREVIEW or DELETE", default="PREVIEW", max_length=7)

    def __init__(self, owner_id: int, channel_id: int) -> None:
        super().__init__(timeout=600)
        self.owner_id = int(owner_id)
        self.channel_id = int(channel_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "❌ Only the staff member who opened this form can submit it.")
        channel = interaction.guild.get_channel(self.channel_id) if interaction.guild else None
        if not isinstance(channel, discord.TextChannel):
            return await _private(interaction, "❌ That text channel no longer exists.")
        try:
            amount = _optional_int(self.amount.value, minimum=1, maximum=100000)
            hours = _optional_int(self.hours.value, minimum=1, maximum=8760)
            include_pinned = bool(_bool_text(self.pins.value, default=False))
            dry_run = not bool(_bool_text(self.mode.value, default=False))
        except (TypeError, ValueError) as exc:
            return await _private(interaction, f"❌ Invalid purge option: {exc}.")
        from .public_cleanup_group import cleanup_purge
        await _invoke(
            cleanup_purge,
            interaction,
            channel=channel,
            amount=amount,
            older_than_hours=hours,
            include_pinned=include_pinned,
            dry_run=dry_run,
            user=None,
            user_id=None,
            scope="channel",
        )


class CleanupUserSelect(discord.ui.UserSelect):
    def __init__(self, parent: "UserPurgeView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Optional: choose a current member…",
            min_values=1,
            max_values=1,
            custom_id="dank:cleanup:user_purge:user:v1",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0] if self.values else None
        self.parent_view.user_id = int(getattr(selected, "id", 0) or 0)
        await interaction.response.edit_message(
            embed=self.parent_view.embed(interaction),
            view=self.parent_view,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class UserPurgeChannelSelect(discord.ui.ChannelSelect):
    def __init__(self, parent: "UserPurgeView") -> None:
        self.parent_view = parent
        super().__init__(
            placeholder="Optional channel target when scope=CHANNEL…",
            min_values=1,
            max_values=1,
            channel_types=[discord.ChannelType.text],
            custom_id="dank:cleanup:user_purge:channel:v1",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        selected = self.values[0] if self.values else None
        channel = interaction.guild.get_channel(int(getattr(selected, "id", 0) or 0)) if interaction.guild else None
        if not isinstance(channel, discord.TextChannel):
            return await _private(interaction, "❌ Choose a text channel from this server.")
        self.parent_view.channel_id = int(channel.id)
        await interaction.response.edit_message(
            embed=self.parent_view.embed(interaction),
            view=self.parent_view,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class UserPurgeView(_OwnedView):
    def __init__(self, owner_id: int) -> None:
        self.user_id = 0
        self.channel_id = 0
        super().__init__(owner_id)
        self.add_item(CleanupUserSelect(self))
        self.add_item(UserPurgeChannelSelect(self))

    def embed(self, interaction: discord.Interaction) -> discord.Embed:
        user_text = f"<@{self.user_id}> (`{self.user_id}`)" if self.user_id else "Not selected — raw ID can be entered in options"
        channel = interaction.guild.get_channel(self.channel_id) if interaction.guild and self.channel_id else None
        channel_text = channel.mention if isinstance(channel, discord.TextChannel) else "Current channel when scope=CHANNEL"
        return discord.Embed(
            title="👤 User Message Purge",
            description=(
                f"**User:** {user_text}\n**Channel target:** {channel_text}\n\n"
                "This path always runs the original **preview first** flow. Actual deletion still requires "
                "the confirmation button and native **Manage Messages** permission."
            ),
            color=discord.Color.blurple(),
        )

    @discord.ui.button(label="Preview Options", emoji="👀", style=discord.ButtonStyle.primary, row=2)
    async def options(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(
            UserPurgeOptionsModal(
                self.owner_id,
                user_id=self.user_id,
                channel_id=self.channel_id,
            )
        )

    @discord.ui.button(label="Cleanup Center", emoji="◀️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, embed=_center_embed(), view=CleanupCenterView(self.owner_id))


class UserPurgeOptionsModal(discord.ui.Modal, title="Preview User Message Purge"):
    target_id = discord.ui.TextInput(label="Discord user ID", placeholder="Paste raw ID; works after user left", max_length=20)
    scope = discord.ui.TextInput(label="Scope: CHANNEL or SERVER", default="CHANNEL", max_length=7)
    amount = discord.ui.TextInput(label="Max messages scanned per channel", default="5000", max_length=6)
    pins = discord.ui.TextInput(label="Include pinned? YES / NO", default="NO", max_length=3)

    def __init__(self, owner_id: int, *, user_id: int = 0, channel_id: int = 0, default_scope: str = "CHANNEL") -> None:
        super().__init__(timeout=600)
        self.owner_id = int(owner_id)
        self.channel_id = int(channel_id or 0)
        if user_id:
            self.target_id.default = str(int(user_id))
        self.scope.default = str(default_scope or "CHANNEL").upper()

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "❌ Only the staff member who opened this form can submit it.")
        raw_id = str(self.target_id.value or "").replace("<@", "").replace("!", "").replace(">", "").strip()
        if not raw_id.isdigit() or int(raw_id) <= 0:
            return await _private(interaction, "❌ Enter a valid Discord user ID.")
        scope = str(self.scope.value or "CHANNEL").strip().lower()
        if scope not in {"channel", "server"}:
            return await _private(interaction, "❌ Scope must be **CHANNEL** or **SERVER**.")
        try:
            amount = _optional_int(self.amount.value, minimum=1, maximum=100000) or 5000
            include_pinned = bool(_bool_text(self.pins.value, default=False))
        except (TypeError, ValueError) as exc:
            return await _private(interaction, f"❌ Invalid purge option: {exc}.")
        channel = interaction.guild.get_channel(self.channel_id) if interaction.guild and self.channel_id else None
        if channel is not None and not isinstance(channel, discord.TextChannel):
            channel = None
        from .public_cleanup_group import cleanup_purge
        await _invoke(
            cleanup_purge,
            interaction,
            channel=channel,
            amount=amount,
            older_than_hours=None,
            include_pinned=include_pinned,
            dry_run=True,
            user=None,
            user_id=raw_id,
            scope=scope,
        )


class InviteCleanupView(_ChannelStateView):
    def __init__(self, owner_id: int) -> None:
        self.channel_id = 0
        super().__init__(owner_id)
        self.add_item(
            CleanupChannelSelect(
                self,
                custom_id="dank:cleanup:invites:channel:v1",
                placeholder="Optional: choose a channel for invite scan…",
            )
        )

    def embed(self, interaction: discord.Interaction) -> discord.Embed:
        channel = self.channel(interaction)
        return discord.Embed(
            title="🔗 Blocked Invite History Cleanup",
            description=(
                f"Channel target: {channel.mention if channel else 'Not selected'}\n"
                "Open options to choose scan amount, one channel vs all readable text channels, and PREVIEW vs DELETE. "
                "The canonical invite policy engine decides what is blocked."
            ),
            color=discord.Color.blurple(),
        )

    @discord.ui.button(label="Invite Cleanup Options", emoji="⚙️", style=discord.ButtonStyle.primary, row=1)
    async def options(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(InviteCleanupOptionsModal(self.owner_id, self.channel_id))

    @discord.ui.button(label="Cleanup Center", emoji="◀️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, embed=_center_embed(), view=CleanupCenterView(self.owner_id))


class InviteCleanupOptionsModal(discord.ui.Modal, title="Blocked Invite Cleanup"):
    amount = discord.ui.TextInput(label="Recent messages to check (1-1000)", default="500", max_length=4)
    scope = discord.ui.TextInput(label="Scope: CHANNEL or SERVER", default="CHANNEL", max_length=7)
    mode = discord.ui.TextInput(label="PREVIEW or DELETE", default="PREVIEW", max_length=7)

    def __init__(self, owner_id: int, channel_id: int = 0) -> None:
        super().__init__(timeout=600)
        self.owner_id = int(owner_id)
        self.channel_id = int(channel_id or 0)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "❌ Only the staff member who opened this form can submit it.")
        try:
            amount = _optional_int(self.amount.value, minimum=1, maximum=1000) or 500
            delete = bool(_bool_text(self.mode.value, default=False))
        except (TypeError, ValueError) as exc:
            return await _private(interaction, f"❌ Invalid invite cleanup option: {exc}.")
        scope = str(self.scope.value or "CHANNEL").strip().lower()
        if scope not in {"channel", "server"}:
            return await _private(interaction, "❌ Scope must be **CHANNEL** or **SERVER**.")
        channel = interaction.guild.get_channel(self.channel_id) if interaction.guild and self.channel_id else None
        if channel is not None and not isinstance(channel, discord.TextChannel):
            channel = None
        from .public_cleanup_group import cleanup_invites
        await _invoke(
            cleanup_invites,
            interaction,
            channel=channel,
            amount=amount,
            all_text_channels=(scope == "server"),
            dry_run=not delete,
        )


class DmSpamReportModal(discord.ui.Modal, title="Report DM Spam / Raider"):
    target_id = discord.ui.TextInput(label="Discord user ID", max_length=20)
    evidence = discord.ui.TextInput(
        label="Evidence / note",
        required=False,
        max_length=900,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=600)
        self.owner_id = int(owner_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "❌ Only the staff member who opened this form can submit it.")
        from .public_cleanup_group import cleanup_report_dm_spam
        await _invoke(
            cleanup_report_dm_spam,
            interaction,
            target_user_id=str(self.target_id.value),
            evidence=str(self.evidence.value or ""),
        )


class CompactDmReportActionView(discord.ui.View):
    def __init__(self, *, target_user_id: int, report_count: int) -> None:
        super().__init__(timeout=900)
        self.target_user_id = int(target_user_id)
        self.report_count = int(report_count)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        member = interaction.user
        perms = getattr(member, "guild_permissions", None)
        if bool(
            getattr(perms, "administrator", False)
            or getattr(perms, "ban_members", False)
            or getattr(perms, "manage_messages", False)
        ):
            return True
        await _private(interaction, "❌ Staff action required.")
        return False

    @discord.ui.button(label="Ban by ID", emoji="🔨", style=discord.ButtonStyle.danger, row=0)
    async def ban_by_id(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_mod_group import mod_ban_unban_group_command
        choice = app_commands.Choice(name="Ban", value="ban")
        await _invoke(
            mod_ban_unban_group_command,
            interaction,
            member=str(self.target_user_id),
            action=choice,
            reason=(
                f"Dank Shield DM spam report action; reports={self.report_count}"
            ),
            delete_message_days=0,
        )

    @discord.ui.button(label="Purge User Messages", emoji="🧹", style=discord.ButtonStyle.secondary, row=0)
    async def purge(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(
            UserPurgeOptionsModal(
                int(interaction.user.id),
                user_id=self.target_user_id,
                default_scope="SERVER",
            )
        )


_CLEANUP_COMPAT_INSTALLED = False


def install_cleanup_menu_compat() -> bool:
    global _CLEANUP_COMPAT_INSTALLED
    if _CLEANUP_COMPAT_INSTALLED:
        return True
    from . import public_cleanup_group as cleanup

    # cleanup_report_dm_spam resolves this global at call time. Replacing only the
    # view preserves the canonical report/storage/posting logic while preventing
    # its old button from advertising a retired slash command.
    cleanup.DmRaiderReportActionView = CompactDmReportActionView
    _CLEANUP_COMPAT_INSTALLED = True
    return True


async def open_cleanup_command_center(interaction: discord.Interaction) -> None:
    if not await _require_staff(interaction):
        return
    install_cleanup_menu_compat()
    await _private(
        interaction,
        embed=_center_embed(),
        view=CleanupCenterView(int(interaction.user.id)),
    )


__all__ = [
    "CleanupCenterView",
    "CompactDmReportActionView",
    "install_cleanup_menu_compat",
    "open_cleanup_command_center",
]
