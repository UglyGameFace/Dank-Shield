from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

import discord

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


_RUNTIME_ATTRIBUTE = "_dank_live_profile_card_runtime"
_MAX_LIVE_CHANNELS = 10
_FIELD_LABELS = {
    "roles": "Profile roles",
    "account_dates": "Account dates",
    "platforms": "Shared platforms",
}


def _clean_channel_ids(values: Iterable[Any]) -> set[int]:
    cleaned: set[int] = set()
    for value in values:
        try:
            channel_id = int(getattr(value, "id", value))
        except (TypeError, ValueError):
            continue
        if channel_id > 0:
            cleaned.add(channel_id)
    return cleaned


def _welcome_channel(guild: discord.Guild, config: Mapping[str, Any]) -> Optional[discord.TextChannel]:
    try:
        channel_id = int(str(config.get("welcome_channel_id") or "0"))
    except (TypeError, ValueError):
        channel_id = 0
    channel = guild.get_channel(channel_id) if channel_id > 0 else None
    return channel if isinstance(channel, discord.TextChannel) else None


def _channel_permission_issues(channel: discord.TextChannel) -> list[str]:
    me = channel.guild.me
    if not isinstance(me, discord.Member):
        return ["Dank Shield member unavailable"]
    permissions = channel.permissions_for(me)
    checks = (
        ("View Channel", permissions.view_channel),
        ("Send Messages", permissions.send_messages),
        ("Embed Links", permissions.embed_links),
        ("Read Message History", permissions.read_message_history),
        ("Attach Files", permissions.attach_files),
    )
    return [label for label, allowed in checks if not allowed]


def _channel_lines(guild: discord.Guild, channel_ids: Iterable[int], *, health: bool) -> str:
    lines: list[str] = []
    for channel_id in sorted(_clean_channel_ids(channel_ids)):
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            lines.append(f"❌ Missing text channel `{channel_id}`")
            continue
        if not health:
            lines.append(channel.mention)
            continue
        issues = _channel_permission_issues(channel)
        lines.append(
            f"{'✅' if not issues else '❌'} {channel.mention}"
            + (" — ready" if not issues else " — missing " + ", ".join(issues))
        )
    return "\n".join(lines)[:1024] if lines else "None selected."


def _runtime(client: Any) -> Optional[LiveProfileCardRuntime]:
    found = getattr(client, _RUNTIME_ATTRIBUTE, None)
    return found if isinstance(found, LiveProfileCardRuntime) else None


