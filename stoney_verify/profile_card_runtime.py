from __future__ import annotations

"""Fast, burst-aware compact live profile signatures.

The durable ownership and cleanup primitives remain in
``profile_card_runtime_core``. This module owns the public runtime hot path,
compact image rendering, responsive burst coalescing, and warm in-memory state.
"""

import asyncio
import hashlib
import re
from dataclasses import dataclass
from io import BytesIO
from time import monotonic
from typing import Any, Awaitable, Callable, Mapping, Optional

import discord

from . import profile_card_runtime_core as _core
from .profile_card_service import (
    PLATFORM_SPECS,
    ProfileStorageUnavailable,
    display_profile_username,
    get_effective_profile_settings,
    list_live_card_states_for_channel,
    visible_platform_entries,
)
from .profile_signature_live_renderer import render_member_profile_signature
from .profile_signature_style import effective_profile_style

# Responsive runtime timings. The old 4s / 30s / 180s defaults made a forum
# signature feel broken. A quiet channel posts immediately; rapid traffic gets
# one trailing replacement after the burst settles.
DEFAULT_DEBOUNCE_SECONDS = 0.0
DEFAULT_REPLACEMENT_COOLDOWN_SECONDS = 0.65
DEFAULT_SAME_SPEAKER_COOLDOWN_SECONDS = 1.5
LIVE_ALLOWED_FIELDS_KEY = _core.LIVE_ALLOWED_FIELDS_KEY
LIVE_CARD_FOOTER_PREFIX = _core.LIVE_CARD_FOOTER_PREFIX
LIVE_CHANNEL_IDS_KEY = _core.LIVE_CHANNEL_IDS_KEY
LIVE_DEBOUNCE_KEY = _core.LIVE_DEBOUNCE_KEY
LIVE_ENABLED_KEY = _core.LIVE_ENABLED_KEY
LIVE_REPLACEMENT_COOLDOWN_KEY = _core.LIVE_REPLACEMENT_COOLDOWN_KEY
LIVE_SAME_SPEAKER_COOLDOWN_KEY = _core.LIVE_SAME_SPEAKER_COOLDOWN_KEY
READY_RECONCILE_THROTTLE_SECONDS = _core.READY_RECONCILE_THROTTLE_SECONDS
LiveCardConfig = _core.LiveCardConfig
PendingTrigger = _core.PendingTrigger
live_card_footer = _core.live_card_footer
_legacy_parse_live_card_footer = _core.parse_live_card_footer

_LIVE_CARD_MARKER_PREFIX = "https://dankshield.app/live-profile/"
_LIVE_CARD_MARKER_RE = re.compile(r"^https://dankshield\.app/live-profile/(\d+)/(\d+)$")
_LIVE_CARD_ATTACHMENT_RE = re.compile(r"^dank-live-profile-(\d+)-(\d+)\.png$")

# Existing private helper imports remain available for callers and tests.
_channel_ids = _core._channel_ids
_channel_can_host_cards = _core._channel_can_host_cards
_copy_base_profile_embed = _core._copy_base_profile_embed
_is_supported_message = _core._is_supported_message
_platform_view = _core._platform_view

# Dependency hooks stay at this public module boundary. Existing tests and
# callers may replace these without knowing about the internal lifecycle split.
get_guild_config = _core.get_guild_config
upsert_guild_config = _core.upsert_guild_config
delete_live_card_state = _core.delete_live_card_state
get_live_card_state = _core.get_live_card_state
list_live_card_states = _core.list_live_card_states
list_live_card_states_for_channel = _core.list_live_card_states_for_channel
list_live_card_states_for_user = _core.list_live_card_states_for_user
upsert_live_card_state = _core.upsert_live_card_state

# PNG bytes are the largest in-process objects in this feature. Keep the cache
# small enough for constrained public hosting while still covering hot speakers.
_SIGNATURE_CACHE_TTL_SECONDS = 300.0
_SIGNATURE_CACHE_MAX_ITEMS = 512
_SIGNATURE_CACHE: dict[tuple[Any, ...], tuple[float, bytes]] = {}


@dataclass(frozen=True)
class LiveCardRender:
    embed: discord.Embed
    view: Optional[discord.ui.View]
    file: Optional[discord.File] = None


@dataclass
class _CurrentCard:
    message_id: int
    user_id: int
    trigger_message_id: int
    message: Optional[discord.Message] = None


