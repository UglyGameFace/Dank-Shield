from __future__ import annotations

"""Guided UI for server-wide inactivity/quiet notices."""

from dataclasses import replace
from typing import Any, Optional

import discord

from stoney_verify.community_quiet_notice_service import (
    DEFAULT_INACTIVITY_SECONDS,
    MAX_INACTIVITY_SECONDS,
    MIN_INACTIVITY_SECONDS,
    QuietNoticeConfig,
    delete_quiet_notice,
    get_quiet_notice,
    save_quiet_notice,
    set_quiet_notice_enabled,
)
from stoney_verify.community_tools_runtime import ensure_community_tools_runtime, quiet_notice_embed, quiet_notice_view
from stoney_verify.community_tools_service import CommunityStorageUnavailable, InvalidCommunityToolValue, utc_now

_ALLOWED_MENTIONS = discord.AllowedMentions.none()
_DEFAULT_QUIET_MESSAGE = (
    "It’s a little quiet here right now, but that doesn’t mean the community is gone. "
    "Members may be hanging out in a partner or secondary community. You’re welcome to join them there, "
    "or start a conversation here."
)


def _text_channel(interaction: discord.Interaction) -> Optional[discord.TextChannel]:
    channel = interaction.channel
    return channel if isinstance(channel, discord.TextChannel) else None


def _manage_quiet_notice(interaction: discord.Interaction) -> bool:
    member = interaction.user
    return bool(
        isinstance(member, discord.Member)
        and (member.guild_permissions.administrator or member.guild_permissions.manage_guild)
    )


def _bot_can_post(channel: discord.TextChannel) -> bool:
    me = channel.guild.me
    if me is None:
        return False
    perms = channel.permissions_for(me)
    return bool(perms.view_channel and perms.send_messages and perms.embed_links)


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


def parse_inactivity_duration(value: str) -> int:
    raw = str(value or "").strip().lower().replace(" ", "")
    if not raw:
        return DEFAULT_INACTIVITY_SECONDS
    multipliers = {
        "m": 60,
        "min": 60,
        "mins": 60,
        "minute": 60,
        "minutes": 60,
        "h": 3600,
        "hr": 3600,
        "hrs": 3600,
        "hour": 3600,
        "hours": 3600,
        "d": 86400,
        "day": 86400,
        "days": 86400,
    }
    if raw.isdigit():
        seconds = int(raw) * 60
    else:
        suffix = next((item for item in sorted(multipliers, key=len, reverse=True) if raw.endswith(item)), None)
        if suffix is None:
            raise InvalidCommunityToolValue("Use a duration like `30m`, `2h`, or `1d`.")
        amount = raw[: -len(suffix)]
        if not amount.isdigit():
            raise InvalidCommunityToolValue("Use a duration like `30m`, `2h`, or `1d`.")
        seconds = int(amount) * multipliers[suffix]
    if not MIN_INACTIVITY_SECONDS <= seconds <= MAX_INACTIVITY_SECONDS:
        raise InvalidCommunityToolValue("Quiet time must be between 5 minutes and 7 days.")
    return seconds


