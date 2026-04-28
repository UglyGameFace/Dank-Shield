from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import discord
from discord import app_commands

from ..guild_config import get_guild_config


# ============================================================
# public_modlog_coverage.py
# ------------------------------------------------------------
# Production-safe supplemental modlog listeners.
#
# This module fills the common moderation/audit gaps that are not covered by
# ticket, member-risk, voice, and quick-mod logging:
# - message delete/edit/bulk delete
# - RAW message delete/edit/bulk delete fallback for uncached messages
# - channel create/delete/update
# - role create/delete/update
# - thread create/delete/update
# - invite create/delete
# - guild/server setting update
#
# Rules:
# - no hardcoded channel ids
# - reads modlog_channel_id from guild_configs
# - gracefully skips unconfigured guilds
# - never pings users/roles from generated logs
# - audit-log lookup is best-effort and bounded
# - listener registration is idempotent
# ============================================================


_LISTENERS_REGISTERED = False
_COMMAND_REGISTERED = False
_BOT_REF: Any = None
_BOT_ID: Optional[int] = None
_RECENT_EVENT_KEYS: dict[str, float] = {}
_EVENT_TTL_SECONDS = 30.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _trim(value: Any, limit: int = 1024) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text or "None"
    return text[: max(0, limit - 1)] + "…"


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _id(value: Any) -> str:
    raw = _safe_int(getattr(value, "id", 0), 0)
    return str(raw) if raw > 0 else "unknown"


def _bot_id() -> Optional[int]:
    global _BOT_ID
    if _BOT_ID is not None:
        return _BOT_ID
    try:
        user = getattr(_BOT_REF, "user", None)
        uid = int(getattr(user, "id", 0) or 0)
        if uid > 0:
            _BOT_ID = uid
            return uid
    except Exception:
        pass
    return None


def _consume_event_key(key: str) -> bool:
    """Return True when this event key was already handled recently."""
    now = time.monotonic()
    try:
        stale = [k for k, ts in _RECENT_EVENT_KEYS.items() if now - ts > _EVENT_TTL_SECONDS]
        for k in stale[:100]:
            _RECENT_EVENT_KEYS.pop(k, None)
    except Exception:
        pass

    if key in _RECENT_EVENT_KEYS:
        _RECENT_EVENT_KEYS[key] = now
        return True
    _RECENT_EVENT_KEYS[key] = now
    return False


def _mention_channel(channel: Any) -> str:
    try:
        return f"{channel.mention} (`{channel.id}`)"
    except Exception:
        try:
            cid = getattr(channel, "id", "unknown")
            name = getattr(channel, "name", "unknown")
            return f"#{name} (`{cid}`)"
        except Exception:
            return "Unknown channel"


def _mention_channel_id(channel_id: Any) -> str:
    cid = _safe_int(channel_id, 0)
    if cid <= 0:
        return "Unknown channel"
    return f"<#{cid}> (`{cid}`)"


def _mention_role(role: Any) -> str:
    try:
        return f"{role.mention} (`{role.id}`)"
    except Exception:
        try:
            return f"@{getattr(role, 'name', 'unknown')} (`{getattr(role, 'id', 'unknown')}`)"
        except Exception:
            return "Unknown role"


def _user_line(user: Any) -> str:
    try:
        uid = int(getattr(user, "id", 0) or 0)
        name = str(user)
        if uid > 0:
            return f"{name} (`{uid}`)"
        return name
    except Exception:
        return "Unknown"


def _is_own_bot_user(user: Any) -> bool:
    try:
        bid = _bot_id()
        if bid is None:
            return False
        return int(getattr(user, "id", 0) or 0) == int(bid)
    except Exception:
        return False


