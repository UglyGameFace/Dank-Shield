from __future__ import annotations

"""Public resource/audit modlog coverage.

This fills the next high-value public moderation gaps that are not covered by the
core public_modlog_coverage module:
- webhook create/update/delete activity through on_webhooks_update
- emoji create/update/delete
- sticker create/update/delete
- scheduled event create/update/delete
- AutoMod rule create/update/delete when the discord.py version exposes events

Everything resolves per-guild modlog config and safely no-ops if Discord/library
support is missing.
"""

import time
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import discord

from stoney_verify.guild_config import get_guild_config

_REGISTERED = False
_BOT_REF: Any = None
_RECENT_KEYS: dict[str, float] = {}
_TTL_SECONDS = 25.0


def _log(message: str) -> None:
    try:
        print(f"🧾 resource_modlog_coverage {message}")
    except Exception:
        pass


def _warn(message: str) -> None:
    try:
        print(f"⚠️ resource_modlog_coverage {message}")
    except Exception:
        pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _trim(value: Any, limit: int = 1024) -> str:
    text = str(value or "").strip()
    if not text:
        return "None"
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _user_line(user: Any) -> str:
    try:
        uid = _safe_int(getattr(user, "id", 0), 0)
        name = str(user or "Unknown")
        return f"{name} (`{uid}`)" if uid > 0 else name
    except Exception:
        return "Unknown"


def _object_name(value: Any) -> str:
    try:
        name = getattr(value, "name", None) or getattr(value, "title", None) or getattr(value, "id", None)
        oid = _safe_int(getattr(value, "id", 0), 0)
        return f"`{name}` (`{oid}`)" if oid > 0 else f"`{name}`"
    except Exception:
        return "Unknown"


def _mention_channel(channel: Any) -> str:
    try:
        return f"{channel.mention} (`{channel.id}`)"
    except Exception:
        return _object_name(channel)


def _ids(values: Iterable[Any]) -> set[int]:
    out: set[int] = set()
    try:
        for item in values or []:
            iid = _safe_int(getattr(item, "id", 0), 0)
            if iid > 0:
                out.add(iid)
    except Exception:
        pass
    return out


def _by_id(values: Iterable[Any]) -> dict[int, Any]:
    out: dict[int, Any] = {}
    try:
        for item in values or []:
            iid = _safe_int(getattr(item, "id", 0), 0)
            if iid > 0:
                out[iid] = item
    except Exception:
        pass
    return out


def _consume(key: str) -> bool:
    now = time.monotonic()
    try:
        stale = [k for k, ts in _RECENT_KEYS.items() if now - ts > _TTL_SECONDS]
        for k in stale[:100]:
            _RECENT_KEYS.pop(k, None)
    except Exception:
        pass
    if key in _RECENT_KEYS:
        _RECENT_KEYS[key] = now
        return True
    _RECENT_KEYS[key] = now
    return False


def _audit_action(name: str) -> Any:
    try:
        return getattr(discord.AuditLogAction, name, None)
    except Exception:
        return None


async def _audit_actor(guild: discord.Guild, *action_names: str, target_id: int = 0, limit: int = 8) -> tuple[str, str, str]:
    for action_name in action_names:
        action = _audit_action(action_name)
        if action is None:
            continue
        try:
            async for entry in guild.audit_logs(limit=limit, action=action):
                try:
                    target = getattr(entry, "target", None)
                    tid = _safe_int(getattr(target, "id", 0), 0)
                    if target_id > 0 and tid > 0 and tid != target_id:
                        continue
                    return (_user_line(getattr(entry, "user", None)), str(getattr(entry, "reason", None) or "").strip(), action_name)
                except Exception:
                    continue
        except discord.Forbidden:
            return ("Unknown — missing View Audit Log", "", action_name)
        except Exception:
            continue
    return ("Unknown", "", action_names[0] if action_names else "unknown")


def _is_writable(channel: Any, guild: discord.Guild) -> bool:
    if not isinstance(channel, discord.TextChannel):
        return False
    try:
        me = guild.me
        if me is None:
            return False
        perms = channel.permissions_for(me)
        return bool(perms.view_channel and perms.send_messages and perms.embed_links and perms.read_message_history)
    except Exception:
        return False


async def _modlog_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    try:
        cfg = await get_guild_config(guild.id)
        for attr in ("modlog_channel_id", "raidlog_channel_id", "force_verify_log_channel_id"):
            cid = _safe_int(getattr(cfg, attr, 0), 0)
            if cid <= 0:
                continue
            channel = guild.get_channel(cid)
            if channel is None:
                try:
                    channel = await guild.fetch_channel(cid)
                except Exception:
                    channel = None
            if _is_writable(channel, guild):
                return channel  # type: ignore[return-value]
    except Exception as e:
        _warn(f"failed resolving modlog guild={getattr(guild, 'id', 'unknown')}: {e!r}")
    return None


async def _send(guild: discord.Guild, embed: discord.Embed) -> None:
    channel = await _modlog_channel(guild)
    if channel is None:
        return
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except Exception as e:
        _warn(f"send failed guild={guild.id}: {e!r}")