def human_duration(seconds: int) -> str:
    value = int(seconds)
    if value % 86400 == 0:
        days = value // 86400
        return f"{days} day{'s' if days != 1 else ''}"
    if value % 3600 == 0:
        hours = value // 3600
        return f"{hours} hour{'s' if hours != 1 else ''}"
    minutes = max(1, value // 60)
    return f"{minutes} minute{'s' if minutes != 1 else ''}"


def quiet_status_embed(config: Optional[QuietNoticeConfig]) -> discord.Embed:
    if config is None:
        embed = discord.Embed(
            title="🌙 Quiet Server Notice",
            description=(
                "Post one helpful message after Dank Shield has observed no human chat activity for a time you choose. "
                "Activity is server-wide across channels the bot can actually see."
            ),
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="Good for",
            value="Partner servers • secondary communities • off-hours guidance • game/community hubs",
            inline=False,
        )
        embed.add_field(
            name="How it behaves",
            value="One notice per quiet period. Visible human activity re-arms it. Auto-clear can remove the old notice when chat wakes up.",
            inline=False,
        )
        embed.add_field(
            name="Who can configure it",
            value="Server-wide behavior requires **Manage Server**. This avoids one channel moderator accidentally changing a guild-wide notice.",
            inline=False,
        )
        embed.set_footer(text="Setup starts in the current channel; later edits preserve the saved destination unless you explicitly change it")
        return embed

    state = "▶️ Active" if config.enabled else "⏸️ Paused"
    embed = discord.Embed(
        title="🌙 Quiet Server Notice",
        description=f"{state} • destination <#{config.channel_id}> • after **{human_duration(config.inactivity_seconds)}** of observed quiet",
        color=discord.Color.green() if config.enabled else discord.Color.orange(),
    )
    embed.add_field(name="Message", value=config.content[:1000], inline=False)
    if config.partner_name or config.partner_url:
        destination = config.partner_name or "Community link"
        if config.partner_url:
            destination = f"[{destination}]({config.partner_url})"
        embed.add_field(name="Partner / destination", value=destination, inline=False)
    embed.add_field(name="Auto-clear when activity returns", value="Yes" if config.auto_clear else "No", inline=True)
    embed.add_field(name="Repeat spam", value="Blocked • one post per quiet cycle", inline=True)
    embed.set_footer(text="Only human messages Dank Shield can receive count as activity; bot/webhook messages are ignored")
    return embed


class _OwnedView(discord.ui.View):
    def __init__(self, owner_id: int, *, timeout: float = 900) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = int(owner_id)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if int(interaction.user.id) == self.owner_id:
            return True
        await _private(interaction, "❌ Open your own `/dank home` panel to use these controls.")
        return False


class QuietNoticeCenterView(_OwnedView):
    def __init__(self, owner_id: int, config: Optional[QuietNoticeConfig]) -> None:
        super().__init__(owner_id)
        self.config = config
        for item in self.children:
            label = str(getattr(item, "label", "") or "")
            if config is None and label in {"Preview / Test", "Pause / Resume", "Use This Channel", "Remove"}:
                item.disabled = True

    @discord.ui.button(label="Setup / Edit", emoji="✏️", style=discord.ButtonStyle.primary, row=0)
    async def setup(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await interaction.response.send_modal(QuietNoticeModal(self.config))

    @discord.ui.button(label="Preview / Test", emoji="👁️", style=discord.ButtonStyle.secondary, row=0)
    async def preview(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if self.config is None:
            return await _private(interaction, "ℹ️ Set up a quiet notice first.")
        await _private(
            interaction,
            f"👁️ **Private preview for <#{self.config.channel_id}>** — only you can see this panel.",
            embed=quiet_notice_embed(self.config),
            view=QuietNoticePreviewView(self.owner_id, self.config),
        )

    @discord.ui.button(label="Pause / Resume", emoji="⏯️", style=discord.ButtonStyle.secondary, row=0)
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _manage_quiet_notice(interaction):
            return await _private(interaction, "❌ Quiet Server Notice requires **Manage Server**.")
        if interaction.guild is None or self.config is None:
            return await _private(interaction, "❌ No quiet notice is configured here.")
        try:
            saved = await set_quiet_notice_enabled(
                int(interaction.guild.id),
                not self.config.enabled,
                actor_id=int(interaction.user.id),
                reset_activity_on_enable=not self.config.enabled,
            )
        except CommunityStorageUnavailable:
            return await _private(interaction, "❌ Quiet-notice storage is unavailable, so the current live notice was left untouched.")
        if saved is None:
            return await _private(interaction, "❌ That quiet notice no longer exists.")

        runtime = ensure_community_tools_runtime(interaction.client)
        runtime.set_quiet_config(saved)
        if not saved.enabled and self.config.last_notice_message_id:
            await runtime.delete_quiet_live_message(self.config)
        await _private(
            interaction,
            "✅ Quiet notice resumed and its inactivity timer restarted." if saved.enabled else "⏸️ Quiet notice paused. Durable state changed before the old live notice was removed.",
            embed=quiet_status_embed(saved),
            view=QuietNoticeCenterView(self.owner_id, saved),
        )

    @discord.ui.button(label="Use This Channel", emoji="📍", style=discord.ButtonStyle.secondary, row=1)
    async def use_here(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _manage_quiet_notice(interaction):
            return await _private(interaction, "❌ Changing the server-wide destination requires **Manage Server**.")
        if self.config is None:
            return await _private(interaction, "❌ Set up a quiet notice first.")
        channel = _text_channel(interaction)
        if channel is None:
            return await _private(interaction, "❌ Choose a normal text channel.")
        if int(channel.id) == int(self.config.channel_id):
            return await _private(interaction, "ℹ️ This is already the saved quiet-notice destination.")
        if not _bot_can_post(channel):
            return await _private(interaction, "❌ Dank Shield needs **View Channel + Send Messages + Embed Links** in this destination first.")
        moved = replace(
            self.config,
            channel_id=int(channel.id),
            last_activity_at=utc_now(),
            last_notice_message_id=None,
            last_notice_sent_at=None,
            updated_by=int(interaction.user.id),
        )
        try:
            saved = await save_quiet_notice(moved)
        except (InvalidCommunityToolValue, CommunityStorageUnavailable) as exc:
            return await _private(interaction, f"❌ {exc}")
        runtime = ensure_community_tools_runtime(interaction.client)
        runtime.set_quiet_config(saved)
        if self.config.last_notice_message_id:
            await runtime.delete_quiet_live_message(self.config)
        await _private(
            interaction,
            f"✅ Quiet-notice destination moved to <#{channel.id}>. The quiet timer restarted from now.",
            embed=quiet_status_embed(saved),
            view=QuietNoticeCenterView(self.owner_id, saved),
        )

    @discord.ui.button(label="Remove", emoji="🗑️", style=discord.ButtonStyle.danger, row=1)
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if self.config is None:
            return await _private(interaction, "❌ No quiet notice is configured.")
        await _private(
            interaction,
            "Remove the server-wide quiet notice? This does not affect normal channel stickies.",
            view=QuietNoticeRemoveView(self.owner_id, self.config),
        )

    @discord.ui.button(label="Back to Stickies", emoji="↩️", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        from .public_community_tools import _open_sticky_center

        await _open_sticky_center(interaction, replace_message=False)


class QuietNoticePreviewView(_OwnedView):
    def __init__(self, owner_id: int, config: QuietNoticeConfig) -> None:
        super().__init__(owner_id, timeout=300)
        self.config = config
        if config.partner_url:
            self.add_item(
                discord.ui.Button(
                    label=(f"Open {config.partner_name}" if config.partner_name else "Open community link")[:80],
                    emoji="🔗",
                    style=discord.ButtonStyle.link,
                    url=config.partner_url,
                )
            )

    @discord.ui.button(label="Post 30s Test", emoji="🧪", style=discord.ButtonStyle.primary)
    async def test_post(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _manage_quiet_notice(interaction):
            return await _private(interaction, "❌ Quiet Server Notice testing requires **Manage Server**.")
        channel = interaction.client.get_channel(int(self.config.channel_id))
        if not isinstance(channel, discord.TextChannel):
            return await _private(interaction, "❌ The configured destination channel is unavailable.")
        if not _bot_can_post(channel):
            return await _private(interaction, "❌ Dank Shield cannot post embeds in the configured destination.")
        try:
            await channel.send(
                content="🧪 **Quiet-notice test** — this temporary message does not change the inactivity timer.",
                embed=quiet_notice_embed(self.config),
                view=quiet_notice_view(self.config),
                allowed_mentions=_ALLOWED_MENTIONS,
                delete_after=30,
            )
        except (discord.Forbidden, discord.HTTPException):
            return await _private(interaction, "❌ Dank Shield could not post the temporary test in the configured destination.")
        await _private(interaction, f"✅ Temporary quiet notice posted in <#{channel.id}> for 30 seconds. Durable delivery state was not changed.")


class QuietNoticeRemoveView(_OwnedView):
    def __init__(self, owner_id: int, config: QuietNoticeConfig) -> None:
        super().__init__(owner_id, timeout=120)
        self.config = config

    @discord.ui.button(label="Remove Quiet Notice", emoji="🗑️", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        if not _manage_quiet_notice(interaction):
            return await _private(interaction, "❌ Removing the server-wide notice requires **Manage Server**.")
        try:
            await delete_quiet_notice(int(self.config.guild_id))
        except CommunityStorageUnavailable:
            return await _private(interaction, "❌ Quiet-notice storage is unavailable, so the live notice was left untouched.")
        runtime = ensure_community_tools_runtime(interaction.client)
        runtime.remove_quiet_config(int(self.config.guild_id))
        if self.config.last_notice_message_id:
            await runtime.delete_quiet_live_message(self.config)
        await _private(
            interaction,
            "✅ Quiet notice removed. Normal stickies were left alone.",
            embed=quiet_status_embed(None),
            view=QuietNoticeCenterView(self.owner_id, None),
        )

    @discord.ui.button(label="Cancel", emoji="↩️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        _ = button
        await _private(interaction, embed=quiet_status_embed(self.config), view=QuietNoticeCenterView(self.owner_id, self.config))


async def _refresh_live_notice(interaction: discord.Interaction, config: QuietNoticeConfig) -> bool:
    if not config.last_notice_message_id:
        return True
    channel = interaction.client.get_channel(int(config.channel_id))
    if not isinstance(channel, discord.TextChannel):
        return False
    try:
        message = await channel.fetch_message(int(config.last_notice_message_id))
        await message.edit(
            embed=quiet_notice_embed(config),
            view=quiet_notice_view(config),
            allowed_mentions=_ALLOWED_MENTIONS,
        )
        return True
    except discord.NotFound:
        return False
    except (discord.Forbidden, discord.HTTPException):
        return False


class QuietNoticeModal(discord.ui.Modal, title="Quiet server notice"):
    inactivity = discord.ui.TextInput(
        label="Send after no visible human chat for...",
        required=True,
        max_length=12,
        default="2h",
        placeholder="Examples: 30m, 2h, 1d",
    )
    message = discord.ui.TextInput(
        label="Quiet-time message",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1800,
        default=_DEFAULT_QUIET_MESSAGE,
    )
    partner_name = discord.ui.TextInput(label="Partner/community name (optional)", required=False, max_length=100)
    partner_url = discord.ui.TextInput(
        label="Partner/community HTTPS link (optional)",
        required=False,
        max_length=1000,
        placeholder="https://discord.gg/...",
    )
    auto_clear = discord.ui.TextInput(
        label="Remove notice when chat wakes up? yes/no",
        required=True,
        max_length=3,
        default="yes",
    )

    def __init__(self, current: Optional[QuietNoticeConfig]) -> None:
        super().__init__()
        self.current = current
        if current is not None:
            self.inactivity.default = _duration_input(current.inactivity_seconds)
            self.message.default = current.content
            self.partner_name.default = current.partner_name
            self.partner_url.default = current.partner_url
            self.auto_clear.default = "yes" if current.auto_clear else "no"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not _manage_quiet_notice(interaction):
            return await _private(interaction, "❌ Quiet Server Notice requires **Manage Server**.")
        current_channel = _text_channel(interaction)
        if interaction.guild is None or current_channel is None:
            return await _private(interaction, "❌ Configure the quiet notice from a normal server text channel.")
        try:
            seconds = parse_inactivity_duration(str(self.inactivity.value))
        except InvalidCommunityToolValue as exc:
            return await _private(interaction, f"❌ {exc}")

        clear_choice = str(self.auto_clear.value).strip().lower()
        if clear_choice not in {"y", "yes", "n", "no"}:
            return await _private(interaction, "❌ For auto-clear, enter `yes` or `no`; nothing was changed.")
        auto_clear = clear_choice in {"y", "yes"}
        now = utc_now()
        is_new = self.current is None
        base = self.current or QuietNoticeConfig(
            guild_id=int(interaction.guild.id),
            channel_id=int(current_channel.id),
            content=_DEFAULT_QUIET_MESSAGE,
            last_activity_at=now,
        )
        config = replace(
            base,
            guild_id=int(interaction.guild.id),
            # Editing text/timing never silently moves the destination. Use the
            # explicit Use This Channel action for that.
            channel_id=int(base.channel_id),
            enabled=base.enabled,
            content=str(self.message.value or ""),
            inactivity_seconds=seconds,
            partner_name=str(self.partner_name.value or ""),
            partner_url=str(self.partner_url.value or ""),
            auto_clear=auto_clear,
            last_activity_at=now if is_new else base.last_activity_at,
            last_notice_message_id=base.last_notice_message_id,
            last_notice_sent_at=base.last_notice_sent_at,
            updated_by=int(interaction.user.id),
        )
        try:
            saved = await save_quiet_notice(config)
        except (InvalidCommunityToolValue, CommunityStorageUnavailable) as exc:
            return await _private(interaction, f"❌ {exc}")

        runtime = ensure_community_tools_runtime(interaction.client)
        runtime.set_quiet_config(saved)
        live_updated = await _refresh_live_notice(interaction, saved)
        note = "✅ Quiet notice saved. Existing destination and quiet-cycle state were preserved."
        if is_new:
            note = f"✅ Quiet notice created for <#{saved.channel_id}>. Its inactivity timer starts now."
        elif saved.last_notice_message_id and not live_updated:
            note += " The durable config is updated, but the current live card could not be edited."
        await _private(
            interaction,
            note,
            embed=quiet_status_embed(saved),
            view=QuietNoticeCenterView(int(interaction.user.id), saved),
        )


def _duration_input(seconds: int) -> str:
    value = int(seconds)
    if value % 86400 == 0:
        return f"{value // 86400}d"
    if value % 3600 == 0:
        return f"{value // 3600}h"
    return f"{max(1, value // 60)}m"


async def open_quiet_notice_center(interaction: discord.Interaction) -> None:
    if not _manage_quiet_notice(interaction):
        return await _private(interaction, "❌ Quiet Server Notice is server-wide and requires **Manage Server**.")
    if interaction.guild is None or _text_channel(interaction) is None:
        return await _private(interaction, "❌ Open this inside a normal server text channel.")
    try:
        config = await get_quiet_notice(int(interaction.guild.id))
    except CommunityStorageUnavailable:
        return await _private(interaction, "❌ Quiet-notice storage is unavailable. Apply the Community Tools quiet-notice migration first.")
    await _private(
        interaction,
        embed=quiet_status_embed(config),
        view=QuietNoticeCenterView(int(interaction.user.id), config),
    )


__all__ = [
    "QuietNoticeCenterView",
    "QuietNoticeModal",
    "human_duration",
    "open_quiet_notice_center",
    "parse_inactivity_duration",
    "quiet_status_embed",
]
