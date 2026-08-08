from __future__ import annotations

"""Canonical live member-exit delivery owned by Exit Card Studio."""

import asyncio
import time
import weakref
from dataclasses import dataclass
from typing import Any, Mapping, Optional

import discord

from .exit_card_service import exit_card_file, exit_cards_enabled
from .guild_config import get_guild_config
from .lifecycle_template_renderer import render_lifecycle_template


@dataclass(frozen=True)
class ExitCardDelivery:
    sent: bool
    code: str
    channel_id: int = 0
    message_id: int = 0
    used_image: bool = False


_DELIVERY_LOCKS: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()
_RECENT_DELIVERIES: dict[str, float] = {}
_RECENT_TTL_SECONDS = 45.0

_COMPAT_CHANNEL_KEYS = (
    "goodbye_channel_id",
    "welcome_exit_channel_id",
    "welcome_exit_log_channel_id",
    "leave_channel_id",
    "join_leave_log_channel_id",
    "join_leave_channel_id",
    "member_join_leave_log_channel_id",
    "member_lifecycle_log_channel_id",
    "member_log_channel_id",
    "member_logs_channel_id",
    "leave_log_channel_id",
    "join_exit_log_channel_id",
    "joinleave_channel_id",
    "welcome_leave_channel_id",
    "welcome_channel_id",
)


def _log(message: str) -> None:
    try:
        print(f"👋 exit_card_runtime {message}")
    except Exception:
        pass


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
            if isinstance(nested, Mapping) and key in nested:
                return nested.get(key)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, Mapping) and key in nested:
                    return nested.get(key)
        except Exception:
            pass
    return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip().strip("<#@!&>")
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _cfg_text(cfg: Any, keys: tuple[str, ...], default: str) -> str:
    for key in keys:
        try:
            raw = _cfg_value(cfg, key, None)
            if raw is not None and str(raw).strip():
                return str(raw).strip()
        except Exception:
            continue
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
    for key in [
        key
        for key, recorded_at in _RECENT_DELIVERIES.items()
        if now - recorded_at > _RECENT_TTL_SECONDS
    ]:
        _RECENT_DELIVERIES.pop(key, None)


def resolve_exit_card_channel(
    guild: discord.Guild,
    cfg: Any,
) -> tuple[Optional[discord.TextChannel], str]:
    """Resolve the exact exit target with old leave settings as compatibility.

    Once ``exit_card_channel_id`` is configured it is authoritative. A stale
    explicit Exit Studio channel never silently redirects to a legacy route.
    """

    explicit_raw = _cfg_value(cfg, "exit_card_channel_id", None)
    explicit_id = _safe_int(explicit_raw, 0)
    if explicit_raw is not None and str(explicit_raw).strip() and explicit_id > 0:
        channel = guild.get_channel(explicit_id)
        if isinstance(channel, discord.TextChannel):
            return channel, "configured exit-card channel"
        return None, f"configured exit-card channel {explicit_id} is missing"

    for key in _COMPAT_CHANNEL_KEYS:
        channel_id = _safe_int(_cfg_value(cfg, key, None), 0)
        if channel_id <= 0:
            continue
        channel = guild.get_channel(channel_id)
        if isinstance(channel, discord.TextChannel):
            return channel, f"{key} compatibility fallback"
        return None, f"configured compatibility channel {channel_id} from {key} is missing"

    return None, "no exit-card channel configured"


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
    return ", ".join(
        name for name, allowed in required.items() if not allowed
    )


def build_exit_card_embed(member: discord.Member, cfg: Any) -> discord.Embed:
    title_template = _cfg_text(
        cfg,
        ("exit_card_title", "welcome_leave_title"),
        "{display_name} left",
    )
    body_template = _cfg_text(
        cfg,
        ("exit_card_body", "welcome_leave_body"),
        "Thanks for being part of {server_name}. Members now: {member_count}.",
    )
    title = render_lifecycle_template(title_template, member, cfg)[:256]
    body = render_lifecycle_template(body_template, member, cfg)[:4000]
    embed = discord.Embed(
        title=title,
        description=body,
        color=discord.Color.dark_grey(),
        timestamp=discord.utils.utcnow(),
    )
    try:
        embed.set_thumbnail(url=member.display_avatar.url)
    except Exception:
        pass
    embed.set_footer(text="dank_shield:exit_card_runtime:v1")
    return embed


async def send_live_exit_card(member: discord.Member) -> ExitCardDelivery:
    key = _delivery_key(member)
    lock = _delivery_lock(member)
    if lock.locked():
        return ExitCardDelivery(False, "delivery_in_progress")

    async with lock:
        now = time.monotonic()
        _cleanup_recent(now)
        last = _RECENT_DELIVERIES.get(key)
        if last is not None and now - last <= _RECENT_TTL_SECONDS:
            return ExitCardDelivery(False, "duplicate_suppressed")

        cfg = await get_guild_config(int(member.guild.id), refresh=True)
        if not exit_cards_enabled(cfg):
            return ExitCardDelivery(False, "studio_disabled")

        channel, route_reason = resolve_exit_card_channel(member.guild, cfg)
        if not isinstance(channel, discord.TextChannel):
            _log(
                f"delivery skipped guild={member.guild.id} member={member.id} "
                f"reason={route_reason}"
            )
            return ExitCardDelivery(False, "channel_unavailable")

        permission_problem = _channel_permission_problem(channel)
        if permission_problem:
            _log(
                f"delivery skipped guild={member.guild.id} member={member.id} "
                f"channel={channel.id} missing={permission_problem}"
            )
            return ExitCardDelivery(
                False,
                "missing_channel_permissions",
                channel_id=int(channel.id),
            )

        embed = build_exit_card_embed(member, cfg)
        used_image = False
        message: Optional[discord.Message] = None
        me = channel.guild.me
        can_attach = bool(
            isinstance(me, discord.Member)
            and channel.permissions_for(me).attach_files
        )

        if can_attach:
            try:
                card = await exit_card_file(member, cfg)
                embed.set_image(url=f"attachment://{card.filename}")
                message = await channel.send(
                    embed=embed,
                    file=card,
                    allowed_mentions=discord.AllowedMentions.none(),
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
                embed=embed,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        _RECENT_DELIVERIES[key] = time.monotonic()
        _log(
            f"delivery sent guild={member.guild.id} member={member.id} "
            f"channel={channel.id} message={getattr(message, 'id', 0)} "
            f"image={used_image} route={route_reason}"
        )
        return ExitCardDelivery(
            True,
            "sent",
            channel_id=int(channel.id),
            message_id=_safe_int(getattr(message, "id", 0), 0),
            used_image=used_image,
        )


__all__ = [
    "ExitCardDelivery",
    "build_exit_card_embed",
    "resolve_exit_card_channel",
    "send_live_exit_card",
]
