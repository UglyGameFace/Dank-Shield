from __future__ import annotations

"""Canonical live join-card delivery owned by Welcome Card Studio.

The Studio's ``welcome_card_enabled`` flag is the authoritative live gate. Join
and leave announcement toggles remain separate features and are not required for
a personalized image card to post.
"""

import asyncio
import time
import weakref
from dataclasses import dataclass
from typing import Any, Mapping, Optional

import discord

from .guild_config import get_guild_config
from .welcome_card_service import welcome_card_file, welcome_cards_enabled


@dataclass(frozen=True)
class WelcomeCardDelivery:
    sent: bool
    code: str
    channel_id: int = 0
    message_id: int = 0
    used_image: bool = False


_DELIVERY_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
_RECENT_DELIVERIES: dict[str, float] = {}
_RECENT_TTL_SECONDS = 45.0


def _log(message: str) -> None:
    try:
        print(f"🪄 welcome_card_runtime {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip().strip("<#@!&>")
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _cfg_value(cfg: Any, key: str, default: Any = None) -> Any:
    try:
        value = getattr(cfg, key, None)
        if value is not None:
            return value
    except Exception:
        pass
    try:
        if hasattr(cfg, "get"):
            value = cfg.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    for bucket in ("settings", "config", "metadata", "meta"):
        try:
            nested = getattr(cfg, bucket, None)
            if isinstance(nested, Mapping) and nested.get(key) is not None:
                return nested.get(key)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, Mapping) and nested.get(key) is not None:
                    return nested.get(key)
        except Exception:
            pass
    return default


def _cfg_text(cfg: Any, key: str, default: str) -> str:
    try:
        text = str(_cfg_value(cfg, key, "") or "").strip()
        return text if text else default
    except Exception:
        return default


def _delivery_key(member: discord.Member) -> str:
    return f"{int(member.guild.id)}:{int(member.id)}"


def _delivery_lock(member: discord.Member) -> asyncio.Lock:
    key = _delivery_key(member)
    lock = _DELIVERY_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _DELIVERY_LOCKS[key] = lock
    return lock


def _cleanup_recent(now: float) -> None:
    stale = [
        key
        for key, recorded_at in _RECENT_DELIVERIES.items()
        if now - recorded_at > _RECENT_TTL_SECONDS
    ]
    for key in stale:
        _RECENT_DELIVERIES.pop(key, None)


def resolve_join_card_channel(
    guild: discord.Guild,
    cfg: Any,
) -> tuple[Optional[discord.TextChannel], str]:
    """Resolve the exact Studio/live join-card target.

    ``join_welcome_channel_id`` is authoritative when configured. The static
    ``welcome_channel_id`` is a compatibility fallback only when no dedicated
    join target exists. A stale explicit channel never silently reroutes.
    """

    explicit_id = _safe_int(_cfg_value(cfg, "join_welcome_channel_id", None), 0)
    if explicit_id > 0:
        channel = guild.get_channel(explicit_id)
        if isinstance(channel, discord.TextChannel):
            return channel, "configured join-card channel"
        return None, f"configured join-card channel {explicit_id} is missing"

    legacy_id = _safe_int(_cfg_value(cfg, "welcome_channel_id", None), 0)
    if legacy_id > 0:
        channel = guild.get_channel(legacy_id)
        if isinstance(channel, discord.TextChannel):
            return channel, "static welcome channel compatibility fallback"
        return None, f"configured welcome channel {legacy_id} is missing"

    return None, "no join-card channel configured"


def _channel_permission_problem(channel: discord.TextChannel) -> str:
    me = channel.guild.me
    if not isinstance(me, discord.Member):
        return "bot member is unavailable"
    permissions = channel.permissions_for(me)
    required = {
        "View Channel": permissions.view_channel,
        "Send Messages": permissions.send_messages,
        "Embed Links": permissions.embed_links,
        "Read Message History": permissions.read_message_history,
    }
    missing = [name for name, allowed in required.items() if not allowed]
    return ", ".join(missing)


def _format_template(text: str, member: discord.Member, cfg: Any) -> str:
    guild = member.guild

    def channel_mention(*keys: str) -> str:
        for key in keys:
            channel_id = _safe_int(_cfg_value(cfg, key, None), 0)
            channel = guild.get_channel(channel_id) if channel_id > 0 else None
            if isinstance(channel, discord.TextChannel):
                return channel.mention
        return "not set"

    replacements = {
        "server_name": str(getattr(guild, "name", "this server") or "this server"),
        "member": str(getattr(member, "display_name", "") or member),
        "member_name": str(getattr(member, "display_name", "") or member),
        "user": str(getattr(member, "display_name", "") or member),
        "mention": member.mention,
        "member_mention": member.mention,
        "username": str(member),
        "display_name": str(getattr(member, "display_name", "") or member),
        "member_count": str(getattr(guild, "member_count", "") or ""),
        "rules_channel": channel_mention("rules_channel_id", "rules_id"),
        "verify_channel": channel_mention(
            "verify_channel_id",
            "verification_channel_id",
            "verify_id",
        ),
        "support_channel": channel_mention(
            "support_channel_id",
            "ticket_channel_id",
            "tickets_channel_id",
            "support_id",
        ),
        "random_welcome_line": "Welcome in — start with the rules, verify if needed, and enjoy the community.",
    }
    output = str(text or "")
    for key, value in replacements.items():
        output = output.replace("{" + key + "}", value)
    return output


