from __future__ import annotations

"""Add missing modlog coverage without duplicating existing core listeners.

Existing systems already cover:
- joins/leaves/bans/kicks/member updates/voice state in events.py/modlog.py
- messages/channels/roles/threads/invites/server update in public_modlog_coverage.py

This guard only adds missing public modlog pieces and exposes /dank modlog.
"""

from typing import Any, Iterable, Optional

import discord

_PATCHED = False
_BOT: Any = None


def _trim(value: Any, limit: int = 1024) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: max(0, limit - 1)] + "…"


def _user_line(user: Any) -> str:
    try:
        uid = int(getattr(user, "id", 0) or 0)
        return f"{user} (`{uid}`)" if uid else str(user or "Unknown")
    except Exception:
        return "Unknown"


def _mention_channel(channel: Any) -> str:
    try:
        return f"{channel.mention} (`{channel.id}`)"
    except Exception:
        return f"`{getattr(channel, 'name', 'unknown')}` (`{getattr(channel, 'id', 'unknown')}`)"


def _base(title: str, color: discord.Color) -> discord.Embed:
    return discord.Embed(title=title, color=color, timestamp=discord.utils.utcnow())


async def _modlog_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    try:
        from stoney_verify.commands_ext.public_modlog_coverage import _modlog_channel as existing

        return await existing(guild)
    except Exception:
        return None


async def _send(guild: discord.Guild, embed: discord.Embed) -> None:
    channel = await _modlog_channel(guild)
    if channel is None:
        return
    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
    except Exception as exc:
        try:
            print(f"⚠️ modlog_probot_parity send failed guild={guild.id}: {type(exc).__name__}: {exc}")
        except Exception:
            pass


async def _audit_actor(guild: discord.Guild, action_name: str, *, target_id: Optional[int] = None, limit: int = 6) -> tuple[str, str]:
    try:
        from stoney_verify.commands_ext.public_modlog_coverage import _find_audit_actor

        return await _find_audit_actor(guild, action_name, target_id=target_id, limit=limit)
    except Exception:
        return ("Unknown", "")


async def _on_member_unban(guild: discord.Guild, user: discord.User) -> None:
    try:
        actor, reason = await _audit_actor(guild, "unban", target_id=int(getattr(user, "id", 0) or 0), limit=8)
        embed = _base("🔓 Member Unbanned", discord.Color.green())
        embed.add_field(name="User", value=_user_line(user), inline=False)
        embed.add_field(name="Unbanned By", value=actor, inline=False)
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 300), inline=False)
        await _send(guild, embed)
    except Exception as exc:
        print(f"⚠️ modlog_probot_parity member_unban failed: {exc!r}")


async def _on_webhooks_update(channel: discord.abc.GuildChannel) -> None:
    try:
        guild = getattr(channel, "guild", None)
        if not isinstance(guild, discord.Guild):
            return
        actor, reason = await _audit_actor(guild, "webhook_update", target_id=int(getattr(channel, "id", 0) or 0), limit=8)
        embed = _base("🪝 Webhooks Updated", discord.Color.blurple())
        embed.add_field(name="Channel", value=_mention_channel(channel), inline=False)
        embed.add_field(name="Updated By", value=actor, inline=False)
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 300), inline=False)
        await _send(guild, embed)
    except Exception as exc:
        print(f"⚠️ modlog_probot_parity webhooks_update failed: {exc!r}")


def _named_diff(before: Iterable[Any], after: Iterable[Any]) -> tuple[list[str], list[str]]:
    b = {int(getattr(x, "id", 0) or 0): x for x in list(before or []) if int(getattr(x, "id", 0) or 0) > 0}
    a = {int(getattr(x, "id", 0) or 0): x for x in list(after or []) if int(getattr(x, "id", 0) or 0) > 0}
    added = [f"{getattr(a[i], 'name', 'unknown')} (`{i}`)" for i in sorted(set(a) - set(b))]
    removed = [f"{getattr(b[i], 'name', 'unknown')} (`{i}`)" for i in sorted(set(b) - set(a))]
    return added, removed


