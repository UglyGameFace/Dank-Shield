from __future__ import annotations

"""Per-guild welcome message rendering and idempotent posting.

This is separate from Discord Community Onboarding and separate from Basic Verify.
It gives each guild a reusable welcome/start-here message that points members to
that guild's saved rules, verification, and support channels.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

import discord

from .guild_config import get_guild_config, invalidate_guild_config
from .commands_ext.public_setup_group import _upsert_config

WELCOME_FOOTER = "dank_shield:welcome_message:v1"
DEFAULT_TITLE = "👋 Welcome to {server_name}!"
DEFAULT_BODY = (
    "Thanks for joining **{server_name}**. Your access may be limited until you verify.\n\n"
    "**Start here:**\n"
    "1. Read {rules}\n"
    "2. Click **Verify** in {verify}\n"
    "3. Need help? Open a ticket in {support}\n\n"
    "Once verified, the full server opens up."
)


@dataclass(frozen=True)
class WelcomePostResult:
    status: str
    channel_id: int
    message_id: int


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
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


def _cfg_int(cfg: Any, *keys: str) -> int:
    for key in keys:
        value = _safe_int(_cfg_value(cfg, key, 0), 0)
        if value > 0:
            return value
    return 0


def _cfg_str(cfg: Any, *keys: str, default: str = "") -> str:
    for key in keys:
        try:
            value = str(_cfg_value(cfg, key, "") or "").strip()
            if value:
                return value
        except Exception:
            continue
    return default


def _clean_name(value: Any) -> str:
    return str(value or "").lower().replace("_", "-").replace(" ", "-")


def _text_channel(guild: discord.Guild, channel_id: int) -> Optional[discord.TextChannel]:
    channel = guild.get_channel(int(channel_id or 0)) if int(channel_id or 0) > 0 else None
    return channel if isinstance(channel, discord.TextChannel) else None


def _channel_by_name(guild: discord.Guild, *tokens: str) -> Optional[discord.TextChannel]:
    wanted = tuple(_clean_name(token) for token in tokens if str(token or "").strip())
    if not wanted:
        return None
    try:
        for channel in list(getattr(guild, "text_channels", []) or []):
            if not isinstance(channel, discord.TextChannel):
                continue
            name = _clean_name(getattr(channel, "name", ""))
            if any(token in name for token in wanted):
                return channel
    except Exception:
        pass
    return None


def welcome_channel_for(guild: discord.Guild, cfg: Any) -> Optional[discord.TextChannel]:
    return (
        _text_channel(guild, _cfg_int(cfg, "welcome_channel_id", "start_channel_id"))
        or _channel_by_name(guild, "welcome", "start-here")
    )


def rules_channel_for(guild: discord.Guild, cfg: Any) -> Optional[discord.TextChannel]:
    return (
        _text_channel(guild, _cfg_int(cfg, "rules_channel_id", "rule_channel_id", "rules_text_channel_id"))
        or _channel_by_name(guild, "rules", "rule")
    )


def verify_channel_for(guild: discord.Guild, cfg: Any) -> Optional[discord.TextChannel]:
    return (
        _text_channel(guild, _cfg_int(cfg, "verify_channel_id", "verification_channel_id"))
        or _channel_by_name(guild, "verification", "verify")
    )


def support_channel_for(guild: discord.Guild, cfg: Any) -> Optional[discord.TextChannel]:
    return (
        _text_channel(guild, _cfg_int(cfg, "ticket_panel_channel_id", "support_channel_id", "panel_channel_id"))
        or _channel_by_name(guild, "support", "ticket")
    )


def _mention(channel: Optional[discord.TextChannel], fallback: str) -> str:
    return channel.mention if isinstance(channel, discord.TextChannel) else fallback


def _format_template(text: str, *, guild: discord.Guild, cfg: Any) -> str:
    rules = rules_channel_for(guild, cfg)
    verify = verify_channel_for(guild, cfg)
    support = support_channel_for(guild, cfg)
    replacements = {
        "server_name": str(getattr(guild, "name", "this server") or "this server"),
        "rules": _mention(rules, "the rules channel"),
        "rules_channel": _mention(rules, "the rules channel"),
        "verify": _mention(verify, "the verification channel"),
        "verify_channel": _mention(verify, "the verification channel"),
        "support": _mention(support, "the support channel"),
        "support_channel": _mention(support, "the support channel"),
    }
    out = str(text or "")
    for key, value in replacements.items():
        out = out.replace("{" + key + "}", value)
    return out


def build_welcome_embed(guild: discord.Guild, cfg: Any) -> discord.Embed:
    title_template = _cfg_str(cfg, "welcome_message_title", "welcome_title", default=DEFAULT_TITLE)
    body_template = _cfg_str(cfg, "welcome_message_body", "welcome_body", default=DEFAULT_BODY)
    embed = discord.Embed(
        title=_format_template(title_template, guild=guild, cfg=cfg)[:256],
        description=_format_template(body_template, guild=guild, cfg=cfg)[:4000],
        color=discord.Color.green(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=WELCOME_FOOTER)
    return embed


async def _fetch_message(channel: discord.TextChannel, message_id: int) -> Optional[discord.Message]:
    if message_id <= 0:
        return None
    try:
        return await channel.fetch_message(int(message_id))
    except Exception:
        return None


async def _find_existing_welcome_message(channel: discord.TextChannel) -> Optional[discord.Message]:
    try:
        me = channel.guild.me
        me_id = int(getattr(me, "id", 0) or 0)
        async for msg in channel.history(limit=80):
            if me_id > 0 and int(getattr(getattr(msg, "author", None), "id", 0) or 0) != me_id:
                continue
            for embed in list(getattr(msg, "embeds", []) or []):
                footer = str(getattr(getattr(embed, "footer", None), "text", "") or "")
                if WELCOME_FOOTER in footer:
                    return msg
    except Exception:
        return None
    return None


async def post_or_update_welcome_message(
    guild: discord.Guild,
    *,
    channel: Optional[discord.TextChannel] = None,
    actor_id: int = 0,
) -> WelcomePostResult:
    cfg = await get_guild_config(int(guild.id), refresh=True)
    target = channel or welcome_channel_for(guild, cfg)
    if not isinstance(target, discord.TextChannel):
        raise RuntimeError("Welcome channel is not configured. Pick one with `/dank welcome set-channel` or `/dank setup`.")

    me = guild.me
    if not isinstance(me, discord.Member):
        raise RuntimeError("Dank Shield could not resolve its bot member in this server.")
    perms = target.permissions_for(me)
    if not (perms.view_channel and perms.send_messages and perms.embed_links and perms.read_message_history):
        raise RuntimeError("Dank Shield needs View Channel, Send Messages, Embed Links, and Read Message History in the welcome channel.")

    embed = build_welcome_embed(guild, cfg)
    saved_id = _cfg_int(cfg, "welcome_message_id")
    msg = await _fetch_message(target, saved_id) or await _find_existing_welcome_message(target)

    if msg is not None:
        await msg.edit(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        status = "updated"
    else:
        msg = await target.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        status = "posted"

    await _upsert_config(
        int(guild.id),
        {
            "welcome_channel_id": str(int(target.id)),
            "welcome_message_id": str(int(msg.id)),
            "welcome_message_enabled": True,
            "welcome_message_version": "1",
            "welcome_message_updated_by_id": str(int(actor_id or 0)) if int(actor_id or 0) > 0 else None,
            "welcome_message_updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    invalidate_guild_config(int(guild.id))
    return WelcomePostResult(status=status, channel_id=int(target.id), message_id=int(msg.id))


async def save_welcome_template(guild_id: int, *, title: Optional[str] = None, body: Optional[str] = None, actor_id: int = 0) -> None:
    updates: dict[str, Any] = {
        "welcome_message_updated_by_id": str(int(actor_id or 0)) if int(actor_id or 0) > 0 else None,
        "welcome_message_updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if title is not None:
        updates["welcome_message_title"] = str(title).strip()[:256]
    if body is not None:
        updates["welcome_message_body"] = str(body).strip()[:4000]
    await _upsert_config(int(guild_id), updates)
    invalidate_guild_config(int(guild_id))


async def reset_welcome_template(guild_id: int, *, actor_id: int = 0) -> None:
    await _upsert_config(
        int(guild_id),
        {
            "welcome_message_title": DEFAULT_TITLE,
            "welcome_message_body": DEFAULT_BODY,
            "welcome_message_updated_by_id": str(int(actor_id or 0)) if int(actor_id or 0) > 0 else None,
            "welcome_message_updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    invalidate_guild_config(int(guild_id))


__all__ = [
    "WELCOME_FOOTER",
    "WelcomePostResult",
    "build_welcome_embed",
    "post_or_update_welcome_message",
    "save_welcome_template",
    "reset_welcome_template",
    "welcome_channel_for",
]
