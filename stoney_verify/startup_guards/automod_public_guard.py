from __future__ import annotations

"""Public Automod message filters.

This intentionally does not replace Spam Guard or Raid Guard. It only handles
simple content filters that guild owners configure through /dank protection.
The legacy /dank automod commands stay hidden unless explicitly enabled by env.
"""

import os
import re
import time
import unicodedata
from datetime import timedelta
from typing import Any, Optional

import discord

_PATCHED = False
_LISTENER_NAME = "_dank_public_automod_listener"
_LAST_ACTION: dict[tuple[int, int, str], float] = {}

ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\u2060\ufeff]")
SPACE_RE = re.compile(r"\s+")
INVITE_RE = re.compile(r"(?:discord\.gg|discord(?:app)?\.com/invite)/[A-Za-z0-9-]+", re.I)
LINK_RE = re.compile(r"https?://[^\s<>()]+", re.I)
CUSTOM_EMOJI_RE = re.compile(r"<a?:[A-Za-z0-9_]{2,32}:\d{5,25}>")

LEET_MAP = str.maketrans(
    {
        "@": "a",
        "4": "a",
        "0": "o",
        "1": "i",
        "!": "i",
        "|": "i",
        "3": "e",
        "5": "s",
        "$": "s",
        "7": "t",
        "+": "t",
        "8": "b",
        "9": "g",
        "6": "g",
        "а": "a",  # Cyrillic lookalikes
        "е": "e",
        "о": "o",
        "р": "p",
        "с": "c",
        "х": "x",
        "у": "y",
        "к": "k",
        "м": "m",
        "н": "h",
        "т": "t",
    }
)


def _env_bool(name: str, default: bool = False) -> bool:
    try:
        raw = os.getenv(name, "")
        if raw is None or str(raw).strip() == "":
            return bool(default)
        return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}
    except Exception:
        return bool(default)


def _legacy_automod_commands_enabled() -> bool:
    return _env_bool("STONEY_EXPOSE_LEGACY_AUTOMOD_COMMANDS", False)


