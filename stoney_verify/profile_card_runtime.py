from __future__ import annotations

"""Channel-scoped, burst-safe live profile signatures.

Discord cannot attach a bot-rendered image to another member's message. The only
safe public approximation is one bot-owned signature per configured channel,
representing the latest eligible speaker after the conversation becomes quiet.

This module intentionally keeps the proven renderer and lifecycle primitives from
the earlier channel-scoped implementation while replacing its scheduler and
ownership recovery with stricter fail-closed behavior:

* one worker, lock, durable row, and visible signature per channel;
* no immediate leading post that can land beneath a newer speaker;
* stale renders are rejected before cleanup, before send, and after send;
* every older Dank Shield signature is removed before a replacement is posted;
* cleanup/ownership uncertainty suppresses the new post instead of creating spam.
"""

import asyncio
from time import monotonic
from typing import Any, Mapping, Optional

import discord

from . import profile_card_runtime_legacy as _legacy
from .profile_card_runtime_legacy import (  # re-export compatibility surface
    LIVE_ALLOWED_FIELDS_KEY,
    LIVE_CARD_FOOTER_PREFIX,
    LIVE_CHANNEL_IDS_KEY,
    LIVE_DEBOUNCE_KEY,
    LIVE_ENABLED_KEY,
    LIVE_REPLACEMENT_COOLDOWN_KEY,
    LIVE_SAME_SPEAKER_COOLDOWN_KEY,
    READY_RECONCILE_THROTTLE_SECONDS,
    LiveCardConfig,
    LiveCardRender,
    PendingTrigger,
    _channel_can_host_cards,
    _channel_ids,
    _copy_base_profile_embed,
    _is_supported_message,
    _platform_view,
    live_card_footer,
    live_card_marker_url,
    parse_live_card_footer,
)
from .profile_card_service import (
    ProfileStorageUnavailable,
    delete_live_card_state,
    get_effective_profile_settings,
    get_live_card_state,
    list_live_card_states,
    list_live_card_states_for_channel,
    list_live_card_states_for_user,
    upsert_live_card_state,
    visible_platform_entries,
)

# A signature should appear only after the current burst has clearly settled.
# This prevents a rendered card for speaker A from landing below speaker B.
DEFAULT_DEBOUNCE_SECONDS = 0.85
DEFAULT_REPLACEMENT_COOLDOWN_SECONDS = DEFAULT_DEBOUNCE_SECONDS
DEFAULT_SAME_SPEAKER_COOLDOWN_SECONDS = 1.5
LIVE_CARD_HISTORY_SCAN_LIMIT = 100

get_guild_config = _legacy.get_guild_config
upsert_guild_config = _legacy.upsert_guild_config
render_member_profile_signature = _legacy.render_member_profile_signature
effective_profile_style = _legacy.effective_profile_style

_ChannelKey = tuple[int, int]
_TriggerTimeKey = tuple[int, int, int]
_CurrentCard = _legacy._CurrentCard
_CurrentCardVerificationUnavailable = _legacy._CurrentCardVerificationUnavailable
RenderProfile = _legacy.RenderProfile
Sleep = _legacy.Sleep


def parse_live_card_config(config: Mapping[str, Any]) -> LiveCardConfig:
    parsed = _legacy._core.parse_live_card_config(config)
    return LiveCardConfig(
        enabled=parsed.enabled,
        channel_ids=parsed.channel_ids,
        allowed_fields=parsed.allowed_fields,
        debounce_seconds=DEFAULT_DEBOUNCE_SECONDS,
        replacement_cooldown_seconds=DEFAULT_REPLACEMENT_COOLDOWN_SECONDS,
        same_speaker_cooldown_seconds=DEFAULT_SAME_SPEAKER_COOLDOWN_SECONDS,
    )


def _sync_dependencies() -> None:
    """Keep test hooks and the shared lifecycle core on the same dependencies."""

    dependencies = {
        "get_guild_config": get_guild_config,
        "upsert_guild_config": upsert_guild_config,
        "delete_live_card_state": delete_live_card_state,
        "get_live_card_state": get_live_card_state,
        "list_live_card_states": list_live_card_states,
        "list_live_card_states_for_channel": list_live_card_states_for_channel,
        "list_live_card_states_for_user": list_live_card_states_for_user,
        "upsert_live_card_state": upsert_live_card_state,
        "parse_live_card_footer": parse_live_card_footer,
        "monotonic": monotonic,
    }
    for module in (_legacy, _legacy._core):
        for name, value in dependencies.items():
            setattr(module, name, value)