def _is_writable_modlog(channel: Any, guild: discord.Guild) -> bool:
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
        channel_id = int(getattr(cfg, "modlog_channel_id", 0) or 0)
        if channel_id <= 0:
            return None

        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except Exception:
                channel = None

        if _is_writable_modlog(channel, guild):
            return channel  # type: ignore[return-value]

        print(f"⚠️ public_modlog_coverage modlog unavailable/wrong perms guild={guild.id} channel={channel_id}")
        return None
    except Exception as e:
        print(f"⚠️ public_modlog_coverage failed resolving modlog guild={getattr(guild, 'id', 'unknown')}: {repr(e)}")
        return None


def _audit_action(name: str) -> Optional[discord.AuditLogAction]:
    try:
        value = getattr(discord.AuditLogAction, name, None)
        return value if value is not None else None
    except Exception:
        return None


async def _find_audit_actor(
    guild: discord.Guild,
    action_name: str,
    *,
    target_id: Optional[int] = None,
    limit: int = 8,
) -> tuple[str, str]:
    action = _audit_action(action_name)
    if action is None:
        return ("Unknown", "")

    try:
        async for entry in guild.audit_logs(limit=limit, action=action):
            try:
                if target_id is not None:
                    target = getattr(entry, "target", None)
                    tid = int(getattr(target, "id", 0) or 0)
                    # Some audit entries target the channel, some target a user,
                    # and some do not expose a useful target. Only reject when
                    # Discord gave us a concrete different target id.
                    if tid and tid != int(target_id):
                        continue
                actor = getattr(entry, "user", None)
                reason = str(getattr(entry, "reason", None) or "").strip()
                return (_user_line(actor), reason)
            except Exception:
                continue
    except discord.Forbidden:
        return ("Unknown — missing View Audit Log", "")
    except Exception:
        return ("Unknown", "")

    return ("Unknown", "")


async def _send(guild: discord.Guild, embed: discord.Embed) -> None:
    channel = await _modlog_channel(guild)
    if channel is None:
        return
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except discord.Forbidden:
        print(f"⚠️ public_modlog_coverage cannot send guild={guild.id} channel={channel.id}")
    except Exception as e:
        print(f"⚠️ public_modlog_coverage send failed guild={guild.id}: {repr(e)}")


def _base_embed(title: str, color: discord.Color) -> discord.Embed:
    return discord.Embed(title=title, color=color, timestamp=_utcnow())


def _changed(before: Any, after: Any, attr: str) -> bool:
    try:
        return getattr(before, attr, None) != getattr(after, attr, None)
    except Exception:
        return False


def _field_if_changed(embed: discord.Embed, before: Any, after: Any, attr: str, label: str) -> None:
    if not _changed(before, after, attr):
        return
    try:
        old = getattr(before, attr, None)
        new = getattr(after, attr, None)
        embed.add_field(name=label, value=f"Before: `{_trim(old, 450)}`\nAfter: `{_trim(new, 450)}`", inline=False)
    except Exception:
        pass


def _overwrites_count(channel: Any) -> int:
    try:
        return len(getattr(channel, "overwrites", {}) or {})
    except Exception:
        return 0


def _role_permissions_diff(before: discord.Role, after: discord.Role) -> str:
    try:
        before_perms = dict(before.permissions)
        after_perms = dict(after.permissions)
        added = [name for name, enabled in after_perms.items() if enabled and not before_perms.get(name, False)]
        removed = [name for name, enabled in before_perms.items() if enabled and not after_perms.get(name, False)]
        lines: list[str] = []
        if added:
            lines.append("Added: " + ", ".join(f"`{x}`" for x in added[:20]))
        if removed:
            lines.append("Removed: " + ", ".join(f"`{x}`" for x in removed[:20]))
        return "\n".join(lines) if lines else "No permission bit changes detected."
    except Exception:
        return "Permission diff unavailable."


def _guild_from_payload(payload: Any) -> Optional[discord.Guild]:
    try:
        guild_id = _safe_int(getattr(payload, "guild_id", 0), 0)
        if guild_id <= 0 or _BOT_REF is None:
            return None
        guild = _BOT_REF.get_guild(guild_id)
        return guild if isinstance(guild, discord.Guild) else None
    except Exception:
        return None


