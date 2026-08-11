from __future__ import annotations

"""Menu-first StickyBot-style community utilities for Dank Shield."""

import hashlib
import random
from dataclasses import replace
from datetime import timedelta
from typing import Any, Optional

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
from stoney_verify.community_tools_runtime import (
    community_tools_runtime,
    ensure_community_tools_runtime,
    sticky_poll_embed,
)
from stoney_verify.community_tools_service import (
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
    save_sticky_poll,
    set_sticky_enabled,
    set_sticky_poll_state,
)

_ALLOWED_MENTIONS = discord.AllowedMentions.none()


def _text_channel(interaction: discord.Interaction) -> Optional[discord.TextChannel]:
    channel = interaction.channel
    return channel if isinstance(channel, discord.TextChannel) else None


def _manage_messages(interaction: discord.Interaction) -> bool:
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False
    return bool(member.guild_permissions.administrator or member.guild_permissions.manage_messages)


def _manage_webhooks(interaction: discord.Interaction) -> bool:
    member = interaction.user
    if not isinstance(member, discord.Member):
        return False
    return bool(member.guild_permissions.administrator or member.guild_permissions.manage_webhooks)


def _can_send_messages(interaction: discord.Interaction) -> bool:
    channel = _text_channel(interaction)
    member = interaction.user
    if channel is None or not isinstance(member, discord.Member):
        return False
    return bool(channel.permissions_for(member).send_messages)


async def _private(
    interaction: discord.Interaction,
    content: str = "",
    *,
    embed: Optional[discord.Embed] = None,
    view: Optional[discord.ui.View] = None,
) -> None:
    payload: dict[str, Any] = {
        "ephemeral": True,
        "allowed_mentions": _ALLOWED_MENTIONS,
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
            "Persistent messages, polls, embeds, info, permission checks, and lightweight lookups—"
            "without adding a wall of slash commands."
        ),
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Persistent messages",
        value="📌 One smart sticky per channel • plain/embed/poll • pause/resume • custom cadence • images • custom sender",
        inline=False,
    )
    embed.add_field(
        name="Community",
        value="📊 Native Discord polls • 🧱 embed builder • 👤 member/server info • 🔐 permission check",
        inline=False,
    )
    embed.add_field(
        name="Fun & lookup",
        value="🌦️ Weather • 📚 Wikipedia • 🛠️ WikiHow • 🔞 Urban Dictionary • 🎲 Dice • 🪙 Coin Flip • 💘 Compatibility",
        inline=False,
    )
    embed.set_footer(text="Repeated sticky mentions are suppressed by design • no raw webhook URLs are stored")
    return embed


