from __future__ import annotations

"""Live-profile runtime with a compact horizontal image-signature renderer.

The proven debounce, persistence, reconciliation, and deletion lifecycle remains
in ``profile_card_runtime_core``. This module owns the compact visual payload
and the attachment-aware replacement send path.
"""

import asyncio
from dataclasses import dataclass
from io import BytesIO
from time import monotonic
from typing import Any, Awaitable, Callable, Mapping, Optional

import discord

from . import profile_card_runtime_core as _core
from .profile_card_service import (
    PLATFORM_SPECS,
    display_profile_username,
    get_effective_profile_settings,
    visible_platform_entries,
)
from .profile_signature_renderer import render_member_profile_signature
from .profile_signature_style import effective_profile_style

# Stable public constants and models from the lifecycle core.
DEFAULT_DEBOUNCE_SECONDS = _core.DEFAULT_DEBOUNCE_SECONDS
DEFAULT_REPLACEMENT_COOLDOWN_SECONDS = _core.DEFAULT_REPLACEMENT_COOLDOWN_SECONDS
DEFAULT_SAME_SPEAKER_COOLDOWN_SECONDS = _core.DEFAULT_SAME_SPEAKER_COOLDOWN_SECONDS
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
parse_live_card_config = _core.parse_live_card_config
parse_live_card_footer = _core.parse_live_card_footer

# Existing private helper imports remain available for callers and tests.
_channel_ids = _core._channel_ids
_channel_can_host_cards = _core._channel_can_host_cards
_copy_base_profile_embed = _core._copy_base_profile_embed
_state_age_seconds = _core._state_age_seconds
_platform_view = _core._platform_view

# Dependency hooks stay at this public module boundary. Existing tests and
# callers may replace these without knowing about the internal lifecycle split.
get_guild_config = _core.get_guild_config
upsert_guild_config = _core.upsert_guild_config
delete_live_card_state = _core.delete_live_card_state
get_live_card_state = _core.get_live_card_state
list_live_card_states = _core.list_live_card_states
list_live_card_states_for_user = _core.list_live_card_states_for_user
upsert_live_card_state = _core.upsert_live_card_state


@dataclass(frozen=True)
class LiveCardRender:
    embed: discord.Embed
    view: Optional[discord.ui.View]
    file: Optional[discord.File] = None


RenderProfile = Callable[..., Awaitable[Optional[LiveCardRender]]]
Sleep = Callable[[float], Awaitable[None]]


def _sync_core_dependencies() -> None:
    for name in (
        "get_guild_config",
        "upsert_guild_config",
        "delete_live_card_state",
        "get_live_card_state",
        "list_live_card_states",
        "list_live_card_states_for_user",
        "upsert_live_card_state",
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


async def render_live_profile_card(
    member: discord.Member,
    server_allowed_fields: set[str],
    *,
    trigger_message_id: int,
    require_live_enabled: bool = True,
) -> Optional[LiveCardRender]:
    """Render one compact horizontal signature with member-first privacy."""
    settings = await get_effective_profile_settings(member.guild.id, member.id)
    preferences = dict(settings.get("preferences") or {})
    if require_live_enabled and not bool(preferences.get("live_cards_enabled", True)):
        return None

    show_roles = bool(preferences.get("show_roles", True)) and "roles" in server_allowed_fields
    show_dates = bool(preferences.get("show_account_dates", True)) and "account_dates" in server_allowed_fields
    show_platforms = bool(preferences.get("show_platforms", True)) and "platforms" in server_allowed_fields
    platforms = visible_platform_entries(settings.get("platforms"), allowed=show_platforms)

    # A member may hide every optional field and still keep a basic
    # avatar/name signature. Privacy removes chips; it does not disable the card.

    try:
        cfg = await get_guild_config(member.guild.id)
    except Exception:
        cfg = {}

    image_bytes = await render_member_profile_signature(
        member,
        style=effective_profile_style(preferences, cfg),
        role_labels=_compact_role_labels(member) if show_roles else [],
        date_labels=_compact_date_labels(member) if show_dates else [],
        platform_labels=_compact_platform_labels(platforms),
    )

    filename = f"profile-signature-{int(member.id)}.png"
    file = discord.File(BytesIO(image_bytes), filename=filename)
    try:
        color = member.color if getattr(member.color, "value", 0) else discord.Color.blurple()
    except Exception:
        color = discord.Color.blurple()
    embed = discord.Embed(color=color)
    embed.set_image(url=f"attachment://{filename}")
    embed.set_footer(text=live_card_footer(member.id, trigger_message_id))
    return LiveCardRender(embed=embed, view=_platform_view(platforms), file=file)


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

    async def on_ready(self) -> None:
        _sync_core_dependencies()
        await super().on_ready()

    async def on_message(self, message: discord.Message) -> None:
        _sync_core_dependencies()
        await super().on_message(message)

    async def reconcile(self) -> None:
        _sync_core_dependencies()
        await super().reconcile()

    async def _reconcile_channel(
        self,
        channel: discord.TextChannel,
        state: Optional[Mapping[str, Any]],
    ) -> None:
        _sync_core_dependencies()
        await super()._reconcile_channel(channel, state)

    async def remove_user_cards(self, guild: discord.Guild, user_id: int) -> None:
        _sync_core_dependencies()
        await super().remove_user_cards(guild, user_id)

    async def remove_user_cards_all_guilds(self, user_id: int) -> None:
        _sync_core_dependencies()
        await super().remove_user_cards_all_guilds(user_id)

    async def invalidate_guild_cards(self, guild: discord.Guild) -> None:
        _sync_core_dependencies()
        await super().invalidate_guild_cards(guild)

    async def disable_channel(self, guild: discord.Guild, channel: discord.TextChannel) -> None:
        _sync_core_dependencies()
        await super().disable_channel(guild, channel)

    async def on_member_remove(self, member: discord.Member) -> None:
        _sync_core_dependencies()
        await super().on_member_remove(member)

    async def on_guild_channel_delete(self, channel: discord.abc.GuildChannel) -> None:
        _sync_core_dependencies()
        await super().on_guild_channel_delete(channel)

    async def _replace_card(
        self,
        message: discord.Message,
        config: LiveCardConfig,
        trigger: PendingTrigger,
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
            print(
                "⚠️ live_profile_card skipped unsupported channel "
                f"guild={trigger.guild_id} channel={trigger.channel_id} user={trigger.user_id}"
            )
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
            print(
                "ℹ️ live_profile_card skipped member disabled "
                f"guild={trigger.guild_id} channel={trigger.channel_id} user={trigger.user_id}"
            )
            return

        try:
            old_message_id = int(str((state or {}).get("message_id") or "0"))
        except Exception:
            old_message_id = 0

        new_message: Optional[discord.Message] = None
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

        self._last_posted[(trigger.guild_id, trigger.channel_id)] = (trigger.user_id, monotonic())
        if old_message_id and old_message_id != int(new_message.id):
            await self._delete_stored_message(channel, old_message_id)


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
    "parse_live_card_config",
    "parse_live_card_footer",
    "render_live_profile_card",
]