async def _on_message_delete(message: discord.Message) -> None:
    try:
        guild = message.guild
        if guild is None or _is_own_bot_user(message.author):
            return
        if _consume_event_key(f"message_delete:{guild.id}:{message.id}"):
            return

        actor, reason = await _find_audit_actor(guild, "message_delete", target_id=getattr(message.author, "id", None))
        embed = _base_embed("🗑️ Message Deleted", discord.Color.orange())
        embed.add_field(name="Author", value=_user_line(message.author), inline=False)
        embed.add_field(name="Channel", value=_mention_channel(message.channel), inline=False)
        embed.add_field(name="Message ID", value=f"`{message.id}`", inline=True)
        embed.add_field(name="Deleted By", value=actor, inline=True)
        content = str(getattr(message, "content", "") or "").strip()
        if content:
            embed.add_field(name="Content", value=_trim(content, 1000), inline=False)
        attachments = list(getattr(message, "attachments", []) or [])
        if attachments:
            embed.add_field(name="Attachments", value="\n".join(_trim(getattr(a, "url", ""), 250) for a in attachments[:5]), inline=False)
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 300), inline=False)
        await _send(guild, embed)
    except Exception as e:
        print(f"⚠️ public_modlog_coverage on_message_delete failed: {repr(e)}")


async def _on_raw_message_delete(payload: discord.RawMessageDeleteEvent) -> None:
    try:
        guild = _guild_from_payload(payload)
        if guild is None:
            return
        message_id = _safe_int(getattr(payload, "message_id", 0), 0)
        if message_id <= 0:
            return
        if _consume_event_key(f"message_delete:{guild.id}:{message_id}"):
            return

        channel_id = _safe_int(getattr(payload, "channel_id", 0), 0)
        actor, reason = await _find_audit_actor(guild, "message_delete", target_id=channel_id if channel_id else None)
        embed = _base_embed("🗑️ Message Deleted", discord.Color.orange())
        embed.description = "Uncached/raw delete event. Content may be unavailable if Discord did not have the message cached."
        embed.add_field(name="Channel", value=_mention_channel_id(channel_id), inline=False)
        embed.add_field(name="Message ID", value=f"`{message_id}`", inline=True)
        embed.add_field(name="Deleted By", value=actor, inline=True)
        cached = getattr(payload, "cached_message", None)
        if cached is not None:
            author = getattr(cached, "author", None)
            if author is not None:
                embed.add_field(name="Author", value=_user_line(author), inline=False)
            content = str(getattr(cached, "content", "") or "").strip()
            if content:
                embed.add_field(name="Content", value=_trim(content, 1000), inline=False)
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 300), inline=False)
        await _send(guild, embed)
    except Exception as e:
        print(f"⚠️ public_modlog_coverage raw_message_delete failed: {repr(e)}")


async def _on_bulk_message_delete(messages: list[discord.Message]) -> None:
    try:
        if not messages:
            return
        first = messages[0]
        guild = first.guild
        if guild is None:
            return
        ids = sorted(str(getattr(m, "id", "")) for m in messages if getattr(m, "id", None))
        key = f"bulk_delete:{guild.id}:{getattr(first.channel, 'id', 0)}:{','.join(ids[:50])}"
        if _consume_event_key(key):
            return

        actor, reason = await _find_audit_actor(guild, "message_bulk_delete", target_id=getattr(first.channel, "id", None))
        authors: dict[str, int] = {}
        for msg in messages:
            try:
                if _is_own_bot_user(msg.author):
                    continue
                author_key = _user_line(msg.author)
                authors[author_key] = authors.get(author_key, 0) + 1
            except Exception:
                continue
        top_authors = sorted(authors.items(), key=lambda kv: kv[1], reverse=True)[:8]
        embed = _base_embed("🧹 Bulk Messages Deleted", discord.Color.orange())
        embed.add_field(name="Channel", value=_mention_channel(first.channel), inline=False)
        embed.add_field(name="Count", value=f"`{len(messages)}` message(s)", inline=True)
        embed.add_field(name="Deleted By", value=actor, inline=True)
        if top_authors:
            embed.add_field(name="Top Authors", value="\n".join(f"{name}: `{count}`" for name, count in top_authors), inline=False)
        if ids:
            embed.add_field(name="Message IDs", value=_trim(", ".join(f"`{mid}`" for mid in ids[:20]), 1000), inline=False)
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 300), inline=False)
        await _send(guild, embed)
    except Exception as e:
        print(f"⚠️ public_modlog_coverage on_bulk_message_delete failed: {repr(e)}")