def build_join_card_embed(member: discord.Member, cfg: Any) -> discord.Embed:
    title_template = _cfg_text(
        cfg,
        "welcome_join_title",
        "Welcome, {display_name}!",
    )
    body_template = _cfg_text(
        cfg,
        "welcome_join_body",
        "{random_welcome_line}\n\nStart here: {rules_channel} • Verify: {verify_channel} • Help: {support_channel}",
    )
    embed = discord.Embed(
        title=_format_template(title_template, member, cfg)[:256],
        description=_format_template(body_template, member, cfg)[:4000],
        color=discord.Color.green(),
        timestamp=discord.utils.utcnow(),
    )
    try:
        embed.set_thumbnail(url=member.display_avatar.url)
    except Exception:
        pass
    embed.set_footer(text="dank_shield:welcome_card_runtime:v1")
    return embed


async def send_live_welcome_card(member: discord.Member) -> WelcomeCardDelivery:
    """Render and send one canonical Studio-owned live join card."""

    key = _delivery_key(member)
    lock = _delivery_lock(member)
    if lock.locked():
        return WelcomeCardDelivery(False, "delivery_in_progress")

    async with lock:
        now = time.monotonic()
        _cleanup_recent(now)
        last = _RECENT_DELIVERIES.get(key)
        if last is not None and now - last <= _RECENT_TTL_SECONDS:
            return WelcomeCardDelivery(False, "duplicate_suppressed")

        cfg = await get_guild_config(int(member.guild.id), refresh=True)
        if not welcome_cards_enabled(cfg):
            return WelcomeCardDelivery(False, "studio_disabled")

        channel, route_reason = resolve_join_card_channel(member.guild, cfg)
        if not isinstance(channel, discord.TextChannel):
            _log(
                f"delivery skipped guild={member.guild.id} member={member.id} "
                f"reason={route_reason}"
            )
            return WelcomeCardDelivery(False, "channel_unavailable")

        permission_problem = _channel_permission_problem(channel)
        if permission_problem:
            _log(
                f"delivery skipped guild={member.guild.id} member={member.id} "
                f"channel={channel.id} missing={permission_problem}"
            )
            return WelcomeCardDelivery(False, "missing_channel_permissions", channel_id=channel.id)

        embed = build_join_card_embed(member, cfg)
        allowed_mentions = discord.AllowedMentions(
            users=True,
            roles=False,
            everyone=False,
        )

        used_image = False
        message: Optional[discord.Message] = None
        me = channel.guild.me
        can_attach = bool(
            isinstance(me, discord.Member)
            and channel.permissions_for(me).attach_files
        )

        if can_attach:
            try:
                card = await welcome_card_file(member, cfg)
                embed.set_image(url=f"attachment://{card.filename}")
                message = await channel.send(
                    content=member.mention,
                    embed=embed,
                    file=card,
                    allowed_mentions=allowed_mentions,
                )
                used_image = True
            except Exception as exc:
                _log(
                    f"image render/send failed guild={member.guild.id} member={member.id} "
                    f"channel={channel.id} error={type(exc).__name__}: {exc}; "
                    "using canonical embed fallback"
                )
        else:
            _log(
                f"image unavailable guild={member.guild.id} member={member.id} "
                f"channel={channel.id} reason=missing Attach Files; "
                "using canonical embed fallback"
            )

        if message is None:
            message = await channel.send(
                content=member.mention,
                embed=embed,
                allowed_mentions=allowed_mentions,
            )

        _RECENT_DELIVERIES[key] = time.monotonic()
        _log(
            f"delivery sent guild={member.guild.id} member={member.id} "
            f"channel={channel.id} message={getattr(message, 'id', 0)} "
            f"image={used_image} route={route_reason}"
        )
        return WelcomeCardDelivery(
            True,
            "sent",
            channel_id=int(channel.id),
            message_id=_safe_int(getattr(message, "id", 0), 0),
            used_image=used_image,
        )


__all__ = [
    "WelcomeCardDelivery",
    "build_join_card_embed",
    "resolve_join_card_channel",
    "send_live_welcome_card",
]