async def _on_guild_emojis_update(guild: discord.Guild, before: list[discord.Emoji], after: list[discord.Emoji]) -> None:
    try:
        added, removed = _named_diff(before, after)
        if not added and not removed:
            return
        actor, reason = await _audit_actor(guild, "emoji_update", limit=8)
        embed = _base("😀 Emojis Updated", discord.Color.blurple())
        embed.add_field(name="Updated By", value=actor, inline=False)
        if added:
            embed.add_field(name="Added", value=_trim("\n".join(added), 1000), inline=False)
        if removed:
            embed.add_field(name="Removed", value=_trim("\n".join(removed), 1000), inline=False)
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 300), inline=False)
        await _send(guild, embed)
    except Exception as exc:
        print(f"⚠️ modlog_probot_parity emojis_update failed: {exc!r}")


async def _on_guild_stickers_update(guild: discord.Guild, before: list[discord.GuildSticker], after: list[discord.GuildSticker]) -> None:
    try:
        added, removed = _named_diff(before, after)
        if not added and not removed:
            return
        actor, reason = await _audit_actor(guild, "sticker_update", limit=8)
        embed = _base("🏷️ Stickers Updated", discord.Color.blurple())
        embed.add_field(name="Updated By", value=actor, inline=False)
        if added:
            embed.add_field(name="Added", value=_trim("\n".join(added), 1000), inline=False)
        if removed:
            embed.add_field(name="Removed", value=_trim("\n".join(removed), 1000), inline=False)
        if reason:
            embed.add_field(name="Audit Reason", value=_trim(reason, 300), inline=False)
        await _send(guild, embed)
    except Exception as exc:
        print(f"⚠️ modlog_probot_parity stickers_update failed: {exc!r}")


def _listener_count(bot: Any, event_name: str) -> int:
    try:
        return len(list((getattr(bot, "extra_events", {}) or {}).get(event_name) or []))
    except Exception:
        return 0


def _add_missing_listener(bot: Any, callback: Any, event_name: str) -> None:
    try:
        # Avoid adding duplicate extra listeners if this guard reloads. Core @bot.event
        # handlers are allowed to coexist; this only dedupes our extra-event hooks.
        existing = list((getattr(bot, "extra_events", {}) or {}).get(event_name) or [])
        if any(getattr(cb, "__name__", "") == getattr(callback, "__name__", "") for cb in existing):
            return
        bot.add_listener(callback, event_name)
    except Exception as exc:
        print(f"⚠️ modlog_probot_parity could not register {event_name}: {exc!r}")


def apply(bot: Any = None) -> bool:
    global _PATCHED, _BOT
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
    _BOT = bot
    try:
        # Slash command surface: /dank modlog set-channel/health/test.
        import stoney_verify.commands_ext as commands_ext

        allowed = set(getattr(commands_ext, "_ALLOWED_DANK_CHILDREN", set()) or set())
        allowed.add("modlog")
        commands_ext._ALLOWED_DANK_CHILDREN = allowed
        from stoney_verify.commands_ext import public_modlog_group

        register = getattr(public_modlog_group, "register_public_modlog_group_commands", None)
        if callable(register):
            register(bot, getattr(bot, "tree", None))

        # Add only the missing families. Do not duplicate joins/leaves/bans/member update/voice.
        _add_missing_listener(bot, _on_member_unban, "on_member_unban")
        _add_missing_listener(bot, _on_webhooks_update, "on_webhooks_update")
        _add_missing_listener(bot, _on_guild_emojis_update, "on_guild_emojis_update")
        _add_missing_listener(bot, _on_guild_stickers_update, "on_guild_stickers_update")
        _PATCHED = True
        print(
            "✅ modlog_probot_parity_guard active; /dank modlog exposed and missing log families attached "
            f"unban={_listener_count(bot, 'on_member_unban')} webhooks={_listener_count(bot, 'on_webhooks_update')} "
            f"emojis={_listener_count(bot, 'on_guild_emojis_update')} stickers={_listener_count(bot, 'on_guild_stickers_update')}"
        )
        return True
    except Exception as exc:
        print(f"⚠️ modlog_probot_parity_guard failed: {type(exc).__name__}: {exc}")
        return False


apply()

__all__ = ["apply"]