def _sticky_status_embed(config: Optional[StickyConfig], poll: Optional[StickyPoll] = None) -> discord.Embed:
    if config is None:
        embed = discord.Embed(
            title="📌 Sticky Messages",
            description="No sticky is configured in this channel yet.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Start here",
            value="Use **Create / Edit** for a plain message or rich embed, or **Sticky Poll** for a persistent poll.",
            inline=False,
        )
        embed.set_footer(text="Default movement: after 15 seconds or 5 new human messages")
        return embed

    state = "▶️ Active" if config.enabled else "⏸️ Paused"
    mode = {"plain": "Plain message", "embed": "Rich embed", "poll": "Sticky poll"}.get(config.mode, config.mode)
    embed = discord.Embed(
        title="📌 Sticky Messages",
        description=f"{state} • **{mode}** • <#{config.channel_id}>",
        color=discord.Color.green() if config.enabled else discord.Color.orange(),
    )
    embed.add_field(
        name="Movement",
        value=f"Move after **{config.interval_seconds}s** or **{config.message_threshold}** new human messages—whichever happens first.",
        inline=False,
    )
    if config.mode == "plain":
        embed.add_field(name="Message", value=(config.content or "(empty)")[:1000], inline=False)
    elif config.mode == "embed":
        preview = config.title or config.content or config.image_url or "(empty embed)"
        embed.add_field(name="Embed", value=preview[:1000], inline=False)
    elif poll is not None:
        embed.add_field(
            name="Poll",
            value=f"{poll.question[:700]}\n**{poll.total_votes}** votes • state: **{poll.state}**",
            inline=False,
        )
    embed.add_field(
        name="Custom sender",
        value=(f"✅ {config.sender_name or 'Dank Shield'}" if config.use_webhook else "Off • normal Dank Shield message"),
        inline=True,
    )
    embed.add_field(
        name="Repeated pings",
        value="Blocked",
        inline=True,
    )
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
        return await _private(interaction, "❌ Sticky management requires **Manage Messages**.")
    if _text_channel(interaction) is None:
        return await _private(interaction, "❌ Configure stickies inside a normal text channel.")
    try:
        config, poll = await _current_sticky(interaction)
    except CommunityStorageUnavailable:
        return await _private(interaction, "❌ Community Tools storage is unavailable. The sticky migration must be applied first.")
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
        if not _can_send_messages(interaction):
            return await _private(interaction, "❌ You need **Send Messages** in this channel to create a poll.")
        await interaction.response.send_modal(NativePollModal())

    @discord.ui.button(label="Embed Builder", emoji="🧱", style=discord.ButtonStyle.primary, row=0)
    async def embed_builder(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _manage_messages(interaction):
            return await _private(interaction, "❌ Publishing custom embeds requires **Manage Messages**.")
        await interaction.response.send_modal(EmbedBuilderModal())

    @discord.ui.button(label="Member / Server Info", emoji="👤", style=discord.ButtonStyle.secondary, row=1)
    async def info(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(
            interaction,
            embed=discord.Embed(
                title="👤 Member / Server Info",
                description="Pick a member for account/server basics, or open server info.",
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
        await _replace(
            interaction,
            embed=_fun_embed(),
            view=FunLookupView(self.owner_id),
        )

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

    @discord.ui.button(label="Create / Edit", emoji="✏️", style=discord.ButtonStyle.primary, row=0)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(StickyEditorModal(self.config))

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

    @discord.ui.button(label="Remove", emoji="🗑️", style=discord.ButtonStyle.danger, row=0)
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if self.config is None:
            return await _private(interaction, "❌ There is no sticky in this channel.")
        await _replace(
            interaction,
            embed=discord.Embed(
                title="Remove sticky?",
                description="This deletes the saved sticky and its sticky-poll state for this channel.",
                color=discord.Color.red(),
            ),
            view=StickyRemoveConfirmView(self.owner_id, self.config),
        )

    @discord.ui.button(label="Server Stickies", emoji="📋", style=discord.ButtonStyle.secondary, row=1)
    async def list_server(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if interaction.guild is None:
            return await _private(interaction, "❌ Use this in a server.")
        try:
            rows = await list_stickies(guild_id=int(interaction.guild.id))
        except CommunityStorageUnavailable:
            return await _private(interaction, "❌ Sticky storage is unavailable.")
        embed = discord.Embed(title="📋 Server Stickies", color=discord.Color.blurple())
        if not rows:
            embed.description = "No stickies are configured."
        else:
            lines = []
            for item in rows[:25]:
                state = "on" if item.enabled else "paused"
                lines.append(f"• <#{item.channel_id}> — **{item.mode}** — {state}")
            embed.description = "\n".join(lines)
        await _private(interaction, embed=embed)

    @discord.ui.button(label="Speed / Cadence", emoji="⏱️", style=discord.ButtonStyle.secondary, row=1)
    async def speed(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if self.config is None:
            return await _private(interaction, "❌ Create a sticky first.")
        await interaction.response.send_modal(StickySpeedModal(self.config))

    @discord.ui.button(label="Custom Sender", emoji="🎭", style=discord.ButtonStyle.secondary, row=1)
    async def persona(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if self.config is None:
            return await _private(interaction, "❌ Create a sticky first.")
        if self.config.mode == "poll":
            return await _private(interaction, "ℹ️ Sticky polls stay bot-owned so their persistent vote buttons remain reliable.")
        if not _manage_webhooks(interaction):
            return await _private(interaction, "❌ Custom sender personas require **Manage Webhooks**.")
        await interaction.response.send_modal(StickyPersonaModal(self.config))

    @discord.ui.button(label="Sticky Poll", emoji="📊", style=discord.ButtonStyle.success, row=2)
    async def sticky_poll(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(StickyPollModal(self.poll))

    @discord.ui.button(label="Poll Controls", emoji="🗳️", style=discord.ButtonStyle.secondary, row=2)
    async def poll_controls(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if self.config is None or self.config.mode != "poll" or self.poll is None:
            return await _private(interaction, "❌ This channel does not have a sticky poll.")
        await _replace(
            interaction,
            embed=sticky_poll_embed(self.poll),
            view=StickyPollControlView(self.owner_id, self.config, self.poll),
        )

    @discord.ui.button(label="Community Tools", emoji="🧰", style=discord.ButtonStyle.secondary, row=3)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, embed=_center_embed(), view=CommunityToolsView(self.owner_id))


class StickyRemoveConfirmView(_OwnedView):
    def __init__(self, owner_id: int, config: StickyConfig) -> None:
        super().__init__(owner_id, timeout=120)
        self.config = config

    @discord.ui.button(label="Remove Sticky", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        channel = _text_channel(interaction)
        try:
            await delete_sticky(int(self.config.channel_id))
        except CommunityStorageUnavailable:
            return await _private(interaction, "❌ Sticky storage is unavailable.")
        runtime = ensure_community_tools_runtime(interaction.client)
        if channel is not None:
            await runtime.delete_live_message(channel, self.config.last_message_id)
        runtime.remove_config(int(self.config.channel_id))
        await _replace(
            interaction,
            embed=_sticky_status_embed(None),
            view=StickyCenterView(self.owner_id, config=None, poll=None),
        )

    @discord.ui.button(label="Cancel", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(
            interaction,
            embed=_sticky_status_embed(self.config),
            view=StickyCenterView(self.owner_id, config=self.config, poll=None),
        )


class StickyEditorModal(discord.ui.Modal, title="Create or edit sticky"):
    message = discord.ui.TextInput(
        label="Message text",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1900,
        placeholder="Text shown in the sticky.",
    )
    mode = discord.ui.TextInput(
        label="Mode: plain or embed",
        required=True,
        max_length=10,
        default="plain",
    )
    embed_title = discord.ui.TextInput(label="Embed title (optional)", required=False, max_length=256)
    image_url = discord.ui.TextInput(label="Large image HTTPS URL (optional)", required=False, max_length=1000)
    thumbnail_url = discord.ui.TextInput(label="Thumbnail HTTPS URL (optional)", required=False, max_length=1000)

    def __init__(self, current: Optional[StickyConfig]) -> None:
        super().__init__()
        self.current = current
        if current is not None:
            self.message.default = current.content
            self.mode.default = current.mode if current.mode in {"plain", "embed"} else "embed"
            self.embed_title.default = current.title
            self.image_url.default = current.image_url
            self.thumbnail_url.default = current.thumbnail_url

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _manage_messages(interaction):
            return await _private(interaction, "❌ Sticky management requires **Manage Messages**.")
        channel = _text_channel(interaction)
        if channel is None or interaction.guild is None:
            return await _private(interaction, "❌ Configure stickies inside a normal text channel.")
        mode = str(self.mode.value).strip().lower()
        if mode not in {"plain", "embed"}:
            return await _private(interaction, "❌ Mode must be `plain` or `embed`.")
        base = self.current or StickyConfig(guild_id=int(interaction.guild.id), channel_id=int(channel.id))
        config = replace(
            base,
            guild_id=int(interaction.guild.id),
            channel_id=int(channel.id),
            enabled=True,
            content=str(self.message.value or ""),
            mode=mode,
            title=str(self.embed_title.value or ""),
            image_url=str(self.image_url.value or ""),
            thumbnail_url=str(self.thumbnail_url.value or ""),
            updated_by=int(interaction.user.id),
        )
        try:
            saved = await save_sticky(config)
        except (InvalidCommunityToolValue, CommunityStorageUnavailable) as exc:
            return await _private(interaction, f"❌ {exc}")
        runtime = ensure_community_tools_runtime(interaction.client)
        runtime.set_config(saved)
        posted = await runtime.refresh_channel(channel, force=True)
        text = "✅ Sticky saved and posted." if posted is not None else "⚠️ Sticky saved, but Dank Shield could not post it in this channel."
        await _private(
            interaction,
            text,
            embed=_sticky_status_embed(saved),
            view=StickyCenterView(int(interaction.user.id), config=saved, poll=None),
        )


class StickySpeedModal(discord.ui.Modal, title="Sticky speed / cadence"):
    seconds = discord.ui.TextInput(
        label="Seconds between time-based moves (15-3600)",
        required=True,
        max_length=4,
    )
    messages = discord.ui.TextInput(
        label="New messages before move (1-100)",
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
        config = replace(
            self.current,
            interval_seconds=seconds,
            message_threshold=messages,
            updated_by=int(interaction.user.id),
        )
        try:
            saved = await save_sticky(config)
        except (InvalidCommunityToolValue, CommunityStorageUnavailable) as exc:
            return await _private(interaction, f"❌ {exc}")
        ensure_community_tools_runtime(interaction.client).set_config(saved)
        await _private(
            interaction,
            "✅ Sticky cadence updated.",
            embed=_sticky_status_embed(saved),
            view=StickyCenterView(int(interaction.user.id), config=saved, poll=None),
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
            return await _private(interaction, "❌ Custom sender personas require **Manage Webhooks**.")
        enabled = str(self.enabled.value).strip().lower() in {"y", "yes", "1", "on", "true"}
        config = replace(
            self.current,
            use_webhook=enabled,
            sender_name=str(self.name.value or ""),
            sender_avatar_url=str(self.avatar.value or ""),
            updated_by=int(interaction.user.id),
        )
        try:
            saved = await save_sticky(config)
        except (InvalidCommunityToolValue, CommunityStorageUnavailable) as exc:
            return await _private(interaction, f"❌ {exc}")
        runtime = ensure_community_tools_runtime(interaction.client)
        runtime.set_config(saved)
        channel = _text_channel(interaction)
        if channel is not None and saved.enabled:
            await runtime.refresh_channel(channel, force=True)
        await _private(
            interaction,
            "✅ Custom sender settings updated. Dank Shield manages its own webhook; no webhook URL was saved.",
            embed=_sticky_status_embed(saved),
            view=StickyCenterView(int(interaction.user.id), config=saved, poll=None),
        )


class StickyPollModal(discord.ui.Modal, title="Create or edit sticky poll"):
    question = discord.ui.TextInput(label="Poll question", required=True, max_length=300)
    choices = discord.ui.TextInput(
        label="Choices — one per line (2-7)",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500,
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
            return await _private(interaction, "❌ Sticky polls require **Manage Messages**.")
        channel = _text_channel(interaction)
        if channel is None or interaction.guild is None:
            return await _private(interaction, "❌ Create sticky polls inside a normal text channel.")
        options = tuple(line.strip() for line in str(self.choices.value).splitlines() if line.strip())
        poll = StickyPoll(
            guild_id=int(interaction.guild.id),
            channel_id=int(channel.id),
            question=str(self.question.value),
            options=options,
            votes=dict(self.current.votes) if self.current is not None and tuple(self.current.options) == options else {},
            state="active",
            updated_by=int(interaction.user.id),
        )
        try:
            validated_poll = normalize_poll(poll)
            existing = await get_sticky(int(channel.id))
            sticky = StickyConfig(
                guild_id=int(interaction.guild.id),
                channel_id=int(channel.id),
                enabled=True,
                content=str(self.question.value),
                mode="poll",
                interval_seconds=existing.interval_seconds if existing else 15,
                message_threshold=existing.message_threshold if existing else 5,
                updated_by=int(interaction.user.id),
                last_message_id=existing.last_message_id if existing else None,
                last_sent_at=existing.last_sent_at if existing else None,
            )
            saved_sticky = await save_sticky(sticky)
            saved_poll = await save_sticky_poll(validated_poll)
        except (InvalidCommunityToolValue, CommunityStorageUnavailable) as exc:
            return await _private(interaction, f"❌ {exc}")
        runtime = ensure_community_tools_runtime(interaction.client)
        runtime.set_config(saved_sticky)
        await runtime.refresh_channel(channel, force=True)
        await _private(
            interaction,
            "✅ Sticky poll saved and posted.",
            embed=_sticky_status_embed(saved_sticky, saved_poll),
            view=StickyCenterView(int(interaction.user.id), config=saved_sticky, poll=saved_poll),
        )


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


class NativePollModal(discord.ui.Modal, title="Create Discord poll"):
    question = discord.ui.TextInput(label="Question", required=True, max_length=300)
    choices = discord.ui.TextInput(
        label="Choices — one per line (2-7)",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=400,
    )
    hours = discord.ui.TextInput(label="Duration in hours (1-168)", required=True, max_length=3, default="24")
    multiple = discord.ui.TextInput(label="Allow multiple choices? yes/no", required=True, max_length=3, default="no")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        channel = _text_channel(interaction)
        if channel is None:
            return await _private(interaction, "❌ Create polls inside a normal text channel.")
        if not _can_send_messages(interaction):
            return await _private(interaction, "❌ You need **Send Messages** in this channel to create a poll.")
        options = []
        for line in str(self.choices.value).splitlines():
            value = line.strip()
            if value and value.casefold() not in {item.casefold() for item in options}:
                options.append(value[:55])
        if not 2 <= len(options) <= 7:
            return await _private(interaction, "❌ Polls need 2 to 7 unique choices.")
        try:
            duration_hours = max(1, min(int(str(self.hours.value).strip()), 168))
        except ValueError:
            return await _private(interaction, "❌ Duration must be a whole number from 1 to 168.")
        allow_multiple = str(self.multiple.value).strip().lower() in {"y", "yes", "1", "true", "on"}
        try:
            poll = discord.Poll(str(self.question.value), timedelta(hours=duration_hours), multiple=allow_multiple)
            for option in options:
                poll.add_answer(text=option)
            await channel.send(poll=poll, allowed_mentions=_ALLOWED_MENTIONS)
        except (discord.HTTPException, discord.Forbidden, discord.ClientException):
            return await _private(interaction, "❌ Discord could not create that poll in this channel.")
        await _private(interaction, "✅ Poll posted.")


def _parse_color(value: str) -> int:
    raw = str(value or "").strip().lower().removeprefix("#").removeprefix("0x")
    if not raw:
        return 0x5865F2
    try:
        number = int(raw, 16)
    except ValueError as exc:
        raise InvalidCommunityToolValue("Color must be a six-digit hex value such as `5865F2`.") from exc
    if not 0 <= number <= 0xFFFFFF:
        raise InvalidCommunityToolValue("Color must be between `000000` and `FFFFFF`.")
    return number


class EmbedBuilderModal(discord.ui.Modal, title="Build an embed"):
    embed_title = discord.ui.TextInput(label="Title", required=False, max_length=256)
    description = discord.ui.TextInput(label="Description", style=discord.TextStyle.paragraph, required=True, max_length=4000)
    color = discord.ui.TextInput(label="Hex color", required=False, max_length=8, default="5865F2")
    image = discord.ui.TextInput(label="Large image HTTPS URL (optional)", required=False, max_length=1000)
    thumbnail = discord.ui.TextInput(label="Thumbnail HTTPS URL (optional)", required=False, max_length=1000)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _manage_messages(interaction):
            return await _private(interaction, "❌ Publishing custom embeds requires **Manage Messages**.")
        channel = _text_channel(interaction)
        if channel is None:
            return await _private(interaction, "❌ Publish embeds inside a normal text channel.")
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
        embed.set_footer(text=f"Posted with Dank Shield by {interaction.user}")
        try:
            await channel.send(embed=embed, allowed_mentions=_ALLOWED_MENTIONS)
        except (discord.Forbidden, discord.HTTPException):
            return await _private(interaction, "❌ Dank Shield cannot post embeds in this channel.")
        await _private(interaction, "✅ Embed posted.")


class InfoMemberSelect(discord.ui.UserSelect):
    def __init__(self) -> None:
        super().__init__(
            placeholder="Choose a member",
            min_values=1,
            max_values=1,
            custom_id="dank:community:info:user:v1",
            row=0,
        )

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
        embed.add_field(name="Discord account", value=discord.utils.format_dt(user.created_at, style="R"), inline=True)
        if member is not None:
            embed.add_field(name="Joined this server", value=discord.utils.format_dt(member.joined_at, style="R") if member.joined_at else "Unknown", inline=True)
            roles = [role.mention for role in member.roles[1:] if not role.managed][-10:]
            embed.add_field(name="Roles", value=" ".join(roles) if roles else "No extra roles", inline=False)
        embed.set_footer(text="For Dank Shield profile/privacy controls use My Profile.")
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
        embed = discord.Embed(title=f"🏠 {guild.name}", color=discord.Color.blurple())
        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        embed.add_field(name="Members", value=str(guild.member_count or len(guild.members)), inline=True)
        embed.add_field(name="Channels", value=str(len(guild.channels)), inline=True)
        embed.add_field(name="Roles", value=str(len(guild.roles)), inline=True)
        embed.add_field(name="Created", value=discord.utils.format_dt(guild.created_at, style="R"), inline=True)
        embed.add_field(name="Owner", value=f"<@{guild.owner_id}>", inline=True)
        embed.add_field(name="Boost tier", value=str(guild.premium_tier), inline=True)
        await _replace(interaction, embed=embed, view=InfoView(self.owner_id))

    @discord.ui.button(label="Community Tools", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, embed=_center_embed(), view=CommunityToolsView(self.owner_id))


async def show_permission_check(interaction: discord.Interaction) -> None:
    channel = _text_channel(interaction)
    guild = interaction.guild
    if channel is None or guild is None or guild.me is None:
        return await _private(interaction, "❌ Permission Check needs a normal server text channel.")
    perms = channel.permissions_for(guild.me)
    checks = [
        ("View Channel", perms.view_channel),
        ("Send Messages", perms.send_messages),
        ("Manage Messages", perms.manage_messages),
        ("Embed Links", perms.embed_links),
        ("Read Message History", perms.read_message_history),
        ("Add Reactions", perms.add_reactions),
        ("Use External Emojis", perms.use_external_emojis),
        ("Manage Webhooks", perms.manage_webhooks),
        ("Attach Files", perms.attach_files),
    ]
    lines = [f"{'✅' if allowed else '❌'} {label}" for label, allowed in checks]
    embed = discord.Embed(
        title=f"🔐 Dank Shield permissions in #{channel.name}",
        description="\n".join(lines),
        color=discord.Color.green() if all(value for _, value in checks[:5]) else discord.Color.orange(),
    )
    embed.set_footer(text="Manage Webhooks is only needed for custom sticky sender personas.")
    await _private(interaction, embed=embed)


def _fun_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🎲 Fun & Lookup",
        description="No paid lookup key required. Network lookups use short timeouts and fail cleanly.",
        color=discord.Color.blurple(),
    )
    embed.add_field(
        name="Lookups",
        value="Weather • Wikipedia • random Wikipedia • random WikiHow • Urban Dictionary (NSFW channels only)",
        inline=False,
    )
    embed.add_field(
        name="Quick games",
        value="Two dice • coin flip • deterministic compatibility score",
        inline=False,
    )
    embed.add_field(
        name="Image recognition",
        value="Provider-ready, but disabled until Dank Shield has a configured vision provider. No fake AI endpoint is used.",
        inline=False,
    )
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
        embed = discord.Embed(title=result.title, description=result.summary, url=result.url, color=discord.Color.blurple())
        await _private(interaction, embed=embed)

    @discord.ui.button(label="Random WikiHow", emoji="🛠️", style=discord.ButtonStyle.secondary, row=0)
    async def wikihow(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        try:
            result = await random_wikihow()
        except CommunityLookupError as exc:
            return await _private(interaction, f"❌ {exc}")
        embed = discord.Embed(title=result.title, description=result.summary, url=result.url, color=discord.Color.blurple())
        await _private(interaction, embed=embed)

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
        await _private(interaction, embed=_dice_embed(), view=DiceView(int(interaction.user.id)))

    @discord.ui.button(label="Coin Flip", emoji="🪙", style=discord.ButtonStyle.success, row=1)
    async def coin(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _private(interaction, f"🪙 **{random.choice(('Heads', 'Tails'))}!**")

    @discord.ui.button(label="Compatibility", emoji="💘", style=discord.ButtonStyle.success, row=1)
    async def love(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(CompatibilityModal())

    @discord.ui.button(label="Image AI Status", emoji="🖼️", style=discord.ButtonStyle.secondary, row=2)
    async def image_ai(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _private(
            interaction,
            "🖼️ **Image recognition is provider-ready but not enabled.** Dank Shield has no configured vision provider, so this button intentionally refuses to invent or scrape one.",
        )

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
        condition = WEATHER_LABELS.get(result.weather_code, f"Weather code {result.weather_code}")
        embed = discord.Embed(title=f"🌦️ {result.location}", description=condition, color=discord.Color.blurple())
        embed.add_field(name="Temperature", value=f"{result.temperature_f:.0f}°F / {result.temperature_c:.0f}°C", inline=True)
        embed.add_field(name="Feels like", value=f"{result.apparent_f:.0f}°F / {result.apparent_c:.0f}°C", inline=True)
        embed.add_field(name="Humidity", value=f"{result.humidity}%", inline=True)
        embed.add_field(name="Wind", value=f"{result.wind_mph:.0f} mph / {result.wind_kmh:.0f} km/h", inline=True)
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


def _dice_embed() -> discord.Embed:
    a, b = random.randint(1, 6), random.randint(1, 6)
    embed = discord.Embed(title="🎲 Dice Roll", description=f"**{a} + {b} = {a + b}**", color=discord.Color.blurple())
    return embed


class DiceView(_OwnedView):
    @discord.ui.button(label="Roll Again", emoji="🎲", style=discord.ButtonStyle.success)
    async def reroll(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _replace(interaction, embed=_dice_embed(), view=DiceView(self.owner_id))


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
        await _private(interaction, f"💘 **{self.first.value} + {self.second.value}: {score}% compatible**")


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
    "FunLookupView",
    "InfoView",
    "StickyCenterView",
    "ensure_community_tools_runtime",
    "open_community_tools",
    "show_permission_check",
]
