from __future__ import annotations

"""Single-owner runtime for persistent Dank Shield Community Tools."""

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any, Awaitable, Iterable, Optional

import discord

from .community_quiet_notice_service import (
    QuietNoticeConfig,
    clear_quiet_delivery,
    list_quiet_notices,
    record_quiet_activity,
    update_quiet_delivery,
)
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
QUIET_CHECK_SECONDS = 30
QUIET_ACTIVITY_PERSIST_SECONDS = 60
STARTUP_RECONCILE_CONCURRENCY = 10


def _log(message: str) -> None:
    try:
        print(f"community_tools_runtime: {message}", flush=True)
    except Exception:
        pass


def _utc(value: Optional[datetime], *, fallback: Optional[datetime] = None) -> Optional[datetime]:
    current = value or fallback
    if current is None:
        return None
    if current.tzinfo is None:
        return current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def should_refresh_sticky(
    config: StickyConfig,
    *,
    message_count: int,
    now: Optional[datetime] = None,
) -> bool:
    """Return whether this human-message event should move the sticky."""
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


def should_send_quiet_notice(
    config: QuietNoticeConfig,
    *,
    last_activity_at: Optional[datetime] = None,
    now: Optional[datetime] = None,
) -> bool:
    """Return whether a server has entered a new quiet period that needs one notice."""
    if not config.enabled:
        return False
    current = _utc(now, fallback=datetime.now(timezone.utc))
    activity = _utc(last_activity_at or config.last_activity_at)
    if current is None or activity is None:
        return False
    if (current - activity).total_seconds() < int(config.inactivity_seconds):
        return False
    sent_at = _utc(config.last_notice_sent_at)
    if sent_at is not None and sent_at >= activity:
        return False
    return True


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
    embed.set_footer(
        text=f"Dank Shield • {poll.total_votes} total vote{'s' if poll.total_votes != 1 else ''} • one choice per member"
    )
    return embed


def quiet_notice_embed(config: QuietNoticeConfig) -> discord.Embed:
    embed = discord.Embed(
        title="🌙 It’s quiet here right now",
        description=config.content,
        color=discord.Color.blurple(),
    )
    if config.partner_name or config.partner_url:
        label = config.partner_name or "Community link"
        value = f"[{label}]({config.partner_url})" if config.partner_url else label
        embed.add_field(name="Where people may be hanging out", value=value, inline=False)
    footer = "Dank Shield • quiet-server notice"
    if config.auto_clear:
        footer += " • clears when visible human activity returns"
    embed.set_footer(text=footer)
    return embed


def quiet_notice_view(config: QuietNoticeConfig) -> Optional[discord.ui.View]:
    if not config.partner_url:
        return None
    view = discord.ui.View(timeout=None)
    view.add_item(
        discord.ui.Button(
            label=(f"Open {config.partner_name}" if config.partner_name else "Open community link")[:80],
            emoji="🔗",
            style=discord.ButtonStyle.link,
            url=config.partner_url,
        )
    )
    return view


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
        runtime = ensure_community_tools_runtime(interaction.client)
        await runtime.cast_and_render_poll_vote(interaction, self.channel_id, self.option_index)


class StickyPollView(discord.ui.View):
    def __init__(self, poll: StickyPoll) -> None:
        super().__init__(timeout=None)
        self.channel_id = int(poll.channel_id)
        if poll.state == "active":
            for index, option in enumerate(poll.options):
                self.add_item(StickyVoteButton(poll.channel_id, index, option))