async def render_live_profile_card(
    member: discord.Member,
    server_allowed_fields: set[str],
    *,
    trigger_message_id: int,
    require_live_enabled: bool = True,
) -> Optional[LiveCardRender]:
    """Render the compact image without a second public username text block."""

    rendered = await _legacy.render_live_profile_card(
        member,
        server_allowed_fields,
        trigger_message_id=trigger_message_id,
        require_live_enabled=require_live_enabled,
    )
    if rendered is None:
        return None

    # The image already contains public platform chips. Repeating usernames in
    # embed text made the signature much taller and visually looked like another
    # message. Keep official links as compact buttons instead.
    rendered.embed.description = None
    try:
        settings = await get_effective_profile_settings(member.guild.id, member.id)
        preferences = dict(settings.get("preferences") or {})
        show_platforms = bool(preferences.get("show_platforms", True)) and "platforms" in server_allowed_fields
        platforms = visible_platform_entries(settings.get("platforms"), allowed=show_platforms)
        view = _platform_view(platforms)
    except Exception:
        view = None
    return LiveCardRender(embed=rendered.embed, view=view, file=rendered.file)


def _live_card_send_payload(rendered: LiveCardRender) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "embed": rendered.embed,
        "allowed_mentions": discord.AllowedMentions.none(),
    }
    if rendered.view is not None:
        payload["view"] = rendered.view
    if rendered.file is not None:
        payload["file"] = rendered.file
    return payload


def _state_sort_key(state: Mapping[str, Any]) -> tuple[str, int]:
    updated = str(state.get("updated_at") or "")
    try:
        message_id = int(str(state.get("message_id") or "0"))
    except Exception:
        message_id = 0
    return updated, message_id


