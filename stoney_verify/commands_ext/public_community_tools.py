from __future__ import annotations

"""Menu-first community utilities for Dank Shield."""

import hashlib
import random
import re
from dataclasses import replace
from datetime import timedelta
from typing import Any, Optional, Sequence

import discord

from stoney_verify.community_lookup_service import (
    CommunityLookupError,
    WEATHER_LABELS,
    random_wikihow,
    random_wikipedia,
    urban_dictionary_lookup,
    weather_lookup,
    wikipedia_lookup,
)
from stoney_verify.community_tools_runtime import ensure_community_tools_runtime, sticky_poll_embed
from stoney_verify.community_tools_service import (
    MAX_INTERVAL_SECONDS,
    MAX_MESSAGE_THRESHOLD,
    MIN_INTERVAL_SECONDS,
    MIN_MESSAGE_THRESHOLD,
    CommunityStorageUnavailable,
    InvalidCommunityToolValue,
    StickyConfig,
    StickyPoll,
    delete_sticky,
    get_sticky,
    get_sticky_poll,
    list_stickies,
    normalize_https_url,
    normalize_poll,
    reset_sticky_poll,
    save_sticky,
    save_sticky_bundle,
    set_sticky_enabled,
    set_sticky_poll_state,
)
from .public_quiet_notice import open_quiet_notice_center
from .public_sticky_preview import _draft_is_stale, show_sticky_draft_preview, show_sticky_preview

_ALLOWED_MENTIONS = discord.AllowedMentions.none()
_SERVER_STICKIES_PAGE_SIZE = 15
_DICE_PATTERN = re.compile(r"^(?P<count>\d{1,2})?d(?P<sides>\d{1,4})(?P<modifier>[+-]\d{1,5})?$", re.IGNORECASE)


def _text_channel(interaction: discord.Interaction) -> Optional[discord.TextChannel]:
    channel = interaction.channel
    return channel if isinstance(channel, discord.TextChannel) else None


def _member_permissions(interaction: discord.Interaction) -> Optional[discord.Permissions]:
    channel = _text_channel(interaction)
    member = interaction.user
    if channel is None or not isinstance(member, discord.Member):
        return None
    return channel.permissions_for(member)


def _manage_messages(interaction: discord.Interaction) -> bool:
    member = interaction.user
    perms = _member_permissions(interaction)
    return bool(
        isinstance(member, discord.Member)
        and perms is not None
        and (member.guild_permissions.administrator or perms.manage_messages)
    )


def _manage_webhooks(interaction: discord.Interaction) -> bool:
    member = interaction.user
    perms = _member_permissions(interaction)
    return bool(
        isinstance(member, discord.Member)
        and perms is not None
        and (member.guild_permissions.administrator or perms.manage_webhooks)
    )


def _can_send_messages(interaction: discord.Interaction) -> bool:
    perms = _member_permissions(interaction)
    return bool(perms and perms.view_channel and perms.send_messages)


def _poll_permission(perms: Any) -> bool:
    # discord.py 2.4 introduced poll permissions. Different Discord API/library
    # revisions have exposed send_polls and create_polls; honor either when present.
    values = [bool(getattr(perms, name)) for name in ("send_polls", "create_polls") if hasattr(perms, name)]
    return all(values) if values else True


def _can_create_poll(interaction: discord.Interaction) -> bool:
    perms = _member_permissions(interaction)
    return bool(perms and perms.view_channel and perms.send_messages and _poll_permission(perms))


def _bot_permissions(channel: discord.TextChannel) -> Optional[discord.Permissions]:
    me = channel.guild.me
    return channel.permissions_for(me) if me is not None else None


def _bot_can_post(channel: discord.TextChannel, *, embed: bool = False, poll: bool = False) -> bool:
    perms = _bot_permissions(channel)
    if perms is None or not (perms.view_channel and perms.send_messages):
        return False
    if embed and not perms.embed_links:
        return False
    if poll and not _poll_permission(perms):
        return False
    return True


async def _private(
    interaction: discord.Interaction,
    content: str = "",
    *,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    payload: dict[str, Any] = {"ephemeral": True, "allowed_mentions": _ALLOWED_MENTIONS}
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
    content: Optional[str] = None,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    await interaction.response.edit_message(
        content=content,
        embed=embed,
        view=view,
        allowed_mentions=_ALLOWED_MENTIONS,
    )


class _OwnedView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: float = 900) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id:
            return True
        await _private(interaction, "❌ Open your own `/dank home` panel to use these controls.")
        return False


def _center_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🧰 Community Tools",
        description=(
            "Useful server tools without turning `/dank` into a phone book: durable stickies, polls, embeds, "
            "server diagnostics, and lightweight lookups."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Persistent messages",
        value=(
            "📌 Message/embed stickies • 📊 sticky polls • 👁️ preview + temporary test • 🌙 quiet notices • "
            "safe cadence • optional managed sender"
        ),
        inline=False,
    )
    embed.add_field(
        name="Community",
        value="📊 Native Discord polls • 🧱 reviewed embed builder • 👤 member/server info • 🔐 feature-aware permission check",
        inline=False,
    )
    embed.add_field(
        name="Fun & lookup",
        value="🌦️ Weather • 📚 Wikipedia • 🛠️ WikiHow • 🔞 Urban Dictionary • 🎲 custom dice • 🪙 coin flip • 💘 compatibility",
        inline=False,
    )
    embed.set_footer(text="No raw webhook secrets are stored • persistent replacements are recorded before old messages are removed")
    return embed


def _sticky_status_embed(config: Optional[StickyConfig], poll: Optional[StickyPoll] = None) -> discord.Embed:
    if config is None:
        embed = discord.Embed(
            title="📌 Sticky Messages",
            description="No channel sticky is configured here yet.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Quick setup",
            value=(
                "**1. Create / Edit** and choose Message or Embed.\n"
                "**2. Review the private draft** and optionally post a temporary test.\n"
                "**3. Publish** only when it looks right. Use **Sticky Poll** for a persistent vote card."
            ),
            inline=False,
        )
        embed.add_field(
            name="Quiet-time message instead?",
            value=(
                "Use **Quiet Server Notice** for one post after no human messages are observed in channels Dank Shield can see. "
                "It is independent from normal channel stickies."
            ),
            inline=False,
        )
        embed.set_footer(text="Default: on human activity, move after 15s have elapsed or after 5 new human messages")
        return embed

    state = "▶️ Active" if config.enabled else "⏸️ Paused"
    mode = {"plain": "Message", "embed": "Rich embed", "poll": "Sticky poll"}.get(config.mode, config.mode)
    embed = discord.Embed(
        title="📌 Sticky Messages",
        description=f"{state} • **{mode}** • <#{config.channel_id}>",
        color=discord.Color.green() if config.enabled else discord.Color.orange(),
    )
    embed.add_field(
        name="Movement",
        value=(
            f"When a human message arrives, move if **{config.interval_seconds}s** have elapsed since the last sticky, "
            f"or after **{config.message_threshold}** new human messages."
        ),
        inline=False,
    )
    if config.mode == "plain":
        embed.add_field(name="Message", value=(config.content or "(empty)")[:1000], inline=False)
    elif config.mode == "embed":
        preview = config.title or config.content or config.image_url or "(empty embed)"
        embed.add_field(name="Embed", value=preview[:1000], inline=False)
        embed.add_field(name="Color", value=f"`#{int(config.color):06X}`", inline=True)
    elif poll is not None:
        embed.add_field(
            name="Poll",
            value=f"{poll.question[:700]}\n**{poll.total_votes}** votes • **{poll.state}**",
            inline=False,
        )
    embed.add_field(
        name="Custom sender",
        value=(f"✅ {config.sender_name or 'Dank Shield'}" if config.use_webhook else "Off • normal Dank Shield message"),
        inline=True,
    )
    embed.add_field(name="Repeated pings", value="Suppressed", inline=True)
    embed.set_footer(text="Preview/test is private until you deliberately post a temporary test or publish a draft")
    return embed