def _setup_embed(
    guild: discord.Guild,
    config: Mapping[str, Any],
    *,
    pending_channel_ids: Optional[set[int]] = None,
    notice: str = "",
) -> discord.Embed:
    live = parse_live_card_config(config)
    pending = set(live.channel_ids) if pending_channel_ids is None else _clean_channel_ids(pending_channel_ids)
    welcome = _welcome_channel(guild, config)
    fields = [
        _FIELD_LABELS[key]
        for key in ("roles", "account_dates", "platforms")
        if key in live.allowed_fields
    ]
    embed = discord.Embed(
        title="🪪 Member Profiles & Live Cards",
        description=(
            "Choose where a compact member profile follows the latest eligible human conversation. "
            "This feature is **not** the static welcome/start-here message, a join-only welcome image, "
            "or a join/leave announcement.\n\n"
            "In a welcome channel, a live profile appears only after an eligible human sends a message. "
            "It never posts just because someone joined and never replaces your welcome message."
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
        name="Allowed by server",
        value=", ".join(fields) if fields else "No optional fields",
        inline=True,
    )
    embed.add_field(
        name="Active channels",
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
        name="Saved welcome/start-here channel",
        value=(
            f"{welcome.mention} • {'already active' if welcome.id in pending else 'press Add Welcome Channel to stage it'}"
            if isinstance(welcome, discord.TextChannel)
            else "Not configured. Choose any text channel below, or set the static welcome channel first."
        ),
        inline=False,
    )
    embed.add_field(
        name="Privacy and anti-repetition",
        value=(
            "Member privacy always wins. Dank Shield coalesces message bursts, suppresses repeated cards "
            "for the same speaker, and owns only one live card per enabled channel. User messages are never edited, deleted, or reposted."
        ),
        inline=False,
    )
    if notice:
        embed.add_field(name="Last action", value=notice[:1024], inline=False)
    embed.set_footer(text="/dank setup → All Features & Settings → Member Profiles & Live Cards")
    return embed


async def _edit_or_send(
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
    if not interaction.response.is_done():
        try:
            await interaction.response.edit_message(**payload)
            return
        except Exception:
            await interaction.response.send_message(**payload, ephemeral=True)
            return
    try:
        await interaction.edit_original_response(**payload)
    except Exception:
        await interaction.followup.send(**payload, ephemeral=True)


async def _private_message(interaction: discord.Interaction, content: str, *, ok: bool = True) -> None:
    text = ("✅ " if ok else "❌ ") + str(content or "")[:1900]
    kwargs = {
        "content": text,
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if not interaction.response.is_done():
        await interaction.response.send_message(**kwargs)
    else:
        await interaction.followup.send(**kwargs)


class LiveProfileChannelSelect(discord.ui.ChannelSelect):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Choose every text channel that should show live profiles…",
            min_values=1,
            max_values=_MAX_LIVE_CHANNELS,
            channel_types=[discord.ChannelType.text],
            custom_id="dank_setup_profile_cards:channels",
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
        if not selected:
            return await _private_message(interaction, "Choose at least one available text channel.", ok=False)
        view.pending_channel_ids = selected
        if not interaction.response.is_done():
            await interaction.response.defer()
        await view.refresh(
            interaction,
            notice=(
                f"Staged {len(selected)} channel(s). Nothing changed yet. "
                "Press Save Selected Channels to apply them."
            ),
        )


class _SaveChannelsButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Save Selected Channels",
            emoji="💾",
            style=discord.ButtonStyle.success,
            custom_id="dank_setup_profile_cards:save_channels",
            row=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, ProfileCardSetupView) or not await view.interaction_check(interaction):
            return
        guild = interaction.guild
        if guild is None:
            return await _private_message(interaction, "Use this inside a server.", ok=False)
        selected = _clean_channel_ids(view.pending_channel_ids)
        if not selected:
            return await _private_message(
                interaction,
                "Choose one or more channels first. Use Disable All to turn the feature off.",
                ok=False,
            )
        if len(selected) > _MAX_LIVE_CHANNELS:
            return await _private_message(
                interaction,
                f"Choose no more than {_MAX_LIVE_CHANNELS} channels.",
                ok=False,
            )
        problems: list[str] = []
        for channel_id in sorted(selected):
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
                "Fix these channel permissions before enabling live cards:\n" + "\n".join(problems)[:1500],
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
                LIVE_CHANNEL_IDS_KEY: [str(value) for value in sorted(selected)],
            },
        )
        runtime = _runtime(interaction.client)
        if runtime is not None:
            for removed_id in sorted(old_ids - selected):
                removed_channel = guild.get_channel(removed_id)
                if isinstance(removed_channel, discord.TextChannel):
                    await runtime.disable_channel(guild, removed_channel)
            await runtime.reconcile()
        view.pending_channel_ids = selected
        await view.refresh(
            interaction,
            config=updated,
            notice=f"Enabled live profile cards in {len(selected)} channel(s).",
        )


class _AddWelcomeChannelButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Add Welcome Channel",
            emoji="👋",
            style=discord.ButtonStyle.primary,
            custom_id="dank_setup_profile_cards:add_welcome",
            row=1,
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
        welcome = _welcome_channel(guild, config)
        if not isinstance(welcome, discord.TextChannel):
            return await _private_message(
                interaction,
                "No static welcome/start-here channel is saved. Choose a text channel with the picker, or set `/dank welcome set-channel` first.",
                ok=False,
            )
        pending = set(view.pending_channel_ids)
        pending.add(int(welcome.id))
        if len(pending) > _MAX_LIVE_CHANNELS:
            return await _private_message(
                interaction,
                f"The picker is already at the {_MAX_LIVE_CHANNELS}-channel limit.",
                ok=False,
            )
        view.pending_channel_ids = pending
        await view.refresh(
            interaction,
            config=config,
            notice=f"Staged {welcome.mention}. Press Save Selected Channels to apply it.",
        )