async def _on_raw_bulk_message_delete(payload: discord.RawBulkMessageDeleteEvent) -> None:
    try:
        guild = _guild_from_payload(payload)
        if guild is None:
            return
        ids = sorted(str(mid) for mid in list(getattr(payload, "message_ids", []) or []) if mid)
        channel_id = _safe_int(getattr(payload, "channel_id", 0), 0)
        key = f"bulk_delete:{guild.id}:{channel_id}:{','.join(ids[:50])}"
        if _consume_event_key(key):
            return

        actor, reason = await _find_audit_actor(guild, "message_bulk_delete", target_id=channel_id if channel_id else None)
        embed = _base_embed("🧹 Bulk Messages Deleted", discord.Color.orange())
        embed.description = "Raw bulk delete event. Individual message content is unavailable for uncached messages."
        embed.add_field(name="Channel", value=_mention_channel_id(channel_id), inline=False)
        embed.add_field(name="Count", value=f"`{len(ids)}` message(s)", inline=True)
        embed.add_field(name="Deleted By", value=actor, inline=True)
        if ids:
            embed.add_field(name="Message IDs", value=_trim(", ".join(f"`{mid}`" for mid in ids[:20]), 1000), inline=False)
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 300), inline=False)
        await _send(guild, embed)
    except Exception as e:
        print(f"⚠️ public_modlog_coverage raw_bulk_message_delete failed: {repr(e)}")


async def _on_message_edit(before: discord.Message, after: discord.Message) -> None:
    try:
        guild = before.guild or after.guild
        if guild is None or _is_own_bot_user(before.author):
            return
        if _consume_event_key(f"message_edit:{guild.id}:{after.id}"):
            return
        before_content = str(getattr(before, "content", "") or "")
        after_content = str(getattr(after, "content", "") or "")
        if before_content == after_content:
            return
        embed = _base_embed("✏️ Message Edited", discord.Color.blurple())
        embed.add_field(name="Author", value=_user_line(before.author), inline=False)
        embed.add_field(name="Channel", value=_mention_channel(before.channel), inline=False)
        try:
            embed.add_field(name="Jump", value=f"[Open message]({after.jump_url})", inline=False)
        except Exception:
            pass
        embed.add_field(name="Before", value=_trim(before_content, 900), inline=False)
        embed.add_field(name="After", value=_trim(after_content, 900), inline=False)
        await _send(guild, embed)
    except Exception as e:
        print(f"⚠️ public_modlog_coverage on_message_edit failed: {repr(e)}")


async def _on_raw_message_edit(payload: discord.RawMessageUpdateEvent) -> None:
    try:
        guild = _guild_from_payload(payload)
        if guild is None:
            return
        message_id = _safe_int(getattr(payload, "message_id", 0), 0)
        if message_id <= 0:
            return
        if _consume_event_key(f"message_edit:{guild.id}:{message_id}"):
            return

        data = getattr(payload, "data", {}) or {}
        channel_id = _safe_int(getattr(payload, "channel_id", 0), 0)
        cached = getattr(payload, "cached_message", None)
        author = getattr(cached, "author", None) if cached is not None else None
        if author is not None and _is_own_bot_user(author):
            return

        embed = _base_embed("✏️ Message Edited", discord.Color.blurple())
        embed.description = "Raw edit event. Previous content may be unavailable if the message was not cached."
        embed.add_field(name="Channel", value=_mention_channel_id(channel_id), inline=False)
        embed.add_field(name="Message ID", value=f"`{message_id}`", inline=True)
        if author is not None:
            embed.add_field(name="Author", value=_user_line(author), inline=False)
        if "content" in data:
            embed.add_field(name="New Content", value=_trim(data.get("content"), 1000), inline=False)
        if cached is not None:
            try:
                embed.add_field(name="Jump", value=f"[Open message]({cached.jump_url})", inline=False)
            except Exception:
                pass
        await _send(guild, embed)
    except Exception as e:
        print(f"⚠️ public_modlog_coverage raw_message_edit failed: {repr(e)}")


