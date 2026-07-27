from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from time import monotonic
from typing import Any, Awaitable, Callable, Mapping, Optional

import discord

from .guild_config import get_guild_config, upsert_guild_config
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
    normalize_server_allowed_fields,
    upsert_live_card_state,
    visible_platform_entries,
)


LIVE_ENABLED_KEY = "profile_live_cards_enabled"
LIVE_CHANNEL_IDS_KEY = "profile_live_card_channel_ids"
LIVE_ALLOWED_FIELDS_KEY = "profile_live_card_allowed_fields"
LIVE_DEBOUNCE_KEY = "profile_live_card_debounce_seconds"
LIVE_REPLACEMENT_COOLDOWN_KEY = "profile_live_card_replacement_cooldown_seconds"
LIVE_SAME_SPEAKER_COOLDOWN_KEY = "profile_live_card_same_speaker_cooldown_seconds"

DEFAULT_DEBOUNCE_SECONDS = 4.0
DEFAULT_REPLACEMENT_COOLDOWN_SECONDS = 30.0
DEFAULT_SAME_SPEAKER_COOLDOWN_SECONDS = 180.0
MIN_DEBOUNCE_SECONDS = 2.0
MAX_DEBOUNCE_SECONDS = 15.0
MIN_REPLACEMENT_COOLDOWN_SECONDS = 15.0
MAX_REPLACEMENT_COOLDOWN_SECONDS = 300.0
MIN_SAME_SPEAKER_COOLDOWN_SECONDS = 30.0
MAX_SAME_SPEAKER_COOLDOWN_SECONDS = 3600.0
LIVE_CARD_HISTORY_SCAN_LIMIT = 100
READY_RECONCILE_THROTTLE_SECONDS = 60.0
LIVE_CARD_FOOTER_PREFIX = "Dank Shield live profile"
_LIVE_CARD_FOOTER_RE = re.compile(r"^Dank Shield live profile • user:(\d+) • trigger:(\d+)$")


@dataclass(frozen=True)
class LiveCardConfig:
    enabled: bool
    channel_ids: frozenset[int]
    allowed_fields: frozenset[str]
    debounce_seconds: float
    replacement_cooldown_seconds: float
    same_speaker_cooldown_seconds: float


@dataclass(frozen=True)
class LiveCardRender:
    embed: discord.Embed
    view: Optional[discord.ui.View]


@dataclass(frozen=True)
class PendingTrigger:
    guild_id: int
    channel_id: int
    user_id: int
    message_id: int
    delay_seconds: float = 0.0


RenderProfile = Callable[[discord.Member, set[str]], Awaitable[Optional[LiveCardRender]]]
Sleep = Callable[[float], Awaitable[None]]


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _safe_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        resolved = float(value)
    except Exception:
        resolved = float(default)
    return max(float(minimum), min(float(maximum), resolved))


def _channel_ids(value: Any) -> frozenset[int]:
    if isinstance(value, str):
        raw_values = re.findall(r"\d{5,25}", value)
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw_values = list(value)
    else:
        raw_values = []
    cleaned: set[int] = set()
    for item in raw_values:
        try:
            channel_id = int(str(item).strip())
        except Exception:
            continue
        if channel_id > 0:
            cleaned.add(channel_id)
    return frozenset(cleaned)


