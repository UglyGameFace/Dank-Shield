from __future__ import annotations

"""Dedicated Discord invite blocker runtime.

This keeps Discord invite blocking separate from behavior-spam handling. If a
server enables invite blocking, Discord invite links are handled even when the
message came from a bot such as a bump/listing bot.
"""

import re
from typing import Any, Iterable

import discord

try:
    from stoney_verify.globals import bot
except Exception:  # pragma: no cover
    bot = None  # type: ignore

_INSTALLED = False

INVITE_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:discord(?:app)?\.com/invite|discord\.gg)\s*/\s*([A-Za-z0-9-]+)",
    re.IGNORECASE,
)


def _log(message: str) -> None:
    try:
        print(f"🛡️ discord_invite_blocker_runtime_guard {message}")
    except Exception:
        pass


def _safe_bool(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return bool(default)
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled", "all", "allow", "allowed"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled", "none", "block", "blocked"}:
            return False
    except Exception:
        pass
    return bool(default)


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
            if isinstance(nested, dict) and nested.get(key) is not None:
                return nested.get(key)
        except Exception:
            pass
        try:
            if hasattr(cfg, "get"):
                nested = cfg.get(bucket)
                if isinstance(nested, dict) and nested.get(key) is not None:
                    return nested.get(key)
        except Exception:
            pass
    return default


def _ids(values: Any) -> set[str]:
    out: set[str] = set()
    try:
        if isinstance(values, str):
            raw_items = re.split(r"[\s,;]+", values)
        elif isinstance(values, Iterable) and not isinstance(values, (bytes, dict)):
            raw_items = list(values)
        else:
            raw_items = [values]
        for raw in raw_items:
            text = str(raw or "").strip().strip("<@#!&>")
            if text.isdigit():
                out.add(text)
    except Exception:
        pass
    return out


def _codes_from_text(value: Any) -> list[str]:
    text = str(value or "")
    text = text.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "")
    text = re.sub(r"discord\s*\.\s*gg", "discord.gg", text, flags=re.IGNORECASE)
    text = re.sub(r"discord(?:app)?\s*\.\s*com\s*/\s*invite", "discord.com/invite", text, flags=re.IGNORECASE)
    compact = re.sub(r"\s+", "", text)
    codes: list[str] = []
    for source in (text, compact):
        for code in INVITE_RE.findall(source):
            cleaned = code.strip().lower()
            if cleaned and cleaned not in codes:
                codes.append(cleaned)
    return codes


def _component_text(component: Any) -> list[str]:
    parts: list[str] = []
    try:
        for attr in ("url", "label", "custom_id"):
            raw = getattr(component, attr, None)
            if raw:
                parts.append(str(raw))
    except Exception:
        pass
    try:
        for child in list(getattr(component, "children", []) or []):
            parts.extend(_component_text(child))
    except Exception:
        pass
    return parts


def _message_text(message: discord.Message) -> str:
    parts = [str(getattr(message, "content", "") or "")]
    try:
        for embed in list(getattr(message, "embeds", []) or []):
            for attr in ("title", "description", "url"):
                raw = getattr(embed, attr, None)
                if raw:
                    parts.append(str(raw))
            for field in list(getattr(embed, "fields", []) or []):
                parts.append(str(getattr(field, "name", "") or ""))
                parts.append(str(getattr(field, "value", "") or ""))
            try:
                footer = getattr(embed, "footer", None)
                if getattr(footer, "text", None):
                    parts.append(str(footer.text))
            except Exception:
                pass
            try:
                author = getattr(embed, "author", None)
                if getattr(author, "name", None):
                    parts.append(str(author.name))
                if getattr(author, "url", None):
                    parts.append(str(author.url))
            except Exception:
                pass
    except Exception:
        pass
    try:
        for row in list(getattr(message, "components", []) or []):
            parts.extend(_component_text(row))
    except Exception:
        pass
    return "\n".join(part for part in parts if part)


def _channel_ids(message: discord.Message) -> set[str]:
    out: set[str] = set()
    try:
        channel = getattr(message, "channel", None)
        for obj in (channel, getattr(channel, "parent", None), getattr(channel, "category", None)):
            cid = str(getattr(obj, "id", "") or "")
            if cid.isdigit():
                out.add(cid)
    except Exception:
        pass
    return out


def _normalize_codes(values: Any) -> set[str]:
    out: set[str] = set()
    try:
        source = values if isinstance(values, Iterable) and not isinstance(values, (str, bytes, dict)) else [values]
        for raw in source:
            text = str(raw or "").lower().strip().strip("/")
            text = text.replace("https://discord.gg/", "").replace("http://discord.gg/", "")
            text = text.replace("https://discord.com/invite/", "").replace("http://discord.com/invite/", "")
            text = text.replace("https://discordapp.com/invite/", "").replace("http://discordapp.com/invite/", "")
            if text:
                out.add(text)
    except Exception:
        pass
    return out


async def _own_invite_codes(guild: discord.Guild) -> set[str]:
    try:
        from stoney_verify import spam_guard
        getter = getattr(spam_guard, "_fetch_guild_invite_codes", None)
        if callable(getter):
            return set(str(code).lower() for code in await getter(guild))
    except Exception:
        pass
    try:
        return {str(inv.code).lower() for inv in await guild.invites() if getattr(inv, "code", None)}
    except Exception:
        return set()