class _DisableAllButton(discord.ui.Button):
    def __init__(self, *, disabled: bool) -> None:
        super().__init__(
            label="Disable All",
            emoji="⏸️",
            style=discord.ButtonStyle.danger,
            custom_id="dank_setup_profile_cards:disable_all",
            row=1,
            disabled=disabled,
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
        old_ids = set(parse_live_card_config(config).channel_ids)
        updated = await upsert_guild_config(
            guild.id,
            {LIVE_ENABLED_KEY: False, LIVE_CHANNEL_IDS_KEY: []},
        )
        runtime = _runtime(interaction.client)
        if runtime is not None:
            for channel_id in sorted(old_ids):
                channel = guild.get_channel(channel_id)
                if isinstance(channel, discord.TextChannel):
                    await runtime.disable_channel(guild, channel)
            await runtime.reconcile()
        view.pending_channel_ids = set()
        await view.refresh(
            interaction,
            config=updated,
            notice="Disabled live profile cards and cleaned up Dank Shield-owned cards.",
        )


class _FieldToggleButton(discord.ui.Button):
    def __init__(self, field_key: str, *, allowed: bool) -> None:
        self.field_key = field_key
        label = _FIELD_LABELS[field_key]
        super().__init__(
            label=f"{label}: {'Allowed' if allowed else 'Hidden'}",
            emoji={"roles": "🎭", "account_dates": "📅", "platforms": "🔗"}[field_key],
            style=discord.ButtonStyle.success if allowed else discord.ButtonStyle.secondary,
            custom_id=f"dank_setup_profile_cards:field:{field_key}",
            row=2,
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
        allowed = set(parse_live_card_config(config).allowed_fields)
        if self.field_key in allowed:
            allowed.remove(self.field_key)
            action = "hidden"
        else:
            allowed.add(self.field_key)
            action = "allowed"
        updated = await upsert_guild_config(
            guild.id,
            {LIVE_ALLOWED_FIELDS_KEY: sorted(allowed)},
        )
        runtime = _runtime(interaction.client)
        if runtime is not None:
            await runtime.invalidate_guild_cards(guild)
        await view.refresh(
            interaction,
            config=updated,
            notice=f"{_FIELD_LABELS[self.field_key]} are now {action}. Member privacy can still hide them.",
        )


class _PreviewButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Preview My Card",
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
                "Your privacy choices currently hide every optional live-card field.",
                ok=False,
            )
        rendered.embed.set_footer(
            text="Preview only • not a join event • nothing was posted publicly"
        )
        await interaction.edit_original_response(
            embed=rendered.embed,
            view=rendered.view,
            allowed_mentions=discord.AllowedMentions.none(),
        )


class _RefreshButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Refresh",
            emoji="🔄",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_setup_profile_cards:refresh",
            row=3,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, ProfileCardSetupView) or not await view.interaction_check(interaction):
            return
        if not interaction.response.is_done():
            await interaction.response.defer()
        await view.refresh(interaction, notice="Refreshed from saved configuration.")


class _BackButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Back to All Features",
            emoji="↩️",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_setup_profile_cards:back",
            row=4,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from .commands_ext import public_setup_recommend

        await public_setup_recommend._open_advanced_settings(interaction)


class _HomeButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Setup Home",
            emoji="🏠",
            style=discord.ButtonStyle.secondary,
            custom_id="dank_setup_profile_cards:home",
            row=4,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        from .commands_ext import public_setup_recommend

        await public_setup_recommend._home_edit(interaction)


class _CloseButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="Close",
            emoji="✖️",
            style=discord.ButtonStyle.danger,
            custom_id="dank_setup_profile_cards:close",
            row=4,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.view is not None:
            for child in self.view.children:
                child.disabled = True
        await interaction.response.edit_message(
            content="Closed Member Profiles & Live Cards setup. Reopen it from `/dank setup`.",
            embed=None,
            view=self.view,
        )


class ProfileCardSetupView(discord.ui.View):
    def __init__(
        self,
        *,
        owner_id: int,
        config: Mapping[str, Any],
        pending_channel_ids: Optional[set[int]] = None,
    ) -> None:
        super().__init__(timeout=900)
        self.owner_id = int(owner_id)
        live = parse_live_card_config(config)
        self.pending_channel_ids = (
            set(live.channel_ids)
            if pending_channel_ids is None
            else _clean_channel_ids(pending_channel_ids)
        )
        self.add_item(LiveProfileChannelSelect())
        self.add_item(_SaveChannelsButton())
        self.add_item(_AddWelcomeChannelButton())
        self.add_item(_DisableAllButton(disabled=not live.channel_ids))
        for field_key in ("roles", "account_dates", "platforms"):
            self.add_item(_FieldToggleButton(field_key, allowed=field_key in live.allowed_fields))
        self.add_item(_PreviewButton())
        self.add_item(_RefreshButton())
        self.add_item(_BackButton())
        self.add_item(_HomeButton())
        self.add_item(_CloseButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) != self.owner_id:
            await _private_message(
                interaction,
                "Only the manager who opened this setup panel can use it.",
                ok=False,
            )
            return False
        if interaction.guild is None:
            await _private_message(interaction, "Use this inside a server.", ok=False)
            return False
        return await _require_setup_permission(interaction)

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
    view = ProfileCardSetupView(
        owner_id=interaction.user.id,
        config=config,
    )
    await _edit_or_send(
        interaction,
        embed=_setup_embed(guild, config),
        view=view,
    )


__all__ = [
    "LiveProfileChannelSelect",
    "ProfileCardSetupView",
    "open_profile_card_setup",
]