def _sticky_settings_embed(config: StickyConfig) -> discord.Embed:
    state = "Active" if config.enabled else "Paused"
    embed = discord.Embed(
        title="⚙️ Sticky Settings",
        description=f"Settings for <#{config.channel_id}> • **{state}** • **{config.mode}**",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Movement",
        value=(
            f"On human activity: eligible after **{config.interval_seconds}s**, or immediately after "
            f"**{config.message_threshold}** new human messages."
        ),
        inline=False,
    )
    embed.add_field(
        name="Sender",
        value=(f"Custom sender: **{config.sender_name or 'Dank Shield'}**" if config.use_webhook else "Normal Dank Shield message"),
        inline=False,
    )
    if config.mode == "poll":
        embed.add_field(
            name="Sticky polls",
            value="Custom Sender is unavailable because the bot must own persistent vote buttons.",
            inline=False,
        )
    embed.set_footer(text="Pause keeps the current copy visible but stops movement; Remove deletes saved state and the managed live copy")
    return embed


async def _current_sticky(interaction: discord.Interaction) -> tuple[Optional[StickyConfig], Optional[StickyPoll]]:
    channel = _text_channel(interaction)
    if channel is None:
        return None, None
    config = await get_sticky(int(channel.id))
    poll = await get_sticky_poll(int(channel.id)) if config and config.mode == "poll" else None
    return config, poll


async def _open_sticky_center(interaction: discord.Interaction, *, replace_message: bool = True) -> None:
    if not _manage_messages(interaction):
        return await _private(interaction, "❌ Sticky management requires **Manage Messages in this channel**.")
    if _text_channel(interaction) is None:
        return await _private(interaction, "❌ Configure stickies inside a normal text channel.")
    try:
        config, poll = await _current_sticky(interaction)
    except CommunityStorageUnavailable:
        return await _private(interaction, "❌ Community Tools storage is unavailable. Apply the Community Tools migrations first.")
    embed = _sticky_status_embed(config, poll)
    view = StickyCenterView(int(interaction.user.id), config=config, poll=poll)
    if replace_message and not interaction.response.is_done():
        await _replace(interaction, embed=embed, view=view)
    else:
        await _private(interaction, embed=embed, view=view)