class StickyRuntime:
    """Owns sticky movement, quiet notices, and startup reconciliation for the whole bot."""

    def __init__(self, bot: Any) -> None:
        self.bot = bot
        self._configs: dict[int, StickyConfig] = {}
        self._counts: dict[int, int] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._webhooks: dict[int, discord.Webhook] = {}
        self._registered_poll_views: set[int] = set()
        self._poll_render_locks: dict[int, asyncio.Lock] = {}
        self._pending_refreshes: set[int] = set()
        self._ready_lock = asyncio.Lock()

        self._quiet_configs: dict[int, QuietNoticeConfig] = {}
        self._guild_last_activity: dict[int, datetime] = {}
        self._quiet_last_persisted: dict[int, datetime] = {}
        self._quiet_locks: dict[int, asyncio.Lock] = {}
        self._quiet_activity_pending: set[int] = set()
        self._quiet_watch_task: Optional[asyncio.Task[Any]] = None

    def set_config(self, config: StickyConfig) -> None:
        self._configs[int(config.channel_id)] = config
        self._counts[int(config.channel_id)] = 0

    def remove_config(self, channel_id: int) -> None:
        channel_key = int(channel_id)
        self._configs.pop(channel_key, None)
        self._counts.pop(channel_key, None)
        self._webhooks.pop(channel_key, None)
        self._pending_refreshes.discard(channel_key)

    def set_quiet_config(self, config: QuietNoticeConfig) -> None:
        guild_id = int(config.guild_id)
        self._quiet_configs[guild_id] = config
        activity = _utc(config.last_activity_at)
        if activity is not None:
            current = self._guild_last_activity.get(guild_id)
            if current is None or activity > current:
                self._guild_last_activity[guild_id] = activity
            self._quiet_last_persisted[guild_id] = activity
        if config.enabled:
            self._ensure_quiet_watch_task()

    def remove_quiet_config(self, guild_id: int) -> None:
        guild_key = int(guild_id)
        self._quiet_configs.pop(guild_key, None)
        self._guild_last_activity.pop(guild_key, None)
        self._quiet_last_persisted.pop(guild_key, None)
        self._quiet_activity_pending.discard(guild_key)

    def _ensure_quiet_watch_task(self) -> None:
        task = self._quiet_watch_task
        if task is not None and not task.done():
            return
        try:
            self._quiet_watch_task = asyncio.create_task(
                self._quiet_watch_loop(),
                name="dank-quiet-server-watch",
            )
        except RuntimeError:
            self._quiet_watch_task = None

    async def _config_for(self, channel_id: int) -> Optional[StickyConfig]:
        # The active sticky index is loaded once in on_ready and updated in-process
        # whenever the Community Tools UI saves/removes a sticky. Unknown channels
        # stay a zero-database hot path.
        return self._configs.get(int(channel_id))

    async def on_message(self, message: discord.Message) -> None:
        guild = getattr(message, "guild", None)
        if guild is None:
            return
        author = getattr(message, "author", None)
        if bool(getattr(author, "bot", False)):
            return
        if getattr(message, "webhook_id", None):
            return

        guild_id = int(getattr(guild, "id", 0) or 0)
        if guild_id > 0:
            quiet = self._quiet_configs.get(guild_id)
            if quiet is not None and quiet.enabled:
                self._observe_quiet_activity(message, quiet)

        channel_id = int(getattr(message.channel, "id", 0) or 0)
        if channel_id <= 0:
            return
        config = await self._config_for(channel_id)
        if config is None or not config.enabled:
            return
        if int(config.guild_id) != guild_id:
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

    def _observe_quiet_activity(self, message: discord.Message, config: QuietNoticeConfig) -> None:
        guild_id = int(config.guild_id)
        observed = _utc(getattr(message, "created_at", None), fallback=datetime.now(timezone.utc))
        if observed is None:
            return
        current = self._guild_last_activity.get(guild_id)
        if current is None or observed > current:
            self._guild_last_activity[guild_id] = observed

        last_persisted = self._quiet_last_persisted.get(guild_id) or _utc(config.last_activity_at)
        persistence_due = last_persisted is None or (observed - last_persisted).total_seconds() >= QUIET_ACTIVITY_PERSIST_SECONDS
        clear_live = bool(config.auto_clear and config.last_notice_message_id)
        if not persistence_due and not clear_live:
            return
        if guild_id in self._quiet_activity_pending:
            return
        self._quiet_activity_pending.add(guild_id)
        asyncio.create_task(
            self._persist_quiet_activity(config, observed, clear_live=clear_live),
            name=f"dank-quiet-activity-{guild_id}",
        )

    async def _persist_quiet_activity(
        self,
        config: QuietNoticeConfig,
        observed: datetime,
        *,
        clear_live: bool,
    ) -> None:
        guild_id = int(config.guild_id)
        lock = self._quiet_locks.setdefault(guild_id, asyncio.Lock())
        try:
            async with lock:
                latest = self._quiet_configs.get(guild_id) or config
                newest_observed = self._guild_last_activity.get(guild_id) or observed
                should_clear_live = bool(
                    latest.auto_clear
                    and latest.last_notice_message_id
                    and (clear_live or (_utc(latest.last_notice_sent_at) or newest_observed) <= newest_observed)
                )
                try:
                    saved = await record_quiet_activity(
                        guild_id,
                        activity_at=newest_observed,
                        clear_delivery=should_clear_live,
                    )
                except CommunityStorageUnavailable:
                    # Keep the newest activity in memory, but do not destroy a live
                    # notice whose durable delivery record could not be cleared.
                    self._guild_last_activity[guild_id] = newest_observed
                    return
                if saved is not None:
                    self.set_quiet_config(saved)
                    self._quiet_last_persisted[guild_id] = newest_observed
                    if should_clear_live:
                        await self.delete_quiet_live_message(latest)
        finally:
            self._quiet_activity_pending.discard(guild_id)

    async def _refresh_from_activity(self, channel: Any, config: StickyConfig) -> None:
        channel_id = int(getattr(channel, "id", config.channel_id) or config.channel_id)
        try:
            # The listener already evaluated the time/message trigger. Force the
            # serialized worker instead of re-reading the intentionally reset count.
            await self.refresh_channel(channel, expected_config=config, force=True)
        finally:
            self._pending_refreshes.discard(channel_id)

    async def _bounded(self, jobs: Iterable[Awaitable[Any]]) -> None:
        semaphore = asyncio.Semaphore(STARTUP_RECONCILE_CONCURRENCY)

        async def run(job: Awaitable[Any]) -> None:
            async with semaphore:
                try:
                    await job
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    _log(f"startup reconciliation item failed: {type(exc).__name__}: {exc}")

        await asyncio.gather(*(run(job) for job in jobs))

    async def on_ready(self) -> None:
        async with self._ready_lock:
            try:
                configs = await list_stickies(enabled_only=True)
            except CommunityStorageUnavailable as exc:
                _log(f"sticky startup load unavailable: {exc}")
                configs = []
            self._configs = {int(item.channel_id): item for item in configs}
            self._counts = {int(item.channel_id): 0 for item in configs}
            await self._bounded(self._register_poll_view(config) for config in configs)
            await self._bounded(self._reconcile_config(config) for config in configs)

            try:
                quiet_configs = await list_quiet_notices(enabled_only=True)
            except CommunityStorageUnavailable as exc:
                _log(f"quiet-notice startup load unavailable: {exc}")
                quiet_configs = []
            self._quiet_configs = {int(item.guild_id): item for item in quiet_configs}
            self._guild_last_activity = {}
            self._quiet_last_persisted = {}
            now = datetime.now(timezone.utc)
            for config in quiet_configs:
                guild_id = int(config.guild_id)
                activity = _utc(config.last_activity_at or config.updated_at, fallback=now) or now
                self._guild_last_activity[guild_id] = activity
                self._quiet_last_persisted[guild_id] = activity
            await self._bounded(self._reconcile_quiet_config(config) for config in quiet_configs)
            if quiet_configs:
                self._ensure_quiet_watch_task()

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

    async def _reconcile_quiet_config(self, config: QuietNoticeConfig) -> None:
        if not config.last_notice_message_id:
            return
        channel = self.bot.get_channel(int(config.channel_id))
        if not isinstance(channel, discord.TextChannel):
            return
        activity = _utc(config.last_activity_at)
        sent_at = _utc(config.last_notice_sent_at)
        if config.auto_clear and activity is not None and sent_at is not None and activity > sent_at:
            try:
                saved = await clear_quiet_delivery(int(config.guild_id))
            except CommunityStorageUnavailable:
                return
            if saved is not None:
                self.set_quiet_config(saved)
                await self.delete_quiet_live_message(config)
            return
        try:
            await channel.fetch_message(int(config.last_notice_message_id))
        except discord.NotFound:
            try:
                saved = await clear_quiet_delivery(int(config.guild_id))
            except CommunityStorageUnavailable:
                return
            if saved is not None:
                self.set_quiet_config(saved)
        except (discord.Forbidden, discord.HTTPException):
            return

    async def _quiet_watch_loop(self) -> None:
        try:
            await self.bot.wait_until_ready()
            while not self.bot.is_closed():
                try:
                    await self._check_quiet_notices()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    _log(f"quiet watcher iteration failed: {type(exc).__name__}: {exc}")
                await asyncio.sleep(QUIET_CHECK_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log(f"quiet watcher stopped unexpectedly: {type(exc).__name__}: {exc}")

    async def _check_quiet_notices(self, *, now: Optional[datetime] = None) -> None:
        current = _utc(now, fallback=datetime.now(timezone.utc)) or datetime.now(timezone.utc)
        for guild_id, config in list(self._quiet_configs.items()):
            if not config.enabled:
                continue
            activity = self._guild_last_activity.get(guild_id) or _utc(config.last_activity_at)
            if not should_send_quiet_notice(config, last_activity_at=activity, now=current):
                continue
            try:
                await self._post_quiet_notice(guild_id, current)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log(f"quiet notice failed guild={guild_id}: {type(exc).__name__}: {exc}")

    async def _post_quiet_notice(self, guild_id: int, now: datetime) -> None:
        lock = self._quiet_locks.setdefault(int(guild_id), asyncio.Lock())
        async with lock:
            config = self._quiet_configs.get(int(guild_id))
            if config is None or not config.enabled:
                return
            activity = self._guild_last_activity.get(int(guild_id)) or _utc(config.last_activity_at)
            if not should_send_quiet_notice(config, last_activity_at=activity, now=now):
                return
            channel = self.bot.get_channel(int(config.channel_id))
            if not isinstance(channel, discord.TextChannel):
                return
            try:
                message = await channel.send(
                    embed=quiet_notice_embed(config),
                    view=quiet_notice_view(config),
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except (discord.Forbidden, discord.HTTPException):
                return
            try:
                saved = await update_quiet_delivery(
                    int(guild_id),
                    message_id=int(message.id),
                    sent_at=now,
                )
            except CommunityStorageUnavailable:
                await self._delete_message_object(message)
                return
            if saved is None:
                await self._delete_message_object(message)
                return
            self.set_quiet_config(saved)

    async def _delete_message_object(self, message: Any) -> bool:
        try:
            await message.delete()
            return True
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return False
        except Exception:
            return False

    async def _delete_previous(self, channel: discord.TextChannel, message_id: Optional[int]) -> bool:
        if not message_id:
            return True
        try:
            message = await channel.fetch_message(int(message_id))
        except discord.NotFound:
            return True
        except (discord.Forbidden, discord.HTTPException):
            return False
        return await self._delete_message_object(message)

    async def _managed_webhook(self, channel: discord.TextChannel) -> Optional[discord.Webhook]:
        channel_id = int(channel.id)
        cached = self._webhooks.get(channel_id)
        if cached is not None:
            return cached

        me = channel.guild.me
        if me is None:
            return None
        perms = channel.permissions_for(me)
        # Bot-authenticated deletion of an older webhook-authored message needs
        # Manage Messages. Requiring both permissions keeps Custom Sender from
        # accumulating orphaned copies after every sticky movement.
        if not (perms.manage_webhooks and perms.manage_messages):
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

    async def ensure_managed_webhook(self, channel: Any) -> bool:
        if not isinstance(channel, discord.TextChannel):
            return False
        return await self._managed_webhook(channel) is not None

    async def cleanup_managed_webhook(self, channel: Any) -> None:
        if not isinstance(channel, discord.TextChannel):
            return
        channel_id = int(channel.id)
        cached = self._webhooks.pop(channel_id, None)
        if cached is not None:
            try:
                await cached.delete(reason="Dank Shield sticky custom sender disabled")
                return
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass
        me = channel.guild.me
        if me is None or not channel.permissions_for(me).manage_webhooks:
            return
        try:
            webhooks = await channel.webhooks()
        except (discord.Forbidden, discord.HTTPException):
            return
        bot_id = int(getattr(getattr(self.bot, "user", None), "id", 0) or 0)
        for webhook in webhooks:
            owner_id = int(getattr(getattr(webhook, "user", None), "id", 0) or 0)
            if webhook.name != MANAGED_WEBHOOK_NAME or owner_id != bot_id:
                continue
            try:
                await webhook.delete(reason="Dank Shield sticky custom sender disabled")
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

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
            if webhook is None:
                raise InvalidCommunityToolValue(
                    "Custom Sender is enabled, but Dank Shield does not have Manage Webhooks + Manage Messages in this channel."
                )
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
                raise InvalidCommunityToolValue("Dank Shield's managed webhook did not return a message.")
            except (discord.Forbidden, discord.HTTPException, ValueError) as exc:
                self._webhooks.pop(int(channel.id), None)
                raise InvalidCommunityToolValue("The configured custom sender could not post in this channel.") from exc

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

            previous_message_id = current.last_message_id
            try:
                message = await self._send(channel, current)
            except (discord.Forbidden, discord.HTTPException, InvalidCommunityToolValue, CommunityStorageUnavailable):
                return None

            sent_at = datetime.now(timezone.utc)
            try:
                saved = await update_sticky_delivery(
                    channel_id,
                    message_id=int(message.id),
                    sent_at=sent_at,
                )
            except CommunityStorageUnavailable:
                # Delivery state is authoritative. If it cannot be recorded, roll
                # back the new post and leave the previous known-good sticky alone.
                await self._delete_message_object(message)
                return None
            if saved is None:
                await self._delete_message_object(message)
                return None

            self.set_config(saved)
            if previous_message_id and int(previous_message_id) != int(message.id):
                await self._delete_previous(channel, previous_message_id)
            return message

    async def cast_and_render_poll_vote(
        self,
        interaction: discord.Interaction,
        channel_id: int,
        option_index: int,
    ) -> None:
        lock = self._poll_render_locks.setdefault(int(channel_id), asyncio.Lock())
        async with lock:
            try:
                current = await get_sticky(int(channel_id))
                clicked_message_id = int(getattr(getattr(interaction, "message", None), "id", 0) or 0)
                if (
                    current is None
                    or current.mode != "poll"
                    or not current.last_message_id
                    or clicked_message_id != int(current.last_message_id)
                ):
                    return await _private(
                        interaction,
                        "❌ This is an older sticky-poll card. Use the newest poll message so your vote cannot land on a stale copy.",
                    )
                poll = await cast_sticky_poll_vote(
                    int(channel_id),
                    int(interaction.user.id),
                    int(option_index),
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

    async def delete_live_message(self, channel: Any, message_id: Optional[int]) -> None:
        if isinstance(channel, discord.TextChannel):
            await self._delete_previous(channel, message_id)

    async def delete_quiet_live_message(self, config: QuietNoticeConfig) -> None:
        if not config.last_notice_message_id:
            return
        channel = self.bot.get_channel(int(config.channel_id))
        if isinstance(channel, discord.TextChannel):
            await self._delete_previous(channel, config.last_notice_message_id)


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
    "QUIET_ACTIVITY_PERSIST_SECONDS",
    "QUIET_CHECK_SECONDS",
    "STARTUP_RECONCILE_CONCURRENCY",
    "StickyPollView",
    "StickyRuntime",
    "community_tools_runtime",
    "ensure_community_tools_runtime",
    "quiet_notice_embed",
    "quiet_notice_view",
    "should_refresh_sticky",
    "should_send_quiet_notice",
    "sticky_embed",
    "sticky_poll_embed",
]
