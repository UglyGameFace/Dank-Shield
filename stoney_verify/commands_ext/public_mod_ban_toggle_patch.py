from __future__ import annotations

"""
Replace the old public /mod_ban command with a clearer /mod_ban_toggle command.

Why:
Discord mobile can hide optional choices and staff can miss that unban exists.
The command name now makes the behavior obvious:

/mod_ban_toggle member:<id or member> action:Ban|Unban|Auto toggle

The old /mod_ban command is removed from the local command tree so the next
slash sync deletes it globally instead of leaving two confusing commands.
"""

from datetime import datetime, timezone
from typing import Any, Optional

import discord
from discord import app_commands

from ..globals import now_utc
from ..guild_config import get_guild_config
from .common import (
    _staff_check,
    RUNTIME_STATS,
    member_autocomplete,
    parse_member_id_from_target,
    resolve_member_from_target,
    safe_defer,
    safe_followup,
)


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


def _user_line(user: discord.abc.User | discord.Member | None) -> str:
    if user is None:
        return "Unknown"
    try:
        mention = getattr(user, "mention", None)
        uid = getattr(user, "id", None)
        name = (
            getattr(user, "display_name", None)
            or getattr(user, "global_name", None)
            or getattr(user, "name", None)
            or str(user)
        )
        if mention and uid:
            return f"{mention} `{uid}`"
        if uid:
            return f"{name} `{uid}`"
        return str(name)
    except Exception:
        return "Unknown"


def _created_line(user: discord.abc.User | discord.Member | None) -> str:
    try:
        created_at = getattr(user, "created_at", None)
        if not created_at:
            return "Unknown"
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        ts = int(created_at.astimezone(timezone.utc).timestamp())
        return f"<t:{ts}:F> • <t:{ts}:R>"
    except Exception:
        return "Unknown"


def _target_reply_label(user: Any, fallback_id: int = 0) -> str:
    try:
        mention = getattr(user, "mention", None)
        if mention:
            return str(mention)
    except Exception:
        pass
    try:
        uid = int(getattr(user, "id", None) or fallback_id or 0)
        if uid > 0:
            return f"`{uid}`"
    except Exception:
        pass
    return "that user"


async def _configured_modlog_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    try:
        cfg = await get_guild_config(guild.id)
        channel_id = int(getattr(cfg, "modlog_channel_id", 0) or 0)
        if channel_id <= 0:
            return None
        channel = guild.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return None
        me = guild.me
        if me is not None:
            perms = channel.permissions_for(me)
            if not perms.view_channel or not perms.send_messages or not perms.embed_links:
                return None
        return channel
    except Exception:
        return None


async def _log_slash_mod_action(
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
    embed.set_footer(text=f"Guild {guild.id} • source: /mod_ban_toggle")

    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        return True
    except Exception as e:
        try:
            print(f"⚠️ /mod_ban_toggle action modlog failed guild={guild.id} action={action}: {repr(e)}")
        except Exception:
            pass
        return False


def _modlog_suffix(logged: bool) -> str:
    return "" if logged else "\n⚠️ Action succeeded, but I could not post to the configured modlog."


async def _fetch_ban_entry_by_id(guild: discord.Guild, user_id: int) -> Optional[Any]:
    try:
        return await guild.fetch_ban(discord.Object(id=int(user_id)))
    except discord.NotFound:
        return None
    except discord.Forbidden:
        raise
    except Exception:
        return None


async def _resolve_ban_target(guild: discord.Guild, raw_target: str) -> tuple[int, Optional[discord.Member], Optional[Any]]:
    user_id = parse_member_id_from_target(raw_target)
    member: Optional[discord.Member] = None

    if user_id > 0:
        try:
            member = guild.get_member(int(user_id))
        except Exception:
            member = None
        if member is None:
            try:
                member = await guild.fetch_member(int(user_id))
            except Exception:
                member = None
    else:
        member = await resolve_member_from_target(guild, raw_target)
        if member is not None:
            try:
                user_id = int(member.id)
            except Exception:
                user_id = 0

    ban_entry = await _fetch_ban_entry_by_id(guild, int(user_id)) if user_id > 0 else None
    return int(user_id or 0), member, ban_entry


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
async def _mod_ban_toggle_command(
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
            logged = await _log_slash_mod_action(
                guild=guild,
                action="Member Unbanned",
                actor=interaction.user,
                target=banned_user,
                reason=action_reason,
                extra=f"Command: `/mod_ban_toggle`\nAction selected: `{selected}`\nResolved state before action: `banned`",
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
            logged = await _log_slash_mod_action(
                guild=guild,
                action="Member Banned",
                actor=interaction.user,
                target=target_for_ban,
                reason=action_reason,
                extra=(
                    f"Command: `/mod_ban_toggle`\n"
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

    return await safe_followup(interaction, "❌ Invalid /mod_ban_toggle action state.", ephemeral=True)


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


def register_public_mod_ban_toggle_patch(bot: Any, tree: app_commands.CommandTree) -> None:
    _ = bot
    _remove_existing_global_command(tree, "mod_ban")
    _remove_existing_global_command(tree, "mod_ban_toggle")
    command = app_commands.Command(
        name="mod_ban_toggle",
        description="(Staff) Ban or unban a user. The action selector makes the toggle obvious.",
        callback=_mod_ban_toggle_command,
    )
    tree.add_command(command)
    try:
        print("✅ public_mod_ban_toggle_patch: replaced /mod_ban with /mod_ban_toggle explicit Ban/Unban action selector")
    except Exception:
        pass


__all__ = ["register_public_mod_ban_toggle_patch"]
