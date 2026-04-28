from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import discord

from ..guild_config import get_guild_config


# ============================================================
# public_modlog_coverage.py
# ------------------------------------------------------------
# Production-safe supplemental modlog listeners.
#
# This module fills the common moderation/audit gaps that are not covered by
# ticket, member-risk, voice, and quick-mod logging:
# - message delete/edit/bulk delete
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
_BOT_ID: Optional[int] = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _trim(value: Any, limit: int = 1024) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text or "None"
    return text[: max(0, limit - 1)] + "…"


def _id(value: Any) -> str:
    try:
        raw = int(getattr(value, "id", 0) or 0)
        return str(raw) if raw > 0 else "unknown"
    except Exception:
        return "unknown"


def _mention_channel(channel: Any) -> str:
    try:
        return f"{channel.mention} (`{channel.id}`)"
    except Exception:
        try:
            return f"#{getattr(channel, 'name', 'unknown')} (`{getattr(channel, 'id', 'unknown')}`)"
        except Exception:
            return "Unknown channel"


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


def _guild_of(obj: Any) -> Optional[discord.Guild]:
    try:
        guild = getattr(obj, "guild", None)
        return guild if isinstance(guild, discord.Guild) else None
    except Exception:
        return None


def _is_own_bot_user(user: Any) -> bool:
    try:
        if _BOT_ID is None:
            return False
        return int(getattr(user, "id", 0) or 0) == int(_BOT_ID)
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

        try:
            print(f"⚠️ public_modlog_coverage modlog unavailable/wrong perms guild={guild.id} channel={channel_id}")
        except Exception:
            pass
        return None
    except Exception as e:
        try:
            print(f"⚠️ public_modlog_coverage failed resolving modlog guild={getattr(guild, 'id', 'unknown')}: {repr(e)}")
        except Exception:
            pass
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
    limit: int = 6,
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
        try:
            print(f"⚠️ public_modlog_coverage cannot send guild={guild.id} channel={channel.id}")
        except Exception:
            pass
    except Exception as e:
        try:
            print(f"⚠️ public_modlog_coverage send failed guild={guild.id}: {repr(e)}")
        except Exception:
            pass


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


async def _on_message_delete(message: discord.Message) -> None:
    try:
        guild = message.guild
        if guild is None or _is_own_bot_user(message.author):
            return
        actor, reason = await _find_audit_actor(guild, "message_delete", target_id=getattr(message.author, "id", None))
        embed = _base_embed("🗑️ Message Deleted", discord.Color.orange())
        embed.add_field(name="Author", value=_user_line(message.author), inline=False)
        embed.add_field(name="Channel", value=_mention_channel(message.channel), inline=False)
        embed.add_field(name="Deleted By", value=actor, inline=False)
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


async def _on_bulk_message_delete(messages: list[discord.Message]) -> None:
    try:
        if not messages:
            return
        first = messages[0]
        guild = first.guild
        if guild is None:
            return
        actor, reason = await _find_audit_actor(guild, "message_bulk_delete", target_id=getattr(first.channel, "id", None))
        authors: dict[str, int] = {}
        for msg in messages:
            try:
                if _is_own_bot_user(msg.author):
                    continue
                key = _user_line(msg.author)
                authors[key] = authors.get(key, 0) + 1
            except Exception:
                continue
        top_authors = sorted(authors.items(), key=lambda kv: kv[1], reverse=True)[:8]
        embed = _base_embed("🧹 Bulk Messages Deleted", discord.Color.orange())
        embed.add_field(name="Channel", value=_mention_channel(first.channel), inline=False)
        embed.add_field(name="Count", value=f"`{len(messages)}` message(s)", inline=True)
        embed.add_field(name="Deleted By", value=actor, inline=True)
        if top_authors:
            embed.add_field(name="Top Authors", value="\n".join(f"{name}: `{count}`" for name, count in top_authors), inline=False)
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 300), inline=False)
        await _send(guild, embed)
    except Exception as e:
        print(f"⚠️ public_modlog_coverage on_bulk_message_delete failed: {repr(e)}")


async def _on_message_edit(before: discord.Message, after: discord.Message) -> None:
    try:
        guild = before.guild or after.guild
        if guild is None or _is_own_bot_user(before.author):
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


def _add_listeners(bot: Any, pairs: Iterable[tuple[Any, str]]) -> None:
    for callback, event_name in pairs:
        try:
            bot.add_listener(callback, event_name)
        except Exception as e:
            print(f"⚠️ public_modlog_coverage could not register {event_name}: {repr(e)}")


def register_public_modlog_coverage_listeners(bot, tree) -> None:
    global _LISTENERS_REGISTERED, _BOT_ID
    _ = tree
    if _LISTENERS_REGISTERED:
        return

    try:
        if getattr(bot, "user", None) is not None:
            _BOT_ID = int(bot.user.id)
    except Exception:
        _BOT_ID = None

    _add_listeners(
        bot,
        (
            (_on_message_delete, "on_message_delete"),
            (_on_bulk_message_delete, "on_bulk_message_delete"),
            (_on_message_edit, "on_message_edit"),
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
    try:
        print("✅ public_modlog_coverage: registered supplemental modlog listeners")
    except Exception:
        pass


__all__ = ["register_public_modlog_coverage_listeners"]