def parse_live_card_config(config: Mapping[str, Any]) -> LiveCardConfig:
    channel_ids = _channel_ids(config.get(LIVE_CHANNEL_IDS_KEY))
    enabled = _safe_bool(config.get(LIVE_ENABLED_KEY), False) and bool(channel_ids)
    return LiveCardConfig(
        enabled=enabled,
        channel_ids=channel_ids,
        allowed_fields=frozenset(normalize_server_allowed_fields(config.get(LIVE_ALLOWED_FIELDS_KEY))),
        debounce_seconds=_safe_float(
            config.get(LIVE_DEBOUNCE_KEY),
            DEFAULT_DEBOUNCE_SECONDS,
            MIN_DEBOUNCE_SECONDS,
            MAX_DEBOUNCE_SECONDS,
        ),
        replacement_cooldown_seconds=_safe_float(
            config.get(LIVE_REPLACEMENT_COOLDOWN_KEY),
            DEFAULT_REPLACEMENT_COOLDOWN_SECONDS,
            MIN_REPLACEMENT_COOLDOWN_SECONDS,
            MAX_REPLACEMENT_COOLDOWN_SECONDS,
        ),
        same_speaker_cooldown_seconds=_safe_float(
            config.get(LIVE_SAME_SPEAKER_COOLDOWN_KEY),
            DEFAULT_SAME_SPEAKER_COOLDOWN_SECONDS,
            MIN_SAME_SPEAKER_COOLDOWN_SECONDS,
            MAX_SAME_SPEAKER_COOLDOWN_SECONDS,
        ),
    )


def live_card_footer(user_id: int, trigger_message_id: int) -> str:
    return f"{LIVE_CARD_FOOTER_PREFIX} • user:{int(user_id)} • trigger:{int(trigger_message_id)}"


def parse_live_card_footer(message: Any) -> Optional[tuple[int, int]]:
    try:
        embeds = list(getattr(message, "embeds", []) or [])
        for embed in embeds:
            footer = str(getattr(getattr(embed, "footer", None), "text", "") or "")
            match = _LIVE_CARD_FOOTER_RE.fullmatch(footer)
            if match:
                return int(match.group(1)), int(match.group(2))
    except Exception:
        return None
    return None


def _state_age_seconds(state: Optional[Mapping[str, Any]]) -> Optional[float]:
    if not isinstance(state, Mapping):
        return None
    raw = str(state.get("updated_at") or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds())
    except Exception:
        return None


def _is_supported_message(message: discord.Message) -> bool:
    if message.guild is None:
        return False
    if not isinstance(message.channel, discord.TextChannel):
        return False
    if getattr(message, "webhook_id", None):
        return False
    author = getattr(message, "author", None)
    if not isinstance(author, discord.Member) or author.bot:
        return False
    message_type = getattr(message, "type", discord.MessageType.default)
    return message_type in {discord.MessageType.default, discord.MessageType.reply}


def _channel_can_host_cards(channel: discord.TextChannel) -> bool:
    guild = channel.guild
    me = guild.me
    if not isinstance(me, discord.Member):
        return False
    try:
        permissions = channel.permissions_for(me)
        return bool(
            permissions.view_channel
            and permissions.send_messages
            and permissions.embed_links
            and permissions.read_message_history
            and permissions.attach_files
        )
    except Exception:
        return False


def _copy_base_profile_embed(base: discord.Embed, *, show_roles: bool, show_dates: bool) -> discord.Embed:
    embed = discord.Embed(
        title=base.title,
        description=base.description,
        color=base.color,
        timestamp=base.timestamp,
    )
    role_field_names = {"🪪 Pronouns", "🌈 Identity", "🎮 Interests", "Profile roles"}
    date_field_names = {"Joined server", "Account created"}
    for field in list(base.fields):
        field_name = str(field.name or "")
        # Live/public cards are compact. Never show a dead pagination counter,
        # and remove both the base and dynamic paginated role fields when the
        # member hides roles.
        if field_name == "Pages":
            continue
        if not show_roles and (
            field_name in role_field_names
            or field_name.startswith("Profile roles ")
        ):
            continue
        if field_name in date_field_names and not show_dates:
            continue
        embed.add_field(name=field.name, value=field.value, inline=field.inline)
    try:
        if base.thumbnail and base.thumbnail.url:
            embed.set_thumbnail(url=base.thumbnail.url)
    except Exception:
        pass
    return embed


