from __future__ import annotations

"""Single-owner runtime for persistent Dank Shield sticky messages."""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Optional

import discord

from .community_tools_service import (
    CommunityStorageUnavailable,
    InvalidCommunityToolValue,
    StickyConfig,
    StickyPoll,
    cast_sticky_poll_vote,
    get_sticky,
    get_sticky_poll,
    list_stickies,
    update_sticky_delivery,
)

MANAGED_WEBHOOK_NAME = "Dank Shield Sticky"
_RUNTIME_ATTR = "_dank_community_tools_runtime"


def should_refresh_sticky(
    config: StickyConfig,
    *,
    message_count: int,
    now: Optional[datetime] = None,
) -> bool:
    """Return whether a new human message should move this sticky to the bottom."""
    if not config.enabled:
        return False
    if int(message_count) >= int(config.message_threshold):
        return True
    if config.last_sent_at is None:
        return True
    current = now or datetime.now(timezone.utc)
    sent_at = config.last_sent_at
    if sent_at.tzinfo is None:
        sent_at = sent_at.replace(tzinfo=timezone.utc)
    return (current - sent_at.astimezone(timezone.utc)).total_seconds() >= int(config.interval_seconds)


def sticky_embed(config: StickyConfig) -> discord.Embed:
    embed = discord.Embed(
        title=config.title or None,
        description=config.content or None,
        color=discord.Color(int(config.color)),
    )
    if config.image_url:
        embed.set_image(url=config.image_url)
    if config.thumbnail_url:
        embed.set_thumbnail(url=config.thumbnail_url)
    embed.set_footer(text="Dank Shield • persistent message")
    return embed


def sticky_poll_embed(poll: StickyPoll) -> discord.Embed:
    counts = poll.counts()
    total = max(1, poll.total_votes)
    lines: list[str] = []
    for index, option in enumerate(poll.options):
        count = counts[index] if index < len(counts) else 0
        percent = (count / total * 100.0) if poll.total_votes else 0.0
        lines.append(f"**{index + 1}. {option}** — {count} vote{'s' if count != 1 else ''} ({percent:.0f}%)")
    state_label = {"active": "Voting open", "paused": "Voting paused", "ended": "Poll ended"}.get(poll.state, poll.state)
    embed = discord.Embed(
        title="📊 Sticky Poll",
        description=poll.question,
        color=discord.Color.blurple(),
    )
    embed.add_field(name=state_label, value="\n".join(lines) or "No choices.", inline=False)
    embed.set_footer(text=f"Dank Shield • {poll.total_votes} total vote{'s' if poll.total_votes != 1 else ''} • one choice per member")
    return embed


