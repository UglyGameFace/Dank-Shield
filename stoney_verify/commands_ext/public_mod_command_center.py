from __future__ import annotations

"""Compact moderation doorway that preserves every former /mod action."""

from typing import Any, Optional

import discord

from ..panel_lifecycle import PRIVATE_MENU_TTL_SECONDS
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


async def _invoke(command: Any, interaction: discord.Interaction, /, *args: Any, **kwargs: Any) -> Any:
    callback = getattr(command, "callback", command)
    if not callable(callback):
        raise RuntimeError("Moderation action is unavailable")
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
    def __init__(self, owner_id: int, *, timeout: float = PRIVATE_MENU_TTL_SECONDS) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id:
            return True
        await _private(interaction, "❌ Open your own moderation center to use these controls.")
        return False


def _center_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🛡️ Moderation",
        description=(
            "Choose a member below to see who they are and act on them: "
            "timeout, kick, ban, message, verify or roles.\n\n"
            "-# Someone who already left? Use **Ban / Unban by ID**."
        ),
        color=discord.Color.blurple(),
    )
    embed.set_footer(text="Every action re-checks permissions and is logged.")
    return embed


class ModMemberSelect(discord.ui.UserSelect):
    """First step of /mod: pick the member, land on their action card."""

    def __init__(self) -> None:
        super().__init__(
            placeholder="Pick a member to moderate…",
            min_values=1,
            max_values=1,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _require_staff(interaction):
            return
        guild = interaction.guild
        selected = self.values[0] if self.values else None
        member_id = int(getattr(selected, "id", 0) or 0)
        member = guild.get_member(member_id) if guild is not None else None
        if member is None and guild is not None and member_id:
            try:
                member = await guild.fetch_member(member_id)
            except Exception:
                member = None
        if not isinstance(member, discord.Member):
            return await _private(
                interaction,
                "❌ That user isn't in the server right now. Use **Ban / Unban by ID** instead.",
            )

        from .member_role_browser_actions import MemberActionView, member_detail_embed

        center = self.view
        await interaction.response.edit_message(
            embed=member_detail_embed(member, None),
            view=MemberActionView(owner_id=center.owner_id, member=member, browser=center),
            allowed_mentions=discord.AllowedMentions.none(),
        )


class ModerationCenterView(_OwnedView):
    """/mod entry. Doubles as the "browser" MemberActionView returns to."""

    role = None  # MemberActionView reads browser.role for its embed context.

    def __init__(self, owner_id: int, *, timeout: float = PRIVATE_MENU_TTL_SECONDS) -> None:
        super().__init__(owner_id, timeout=timeout)
        self.add_item(ModMemberSelect())

    def rebuild(self) -> None:
        """Called by MemberActionView's Back button; nothing to recompute here."""

    def render_embed(self) -> discord.Embed:
        return _center_embed()

    @discord.ui.button(label="Member Command Center", emoji="👥", style=discord.ButtonStyle.secondary, row=1)
    async def members(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .member_command_center import open_member_command_center
        await open_member_command_center(interaction)

    @discord.ui.button(label="Cleanup Tools", emoji="🧹", style=discord.ButtonStyle.secondary, row=1)
    async def cleanup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_cleanup_command_center import open_cleanup_command_center
        await open_cleanup_command_center(interaction)

    @discord.ui.button(label="Ban / Unban by ID", emoji="🔨", style=discord.ButtonStyle.danger, row=1)
    async def ban_unban(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(BanUnbanByIdModal(self.owner_id))

    @discord.ui.button(label="Gateway Intents", emoji="📡", style=discord.ButtonStyle.secondary, row=1)
    async def intents(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not await _require_staff(interaction):
            return
        from .public_mod_group import mod_debug_intents_group_command
        await _invoke(mod_debug_intents_group_command, interaction)

    @discord.ui.button(label="Dank Shield Home", emoji="🏠", style=discord.ButtonStyle.secondary, row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_command_surface_v2 import open_compact_dank_home
        await open_compact_dank_home(interaction)


class BanUnbanByIdModal(discord.ui.Modal, title="Ban / Unban by User ID"):
    user = discord.ui.TextInput(
        label="Discord user ID",
        placeholder="Paste the raw Discord user ID",
        max_length=20,
    )
    action = discord.ui.TextInput(
        label="Action: BAN, UNBAN, or AUTO",
        placeholder="AUTO",
        default="AUTO",
        max_length=5,
    )
    reason = discord.ui.TextInput(
        label="Reason",
        required=False,
        max_length=400,
        style=discord.TextStyle.paragraph,
    )
    delete_days = discord.ui.TextInput(
        label="Ban only: message history days (0-7)",
        default="0",
        max_length=1,
    )

    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=600)
        self.owner_id = int(owner_id)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if int(interaction.user.id) != self.owner_id:
            return await _private(interaction, "❌ Only the staff member who opened this form can submit it.")
        if not await _require_staff(interaction):
            return
        raw_action = str(self.action.value or "AUTO").strip().lower()
        if raw_action not in {"ban", "unban", "auto"}:
            return await _private(interaction, "❌ Action must be **BAN**, **UNBAN**, or **AUTO**.")
        try:
            days = int(str(self.delete_days.value or "0").strip())
        except Exception:
            return await _private(interaction, "❌ Message-history days must be a whole number from 0 to 7.")
        if days < 0 or days > 7:
            return await _private(interaction, "❌ Message-history days must be between 0 and 7.")
        from .public_mod_group import mod_ban_unban_group_command
        choice = app_commands.Choice(name=raw_action.title(), value=raw_action)
        await _invoke(
            mod_ban_unban_group_command,
            interaction,
            member=str(self.user.value).strip(),
            action=choice,
            reason=str(self.reason.value or "").strip() or None,
            delete_message_days=days,
        )


async def open_mod_command_center(interaction: discord.Interaction) -> None:
    if not await _require_staff(interaction):
        return
    await _private(
        interaction,
        embed=_center_embed(),
        view=ModerationCenterView(int(interaction.user.id)),
    )


__all__ = ["ModerationCenterView", "open_mod_command_center"]
