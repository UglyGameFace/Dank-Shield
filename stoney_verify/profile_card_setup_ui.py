from __future__ import annotations

"""Clean setup surface for compact live profile signatures.

The channel picker is intentionally isolated from all welcome-channel settings.
"""

from typing import Any, Iterable, Mapping, Optional

import discord

from . import profile_card_setup_ui_core as _core
from .commands_ext.public_setup_group import _require_setup_permission
from .guild_config import get_guild_config
from .profile_card_runtime import (
    LIVE_ALLOWED_FIELDS_KEY,
    LIVE_CHANNEL_IDS_KEY,
    LIVE_ENABLED_KEY,
    LiveProfileCardRuntime,
    parse_live_card_config,
    render_live_profile_card,
)
from .profile_card_service import ProfileStorageUnavailable

_RUNTIME_ATTRIBUTE = _core._RUNTIME_ATTRIBUTE
_MAX_LIVE_CHANNELS = _core._MAX_LIVE_CHANNELS
_FIELD_LABELS = _core._FIELD_LABELS
_clean_channel_ids = _core._clean_channel_ids
_channel_lines = _core._channel_lines
_channel_permission_issues = _core._channel_permission_issues
_runtime = _core._runtime
_edit_or_send = _core._edit_or_send
_private_message = _core._private_message

# Reuse the already-tested picker and action callbacks. The replacement view
# below deliberately omits the old welcome-channel shortcut.
LiveProfileChannelSelect = _core.LiveProfileChannelSelect


def _setup_embed(
    guild: discord.Guild,
    config: Mapping[str, Any],
    *,
    pending_channel_ids: Optional[set[int]] = None,
    notice: str = "",
) -> discord.Embed:
    live = parse_live_card_config(config)
    pending = set(live.channel_ids) if pending_channel_ids is None else _clean_channel_ids(pending_channel_ids)
    fields = [
        _FIELD_LABELS[key]
        for key in ("roles", "account_dates", "platforms")
        if key in live.allowed_fields
    ]
    embed = discord.Embed(
        title="🪪 Compact Profile Signatures",
        description=(
            "Choose the text channels where a small horizontal member signature follows the latest eligible speaker. "
            "This controls only live profile signatures. It does **not** change join cards, welcome/start-here messages, "
            "or join/leave announcements."
        ),
        color=discord.Color.green() if live.enabled else discord.Color.blurple(),
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(
        name="Current status",
        value=f"**{'ON' if live.enabled else 'OFF'}** • {len(live.channel_ids)} active channel(s)",
        inline=True,
    )
    embed.add_field(
        name="Optional details allowed",
        value=", ".join(fields) if fields else "No optional details",
        inline=True,
    )
    embed.add_field(
        name="Active signature channels",
        value=_channel_lines(guild, live.channel_ids, health=True),
        inline=False,
    )
    if pending != set(live.channel_ids):
        embed.add_field(
            name="Pending picker selection",
            value=_channel_lines(guild, pending, health=False)
            + "\nPress **Save Selected Channels** to apply this list.",
            inline=False,
        )
    embed.add_field(
        name="Privacy and anti-repetition",
        value=(
            "Member privacy always wins. Dank Shield keeps one compact bot-owned signature per enabled channel, "
            "coalesces message bursts, and never edits, deletes, copies, or reposts user messages."
        ),
        inline=False,
    )
    if notice:
        embed.add_field(name="Last action", value=notice[:1024], inline=False)
    embed.set_footer(text="/dank setup → All Features & Settings → Compact Profile Signatures")
    return embed


class _PreviewButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Preview Compact Signature",
            emoji="👀",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_setup_profile_cards:preview",
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, ProfileCardSetupView) or not await view.interaction_check(interaction):
            return
        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if guild is None or member is None:
            return await _private_message(interaction, "Use this inside a server as a member.", ok=False)
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            config = parse_live_card_config(await get_guild_config(guild.id, refresh=True))
            rendered = await render_live_profile_card(
                member,
                set(config.allowed_fields),
                trigger_message_id=0,
                require_live_enabled=False,
            )
        except ProfileStorageUnavailable:
            return await _private_message(interaction, "Private profile storage is unavailable.", ok=False)
        if rendered is None:
            return await _private_message(
                interaction,
                "Your privacy choices currently hide every optional signature detail.",
                ok=False,
            )
        rendered.embed.set_footer(text="Preview only • compact signature • nothing was posted publicly")
        payload: dict[str, Any] = {
            "embed": rendered.embed,
            "view": rendered.view,
            "allowed_mentions": discord.AllowedMentions.none(),
            "attachments": [rendered.file] if rendered.file is not None else [],
        }
        await interaction.edit_original_response(**payload)


class ProfileCardSetupView(_core.ProfileCardSetupView):
    def __init__(
        self,
        *,
        owner_id: int,
        config: Mapping[str, Any],
        pending_channel_ids: Optional[set[int]] = None,
    ) -> None:
        discord.ui.View.__init__(self, timeout=900)
        self.owner_id = int(owner_id)
        live = parse_live_card_config(config)
        self.pending_channel_ids = (
            set(live.channel_ids)
            if pending_channel_ids is None
            else _clean_channel_ids(pending_channel_ids)
        )
        self.add_item(LiveProfileChannelSelect())
        self.add_item(_core._SaveChannelsButton())
        self.add_item(_core._DisableAllButton(disabled=not live.channel_ids))
        for field_key in ("roles", "account_dates", "platforms"):
            self.add_item(_core._FieldToggleButton(field_key, allowed=field_key in live.allowed_fields))
        self.add_item(_PreviewButton())
        self.add_item(_core._RefreshButton())
        self.add_item(_core._BackButton())
        self.add_item(_core._HomeButton())
        self.add_item(_core._CloseButton())

    async def refresh(
        self,
        interaction: discord.Interaction,
        *,
        config: Optional[Mapping[str, Any]] = None,
        notice: str = "",
    ) -> None:
        guild = interaction.guild
        if guild is None:
            return
        latest = dict(config or await get_guild_config(guild.id, refresh=True))
        replacement = ProfileCardSetupView(
            owner_id=self.owner_id,
            config=latest,
            pending_channel_ids=set(self.pending_channel_ids),
        )
        await _edit_or_send(
            interaction,
            embed=_setup_embed(
                guild,
                latest,
                pending_channel_ids=set(self.pending_channel_ids),
                notice=notice,
            ),
            view=replacement,
        )


async def open_profile_card_setup(interaction: discord.Interaction) -> None:
    if not await _require_setup_permission(interaction):
        return
    guild = interaction.guild
    if guild is None:
        return await _private_message(interaction, "Use this inside a server.", ok=False)
    if not interaction.response.is_done():
        await interaction.response.defer()
    config = await get_guild_config(guild.id, refresh=True)
    view = ProfileCardSetupView(owner_id=interaction.user.id, config=config)
    await _edit_or_send(interaction, embed=_setup_embed(guild, config), view=view)


__all__ = [
    "LiveProfileChannelSelect",
    "ProfileCardSetupView",
    "open_profile_card_setup",
]