_ChannelKey = tuple[int, int]
_MemberCardKey = tuple[int, int, int]
_TriggerTimeKey = tuple[int, int, int, int]


class _CurrentCardVerificationUnavailable(RuntimeError):
    """Raised when ownership cannot be verified without risking duplicates."""


RenderProfile = Callable[..., Awaitable[Optional[LiveCardRender]]]
Sleep = Callable[[float], Awaitable[None]]


def live_card_marker_url(user_id: int, trigger_message_id: int) -> str:
    """Return an invisible embed URL used as durable ownership metadata."""

    return f"{_LIVE_CARD_MARKER_PREFIX}{int(user_id)}/{int(trigger_message_id)}"


def parse_live_card_footer(message: Any) -> Optional[tuple[int, int]]:
    """Parse legacy footers plus invisible URL/attachment ownership markers."""

    legacy = _legacy_parse_live_card_footer(message)
    if legacy is not None:
        return legacy
    try:
        for embed in list(getattr(message, "embeds", []) or []):
            marker = str(getattr(embed, "url", "") or "").strip()
            match = _LIVE_CARD_MARKER_RE.fullmatch(marker)
            if match:
                return int(match.group(1)), int(match.group(2))
        for attachment in list(getattr(message, "attachments", []) or []):
            filename = str(getattr(attachment, "filename", "") or "").strip()
            match = _LIVE_CARD_ATTACHMENT_RE.fullmatch(filename)
            if match:
                return int(match.group(1)), int(match.group(2))
    except Exception:
        return None
    return None


def parse_live_card_config(config: Mapping[str, Any]) -> LiveCardConfig:
    """Resolve server scope while migrating legacy anti-spam delays.

    The timing keys were never exposed as a supported manager control. Existing
    rows may still contain the old 4/30/180 values, so the public runtime uses
    one responsive policy instead of inheriting those stale delays forever.
    """

    parsed = _core.parse_live_card_config(config)
    return LiveCardConfig(
        enabled=parsed.enabled,
        channel_ids=parsed.channel_ids,
        allowed_fields=parsed.allowed_fields,
        debounce_seconds=DEFAULT_DEBOUNCE_SECONDS,
        replacement_cooldown_seconds=DEFAULT_REPLACEMENT_COOLDOWN_SECONDS,
        same_speaker_cooldown_seconds=DEFAULT_SAME_SPEAKER_COOLDOWN_SECONDS,
    )


def _sync_core_dependencies() -> None:
    for name in (
        "get_guild_config",
        "upsert_guild_config",
        "delete_live_card_state",
        "get_live_card_state",
        "list_live_card_states",
        "list_live_card_states_for_channel",
        "list_live_card_states_for_user",
        "upsert_live_card_state",
        "parse_live_card_footer",
        "monotonic",
    ):
        setattr(_core, name, globals()[name])


def _compact_role_labels(member: discord.Member) -> list[str]:
    from .commands_ext.public_self_roles_group import (
        DEFAULT_IDENTITY_ROLE_NAMES,
        DEFAULT_INTEREST_ROLE_NAMES,
        DEFAULT_PRONOUN_ROLE_NAMES,
        _member_profile_roles,
        _short_role_label,
    )

    labels: list[str] = []
    pronouns = [
        _short_role_label(role.name)
        for role in _member_profile_roles(member, DEFAULT_PRONOUN_ROLE_NAMES)
    ]
    identity = [
        _short_role_label(role.name)
        for role in _member_profile_roles(member, DEFAULT_IDENTITY_ROLE_NAMES)
    ]
    interests = [
        _short_role_label(role.name)
        for role in _member_profile_roles(member, DEFAULT_INTEREST_ROLE_NAMES)
    ]

    if pronouns:
        labels.append("Pronouns: " + ", ".join(pronouns[:2]))
    if identity:
        labels.append("Identity: " + ", ".join(identity[:2]))
    if interests:
        shown = interests[:3]
        suffix = " + more" if len(interests) > len(shown) else ""
        labels.append("Interests: " + " • ".join(shown) + suffix)
    return labels


def _compact_date_labels(member: discord.Member) -> list[str]:
    labels: list[str] = []
    joined_at = getattr(member, "joined_at", None)
    created_at = getattr(member, "created_at", None)
    try:
        if joined_at is not None:
            labels.append(f"Joined {joined_at.strftime('%b %Y')}")
    except Exception:
        pass
    try:
        if created_at is not None:
            labels.append(f"Discord since {created_at.strftime('%b %Y')}")
    except Exception:
        pass
    return labels


