from __future__ import annotations

"""Clean setup surface for compact live profile signatures.

The channel picker is intentionally isolated from all welcome-channel settings.
"""

from typing import Any, Iterable, Mapping, Optional

import discord

from . import profile_card_setup_ui_core as _core
from .commands_ext.public_setup_group import _require_setup_permission
from .guild_config import get_guild_config, upsert_guild_config
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

async def _save_selected_channels(
    interaction: discord.Interaction,
    view: "ProfileCardSetupView",
    selected: set[int],
) -> None:
    guild = interaction.guild
    if guild is None:
        return await _private_message(interaction, "Use this inside a server.", ok=False)
    cleaned = _clean_channel_ids(selected)
    if not cleaned:
        return await _private_message(
            interaction,
            "Choose at least one channel. Use Disable Live Signatures when you want the feature off.",
            ok=False,
        )
    if len(cleaned) > _MAX_LIVE_CHANNELS:
        return await _private_message(interaction, f"Choose no more than {_MAX_LIVE_CHANNELS} channels.", ok=False)

    problems: list[str] = []
    for channel_id in sorted(cleaned):
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            problems.append(f"`{channel_id}` no longer exists")
            continue
        missing = _channel_permission_issues(channel)
        if missing:
            problems.append(f"{channel.mention}: {', '.join(missing)}")
    if problems:
        return await _private_message(
            interaction,
            "Fix these channel permissions before enabling live signatures:\n" + "\n".join(problems)[:1500],
            ok=False,
        )

    if not interaction.response.is_done():
        await interaction.response.defer()
    old_config = await get_guild_config(guild.id, refresh=True)
    old_ids = set(parse_live_card_config(old_config).channel_ids)
    updated = await upsert_guild_config(
        guild.id,
        {
            LIVE_ENABLED_KEY: True,
            LIVE_CHANNEL_IDS_KEY: [str(value) for value in sorted(cleaned)],
        },
    )
    runtime = _runtime(interaction.client)
    if runtime is not None:
        for removed_id in sorted(old_ids - cleaned):
            removed_channel = guild.get_channel(removed_id)
            if isinstance(removed_channel, discord.TextChannel):
                await runtime.disable_channel(guild, removed_channel)
        await runtime.reconcile()
    view.pending_channel_ids = cleaned
    print(
        "🪪 live_profile_card setup saved "
        f"guild={guild.id} enabled=True channels={','.join(str(value) for value in sorted(cleaned))}"
    )
    await view.refresh(
        interaction,
        config=updated,
        notice=(
            f"Saved and enabled immediately in {len(cleaned)} channel(s). "
            "There is no second Save button. Send a normal member message in one of the listed channels."
        ),
    )