async def _on_guild_channel_create(channel: discord.abc.GuildChannel) -> None:
    try:
        guild = channel.guild
        actor, reason = await _find_audit_actor(guild, "channel_create", target_id=getattr(channel, "id", None))
        embed = _base_embed("➕ Channel Created", discord.Color.green())
        embed.add_field(name="Channel", value=_mention_channel(channel), inline=False)
        embed.add_field(name="Type", value=f"`{type(channel).__name__}`", inline=True)
        embed.add_field(name="Created By", value=actor, inline=True)
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 300), inline=False)
        await _send(guild, embed)
    except Exception as e:
        print(f"⚠️ public_modlog_coverage channel_create failed: {repr(e)}")


async def _on_guild_channel_delete(channel: discord.abc.GuildChannel) -> None:
    try:
        guild = channel.guild
        actor, reason = await _find_audit_actor(guild, "channel_delete", target_id=getattr(channel, "id", None))
        embed = _base_embed("➖ Channel Deleted", discord.Color.red())
        embed.add_field(name="Channel", value=f"`#{getattr(channel, 'name', 'unknown')}` (`{_id(channel)}`)", inline=False)
        embed.add_field(name="Type", value=f"`{type(channel).__name__}`", inline=True)
        embed.add_field(name="Deleted By", value=actor, inline=True)
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 300), inline=False)
        await _send(guild, embed)
    except Exception as e:
        print(f"⚠️ public_modlog_coverage channel_delete failed: {repr(e)}")


async def _on_guild_channel_update(before: discord.abc.GuildChannel, after: discord.abc.GuildChannel) -> None:
    try:
        guild = after.guild
        actor, reason = await _find_audit_actor(guild, "channel_update", target_id=getattr(after, "id", None))
        embed = _base_embed("🛠️ Channel Updated", discord.Color.blurple())
        embed.add_field(name="Channel", value=_mention_channel(after), inline=False)
        embed.add_field(name="Updated By", value=actor, inline=False)
        _field_if_changed(embed, before, after, "name", "Name")
        _field_if_changed(embed, before, after, "category", "Category")
        _field_if_changed(embed, before, after, "topic", "Topic")
        _field_if_changed(embed, before, after, "slowmode_delay", "Slowmode")
        _field_if_changed(embed, before, after, "nsfw", "NSFW")
        if _overwrites_count(before) != _overwrites_count(after):
            embed.add_field(name="Permission Overwrites", value=f"Before: `{_overwrites_count(before)}`\nAfter: `{_overwrites_count(after)}`", inline=False)
        if len(embed.fields) <= 2 and not reason:
            return
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 300), inline=False)
        await _send(guild, embed)
    except Exception as e:
        print(f"⚠️ public_modlog_coverage channel_update failed: {repr(e)}")


async def _on_guild_role_create(role: discord.Role) -> None:
    try:
        actor, reason = await _find_audit_actor(role.guild, "role_create", target_id=role.id)
        embed = _base_embed("➕ Role Created", discord.Color.green())
        embed.add_field(name="Role", value=_mention_role(role), inline=False)
        embed.add_field(name="Created By", value=actor, inline=False)
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 300), inline=False)
        await _send(role.guild, embed)
    except Exception as e:
        print(f"⚠️ public_modlog_coverage role_create failed: {repr(e)}")


