from __future__ import annotations

"""Dedicated Discord invite blocker runtime.

This keeps Discord invite blocking separate from behavior-spam handling. If a
server enables invite blocking, Discord invite links are handled even when the
message came from a bot such as a bump/listing bot.
"""

import asyncio
import time
import re
from typing import Any, Iterable

import discord

try:
    from stoney_verify.globals import bot
except Exception:  # pragma: no cover
    bot = None  # type: ignore

_INSTALLED = False
_SWEEP_TASKS: dict[tuple[int, int], asyncio.Task] = {}
_LAST_SWEEP_AT: dict[tuple[int, int], float] = {}
_POLICY_CACHE: dict[int, tuple[float, Any, dict[str, Any]]] = {}
_SPLASH_LAST_AT: dict[tuple[int, int], float] = {}

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


async def _load_policy_cached(guild: discord.Guild, *, ttl_seconds: float = 8.0) -> tuple[Any, dict[str, Any]]:
    """Short cache for invite enforcement policy.

    This keeps instant delete fast without hammering config/database reads across
    large public deployments.
    """

    gid = int(guild.id)
    now = time.monotonic()
    cached = _POLICY_CACHE.get(gid)
    if cached is not None:
        saved_at, cfg, settings = cached
        if now - float(saved_at) <= float(ttl_seconds):
            return cfg, dict(settings or {})

    cfg, settings = await _load_policy_cached(guild)
    _POLICY_CACHE[gid] = (now, cfg, dict(settings or {}))
    return cfg, dict(settings or {})


def _looks_invite_related(message: discord.Message) -> bool:
    """Cheap gate for fallback sweeps.

    Live deletion still handles exact codes immediately. The fallback sweep only
    runs for messages likely to produce late invite cards/components.
    """

    try:
        text = _message_text(message)
        if _codes_from_text(text):
            return True

        lowered = str(text or "").lower()
        if "discord" in lowered or "invite" in lowered or "discord.gg" in lowered:
            return True

        author_is_bot = bool(getattr(message.author, "bot", False))
        has_components = bool(getattr(message, "components", None))
        has_embeds = bool(getattr(message, "embeds", None))

        if author_is_bot and (has_components or has_embeds):
            return True
    except Exception:
        pass

    return False


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



def _invite_shield_runtime_on(cfg: Any, settings: dict[str, Any]) -> bool:
    try:
        if _safe_bool(_cfg_value(cfg, "automod_block_invites", False), False):
            return True
        if _safe_bool(_cfg_value(cfg, "automod_block_links", False), False):
            return True
        if _safe_bool(settings.get("enabled"), False):
            return True
        for key in (
            "invite_shield_enabled",
            "invite_hard_block_enabled",
            "automod_block_invites",
            "block_invites",
            "spam_invite_shield_enabled",
            "spam_invite_hard_block_enabled",
            "spam_automod_block_invites",
            "spam_block_invites",
        ):
            if _safe_bool(settings.get(key), False):
                return True
    except Exception:
        pass
    return False