class LiveProfileChannelSelect(discord.ui.ChannelSelect):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Choose channels — selection saves immediately…",
            min_values=1,
            max_values=_MAX_LIVE_CHANNELS,
            channel_types=[discord.ChannelType.text],
            custom_id="dank_setup_profile_cards:channels:v2",
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, ProfileCardSetupView) or not await view.interaction_check(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _private_message(interaction, "Use this inside a server.", ok=False)
        selected: set[int] = set()
        for value in self.values:
            channel = guild.get_channel(int(value.id))
            if isinstance(channel, discord.TextChannel):
                selected.add(int(channel.id))
        await _save_selected_channels(interaction, view, selected)


class _ServerLiveToggleButton(discord.ui.Button):
    def __init__(self, *, enabled: bool, has_channels: bool) -> None:
        self.current_enabled = bool(enabled)
        label = "Disable Live Signatures" if self.current_enabled else "Enable Live Signatures"
        super().__init__(
            label=label,
            emoji="⏸️" if self.current_enabled else "▶️",
            style=discord.ButtonStyle.danger if self.current_enabled else discord.ButtonStyle.success,
            custom_id="dank_setup_profile_cards:server_toggle:v2",
            row=1,
            disabled=not self.current_enabled and not bool(has_channels),
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, ProfileCardSetupView) or not await view.interaction_check(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _private_message(interaction, "Use this inside a server.", ok=False)
        if not interaction.response.is_done():
            await interaction.response.defer()
        config = await get_guild_config(guild.id, refresh=True)
        live = parse_live_card_config(config)
        channel_ids = set(live.channel_ids)
        if live.enabled:
            updated = await upsert_guild_config(guild.id, {LIVE_ENABLED_KEY: False})
            runtime = _runtime(interaction.client)
            if runtime is not None:
                for channel_id in sorted(channel_ids):
                    channel = guild.get_channel(channel_id)
                    if isinstance(channel, discord.TextChannel):
                        await runtime.disable_channel(guild, channel)
            notice = "Server live signatures are OFF. The saved channel list is preserved for one-tap re-enable."
            enabled = False
        else:
            if not channel_ids:
                return await _private_message(
                    interaction,
                    "Choose at least one channel above. Selecting it saves and enables the feature immediately.",
                    ok=False,
                )
            updated = await upsert_guild_config(guild.id, {LIVE_ENABLED_KEY: True})
            runtime = _runtime(interaction.client)
            if runtime is not None:
                await runtime.reconcile()
            notice = "Server live signatures are ON in the saved channels."
            enabled = True
        print(
            "🪪 live_profile_card server toggle "
            f"guild={guild.id} enabled={enabled} channels={','.join(str(value) for value in sorted(channel_ids))}"
        )
        view.pending_channel_ids = channel_ids
        await view.refresh(interaction, config=updated, notice=notice)


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
            "This controls only live profile signatures. Choose the text channels where a small horizontal member signature follows the latest eligible speaker. "
            "**Channel selections save and enable immediately**—there is no hidden second Save step. Use the large "
            "Enable/Disable button for the server-wide switch. This does **not** change join cards, welcome/start-here messages, "
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
        value=", ".join(fields) if fields else "No optional details • basic avatar/name still posts",
        inline=True,
    )
    embed.add_field(
        name="Active signature channels",
        value=_channel_lines(guild, live.channel_ids, health=True),
        inline=False,
    )
    if pending != set(live.channel_ids):
        embed.add_field(
            name="Selection status",
            value="Saving the selected channels now…",
            inline=False,
        )
    embed.add_field(
        name="What members can customize",
        value=(
            "Theme, font, colors, background style, layout, avatar frame, privacy, platforms, and profile roles. "
            "Server managers choose channels, allowed information, and the starting visual defaults."
        ),
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
            "allowed_mentions": discord.AllowedMentions.none(),
            "attachments": [rendered.file] if rendered.file is not None else [],
        }
        if rendered.view is not None:
            payload["view"] = rendered.view
        await interaction.edit_original_response(**payload)


class _ServerDefaultsButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Server Signature Defaults",
            emoji="🎨",
            style=discord.ButtonStyle.primary,
            custom_id="dank_setup_profile_cards:defaults",
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from .profile_signature_studio import open_server_signature_defaults

        await open_server_signature_defaults(interaction)


class _ProfileRoleBuilderButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Profile Panel & Roles",
            emoji="🎭",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_setup_profile_cards:roles",
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from .commands_ext.public_self_roles_group import _post_profile_builder

        await _post_profile_builder(interaction, title="Profile Panel")


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
        self.add_item(_ServerLiveToggleButton(enabled=live.enabled, has_channels=bool(live.channel_ids)))
        for field_key in ("roles", "account_dates", "platforms"):
            self.add_item(_core._FieldToggleButton(field_key, allowed=field_key in live.allowed_fields))
        self.add_item(_PreviewButton())
        self.add_item(_core._RefreshButton())
        self.add_item(_ServerDefaultsButton())
        self.add_item(_ProfileRoleBuilderButton())
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