async def _private(interaction: discord.Interaction, text: str) -> None:
    payload = {
        "content": text,
        "ephemeral": True,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if interaction.response.is_done():
        await interaction.followup.send(**payload)
    else:
        await interaction.response.send_message(**payload)


class StickyVoteButton(discord.ui.Button["StickyPollView"]):
    def __init__(self, channel_id: int, option_index: int, label: str) -> None:
        super().__init__(
            label=f"{option_index + 1}. {label}"[:80],
            style=discord.ButtonStyle.secondary,
            custom_id=f"dank:sticky:vote:v1:{int(channel_id)}:{int(option_index)}",
            row=min(option_index // 4, 1),
        )
        self.channel_id = int(channel_id)
        self.option_index = int(option_index)

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            poll = await cast_sticky_poll_vote(
                self.channel_id,
                int(interaction.user.id),
                self.option_index,
            )
        except (InvalidCommunityToolValue, CommunityStorageUnavailable) as exc:
            return await _private(interaction, f"❌ {exc}")
        except Exception:
            return await _private(interaction, "❌ The vote could not be saved.")

        view = StickyPollView(poll)
        try:
            await interaction.response.edit_message(
                embed=sticky_poll_embed(poll),
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException:
            await _private(interaction, "✅ Vote saved. I could not refresh the public poll card yet.")


class StickyPollView(discord.ui.View):
    def __init__(self, poll: StickyPoll) -> None:
        super().__init__(timeout=None)
        self.channel_id = int(poll.channel_id)
        if poll.state == "active":
            for index, option in enumerate(poll.options):
                self.add_item(StickyVoteButton(poll.channel_id, index, option))


class StickyRuntime:
    """Owns sticky movement and startup reconciliation for the whole bot."""

    def __init__(self, bot: Any) -> None:
        self.bot = bot
        self._configs: dict[int, StickyConfig] = {}
        self._counts: dict[int, int] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._webhooks: dict[int, discord.Webhook] = {}
        self._registered_poll_views: set[int] = set()
        self._pending_refreshes: set[int] = set()
        self._ready_lock = asyncio.Lock()

    def set_config(self, config: StickyConfig) -> None:
        self._configs[int(config.channel_id)] = config
        self._counts[int(config.channel_id)] = 0

    def remove_config(self, channel_id: int) -> None:
        channel_key = int(channel_id)
        self._configs.pop(channel_key, None)
        self._counts.pop(channel_key, None)
        self._webhooks.pop(channel_key, None)
        self._pending_refreshes.discard(channel_key)

    async def _config_for(self, channel_id: int) -> Optional[StickyConfig]:
        key = int(channel_id)
        cached = self._configs.get(key)
        if cached is not None:
            return cached
        try:
            config = await get_sticky(key)
        except CommunityStorageUnavailable:
            return None
        if config is not None:
            self._configs[key] = config
        return config

    async def on_message(self, message: discord.Message) -> None:
        guild = getattr(message, "guild", None)
        if guild is None:
            return
        author = getattr(message, "author", None)
        if bool(getattr(author, "bot", False)):
            return
        if getattr(message, "webhook_id", None):
            return

        channel_id = int(getattr(message.channel, "id", 0) or 0)
        if channel_id <= 0:
            return
        config = await self._config_for(channel_id)
        if config is None or not config.enabled:
            return
        if int(config.guild_id) != int(guild.id):
            return

        count = int(self._counts.get(channel_id, 0)) + 1
        self._counts[channel_id] = count
        if not should_refresh_sticky(config, message_count=count):
            return

        self._counts[channel_id] = 0
        if channel_id in self._pending_refreshes:
            return
        self._pending_refreshes.add(channel_id)
        asyncio.create_task(
            self._refresh_from_activity(message.channel, config),
            name=f"dank-sticky-refresh-{channel_id}",
        )

    async def _refresh_from_activity(self, channel: Any, config: StickyConfig) -> None:
        channel_id = int(getattr(channel, "id", config.channel_id) or config.channel_id)
        try:
            # The listener already evaluated the 15s/message threshold trigger. Force
            # the serialized worker to perform that decision instead of re-reading a
            # counter that was intentionally reset after scheduling.
            await self.refresh_channel(channel, expected_config=config, force=True)
        finally:
            self._pending_refreshes.discard(channel_id)

    async def on_ready(self) -> None:
        async with self._ready_lock:
            try:
                configs = await list_stickies(enabled_only=True)
            except CommunityStorageUnavailable:
                return
            self._configs = {int(item.channel_id): item for item in configs}
            self._counts = {int(item.channel_id): 0 for item in configs}
            for config in configs:
                await self._register_poll_view(config)
            await asyncio.gather(
                *(self._reconcile_config(config) for config in configs),
                return_exceptions=True,
            )

    async def _register_poll_view(self, config: StickyConfig) -> None:
        channel_id = int(config.channel_id)
        if config.mode != "poll" or channel_id in self._registered_poll_views:
            return
        try:
            poll = await get_sticky_poll(channel_id)
        except CommunityStorageUnavailable:
            return
        if poll is None or poll.state != "active":
            return
        try:
            self.bot.add_view(StickyPollView(poll))
        except Exception:
            return
        self._registered_poll_views.add(channel_id)

    async def _reconcile_config(self, config: StickyConfig) -> None:
        channel = self.bot.get_channel(int(config.channel_id))
        if not isinstance(channel, discord.TextChannel):
            return
        if not config.last_message_id:
            await self.refresh_channel(channel, expected_config=config, force=True)
            return
        try:
            await channel.fetch_message(int(config.last_message_id))
        except discord.NotFound:
            await self.refresh_channel(channel, expected_config=config, force=True)
        except (discord.Forbidden, discord.HTTPException):
            return

    async def _delete_previous(self, channel: discord.TextChannel, message_id: Optional[int]) -> None:
        if not message_id:
            return
        try:
            message = await channel.fetch_message(int(message_id))
        except discord.NotFound:
            return
        except (discord.Forbidden, discord.HTTPException):
            return
        try:
            await message.delete()
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    async def _managed_webhook(self, channel: discord.TextChannel) -> Optional[discord.Webhook]:
        channel_id = int(channel.id)
        cached = self._webhooks.get(channel_id)
        if cached is not None:
            return cached

        me = channel.guild.me
        if me is None or not channel.permissions_for(me).manage_webhooks:
            return None
        try:
            webhooks = await channel.webhooks()
            bot_id = int(getattr(getattr(self.bot, "user", None), "id", 0) or 0)
            for webhook in webhooks:
                owner_id = int(getattr(getattr(webhook, "user", None), "id", 0) or 0)
                if webhook.name == MANAGED_WEBHOOK_NAME and owner_id == bot_id:
                    self._webhooks[channel_id] = webhook
                    return webhook
            webhook = await channel.create_webhook(
                name=MANAGED_WEBHOOK_NAME,
                reason="Dank Shield persistent-message sender",
            )
            self._webhooks[channel_id] = webhook
            return webhook
        except (discord.Forbidden, discord.HTTPException):
            return None

    async def _send(self, channel: discord.TextChannel, config: StickyConfig) -> discord.Message:
        allowed_mentions = discord.AllowedMentions.none()
        if config.mode == "poll":
            poll = await get_sticky_poll(int(channel.id))
            if poll is None:
                raise InvalidCommunityToolValue("Sticky poll state is missing.")
            view = StickyPollView(poll)
            channel_id = int(channel.id)
            if channel_id not in self._registered_poll_views:
                try:
                    self.bot.add_view(view)
                except Exception:
                    pass
                else:
                    self._registered_poll_views.add(channel_id)
            return await channel.send(
                embed=sticky_poll_embed(poll),
                view=view,
                allowed_mentions=allowed_mentions,
            )

        content = config.content if config.mode == "plain" else None
        embed = sticky_embed(config) if config.mode == "embed" else None

        if config.use_webhook:
            webhook = await self._managed_webhook(channel)
            if webhook is not None:
                try:
                    webhook_payload: dict[str, Any] = {
                        "content": content,
                        "embed": embed,
                        "username": config.sender_name or "Dank Shield",
                        "allowed_mentions": allowed_mentions,
                        "wait": True,
                    }
                    if config.sender_avatar_url:
                        webhook_payload["avatar_url"] = config.sender_avatar_url
                    message = await webhook.send(**webhook_payload)
                    if isinstance(message, discord.WebhookMessage):
                        return message
                except (discord.Forbidden, discord.HTTPException, ValueError):
                    self._webhooks.pop(int(channel.id), None)

        return await channel.send(
            content=content,
            embed=embed,
            allowed_mentions=allowed_mentions,
        )

    async def refresh_channel(
        self,
        channel: Any,
        *,
        expected_config: Optional[StickyConfig] = None,
        force: bool = False,
    ) -> Optional[discord.Message]:
        if not isinstance(channel, discord.TextChannel):
            return None
        channel_id = int(channel.id)
        lock = self._locks.setdefault(channel_id, asyncio.Lock())
        async with lock:
            try:
                current = await get_sticky(channel_id)
            except CommunityStorageUnavailable:
                return None
            if current is None or not current.enabled:
                self.remove_config(channel_id)
                return None
            if expected_config is not None and int(expected_config.guild_id) != int(current.guild_id):
                return None
            if not force and not should_refresh_sticky(
                current,
                message_count=max(1, self._counts.get(channel_id, 0)),
            ):
                return None

            await self._delete_previous(channel, current.last_message_id)
            try:
                message = await self._send(channel, current)
            except (discord.Forbidden, discord.HTTPException, InvalidCommunityToolValue, CommunityStorageUnavailable):
                return None

            try:
                saved = await update_sticky_delivery(
                    channel_id,
                    message_id=int(message.id),
                    sent_at=datetime.now(timezone.utc),
                )
            except CommunityStorageUnavailable:
                saved = replace(current, last_message_id=int(message.id), last_sent_at=datetime.now(timezone.utc))
            if saved is not None:
                self.set_config(saved)
            return message

    async def delete_live_message(self, channel: Any, message_id: Optional[int]) -> None:
        if isinstance(channel, discord.TextChannel):
            await self._delete_previous(channel, message_id)


def ensure_community_tools_runtime(bot: Any) -> StickyRuntime:
    existing = getattr(bot, _RUNTIME_ATTR, None)
    if isinstance(existing, StickyRuntime):
        return existing

    runtime = StickyRuntime(bot)
    setattr(bot, _RUNTIME_ATTR, runtime)
    bot.add_listener(runtime.on_message, "on_message")
    bot.add_listener(runtime.on_ready, "on_ready")
    return runtime


def community_tools_runtime(bot: Any) -> Optional[StickyRuntime]:
    runtime = getattr(bot, _RUNTIME_ATTR, None)
    return runtime if isinstance(runtime, StickyRuntime) else None


__all__ = [
    "MANAGED_WEBHOOK_NAME",
    "StickyPollView",
    "StickyRuntime",
    "community_tools_runtime",
    "ensure_community_tools_runtime",
    "should_refresh_sticky",
    "sticky_embed",
    "sticky_poll_embed",
]