class CommunityToolsView(_OwnedView):
    @discord.ui.button(label="Sticky Messages", emoji="📌", style=discord.ButtonStyle.primary, row=0)
    async def sticky(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_sticky_center(interaction)

    @discord.ui.button(label="Create Poll", emoji="📊", style=discord.ButtonStyle.primary, row=0)
    async def poll(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if _text_channel(interaction) is None:
            return await _private(interaction, "❌ Create polls inside a normal text channel.")
        if not _can_create_poll(interaction):
            return await _private(interaction, "❌ You need this channel's **Send Messages + poll permission** to create a poll.")
        await interaction.response.send_modal(NativePollModal())

    @discord.ui.button(label="Embed Builder", emoji="🧱", style=discord.ButtonStyle.primary, row=0)
    async def embed_builder(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _manage_messages(interaction):
            return await _private(interaction, "❌ Embed Builder requires **Manage Messages in this channel**.")
        await interaction.response.send_modal(EmbedBuilderModal())

    @discord.ui.button(label="Member / Server Info", emoji="👤", style=discord.ButtonStyle.secondary, row=1)
    async def info(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(
            interaction,
            embed=discord.Embed(
                title="👤 Member / Server Info",
                description="Pick a member for useful account/server facts, or open the server summary.",
                color=discord.Color.blurple(),
            ),
            view=InfoView(self.owner_id),
        )

    @discord.ui.button(label="Permission Check", emoji="🔐", style=discord.ButtonStyle.secondary, row=1)
    async def permissions(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await show_permission_check(interaction)

    @discord.ui.button(label="Fun & Lookup", emoji="🎲", style=discord.ButtonStyle.secondary, row=1)
    async def fun(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, embed=_fun_embed(), view=FunLookupView(self.owner_id))

    @discord.ui.button(label="Dank Shield Home", emoji="🏠", style=discord.ButtonStyle.secondary, row=2)
    async def home(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_command_surface_v2 import CompactDankHomeView, _home_embed

        await _replace(interaction, embed=_home_embed(), view=CompactDankHomeView(self.owner_id))

    @discord.ui.button(label="Close", emoji="✖️", style=discord.ButtonStyle.danger, row=2)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, content="Community Tools closed.", embed=None, view=None)


class StickyCenterView(_OwnedView):
    def __init__(self, owner_id: int, *, config: Optional[StickyConfig], poll: Optional[StickyPoll]) -> None:
        super().__init__(owner_id)
        self.config = config
        self.poll = poll
        for item in self.children:
            label = str(getattr(item, "label", "") or "")
            if config is None and label in {"Preview / Test", "Sticky Settings"}:
                item.disabled = True
            if label == "Sticky Poll" and config is not None and config.mode == "poll" and poll is not None:
                item.label = "Poll Controls"
                item.emoji = "🗳️"

    @discord.ui.button(label="Create / Edit", emoji="✏️", style=discord.ButtonStyle.primary, row=0)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        embed = discord.Embed(
            title="✏️ Choose Sticky Type",
            description="Choose a normal message or rich embed. Nothing live changes until you preview and publish the draft.",
            color=discord.Color.blurple(),
        )
        if self.config is not None and self.config.mode == "poll":
            embed.add_field(
                name="Current sticky is a poll",
                value="Publishing a Message/Embed draft will atomically replace the poll and remove its saved vote state.",
                inline=False,
            )
        await _replace(interaction, embed=embed, view=StickyTypeView(self.owner_id, self.config, self.poll))

    @discord.ui.button(label="Preview / Test", emoji="👁️", style=discord.ButtonStyle.secondary, row=0)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await show_sticky_preview(interaction, self.config, self.poll)

    @discord.ui.button(label="Sticky Settings", emoji="⚙️", style=discord.ButtonStyle.secondary, row=0)
    async def settings(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if self.config is None:
            return await _private(interaction, "❌ Create a sticky first.")
        await _replace(
            interaction,
            embed=_sticky_settings_embed(self.config),
            view=StickySettingsView(self.owner_id, self.config, self.poll),
        )

    @discord.ui.button(label="Sticky Poll", emoji="📊", style=discord.ButtonStyle.success, row=1)
    async def sticky_poll(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if self.config is not None and self.config.mode == "poll" and self.poll is not None:
            return await _replace(
                interaction,
                embed=sticky_poll_embed(self.poll),
                view=StickyPollControlView(self.owner_id, self.config, self.poll),
            )
        await interaction.response.send_modal(StickyPollModal(self.poll))

    @discord.ui.button(label="Quiet Server Notice", emoji="🌙", style=discord.ButtonStyle.success, row=1)
    async def quiet_notice(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await open_quiet_notice_center(interaction)

    @discord.ui.button(label="Server Stickies", emoji="📋", style=discord.ButtonStyle.secondary, row=1)
    async def list_server(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if interaction.guild is None:
            return await _private(interaction, "❌ Use this in a server.")
        try:
            rows = await list_stickies(guild_id=int(interaction.guild.id))
        except CommunityStorageUnavailable:
            return await _private(interaction, "❌ Sticky storage is unavailable.")
        await _private(
            interaction,
            embed=_server_stickies_embed(rows, 0),
            view=ServerStickiesView(self.owner_id, rows, page=0),
        )

    @discord.ui.button(label="Community Tools", emoji="🧰", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, embed=_center_embed(), view=CommunityToolsView(self.owner_id))


class StickyTypeView(_OwnedView):
    def __init__(self, owner_id: int, config: Optional[StickyConfig], poll: Optional[StickyPoll]) -> None:
        super().__init__(owner_id, timeout=300)
        self.config = config
        self.poll = poll

    @discord.ui.button(label="Message Sticky", emoji="💬", style=discord.ButtonStyle.primary)
    async def plain(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(StickyMessageModal(self.config, self.poll))

    @discord.ui.button(label="Embed Sticky", emoji="🧱", style=discord.ButtonStyle.primary)
    async def embed(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(StickyEmbedModal(self.config, self.poll))

    @discord.ui.button(label="Back", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(
            interaction,
            embed=_sticky_status_embed(self.config, self.poll),
            view=StickyCenterView(self.owner_id, config=self.config, poll=self.poll),
        )


class StickySettingsView(_OwnedView):
    def __init__(self, owner_id: int, config: StickyConfig, poll: Optional[StickyPoll]) -> None:
        super().__init__(owner_id)
        self.config = config
        self.poll = poll
        if config.mode == "poll":
            for item in self.children:
                if str(getattr(item, "label", "") or "") == "Custom Sender":
                    item.disabled = True

    @discord.ui.button(label="Pause / Resume", emoji="⏯️", style=discord.ButtonStyle.secondary, row=0)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        channel = _text_channel(interaction)
        if channel is None:
            return await _private(interaction, "❌ This needs a text channel.")
        try:
            current = await get_sticky(int(channel.id))
            if current is None:
                return await _private(interaction, "❌ There is no sticky in this channel.")
            saved = await set_sticky_enabled(int(channel.id), not current.enabled, actor_id=int(interaction.user.id))
        except CommunityStorageUnavailable:
            return await _private(interaction, "❌ Sticky storage is unavailable.")
        runtime = ensure_community_tools_runtime(interaction.client)
        if saved is not None:
            runtime.set_config(saved)
            if saved.enabled:
                await runtime.refresh_channel(channel, force=True)
        await _open_sticky_center(interaction, replace_message=False)

    @discord.ui.button(label="Speed / Cadence", emoji="⏱️", style=discord.ButtonStyle.secondary, row=0)
    async def speed(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(StickySpeedModal(self.config))

    @discord.ui.button(label="Custom Sender", emoji="🎭", style=discord.ButtonStyle.secondary, row=0)
    async def persona(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if self.config.mode == "poll":
            return await _private(interaction, "ℹ️ Sticky polls stay bot-owned so their persistent vote buttons remain reliable.")
        if not _manage_webhooks(interaction):
            return await _private(interaction, "❌ Custom Sender requires **Manage Webhooks in this channel**.")
        await interaction.response.send_modal(StickyPersonaModal(self.config))

    @discord.ui.button(label="Remove", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(
            interaction,
            embed=discord.Embed(
                title="Remove sticky?",
                description="This deletes the saved sticky, managed live copy, and sticky-poll state for this channel. Quiet Server Notice is separate.",
                color=discord.Color.red(),
            ),
            view=StickyRemoveConfirmView(self.owner_id, self.config),
        )

    @discord.ui.button(label="Back to Sticky", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(
            interaction,
            embed=_sticky_status_embed(self.config, self.poll),
            view=StickyCenterView(self.owner_id, config=self.config, poll=self.poll),
        )


class StickyRemoveConfirmView(_OwnedView):
    def __init__(self, owner_id: int, config: StickyConfig) -> None:
        super().__init__(owner_id, timeout=120)
        self.config = config

    @discord.ui.button(label="Remove Sticky", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        try:
            current = await get_sticky(int(self.config.channel_id))
            if current is None:
                ensure_community_tools_runtime(interaction.client).remove_config(int(self.config.channel_id))
                return await _replace(
                    interaction,
                    embed=_sticky_status_embed(None),
                    view=StickyCenterView(self.owner_id, config=None, poll=None),
                )
            await delete_sticky(int(current.channel_id))
        except CommunityStorageUnavailable:
            return await _private(interaction, "❌ Sticky storage is unavailable, so the live sticky was left untouched.")
        runtime = ensure_community_tools_runtime(interaction.client)
        channel = interaction.client.get_channel(int(current.channel_id))
        if isinstance(channel, discord.TextChannel):
            await runtime.delete_live_message(channel, current.last_message_id)
            if current.use_webhook:
                await runtime.cleanup_managed_webhook(channel)
        runtime.remove_config(int(current.channel_id))
        await _replace(
            interaction,
            embed=_sticky_status_embed(None),
            view=StickyCenterView(self.owner_id, config=None, poll=None),
        )

    @discord.ui.button(label="Cancel", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_sticky_center(interaction)


class StickyMessageModal(discord.ui.Modal, title="Message sticky"):
    message = discord.ui.TextInput(
        label="Message text",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1900,
        placeholder="Text shown in the sticky.",
    )

    def __init__(self, current: Optional[StickyConfig], current_poll: Optional[StickyPoll] = None) -> None:
        super().__init__()
        self.current = current
        self.current_poll = current_poll
        if current is not None and current.mode == "plain":
            self.message.default = current.content

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _manage_messages(interaction):
            return await _private(interaction, "❌ Sticky management requires **Manage Messages in this channel**.")
        channel = _text_channel(interaction)
        if channel is None or interaction.guild is None:
            return await _private(interaction, "❌ Configure stickies inside a normal text channel.")
        base = self.current or StickyConfig(guild_id=int(interaction.guild.id), channel_id=int(channel.id))
        config = replace(
            base,
            guild_id=int(interaction.guild.id),
            channel_id=int(channel.id),
            content=str(self.message.value or ""),
            mode="plain",
            title="",
            image_url="",
            thumbnail_url="",
            use_webhook=bool(base.use_webhook and base.mode != "poll"),
            updated_by=int(interaction.user.id),
        )
        await show_sticky_draft_preview(
            interaction,
            config,
            baseline=self.current,
            baseline_poll=self.current_poll,
        )


class StickyEmbedModal(discord.ui.Modal, title="Embed sticky"):
    embed_title = discord.ui.TextInput(label="Title (optional)", required=False, max_length=256)
    message = discord.ui.TextInput(label="Description (optional)", style=discord.TextStyle.paragraph, required=False, max_length=1900)
    color = discord.ui.TextInput(label="Hex color", required=True, max_length=8, default="5865F2")
    image_url = discord.ui.TextInput(label="Large image HTTPS URL (optional)", required=False, max_length=1000)
    thumbnail_url = discord.ui.TextInput(label="Thumbnail HTTPS URL (optional)", required=False, max_length=1000)

    def __init__(self, current: Optional[StickyConfig], current_poll: Optional[StickyPoll] = None) -> None:
        super().__init__()
        self.current = current
        self.current_poll = current_poll
        if current is not None and current.mode == "embed":
            self.embed_title.default = current.title
            self.message.default = current.content
            self.color.default = f"{int(current.color):06X}"
            self.image_url.default = current.image_url
            self.thumbnail_url.default = current.thumbnail_url

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _manage_messages(interaction):
            return await _private(interaction, "❌ Sticky management requires **Manage Messages in this channel**.")
        channel = _text_channel(interaction)
        if channel is None or interaction.guild is None:
            return await _private(interaction, "❌ Configure stickies inside a normal text channel.")
        try:
            color = _parse_color(str(self.color.value))
        except InvalidCommunityToolValue as exc:
            return await _private(interaction, f"❌ {exc}")
        base = self.current or StickyConfig(guild_id=int(interaction.guild.id), channel_id=int(channel.id))
        config = replace(
            base,
            guild_id=int(interaction.guild.id),
            channel_id=int(channel.id),
            content=str(self.message.value or ""),
            mode="embed",
            title=str(self.embed_title.value or ""),
            color=color,
            image_url=str(self.image_url.value or ""),
            thumbnail_url=str(self.thumbnail_url.value or ""),
            use_webhook=bool(base.use_webhook and base.mode != "poll"),
            updated_by=int(interaction.user.id),
        )
        await show_sticky_draft_preview(
            interaction,
            config,
            baseline=self.current,
            baseline_poll=self.current_poll,
        )


class StickySpeedModal(discord.ui.Modal, title="Sticky speed / cadence"):
    seconds = discord.ui.TextInput(
        label=f"Elapsed seconds ({MIN_INTERVAL_SECONDS}-{MAX_INTERVAL_SECONDS})",
        required=True,
        max_length=4,
    )
    messages = discord.ui.TextInput(
        label=f"Human messages ({MIN_MESSAGE_THRESHOLD}-{MAX_MESSAGE_THRESHOLD})",
        required=True,
        max_length=3,
    )

    def __init__(self, current: StickyConfig) -> None:
        super().__init__()
        self.current = current
        self.seconds.default = str(current.interval_seconds)
        self.messages.default = str(current.message_threshold)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            seconds = int(str(self.seconds.value).strip())
            messages = int(str(self.messages.value).strip())
        except ValueError:
            return await _private(interaction, "❌ Enter whole numbers for both cadence fields.")
        if not MIN_INTERVAL_SECONDS <= seconds <= MAX_INTERVAL_SECONDS:
            return await _private(interaction, f"❌ Seconds must be {MIN_INTERVAL_SECONDS}-{MAX_INTERVAL_SECONDS}; the value was not changed.")
        if not MIN_MESSAGE_THRESHOLD <= messages <= MAX_MESSAGE_THRESHOLD:
            return await _private(interaction, f"❌ Message count must be {MIN_MESSAGE_THRESHOLD}-{MAX_MESSAGE_THRESHOLD}; the value was not changed.")
        try:
            current = await get_sticky(int(self.current.channel_id))
            if current is None:
                return await _private(interaction, "❌ This sticky was removed while the cadence editor was open.")
            config = replace(
                current,
                interval_seconds=seconds,
                message_threshold=messages,
                updated_by=int(interaction.user.id),
            )
            saved = await save_sticky(config)
            poll = await get_sticky_poll(int(saved.channel_id)) if saved.mode == "poll" else None
        except (InvalidCommunityToolValue, CommunityStorageUnavailable) as exc:
            return await _private(interaction, f"❌ {exc}")
        ensure_community_tools_runtime(interaction.client).set_config(saved)
        await _private(
            interaction,
            "✅ Sticky cadence updated exactly as entered without overwriting newer sticky content or sender settings.",
            embed=_sticky_status_embed(saved, poll),
            view=StickyCenterView(int(interaction.user.id), config=saved, poll=poll),
        )


class StickyPersonaModal(discord.ui.Modal, title="Custom sticky sender"):
    enabled = discord.ui.TextInput(label="Enable custom sender? yes/no", required=True, max_length=3, default="yes")
    name = discord.ui.TextInput(label="Sender name", required=False, max_length=80)
    avatar = discord.ui.TextInput(label="Avatar HTTPS URL (optional)", required=False, max_length=1000)

    def __init__(self, current: StickyConfig) -> None:
        super().__init__()
        self.current = current
        self.enabled.default = "yes" if current.use_webhook else "no"
        self.name.default = current.sender_name
        self.avatar.default = current.sender_avatar_url

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _manage_webhooks(interaction):
            return await _private(interaction, "❌ Custom Sender requires **Manage Webhooks in this channel**.")
        choice = str(self.enabled.value).strip().lower()
        if choice not in {"y", "yes", "n", "no"}:
            return await _private(interaction, "❌ Enter `yes` or `no`; the sender setting was not changed.")
        enabled = choice in {"y", "yes"}
        channel = _text_channel(interaction)
        if channel is None:
            return await _private(interaction, "❌ Configure the sender inside its text channel.")
        try:
            current = await get_sticky(int(channel.id))
        except CommunityStorageUnavailable:
            return await _private(interaction, "❌ Sticky storage is unavailable.")
        if current is None:
            return await _private(interaction, "❌ This sticky was removed while the sender editor was open.")
        if current.mode == "poll":
            return await _private(interaction, "❌ This sticky is now a poll; Custom Sender is unavailable for poll buttons.")

        runtime = ensure_community_tools_runtime(interaction.client)
        created_for_enable = bool(enabled and not current.use_webhook)
        if enabled and not await runtime.ensure_managed_webhook(channel):
            return await _private(
                interaction,
                "❌ Dank Shield needs **Manage Webhooks + Manage Messages** in this channel for a reliable custom sender. Nothing was changed.",
            )
        config = replace(
            current,
            use_webhook=enabled,
            sender_name=str(self.name.value or "") if enabled else "",
            sender_avatar_url=str(self.avatar.value or "") if enabled else "",
            updated_by=int(interaction.user.id),
        )
        try:
            saved = await save_sticky(config)
        except (InvalidCommunityToolValue, CommunityStorageUnavailable) as exc:
            if created_for_enable:
                await runtime.cleanup_managed_webhook(channel)
            return await _private(interaction, f"❌ {exc}")
        runtime.set_config(saved)
        posted = await runtime.refresh_channel(channel, force=True) if saved.enabled else None
        if not enabled:
            await runtime.cleanup_managed_webhook(channel)
        note = "✅ Custom Sender settings saved. No webhook URL/token is stored."
        if saved.enabled and posted is None:
            note += " The setting is durable, but the replacement could not be posted; Permission Check will show the blocker."
        await _private(
            interaction,
            note,
            embed=_sticky_status_embed(saved),
            view=StickyCenterView(int(interaction.user.id), config=saved, poll=None),
        )


class StickyPollModal(discord.ui.Modal, title="Create or edit sticky poll"):
    question = discord.ui.TextInput(label="Poll question", required=True, max_length=300)
    choices = discord.ui.TextInput(
        label="Choices — one per line (2-7)",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=560,
        placeholder="Choice one\nChoice two",
    )

    def __init__(self, current: Optional[StickyPoll]) -> None:
        super().__init__()
        self.current = current
        if current is not None:
            self.question.default = current.question
            self.choices.default = "\n".join(current.options)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _manage_messages(interaction):
            return await _private(interaction, "❌ Sticky polls require **Manage Messages in this channel**.")
        channel = _text_channel(interaction)
        if channel is None or interaction.guild is None:
            return await _private(interaction, "❌ Create sticky polls inside a normal text channel.")
        options = tuple(line.strip() for line in str(self.choices.value).splitlines() if line.strip())
        try:
            existing = await get_sticky(int(channel.id))
            live_poll = await get_sticky_poll(int(channel.id)) if existing is not None and existing.mode == "poll" else None
            if _draft_is_stale(existing if self.current is not None else None, existing, self.current, live_poll):
                # The sticky row comparison above intentionally collapses to itself for
                # existing poll edits; poll design is the authority we need here.
                raise InvalidCommunityToolValue("The sticky poll changed while this editor was open. Reopen it before saving a draft.")
            if self.current is None and live_poll is not None:
                raise InvalidCommunityToolValue("A sticky poll was created while this editor was open. Reopen Community Tools before replacing it.")
            base_votes = dict(live_poll.votes) if live_poll is not None and tuple(live_poll.options) == options else {}
            base_state = live_poll.state if live_poll is not None else "active"
            poll = StickyPoll(
                guild_id=int(interaction.guild.id),
                channel_id=int(channel.id),
                question=str(self.question.value),
                options=options,
                votes=base_votes,
                state=base_state,
                updated_by=int(interaction.user.id),
            )
            safe_poll = normalize_poll(poll)
        except (InvalidCommunityToolValue, CommunityStorageUnavailable) as exc:
            return await _private(interaction, f"❌ {exc}")
        draft_sticky = StickyConfig(
            guild_id=int(interaction.guild.id),
            channel_id=int(channel.id),
            enabled=existing.enabled if existing else True,
            content=safe_poll.question,
            mode="poll",
            interval_seconds=existing.interval_seconds if existing else 15,
            message_threshold=existing.message_threshold if existing else 5,
            updated_by=int(interaction.user.id),
            last_message_id=existing.last_message_id if existing else None,
            last_sent_at=existing.last_sent_at if existing else None,
        )
        await _private(
            interaction,
            "👁️ **Sticky poll draft** — live votes/state are preserved when the choices still match, and newer edits will not be overwritten.",
            embed=sticky_poll_embed(safe_poll),
            view=StickyPollDraftView(
                int(interaction.user.id),
                draft_sticky,
                safe_poll,
                baseline_sticky=existing,
                baseline_poll=live_poll,
            ),
        )


class StickyPollDraftView(_OwnedView):
    def __init__(
        self,
        owner_id: int,
        sticky: StickyConfig,
        poll: StickyPoll,
        *,
        baseline_sticky: Optional[StickyConfig],
        baseline_poll: Optional[StickyPoll],
    ) -> None:
        super().__init__(owner_id, timeout=300)
        self.sticky = sticky
        self.poll = poll
        self.baseline_sticky = baseline_sticky
        self.baseline_poll = baseline_poll

    @discord.ui.button(label="Publish Sticky Poll", emoji="✅", style=discord.ButtonStyle.success)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _manage_messages(interaction):
            return await _private(interaction, "❌ Publishing requires **Manage Messages in this channel**.")
        channel = _text_channel(interaction)
        if channel is None or int(channel.id) != int(self.sticky.channel_id):
            return await _private(interaction, "❌ Reopen Community Tools in this poll's destination channel.")
        if not _bot_can_post(channel, embed=True):
            return await _private(interaction, "❌ Dank Shield needs **View Channel + Send Messages + Embed Links** here.")
        try:
            current = await get_sticky(int(channel.id))
            current_poll = await get_sticky_poll(int(channel.id)) if current is not None and current.mode == "poll" else None
            if _draft_is_stale(self.baseline_sticky, current, self.baseline_poll, current_poll):
                raise InvalidCommunityToolValue(
                    "This poll draft is stale because another Community Tools change modified the live poll while you were reviewing it. Reopen the editor first."
                )
            publish_poll = replace(
                self.poll,
                votes=(dict(current_poll.votes) if current_poll is not None and tuple(current_poll.options) == tuple(self.poll.options) else {}),
                state=current_poll.state if current_poll is not None else self.poll.state,
                updated_by=int(interaction.user.id),
            )
            sticky = replace(
                self.sticky,
                enabled=current.enabled if current else True,
                interval_seconds=current.interval_seconds if current else self.sticky.interval_seconds,
                message_threshold=current.message_threshold if current else self.sticky.message_threshold,
                last_message_id=current.last_message_id if current else None,
                last_sent_at=current.last_sent_at if current else None,
                updated_by=int(interaction.user.id),
            )
            saved_sticky, saved_poll = await save_sticky_bundle(sticky, publish_poll)
        except (InvalidCommunityToolValue, CommunityStorageUnavailable) as exc:
            return await _private(interaction, f"❌ {exc}")
        if saved_poll is None:
            return await _private(interaction, "❌ Poll state was not returned after the atomic save.")
        runtime = ensure_community_tools_runtime(interaction.client)
        runtime.set_config(saved_sticky)
        posted = await runtime.refresh_channel(channel, force=True) if saved_sticky.enabled else None
        message = "✅ Sticky poll published." if posted is not None or not saved_sticky.enabled else (
            "⚠️ Sticky poll state was saved atomically, but Dank Shield could not post the live replacement. Check Permission Check."
        )
        await _replace(
            interaction,
            content=message,
            embed=_sticky_status_embed(saved_sticky, saved_poll),
            view=StickyCenterView(self.owner_id, config=saved_sticky, poll=saved_poll),
        )
        self.stop()

    @discord.ui.button(label="Discard Draft", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def discard(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, content="Draft discarded. Live sticky/poll state was not changed.", embed=None, view=None)
        self.stop()


class StickyPollControlView(_OwnedView):
    def __init__(self, owner_id: int, config: StickyConfig, poll: StickyPoll) -> None:
        super().__init__(owner_id)
        self.config = config
        self.poll = poll

    async def _apply(self, interaction: discord.Interaction, action: str) -> None:
        try:
            if action == "reset":
                poll = await reset_sticky_poll(int(self.poll.channel_id), actor_id=int(interaction.user.id))
            else:
                poll = await set_sticky_poll_state(int(self.poll.channel_id), action, actor_id=int(interaction.user.id))
        except (InvalidCommunityToolValue, CommunityStorageUnavailable) as exc:
            return await _private(interaction, f"❌ {exc}")
        channel = _text_channel(interaction)
        runtime = ensure_community_tools_runtime(interaction.client)
        if channel is not None:
            await runtime.refresh_channel(channel, force=True)
        await _replace(
            interaction,
            embed=sticky_poll_embed(poll),
            view=StickyPollControlView(self.owner_id, self.config, poll),
        )

    @discord.ui.button(label="Resume", emoji="▶️", style=discord.ButtonStyle.success, row=0)
    async def resume(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._apply(interaction, "active")

    @discord.ui.button(label="Pause", emoji="⏸️", style=discord.ButtonStyle.secondary, row=0)
    async def pause(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._apply(interaction, "paused")

    @discord.ui.button(label="Reset Votes", emoji="🔄", style=discord.ButtonStyle.secondary, row=0)
    async def reset(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._apply(interaction, "reset")

    @discord.ui.button(label="End Poll", emoji="🛑", style=discord.ButtonStyle.danger, row=0)
    async def end(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await self._apply(interaction, "ended")

    @discord.ui.button(label="Sticky Center", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _open_sticky_center(interaction)


def _normalize_native_poll_choices(raw: str) -> list[str]:
    options: list[str] = []
    seen: set[str] = set()
    for line in str(raw or "").splitlines():
        value = " ".join(line.split()).strip()[:55]
        key = value.casefold()
        if value and key not in seen:
            options.append(value)
            seen.add(key)
    if not 2 <= len(options) <= 7:
        raise InvalidCommunityToolValue("Polls need 2 to 7 unique visible choices.")
    return options


class NativePollModal(discord.ui.Modal, title="Create Discord poll"):
    question = discord.ui.TextInput(label="Question", required=True, max_length=300)
    choices = discord.ui.TextInput(label="Choices — one per line (2-7)", style=discord.TextStyle.paragraph, required=True, max_length=400)
    hours = discord.ui.TextInput(label="Duration in hours (1-168)", required=True, max_length=3, default="24")
    multiple = discord.ui.TextInput(label="Allow multiple choices? yes/no", required=True, max_length=3, default="no")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        channel = _text_channel(interaction)
        if channel is None:
            return await _private(interaction, "❌ Create polls inside a normal text channel.")
        if not _can_create_poll(interaction):
            return await _private(interaction, "❌ You no longer have this channel's Send Messages + poll permission.")
        try:
            options = _normalize_native_poll_choices(str(self.choices.value))
            duration_hours = int(str(self.hours.value).strip())
        except ValueError:
            return await _private(interaction, "❌ Duration must be a whole number from 1 to 168.")
        except InvalidCommunityToolValue as exc:
            return await _private(interaction, f"❌ {exc}")
        if not 1 <= duration_hours <= 168:
            return await _private(interaction, "❌ Duration must be 1-168 hours; the value was not silently changed.")
        multiple = str(self.multiple.value).strip().lower()
        if multiple not in {"y", "yes", "n", "no"}:
            return await _private(interaction, "❌ For multiple choices, enter `yes` or `no`.")
        allow_multiple = multiple in {"y", "yes"}
        preview = discord.Embed(title="📊 Poll Preview", description=str(self.question.value), color=discord.Color.blurple())
        preview.add_field(name="Choices", value="\n".join(f"**{i + 1}.** {value}" for i, value in enumerate(options)), inline=False)
        preview.add_field(name="Duration", value=f"{duration_hours} hour{'s' if duration_hours != 1 else ''}", inline=True)
        preview.add_field(name="Multiple choices", value="Yes" if allow_multiple else "No", inline=True)
        await _private(
            interaction,
            "👁️ Private preview. Discord does not receive the poll until you press **Publish Poll**.",
            embed=preview,
            view=NativePollPreviewView(
                int(interaction.user.id), int(channel.id), str(self.question.value), options, duration_hours, allow_multiple
            ),
        )


class NativePollPreviewView(_OwnedView):
    def __init__(
        self,
        owner_id: int,
        channel_id: int,
        question: str,
        options: Sequence[str],
        duration_hours: int,
        allow_multiple: bool,
    ) -> None:
        super().__init__(owner_id, timeout=300)
        self.channel_id = int(channel_id)
        self.question = question
        self.options = tuple(options)
        self.duration_hours = int(duration_hours)
        self.allow_multiple = bool(allow_multiple)

    @discord.ui.button(label="Publish Poll", emoji="✅", style=discord.ButtonStyle.success)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        channel = interaction.client.get_channel(self.channel_id)
        if not isinstance(channel, discord.TextChannel):
            return await _private(interaction, "❌ Poll destination no longer exists or is unavailable.")
        member = interaction.user
        if not isinstance(member, discord.Member):
            return await _private(interaction, "❌ Member permissions could not be resolved.")
        member_perms = channel.permissions_for(member)
        if not (member_perms.view_channel and member_perms.send_messages and _poll_permission(member_perms)):
            return await _private(interaction, "❌ You no longer have permission to post polls in the destination channel.")
        if not _bot_can_post(channel, poll=True):
            return await _private(interaction, "❌ Dank Shield lacks Send Messages or poll permission in the destination channel.")
        try:
            poll = discord.Poll(self.question, timedelta(hours=self.duration_hours), multiple=self.allow_multiple)
            for option in self.options:
                poll.add_answer(text=option)
            message = await channel.send(poll=poll, allowed_mentions=_ALLOWED_MENTIONS)
        except (discord.HTTPException, discord.Forbidden, discord.ClientException, ValueError):
            return await _private(interaction, "❌ Discord rejected that poll. Nothing else was changed.")
        await _replace(
            interaction,
            content=f"✅ Poll posted in <#{channel.id}>: {message.jump_url}",
            embed=None,
            view=None,
        )
        self.stop()

    @discord.ui.button(label="Discard", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def discard(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, content="Poll draft discarded.", embed=None, view=None)
        self.stop()


def _parse_color(value: str) -> int:
    raw = str(value or "").strip().lower().removeprefix("#").removeprefix("0x")
    if not raw:
        return 0x5865F2
    if len(raw) != 6:
        raise InvalidCommunityToolValue("Color must be exactly six hex digits, such as `5865F2`.")
    try:
        number = int(raw, 16)
    except ValueError as exc:
        raise InvalidCommunityToolValue("Color must be a six-digit hex value such as `5865F2`.") from exc
    return number


class EmbedBuilderModal(discord.ui.Modal, title="Build an embed"):
    embed_title = discord.ui.TextInput(label="Title", required=False, max_length=256)
    description = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, required=True, max_length=4000)
    color = discord.ui.TextInput(label="Hex color", required=False, max_length=8, default="5865F2")
    image = discord.ui.TextInput(label="Large image HTTPS URL (optional)", required=False, max_length=1000)
    thumbnail = discord.ui.TextInput(label="Thumbnail HTTPS URL (optional)", required=False, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _manage_messages(interaction):
            return await _private(interaction, "❌ Embed Builder requires **Manage Messages in this channel**.")
        channel = _text_channel(interaction)
        if channel is None:
            return await _private(interaction, "❌ Build embeds inside a normal text channel.")
        try:
            color = _parse_color(str(self.color.value))
            image = normalize_https_url(self.image.value)
            thumbnail = normalize_https_url(self.thumbnail.value)
        except InvalidCommunityToolValue as exc:
            return await _private(interaction, f"❌ {exc}")
        embed = discord.Embed(
            title=str(self.embed_title.value or "") or None,
            description=str(self.description.value),
            color=discord.Color(color),
        )
        if image:
            embed.set_image(url=image)
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        embed.set_footer(text=f"Prepared with Dank Shield by {interaction.user}")
        await _private(
            interaction,
            "👁️ **Private embed preview** — review it before publishing.",
            embed=embed,
            view=EmbedDraftView(int(interaction.user.id), int(channel.id), embed),
        )


class EmbedDraftView(_OwnedView):
    def __init__(self, owner_id: int, channel_id: int, embed: discord.Embed) -> None:
        super().__init__(owner_id, timeout=300)
        self.channel_id = int(channel_id)
        self.embed = embed

    async def _channel(self, interaction: discord.Interaction) -> Optional[discord.TextChannel]:
        channel = interaction.client.get_channel(self.channel_id)
        return channel if isinstance(channel, discord.TextChannel) else None

    @discord.ui.button(label="Publish Embed", emoji="✅", style=discord.ButtonStyle.success)
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        channel = await self._channel(interaction)
        if channel is None:
            return await _private(interaction, "❌ Embed destination is unavailable.")
        member = interaction.user
        if not isinstance(member, discord.Member) or not channel.permissions_for(member).manage_messages:
            return await _private(interaction, "❌ You no longer have Manage Messages in the destination channel.")
        if not _bot_can_post(channel, embed=True):
            return await _private(interaction, "❌ Dank Shield needs View Channel, Send Messages, and Embed Links there.")
        try:
            message = await channel.send(embed=self.embed, allowed_mentions=_ALLOWED_MENTIONS)
        except (discord.Forbidden, discord.HTTPException):
            return await _private(interaction, "❌ Dank Shield could not publish the embed.")
        await _replace(interaction, content=f"✅ Embed posted: {message.jump_url}", embed=None, view=None)
        self.stop()

    @discord.ui.button(label="Post 30s Test", emoji="🧪", style=discord.ButtonStyle.primary)
    async def test(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        channel = await self._channel(interaction)
        if channel is None or not _bot_can_post(channel, embed=True):
            return await _private(interaction, "❌ Dank Shield cannot post the temporary test in the destination channel.")
        try:
            await channel.send(embed=self.embed, allowed_mentions=_ALLOWED_MENTIONS, delete_after=30)
        except (discord.Forbidden, discord.HTTPException):
            return await _private(interaction, "❌ Temporary embed test failed.")
        await _private(interaction, f"✅ Temporary embed posted in <#{channel.id}> for 30 seconds.")

    @discord.ui.button(label="Discard", emoji="✖️", style=discord.ButtonStyle.secondary)
    async def discard(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, content="Embed draft discarded.", embed=None, view=None)
        self.stop()


class InfoMemberSelect(discord.ui.UserSelect):
    def __init__(self) -> None:
        super().__init__(placeholder="Choose a member", min_values=1, max_values=1, custom_id="dank:community:info:user:v2", row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.view
        if not isinstance(view, InfoView) or not await view.interaction_check(interaction):
            return
        target = self.values[0]
        guild = interaction.guild
        member = guild.get_member(int(target.id)) if guild is not None else None
        user = member or target
        embed = discord.Embed(
            title=f"👤 {getattr(user, 'display_name', str(user))}",
            color=getattr(member, "color", discord.Color.blurple()) if member else discord.Color.blurple(),
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.add_field(name="User ID", value=f"`{user.id}`", inline=True)
        embed.add_field(name="Account created", value=discord.utils.format_dt(user.created_at, style="R"), inline=True)
        embed.add_field(name="Account type", value="Bot" if bool(getattr(user, "bot", False)) else "Member", inline=True)
        if member is not None:
            embed.add_field(name="Joined server", value=discord.utils.format_dt(member.joined_at, style="R") if member.joined_at else "Unknown", inline=True)
            embed.add_field(name="Top role", value=member.top_role.mention if member.top_role else "None", inline=True)
            visible_roles = [role.mention for role in member.roles[1:] if not role.managed]
            shown = visible_roles[-10:]
            role_text = " ".join(shown) if shown else "No extra roles"
            if len(visible_roles) > len(shown):
                role_text += f"\n+{len(visible_roles) - len(shown)} more"
            embed.add_field(name=f"Roles ({len(visible_roles)})", value=role_text, inline=False)
        embed.set_footer(text="This view only shows ordinary Discord server/account metadata")
        await _replace(interaction, embed=embed, view=InfoView(view.owner_id))


class InfoView(_OwnedView):
    def __init__(self, owner_id: int) -> None:
        super().__init__(owner_id)
        self.add_item(InfoMemberSelect())

    @discord.ui.button(label="Server Info", emoji="🏠", style=discord.ButtonStyle.primary, row=1)
    async def server(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        guild = interaction.guild
        if guild is None:
            return await _private(interaction, "❌ Use this in a server.")
        humans = sum(1 for member in guild.members if not member.bot)
        bots = max(0, len(guild.members) - humans)
        text_channels = len(guild.text_channels)
        voice_channels = len(guild.voice_channels)
        embed = discord.Embed(title=f"🏠 {guild.name}", color=discord.Color.blurple())
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Server ID", value=f"`{guild.id}`", inline=True)
        embed.add_field(name="Members", value=f"{guild.member_count or len(guild.members)} ({humans} human • {bots} bot cached)", inline=True)
        embed.add_field(name="Channels", value=f"{text_channels} text • {voice_channels} voice • {len(guild.channels)} total", inline=True)
        embed.add_field(name="Roles", value=str(len(guild.roles)), inline=True)
        embed.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, style="R"), inline=True)
        embed.add_field(name="Owner", value=f"<@{guild.owner_id}>", inline=True)
        embed.add_field(name="Boosts", value=f"Tier {guild.premium_tier} • {guild.premium_subscription_count or 0} boosts", inline=True)
        embed.add_field(name="Verification", value=str(guild.verification_level).replace("_", " ").title(), inline=True)
        await _replace(interaction, embed=embed, view=InfoView(self.owner_id))

    @discord.ui.button(label="Community Tools", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, embed=_center_embed(), view=CommunityToolsView(self.owner_id))


def _permission_line(label: str, allowed: bool) -> str:
    return f"{'✅' if allowed else '❌'} {label}"


async def show_permission_check(interaction: discord.Interaction) -> None:
    channel = _text_channel(interaction)
    guild = interaction.guild
    member = interaction.user
    if channel is None or guild is None or guild.me is None or not isinstance(member, discord.Member):
        return await _private(interaction, "❌ Permission Check needs a normal server text channel.")
    bot = channel.permissions_for(guild.me)
    user = channel.permissions_for(member)

    bot_checks = [
        ("View Channel", bot.view_channel),
        ("Send Messages", bot.send_messages),
        ("Embed Links", bot.embed_links),
        ("Read Message History", bot.read_message_history),
        ("Manage Messages (custom sender cleanup)", bot.manage_messages),
        ("Manage Webhooks (custom sender)", bot.manage_webhooks),
        ("Send/Create Polls", _poll_permission(bot)),
    ]
    user_checks = [
        ("View Channel", user.view_channel),
        ("Send Messages", user.send_messages),
        ("Manage Messages (stickies/embeds)", user.manage_messages or member.guild_permissions.administrator),
        ("Manage Webhooks (custom sender)", user.manage_webhooks or member.guild_permissions.administrator),
        ("Send/Create Polls", _poll_permission(user)),
    ]
    sticky_ok = bool(bot.view_channel and bot.send_messages and bot.read_message_history)
    embed_ok = bool(sticky_ok and bot.embed_links)
    native_poll_ok = bool(bot.view_channel and bot.send_messages and _poll_permission(bot))
    custom_sender_ok = bool(sticky_ok and bot.manage_webhooks and bot.manage_messages)

    embed = discord.Embed(
        title=f"🔐 Permission Check • #{channel.name}",
        description="Effective channel permissions after role/category/channel overwrites are applied.",
        color=discord.Color.green() if sticky_ok and embed_ok else discord.Color.orange(),
    )
    embed.add_field(name="Dank Shield", value="\n".join(_permission_line(*item) for item in bot_checks), inline=False)
    embed.add_field(name="You", value="\n".join(_permission_line(*item) for item in user_checks), inline=False)
    embed.add_field(
        name="Feature readiness",
        value=(
            f"{'✅' if sticky_ok else '❌'} Message sticky\n"
            f"{'✅' if embed_ok else '❌'} Embed / sticky poll\n"
            f"{'✅' if native_poll_ok else '❌'} Native Discord poll\n"
            f"{'✅' if custom_sender_ok else '❌'} Custom sticky sender"
        ),
        inline=False,
    )
    embed.set_footer(text="Custom Sender requires both Manage Webhooks and Manage Messages so old webhook-authored sticky copies can be cleaned up reliably")
    await _private(interaction, embed=embed)


def _server_stickies_embed(rows: Sequence[StickyConfig], page: int) -> discord.Embed:
    total = len(rows)
    pages = max(1, (total + _SERVER_STICKIES_PAGE_SIZE - 1) // _SERVER_STICKIES_PAGE_SIZE)
    safe_page = max(0, min(int(page), pages - 1))
    start = safe_page * _SERVER_STICKIES_PAGE_SIZE
    chunk = rows[start : start + _SERVER_STICKIES_PAGE_SIZE]
    embed = discord.Embed(title="📋 Server Stickies", color=discord.Color.blurple())
    if not chunk:
        embed.description = "No stickies are configured."
    else:
        embed.description = "\n".join(
            f"• <#{item.channel_id}> — **{item.mode}** — {'active' if item.enabled else 'paused'}"
            for item in chunk
        )
    embed.set_footer(text=f"{total} configured • page {safe_page + 1}/{pages}")
    return embed


class ServerStickiesView(_OwnedView):
    def __init__(self, owner_id: int, rows: Sequence[StickyConfig], *, page: int) -> None:
        super().__init__(owner_id, timeout=300)
        self.rows = tuple(rows)
        self.page = max(0, int(page))
        pages = max(1, (len(self.rows) + _SERVER_STICKIES_PAGE_SIZE - 1) // _SERVER_STICKIES_PAGE_SIZE)
        for item in self.children:
            label = str(getattr(item, "label", "") or "")
            if label == "Previous":
                item.disabled = self.page <= 0
            elif label == "Next":
                item.disabled = self.page >= pages - 1

    @discord.ui.button(label="Previous", emoji="⬅️", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        page = max(0, self.page - 1)
        await _replace(interaction, embed=_server_stickies_embed(self.rows, page), view=ServerStickiesView(self.owner_id, self.rows, page=page))

    @discord.ui.button(label="Next", emoji="➡️", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        page = self.page + 1
        await _replace(interaction, embed=_server_stickies_embed(self.rows, page), view=ServerStickiesView(self.owner_id, self.rows, page=page))


def _fun_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎲 Fun & Lookup",
        description="Network lookups are bounded and fail cleanly; quick games are local and instant.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Lookups",
        value="Weather • Wikipedia • random Wikipedia • random WikiHow • Urban Dictionary (age-restricted channels only)",
        inline=False,
    )
    embed.add_field(name="Quick games", value="Custom dice notation • coin flip • deterministic-for-the-same-pair compatibility joke", inline=False)
    embed.set_footer(text="Unavailable provider-backed features are not advertised as buttons")
    return embed


class FunLookupView(_OwnedView):
    @discord.ui.button(label="Weather", emoji="🌦️", style=discord.ButtonStyle.primary, row=0)
    async def weather(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(WeatherModal())

    @discord.ui.button(label="Wikipedia", emoji="📚", style=discord.ButtonStyle.primary, row=0)
    async def wiki(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(WikipediaModal())

    @discord.ui.button(label="Random Wikipedia", emoji="🎲", style=discord.ButtonStyle.secondary, row=0)
    async def random_wiki(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        try:
            result = await random_wikipedia()
        except CommunityLookupError as exc:
            return await _private(interaction, f"❌ {exc}")
        await _private(interaction, embed=discord.Embed(title=result.title, description=result.summary, url=result.url, color=discord.Color.blurple()))

    @discord.ui.button(label="Random WikiHow", emoji="🛠️", style=discord.ButtonStyle.secondary, row=0)
    async def wikihow(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        try:
            result = await random_wikihow()
        except CommunityLookupError as exc:
            return await _private(interaction, f"❌ {exc}")
        await _private(interaction, embed=discord.Embed(title=result.title, description=result.summary, url=result.url, color=discord.Color.blurple()))

    @discord.ui.button(label="Urban Dictionary", emoji="🔞", style=discord.ButtonStyle.secondary, row=1)
    async def urban(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        channel = _text_channel(interaction)
        if channel is None or not channel.is_nsfw():
            return await _private(interaction, "❌ Urban Dictionary is restricted to Discord channels marked **Age-Restricted / NSFW**.")
        await interaction.response.send_modal(UrbanDictionaryModal())

    @discord.ui.button(label="Roll Dice", emoji="🎲", style=discord.ButtonStyle.success, row=1)
    async def dice(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(DiceModal())

    @discord.ui.button(label="Coin Flip", emoji="🪙", style=discord.ButtonStyle.success, row=1)
    async def coin(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _private(interaction, f"🪙 **{random.choice(('Heads', 'Tails'))}!**")

    @discord.ui.button(label="Compatibility", emoji="💘", style=discord.ButtonStyle.success, row=1)
    async def love(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(CompatibilityModal())

    @discord.ui.button(label="Community Tools", emoji="↩️", style=discord.ButtonStyle.secondary, row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, embed=_center_embed(), view=CommunityToolsView(self.owner_id))


class WeatherModal(discord.ui.Modal, title="Weather lookup"):
    location = discord.ui.TextInput(label="City, region, or postal code", required=True, max_length=120)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await weather_lookup(str(self.location.value))
        except CommunityLookupError as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        condition = WEATHER_LABELS.get(result.weather_code, "Current conditions")
        embed = discord.Embed(title=f"🌦️ {result.location}", description=condition, color=discord.Color.blurple())
        embed.add_field(name="Temperature", value=f"{result.temperature_f:.0f}°F / {result.temperature_c:.0f}°C", inline=True)
        embed.add_field(name="Feels like", value=f"{result.apparent_f:.0f}°F / {result.apparent_c:.0f}°C", inline=True)
        embed.add_field(name="Humidity", value=f"{result.humidity}%", inline=True)
        embed.add_field(name="Wind", value=f"{result.wind_mph:.0f} mph / {result.wind_kmh:.0f} km/h", inline=True)
        if result.high_c is not None and result.low_c is not None:
            embed.add_field(name="Today", value=f"High {result.high_f:.0f}°F • Low {result.low_f:.0f}°F", inline=True)
        if result.precipitation_probability is not None:
            embed.add_field(name="Rain/snow chance", value=f"{result.precipitation_probability}%", inline=True)
        embed.set_footer(text="Weather data: Open-Meteo")
        await interaction.followup.send(embed=embed, ephemeral=True)


class WikipediaModal(discord.ui.Modal, title="Wikipedia lookup"):
    topic = discord.ui.TextInput(label="Topic", required=True, max_length=200)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await wikipedia_lookup(str(self.topic.value))
        except CommunityLookupError as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        embed = discord.Embed(title=result.title, description=result.summary, url=result.url, color=discord.Color.blurple())
        embed.set_footer(text="Source: Wikipedia")
        await interaction.followup.send(embed=embed, ephemeral=True)


class UrbanDictionaryModal(discord.ui.Modal, title="Urban Dictionary lookup"):
    term = discord.ui.TextInput(label="Word or phrase", required=True, max_length=100)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        channel = _text_channel(interaction)
        if channel is None or not channel.is_nsfw():
            return await _private(interaction, "❌ Urban Dictionary is restricted to age-restricted / NSFW channels.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await urban_dictionary_lookup(str(self.term.value))
        except CommunityLookupError as exc:
            return await interaction.followup.send(f"❌ {exc}", ephemeral=True)
        embed = discord.Embed(
            title=f"🔞 {result.word}",
            description=result.definition or "No definition text.",
            url=result.permalink or None,
            color=discord.Color.blurple(),
        )
        if result.example:
            embed.add_field(name="Example", value=result.example, inline=False)
        embed.set_footer(text=f"Urban Dictionary • 👍 {result.thumbs_up} • 👎 {result.thumbs_down}")
        await interaction.followup.send(embed=embed, ephemeral=True)


def _parse_dice_notation(value: str) -> tuple[int, int, int, str]:
    raw = str(value or "").strip().replace(" ", "").lower()
    match = _DICE_PATTERN.fullmatch(raw)
    if match is None:
        raise InvalidCommunityToolValue("Use dice notation like `d20`, `2d6`, or `4d8+2`.")
    count_text = match.group("count")
    count = int(count_text) if count_text else 1
    sides = int(match.group("sides"))
    modifier = int(match.group("modifier") or 0)
    if not 1 <= count <= 50:
        raise InvalidCommunityToolValue("Roll between 1 and 50 dice at a time.")
    if not 2 <= sides <= 1000:
        raise InvalidCommunityToolValue("Dice can have between 2 and 1000 sides.")
    if not -10000 <= modifier <= 10000:
        raise InvalidCommunityToolValue("Keep the modifier between -10000 and +10000.")
    normalized = f"{count}d{sides}{modifier:+d}" if modifier else f"{count}d{sides}"
    return count, sides, modifier, normalized


def _dice_embed(notation: str) -> discord.Embed:
    count, sides, modifier, normalized = _parse_dice_notation(notation)
    rolls = [random.randint(1, sides) for _ in range(count)]
    total = sum(rolls) + modifier
    shown = ", ".join(str(value) for value in rolls[:20])
    if len(rolls) > 20:
        shown += f", … +{len(rolls) - 20} more"
    modifier_text = f" {modifier:+d}" if modifier else ""
    embed = discord.Embed(title=f"🎲 {normalized}", description=f"**Total: {total}**", color=discord.Color.blurple())
    embed.add_field(name="Rolls", value=shown, inline=False)
    if modifier:
        embed.add_field(name="Modifier", value=modifier_text.strip(), inline=True)
    return embed


class DiceModal(discord.ui.Modal, title="Roll dice"):
    notation = discord.ui.TextInput(label="Dice notation", required=True, max_length=20, default="2d6", placeholder="d20, 2d6, 4d8+2")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            _, _, _, normalized = _parse_dice_notation(str(self.notation.value))
            embed = _dice_embed(normalized)
        except InvalidCommunityToolValue as exc:
            return await _private(interaction, f"❌ {exc}")
        await _private(interaction, embed=embed, view=DiceView(int(interaction.user.id), normalized))


class DiceView(_OwnedView):
    def __init__(self, owner_id: int, notation: str = "2d6") -> None:
        super().__init__(owner_id, timeout=300)
        self.notation = notation

    @discord.ui.button(label="Roll Again", emoji="🎲", style=discord.ButtonStyle.success)
    async def reroll(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, embed=_dice_embed(self.notation), view=DiceView(self.owner_id, self.notation))

    @discord.ui.button(label="Change Dice", emoji="✏️", style=discord.ButtonStyle.secondary)
    async def change(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(DiceModal())


class CompatibilityModal(discord.ui.Modal, title="Compatibility"):
    first = discord.ui.TextInput(label="First name", required=True, max_length=80)
    second = discord.ui.TextInput(label="Second name", required=True, max_length=80)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        a = " ".join(str(self.first.value).split()).casefold()
        b = " ".join(str(self.second.value).split()).casefold()
        if not a or not b:
            return await _private(interaction, "❌ Enter two names.")
        pair = "\0".join(sorted((a, b))).encode("utf-8")
        score = int.from_bytes(hashlib.sha256(pair).digest()[:4], "big") % 101
        await _private(
            interaction,
            f"💘 **{self.first.value} + {self.second.value}: {score}% compatible**\n*For entertainment only; the same pair gets the same silly score.*",
        )


async def open_community_tools(interaction: discord.Interaction, *, replace_message: bool = False) -> None:
    if interaction.guild is None:
        return await _private(interaction, "❌ Community Tools are available inside servers.")
    ensure_community_tools_runtime(interaction.client)
    if replace_message and not interaction.response.is_done():
        await _replace(interaction, embed=_center_embed(), view=CommunityToolsView(int(interaction.user.id)))
    else:
        await _private(interaction, embed=_center_embed(), view=CommunityToolsView(int(interaction.user.id)))


__all__ = [
    "CommunityToolsView",
    "DiceView",
    "FunLookupView",
    "InfoView",
    "NativePollPreviewView",
    "ServerStickiesView",
    "StickyCenterView",
    "StickySettingsView",
    "StickyTypeView",
    "ensure_community_tools_runtime",
    "open_community_tools",
    "show_permission_check",
]