def _compact_platform_labels(entries: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    for entry in entries[:4]:
        spec = PLATFORM_SPECS.get(str(entry.get("platform") or ""))
        if spec is None:
            continue
        try:
            username = display_profile_username(entry.get("username"))
        except Exception:
            continue
        labels.append(f"{spec.label}: {username}")
    return labels


def _platform_link_line(entries: list[dict[str, Any]]) -> str:
    """Build a neat clickable account row inside the same Discord embed."""

    parts: list[str] = []
    for entry in entries[:5]:
        spec = PLATFORM_SPECS.get(str(entry.get("platform") or ""))
        if spec is None:
            continue
        try:
            username = display_profile_username(entry.get("username"))
        except Exception:
            continue
        url = str(entry.get("url") or "").strip()
        if url:
            parts.append(f"[{spec.emoji} {spec.label}]({url}) `{username}`")
        elif spec.supports_url:
            parts.append(f"⚠️ **{spec.label}** `{username}` *(add official link)*")
        else:
            parts.append(f"{spec.emoji} **{spec.label}** `{username}`")
    if not parts:
        return ""
    return "**Connected profiles**  •  " + "  •  ".join(parts)


def _stable_cache_value(value: Any) -> Any:
    if isinstance(value, bytes):
        return ("bytes", hashlib.sha256(value).hexdigest())
    if isinstance(value, Mapping):
        return tuple(
            (str(key), _stable_cache_value(item))
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_stable_cache_value(item) for item in value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return str(value)


def _signature_cache_get(key: tuple[Any, ...]) -> Optional[bytes]:
    found = _SIGNATURE_CACHE.get(key)
    if found is None:
        return None
    created_at, payload = found
    if monotonic() - created_at > _SIGNATURE_CACHE_TTL_SECONDS:
        _SIGNATURE_CACHE.pop(key, None)
        return None
    return bytes(payload)


def _signature_cache_put(key: tuple[Any, ...], payload: bytes) -> None:
    if len(_SIGNATURE_CACHE) >= _SIGNATURE_CACHE_MAX_ITEMS:
        expired_before = monotonic() - _SIGNATURE_CACHE_TTL_SECONDS
        for stale_key, (created_at, _payload) in list(_SIGNATURE_CACHE.items()):
            if created_at < expired_before:
                _SIGNATURE_CACHE.pop(stale_key, None)
        if len(_SIGNATURE_CACHE) >= _SIGNATURE_CACHE_MAX_ITEMS:
            oldest = sorted(_SIGNATURE_CACHE.items(), key=lambda item: item[1][0])[:128]
            for stale_key, _value in oldest:
                _SIGNATURE_CACHE.pop(stale_key, None)
    _SIGNATURE_CACHE[key] = (monotonic(), bytes(payload))


async def render_live_profile_card(
    member: discord.Member,
    server_allowed_fields: set[str],
    *,
    trigger_message_id: int,
    require_live_enabled: bool = True,
) -> Optional[LiveCardRender]:
    """Render one legible horizontal signature with member-first privacy."""

    settings = await get_effective_profile_settings(member.guild.id, member.id)
    preferences = dict(settings.get("preferences") or {})
    if require_live_enabled and not bool(preferences.get("live_cards_enabled", True)):
        return None

    show_roles = bool(preferences.get("show_roles", True)) and "roles" in server_allowed_fields
    show_dates = bool(preferences.get("show_account_dates", True)) and "account_dates" in server_allowed_fields
    show_platforms = bool(preferences.get("show_platforms", True)) and "platforms" in server_allowed_fields
    platforms = visible_platform_entries(settings.get("platforms"), allowed=show_platforms)
    role_labels = _compact_role_labels(member) if show_roles else []
    date_labels = _compact_date_labels(member) if show_dates else []
    platform_labels = _compact_platform_labels(platforms)

    try:
        cfg = await get_guild_config(member.guild.id)
    except Exception:
        cfg = {}
    style = effective_profile_style(preferences, cfg)
    avatar = getattr(member, "display_avatar", None)
    avatar_identity = str(getattr(avatar, "key", None) or getattr(avatar, "url", "") or "")
    cache_key = (
        int(member.guild.id),
        int(member.id),
        str(getattr(member.guild, "name", "") or ""),
        str(getattr(member, "display_name", None) or member),
        avatar_identity,
        tuple(sorted(str(value) for value in server_allowed_fields)),
        tuple(role_labels),
        tuple(date_labels),
        tuple(platform_labels),
        _stable_cache_value(style),
    )
    image_bytes = _signature_cache_get(cache_key)
    if image_bytes is None:
        image_bytes = await render_member_profile_signature(
            member,
            style=style,
            role_labels=role_labels,
            date_labels=date_labels,
            platform_labels=platform_labels,
        )
        _signature_cache_put(cache_key, image_bytes)

    filename = f"dank-live-profile-{int(member.id)}-{int(trigger_message_id)}.png"
    file = discord.File(BytesIO(image_bytes), filename=filename)
    try:
        color = member.color if getattr(member.color, "value", 0) else discord.Color.blurple()
    except Exception:
        color = discord.Color.blurple()
    embed = discord.Embed(
        color=color,
        description=_platform_link_line(platforms) or None,
        url=live_card_marker_url(member.id, trigger_message_id),
    )
    embed.set_image(url=f"attachment://{filename}")
    # No visible technical footer. Ownership is stored in invisible embed and
    # attachment metadata; legacy footer-marked cards remain cleanup-compatible.
    return LiveCardRender(embed=embed, view=None, file=file)


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


class LiveProfileCardRuntime(_core.LiveProfileCardRuntime):
    def __init__(
        self,
        bot: Any,
        *,
        renderer: RenderProfile = render_live_profile_card,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        _sync_core_dependencies()
        super().__init__(bot, renderer=renderer, sleep=sleep)
        self._leading: dict[_MemberCardKey, asyncio.Task[Any]] = {}
        self._latest_messages: dict[_MemberCardKey, discord.Message] = {}
        self._latest_configs: dict[_MemberCardKey, LiveCardConfig] = {}
        self._last_activity: dict[_MemberCardKey, float] = {}
        self._current_cards: dict[_MemberCardKey, _CurrentCard] = {}
        self._trigger_received_at: dict[_TriggerTimeKey, float] = {}
        self._channel_send_locks: dict[_ChannelKey, asyncio.Lock] = {}

    async def on_ready(self) -> None:
        """Avoid an all-guild history scan during every process start/reconnect.

        Durable state is verified lazily on the first message in each active
        channel. This keeps startup bounded when the bot is sharded across many
        servers while preserving exact bot-owned cleanup on demand.
        """

        _sync_core_dependencies()
        self._last_reconcile_at = monotonic()
        print("🪪 live_profile_card ready lazy_recovery=enabled startup_history_scan=skipped")

    async def on_message(self, message: discord.Message) -> None:
        _sync_core_dependencies()
        if not _is_supported_message(message):
            return

        try:
            # The shared guild config cache is updated immediately by setup
            # writes. Never force a Supabase read for every Discord message.
            raw_config = await get_guild_config(message.guild.id)
            config = parse_live_card_config(raw_config)
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

        key = (int(message.guild.id), int(message.channel.id), int(message.author.id))
        now = monotonic()
        prior_activity = self._last_activity.get(key)
        idle = prior_activity is None or now - prior_activity >= config.same_speaker_cooldown_seconds
        self._last_activity[key] = now

        trigger = PendingTrigger(
            guild_id=key[0],
            channel_id=key[1],
            user_id=key[2],
            message_id=int(message.id),
            delay_seconds=0.0 if idle else config.replacement_cooldown_seconds,
        )
        self._latest[key] = trigger
        self._latest_messages[key] = message
        self._latest_configs[key] = config
        self._trigger_received_at[(key[0], key[1], key[2], trigger.message_id)] = now
        self._prune_trigger_times()

        if idle and not self._task_running(self._leading.get(key)):
            previous = self._pending.pop(key, None)
            if self._task_running(previous):
                previous.cancel()
            task = asyncio.create_task(self._run_immediate(key, message, config, trigger))
            self._leading[key] = task
            # Preserve the historical contract that _pending contains every
            # outstanding channel worker, including the immediate leading task.
            self._pending[key] = task
            task.add_done_callback(
                lambda finished, resolved_key=key: self._leading_done(resolved_key, finished)
            )
            return

        previous = self._pending.get(key)
        leading = self._leading.get(key)
        if previous is not leading and self._task_running(previous):
            previous.cancel()
        task = asyncio.create_task(self._run_trailing(key, trigger))
        self._pending[key] = task
        task.add_done_callback(
            lambda finished, resolved_key=key: self._task_done(self._pending, resolved_key, finished)
        )

    @staticmethod
    def _task_running(task: Optional[asyncio.Task[Any]]) -> bool:
        return task is not None and not task.done()

    @staticmethod
    def _consume_task_result(task: asyncio.Task[Any]) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            print(f"⚠️ live_profile_card worker failed: {type(exc).__name__}: {exc}")

    @classmethod
    def _task_done(
        cls,
        bucket: dict[_MemberCardKey, asyncio.Task[Any]],
        key: _MemberCardKey,
        task: asyncio.Task[Any],
    ) -> None:
        if bucket.get(key) is task:
            bucket.pop(key, None)
        cls._consume_task_result(task)

    def _leading_done(self, key: _MemberCardKey, task: asyncio.Task[Any]) -> None:
        if self._leading.get(key) is task:
            self._leading.pop(key, None)
        if self._pending.get(key) is task:
            self._pending.pop(key, None)
        self._consume_task_result(task)

    def _release_trigger_context(self, key: _MemberCardKey, trigger: PendingTrigger) -> None:
        """Release heavy incoming message/config references after the worker."""

        if self._latest.get(key) != trigger:
            return
        self._latest.pop(key, None)
        self._latest_messages.pop(key, None)
        self._latest_configs.pop(key, None)
        self._trigger_received_at.pop((key[0], key[1], key[2], trigger.message_id), None)

    def _prune_trigger_times(self) -> None:
        if len(self._trigger_received_at) <= 2048:
            return
        oldest = sorted(self._trigger_received_at.items(), key=lambda item: item[1])[:512]
        for trigger_key, _created_at in oldest:
            self._trigger_received_at.pop(trigger_key, None)

    async def _run_immediate(
        self,
        key: _MemberCardKey,
        fallback_message: discord.Message,
        fallback_config: LiveCardConfig,
        fallback_trigger: PendingTrigger,
    ) -> None:
        # Yield once so messages delivered in the same event-loop turn collapse
        # before any image or network work begins. This is not a user-visible
        # debounce and introduces no timer delay.
        await asyncio.sleep(0)
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            trigger = self._latest.get(key, fallback_trigger)
            message = self._latest_messages.get(key, fallback_message)
            config = self._latest_configs.get(key, fallback_config)
            try:
                await self._replace_card(
                    message,
                    config,
                    trigger,
                    force_reposition=True,
                    source="leading",
                )
            finally:
                self._release_trigger_context(key, trigger)

    async def _run_trailing(self, key: _MemberCardKey, trigger: PendingTrigger) -> None:
        config = self._latest_configs.get(key)
        quiet_seconds = (
            config.replacement_cooldown_seconds
            if config is not None
            else DEFAULT_REPLACEMENT_COOLDOWN_SECONDS
        )
        await self.sleep(max(0.0, quiet_seconds))

        # The quiet timer may overlap the leading render, but a trailing worker
        # must never overtake it. Shielding prevents cancellation of a new burst
        # target from canceling the already-started instant post.
        leading = self._leading.get(key)
        if leading is not None and leading is not asyncio.current_task() and self._task_running(leading):
            await asyncio.shield(leading)

        if self._latest.get(key) != trigger:
            return
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if self._latest.get(key) != trigger:
                return
            message = self._latest_messages.get(key)
            config = self._latest_configs.get(key)
            if message is None or config is None:
                self._release_trigger_context(key, trigger)
                return
            try:
                await self._replace_card(
                    message,
                    config,
                    trigger,
                    force_reposition=True,
                    source="trailing",
                )
            finally:
                self._release_trigger_context(key, trigger)

    async def reconcile(self) -> None:
        """Keep setup-time reconciliation bounded in public deployments.

        Small development installs retain the historical deep audit. Once the
        bot spans more than five guilds, normal ownership is recovered lazily
        per active channel and setup buttons never trigger an all-server scan.
        """

        _sync_core_dependencies()
        guild_count = len(list(getattr(self.bot, "guilds", []) or []))
        if guild_count <= 5:
            await super().reconcile()
            return
        print(
            "🪪 live_profile_card reconcile mode=lazy "
            f"reason=bounded_public_runtime guilds={guild_count}"
        )

    async def reconcile_deep(self) -> None:
        """Run the legacy all-guild ownership audit only when explicitly asked."""

        _sync_core_dependencies()
        await super().reconcile()

    async def reconcile_guild(self, guild: discord.Guild) -> None:
        """Warm only one guild after setup without scanning every public server."""

        _sync_core_dependencies()
        try:
            config = parse_live_card_config(await get_guild_config(guild.id))
        except Exception:
            return
        for channel_id in config.channel_ids:
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel) or not _channel_can_host_cards(channel):
                continue
            channel_key = (int(guild.id), int(channel.id))
            for key in list(self._current_cards):
                if key[:2] == channel_key:
                    self._current_cards.pop(key, None)
            try:
                states = await list_live_card_states_for_channel(*channel_key)
            except ProfileStorageUnavailable:
                continue
            for state in states:
                try:
                    user_id = int(str(state.get("user_id") or "0"))
                except Exception:
                    user_id = 0
                if user_id <= 0:
                    continue
                try:
                    await self._load_current_card(channel, user_id)
                except (ProfileStorageUnavailable, _CurrentCardVerificationUnavailable):
                    continue

    async def _reconcile_channel(
        self,
        channel: discord.TextChannel,
        states: Optional[Any],
    ) -> None:
        _sync_core_dependencies()
        await super()._reconcile_channel(channel, states)
        channel_key = (int(channel.guild.id), int(channel.id))
        for key in list(self._current_cards):
            if key[:2] == channel_key:
                self._current_cards.pop(key, None)

    async def _remove_channel_card_state(
        self,
        guild: discord.Guild,
        channel_id: int,
        *,
        cancel_pending: bool = True,
    ) -> bool:
        key = (int(guild.id), int(channel_id))
        self._forget_channel(key, cancel_tasks=cancel_pending)
        _sync_core_dependencies()
        return await super()._remove_channel_card_state(
            guild,
            channel_id,
            cancel_pending=cancel_pending,
        )

    def _forget_member_card(self, key: _MemberCardKey, *, cancel_tasks: bool = True) -> None:
        if cancel_tasks:
            for bucket in (self._leading, self._pending):
                task = bucket.pop(key, None)
                if self._task_running(task):
                    task.cancel()
        self._latest.pop(key, None)
        self._latest_messages.pop(key, None)
        self._latest_configs.pop(key, None)
        self._last_activity.pop(key, None)
        self._last_posted.pop(key, None)
        self._locks.pop(key, None)
        self._current_cards.pop(key, None)
        for trigger_key in list(self._trigger_received_at):
            if trigger_key[:3] == key:
                self._trigger_received_at.pop(trigger_key, None)

    def _forget_channel(self, key: _ChannelKey, *, cancel_tasks: bool = True) -> None:
        for member_key in {
            *[item for item in self._latest if item[:2] == key],
            *[item for item in self._pending if item[:2] == key],
            *[item for item in self._leading if item[:2] == key],
            *[item for item in self._current_cards if item[:2] == key],
            *[item for item in self._last_activity if item[:2] == key],
        }:
            self._forget_member_card(member_key, cancel_tasks=cancel_tasks)
        self._channel_send_locks.pop(key, None)
        for trigger_key in list(self._trigger_received_at):
            if trigger_key[:2] == key:
                self._trigger_received_at.pop(trigger_key, None)

    def _cancel_user_leading_tasks(self, user_id: int, *, guild_id: Optional[int] = None) -> None:
        resolved_user_id = int(user_id)
        resolved_guild_id = int(guild_id) if guild_id is not None else None
        for key, trigger in list(self._latest.items()):
            if trigger.user_id != resolved_user_id:
                continue
            if resolved_guild_id is not None and key[0] != resolved_guild_id:
                continue
            task = self._leading.pop(key, None)
            if self._task_running(task):
                task.cancel()
            if self._pending.get(key) is task:
                self._pending.pop(key, None)
            self._latest_messages.pop(key, None)
            self._latest_configs.pop(key, None)

    async def remove_user_cards(self, guild: discord.Guild, user_id: int) -> None:
        _sync_core_dependencies()
        self._cancel_user_leading_tasks(user_id, guild_id=guild.id)
        await super().remove_user_cards(guild, user_id)
        for key, current in list(self._current_cards.items()):
            if key[0] == int(guild.id) and current.user_id == int(user_id):
                self._forget_member_card(key)

    async def remove_user_cards_all_guilds(self, user_id: int) -> None:
        _sync_core_dependencies()
        self._cancel_user_leading_tasks(user_id)
        await super().remove_user_cards_all_guilds(user_id)
        for key, current in list(self._current_cards.items()):
            if current.user_id == int(user_id):
                self._forget_member_card(key)

    async def invalidate_guild_cards(self, guild: discord.Guild) -> None:
        _sync_core_dependencies()
        await super().invalidate_guild_cards(guild)
        channel_keys = {key[:2] for key in self._current_cards if key[0] == int(guild.id)}
        for channel_key in channel_keys:
            self._forget_channel(channel_key)

    async def disable_channel(self, guild: discord.Guild, channel: discord.TextChannel) -> None:
        _sync_core_dependencies()
        await self._remove_channel_card_state(guild, channel.id)

    async def on_member_remove(self, member: discord.Member) -> None:
        _sync_core_dependencies()
        await self.remove_user_cards(member.guild, member.id)

    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        _sync_core_dependencies()
        await super().on_guild_channel_delete(channel)
        if isinstance(channel, discord.TextChannel):
            self._forget_channel((int(channel.guild.id), int(channel.id)))

    async def _load_current_card(
        self,
        channel: discord.TextChannel,
        user_id: int,
    ) -> Optional[_CurrentCard]:
        key = (int(channel.guild.id), int(channel.id), int(user_id))
        cached = self._current_cards.get(key)
        if cached is not None:
            return cached
        state = await get_live_card_state(*key)
        if not isinstance(state, Mapping):
            return None
        try:
            message_id = int(str(state.get("message_id") or "0"))
            stored_user_id = int(str(state.get("user_id") or "0"))
            trigger_message_id = int(str(state.get("trigger_message_id") or "0"))
        except Exception:
            message_id = stored_user_id = trigger_message_id = 0
        if message_id <= 0 or stored_user_id <= 0 or stored_user_id != int(user_id):
            return None
        try:
            stored = await channel.fetch_message(message_id)
        except discord.NotFound:
            stored = None
        except Exception as exc:
            print(
                "⚠️ live_profile_card state verification failed "
                f"guild={channel.guild.id} channel={channel.id} message={message_id} "
                f"error={type(exc).__name__}: {exc}"
            )
            raise _CurrentCardVerificationUnavailable from exc
        bot_user = getattr(self.bot, "user", None)
        parsed = parse_live_card_footer(stored) if stored is not None else None
        if (
            stored is None
            or bot_user is None
            or int(getattr(stored.author, "id", 0) or 0) != int(bot_user.id)
            or parsed is None
            or int(parsed[0]) != stored_user_id
        ):
            try:
                await delete_live_card_state(*key)
            except Exception:
                pass
            return None
        current = _CurrentCard(
            message_id=message_id,
            user_id=stored_user_id,
            trigger_message_id=trigger_message_id or int(parsed[1]),
            message=stored,
        )
        self._current_cards[key] = current
        return current

    async def _replace_card(
        self,
        message: discord.Message,
        config: LiveCardConfig,
        trigger: PendingTrigger,
        *,
        force_reposition: bool = False,
        source: str = "direct",
    ) -> None:
        _sync_core_dependencies()
        channel = message.channel
        guild = message.guild
        message_author = getattr(message, "author", None)
        if isinstance(message_author, discord.Member) and int(message_author.id) == int(trigger.user_id):
            member = message_author
        else:
            member = guild.get_member(trigger.user_id) if guild else None
        if not isinstance(channel, discord.TextChannel):
            return
        if not isinstance(member, discord.Member):
            print(
                "⚠️ live_profile_card skipped member unavailable "
                f"guild={trigger.guild_id} channel={trigger.channel_id} user={trigger.user_id} "
                "source=message_author_then_cache"
            )
            return
        if not _channel_can_host_cards(channel):
            print(
                "⚠️ live_profile_card skipped channel permissions "
                f"guild={trigger.guild_id} channel={trigger.channel_id} user={trigger.user_id} "
                "required=view,send,embed,history,attach"
            )
            return

        key = (trigger.guild_id, trigger.channel_id, trigger.user_id)
        try:
            current = await self._load_current_card(channel, trigger.user_id)
        except (ProfileStorageUnavailable, _CurrentCardVerificationUnavailable) as exc:
            print(
                "⚠️ live_profile_card skipped "
                f"guild={trigger.guild_id} channel={trigger.channel_id} user={trigger.user_id} "
                f"reason=current_card_verification_unavailable error={type(exc).__name__}"
            )
            return
        if current is not None:
            if current.user_id == trigger.user_id and current.trigger_message_id == trigger.message_id:
                return
            if current.user_id == trigger.user_id and not force_reposition:
                return

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
            print(
                "ℹ️ live_profile_card skipped "
                f"guild={trigger.guild_id} channel={trigger.channel_id} user={trigger.user_id} "
                "reason=member_live_signature_disabled"
            )
            return
        render_ms = int((monotonic() - render_started) * 1000)

        channel_key = (trigger.guild_id, trigger.channel_id)
        send_lock = self._channel_send_locks.setdefault(channel_key, asyncio.Lock())
        async with send_lock:
            try:
                new_message = await channel.send(**_live_card_send_payload(rendered))
            except Exception as exc:
                print(
                    "⚠️ live_profile_card send failed "
                    f"guild={trigger.guild_id} channel={trigger.channel_id} user={trigger.user_id} "
                    f"error={type(exc).__name__}: {exc}"
                )
                return

            try:
                await upsert_live_card_state(
                    trigger.guild_id,
                    trigger.channel_id,
                    message_id=new_message.id,
                    user_id=trigger.user_id,
                    trigger_message_id=trigger.message_id,
                )
            except Exception as exc:
                await self._delete_verified_card(new_message)
                print(
                    "⚠️ live_profile_card state write failed; removed new card "
                    f"guild={trigger.guild_id} channel={trigger.channel_id} user={trigger.user_id} "
                    f"error={type(exc).__name__}: {exc}"
                )
                return

        old = current
        self._current_cards[key] = _CurrentCard(
            message_id=int(new_message.id),
            user_id=int(trigger.user_id),
            trigger_message_id=int(trigger.message_id),
            message=new_message,
        )
        self._last_posted[key] = (trigger.user_id, monotonic())
        received_at = self._trigger_received_at.pop(
            (trigger.guild_id, trigger.channel_id, trigger.user_id, trigger.message_id),
            render_started,
        )
        total_ms = int((monotonic() - received_at) * 1000)
        print(
            "✅ live_profile_card posted "
            f"guild={trigger.guild_id} channel={trigger.channel_id} user={trigger.user_id} "
            f"message={new_message.id} trigger={trigger.message_id} source={source} "
            f"render_ms={render_ms} total_ms={total_ms}"
        )

        if old is not None and old.message_id != int(new_message.id):
            if old.message is not None:
                removed = await self._delete_verified_card(old.message)
            else:
                removed = await self._delete_stored_message(channel, old.message_id)
            if not removed:
                print(
                    "⚠️ live_profile_card old card cleanup deferred "
                    f"guild={trigger.guild_id} channel={trigger.channel_id} message={old.message_id}"
                )

    async def _stored_state_is_live(
        self,
        channel: discord.TextChannel,
        state: Optional[Mapping[str, Any]],
    ) -> bool:
        if not isinstance(state, Mapping):
            return False
        try:
            message_id = int(str(state.get("message_id") or "0"))
        except Exception:
            return False
        if message_id <= 0:
            return False
        try:
            stored = await channel.fetch_message(message_id)
        except discord.NotFound:
            return False
        except Exception as exc:
            print(
                "⚠️ live_profile_card state verification failed "
                f"guild={channel.guild.id} channel={channel.id} message={message_id} "
                f"error={type(exc).__name__}: {exc}"
            )
            return True
        bot_user = getattr(self.bot, "user", None)
        if bot_user is None or int(getattr(stored.author, "id", 0) or 0) != int(bot_user.id):
            return False
        parsed = parse_live_card_footer(stored)
        if parsed is None:
            return False
        try:
            stored_user_id = int(str(state.get("user_id") or "0"))
        except Exception:
            return False
        return int(parsed[0]) == stored_user_id


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
    "live_card_footer",
    "live_card_marker_url",
    "parse_live_card_config",
    "parse_live_card_footer",
    "render_live_profile_card",
]
