from __future__ import annotations

"""
Clear public moderation command: /ban_unban

Discord slash command names cannot contain `/` inside the name and must be
lowercase, so the closest clear command to "Ban/Unban" is:

/ban_unban

This replaces the confusing /mod_ban and /mod_ban_toggle local registrations.
It also performs a best-effort remote cleanup on startup so stale global
commands are deleted from Discord, not just removed from the local command tree.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

import discord
from discord import app_commands

from ..globals import now_utc
from .common import (
    _staff_check,
    RUNTIME_STATS,
    member_autocomplete,
    safe_defer,
    safe_followup,
)
from .public_mod_ban_toggle_patch import (
    _configured_modlog_channel,
    _created_line,
    _modlog_suffix,
    _resolve_ban_target,
    _target_reply_label,
    _user_line,
)

_CLEANUP_LISTENER_ATTACHED = False
_CLEANUP_RAN = False
_STALE_BAN_COMMAND_NAMES = {"mod_ban", "mod_ban_toggle"}


def _safe_text(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _utc_now() -> datetime:
    try:
        value = now_utc()
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


async def _log_ban_unban_action(
    *,
    guild: discord.Guild,
    action: str,
    actor: discord.abc.User | discord.Member,
    target: discord.abc.User | discord.Member,
    reason: Optional[str],
    extra: Optional[str] = None,
    color: Optional[discord.Color] = None,
) -> bool:
    channel = await _configured_modlog_channel(guild)
    if channel is None:
        return False

    embed = discord.Embed(
        title=f"🛡️ {action}",
        color=color or discord.Color.orange(),
        timestamp=_utc_now(),
    )
    embed.add_field(name="Target", value=_user_line(target), inline=False)
    embed.add_field(name="Moderator", value=_user_line(actor), inline=False)
    embed.add_field(name="Reason", value=_safe_text(reason, "No reason provided.")[:1024], inline=False)
    if extra:
        embed.add_field(name="Command Details", value=str(extra)[:1024], inline=False)
    embed.add_field(name="Target Account Created", value=_created_line(target), inline=False)
    embed.set_footer(text=f"Guild {guild.id} • source: /ban_unban")

    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        return True
    except Exception as e:
        try:
            print(f"⚠️ /ban_unban action modlog failed guild={guild.id} action={action}: {repr(e)}")
        except Exception:
            pass
        return False


@app_commands.describe(
    member="Mention/ID/name. For unban, paste the raw Discord user ID.",
    action="Choose Ban, Unban, or Auto toggle. Auto unbans if already banned, otherwise bans.",
    reason="Reason saved in audit log and modlog.",
    delete_message_days="Only used when banning. Deletes 0–7 days of message history.",
)
@app_commands.choices(
    action=[
        app_commands.Choice(name="Ban", value="ban"),
        app_commands.Choice(name="Unban", value="unban"),
        app_commands.Choice(name="Auto toggle", value="auto"),
    ]
)
@app_commands.autocomplete(member=member_autocomplete)
async def _ban_unban_command(
    interaction: discord.Interaction,
    member: str,
    action: app_commands.Choice[str],
    reason: Optional[str] = None,
    delete_message_days: Optional[int] = 0,
) -> None:
    if not _staff_check(interaction):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if not guild or not guild.me:
        return await safe_followup(interaction, "❌ Invalid context.", ephemeral=True)

    me = guild.me
    if not me.guild_permissions.ban_members:
        return await safe_followup(interaction, "❌ I lack **Ban Members** permission.", ephemeral=True)

    selected = str(getattr(action, "value", action) or "auto").lower()
    if selected not in {"ban", "unban", "auto"}:
        selected = "auto"

    try:
        user_id, target_member, ban_entry = await _resolve_ban_target(guild, member)
    except discord.Forbidden:
        return await safe_followup(interaction, "❌ I could not check bans. I need **Ban Members** permission.", ephemeral=True)

    if user_id <= 0:
        return await safe_followup(
            interaction,
            "❌ I could not resolve that user. For unban, paste the raw Discord user ID from the ban log.",
            ephemeral=True,
        )

    is_banned = ban_entry is not None
    should_unban = selected == "unban" or (selected == "auto" and is_banned)
    should_ban = selected == "ban" or (selected == "auto" and not is_banned)

    if should_unban:
        if not is_banned:
            return await safe_followup(interaction, f"ℹ️ `{user_id}` is not currently banned. Nothing to unban.", ephemeral=True)

        banned_user = getattr(ban_entry, "user", None) or discord.Object(id=int(user_id))
        action_reason = reason or f"Unban by {interaction.user} ({interaction.user.id})"

        try:
            await guild.unban(banned_user, reason=action_reason)
            try:
                RUNTIME_STATS["mod_actions"] = int(RUNTIME_STATS.get("mod_actions", 0)) + 1
            except Exception:
                pass
            logged = await _log_ban_unban_action(
                guild=guild,
                action="Member Unbanned",
                actor=interaction.user,
                target=banned_user,
                reason=action_reason,
                extra=f"Command: `/ban_unban`\nAction selected: `{selected}`\nResolved state before action: `banned`",
                color=discord.Color.green(),
            )
            return await safe_followup(interaction, f"✅ Unbanned {_target_reply_label(banned_user, user_id)}.{_modlog_suffix(logged)}", ephemeral=True)
        except discord.NotFound:
            return await safe_followup(interaction, f"ℹ️ `{user_id}` is not currently banned. Nothing to unban.", ephemeral=True)
        except discord.Forbidden:
            return await safe_followup(interaction, "❌ Forbidden. I need **Ban Members** and correct role/app permissions.", ephemeral=True)
        except Exception as e:
            return await safe_followup(interaction, f"❌ Error while unbanning: {e}", ephemeral=True)

    if should_ban:
        if is_banned:
            return await safe_followup(interaction, f"ℹ️ `{user_id}` is already banned. Pick **Unban** to reverse it.", ephemeral=True)

        target_for_ban: Any = target_member or discord.Object(id=int(user_id))
        try:
            if int(user_id) == int(interaction.user.id):
                return await safe_followup(interaction, "❌ You can’t ban yourself.", ephemeral=True)
            if int(user_id) == int(me.id):
                return await safe_followup(interaction, "❌ I can’t ban myself.", ephemeral=True)
        except Exception:
            pass

        try:
            if target_member is not None and me.top_role <= target_member.top_role and not me.guild_permissions.administrator:
                return await safe_followup(interaction, "❌ I can’t ban that member because of role hierarchy.", ephemeral=True)
        except Exception:
            pass

        dmd = max(0, min(7, int(delete_message_days or 0)))
        action_reason = reason or f"Ban by {interaction.user} ({interaction.user.id})"

        try:
            await guild.ban(target_for_ban, reason=action_reason, delete_message_days=dmd)
            try:
                RUNTIME_STATS["mod_actions"] = int(RUNTIME_STATS.get("mod_actions", 0)) + 1
            except Exception:
                pass
            logged = await _log_ban_unban_action(
                guild=guild,
                action="Member Banned",
                actor=interaction.user,
                target=target_for_ban,
                reason=action_reason,
                extra=(
                    f"Command: `/ban_unban`\n"
                    f"Action selected: `{selected}`\n"
                    f"Resolved state before action: `not banned`\n"
                    f"Deleted message history: `{dmd}` day(s)"
                ),
                color=discord.Color.red(),
            )
            return await safe_followup(interaction, f"🔨 Banned {_target_reply_label(target_for_ban, user_id)}.{_modlog_suffix(logged)}", ephemeral=True)
        except discord.Forbidden:
            return await safe_followup(interaction, "❌ Forbidden. I need **Ban Members** and correct role/app permissions.", ephemeral=True)
        except Exception as e:
            return await safe_followup(interaction, f"❌ Error while banning: {e}", ephemeral=True)

    return await safe_followup(interaction, "❌ Invalid /ban_unban action state.", ephemeral=True)


def _remove_existing_global_command(tree: app_commands.CommandTree, name: str) -> None:
    try:
        tree.remove_command(name, guild=None)
    except Exception:
        try:
            commands = getattr(tree, "_global_commands", None)
            if isinstance(commands, dict):
                commands.pop(name, None)
        except Exception:
            pass


async def _delete_app_command(command: Any) -> bool:
    try:
        delete = getattr(command, "delete", None)
        if callable(delete):
            await delete()
            return True
    except Exception:
        pass
    return False


async def _remote_delete_stale_ban_commands(bot: Any, tree: app_commands.CommandTree) -> None:
    """Best-effort remote delete so /mod_ban disappears instead of only being hidden locally."""
    global _CLEANUP_RAN
    if _CLEANUP_RAN:
        return
    _CLEANUP_RAN = True

    try:
        await asyncio.sleep(5)
    except Exception:
        pass

    deleted: list[str] = []
    errors: list[str] = []

    # 1) Preferred: fetch global app commands and call delete on stale names.
    try:
        commands = await tree.fetch_commands(guild=None)
        for command in commands:
            name = str(getattr(command, "name", "") or "")
            if name not in _STALE_BAN_COMMAND_NAMES:
                continue
            try:
                if await _delete_app_command(command):
                    deleted.append(name)
                    continue
            except Exception as e:
                errors.append(f"{name}: {type(e).__name__}")
    except Exception as e:
        errors.append(f"fetch_commands: {type(e).__name__}")

    # 2) Fallback: use discord.py HTTPClient delete_global_command if available.
    try:
        app_id = int(getattr(bot, "application_id", 0) or getattr(getattr(bot, "user", None), "id", 0) or 0)
        http = getattr(bot, "http", None)
        if app_id and http is not None:
            commands = await tree.fetch_commands(guild=None)
            for command in commands:
                name = str(getattr(command, "name", "") or "")
                command_id = int(getattr(command, "id", 0) or 0)
                if name not in _STALE_BAN_COMMAND_NAMES or not command_id:
                    continue
                try:
                    delete_global = getattr(http, "delete_global_command", None)
                    if callable(delete_global):
                        await delete_global(app_id, command_id)
                        deleted.append(name)
                except Exception as e:
                    errors.append(f"http:{name}: {type(e).__name__}")
    except Exception as e:
        errors.append(f"http_fallback: {type(e).__name__}")

    try:
        if deleted:
            print(f"🧹 public_ban_unban_patch deleted stale global command(s): {sorted(set(deleted))}")
        else:
            print("🧹 public_ban_unban_patch no stale global /mod_ban command found to delete")
        if errors:
            print(f"⚠️ public_ban_unban_patch stale command cleanup notes: {errors[:5]}")
    except Exception:
        pass


async def _deprecated_command_cleanup_on_ready(bot: Any, tree: app_commands.CommandTree) -> None:
    await _remote_delete_stale_ban_commands(bot, tree)


def _attach_cleanup_listener(bot: Any, tree: app_commands.CommandTree) -> None:
    global _CLEANUP_LISTENER_ATTACHED
    if _CLEANUP_LISTENER_ATTACHED:
        return
    _CLEANUP_LISTENER_ATTACHED = True

    async def _on_ready_cleanup() -> None:
        await _deprecated_command_cleanup_on_ready(bot, tree)

    try:
        bot.add_listener(_on_ready_cleanup, "on_ready")
        print("✅ public_ban_unban_patch: stale /mod_ban remote cleanup listener attached")
    except Exception as e:
        try:
            print(f"⚠️ public_ban_unban_patch failed attaching stale command cleanup listener: {e!r}")
        except Exception:
            pass


def register_public_ban_unban_patch(bot: Any, tree: app_commands.CommandTree) -> None:
    for name in ("mod_ban", "mod_ban_toggle", "ban_unban"):
        _remove_existing_global_command(tree, name)
    command = app_commands.Command(
        name="ban_unban",
        description="(Staff) Ban or unban a user with one obvious action selector.",
        callback=_ban_unban_command,
    )
    tree.add_command(command)
    _attach_cleanup_listener(bot, tree)
    try:
        print("✅ public_ban_unban_patch: replaced /mod_ban and /mod_ban_toggle with /ban_unban")
    except Exception:
        pass


__all__ = ["register_public_ban_unban_patch"]
