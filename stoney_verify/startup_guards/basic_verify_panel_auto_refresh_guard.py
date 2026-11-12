from __future__ import annotations

"""Refresh stale Basic Verify panels on startup.

Existing Discord messages do not update when code changes. This guard edits
old bot-authored Basic Verify panels in each guild's configured verification
channel so customers do not have to delete/repost them manually.
"""

import asyncio
from typing import Any, Optional

import discord

from stoney_verify.setup_engine.verification_modes import (
    effective_verification_mode,
)

_PATCHED = False
_READY_RAN = False


def _log(message: str) -> None:
    try:
        print(f"✅ basic_verify_panel_auto_refresh_guard: {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ basic_verify_panel_auto_refresh_guard: {message}")
    except Exception:
        pass


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _cfg_value(cfg: Any, *names: str) -> Any:
    for name in names:
        try:
            if hasattr(cfg, "get"):
                value = cfg.get(name)
                if value not in (None, "", 0, "0"):
                    return value
        except Exception:
            pass
        try:
            value = getattr(cfg, name, None)
            if value not in (None, "", 0, "0"):
                return value
        except Exception:
            pass
        for bucket in ("settings", "config", "metadata", "meta"):
            try:
                nested = getattr(cfg, bucket, None)
                if isinstance(nested, dict) and nested.get(name) not in (None, "", 0, "0"):
                    return nested.get(name)
            except Exception:
                pass
    return None


async def _load_cfg(guild: discord.Guild) -> Any:
    from stoney_verify.guild_config import get_guild_config

    return await get_guild_config(int(guild.id), refresh=True)


def _channel_by_name(guild: discord.Guild, *tokens: str) -> Optional[discord.TextChannel]:
    wanted = tuple(str(token or "").lower().replace("_", "-").replace(" ", "-") for token in tokens if str(token or "").strip())
    if not wanted:
        return None

    for channel in list(getattr(guild, "text_channels", []) or []):
        if not isinstance(channel, discord.TextChannel):
            continue
        name = str(getattr(channel, "name", "") or "").lower().replace("_", "-").replace(" ", "-")
        if any(token in name for token in wanted):
            return channel
    return None


def _verify_channel(
    guild: discord.Guild,
    cfg: Any,
) -> Optional[discord.TextChannel]:
    """Return only the explicitly configured verification channel.

    Startup repair must never guess by channel name. A guild owner or staff
    member must explicitly configure the verification channel before Dank
    Shield is allowed to edit an existing panel during boot.
    """
    cid = _safe_int(
        _cfg_value(
            cfg,
            "verify_channel_id",
            "verification_channel_id",
        ),
        0,
    )
    if cid <= 0:
        return None

    channel = guild.get_channel(cid)
    return channel if isinstance(channel, discord.TextChannel) else None


def _can_scan(channel: discord.TextChannel) -> bool:
    try:
        me = channel.guild.me
        if not isinstance(me, discord.Member):
            return False
        perms = channel.permissions_for(me)
        return bool(perms.view_channel and perms.read_message_history)
    except Exception:
        return False


async def refresh_basic_verify_panel(
    guild: discord.Guild, *, reason: str = "startup",
    voice_safety_enabled_override: Optional[bool] = None,
) -> bool:
    try:
        from stoney_verify.verification_new.basic_verify import (
            BasicVerifyView,
            build_basic_verify_embed,
            is_basic_verify_panel_embed,
            register_basic_verify_runtime,
        )

        try:
            from stoney_verify.globals import bot
            register_basic_verify_runtime(bot)
        except Exception:
            pass

        cfg = await _load_cfg(guild)

        mode = effective_verification_mode(guild, cfg)
        if mode != "basic_button":
            _log(
                "skipped disabled Basic Verify refresh "
                f"guild={guild.id} mode={mode}"
            )
            return False

        channel = _verify_channel(guild, cfg)
        if not isinstance(channel, discord.TextChannel):
            _log(
                "skipped Basic Verify refresh; no explicitly configured "
                f"verification channel guild={guild.id}"
            )
            return False
        me_id = int(getattr(getattr(guild, "me", None), "id", 0) or 0)
        if me_id <= 0:
            return False

        def is_our_verify_panel(message: Any) -> bool:
            if int(getattr(getattr(message, "author", None), "id", 0) or 0) != me_id:
                return False
            embeds = list(getattr(message, "embeds", []) or [])
            return bool(embeds and is_basic_verify_panel_embed(embeds[0]))

        # Prefer the persisted panel identity. It may be far older than the
        # most recent 100 messages, so a history-only refresh can miss it.
        message = None
        persisted_id = _safe_int(_cfg_value(cfg, "basic_verify_panel_message_id"), 0)
        if persisted_id > 0:
            try:
                saved = await channel.fetch_message(persisted_id)
                if is_our_verify_panel(saved):
                    message = saved
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        if message is None:
            if not _can_scan(channel):
                _warn(f"cannot scan verify channel guild={guild.id} channel={getattr(channel, 'id', 0)}")
                return False
            async for candidate in channel.history(limit=100):
                if is_our_verify_panel(candidate):
                    message = candidate
                    break

        if message is None:
            return False

        # Refresh the SAME public message, including its buttons. In particular
        # disabling Voice Safety removes the monitoring agreement and decline
        # button; enabling it restores the disclosure before any new Verify click.
        # For enabling, pre-publish the disclosure while monitoring is still
        # disabled in storage. A voice-state event must not start monitoring in
        # the gap between enabling the setting and editing the public message.
        panel_cfg = cfg
        if voice_safety_enabled_override is not None:
            if not isinstance(cfg, dict):
                raise TypeError("Voice Safety panel preflight requires a config mapping")
            panel_cfg = {**cfg, "voice_safety_enabled": voice_safety_enabled_override}

        await message.edit(
            embed=build_basic_verify_embed(guild, panel_cfg),
            view=BasicVerifyView(
                voice_safety_agreement=bool(_cfg_value(panel_cfg, "voice_safety_enabled"))
            ),
        )
        _log(f"refreshed basic verify panel guild={guild.id} channel={channel.id} message={message.id} reason={reason}")
        return True
    except Exception as exc:
        _warn(f"refresh failed guild={getattr(guild, 'id', 'unknown')}: {type(exc).__name__}: {exc}")
        return False


async def _refresh_all(bot: Any) -> None:
    try:
        for guild in list(getattr(bot, "guilds", []) or []):
            if isinstance(guild, discord.Guild):
                await refresh_basic_verify_panel(guild, reason="startup")
                await asyncio.sleep(0.2)
    except Exception as exc:
        _warn(f"startup sweep failed: {type(exc).__name__}: {exc}")


async def _on_ready() -> None:
    global _READY_RAN
    if _READY_RAN:
        return
    _READY_RAN = True
    try:
        from stoney_verify.globals import bot
        await _refresh_all(bot)
    except Exception as exc:
        _warn(f"on_ready failed: {type(exc).__name__}: {exc}")


def apply() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    try:
        from stoney_verify.globals import bot

        existing = getattr(bot, "extra_events", {}) or {}
        ready_events = list(existing.get("on_ready") or []) if isinstance(existing, dict) else []
        if not any(getattr(fn, "__module__", "") == __name__ and getattr(fn, "__name__", "") == "_on_ready" for fn in ready_events):
            bot.add_listener(_on_ready, "on_ready")

        _PATCHED = True
        _log("active; stale Basic Verify panels refresh on startup")
        return True
    except Exception as exc:
        _warn(f"failed: {type(exc).__name__}: {exc}")
        return False


apply()

__all__ = ["apply", "refresh_basic_verify_panel"]