def _platform_view(entries: list[dict[str, Any]]) -> Optional[discord.ui.View]:
    linked = [entry for entry in entries if str(entry.get("url") or "").strip()]
    if not linked:
        return None
    view = discord.ui.View(timeout=None)
    for index, entry in enumerate(linked[:20]):
        spec = PLATFORM_SPECS.get(str(entry.get("platform") or ""))
        if spec is None:
            continue
        view.add_item(
            discord.ui.Button(
                label=spec.label[:80],
                emoji=spec.emoji,
                style=discord.ButtonStyle.link,
                url=str(entry.get("url")),
                row=min(3, index // 5),
            )
        )
    return view if view.children else None


async def render_live_profile_card(
    member: discord.Member,
    server_allowed_fields: set[str],
    *,
    trigger_message_id: int,
    require_live_enabled: bool = True,
) -> Optional[LiveCardRender]:
    """Compose the existing canonical profile card with private profile settings."""
    settings = await get_effective_profile_settings(member.guild.id, member.id)
    preferences = dict(settings.get("preferences") or {})
    if require_live_enabled and not bool(preferences.get("live_cards_enabled", True)):
        return None

    show_roles = bool(preferences.get("show_roles", True)) and "roles" in server_allowed_fields
    show_dates = bool(preferences.get("show_account_dates", True)) and "account_dates" in server_allowed_fields
    show_platforms = bool(preferences.get("show_platforms", True)) and "platforms" in server_allowed_fields
    platforms = visible_platform_entries(settings.get("platforms"), allowed=show_platforms)

    # Optional privacy settings remove fields, not the basic member card.

    # Import lazily so the existing profile command module remains the sole base renderer.
    from .commands_ext.public_self_roles_group import _profile_card

    base = _profile_card(member)
    embed = _copy_base_profile_embed(base, show_roles=show_roles, show_dates=show_dates)
    embed.description = "Live member profile • only fields this member chose to share"

    if platforms:
        lines: list[str] = []
        for entry in platforms:
            spec = PLATFORM_SPECS.get(str(entry.get("platform") or ""))
            if spec is None:
                continue
            username = display_profile_username(entry.get("username"))
            lines.append(f"{spec.emoji} **{spec.label}:** `{username}`")
        if lines:
            embed.add_field(name="Connected identities", value="\n".join(lines)[:1024], inline=False)

    embed.set_footer(text=live_card_footer(member.id, trigger_message_id))
    return LiveCardRender(embed=embed, view=_platform_view(platforms))


class LiveProfileCardRuntime:
    def __init__(
        self,
        bot: Any,
        *,
        renderer: RenderProfile = render_live_profile_card,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.bot = bot
        self.renderer = renderer
        self.sleep = sleep
        self._pending: dict[tuple[int, int], asyncio.Task[Any]] = {}
        self._latest: dict[tuple[int, int], PendingTrigger] = {}
        self._locks: dict[tuple[int, int], asyncio.Lock] = {}
        self._last_posted: dict[tuple[int, int], tuple[int, float]] = {}
        self._reconcile_lock = asyncio.Lock()
        self._last_reconcile_at: Optional[float] = None

    async def on_ready(self) -> None:
        """Reconcile durable card ownership once per ready/reconnect window."""
        now = monotonic()
        if (
            self._last_reconcile_at is not None
            and now - self._last_reconcile_at < READY_RECONCILE_THROTTLE_SECONDS
        ):
            return
        async with self._reconcile_lock:
            now = monotonic()
            if (
                self._last_reconcile_at is not None
                and now - self._last_reconcile_at < READY_RECONCILE_THROTTLE_SECONDS
            ):
                return
            # Set before I/O so repeated ready events cannot create a retry storm
            # when Discord or private storage is temporarily unavailable.
            self._last_reconcile_at = now
            try:
                await self.reconcile()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(
                    "⚠️ live_profile_card ready reconciliation failed safely: "
                    f"{type(exc).__name__}: {exc}"
                )

    async def on_message(self, message: discord.Message) -> None:
        if not _is_supported_message(message):
            return

        try:
            try:
                raw_config = await get_guild_config(message.guild.id, refresh=True)
            except TypeError as exc:
                if "refresh" not in str(exc):
                    raise
                raw_config = await get_guild_config(message.guild.id)
            config = parse_live_card_config(raw_config)
        except Exception as exc:
            print(
                "⚠️ live_profile_card skipped "
                f"guild={message.guild.id} channel={message.channel.id} user={message.author.id} "
                f"reason=config_read_failed error={type(exc).__name__}: {exc}"
            )
            return
        if not config.enabled:
            return
        if message.channel.id not in config.channel_ids:
            return
        if not _channel_can_host_cards(message.channel):
            print(
                "⚠️ live_profile_card skipped "
                f"guild={message.guild.id} channel={message.channel.id} user={message.author.id} "
                "reason=channel_permissions_incomplete"
            )
            return

        key = (int(message.guild.id), int(message.channel.id))
        user_id = int(message.author.id)
        in_memory = self._last_posted.get(key)
        delay_seconds = config.debounce_seconds
        if in_memory:
            age = monotonic() - in_memory[1]
            if in_memory[0] == user_id and age < config.same_speaker_cooldown_seconds:
                return
            if age < config.replacement_cooldown_seconds:
                delay_seconds = max(
                    delay_seconds,
                    config.replacement_cooldown_seconds - age,
                )

        trigger = PendingTrigger(
            guild_id=key[0],
            channel_id=key[1],
            user_id=user_id,
            message_id=int(message.id),
            delay_seconds=delay_seconds,
        )
        self._latest[key] = trigger
        previous = self._pending.get(key)
        if previous is not None and not previous.done():
            previous.cancel()
        task = asyncio.create_task(self._run_pending(message, config, trigger))
        self._pending[key] = task
        task.add_done_callback(lambda finished, resolved_key=key: self._pending_done(resolved_key, finished))

    def _pending_done(self, key: tuple[int, int], task: asyncio.Task[Any]) -> None:
        if self._pending.get(key) is task:
            self._pending.pop(key, None)
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            print(f"⚠️ live_profile_card worker failed key={key}: {type(exc).__name__}: {exc}")

    async def _run_pending(
        self,
        message: discord.Message,
        config: LiveCardConfig,
        trigger: PendingTrigger,
    ) -> None:
        await self.sleep(max(config.debounce_seconds, trigger.delay_seconds))
        key = (trigger.guild_id, trigger.channel_id)
        if self._latest.get(key) != trigger:
            return
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            if self._latest.get(key) != trigger:
                return
            await self._replace_card(message, config, trigger)

    async def _replace_card(
        self,
        message: discord.Message,
        config: LiveCardConfig,
        trigger: PendingTrigger,
    ) -> None:
        channel = message.channel
        guild = message.guild
        member = guild.get_member(trigger.user_id) if guild else None
        if not isinstance(channel, discord.TextChannel) or not isinstance(member, discord.Member):
            return
        if not _channel_can_host_cards(channel):
            return

        state = await get_live_card_state(trigger.guild_id, trigger.channel_id)
        age = _state_age_seconds(state)
        if state and str(state.get("user_id") or "") == str(trigger.user_id):
            if age is not None and age < config.same_speaker_cooldown_seconds:
                self._last_posted[(trigger.guild_id, trigger.channel_id)] = (trigger.user_id, monotonic())
                return
        elif state and age is not None and age < config.replacement_cooldown_seconds:
            await self.sleep(config.replacement_cooldown_seconds - age)
            key = (trigger.guild_id, trigger.channel_id)
            if self._latest.get(key) != trigger:
                return

        rendered = await self.renderer(
            member,
            set(config.allowed_fields),
            trigger_message_id=trigger.message_id,
        )
        if rendered is None:
            return

        old_message_id = 0
        try:
            old_message_id = int(str((state or {}).get("message_id") or "0"))
        except Exception:
            old_message_id = 0

        new_message: Optional[discord.Message] = None
        try:
            new_message = await channel.send(
                embed=rendered.embed,
                view=rendered.view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await upsert_live_card_state(
                trigger.guild_id,
                trigger.channel_id,
                message_id=new_message.id,
                user_id=trigger.user_id,
                trigger_message_id=trigger.message_id,
            )
        except Exception:
            if new_message is not None:
                await self._delete_verified_card(new_message)
            return

        self._last_posted[(trigger.guild_id, trigger.channel_id)] = (trigger.user_id, monotonic())
        if old_message_id and old_message_id != int(new_message.id):
            await self._delete_stored_message(channel, old_message_id)

    async def _delete_stored_message(self, channel: discord.TextChannel, message_id: int) -> bool:
        try:
            message = await channel.fetch_message(int(message_id))
        except discord.NotFound:
            return True
        except Exception:
            return False
        return await self._delete_verified_card(message)

    async def _delete_verified_card(self, message: discord.Message) -> bool:
        bot_user = getattr(self.bot, "user", None)
        if bot_user is None or int(getattr(message.author, "id", 0) or 0) != int(bot_user.id):
            return False
        if parse_live_card_footer(message) is None:
            return False
        for attempt in range(3):
            try:
                await message.delete()
                return True
            except discord.NotFound:
                return True
            except discord.HTTPException:
                if attempt < 2:
                    await self.sleep(0.35 * (attempt + 1))
                    continue
                return False
            except Exception:
                return False
        return False

    async def _remove_user_card_states(
        self,
        user_id: int,
        *,
        guild_id: Optional[int] = None,
        guild_hint: Optional[discord.Guild] = None,
    ) -> None:
        resolved_user_id = int(user_id)
        resolved_guild_id = int(guild_id) if guild_id is not None else None

        # Stop an in-flight debounce before it can repost fields the member just
        # hid, even when no prior durable card exists yet.
        for key, latest in list(self._latest.items()):
            if latest.user_id != resolved_user_id:
                continue
            if resolved_guild_id is not None and key[0] != resolved_guild_id:
                continue
            pending = self._pending.pop(key, None)
            self._latest.pop(key, None)
            if pending is not None and not pending.done():
                pending.cancel()
            self._last_posted.pop(key, None)

        try:
            states = await list_live_card_states_for_user(
                resolved_user_id,
                guild_id=resolved_guild_id,
            )
        except ProfileStorageUnavailable:
            return

        for state in states:
            if str(state.get("user_id") or "") != str(resolved_user_id):
                continue
            try:
                state_guild_id = int(state.get("guild_id"))
                channel_id = int(state.get("channel_id"))
                message_id = int(str(state.get("message_id") or "0"))
            except Exception:
                continue
            if resolved_guild_id is not None and state_guild_id != resolved_guild_id:
                continue

            guild = (
                guild_hint
                if guild_hint is not None and int(guild_hint.id) == state_guild_id
                else self.bot.get_guild(state_guild_id)
            )
            if guild is None:
                # Keep durable ownership so reconciliation can retry when the
                # guild is available again.
                continue
            channel = guild.get_channel(channel_id)
            removed = not message_id
            if message_id and isinstance(channel, discord.TextChannel):
                removed = await self._delete_stored_message(channel, message_id)
            elif message_id and channel is None:
                removed = True
            if not removed:
                continue
            try:
                await delete_live_card_state(state_guild_id, channel_id, resolved_user_id)
            except ProfileStorageUnavailable:
                return
            self._last_posted.pop((state_guild_id, channel_id), None)
            self._last_posted.pop((state_guild_id, channel_id, resolved_user_id), None)

    async def remove_user_cards(self, guild: discord.Guild, user_id: int) -> None:
        """Remove this member's persisted and pending live cards in one guild."""
        await self._remove_user_card_states(
            int(user_id),
            guild_id=int(guild.id),
            guild_hint=guild,
        )

    async def remove_user_cards_all_guilds(self, user_id: int) -> None:
        """Remove only the member's actual persisted cards across all guilds."""
        await self._remove_user_card_states(int(user_id))

    async def _remove_channel_card_state(
        self,
        guild: discord.Guild,
        channel_id: int,
        *,
        cancel_pending: bool = True,
    ) -> bool:
        channel_key = (int(guild.id), int(channel_id))
        if cancel_pending:
            for key in list(self._pending):
                if tuple(key[:2]) != channel_key:
                    continue
                pending = self._pending.pop(key, None)
                self._latest.pop(key, None)
                if pending is not None and not pending.done():
                    pending.cancel()
        for key in list(self._last_posted):
            if tuple(key[:2]) == channel_key:
                self._last_posted.pop(key, None)

        try:
            states = await list_live_card_states_for_channel(*channel_key)
        except ProfileStorageUnavailable:
            return False
        if not states:
            return True

        channel = guild.get_channel(channel_id)
        all_removed = True
        for state in states:
            try:
                message_id = int(str(state.get("message_id") or "0"))
                user_id = int(str(state.get("user_id") or "0"))
            except Exception:
                message_id = user_id = 0
            removed = not message_id
            if message_id and isinstance(channel, discord.TextChannel):
                removed = await self._delete_stored_message(channel, message_id)
            elif message_id and channel is None:
                # The channel no longer exists, so the durable state is stale.
                removed = True
            if not removed:
                all_removed = False
                continue
            try:
                if user_id > 0:
                    await delete_live_card_state(*channel_key, user_id)
                else:
                    await delete_live_card_state(*channel_key)
            except ProfileStorageUnavailable:
                all_removed = False
        return all_removed

    async def invalidate_guild_cards(self, guild: discord.Guild) -> None:
        try:
            config = parse_live_card_config(await get_guild_config(guild.id))
        except Exception:
            return
        for channel_id in config.channel_ids:
            await self._remove_channel_card_state(guild, channel_id)

    async def disable_channel(self, guild: discord.Guild, channel: discord.TextChannel) -> None:
        await self._remove_channel_card_state(guild, channel.id)

    async def on_member_remove(self, member: discord.Member) -> None:
        await self.remove_user_cards(member.guild, member.id)

    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            config = await get_guild_config(channel.guild.id, refresh=True)
        except Exception:
            return
        channel_ids = set(_channel_ids(config.get(LIVE_CHANNEL_IDS_KEY)))
        if channel.id not in channel_ids:
            return
        channel_ids.discard(channel.id)
        try:
            await upsert_guild_config(
                channel.guild.id,
                {
                    LIVE_ENABLED_KEY: bool(channel_ids),
                    LIVE_CHANNEL_IDS_KEY: [str(value) for value in sorted(channel_ids)],
                },
            )
        except Exception:
            return
        await self._remove_channel_card_state(channel.guild, channel.id)

    async def reconcile(self) -> None:
        bot_user = getattr(self.bot, "user", None)
        if bot_user is None:
            return
        persisted: dict[tuple[int, int], list[dict[str, Any]]] = {}
        try:
            for row in await list_live_card_states():
                try:
                    key = (int(row["guild_id"]), int(row["channel_id"]))
                except Exception:
                    continue
                persisted.setdefault(key, []).append(row)
        except ProfileStorageUnavailable:
            return

        for guild in list(getattr(self.bot, "guilds", []) or []):
            try:
                config = parse_live_card_config(await get_guild_config(guild.id))
            except Exception:
                continue
            if not config.enabled:
                continue
            for channel_id in config.channel_ids:
                channel = guild.get_channel(channel_id)
                if not isinstance(channel, discord.TextChannel) or not _channel_can_host_cards(channel):
                    continue
                key = (int(guild.id), int(channel.id))
                states = persisted.pop(key, [])
                await self._reconcile_channel(channel, states)

        # Disabled channels are cleaned only when each stored message is still
        # verifiably Dank Shield-owned. One member's row never deletes another's.
        for (guild_id, channel_id), states in persisted.items():
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            channel = guild.get_channel(channel_id)
            for state in states:
                try:
                    message_id = int(str(state.get("message_id") or "0"))
                    user_id = int(str(state.get("user_id") or "0"))
                except Exception:
                    message_id = user_id = 0
                removed = not message_id
                if message_id and isinstance(channel, discord.TextChannel):
                    removed = await self._delete_stored_message(channel, message_id)
                elif message_id and channel is None:
                    removed = True
                if not removed:
                    continue
                try:
                    if user_id > 0:
                        await delete_live_card_state(guild_id, channel_id, user_id)
                    else:
                        await delete_live_card_state(guild_id, channel_id)
                except ProfileStorageUnavailable:
                    return

    async def _reconcile_channel(
        self,
        channel: discord.TextChannel,
        states: Optional[Any],
    ) -> None:
        bot_user = getattr(self.bot, "user", None)
        if bot_user is None:
            return
        if isinstance(states, Mapping):
            state_rows = [dict(states)]
        elif isinstance(states, (list, tuple)):
            state_rows = [dict(item) for item in states if isinstance(item, Mapping)]
        else:
            state_rows = []

        owned_by_id: dict[int, discord.Message] = {}
        persisted_users: set[int] = set()
        for state in state_rows:
            try:
                stored_message_id = int(str(state.get("message_id") or "0"))
                stored_user_id = int(str(state.get("user_id") or "0"))
            except Exception:
                stored_message_id = stored_user_id = 0
            if stored_user_id > 0:
                persisted_users.add(stored_user_id)
            if not stored_message_id:
                continue
            try:
                stored_message = await channel.fetch_message(stored_message_id)
            except discord.NotFound:
                stored_message = None
            except Exception:
                return
            if (
                stored_message is not None
                and int(getattr(stored_message.author, "id", 0) or 0) == int(bot_user.id)
                and parse_live_card_footer(stored_message) is not None
            ):
                owned_by_id[int(stored_message.id)] = stored_message

        try:
            async for message in channel.history(limit=LIVE_CARD_HISTORY_SCAN_LIMIT):
                if int(getattr(message.author, "id", 0) or 0) != int(bot_user.id):
                    continue
                if parse_live_card_footer(message) is not None:
                    owned_by_id[int(message.id)] = message
        except Exception:
            if not owned_by_id:
                return

        by_user: dict[int, list[discord.Message]] = {}
        for owned in owned_by_id.values():
            parsed = parse_live_card_footer(owned)
            if parsed is None:
                continue
            by_user.setdefault(int(parsed[0]), []).append(owned)

        live_users: set[int] = set()
        for user_id, owned in by_user.items():
            newest = max(owned, key=lambda item: int(item.id))
            parsed = parse_live_card_footer(newest)
            if parsed is None:
                continue
            live_users.add(user_id)
            try:
                await upsert_live_card_state(
                    channel.guild.id,
                    channel.id,
                    message_id=newest.id,
                    user_id=user_id,
                    trigger_message_id=int(parsed[1]),
                )
            except ProfileStorageUnavailable:
                return
            for old in owned:
                if int(old.id) != int(newest.id):
                    await self._delete_verified_card(old)

        for stale_user_id in persisted_users - live_users:
            try:
                await delete_live_card_state(channel.guild.id, channel.id, stale_user_id)
            except ProfileStorageUnavailable:
                return


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
    "live_card_footer",
    "parse_live_card_config",
    "parse_live_card_footer",
    "render_live_profile_card",
]
