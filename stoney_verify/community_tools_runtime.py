from __future__ import annotations

"""Single-owner runtime for Dank Shield Quiet Server Notices.

This module used to also run the old Community Tools sticky
engine; that system was removed (stickies and polls live in Post
Builder now). The class keeps its historical name because startup
registration and notification ownership refer to it, but everything it
does today is quiet notices: observing guild activity, posting one
notice per quiet period, auto-clearing it when activity returns, and
reconciling delivery at startup.
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Iterable, Optional

import discord

from .startup_guards.discord_api_safety import fetch_message_with_api_safety
from .community_quiet_notice_service import (
    QuietNoticeConfig,
    clear_quiet_delivery,
    list_quiet_notices,
    record_quiet_activity,
    update_quiet_delivery,
)
from .community_tools_service import CommunityStorageUnavailable

_RUNTIME_ATTR = "_dank_community_tools_runtime"
QUIET_CHECK_SECONDS = 30
QUIET_ACTIVITY_PERSIST_SECONDS = 60
QUIET_RETRY_BACKOFF_SECONDS = 60
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


class StickyRuntime:
    """Owns quiet notices and their startup reconciliation for the whole bot.

    (Historical name: this class also ran the removed Community Tools
    sticky engine. Startup registration keys off it, so the name stays.)
    """

    def __init__(self, bot: Any) -> None:
        self.bot = bot
        self._ready_lock = asyncio.Lock()

        self._quiet_configs: dict[int, QuietNoticeConfig] = {}
        self._guild_last_activity: dict[int, datetime] = {}
        self._quiet_last_persisted: dict[int, datetime] = {}
        self._quiet_locks: dict[int, asyncio.Lock] = {}
        self._quiet_activity_pending: set[int] = set()
        self._quiet_retry_after: dict[int, float] = {}
        self._quiet_watch_task: Optional[asyncio.Task[Any]] = None


    def set_quiet_config(self, config: QuietNoticeConfig) -> None:
        guild_id = int(config.guild_id)
        self._quiet_configs[guild_id] = config
        activity = _utc(config.last_activity_at)
        if activity is not None:
            current = self._guild_last_activity.get(guild_id)
            if current is None or activity > current:
                self._guild_last_activity[guild_id] = activity
            self._quiet_last_persisted[guild_id] = activity
        if not config.last_notice_message_id:
            self._quiet_retry_after.pop(guild_id, None)
        if config.enabled:
            self._ensure_quiet_watch_task()

    def remove_quiet_config(self, guild_id: int) -> None:
        guild_key = int(guild_id)
        self._quiet_configs.pop(guild_key, None)
        self._guild_last_activity.pop(guild_key, None)
        self._quiet_last_persisted.pop(guild_key, None)
        self._quiet_activity_pending.discard(guild_key)
        self._quiet_retry_after.pop(guild_key, None)

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

    def _observe_quiet_activity(self, message: discord.Message, config: QuietNoticeConfig) -> None:
        guild_id = int(config.guild_id)
        observed = _utc(getattr(message, "created_at", None), fallback=datetime.now(timezone.utc))
        if observed is None:
            return
        current = self._guild_last_activity.get(guild_id)
        if current is None or observed > current:
            self._guild_last_activity[guild_id] = observed

        retry_after = self._quiet_retry_after.get(guild_id)
        if retry_after is not None:
            if time.monotonic() < retry_after:
                return
            self._quiet_retry_after.pop(guild_id, None)

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
                try:
                    saved = await record_quiet_activity(
                        guild_id,
                        activity_at=newest_observed,
                    )
                except CommunityStorageUnavailable:
                    # Keep the newest activity in memory, but never delete a live
                    # notice unless its durable delivery identity is still retained.
                    # Back off retries so a busy guild cannot hammer unavailable storage.
                    self._guild_last_activity[guild_id] = newest_observed
                    self._quiet_retry_after[guild_id] = time.monotonic() + QUIET_RETRY_BACKOFF_SECONDS
                    return
                if saved is None:
                    return

                self.set_quiet_config(saved)
                self._quiet_last_persisted[guild_id] = newest_observed

                sent_at = _utc(saved.last_notice_sent_at)
                same_observed_delivery = bool(
                    clear_live
                    and latest.last_notice_message_id
                    and saved.last_notice_message_id == latest.last_notice_message_id
                )
                delivery_precedes_activity = sent_at is None or sent_at <= newest_observed
                should_clear_live = bool(
                    saved.auto_clear
                    and saved.last_notice_message_id
                    and (same_observed_delivery or delivery_precedes_activity)
                )
                if not should_clear_live:
                    return

                deleted = await self.delete_quiet_live_message(saved)
                if not deleted:
                    # Keep the durable message id so a later activity/startup pass
                    # can retry without orphaning the Discord notice. Back off the
                    # hot path so busy guilds do not retry once per message.
                    self._quiet_retry_after[guild_id] = time.monotonic() + QUIET_RETRY_BACKOFF_SECONDS
                    return
                try:
                    cleared = await clear_quiet_delivery(
                        guild_id,
                        expected_message_id=int(saved.last_notice_message_id),
                    )
                except CommunityStorageUnavailable:
                    # Discord deletion already succeeded. Retaining the id is safe:
                    # the next pass treats NotFound as a successful idempotent delete
                    # and retries only the durable clear, with bounded retry pressure.
                    self._quiet_retry_after[guild_id] = time.monotonic() + QUIET_RETRY_BACKOFF_SECONDS
                    return
                if cleared is not None:
                    self.set_quiet_config(cleared)
        finally:
            self._quiet_activity_pending.discard(guild_id)


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
                quiet_configs = await list_quiet_notices(enabled_only=True)
            except CommunityStorageUnavailable as exc:
                _log(f"quiet-notice startup load unavailable: {exc}")
                quiet_configs = []
            self._quiet_configs = {int(item.guild_id): item for item in quiet_configs}
            active_quiet_guilds = set(self._quiet_configs)
            self._quiet_retry_after = {
                guild_id: retry_after
                for guild_id, retry_after in self._quiet_retry_after.items()
                if guild_id in active_quiet_guilds
            }
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


    async def _reconcile_quiet_config(self, config: QuietNoticeConfig) -> None:
        guild_id = int(config.guild_id)
        lock = self._quiet_locks.setdefault(guild_id, asyncio.Lock())
        async with lock:
            current = self._quiet_configs.get(guild_id) or config
            if not current.last_notice_message_id:
                return

            retry_after = self._quiet_retry_after.get(guild_id)
            if retry_after is not None:
                if time.monotonic() < retry_after:
                    return
                self._quiet_retry_after.pop(guild_id, None)

            channel = self.bot.get_channel(int(current.channel_id))
            if not isinstance(channel, discord.TextChannel):
                return
            activity = _utc(current.last_activity_at)
            sent_at = _utc(current.last_notice_sent_at)
            if current.auto_clear and activity is not None and sent_at is not None and activity > sent_at:
                deleted = await self.delete_quiet_live_message(current)
                if not deleted:
                    self._quiet_retry_after[guild_id] = time.monotonic() + QUIET_RETRY_BACKOFF_SECONDS
                    return
                try:
                    saved = await clear_quiet_delivery(
                        guild_id,
                        expected_message_id=int(current.last_notice_message_id),
                    )
                except CommunityStorageUnavailable:
                    self._quiet_retry_after[guild_id] = time.monotonic() + QUIET_RETRY_BACKOFF_SECONDS
                    return
                if saved is not None:
                    self.set_quiet_config(saved)
                return
            try:
                await fetch_message_with_api_safety(
                    channel,
                    int(current.last_notice_message_id),
                    label=(
                        "community quiet startup "
                        f"guild={guild_id}"
                    ),
                    recovery=True,
                )
            except discord.NotFound:
                try:
                    saved = await clear_quiet_delivery(
                        guild_id,
                        expected_message_id=int(current.last_notice_message_id),
                    )
                except CommunityStorageUnavailable:
                    self._quiet_retry_after[guild_id] = time.monotonic() + QUIET_RETRY_BACKOFF_SECONDS
                    return
                if saved is not None:
                    self.set_quiet_config(saved)
            except (discord.Forbidden, discord.HTTPException):
                self._quiet_retry_after[guild_id] = time.monotonic() + QUIET_RETRY_BACKOFF_SECONDS
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


    async def delete_quiet_live_message(self, config: QuietNoticeConfig) -> bool:
        if not config.last_notice_message_id:
            return True
        channel = self.bot.get_channel(int(config.channel_id))
        if not isinstance(channel, discord.TextChannel):
            return False
        return await self._delete_previous(channel, config.last_notice_message_id)


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
    "QUIET_ACTIVITY_PERSIST_SECONDS",
    "QUIET_CHECK_SECONDS",
    "QUIET_RETRY_BACKOFF_SECONDS",
    "STARTUP_RECONCILE_CONCURRENCY",
    "StickyRuntime",
    "community_tools_runtime",
    "ensure_community_tools_runtime",
    "quiet_notice_embed",
    "quiet_notice_view",
    "should_send_quiet_notice",
]
