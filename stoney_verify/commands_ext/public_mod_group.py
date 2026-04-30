from __future__ import annotations

"""
Public grouped moderation commands.

Goal: avoid burning one top-level slash command per moderation action while
keeping names clear enough for public servers.

Public command surface becomes:
- /mod kick
- /mod timeout
- /mod ban-unban
- /mod debug-intents

Old top-level names are removed locally and best-effort deleted remotely:
/mod_kick, /mod_timeout, /mod_ban, /kick_member, /timeout_member, /ban_unban,
/debug_intents, etc.
"""

import asyncio
from datetime import timedelta
from typing import Any, Optional

import discord
from discord import app_commands

from ..globals import MOD_TIMEOUT_MINUTES, now_utc
from .common import (
    _staff_check,
    RUNTIME_STATS,
    member_autocomplete,
    require_target_member,
    safe_defer,
    safe_followup,
)
from .moderation import (
    _log_slash_mod_action,
    _modlog_suffix,
    _resolve_ban_toggle_target,
    _staff_confirmed_ban_member,
    _staff_confirmed_extra,
    _staff_confirmed_kick,
    _target_reply_label,
)


_REGISTERED = False
_CLEANUP_ATTACHED = False
_CLEANUP_RAN = False

_STALE_TOP_LEVEL_MOD_NAMES: set[str] = {
    "mod_ban",
    "mod_ban_toggle",
    "mod_kick",
    "mod_timeout",
    "kick_member",
    "timeout_member",
    "ban_unban",
    "debug_intents",
}

mod_group = app_commands.Group(
    name="mod",
    description="Staff moderation tools.",
)


def _safe_action_value(value: Any, default: str = "auto") -> str:
    try:
        raw = getattr(value, "value", value)
        text = str(raw or default).strip().lower()
        return text if text in {"auto", "ban", "unban"} else default
    except Exception:
        return default


@mod_group.command(name="kick", description="Kick a member from the server.")
@app_commands.describe(
    member="Mention, ID, username, or display name of the member to kick",
    reason="Reason saved to the audit log and modlog",
)
@app_commands.autocomplete(member=member_autocomplete)
async def mod_kick_group_command(
    interaction: discord.Interaction,
    member: str,
    reason: Optional[str] = None,
) -> None:
    if not _staff_check(interaction):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if not guild or not guild.me:
        return await safe_followup(interaction, "❌ Invalid context.", ephemeral=True)

    target = await require_target_member(interaction, member)
    if target is None:
        return

    me = guild.me
    if not me.guild_permissions.kick_members:
        return await safe_followup(interaction, "❌ I lack **Kick Members** permission.", ephemeral=True)

    try:
        if int(target.id) == int(interaction.user.id):
            return await safe_followup(interaction, "❌ You can’t kick yourself.", ephemeral=True)
        if int(target.id) == int(me.id):
            return await safe_followup(interaction, "❌ I can’t kick myself.", ephemeral=True)
    except Exception:
        pass

    try:
        if me.top_role <= target.top_role and not me.guild_permissions.administrator:
            return await safe_followup(interaction, "❌ I can’t kick that member because of role hierarchy.", ephemeral=True)
    except Exception:
        pass

    action_reason = reason or f"Kick by {interaction.user} ({interaction.user.id})"

    try:
        await _staff_confirmed_kick(guild=guild, target=target, reason=action_reason)
        try:
            RUNTIME_STATS["mod_actions"] = int(RUNTIME_STATS.get("mod_actions", 0)) + 1
        except Exception:
            pass

        logged = await _log_slash_mod_action(
            guild=guild,
            action="Member Kicked",
            actor=interaction.user,
            target=target,
            reason=action_reason,
            extra=_staff_confirmed_extra("mod kick"),
            color=discord.Color.orange(),
        )
        return await safe_followup(interaction, f"👢 Kicked {target.mention}.{_modlog_suffix(logged)}", ephemeral=True)
    except discord.Forbidden:
        return await safe_followup(interaction, "❌ Forbidden (permissions/hierarchy).", ephemeral=True)
    except Exception as e:
        return await safe_followup(interaction, f"❌ Error: {e}", ephemeral=True)


@mod_group.command(name="timeout", description="Timeout a member for a set number of minutes.")
@app_commands.describe(
    member="Mention, ID, username, or display name of the member to timeout",
    minutes="How many minutes to timeout this member",
    reason="Reason saved to the audit log and modlog",
)
@app_commands.autocomplete(member=member_autocomplete)
async def mod_timeout_group_command(
    interaction: discord.Interaction,
    member: str,
    minutes: Optional[int] = None,
    reason: Optional[str] = None,
) -> None:
    if not _staff_check(interaction):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if not guild or not guild.me:
        return await safe_followup(interaction, "❌ Invalid context.", ephemeral=True)

    target = await require_target_member(interaction, member)
    if target is None:
        return

    me = guild.me
    if not me.guild_permissions.moderate_members:
        return await safe_followup(interaction, "❌ I lack **Moderate Members** permission.", ephemeral=True)

    mins = max(1, min(60 * 24 * 28, int(minutes or MOD_TIMEOUT_MINUTES)))

    try:
        if int(target.id) == int(interaction.user.id):
            return await safe_followup(interaction, "❌ You can’t timeout yourself.", ephemeral=True)
        if int(target.id) == int(me.id):
            return await safe_followup(interaction, "❌ I can’t timeout myself.", ephemeral=True)
    except Exception:
        pass

    try:
        if me.top_role <= target.top_role and not me.guild_permissions.administrator:
            return await safe_followup(interaction, "❌ I can’t timeout that member because of role hierarchy.", ephemeral=True)
    except Exception:
        pass

    until = now_utc() + timedelta(minutes=mins)
    action_reason = reason or f"Timeout by {interaction.user} ({interaction.user.id})"

    try:
        await target.timeout(until, reason=action_reason)
        try:
            RUNTIME_STATS["mod_actions"] = int(RUNTIME_STATS.get("mod_actions", 0)) + 1
        except Exception:
            pass

        try:
            until_ts = int(until.timestamp())
            detail = f"Duration: `{mins}` minute(s)\nExpires: <t:{until_ts}:F> • <t:{until_ts}:R>"
        except Exception:
            detail = f"Duration: `{mins}` minute(s)"

        logged = await _log_slash_mod_action(
            guild=guild,
            action="Member Timed Out",
            actor=interaction.user,
            target=target,
            reason=action_reason,
            extra=f"Command: `/mod timeout`\n{detail}",
            color=discord.Color.gold(),
        )
        return await safe_followup(interaction, f"⏳ Timed out {target.mention} for {mins} minutes.{_modlog_suffix(logged)}", ephemeral=True)
    except discord.Forbidden:
        return await safe_followup(interaction, "❌ Forbidden (permissions/hierarchy).", ephemeral=True)
    except Exception as e:
        return await safe_followup(interaction, f"❌ Error: {e}", ephemeral=True)