def _embed(title: str, color: discord.Color) -> discord.Embed:
    return discord.Embed(title=title, color=color, timestamp=_utcnow())


def _field_changed(embed: discord.Embed, before: Any, after: Any, attr: str, label: str) -> None:
    try:
        old = getattr(before, attr, None)
        new = getattr(after, attr, None)
        if old != new:
            embed.add_field(name=label, value=f"Before: `{_trim(old, 450)}`\nAfter: `{_trim(new, 450)}`", inline=False)
    except Exception:
        pass


async def _on_webhooks_update(channel: discord.abc.GuildChannel) -> None:
    try:
        guild = getattr(channel, "guild", None)
        if not isinstance(guild, discord.Guild):
            return
        key = f"webhooks:{guild.id}:{getattr(channel, 'id', 0)}"
        if _consume(key):
            return
        actor, reason, action = await _audit_actor(guild, "webhook_create", "webhook_update", "webhook_delete", target_id=_safe_int(getattr(channel, "id", 0), 0), limit=10)
        embed = _embed("🪝 Webhooks Updated", discord.Color.blurple())
        embed.add_field(name="Channel", value=_mention_channel(channel), inline=False)
        embed.add_field(name="Updated By", value=actor, inline=True)
        embed.add_field(name="Audit Action", value=f"`{action}`", inline=True)
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 350), inline=False)
        embed.set_footer(text=f"Guild {guild.id} • webhook audit")
        await _send(guild, embed)
    except Exception as e:
        _warn(f"on_webhooks_update failed: {e!r}")


async def _on_guild_emojis_update(guild: discord.Guild, before: list[discord.Emoji], after: list[discord.Emoji]) -> None:
    try:
        before_map = _by_id(before)
        after_map = _by_id(after)
        added = [after_map[i] for i in sorted(_ids(after) - _ids(before))]
        removed = [before_map[i] for i in sorted(_ids(before) - _ids(after))]
        changed = [after_map[i] for i in sorted(set(before_map).intersection(after_map)) if getattr(before_map[i], "name", None) != getattr(after_map[i], "name", None)]
        if not added and not removed and not changed:
            return
        actor, reason, action = await _audit_actor(guild, "emoji_create", "emoji_update", "emoji_delete", limit=10)
        embed = _embed("😀 Emoji Updated", discord.Color.blurple())
        embed.add_field(name="Updated By", value=actor, inline=False)
        if added:
            embed.add_field(name="Added", value=_trim("\n".join(_object_name(x) for x in added[:15])), inline=False)
        if removed:
            embed.add_field(name="Removed", value=_trim("\n".join(_object_name(x) for x in removed[:15])), inline=False)
        if changed:
            lines = [f"`{getattr(before_map[x.id], 'name', 'unknown')}` → `{getattr(x, 'name', 'unknown')}` (`{x.id}`)" for x in changed[:15]]
            embed.add_field(name="Renamed", value=_trim("\n".join(lines)), inline=False)
        embed.add_field(name="Audit Action", value=f"`{action}`", inline=True)
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 350), inline=False)
        embed.set_footer(text=f"Guild {guild.id} • emoji audit")
        await _send(guild, embed)
    except Exception as e:
        _warn(f"emoji update failed: {e!r}")


async def _on_guild_stickers_update(guild: discord.Guild, before: list[Any], after: list[Any]) -> None:
    try:
        before_map = _by_id(before)
        after_map = _by_id(after)
        added = [after_map[i] for i in sorted(_ids(after) - _ids(before))]
        removed = [before_map[i] for i in sorted(_ids(before) - _ids(after))]
        changed = [after_map[i] for i in sorted(set(before_map).intersection(after_map)) if getattr(before_map[i], "name", None) != getattr(after_map[i], "name", None) or getattr(before_map[i], "description", None) != getattr(after_map[i], "description", None)]
        if not added and not removed and not changed:
            return
        actor, reason, action = await _audit_actor(guild, "sticker_create", "sticker_update", "sticker_delete", limit=10)
        embed = _embed("🏷️ Sticker Updated", discord.Color.blurple())
        embed.add_field(name="Updated By", value=actor, inline=False)
        if added:
            embed.add_field(name="Added", value=_trim("\n".join(_object_name(x) for x in added[:15])), inline=False)
        if removed:
            embed.add_field(name="Removed", value=_trim("\n".join(_object_name(x) for x in removed[:15])), inline=False)
        if changed:
            embed.add_field(name="Changed", value=_trim("\n".join(_object_name(x) for x in changed[:15])), inline=False)
        embed.add_field(name="Audit Action", value=f"`{action}`", inline=True)
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 350), inline=False)
        embed.set_footer(text=f"Guild {guild.id} • sticker audit")
        await _send(guild, embed)
    except Exception as e:
        _warn(f"sticker update failed: {e!r}")