async def _on_guild_role_delete(role: discord.Role) -> None:
    try:
        actor, reason = await _find_audit_actor(role.guild, "role_delete", target_id=role.id)
        embed = _base_embed("➖ Role Deleted", discord.Color.red())
        embed.add_field(name="Role", value=f"`@{role.name}` (`{role.id}`)", inline=False)
        embed.add_field(name="Deleted By", value=actor, inline=False)
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 300), inline=False)
        await _send(role.guild, embed)
    except Exception as e:
        print(f"⚠️ public_modlog_coverage role_delete failed: {repr(e)}")


async def _on_guild_role_update(before: discord.Role, after: discord.Role) -> None:
    try:
        actor, reason = await _find_audit_actor(after.guild, "role_update", target_id=after.id)
        embed = _base_embed("🛠️ Role Updated", discord.Color.blurple())
        embed.add_field(name="Role", value=_mention_role(after), inline=False)
        embed.add_field(name="Updated By", value=actor, inline=False)
        _field_if_changed(embed, before, after, "name", "Name")
        _field_if_changed(embed, before, after, "color", "Color")
        _field_if_changed(embed, before, after, "hoist", "Display Separately")
        _field_if_changed(embed, before, after, "mentionable", "Mentionable")
        perm_diff = _role_permissions_diff(before, after)
        if perm_diff != "No permission bit changes detected.":
            embed.add_field(name="Permission Changes", value=_trim(perm_diff, 1000), inline=False)
        if len(embed.fields) <= 2 and not reason:
            return
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 300), inline=False)
        await _send(after.guild, embed)
    except Exception as e:
        print(f"⚠️ public_modlog_coverage role_update failed: {repr(e)}")


async def _on_thread_create(thread: discord.Thread) -> None:
    try:
        actor, reason = await _find_audit_actor(thread.guild, "thread_create", target_id=thread.id)
        embed = _base_embed("➕ Thread Created", discord.Color.green())
        embed.add_field(name="Thread", value=_mention_channel(thread), inline=False)
        embed.add_field(name="Parent", value=_mention_channel(thread.parent) if thread.parent else "Unknown", inline=False)
        embed.add_field(name="Created By", value=actor, inline=False)
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 300), inline=False)
        await _send(thread.guild, embed)
    except Exception as e:
        print(f"⚠️ public_modlog_coverage thread_create failed: {repr(e)}")


async def _on_thread_delete(thread: discord.Thread) -> None:
    try:
        actor, reason = await _find_audit_actor(thread.guild, "thread_delete", target_id=thread.id)
        embed = _base_embed("➖ Thread Deleted", discord.Color.red())
        embed.add_field(name="Thread", value=f"`{thread.name}` (`{thread.id}`)", inline=False)
        embed.add_field(name="Parent", value=_mention_channel(thread.parent) if thread.parent else "Unknown", inline=False)
        embed.add_field(name="Deleted By", value=actor, inline=False)
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 300), inline=False)
        await _send(thread.guild, embed)
    except Exception as e:
        print(f"⚠️ public_modlog_coverage thread_delete failed: {repr(e)}")


async def _on_thread_update(before: discord.Thread, after: discord.Thread) -> None:
    try:
        actor, reason = await _find_audit_actor(after.guild, "thread_update", target_id=after.id)
        embed = _base_embed("🛠️ Thread Updated", discord.Color.blurple())
        embed.add_field(name="Thread", value=_mention_channel(after), inline=False)
        embed.add_field(name="Updated By", value=actor, inline=False)
        _field_if_changed(embed, before, after, "name", "Name")
        _field_if_changed(embed, before, after, "archived", "Archived")
        _field_if_changed(embed, before, after, "locked", "Locked")
        _field_if_changed(embed, before, after, "slowmode_delay", "Slowmode")
        if len(embed.fields) <= 2 and not reason:
            return
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 300), inline=False)
        await _send(after.guild, embed)
    except Exception as e:
        print(f"⚠️ public_modlog_coverage thread_update failed: {repr(e)}")