def _clean_filter_item(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = ZERO_WIDTH_RE.sub("", text)
    text = text.replace(",", " ")
    text = SPACE_RE.sub(" ", text).strip().casefold()
    return text


def _csv_items(value: Any) -> list[str]:
    raw = str(value or "")
    parts: list[str] = []
    for chunk in raw.replace("\n", ",").split(","):
        item = _clean_filter_item(chunk)
        if item and item not in parts:
            parts.append(item)
    return parts


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
    return default


def _cfg_bool(cfg: Any, key: str, default: bool = False) -> bool:
    try:
        raw = _cfg_value(cfg, key, default)
        if isinstance(raw, bool):
            return raw
        return str(raw or "").strip().lower() in {"1", "true", "yes", "y", "on", "enabled"}
    except Exception:
        return bool(default)


def _cfg_int(cfg: Any, key: str, default: int = 0) -> int:
    try:
        raw = _cfg_value(cfg, key, default)
        if raw is None or isinstance(raw, bool):
            return int(default)
        return int(float(str(raw).strip()))
    except Exception:
        return int(default)


def _cfg_float(cfg: Any, key: str, default: float = 0.0) -> float:
    try:
        raw = _cfg_value(cfg, key, default)
        if raw is None or isinstance(raw, bool):
            return float(default)
        return float(str(raw).strip())
    except Exception:
        return float(default)


def _normalize_for_filter(value: Any, *, compact: bool) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = ZERO_WIDTH_RE.sub("", text).translate(LEET_MAP)
    out: list[str] = []
    last_space = False
    for ch in text:
        if ch.isalnum():
            out.append(ch)
            last_space = False
        elif not compact and not last_space:
            out.append(" ")
            last_space = True
    normalized = "".join(out).strip()
    return normalized if not compact else normalized.replace(" ", "")


def _has_discord_invite(content: str) -> bool:
    text = str(content or "")
    if INVITE_RE.search(text):
        return True
    compact = _normalize_for_filter(text, compact=True)
    return any(marker in compact for marker in ("discordgg", "discordcominvite", "discordappcominvite"))


def _has_external_link(content: str) -> bool:
    text = str(content or "")
    return bool(LINK_RE.search(text) or _has_discord_invite(text))


def _is_staff_like(member: discord.Member) -> bool:
    try:
        perms = member.guild_permissions
        return bool(perms.administrator or perms.manage_messages or perms.manage_guild or perms.moderate_members)
    except Exception:
        return False


def _member_has_any_role(member: discord.Member, role_ids: list[str]) -> bool:
    ids = {str(int(getattr(role, "id", 0) or 0)) for role in list(getattr(member, "roles", []) or [])}
    return any(str(x).strip() in ids for x in role_ids)


def _caps_ratio(text: str) -> float:
    letters = [ch for ch in str(text or "") if ch.isalpha()]
    if len(letters) < 12:
        return 0.0
    uppers = sum(1 for ch in letters if ch.isupper())
    return float(uppers) / float(max(1, len(letters)))


def _bad_word_hit(content: str, bad_words: list[str]) -> Optional[str]:
    raw_lower = str(content or "").casefold()
    normalized_spaced = _normalize_for_filter(content, compact=False)
    normalized_compact = _normalize_for_filter(content, compact=True)
    for word in bad_words:
        token = _clean_filter_item(word)
        if len(token) < 2:
            continue
        token_spaced = _normalize_for_filter(token, compact=False)
        token_compact = _normalize_for_filter(token, compact=True)
        if not token_compact:
            continue
        if " " in token:
            if token in raw_lower or token_spaced in normalized_spaced or token_compact in normalized_compact:
                return token
            continue
        if len(token_compact) <= 2:
            try:
                if re.search(r"(?<!\w)" + re.escape(token_compact) + r"(?!\w)", normalized_spaced):
                    return token
            except Exception:
                pass
            continue
        if token_compact in normalized_compact:
            return token
    return None


def _should_rate_limit(guild_id: int, user_id: int, reason: str, seconds: float = 6.0) -> bool:
    key = (int(guild_id), int(user_id), str(reason))
    now = time.monotonic()
    last = _LAST_ACTION.get(key, 0.0)
    if now - last < seconds:
        return True
    _LAST_ACTION[key] = now
    return False


async def _modlog(guild: discord.Guild, message: discord.Message, reason: str) -> None:
    try:
        from stoney_verify.modlog import _post_modlog

        embed = discord.Embed(title="🛡️ Automod Action", color=discord.Color.orange(), timestamp=discord.utils.utcnow())
        embed.add_field(name="User", value=f"{message.author.mention} (`{message.author.id}`)", inline=False)
        embed.add_field(name="Channel", value=f"{message.channel.mention} (`{message.channel.id}`)", inline=False)
        embed.add_field(name="Reason", value=str(reason)[:500], inline=False)
        content = str(getattr(message, "content", "") or "")
        if content:
            embed.add_field(name="Message", value=content[:1000], inline=False)
        embed.set_footer(text="Dank Shield Automod • configured by Protection Center")
        await _post_modlog(guild, embed)
    except Exception as exc:
        try:
            print(f"⚠️ automod_public_guard modlog failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass


async def _handle_violation(message: discord.Message, *, reason: str, timeout_minutes: int = 0) -> None:
    guild = message.guild
    if guild is None or not isinstance(message.author, discord.Member):
        return
    if _should_rate_limit(int(guild.id), int(message.author.id), reason):
        return
    try:
        await message.delete()
    except Exception:
        pass
    if timeout_minutes > 0:
        try:
            until = discord.utils.utcnow() + timedelta(minutes=int(timeout_minutes))
            await message.author.timeout(until, reason=f"Dank Shield automod: {reason}")
        except Exception:
            pass
    await _modlog(guild, message, reason)
    try:
        await message.channel.send(
            f"🛡️ {message.author.mention}, your message was removed by automod: **{reason}**",
            delete_after=8,
            allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
        )
    except Exception:
        pass


async def _automod_message_listener(message: discord.Message) -> None:
    try:
        guild = message.guild
        if guild is None or not isinstance(message.author, discord.Member):
            return
        if getattr(message.author, "bot", False):
            return
        if _is_staff_like(message.author):
            return

        from stoney_verify.guild_config import get_guild_config

        cfg = await get_guild_config(int(guild.id), refresh=False)
        if not _cfg_bool(cfg, "automod_enabled", False):
            return

        ignored_channels = _csv_items(_cfg_value(cfg, "automod_ignored_channel_ids", ""))
        if str(int(getattr(message.channel, "id", 0) or 0)) in ignored_channels:
            return
        ignored_roles = _csv_items(_cfg_value(cfg, "automod_ignored_role_ids", ""))
        if ignored_roles and _member_has_any_role(message.author, ignored_roles):
            return

        content = str(message.content or "")
        bad_words = _csv_items(_cfg_value(cfg, "automod_bad_words", ""))
        bad_hit = _bad_word_hit(content, bad_words)
        if bad_hit:
            return await _handle_violation(message, reason=f"blocked word/phrase: {bad_hit}", timeout_minutes=_cfg_int(cfg, "automod_timeout_minutes", 0))

        if _cfg_bool(cfg, "automod_block_invites", False) and _has_discord_invite(content):
            return await _handle_violation(message, reason="Discord invite link blocked", timeout_minutes=_cfg_int(cfg, "automod_timeout_minutes", 0))

        if _cfg_bool(cfg, "automod_block_links", False) and _has_external_link(content):
            reason = "Discord invite link blocked" if _has_discord_invite(content) else "external link blocked"
            return await _handle_violation(message, reason=reason, timeout_minutes=_cfg_int(cfg, "automod_timeout_minutes", 0))

        max_mentions = _cfg_int(cfg, "automod_max_mentions", 0)
        if max_mentions > 0:
            mentions = len(getattr(message, "mentions", []) or []) + len(getattr(message, "role_mentions", []) or [])
            if getattr(message, "mention_everyone", False):
                mentions += 10
            if mentions >= max_mentions:
                return await _handle_violation(message, reason=f"too many mentions ({mentions}/{max_mentions})", timeout_minutes=_cfg_int(cfg, "automod_timeout_minutes", 0))

        caps_limit = _cfg_float(cfg, "automod_caps_ratio", 0.0)
        if caps_limit > 0 and len(content) >= 18 and _caps_ratio(content) >= caps_limit:
            return await _handle_violation(message, reason="excessive caps", timeout_minutes=0)

        emoji_limit = _cfg_int(cfg, "automod_max_custom_emojis", 0)
        if emoji_limit > 0:
            count = len(CUSTOM_EMOJI_RE.findall(content))
            if count >= emoji_limit:
                return await _handle_violation(message, reason=f"custom emoji spam ({count}/{emoji_limit})", timeout_minutes=0)
    except Exception as exc:
        try:
            print(f"⚠️ automod_public_guard listener failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass


def _listener_registered(bot: Any) -> bool:
    try:
        existing = list((getattr(bot, "extra_events", {}) or {}).get("on_message") or [])
        return any(getattr(fn, "__name__", "") == "_automod_message_listener" for fn in existing)
    except Exception:
        return False


def _maybe_expose_legacy_automod(bot: Any) -> None:
    if not _legacy_automod_commands_enabled():
        return
    try:
        import stoney_verify.commands_ext as commands_ext

        allowed = set(getattr(commands_ext, "_ALLOWED_STONEY_CHILDREN", set()) or set())
        allowed.add("automod")
        commands_ext._ALLOWED_STONEY_CHILDREN = allowed

        from stoney_verify.commands_ext import public_automod_group

        register = getattr(public_automod_group, "register_public_automod_group_commands", None)
        if callable(register):
            register(bot, getattr(bot, "tree", None))
    except Exception as exc:
        try:
            print(f"⚠️ automod_public_guard legacy automod expose failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass


def apply(bot: Any = None) -> bool:
    global _PATCHED
    if _PATCHED:
        return True
    if bot is None:
        try:
            from stoney_verify.globals import bot as global_bot
            bot = global_bot
        except Exception:
            bot = None
    if bot is None:
        return False
    try:
        if not _listener_registered(bot):
            bot.add_listener(_automod_message_listener, "on_message")
        _maybe_expose_legacy_automod(bot)
        _PATCHED = True
        print(f"✅ automod_public_guard active; message filters attached legacy_commands={_legacy_automod_commands_enabled()}")
        return True
    except Exception as exc:
        try:
            print(f"⚠️ automod_public_guard failed: {type(exc).__name__}: {exc}")
        except Exception:
            pass
        return False


apply()

__all__ = ["apply"]