@mod_group.command(name="ban-unban", description="Ban or unban a user with one action selector.")
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
async def mod_ban_unban_group_command(
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

    selected = _safe_action_value(action, "auto")

    try:
        user_id, target_member, ban_entry = await _resolve_ban_toggle_target(guild, member)
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
                extra=f"Command: `/mod ban-unban`\nAction selected: `{selected}`\nResolved state before action: `banned`",
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
            if isinstance(target_for_ban, discord.Member):
                await _staff_confirmed_ban_member(
                    guild=guild,
                    target=target_for_ban,
                    reason=action_reason,
                    delete_message_days=dmd,
                )
            else:
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
                    f"Command: `/mod ban-unban`\n"
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

    return await safe_followup(interaction, "❌ Invalid /mod ban-unban action state.", ephemeral=True)


@mod_group.command(name="debug-intents", description="Check bot intents and member visibility.")
async def mod_debug_intents_group_command(interaction: discord.Interaction) -> None:
    if not _staff_check(interaction):
        return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await safe_followup(interaction, "❌ Invalid context.", ephemeral=True)

    # Import bot lazily so this grouped module does not pull globals too early.
    from ..globals import bot

    lines = [
        f"**Bot User:** {bot.user}",
        f"**Guild ID:** {guild.id}",
        f"**Guild Name:** {guild.name}",
        "**Intents:**",
        f"- Guilds: {bot.intents.guilds}",
        f"- Members: {bot.intents.members}",
        f"- Presence: {bot.intents.presences}",
        f"- Message Content: {bot.intents.message_content}",
    ]

    try:
        me = guild.me
        if me:
            lines.append(f"**Bot Member:** {me} (roles: {len(me.roles)})")
        else:
            lines.append("**Bot Member:** not resolved")
        members = [m for m in guild.members[:5]]
        lines.append("**First few members in cache:** " + (", ".join(str(m) for m in members) if members else "none"))
    except Exception as e:
        lines.append(f"❌ Error accessing members: {e}")

    await safe_followup(interaction, "\n".join(lines), ephemeral=True)


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


async def _remote_delete_stale_top_level_mod_commands(bot: Any, tree: app_commands.CommandTree) -> None:
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

    try:
        commands = await tree.fetch_commands(guild=None)
        for command in commands:
            name = str(getattr(command, "name", "") or "")
            if name not in _STALE_TOP_LEVEL_MOD_NAMES:
                continue
            try:
                if await _delete_app_command(command):
                    deleted.append(name)
            except Exception as e:
                errors.append(f"{name}: {type(e).__name__}")
    except Exception as e:
        errors.append(f"fetch_commands: {type(e).__name__}")

    try:
        if deleted:
            print(f"🧹 public_mod_group deleted stale top-level moderation command(s): {sorted(set(deleted))}")
        else:
            print("🧹 public_mod_group no stale top-level moderation commands found to delete")
        if errors:
            print(f"⚠️ public_mod_group stale cleanup notes: {errors[:5]}")
    except Exception:
        pass


def _attach_cleanup_listener(bot: Any, tree: app_commands.CommandTree) -> None:
    global _CLEANUP_ATTACHED
    if _CLEANUP_ATTACHED:
        return
    _CLEANUP_ATTACHED = True

    async def _on_ready_cleanup() -> None:
        await _remote_delete_stale_top_level_mod_commands(bot, tree)

    try:
        bot.add_listener(_on_ready_cleanup, "on_ready")
        print("✅ public_mod_group: stale top-level moderation cleanup listener attached")
    except Exception as e:
        try:
            print(f"⚠️ public_mod_group failed attaching cleanup listener: {e!r}")
        except Exception:
            pass


def register_public_mod_group_commands(bot: Any, tree: app_commands.CommandTree) -> None:
    global _REGISTERED
    if _REGISTERED:
        return

    for stale_name in _STALE_TOP_LEVEL_MOD_NAMES:
        _remove_existing_global_command(tree, stale_name)

    _remove_existing_global_command(tree, "mod")
    tree.add_command(mod_group)
    _attach_cleanup_listener(bot, tree)
    _REGISTERED = True

    try:
        print("✅ public_mod_group: registered grouped /mod moderation commands")
    except Exception:
        pass


__all__ = ["register_public_mod_group_commands"]