async def _modlog(guild: discord.Guild, message: discord.Message, codes: list[str], reason: str) -> None:
    try:
        from stoney_verify.startup_guards import spam_guard_invite_hard_block as base
        await base._modlog(guild, message, codes, reason)
        return
    except Exception:
        pass
    try:
        from stoney_verify import spam_guard
        embed = discord.Embed(title="🛡️ Discord Invite Blocked", color=discord.Color.red(), timestamp=discord.utils.utcnow())
        embed.add_field(name="Author", value=f"{message.author.mention} (`{message.author.id}`)", inline=False)
        embed.add_field(name="Channel", value=f"{message.channel.mention} (`{message.channel.id}`)", inline=False)
        embed.add_field(name="Codes", value=", ".join(f"`{code}`" for code in codes[:8]) or "—", inline=False)
        embed.add_field(name="Reason", value=str(reason)[:1024], inline=False)
        sender = getattr(spam_guard, "_send_modlog_embed", None)
        if callable(sender):
            await sender(guild, embed)
    except Exception:
        pass


async def _load_policy(guild: discord.Guild) -> tuple[Any, dict[str, Any]]:
    cfg = None
    try:
        from stoney_verify.guild_config import get_guild_config
        cfg = await get_guild_config(int(guild.id), refresh=False)
    except Exception:
        cfg = None
    try:
        from stoney_verify import spam_guard
        settings = dict(await spam_guard.get_spam_settings(int(guild.id)))
    except Exception:
        settings = {}
    return cfg, settings


def _target_match(message: discord.Message, settings: dict[str, Any]) -> bool:
    author_id = str(getattr(message.author, "id", "") or "")
    author_is_bot = bool(getattr(message.author, "bot", False))
    all_bots = _safe_bool(settings.get("invite_hard_block_target_all_bots", settings.get("spam_invite_hard_block_target_all_bots")), False)
    bot_ids = _ids(settings.get("invite_hard_block_target_bot_ids", settings.get("spam_invite_hard_block_target_bot_ids")))
    wanted_channels = _ids(settings.get("invite_hard_block_target_channel_ids", settings.get("spam_invite_hard_block_target_channel_ids")))
    author_match = author_id in bot_ids or (author_is_bot and all_bots)
    channel_match = bool(wanted_channels and (_channel_ids(message) & wanted_channels))
    return bool(author_match or channel_match)


async def _blocked_codes(guild: discord.Guild, settings: dict[str, Any], codes: list[str]) -> list[str]:
    allowed_codes = _normalize_codes(settings.get("allowed_invite_codes", settings.get("spam_allowed_invite_codes")))
    override_own = _safe_bool(settings.get("invite_override_own_server_invites", settings.get("spam_invite_override_own_server_invites")), False)
    allow_own = _safe_bool(settings.get("allow_server_invites", settings.get("spam_allow_server_invites")), True)
    own_codes: set[str] = set()
    if allow_own and not override_own:
        own_codes = await _own_invite_codes(guild)
    return [code for code in codes if code not in allowed_codes and code not in own_codes]


async def _should_handle(message: discord.Message, cfg: Any, settings: dict[str, Any], codes: list[str]) -> tuple[bool, str, list[str]]:
    guild = message.guild
    if guild is None:
        return False, "no guild", []

    target_match = _target_match(message, settings)
    automod_invites = _safe_bool(_cfg_value(cfg, "automod_block_invites", False), False)
    automod_links = _safe_bool(_cfg_value(cfg, "automod_block_links", False), False)
    spam_enabled = bool(settings.get("enabled"))

    if not (target_match or automod_invites or automod_links or spam_enabled):
        return False, "invite blocker is off", []

    blocked = await _blocked_codes(guild, settings, codes)
    if not blocked:
        return False, "invite code is allowed for this server", []

    if target_match:
        return True, "watched bot/channel matched external invite", blocked

    return True, "Discord invite blocker is enabled", blocked


async def _listener(message: discord.Message) -> None:
    try:
        guild = message.guild
        if guild is None or not isinstance(message.author, discord.Member):
            return
        codes = _codes_from_text(_message_text(message))
        if not codes:
            return
        cfg, settings = await _load_policy(guild)
        should_handle, reason, blocked = await _should_handle(message, cfg, settings, codes)
        if not should_handle:
            return
        try:
            await message.delete(reason=f"Dank Shield Discord Invite Blocker: {reason}")
        except discord.NotFound:
            return
        except discord.Forbidden:
            await _modlog(guild, message, blocked or codes, "Missing Manage Messages permission")
            return
        await _modlog(guild, message, blocked or codes, reason)
    except Exception as exc:
        _log(f"listener failed: {type(exc).__name__}: {exc}")


def install() -> bool:
    global _INSTALLED
    if _INSTALLED:
        return True
    if bot is None:
        _log("bot unavailable; listener not installed")
        return False
    try:
        existing = list((getattr(bot, "extra_events", {}) or {}).get("on_message") or [])
        if not any(getattr(fn, "__name__", "") == "_listener" and getattr(fn, "__module__", "") == __name__ for fn in existing):
            bot.add_listener(_listener, "on_message")
        _INSTALLED = True
        _log("active; Discord invite links are handled independently from behavior spam")
        return True
    except Exception as exc:
        _log(f"install failed: {type(exc).__name__}: {exc}")
        return False


install()

__all__ = ["install"]