async def _on_invite_create(invite: discord.Invite) -> None:
    try:
        guild = invite.guild if isinstance(invite.guild, discord.Guild) else None
        if guild is None:
            return
        embed = _base_embed("🔗 Invite Created", discord.Color.green())
        embed.add_field(name="Code", value=f"`{invite.code}`", inline=True)
        embed.add_field(name="Channel", value=_mention_channel(invite.channel), inline=True)
        embed.add_field(name="Created By", value=_user_line(invite.inviter), inline=False)
        try:
            embed.add_field(name="Max Uses / Max Age", value=f"`{invite.max_uses or 'unlimited'}` / `{invite.max_age or 'unlimited'}s`", inline=False)
        except Exception:
            pass
        await _send(guild, embed)
    except Exception as e:
        print(f"⚠️ public_modlog_coverage invite_create failed: {repr(e)}")


async def _on_invite_delete(invite: discord.Invite) -> None:
    try:
        guild = invite.guild if isinstance(invite.guild, discord.Guild) else None
        if guild is None:
            return
        actor, reason = await _find_audit_actor(guild, "invite_delete", limit=4)
        embed = _base_embed("🔗 Invite Deleted", discord.Color.red())
        embed.add_field(name="Code", value=f"`{invite.code}`", inline=True)
        embed.add_field(name="Channel", value=_mention_channel(invite.channel), inline=True)
        embed.add_field(name="Deleted By", value=actor, inline=False)
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 300), inline=False)
        await _send(guild, embed)
    except Exception as e:
        print(f"⚠️ public_modlog_coverage invite_delete failed: {repr(e)}")


async def _on_guild_update(before: discord.Guild, after: discord.Guild) -> None:
    try:
        actor, reason = await _find_audit_actor(after, "guild_update", target_id=after.id)
        embed = _base_embed("🏠 Server Updated", discord.Color.blurple())
        embed.add_field(name="Updated By", value=actor, inline=False)
        _field_if_changed(embed, before, after, "name", "Name")
        _field_if_changed(embed, before, after, "description", "Description")
        _field_if_changed(embed, before, after, "verification_level", "Verification Level")
        _field_if_changed(embed, before, after, "default_notifications", "Default Notifications")
        _field_if_changed(embed, before, after, "explicit_content_filter", "Explicit Content Filter")
        if len(embed.fields) <= 1 and not reason:
            return
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 300), inline=False)
        await _send(after, embed)
    except Exception as e:
        print(f"⚠️ public_modlog_coverage guild_update failed: {repr(e)}")


async def _modlog_check(interaction: discord.Interaction) -> None:
    try:
        if interaction.guild is None:
            return await interaction.response.send_message("❌ This command must be used inside a server.", ephemeral=True)
        if not isinstance(interaction.user, discord.Member) or not (
            interaction.user.guild_permissions.administrator or interaction.user.guild_permissions.manage_guild
        ):
            return await interaction.response.send_message("❌ Requires Administrator or Manage Server.", ephemeral=True)

        guild = interaction.guild
        cfg = await get_guild_config(guild.id, refresh=True)
        channel = await _modlog_channel(guild)
        listeners = getattr(_BOT_REF, "extra_events", {}) or {}
        event_names = [
            "on_message_delete",
            "on_raw_message_delete",
            "on_bulk_message_delete",
            "on_raw_bulk_message_delete",
            "on_message_edit",
            "on_raw_message_edit",
            "on_guild_channel_create",
            "on_guild_channel_delete",
            "on_guild_channel_update",
            "on_guild_role_create",
            "on_guild_role_delete",
            "on_guild_role_update",
            "on_thread_create",
            "on_thread_delete",
            "on_thread_update",
            "on_invite_create",
            "on_invite_delete",
            "on_guild_update",
        ]
        registered = []
        missing = []
        for name in event_names:
            count = len(list(listeners.get(name) or [])) if isinstance(listeners, dict) else 0
            if count > 0:
                registered.append(f"✅ `{name}` ({count})")
            else:
                missing.append(f"⚠️ `{name}`")

        embed = discord.Embed(
            title="🧾 Stoney Modlog Coverage Check",
            color=discord.Color.green() if channel is not None and not missing else discord.Color.gold(),
            timestamp=_utcnow(),
        )
        embed.add_field(name="Config Source", value=f"`{getattr(cfg, 'source', 'unknown')}`", inline=False)
        embed.add_field(name="Saved Modlog", value=_mention_channel_id(getattr(cfg, "modlog_channel_id", 0)), inline=False)
        embed.add_field(name="Writable", value="✅ Yes" if channel is not None else "❌ No", inline=True)
        embed.add_field(name="Raw Fallbacks", value="✅ Enabled" if not any("raw" in item for item in missing) else "⚠️ Missing", inline=True)
        embed.add_field(name="Registered Events", value=_trim("\n".join(registered), 1000), inline=False)
        if missing:
            embed.add_field(name="Missing Events", value=_trim("\n".join(missing), 1000), inline=False)
        embed.set_footer(text="Read-only check. No server config was changed.")
        await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        try:
            await interaction.response.send_message(f"❌ Modlog check failed: `{e!r}`", ephemeral=True)
        except Exception:
            await interaction.followup.send(f"❌ Modlog check failed: `{e!r}`", ephemeral=True)