async def _send_invite_shield_splash(channel: discord.TextChannel, *, deleted: int = 1, source: str = "live") -> None:
    """Post a short temporary confirmation that Invite Shield handled an invite.

    This avoids silent deletes while staying non-spammy.
    """

    try:
        guild = channel.guild
        key = (int(guild.id), int(channel.id))
        now = time.monotonic()
        last = float(_SPLASH_LAST_AT.get(key, 0.0) or 0.0)

        # Prevent spam during raids/bump bursts.
        if now - last < 12.0:
            return
        _SPLASH_LAST_AT[key] = now

        me = guild.me
        if not isinstance(me, discord.Member):
            return

        perms = channel.permissions_for(me)
        if not perms.send_messages:
            return

        count_text = "an external Discord invite" if int(deleted or 1) <= 1 else f"{int(deleted)} external Discord invites"
        msg = await channel.send(
            f"🛡️ **Invite Shield blocked {count_text}.**\n"
            "Only approved or this-server invite links are allowed here.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

        # Clean up the splash if possible. If Manage Messages is missing,
        # the notice stays, which is still better than silent deletes.
        try:
            if perms.manage_messages:
                await msg.delete(delay=8)
        except Exception:
            pass
    except Exception as exc:
        _log(f"splash failed source={source}: {type(exc).__name__}: {exc}")

async def _sweep_channel_recent_invites(channel: discord.TextChannel, *, reason: str = "fallback") -> None:
    try:
        guild = channel.guild
        cfg, settings = await _load_policy_cached(guild)
        if not _invite_shield_runtime_on(cfg, settings):
            return

        key = (int(guild.id), int(channel.id))
        now = time.monotonic()
        last = float(_LAST_SWEEP_AT.get(key, 0.0) or 0.0)
        if now - last < 8.0:
            return
        _LAST_SWEEP_AT[key] = now

        try:
            from stoney_verify.startup_guards.protection_invite_toggle_cleanup_guard import _clean_existing_invites
        except Exception as exc:
            _log(f"sweep unavailable reason={type(exc).__name__}")
            return

        result = await _clean_existing_invites(channel, limit=75)
        deleted = int((result or {}).get("deleted") or 0)
        matched = int((result or {}).get("matched") or 0)
        failed = int((result or {}).get("failed") or 0)

        if deleted > 0:
            await _send_invite_shield_splash(channel, deleted=deleted, source=reason)

        if matched or deleted or failed:
            _log(
                "fallback sweep complete "
                f"guild={guild.id} channel={channel.id} reason={reason} "
                f"matched={matched} deleted={deleted} failed={failed}"
            )
    except Exception as exc:
        _log(f"fallback sweep failed channel={getattr(channel, 'id', 'unknown')}: {type(exc).__name__}: {exc}")


async def _delayed_sweep(channel: discord.TextChannel, *, reason: str = "delayed") -> None:
    try:
        await asyncio.sleep(1.5)
        await _sweep_channel_recent_invites(channel, reason=reason)
        await asyncio.sleep(4.0)
        await _sweep_channel_recent_invites(channel, reason=f"{reason}-second-pass")
    finally:
        try:
            _SWEEP_TASKS.pop((int(channel.guild.id), int(channel.id)), None)
        except Exception:
            pass


def _schedule_sweep(channel: Any, *, reason: str = "message") -> None:
    try:
        if not isinstance(channel, discord.TextChannel):
            return
        key = (int(channel.guild.id), int(channel.id))
        task = _SWEEP_TASKS.get(key)
        if task is not None and not task.done():
            return
        loop = asyncio.get_running_loop()
        _SWEEP_TASKS[key] = loop.create_task(_delayed_sweep(channel, reason=reason))
    except Exception as exc:
        _log(f"schedule sweep failed: {type(exc).__name__}: {exc}")

async def _fetch_message_for_enforcement(message: discord.Message) -> discord.Message:
    """Fetch the message back from Discord before deciding it has no invite.

    Some invite cards/components/embeds are incomplete in the gateway payload, while
    a REST/history read shows the invite correctly. The Invite Shield Doctor proved
    that history can see invite codes even when live delete missed them.
    """

    try:
        channel = getattr(message, "channel", None)
        if isinstance(channel, discord.TextChannel):
            fetched = await channel.fetch_message(int(message.id))
            if isinstance(fetched, discord.Message):
                return fetched
    except Exception:
        pass
    return message


async def _enforce_message(message: discord.Message, *, source: str = "message") -> None:
    try:
        guild = message.guild
        if guild is None or not isinstance(message.author, discord.Member):
            return
        if not isinstance(message.channel, discord.TextChannel):
            return

        cfg, settings = await _load_policy_cached(guild)

        # First inspect the gateway payload.
        codes = _codes_from_text(_message_text(message))
        effective_message = message

        # If gateway did not expose a code, fetch the exact message through REST
        # and inspect the hydrated message before giving up.
        if not codes:
            fetched = await _fetch_message_for_enforcement(message)
            if fetched is not message:
                effective_message = fetched
                codes = _codes_from_text(_message_text(fetched))
                if codes:
                    _log(
                        "REST fetch recovered invite codes "
                        f"guild={guild.id} channel={message.channel.id} message={message.id} source={source} codes={','.join(codes[:5])}"
                    )

        if not codes:
            return

        should_handle, reason, blocked = await _should_handle(effective_message, cfg, settings, codes)
        if not should_handle:
            return

        try:
            await effective_message.delete(reason=f"Dank Shield Discord Invite Blocker: {reason}")
        except discord.NotFound:
            return
        except discord.Forbidden:
            await _modlog(guild, effective_message, blocked or codes, "Missing Manage Messages permission")
            return

        await _modlog(guild, effective_message, blocked or codes, f"{reason}; source={source}")
        await _send_invite_shield_splash(effective_message.channel, deleted=len(blocked or codes), source=source)
        _log(
            "deleted invite "
            f"guild={guild.id} channel={effective_message.channel.id} message={effective_message.id} "
            f"author={effective_message.author.id} source={source} codes={','.join((blocked or codes)[:5])}"
        )
    except Exception as exc:
        _log(f"enforcement failed source={source}: {type(exc).__name__}: {exc}")


async def _listener(message: discord.Message) -> None:
    try:
        if _looks_invite_related(message):
            _schedule_sweep(getattr(message, "channel", None), reason="create")
    except Exception:
        pass
    await _enforce_message(message, source="create")


async def _edit_listener(before: discord.Message, after: discord.Message) -> None:
    _ = before
    try:
        if _looks_invite_related(after):
            _schedule_sweep(getattr(after, "channel", None), reason="edit")
    except Exception:
        pass
    await _enforce_message(after, source="edit")


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

        existing_edits = list((getattr(bot, "extra_events", {}) or {}).get("on_message_edit") or [])
        if not any(getattr(fn, "__name__", "") == "_edit_listener" and getattr(fn, "__module__", "") == __name__ for fn in existing_edits):
            bot.add_listener(_edit_listener, "on_message_edit")

        _INSTALLED = True
        _log("active; Discord invite links are handled independently from behavior spam using gateway+REST enforcement")
        return True
    except Exception as exc:
        _log(f"install failed: {type(exc).__name__}: {exc}")
        return False


install()

__all__ = ["install"]