class LiveProfileCardRuntime(_legacy.LiveProfileCardRuntime):
    def __init__(
        self,
        bot: Any,
        *,
        renderer: RenderProfile = render_live_profile_card,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        _sync_dependencies()
        super().__init__(bot, renderer=renderer, sleep=sleep)
        # The inherited implementation is channel-scoped, but these containers
        # are reset explicitly so no per-member state from a hot reload survives.
        self._leading: dict[_ChannelKey, asyncio.Task[Any]] = {}
        self._pending: dict[_ChannelKey, asyncio.Task[Any]] = {}
        self._latest: dict[_ChannelKey, PendingTrigger] = {}
        self._locks: dict[_ChannelKey, asyncio.Lock] = {}
        self._latest_messages: dict[_ChannelKey, discord.Message] = {}
        self._latest_configs: dict[_ChannelKey, LiveCardConfig] = {}
        self._current_cards: dict[_ChannelKey, _CurrentCard] = {}
        self._trigger_received_at: dict[_TriggerTimeKey, float] = {}
        self._recovered_channels: set[_ChannelKey] = set()

    async def on_ready(self) -> None:
        _sync_dependencies()
        self._last_reconcile_at = monotonic()
        print(
            "🪪 live_profile_card ready mode=one_per_channel "
            "scheduler=quiet_window stale_render_guard=enabled"
        )

    async def on_message(self, message: discord.Message) -> None:
        _sync_dependencies()
        if not _is_supported_message(message):
            return

        try:
            config = parse_live_card_config(await get_guild_config(message.guild.id))
        except Exception as exc:
            print(
                "⚠️ live_profile_card skipped "
                f"guild={message.guild.id} channel={message.channel.id} user={message.author.id} "
                f"reason=config_read_failed error={type(exc).__name__}: {exc}"
            )
            return
        if not config.enabled or int(message.channel.id) not in config.channel_ids:
            return
        if not _channel_can_host_cards(message.channel):
            print(
                "⚠️ live_profile_card skipped "
                f"guild={message.guild.id} channel={message.channel.id} user={message.author.id} "
                "reason=channel_permissions_incomplete"
            )
            return

        key = (int(message.guild.id), int(message.channel.id))
        trigger = PendingTrigger(
            guild_id=key[0],
            channel_id=key[1],
            user_id=int(message.author.id),
            message_id=int(message.id),
            delay_seconds=config.debounce_seconds,
        )
        self._latest[key] = trigger
        self._latest_messages[key] = message
        self._latest_configs[key] = config
        self._trigger_received_at[(key[0], key[1], trigger.message_id)] = monotonic()
        self._prune_trigger_times()

        task = self._pending.get(key)
        if task is not None and not task.done():
            return
        task = asyncio.create_task(self._run_channel_worker(key))
        self._pending[key] = task
        task.add_done_callback(
            lambda finished, resolved_key=key: self._channel_worker_done(resolved_key, finished)
        )

    def _prune_trigger_times(self) -> None:
        if len(self._trigger_received_at) <= 2048:
            return
        oldest = sorted(self._trigger_received_at.items(), key=lambda item: item[1])[:512]
        for trigger_key, _created_at in oldest:
            self._trigger_received_at.pop(trigger_key, None)

    def _channel_worker_done(self, key: _ChannelKey, task: asyncio.Task[Any]) -> None:
        if self._pending.get(key) is task:
            self._pending.pop(key, None)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            print(
                "⚠️ live_profile_card channel worker failed "
                f"guild={key[0]} channel={key[1]} error={type(exc).__name__}: {exc}"
            )

    async def _run_channel_worker(self, key: _ChannelKey) -> None:
        while True:
            trigger = self._latest.get(key)
            config = self._latest_configs.get(key)
            if trigger is None or config is None:
                return

            received_at = self._trigger_received_at.get(
                (key[0], key[1], trigger.message_id),
                monotonic(),
            )
            remaining = max(0.0, config.debounce_seconds - (monotonic() - received_at))
            if remaining:
                await self.sleep(remaining)
            if self._latest.get(key) != trigger:
                continue

            lock = self._locks.setdefault(key, asyncio.Lock())
            async with lock:
                if self._latest.get(key) != trigger:
                    continue
                message = self._latest_messages.get(key)
                config = self._latest_configs.get(key)
                if message is None or config is None:
                    return
                await self._replace_card(
                    message,
                    config,
                    trigger,
                    force_reposition=True,
                    source="settled",
                )

            if self._latest.get(key) == trigger:
                self._release_trigger_context(key, trigger)
                return

    def _release_trigger_context(self, key: _ChannelKey, trigger: PendingTrigger) -> None:
        if self._latest.get(key) != trigger:
            return
        self._latest.pop(key, None)
        self._latest_messages.pop(key, None)
        self._latest_configs.pop(key, None)
        self._trigger_received_at.pop((key[0], key[1], trigger.message_id), None)

    async def _verified_owned_messages(
        self,
        channel: discord.TextChannel,
        states: list[Mapping[str, Any]],
        *,
        include_history: bool,
    ) -> dict[int, discord.Message]:
        bot_user = getattr(self.bot, "user", None)
        if bot_user is None:
            raise _CurrentCardVerificationUnavailable

        owned: dict[int, discord.Message] = {}
        for state in states:
            try:
                message_id = int(str(state.get("message_id") or "0"))
            except Exception:
                message_id = 0
            if message_id <= 0:
                continue
            try:
                stored = await channel.fetch_message(message_id)
            except discord.NotFound:
                continue
            except Exception as exc:
                raise _CurrentCardVerificationUnavailable from exc
            if (
                int(getattr(stored.author, "id", 0) or 0) == int(bot_user.id)
                and parse_live_card_footer(stored) is not None
            ):
                owned[int(stored.id)] = stored

        if include_history:
            try:
                async for candidate in channel.history(limit=LIVE_CARD_HISTORY_SCAN_LIMIT):
                    if int(getattr(candidate.author, "id", 0) or 0) != int(bot_user.id):
                        continue
                    if parse_live_card_footer(candidate) is not None:
                        owned[int(candidate.id)] = candidate
            except Exception as exc:
                # First recovery must know whether orphaned cards exist. Failing
                # closed is safer than adding another public signature blindly.
                raise _CurrentCardVerificationUnavailable from exc
        return owned

    async def _load_current_card(self, channel: discord.TextChannel) -> Optional[_CurrentCard]:
        key = (int(channel.guild.id), int(channel.id))
        cached = self._current_cards.get(key)
        if cached is not None:
            return cached

        try:
            raw_states = await list_live_card_states_for_channel(*key)
        except Exception as exc:
            raise _CurrentCardVerificationUnavailable from exc
        states = [dict(item) for item in raw_states if isinstance(item, Mapping)]
        include_history = key not in self._recovered_channels
        owned = await self._verified_owned_messages(
            channel,
            states,
            include_history=include_history,
        )

        newest = max(owned.values(), key=lambda item: int(item.id), default=None)
        for old in sorted(owned.values(), key=lambda item: int(item.id)):
            if newest is not None and int(old.id) == int(newest.id):
                continue
            if not await self._delete_verified_card(old):
                raise _CurrentCardVerificationUnavailable

        # Collapse every legacy per-member row to at most one channel row before
        # any new signature can be posted.
        try:
            await delete_live_card_state(*key)
        except Exception as exc:
            raise _CurrentCardVerificationUnavailable from exc

        current: Optional[_CurrentCard] = None
        if newest is not None:
            parsed = parse_live_card_footer(newest)
            if parsed is None:
                raise _CurrentCardVerificationUnavailable
            current = _CurrentCard(
                message_id=int(newest.id),
                user_id=int(parsed[0]),
                trigger_message_id=int(parsed[1]),
                message=newest,
            )
            try:
                await upsert_live_card_state(
                    key[0],
                    key[1],
                    message_id=current.message_id,
                    user_id=current.user_id,
                    trigger_message_id=current.trigger_message_id,
                )
            except Exception as exc:
                # Never keep an untracked public card after recovery.
                await self._delete_verified_card(newest)
                raise _CurrentCardVerificationUnavailable from exc
            self._current_cards[key] = current

        self._recovered_channels.add(key)
        if len(owned) > 1 or len(states) > 1:
            print(
                "🧹 live_profile_card collapsed legacy stack "
                f"guild={key[0]} channel={key[1]} visible_found={len(owned)} states={len(states)}"
            )
        return current

    def _is_latest(self, key: _ChannelKey, trigger: PendingTrigger) -> bool:
        return self._latest.get(key) == trigger

    async def _replace_card(
        self,
        message: discord.Message,
        config: LiveCardConfig,
        trigger: PendingTrigger,
        *,
        force_reposition: bool = False,
        source: str = "direct",
    ) -> None:
        del force_reposition
        _sync_dependencies()
        channel = message.channel
        guild = message.guild
        message_author = getattr(message, "author", None)
        member = (
            message_author
            if isinstance(message_author, discord.Member)
            and int(message_author.id) == int(trigger.user_id)
            else guild.get_member(trigger.user_id) if guild else None
        )
        if not isinstance(channel, discord.TextChannel) or not isinstance(member, discord.Member):
            return
        if not _channel_can_host_cards(channel):
            return

        key = (trigger.guild_id, trigger.channel_id)
        render_started = monotonic()
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

        # A newer human message arrived while the image was rendering.
        if not self._is_latest(key, trigger):
            print(
                "ℹ️ live_profile_card discarded stale render "
                f"guild={key[0]} channel={key[1]} trigger={trigger.message_id} stage=after_render"
            )
            return

        try:
            current = await self._load_current_card(channel)
        except (ProfileStorageUnavailable, _CurrentCardVerificationUnavailable) as exc:
            print(
                "⚠️ live_profile_card skipped "
                f"guild={key[0]} channel={key[1]} user={trigger.user_id} "
                f"reason=ownership_cleanup_unavailable error={type(exc).__name__}"
            )
            return
        if current is not None and current.trigger_message_id == trigger.message_id:
            return
        if not self._is_latest(key, trigger):
            return

        # Delete-before-send is deliberate. A transient missing signature is
        # acceptable; two stacked or misattributed public cards are not.
        if current is not None:
            removed = (
                await self._delete_verified_card(current.message)
                if current.message is not None
                else await self._delete_stored_message(channel, current.message_id)
            )
            if not removed:
                print(
                    "⚠️ live_profile_card replacement blocked "
                    f"guild={key[0]} channel={key[1]} reason=old_card_delete_failed"
                )
                return
            self._current_cards.pop(key, None)
        try:
            await delete_live_card_state(*key)
        except Exception as exc:
            print(
                "⚠️ live_profile_card replacement blocked "
                f"guild={key[0]} channel={key[1]} reason=state_cleanup_failed "
                f"error={type(exc).__name__}: {exc}"
            )
            return

        if not self._is_latest(key, trigger):
            return
        try:
            new_message = await channel.send(**_live_card_send_payload(rendered))
        except Exception as exc:
            print(
                "⚠️ live_profile_card send failed "
                f"guild={key[0]} channel={key[1]} user={trigger.user_id} "
                f"error={type(exc).__name__}: {exc}"
            )
            return

        # A message may arrive during Discord's send request. Remove the stale
        # result immediately and let the existing channel worker render latest.
        if not self._is_latest(key, trigger):
            await self._delete_verified_card(new_message)
            print(
                "ℹ️ live_profile_card removed stale post "
                f"guild={key[0]} channel={key[1]} trigger={trigger.message_id} stage=after_send"
            )
            return

        try:
            await upsert_live_card_state(
                key[0],
                key[1],
                message_id=int(new_message.id),
                user_id=int(trigger.user_id),
                trigger_message_id=int(trigger.message_id),
            )
        except Exception as exc:
            await self._delete_verified_card(new_message)
            print(
                "⚠️ live_profile_card state write failed; removed new card "
                f"guild={key[0]} channel={key[1]} user={trigger.user_id} "
                f"error={type(exc).__name__}: {exc}"
            )
            return

        self._current_cards[key] = _CurrentCard(
            message_id=int(new_message.id),
            user_id=int(trigger.user_id),
            trigger_message_id=int(trigger.message_id),
            message=new_message,
        )
        self._last_posted[key] = (int(trigger.user_id), monotonic())
        render_ms = int((monotonic() - render_started) * 1000)
        received_at = self._trigger_received_at.pop(
            (key[0], key[1], trigger.message_id),
            render_started,
        )
        total_ms = int((monotonic() - received_at) * 1000)
        print(
            "✅ live_profile_card posted one_per_channel "
            f"guild={key[0]} channel={key[1]} user={trigger.user_id} "
            f"message={new_message.id} trigger={trigger.message_id} source={source} "
            f"render_ms={render_ms} total_ms={total_ms}"
        )

    async def reconcile(self) -> None:
        _sync_dependencies()
        guild_count = len(list(getattr(self.bot, "guilds", []) or []))
        if guild_count <= 5:
            await super().reconcile()
            return
        print(
            "🪪 live_profile_card reconcile mode=lazy "
            f"reason=bounded_public_runtime guilds={guild_count}"
        )

    async def reconcile_deep(self) -> None:
        _sync_dependencies()
        await super().reconcile()

    async def reconcile_guild(self, guild: discord.Guild) -> None:
        _sync_dependencies()
        try:
            config = parse_live_card_config(await get_guild_config(guild.id))
        except Exception:
            return
        for channel_id in config.channel_ids:
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel) or not _channel_can_host_cards(channel):
                continue
            key = (int(guild.id), int(channel.id))
            self._current_cards.pop(key, None)
            self._recovered_channels.discard(key)
            try:
                await self._load_current_card(channel)
            except _CurrentCardVerificationUnavailable:
                continue

    async def _reconcile_channel(
        self,
        channel: discord.TextChannel,
        states: Optional[Any],
    ) -> None:
        del states
        key = (int(channel.guild.id), int(channel.id))
        self._current_cards.pop(key, None)
        self._recovered_channels.discard(key)
        try:
            await self._load_current_card(channel)
        except _CurrentCardVerificationUnavailable:
            return

    def _forget_channel(self, key: _ChannelKey, *, cancel_tasks: bool = True) -> None:
        if cancel_tasks:
            task = self._pending.pop(key, None)
            if task is not None and not task.done():
                task.cancel()
        self._leading.pop(key, None)
        self._latest.pop(key, None)
        self._latest_messages.pop(key, None)
        self._latest_configs.pop(key, None)
        self._last_posted.pop(key, None)
        self._locks.pop(key, None)
        self._current_cards.pop(key, None)
        self._recovered_channels.discard(key)
        for trigger_key in list(self._trigger_received_at):
            if trigger_key[:2] == key:
                self._trigger_received_at.pop(trigger_key, None)

    async def _remove_channel_card_state(
        self,
        guild: discord.Guild,
        channel_id: int,
        *,
        cancel_pending: bool = True,
    ) -> bool:
        key = (int(guild.id), int(channel_id))
        self._forget_channel(key, cancel_tasks=cancel_pending)
        _sync_dependencies()
        return await _legacy._core.LiveProfileCardRuntime._remove_channel_card_state(
            self,
            guild,
            channel_id,
            cancel_pending=cancel_pending,
        )

    async def remove_user_cards(self, guild: discord.Guild, user_id: int) -> None:
        resolved = int(user_id)
        for key, trigger in list(self._latest.items()):
            if key[0] == int(guild.id) and int(trigger.user_id) == resolved:
                self._forget_channel(key)
        _sync_dependencies()
        await _legacy._core.LiveProfileCardRuntime.remove_user_cards(self, guild, resolved)
        for key, current in list(self._current_cards.items()):
            if key[0] == int(guild.id) and int(current.user_id) == resolved:
                self._forget_channel(key, cancel_tasks=False)

    async def remove_user_cards_all_guilds(self, user_id: int) -> None:
        resolved = int(user_id)
        for key, trigger in list(self._latest.items()):
            if int(trigger.user_id) == resolved:
                self._forget_channel(key)
        _sync_dependencies()
        await _legacy._core.LiveProfileCardRuntime.remove_user_cards_all_guilds(self, resolved)
        for key, current in list(self._current_cards.items()):
            if int(current.user_id) == resolved:
                self._forget_channel(key, cancel_tasks=False)

    async def invalidate_guild_cards(self, guild: discord.Guild) -> None:
        _sync_dependencies()
        await _legacy._core.LiveProfileCardRuntime.invalidate_guild_cards(self, guild)
        for key in list(self._current_cards):
            if key[0] == int(guild.id):
                self._forget_channel(key)

    async def disable_channel(self, guild: discord.Guild, channel: discord.TextChannel) -> None:
        await self._remove_channel_card_state(guild, channel.id)

    async def on_member_remove(self, member: discord.Member) -> None:
        await self.remove_user_cards(member.guild, member.id)

    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        _sync_dependencies()
        await _legacy._core.LiveProfileCardRuntime.on_guild_channel_delete(self, channel)
        if isinstance(channel, discord.TextChannel):
            self._forget_channel((int(channel.guild.id), int(channel.id)))


