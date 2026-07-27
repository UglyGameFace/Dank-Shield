from __future__ import annotations

"""Public live-profile runtime with one burst-safe card per member/channel.

This module intentionally uses Discord's bot-owned signature messages as the
restart-safe ownership record. A configured channel is scanned once, lazily,
after process start; warm traffic stays entirely in memory. That avoids both a
per-message storage round trip and the old one-card-per-channel data model.
"""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Mapping, Optional

import discord

from . import profile_card_runtime as _legacy

MemberKey = tuple[int, int, int]
ChannelKey = tuple[int, int]

_HISTORY_WARM_LIMIT = 200
_CHANNEL_SEND_GAP_SECONDS = 0.20
_CLEANUP_RETRY_DELAYS = (0.0, 0.25, 0.75)


@dataclass
class _MemberCard:
    message_id: int
    user_id: int
    trigger_message_id: int
    message: Optional[discord.Message] = None


class PerMemberLiveProfileCardRuntime(_legacy.LiveProfileCardRuntime):
    """Keep at most one public signature per member in each configured channel."""

    def __init__(
        self,
        bot: Any,
        *,
        renderer: _legacy.RenderProfile = _legacy.render_live_profile_card,
        sleep: _legacy.Sleep = asyncio.sleep,
    ) -> None:
        super().__init__(bot, renderer=renderer, sleep=sleep)

        # A single worker owns the complete leading/trailing lifecycle for one
        # member in one channel. There cannot be a leading/trailing delete race.
        self._workers: dict[MemberKey, asyncio.Task[Any]] = {}
        self._pending = self._workers
        self._latest: dict[MemberKey, _legacy.PendingTrigger] = {}
        self._latest_messages: dict[MemberKey, discord.Message] = {}
        self._latest_configs: dict[MemberKey, _legacy.LiveCardConfig] = {}
        self._wake_events: dict[MemberKey, asyncio.Event] = {}
        self._locks: dict[MemberKey, asyncio.Lock] = {}
        self._last_activity: dict[MemberKey, float] = {}
        self._last_posted: dict[MemberKey, tuple[int, float]] = {}

        # Discord messages are the durable ownership source. Warm channels use
        # this map and never read durable state or scan history per message.
        self._current_cards: dict[MemberKey, _MemberCard] = {}
        self._warmed_channels: set[ChannelKey] = set()
        self._warm_locks: dict[ChannelKey, asyncio.Lock] = {}

        # Busy channels serialize bot output and keep a small gap between cards.
        # Human messages remain untouched and the first quiet-channel card has no
        # artificial delay.
        self._channel_send_locks: dict[ChannelKey, asyncio.Lock] = {}
        self._channel_next_send_at: dict[ChannelKey, float] = {}

        self._trigger_received_at: dict[tuple[int, int, int, int], float] = {}
        self._cleanup_tasks: set[asyncio.Task[Any]] = set()

    async def on_ready(self) -> None:
        # Never scan every public server on reconnect. Each active configured
        # channel performs one bounded warmup on its first human message.
        self._last_reconcile_at = _legacy.monotonic()
        print(
            "🪪 live_profile_card ready mode=per_member "
            "lazy_channel_warmup=enabled startup_history_scan=skipped"
        )

    async def on_message(self, message: discord.Message) -> None:
        if not _legacy._is_supported_message(message):
            return

        try:
            raw_config = await _legacy.get_guild_config(message.guild.id)
            config = _legacy.parse_live_card_config(raw_config)
        except Exception as exc:
            print(
                "⚠️ live_profile_card skipped "
                f"guild={message.guild.id} channel={message.channel.id} user={message.author.id} "
                f"reason=config_read_failed error={type(exc).__name__}: {exc}"
            )
            return

        if not config.enabled or int(message.channel.id) not in config.channel_ids:
            return
        if not _legacy._channel_can_host_cards(message.channel):
            print(
                "⚠️ live_profile_card skipped "
                f"guild={message.guild.id} channel={message.channel.id} user={message.author.id} "
                "reason=channel_permissions_incomplete"
            )
            return

        key: MemberKey = (
            int(message.guild.id),
            int(message.channel.id),
            int(message.author.id),
        )
        now = _legacy.monotonic()
        prior_activity = self._last_activity.get(key)
        immediate = (
            prior_activity is None
            or now - prior_activity >= config.same_speaker_cooldown_seconds
        )
        self._last_activity[key] = now

        trigger = _legacy.PendingTrigger(
            guild_id=key[0],
            channel_id=key[1],
            user_id=key[2],
            message_id=int(message.id),
            delay_seconds=0.0 if immediate else config.replacement_cooldown_seconds,
        )
        self._latest[key] = trigger
        self._latest_messages[key] = message
        self._latest_configs[key] = config
        self._trigger_received_at[(key[0], key[1], key[2], trigger.message_id)] = now
        self._prune_trigger_times()

        running = self._workers.get(key)
        if self._task_running(running):
            self._wake_events.setdefault(key, asyncio.Event()).set()
            return

        task = asyncio.create_task(self._member_worker(key, immediate=immediate))
        self._workers[key] = task
        task.add_done_callback(
            lambda finished, resolved_key=key: self._member_worker_done(resolved_key, finished)
        )

    async def _member_worker(self, key: MemberKey, *, immediate: bool) -> None:
        posted_trigger_id: Optional[int] = None
        event = self._wake_events.setdefault(key, asyncio.Event())
        lock = self._locks.setdefault(key, asyncio.Lock())

        try:
            if immediate:
                # Same-event-loop messages collapse before rendering without a
                # user-visible debounce timer.
                await asyncio.sleep(0)
                trigger = self._latest.get(key)
                message = self._latest_messages.get(key)
                config = self._latest_configs.get(key)
                if trigger is not None and message is not None and config is not None:
                    async with lock:
                        await self._replace_member_card(
                            message,
                            config,
                            trigger,
                            source="leading",
                        )
                    current = self._current_cards.get(key)
                    if current is not None:
                        posted_trigger_id = current.trigger_message_id

            while True:
                config = self._latest_configs.get(key)
                quiet_seconds = (
                    config.replacement_cooldown_seconds
                    if config is not None
                    else _legacy.DEFAULT_REPLACEMENT_COOLDOWN_SECONDS
                )
                elapsed = _legacy.monotonic() - self._last_activity.get(
                    key, _legacy.monotonic()
                )
                remaining = max(0.0, quiet_seconds - elapsed)
                if remaining > 0:
                    event.clear()
                    try:
                        await asyncio.wait_for(event.wait(), timeout=remaining)
                        continue
                    except asyncio.TimeoutError:
                        pass

                trigger = self._latest.get(key)
                message = self._latest_messages.get(key)
                config = self._latest_configs.get(key)
                if trigger is None or message is None or config is None:
                    return
                if posted_trigger_id != trigger.message_id:
                    async with lock:
                        # Re-read after acquiring the member lock so a message
                        # arriving during a slow render can only become the final
                        # trailing target, never a second concurrent sender.
                        trigger = self._latest.get(key, trigger)
                        message = self._latest_messages.get(key, message)
                        config = self._latest_configs.get(key, config)
                        await self._replace_member_card(
                            message,
                            config,
                            trigger,
                            source="trailing" if immediate else "settled",
                        )
                return
        finally:
            self._release_member_context(key)

    def _member_worker_done(
        self,
        key: MemberKey,
        task: asyncio.Task[Any],
    ) -> None:
        if self._workers.get(key) is task:
            self._workers.pop(key, None)
        self._consume_task_result(task)

    def _release_member_context(self, key: MemberKey) -> None:
        self._latest.pop(key, None)
        self._latest_messages.pop(key, None)
        self._latest_configs.pop(key, None)
        self._wake_events.pop(key, None)
        for trigger_key in list(self._trigger_received_at):
            if trigger_key[:3] == key:
                self._trigger_received_at.pop(trigger_key, None)

    def _prune_trigger_times(self) -> None:
        if len(self._trigger_received_at) <= 4096:
            return
        oldest = sorted(
            self._trigger_received_at.items(), key=lambda item: item[1]
        )[:1024]
        for trigger_key, _created_at in oldest:
            self._trigger_received_at.pop(trigger_key, None)

    async def _ensure_channel_warm(self, channel: discord.TextChannel) -> None:
        channel_key: ChannelKey = (int(channel.guild.id), int(channel.id))
        if channel_key in self._warmed_channels:
            return

        warm_lock = self._warm_locks.setdefault(channel_key, asyncio.Lock())
        async with warm_lock:
            if channel_key in self._warmed_channels:
                return

            newest_by_user: dict[int, _MemberCard] = {}
            duplicates: list[tuple[discord.Message, int]] = []
            bot_user = getattr(self.bot, "user", None)
            bot_id = int(getattr(bot_user, "id", 0) or 0)

            try:
                async for candidate in channel.history(limit=_HISTORY_WARM_LIMIT):
                    if bot_id <= 0 or int(getattr(candidate.author, "id", 0) or 0) != bot_id:
                        continue
                    parsed = _legacy.parse_live_card_footer(candidate)
                    if parsed is None:
                        continue
                    user_id, trigger_message_id = parsed
                    if int(user_id) in newest_by_user:
                        duplicates.append((candidate, int(user_id)))
                        continue
                    newest_by_user[int(user_id)] = _MemberCard(
                        message_id=int(candidate.id),
                        user_id=int(user_id),
                        trigger_message_id=int(trigger_message_id),
                        message=candidate,
                    )
            except Exception as exc:
                print(
                    "⚠️ live_profile_card lazy warmup failed "
                    f"guild={channel.guild.id} channel={channel.id} "
                    f"error={type(exc).__name__}: {exc}"
                )
                # Fail closed: do not post when existing ownership cannot be
                # inspected, otherwise a restart could create duplicate cards.
                raise

            for user_id, card in newest_by_user.items():
                self._current_cards[(channel_key[0], channel_key[1], user_id)] = card

            for duplicate, expected_user_id in duplicates:
                removed = await self._delete_known_card(
                    channel,
                    _MemberCard(
                        message_id=int(duplicate.id),
                        user_id=expected_user_id,
                        trigger_message_id=int(
                            (_legacy.parse_live_card_footer(duplicate) or (0, 0))[1]
                        ),
                        message=duplicate,
                    ),
                )
                if not removed:
                    self._schedule_cleanup_retry(
                        channel,
                        _MemberCard(
                            message_id=int(duplicate.id),
                            user_id=expected_user_id,
                            trigger_message_id=0,
                            message=duplicate,
                        ),
                    )

            # The v1 storage row represented one card for the whole channel and
            # cannot express per-member ownership. Clear it after Discord history
            # has been adopted so stale rows cannot delete another member's card.
            try:
                await _legacy.delete_live_card_state(*channel_key)
            except Exception:
                pass

            self._warmed_channels.add(channel_key)
            print(
                "🪪 live_profile_card channel warmed "
                f"guild={channel_key[0]} channel={channel_key[1]} "
                f"members={len(newest_by_user)} duplicates_removed={len(duplicates)}"
            )

    @asynccontextmanager
    async def _channel_output_slot(self, channel_key: ChannelKey):
        lock = self._channel_send_locks.setdefault(channel_key, asyncio.Lock())
        async with lock:
            now = _legacy.monotonic()
            delay = max(0.0, self._channel_next_send_at.get(channel_key, now) - now)
            if delay > 0:
                await self.sleep(delay)
            try:
                yield
            finally:
                self._channel_next_send_at[channel_key] = (
                    _legacy.monotonic() + _CHANNEL_SEND_GAP_SECONDS
                )

    async def _replace_member_card(
        self,
        message: discord.Message,
        config: _legacy.LiveCardConfig,
        trigger: _legacy.PendingTrigger,
        *,
        source: str,
    ) -> None:
        channel = message.channel
        guild = message.guild
        if not isinstance(channel, discord.TextChannel) or guild is None:
            return

        message_author = getattr(message, "author", None)
        if (
            isinstance(message_author, discord.Member)
            and int(message_author.id) == int(trigger.user_id)
        ):
            member = message_author
        else:
            member = guild.get_member(trigger.user_id)
        if not isinstance(member, discord.Member):
            return
        if not _legacy._channel_can_host_cards(channel):
            return

        try:
            await self._ensure_channel_warm(channel)
        except Exception:
            return

        key: MemberKey = (
            int(trigger.guild_id),
            int(trigger.channel_id),
            int(trigger.user_id),
        )
        current = self._current_cards.get(key)
        if current is not None and current.trigger_message_id == int(trigger.message_id):
            return

        render_started = _legacy.monotonic()
        try:
            rendered = await self.renderer(
                member,
                set(config.allowed_fields),
                trigger_message_id=trigger.message_id,
            )
        except Exception as exc:
            print(
                "⚠️ live_profile_card render failed "
                f"guild={trigger.guild_id} channel={trigger.channel_id} user={trigger.user_id} "
                f"error={type(exc).__name__}: {exc}"
            )
            return
        if rendered is None:
            return
        render_ms = int((_legacy.monotonic() - render_started) * 1000)

        channel_key: ChannelKey = (int(trigger.guild_id), int(trigger.channel_id))
        try:
            async with self._channel_output_slot(channel_key):
                new_message = await channel.send(**_legacy._live_card_send_payload(rendered))
        except Exception as exc:
            print(
                "⚠️ live_profile_card send failed "
                f"guild={trigger.guild_id} channel={trigger.channel_id} user={trigger.user_id} "
                f"error={type(exc).__name__}: {exc}"
            )
            return

        new_card = _MemberCard(
            message_id=int(new_message.id),
            user_id=int(trigger.user_id),
            trigger_message_id=int(trigger.message_id),
            message=new_message,
        )
        old = current
        self._current_cards[key] = new_card
        self._last_posted[key] = (int(trigger.user_id), _legacy.monotonic())

        received_at = self._trigger_received_at.pop(
            (
                int(trigger.guild_id),
                int(trigger.channel_id),
                int(trigger.user_id),
                int(trigger.message_id),
            ),
            render_started,
        )
        total_ms = int((_legacy.monotonic() - received_at) * 1000)
        print(
            "✅ live_profile_card posted "
            f"guild={trigger.guild_id} channel={trigger.channel_id} user={trigger.user_id} "
            f"message={new_message.id} trigger={trigger.message_id} source={source} "
            f"render_ms={render_ms} total_ms={total_ms} ownership=per_member"
        )

        if old is not None and old.message_id != new_card.message_id:
            removed = await self._delete_known_card(channel, old)
            if not removed:
                self._schedule_cleanup_retry(channel, old)

    # Preserve the established internal callback name used by setup code and
    # regression tests while routing it through per-member ownership.
    async def _replace_card(
        self,
        message: discord.Message,
        config: _legacy.LiveCardConfig,
        trigger: _legacy.PendingTrigger,
        *,
        force_reposition: bool = False,
        source: str = "direct",
    ) -> None:
        del force_reposition
        await self._replace_member_card(message, config, trigger, source=source)

    async def _delete_known_card(
        self,
        channel: discord.TextChannel,
        card: _MemberCard,
    ) -> bool:
        stored = card.message
        if stored is None:
            try:
                stored = await channel.fetch_message(int(card.message_id))
            except discord.NotFound:
                return True
            except Exception:
                return False

        bot_user = getattr(self.bot, "user", None)
        if bot_user is None:
            return False
        if int(getattr(stored.author, "id", 0) or 0) != int(bot_user.id):
            return False
        parsed = _legacy.parse_live_card_footer(stored)
        if parsed is None or int(parsed[0]) != int(card.user_id):
            return False
        try:
            await stored.delete()
            return True
        except discord.NotFound:
            return True
        except Exception:
            return False

    def _schedule_cleanup_retry(
        self,
        channel: discord.TextChannel,
        card: _MemberCard,
    ) -> None:
        task = asyncio.create_task(self._retry_card_cleanup(channel, card))
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_task_done)

    async def _retry_card_cleanup(
        self,
        channel: discord.TextChannel,
        card: _MemberCard,
    ) -> None:
        for delay in _CLEANUP_RETRY_DELAYS:
            if delay > 0:
                await self.sleep(delay)
            if await self._delete_known_card(channel, card):
                return
        print(
            "⚠️ live_profile_card duplicate cleanup exhausted "
            f"guild={channel.guild.id} channel={channel.id} "
            f"user={card.user_id} message={card.message_id}"
        )

    def _cleanup_task_done(self, task: asyncio.Task[Any]) -> None:
        self._cleanup_tasks.discard(task)
        self._consume_task_result(task)

    async def _configured_channels(self, guild: discord.Guild) -> list[discord.TextChannel]:
        try:
            config = _legacy.parse_live_card_config(
                await _legacy.get_guild_config(guild.id)
            )
        except Exception:
            return []
        channels: list[discord.TextChannel] = []
        for channel_id in config.channel_ids:
            channel = guild.get_channel(channel_id)
            if isinstance(channel, discord.TextChannel):
                channels.append(channel)
        return channels

    async def reconcile(self) -> None:
        guilds = list(getattr(self.bot, "guilds", []) or [])
        if len(guilds) > 5:
            print(
                "🪪 live_profile_card reconcile mode=lazy_per_member "
                f"guilds={len(guilds)}"
            )
            return
        for guild in guilds:
            await self.reconcile_guild(guild)

    async def reconcile_deep(self) -> None:
        for guild in list(getattr(self.bot, "guilds", []) or []):
            await self.reconcile_guild(guild)

    async def reconcile_guild(self, guild: discord.Guild) -> None:
        for channel in await self._configured_channels(guild):
            try:
                await self._ensure_channel_warm(channel)
            except Exception:
                continue

    async def remove_user_cards(self, guild: discord.Guild, user_id: int) -> None:
        resolved_user = int(user_id)
        self._cancel_member_workers(resolved_user, guild_id=int(guild.id))
        channels = await self._configured_channels(guild)
        for channel in channels:
            try:
                await self._ensure_channel_warm(channel)
            except Exception:
                continue
            key = (int(guild.id), int(channel.id), resolved_user)
            card = self._current_cards.pop(key, None)
            if card is not None and not await self._delete_known_card(channel, card):
                self._schedule_cleanup_retry(channel, card)

    async def remove_user_cards_all_guilds(self, user_id: int) -> None:
        for guild in list(getattr(self.bot, "guilds", []) or []):
            await self.remove_user_cards(guild, int(user_id))

    async def invalidate_guild_cards(self, guild: discord.Guild) -> None:
        for channel in await self._configured_channels(guild):
            await self.disable_channel(guild, channel)

    async def disable_channel(
        self,
        guild: discord.Guild,
        channel: discord.TextChannel,
    ) -> None:
        channel_key = (int(guild.id), int(channel.id))
        try:
            await self._ensure_channel_warm(channel)
        except Exception:
            pass
        self._cancel_channel_workers(channel_key)
        for key, card in list(self._current_cards.items()):
            if key[:2] != channel_key:
                continue
            self._current_cards.pop(key, None)
            if not await self._delete_known_card(channel, card):
                self._schedule_cleanup_retry(channel, card)
        try:
            await _legacy.delete_live_card_state(*channel_key)
        except Exception:
            pass
        self._forget_channel(channel_key, cancel_tasks=False)

    async def _remove_channel_card_state(
        self,
        guild: discord.Guild,
        channel_id: int,
        *,
        cancel_pending: bool = True,
    ) -> bool:
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            self._forget_channel((int(guild.id), int(channel_id)), cancel_tasks=cancel_pending)
            try:
                await _legacy.delete_live_card_state(int(guild.id), int(channel_id))
            except Exception:
                pass
            return False
        await self.disable_channel(guild, channel)
        return True

    async def on_member_remove(self, member: discord.Member) -> None:
        await self.remove_user_cards(member.guild, member.id)

    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        if isinstance(channel, discord.TextChannel):
            self._forget_channel((int(channel.guild.id), int(channel.id)))

    def _cancel_member_workers(
        self,
        user_id: int,
        *,
        guild_id: Optional[int] = None,
    ) -> None:
        for key, task in list(self._workers.items()):
            if key[2] != int(user_id):
                continue
            if guild_id is not None and key[0] != int(guild_id):
                continue
            self._workers.pop(key, None)
            if self._task_running(task):
                task.cancel()
            self._release_member_context(key)

    def _cancel_channel_workers(self, channel_key: ChannelKey) -> None:
        for key, task in list(self._workers.items()):
            if key[:2] != channel_key:
                continue
            self._workers.pop(key, None)
            if self._task_running(task):
                task.cancel()
            self._release_member_context(key)

    def _forget_channel(
        self,
        channel_key: ChannelKey,
        *,
        cancel_tasks: bool = True,
    ) -> None:
        if cancel_tasks:
            self._cancel_channel_workers(channel_key)
        for key in list(self._current_cards):
            if key[:2] == channel_key:
                self._current_cards.pop(key, None)
        for key in list(self._last_activity):
            if key[:2] == channel_key:
                self._last_activity.pop(key, None)
                self._last_posted.pop(key, None)
                self._locks.pop(key, None)
        self._warmed_channels.discard(channel_key)
        self._warm_locks.pop(channel_key, None)
        self._channel_send_locks.pop(channel_key, None)
        self._channel_next_send_at.pop(channel_key, None)


def is_internal_live_signature_message(message: Any, *, bot_user_id: Optional[int] = None) -> bool:
    """Return True only for a bot-owned Dank Shield live-signature message.

    SpamGuard and raid detectors may use this in addition to their mandatory
    human-author check. The marker check prevents unrelated webhook/bot traffic
    from being misclassified as a Dank Shield signature.
    """

    author = getattr(message, "author", None)
    if author is None or not bool(getattr(author, "bot", False)):
        return False
    if bot_user_id is not None and int(getattr(author, "id", 0) or 0) != int(bot_user_id):
        return False
    return _legacy.parse_live_card_footer(message) is not None


LiveProfileCardRuntime = PerMemberLiveProfileCardRuntime
