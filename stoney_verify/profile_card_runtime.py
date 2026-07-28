from __future__ import annotations

"""Channel-scoped, burst-safe compact profile signatures.

Discord cannot attach a bot-rendered image to another member's message. The safe
public approximation is therefore one Dank Shield-owned signature per configured
channel, representing the latest eligible speaker after the current chat burst
settles.

The runtime fails closed whenever ownership or cleanup cannot be verified. A
temporarily missing signature is acceptable; stacked, stale, or misattributed
public cards are not.
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
    delete_live_card_state,
    display_profile_username,
    get_effective_profile_settings,
    get_live_card_state,
    list_live_card_states,
    list_live_card_states_for_channel,
    list_live_card_states_for_user,
    platform_entry_mode,
    upsert_live_card_state,
    visible_platform_entries,
)
from .profile_signature_live_renderer import render_member_profile_signature
from .profile_signature_style import effective_profile_style

# Wait for a short quiet window before posting. This prevents a completed render
# for speaker A from landing below a newer message from speaker B.
DEFAULT_DEBOUNCE_SECONDS = 0.85
DEFAULT_REPLACEMENT_COOLDOWN_SECONDS = DEFAULT_DEBOUNCE_SECONDS
DEFAULT_SAME_SPEAKER_COOLDOWN_SECONDS = 1.5
LIVE_CARD_HISTORY_SCAN_LIMIT = 100

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

_channel_ids = _core._channel_ids
_channel_can_host_cards = _core._channel_can_host_cards
_copy_base_profile_embed = _core._copy_base_profile_embed
_is_supported_message = _core._is_supported_message
_platform_view = _core._platform_view

# Dependency hooks remain patchable at this public module boundary for tests and
# callers that historically replaced them.
get_guild_config = _core.get_guild_config
upsert_guild_config = _core.upsert_guild_config

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


class _CurrentCardVerificationUnavailable(RuntimeError):
    """Ownership cannot be verified without risking another visible card."""


_ChannelKey = tuple[int, int]
_TriggerTimeKey = tuple[int, int, int]
RenderProfile = Callable[..., Awaitable[Optional[LiveCardRender]]]
Sleep = Callable[[float], Awaitable[None]]


def live_card_marker_url(user_id: int, trigger_message_id: int) -> str:
    return f"{_LIVE_CARD_MARKER_PREFIX}{int(user_id)}/{int(trigger_message_id)}"


def parse_live_card_footer(message: Any) -> Optional[tuple[int, int]]:
    """Recognize both old footer metadata and current invisible markers."""

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


def _profile_role_name_keys() -> set[str]:
    from .commands_ext.public_self_roles_group import _all_profile_role_names, _role_name_key

    return {_role_name_key(name) for name in _all_profile_role_names()}


def _configured_role_ids(config: Mapping[str, Any], *keys: str) -> set[int]:
    out: set[int] = set()
    for key in keys:
        raw = config.get(key)
        values = raw if isinstance(raw, (list, tuple, set, frozenset)) else [raw]
        for value in values:
            try:
                role_id = int(str(value or "0").strip())
            except Exception:
                role_id = 0
            if role_id > 0:
                out.add(role_id)
    return out


def _compact_server_role_labels(member: discord.Member, config: Mapping[str, Any]) -> list[str]:
    """Return safe real server roles, separate from member-selected profile tags."""
    from .commands_ext.public_self_roles_group import _role_name_key, _short_role_label

    profile_name_keys = _profile_role_name_keys()
    cosmetic_ids = _configured_role_ids(config, "profile_cosmetic_role_ids")
    protected_ids = _configured_role_ids(
        config,
        "unverified_role_id",
        "verified_role_id",
        "resident_role_id",
        "staff_role_id",
        "vc_staff_role_id",
        "server_control_role_id",
        "bot_manager_role_id",
    )
    excluded_ids = cosmetic_ids | protected_ids
    roles: list[discord.Role] = []
    for role in sorted(list(getattr(member, "roles", []) or []), reverse=True):
        try:
            if role.is_default() or role.managed or int(role.id) in excluded_ids:
                continue
        except Exception:
            continue
        if _role_name_key(role.name) in profile_name_keys:
            continue
        roles.append(role)
    return [_short_role_label(role.name) for role in roles[:3]]


def _compact_profile_tag_labels(member: discord.Member, config: Mapping[str, Any]) -> list[str]:
    """Return pronouns/identity/interests and configured cosmetic tags only."""
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
        labels.append("Interests: " + " / ".join(shown) + suffix)

    cosmetic_ids = _configured_role_ids(config, "profile_cosmetic_role_ids")
    cosmetics = [
        _short_role_label(role.name)
        for role in sorted(list(getattr(member, "roles", []) or []), reverse=True)
        if int(getattr(role, "id", 0) or 0) in cosmetic_ids
    ]
    if cosmetics:
        labels.append("Tags: " + " / ".join(cosmetics[:3]))
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
        if spec is None or platform_entry_mode(entry) == "logo":
            continue
        username = ""
        if str(entry.get("username") or "").strip():
            try:
                username = display_profile_username(entry.get("username"))
            except Exception:
                username = ""
        labels.append(f"{spec.label}: {username}" if username else spec.label)
    return labels


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
    """Render one compact signature with member privacy taking precedence."""

    settings = await get_effective_profile_settings(member.guild.id, member.id)
    preferences = dict(settings.get("preferences") or {})
    if require_live_enabled and not bool(preferences.get("live_cards_enabled", True)):
        return None

    show_server_roles = (
        bool(preferences.get("show_server_roles", False))
        and "server_roles" in server_allowed_fields
    )
    show_profile_tags = (
        bool(preferences.get("show_profile_tags", True))
        and "profile_tags" in server_allowed_fields
    )
    show_dates = bool(preferences.get("show_account_dates", True)) and "account_dates" in server_allowed_fields
    show_platforms = bool(preferences.get("show_platforms", True)) and "platforms" in server_allowed_fields
    platforms = visible_platform_entries(settings.get("platforms"), allowed=show_platforms)

    try:
        guild_config = await get_guild_config(member.guild.id)
    except Exception:
        guild_config = {}
    server_role_labels = _compact_server_role_labels(member, guild_config) if show_server_roles else []
    profile_tag_labels = _compact_profile_tag_labels(member, guild_config) if show_profile_tags else []
    date_labels = _compact_date_labels(member) if show_dates else []
    platform_labels = _compact_platform_labels(platforms)
    style = effective_profile_style(preferences, guild_config)
    avatar = getattr(member, "display_avatar", None)
    avatar_identity = str(getattr(avatar, "key", None) or getattr(avatar, "url", "") or "")
    cache_key = (
        int(member.guild.id),
        int(member.id),
        str(getattr(member.guild, "name", "") or ""),
        str(getattr(member, "display_name", None) or member),
        avatar_identity,
        tuple(sorted(str(value) for value in server_allowed_fields)),
        tuple(server_role_labels),
        tuple(profile_tag_labels),
        tuple(date_labels),
        _stable_cache_value(platforms),
        _stable_cache_value(style),
    )
    image_bytes = _signature_cache_get(cache_key)
    if image_bytes is None:
        image_bytes = await render_member_profile_signature(
            member,
            style=style,
            server_role_labels=server_role_labels,
            profile_tag_labels=profile_tag_labels,
            date_labels=date_labels,
            platform_entries=platforms,
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
        url=live_card_marker_url(member.id, trigger_message_id),
    )
    embed.set_image(url=f"attachment://{filename}")

    # The image already contains public platform chips. Do not repeat usernames
    # in a large public text block; validated official URLs remain link buttons.
    view = _platform_view(platforms, owner_user_id=member.id)
    return LiveCardRender(embed=embed, view=view, file=file)


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
        self._leading: dict[_ChannelKey, asyncio.Task[Any]] = {}
        self._pending: dict[_ChannelKey, asyncio.Task[Any]] = {}
        self._latest: dict[_ChannelKey, PendingTrigger] = {}
        self._locks: dict[_ChannelKey, asyncio.Lock] = {}
        self._last_activity: dict[_ChannelKey, float] = {}
        self._last_posted: dict[_ChannelKey, tuple[int, float]] = {}
        self._latest_messages: dict[_ChannelKey, discord.Message] = {}
        self._latest_configs: dict[_ChannelKey, LiveCardConfig] = {}
        self._current_cards: dict[_ChannelKey, _CurrentCard] = {}
        self._trigger_received_at: dict[_TriggerTimeKey, float] = {}
        self._recovered_channels: set[_ChannelKey] = set()

    async def on_ready(self) -> None:
        _sync_core_dependencies()
        self._last_reconcile_at = monotonic()
        print(
            "🪪 live_profile_card ready mode=one_per_channel "
            "scheduler=quiet_window stale_render_guard=enabled"
        )

    @staticmethod
    def _task_running(task: Optional[asyncio.Task[Any]]) -> bool:
        return task is not None and not task.done()

    async def on_message(self, message: discord.Message) -> None:
        _sync_core_dependencies()
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
        self._last_activity[key] = monotonic()
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
        self._ensure_channel_worker(key)

    def _ensure_channel_worker(self, key: _ChannelKey) -> None:
        if self._task_running(self._pending.get(key)):
            return
        task = asyncio.create_task(self._run_channel_worker(key))
        self._pending[key] = task
        task.add_done_callback(
            lambda finished, resolved_key=key: self._channel_worker_done(resolved_key, finished)
        )

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
        # A message can arrive after the worker releases its old context but
        # before this callback removes the finished task. Do not strand it.
        if key in self._latest and not self._task_running(self._pending.get(key)):
            self._ensure_channel_worker(key)

    def _prune_trigger_times(self) -> None:
        if len(self._trigger_received_at) <= 2048:
            return
        oldest = sorted(self._trigger_received_at.items(), key=lambda item: item[1])[:512]
        for trigger_key, _created_at in oldest:
            self._trigger_received_at.pop(trigger_key, None)

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

        history = getattr(channel, "history", None)
        if include_history and callable(history):
            try:
                async for candidate in history(limit=LIVE_CARD_HISTORY_SCAN_LIMIT):
                    if int(getattr(candidate.author, "id", 0) or 0) != int(bot_user.id):
                        continue
                    if parse_live_card_footer(candidate) is not None:
                        owned[int(candidate.id)] = candidate
            except Exception as exc:
                raise _CurrentCardVerificationUnavailable from exc
        return owned

    async def _read_channel_states(self, key: _ChannelKey) -> list[dict[str, Any]]:
        """Read all rows, with the pre-migration single-row API as fallback."""

        try:
            rows = await list_live_card_states_for_channel(*key)
            return [dict(item) for item in rows if isinstance(item, Mapping)]
        except ProfileStorageUnavailable:
            try:
                legacy = await get_live_card_state(*key)
            except Exception as exc:
                raise _CurrentCardVerificationUnavailable from exc
            return [dict(legacy)] if isinstance(legacy, Mapping) else []
        except Exception as exc:
            raise _CurrentCardVerificationUnavailable from exc

    async def _load_current_card(self, channel: discord.TextChannel) -> Optional[_CurrentCard]:
        key = (int(channel.guild.id), int(channel.id))
        cached = self._current_cards.get(key)
        if cached is not None:
            return cached
        states = await self._read_channel_states(key)
        owned = await self._verified_owned_messages(
            channel,
            states,
            include_history=key not in self._recovered_channels,
        )

        newest = max(owned.values(), key=lambda item: int(item.id), default=None)
        for old in sorted(owned.values(), key=lambda item: int(item.id)):
            if newest is not None and int(old.id) == int(newest.id):
                continue
            if not await self._delete_verified_card(old):
                raise _CurrentCardVerificationUnavailable

        # The deployed table may still permit per-member rows. Collapse every row
        # in this channel before storing the single surviving owner. An actually
        # empty channel performs no pointless delete, which also preserves the
        # historical single-row test and compatibility path.
        if states:
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
        current = self._latest.get(key)
        # Direct internal calls used by cleanup tests and diagnostics have no
        # scheduler context. Live workers always populate _latest first.
        return current is None or current == trigger

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
        _sync_core_dependencies()
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
                f"guild={key[0]} channel={key[1]} user={trigger.user_id} "
                f"error={type(exc).__name__}: {exc}"
            )
            return
        if rendered is None or not self._is_latest(key, trigger):
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

        # Delete before send. Briefly showing no card is preferable to ever
        # showing two cards or a card beneath the wrong speaker.
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
        if not self._is_latest(key, trigger):
            await self._delete_verified_card(new_message)
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
        if not self._is_latest(key, trigger):
            await self._delete_verified_card(new_message)
            try:
                await delete_live_card_state(*key)
            except Exception:
                pass
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
        _sync_core_dependencies()
        await super().reconcile()

    async def reconcile_guild(self, guild: discord.Guild) -> None:
        _sync_core_dependencies()
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
            if self._task_running(task):
                task.cancel()
        self._leading.pop(key, None)
        self._latest.pop(key, None)
        self._latest_messages.pop(key, None)
        self._latest_configs.pop(key, None)
        self._last_activity.pop(key, None)
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
        _sync_core_dependencies()
        return await super()._remove_channel_card_state(
            guild,
            channel_id,
            cancel_pending=cancel_pending,
        )

    async def remove_user_cards(self, guild: discord.Guild, user_id: int) -> None:
        resolved = int(user_id)
        for key, trigger in list(self._latest.items()):
            if key[0] == int(guild.id) and int(trigger.user_id) == resolved:
                self._forget_channel(key)
        _sync_core_dependencies()
        await super().remove_user_cards(guild, resolved)
        for key, current in list(self._current_cards.items()):
            if key[0] == int(guild.id) and int(current.user_id) == resolved:
                self._forget_channel(key, cancel_tasks=False)

    async def remove_user_cards_all_guilds(self, user_id: int) -> None:
        resolved = int(user_id)
        for key, trigger in list(self._latest.items()):
            if int(trigger.user_id) == resolved:
                self._forget_channel(key)
        _sync_core_dependencies()
        await super().remove_user_cards_all_guilds(resolved)
        for key, current in list(self._current_cards.items()):
            if int(current.user_id) == resolved:
                self._forget_channel(key, cancel_tasks=False)

    async def invalidate_guild_cards(self, guild: discord.Guild) -> None:
        _sync_core_dependencies()
        await super().invalidate_guild_cards(guild)
        for key in list(self._current_cards):
            if key[0] == int(guild.id):
                self._forget_channel(key)

    async def disable_channel(self, guild: discord.Guild, channel: discord.TextChannel) -> None:
        await self._remove_channel_card_state(guild, channel.id)

    async def on_member_remove(self, member: discord.Member) -> None:
        await self.remove_user_cards(member.guild, member.id)

    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        _sync_core_dependencies()
        await super().on_guild_channel_delete(channel)
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