__all__ = [
    "DEFAULT_DEBOUNCE_SECONDS",
    "DEFAULT_REPLACEMENT_COOLDOWN_SECONDS",
    "DEFAULT_SAME_SPEAKER_COOLDOWN_SECONDS",
    "LIVE_ALLOWED_FIELDS_KEY",
    "LIVE_CARD_FOOTER_PREFIX",
    "LIVE_CHANNEL_IDS_KEY",
    "LIVE_DEBOUNCE_KEY",
    "LIVE_ENABLED_KEY",
    "LIVE_REPLACEMENT_COOLDOWN_KEY",
    "LIVE_SAME_SPEAKER_COOLDOWN_KEY",
    "READY_RECONCILE_THROTTLE_SECONDS",
    "LiveCardConfig",
    "LiveCardRender",
    "LiveProfileCardRuntime",
    "PendingTrigger",
    "_channel_can_host_cards",
    "_channel_ids",
    "_copy_base_profile_embed",
    "_is_supported_message",
    "_platform_view",
    "delete_live_card_state",
    "get_live_card_state",
    "list_live_card_states",
    "list_live_card_states_for_channel",
    "list_live_card_states_for_user",
    "live_card_footer",
    "live_card_marker_url",
    "parse_live_card_config",
    "parse_live_card_footer",
    "render_live_profile_card",
    "upsert_live_card_state",
]