def _register_modlog_check_command() -> None:
    global _COMMAND_REGISTERED
    if _COMMAND_REGISTERED:
        return
    try:
        from .public_setup_group import stoney_group

        existing = {cmd.name for cmd in stoney_group.commands}
        if "modlog-check" not in existing:
            stoney_group.add_command(
                app_commands.Command(
                    name="modlog-check",
                    description="Check whether modlog listeners and raw fallbacks are registered.",
                    callback=_modlog_check,
                )
            )
        _COMMAND_REGISTERED = True
    except Exception as e:
        print(f"⚠️ public_modlog_coverage could not attach /stoney modlog-check: {repr(e)}")


def _add_listeners(bot: Any, pairs: Iterable[tuple[Any, str]]) -> None:
    for callback, event_name in pairs:
        try:
            bot.add_listener(callback, event_name)
        except Exception as e:
            print(f"⚠️ public_modlog_coverage could not register {event_name}: {repr(e)}")


def register_public_modlog_coverage_listeners(bot, tree) -> None:
    global _LISTENERS_REGISTERED, _BOT_REF, _BOT_ID
    _ = tree
    _BOT_REF = bot
    try:
        if getattr(bot, "user", None) is not None:
            _BOT_ID = int(bot.user.id)
    except Exception:
        _BOT_ID = None

    _register_modlog_check_command()

    if _LISTENERS_REGISTERED:
        return

    _add_listeners(
        bot,
        (
            (_on_message_delete, "on_message_delete"),
            (_on_raw_message_delete, "on_raw_message_delete"),
            (_on_bulk_message_delete, "on_bulk_message_delete"),
            (_on_raw_bulk_message_delete, "on_raw_bulk_message_delete"),
            (_on_message_edit, "on_message_edit"),
            (_on_raw_message_edit, "on_raw_message_edit"),
            (_on_guild_channel_create, "on_guild_channel_create"),
            (_on_guild_channel_delete, "on_guild_channel_delete"),
            (_on_guild_channel_update, "on_guild_channel_update"),
            (_on_guild_role_create, "on_guild_role_create"),
            (_on_guild_role_delete, "on_guild_role_delete"),
            (_on_guild_role_update, "on_guild_role_update"),
            (_on_thread_create, "on_thread_create"),
            (_on_thread_delete, "on_thread_delete"),
            (_on_thread_update, "on_thread_update"),
            (_on_invite_create, "on_invite_create"),
            (_on_invite_delete, "on_invite_delete"),
            (_on_guild_update, "on_guild_update"),
        ),
    )

    _LISTENERS_REGISTERED = True
    print("✅ public_modlog_coverage: registered supplemental modlog listeners + raw message fallbacks")


__all__ = ["register_public_modlog_coverage_listeners"]