async def _on_scheduled_event_create(event: Any) -> None:
    try:
        guild = getattr(event, "guild", None)
        if not isinstance(guild, discord.Guild):
            return
        actor, reason, action = await _audit_actor(guild, "guild_scheduled_event_create", target_id=_safe_int(getattr(event, "id", 0), 0))
        embed = _embed("📅 Scheduled Event Created", discord.Color.green())
        embed.add_field(name="Event", value=_object_name(event), inline=False)
        embed.add_field(name="Created By", value=actor, inline=False)
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 350), inline=False)
        embed.set_footer(text=f"Guild {guild.id} • scheduled event audit")
        await _send(guild, embed)
    except Exception as e:
        _warn(f"scheduled event create failed: {e!r}")


async def _on_scheduled_event_update(before: Any, after: Any) -> None:
    try:
        guild = getattr(after, "guild", None)
        if not isinstance(guild, discord.Guild):
            return
        actor, reason, action = await _audit_actor(guild, "guild_scheduled_event_update", target_id=_safe_int(getattr(after, "id", 0), 0))
        embed = _embed("📅 Scheduled Event Updated", discord.Color.blurple())
        embed.add_field(name="Event", value=_object_name(after), inline=False)
        embed.add_field(name="Updated By", value=actor, inline=False)
        for attr, label in (("name", "Name"), ("description", "Description"), ("status", "Status"), ("start_time", "Start Time"), ("end_time", "End Time"), ("location", "Location")):
            _field_changed(embed, before, after, attr, label)
        if len(embed.fields) <= 2 and not reason:
            return
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 350), inline=False)
        embed.set_footer(text=f"Guild {guild.id} • scheduled event audit")
        await _send(guild, embed)
    except Exception as e:
        _warn(f"scheduled event update failed: {e!r}")


async def _on_scheduled_event_delete(event: Any) -> None:
    try:
        guild = getattr(event, "guild", None)
        if not isinstance(guild, discord.Guild):
            return
        actor, reason, action = await _audit_actor(guild, "guild_scheduled_event_delete", target_id=_safe_int(getattr(event, "id", 0), 0))
        embed = _embed("📅 Scheduled Event Deleted", discord.Color.red())
        embed.add_field(name="Event", value=_object_name(event), inline=False)
        embed.add_field(name="Deleted By", value=actor, inline=False)
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 350), inline=False)
        embed.set_footer(text=f"Guild {guild.id} • scheduled event audit")
        await _send(guild, embed)
    except Exception as e:
        _warn(f"scheduled event delete failed: {e!r}")


async def _on_automod_rule_create(rule: Any) -> None:
    await _automod_log(rule, "🛡️ AutoMod Rule Created", "automod_rule_create", discord.Color.green())


async def _on_automod_rule_update(rule: Any) -> None:
    await _automod_log(rule, "🛡️ AutoMod Rule Updated", "automod_rule_update", discord.Color.blurple())


async def _on_automod_rule_delete(rule: Any) -> None:
    await _automod_log(rule, "🛡️ AutoMod Rule Deleted", "automod_rule_delete", discord.Color.red())


async def _automod_log(rule: Any, title: str, action_name: str, color: discord.Color) -> None:
    try:
        guild = getattr(rule, "guild", None)
        if not isinstance(guild, discord.Guild):
            return
        actor, reason, action = await _audit_actor(guild, action_name, target_id=_safe_int(getattr(rule, "id", 0), 0))
        embed = _embed(title, color)
        embed.add_field(name="Rule", value=_object_name(rule), inline=False)
        embed.add_field(name="Actor", value=actor, inline=False)
        enabled = getattr(rule, "enabled", None)
        if enabled is not None:
            embed.add_field(name="Enabled", value=f"`{enabled}`", inline=True)
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 350), inline=False)
        embed.set_footer(text=f"Guild {guild.id} • automod audit")
        await _send(guild, embed)
    except Exception as e:
        _warn(f"automod log failed: {e!r}")


def register_resource_modlog_coverage() -> bool:
    global _REGISTERED, _BOT_REF
    if _REGISTERED:
        return True
    try:
        from stoney_verify.globals import bot

        _BOT_REF = bot
        for event_name, listener in (
            ("on_webhooks_update", _on_webhooks_update),
            ("on_guild_emojis_update", _on_guild_emojis_update),
            ("on_guild_stickers_update", _on_guild_stickers_update),
            ("on_scheduled_event_create", _on_scheduled_event_create),
            ("on_scheduled_event_update", _on_scheduled_event_update),
            ("on_scheduled_event_delete", _on_scheduled_event_delete),
            ("on_automod_rule_create", _on_automod_rule_create),
            ("on_automod_rule_update", _on_automod_rule_update),
            ("on_automod_rule_delete", _on_automod_rule_delete),
        ):
            bot.add_listener(listener, event_name)
        _REGISTERED = True
        _log("registered webhook/emoji/sticker/scheduled-event/automod listeners")
        return True
    except Exception as e:
        _warn(f"registration failed: {e!r}")
        return False


register_resource_modlog_coverage()


__all__ = ["register_resource_modlog_coverage"]
