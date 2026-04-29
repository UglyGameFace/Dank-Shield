from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403
from ..globals import now_utc
from ..guild_config import get_guild_config
from ..members_new.join_removal_safety import block_or_run_bot_removal

from .common import (
    _staff_check,
    RUNTIME_STATS,
    member_autocomplete,
    parse_member_id_from_target,
    require_target_member,
    resolve_member_from_target,
    safe_defer,
    safe_followup,
)


# ============================================================
# Per-guild slash moderation logging
# ------------------------------------------------------------
# Discord audit-log listeners are useful, but moderation actions triggered by
# this bot should log themselves immediately to the guild's configured modlog.
# This gives staff TicketTool-style confidence without relying on delayed audit
# log lookups or any beta-server env IDs.
#
# Fresh-join safety note:
# - automatic/bot-driven fresh join kick/ban remains blocked by default
# - slash moderation commands are explicit staff-confirmed actions, so they call
#   the native fresh-join helper with staff_confirmed=True and log that context
# ============================================================


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


async def _fetch_ban_entry_by_id(guild: discord.Guild, user_id: int) -> Optional[Any]:
    """Fetch a ban entry by raw user ID. Returns None when not banned."""
    try:
        snowflake = discord.Object(id=int(user_id))
        return await guild.fetch_ban(snowflake)
    except discord.NotFound:
        return None
    except discord.Forbidden:
        raise
    except Exception:
        return None


async def _resolve_ban_toggle_target(
    guild: discord.Guild,
    raw_target: str,
) -> tuple[int, Optional[discord.Member], Optional[Any]]:
    """
    Resolve a target for /ban_unban toggle mode.

    Returns:
        (user_id, current_member_if_present, ban_entry_if_currently_banned)

    For unbanning, a raw ID or mention is the safest because banned users are no
    longer guild members and cannot always be resolved by display name.
    """
    user_id = parse_member_id_from_target(raw_target)
    target_member: Optional[discord.Member] = None

    if user_id > 0:
        try:
            target_member = guild.get_member(int(user_id))
        except Exception:
            target_member = None
        if target_member is None:
            try:
                target_member = await guild.fetch_member(int(user_id))
            except Exception:
                target_member = None
    else:
        target_member = await resolve_member_from_target(guild, raw_target)
        if target_member is not None:
            try:
                user_id = int(target_member.id)
            except Exception:
                user_id = 0

    ban_entry = await _fetch_ban_entry_by_id(guild, user_id) if user_id > 0 else None
    return int(user_id or 0), target_member, ban_entry


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

    action_clean = _safe_text(action, "Moderation Action")
    embed = discord.Embed(
        title=f"🛡️ {action_clean}",
        color=color or discord.Color.orange(),
        timestamp=_utc_now(),
    )
    embed.add_field(name="Target", value=_user_line(target), inline=False)
    embed.add_field(name="Moderator", value=_user_line(actor), inline=False)
    embed.add_field(name="Reason", value=_safe_text(reason, "No reason provided.")[:1024], inline=False)
    if extra:
        embed.add_field(name="Details", value=str(extra)[:1024], inline=False)
    embed.add_field(name="Target Account Created", value=_created_line(target), inline=False)
    embed.set_footer(text=f"Guild {guild.id} • source: slash command")

    try:
        await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        return True
    except Exception as e:
        try:
            print(f"⚠️ slash moderation modlog failed guild={guild.id} action={action_clean}: {repr(e)}")
        except Exception:
            pass
        return False


def _modlog_suffix(logged: bool) -> str:
    return "" if logged else "\n⚠️ Action succeeded, but I could not post to the configured modlog."


def _staff_confirmed_extra(command_name: str, extra: Optional[str] = None) -> str:
    base = f"Command: `/{command_name}`\nFresh-join context: `staff-confirmed`"
    if extra:
        return f"{base}\n{extra}"
    return base


async def _staff_confirmed_kick(
    *,
    guild: discord.Guild,
    target: discord.Member,
    reason: str,
) -> None:
    await block_or_run_bot_removal(
        action="kick",
        guild=guild,
        member=target,
        reason=reason,
        staff_confirmed=True,
        runner=lambda: guild.kick(target, reason=reason),
    )


async def _staff_confirmed_ban_member(
    *,
    guild: discord.Guild,
    target: discord.Member,
    reason: str,
    delete_message_days: int,
) -> None:
    await block_or_run_bot_removal(
        action="ban",
        guild=guild,
        member=target,
        reason=reason,
        staff_confirmed=True,
        runner=lambda: guild.ban(
            target,
            reason=reason,
            delete_message_days=delete_message_days,
        ),
    )


def register_moderation_commands(bot, tree) -> None:
    # ============================================================
    # /mod_kick
    # ============================================================
    @tree.command(
        name="mod_kick",
        description="(Staff) Kick a member.",
    )
    @app_commands.describe(
        member="Mention, ID, username, or display name of the member to kick",
        reason="Reason (optional)",
    )
    @app_commands.autocomplete(member=member_autocomplete)
    async def mod_kick_slash(
        interaction: discord.Interaction,
        member: str,
        reason: Optional[str] = None,
    ):
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
            return await safe_followup(
                interaction,
                "❌ I lack **Kick Members** permission.",
                ephemeral=True,
            )

        try:
            if int(target.id) == int(interaction.user.id):
                return await safe_followup(
                    interaction,
                    "❌ You can’t use this command on yourself.",
                    ephemeral=True,
                )
        except Exception:
            pass

        try:
            if int(target.id) == int(me.id):
                return await safe_followup(
                    interaction,
                    "❌ I can’t kick myself.",
                    ephemeral=True,
                )
        except Exception:
            pass

        try:
            if me.top_role <= target.top_role and not me.guild_permissions.administrator:
                return await safe_followup(
                    interaction,
                    "❌ I can’t kick that member (role hierarchy).",
                    ephemeral=True,
                )
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
                extra=_staff_confirmed_extra("mod_kick"),
                color=discord.Color.orange(),
            )

            return await safe_followup(
                interaction,
                f"👢 Kicked {target.mention}.{_modlog_suffix(logged)}",
                ephemeral=True,
            )

        except discord.Forbidden:
            return await safe_followup(
                interaction,
                "❌ Forbidden (permissions/hierarchy).",
                ephemeral=True,
            )
        except Exception as e:
            return await safe_followup(
                interaction,
                f"❌ Error: {e}",
                ephemeral=True,
            )

    # ============================================================
    # /mod_ban toggle
    # ------------------------------------------------------------
    # Runtime public patch exposes this as /ban_unban and removes stale /mod_ban.
    # Same handler supports ban + unban:
    # - mode=auto/default: banned user -> unban, unbanned user -> ban
    # - mode=ban: force ban
    # - mode=unban: force unban
    # ============================================================
    @tree.command(
        name="mod_ban",
        description="(Staff) Toggle ban/unban for a user.",
    )
    @app_commands.describe(
        member="Mention, ID, username, or display name. For unban, raw user ID is safest.",
        reason="Reason (optional)",
        delete_message_days="Delete message days when banning (0-7)",
        mode="Auto toggles: banned users are unbanned, unbanned users are banned.",
    )
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Auto toggle", value="auto"),
            app_commands.Choice(name="Ban", value="ban"),
            app_commands.Choice(name="Unban", value="unban"),
        ]
    )
    @app_commands.autocomplete(member=member_autocomplete)
    async def mod_ban_slash(
        interaction: discord.Interaction,
        member: str,
        reason: Optional[str] = None,
        delete_message_days: Optional[int] = 0,
        mode: Optional[str] = "auto",
    ):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        await safe_defer(interaction, ephemeral=True)

        guild = interaction.guild
        if not guild or not guild.me:
            return await safe_followup(interaction, "❌ Invalid context.", ephemeral=True)

        me = guild.me
        if not me.guild_permissions.ban_members:
            return await safe_followup(
                interaction,
                "❌ I lack **Ban Members** permission.",
                ephemeral=True,
            )

        action_mode = _safe_text(mode, "auto").lower()
        if action_mode not in {"auto", "ban", "unban"}:
            action_mode = "auto"

        try:
            user_id, target_member, ban_entry = await _resolve_ban_toggle_target(guild, member)
        except discord.Forbidden:
            return await safe_followup(
                interaction,
                "❌ I could not check the ban list. I need **Ban Members** permission.",
                ephemeral=True,
            )

        if user_id <= 0:
            return await safe_followup(
                interaction,
                "❌ I could not resolve that user. For unban/toggle, use the raw Discord user ID when the user is already banned.",
                ephemeral=True,
            )

        is_banned = ban_entry is not None
        should_unban = action_mode == "unban" or (action_mode == "auto" and is_banned)
        should_ban = action_mode == "ban" or (action_mode == "auto" and not is_banned)

        if should_unban:
            if not is_banned:
                return await safe_followup(
                    interaction,
                    f"ℹ️ `{user_id}` is not currently banned. Nothing to unban.",
                    ephemeral=True,
                )

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
                    extra=f"/ban_unban toggle mode: `{action_mode}`",
                    color=discord.Color.green(),
                )

                return await safe_followup(
                    interaction,
                    f"✅ Unbanned {_target_reply_label(banned_user, user_id)}.{_modlog_suffix(logged)}",
                    ephemeral=True,
                )
            except discord.NotFound:
                return await safe_followup(
                    interaction,
                    f"ℹ️ `{user_id}` is not currently banned. Nothing to unban.",
                    ephemeral=True,
                )
            except discord.Forbidden:
                return await safe_followup(
                    interaction,
                    "❌ Forbidden (missing Ban Members permission).",
                    ephemeral=True,
                )
            except Exception as e:
                return await safe_followup(
                    interaction,
                    f"❌ Error while unbanning: {e}",
                    ephemeral=True,
                )

        if should_ban:
            if is_banned:
                return await safe_followup(
                    interaction,
                    f"ℹ️ `{user_id}` is already banned. Use mode **Unban** or Auto toggle to unban.",
                    ephemeral=True,
                )

            target_for_ban: Any = target_member or discord.Object(id=int(user_id))

            try:
                if int(user_id) == int(interaction.user.id):
                    return await safe_followup(
                        interaction,
                        "❌ You can’t use this command on yourself.",
                        ephemeral=True,
                    )
            except Exception:
                pass

            try:
                if int(user_id) == int(me.id):
                    return await safe_followup(
                        interaction,
                        "❌ I can’t ban myself.",
                        ephemeral=True,
                    )
            except Exception:
                pass

            try:
                if target_member is not None and me.top_role <= target_member.top_role and not me.guild_permissions.administrator:
                    return await safe_followup(
                        interaction,
                        "❌ I can’t ban that member (role hierarchy).",
                        ephemeral=True,
                    )
            except Exception:
                pass

            dmd = int(delete_message_days or 0)
            dmd = max(0, min(7, dmd))
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
                    await guild.ban(
                        target_for_ban,
                        reason=action_reason,
                        delete_message_days=dmd,
                    )
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
                    extra=_staff_confirmed_extra(
                        "ban_unban",
                        f"Deleted message history: `{dmd}` day(s)\nToggle mode: `{action_mode}`",
                    ),
                    color=discord.Color.red(),
                )

                return await safe_followup(
                    interaction,
                    f"🔨 Banned {_target_reply_label(target_for_ban, user_id)}.{_modlog_suffix(logged)}",
                    ephemeral=True,
                )

            except discord.Forbidden:
                return await safe_followup(
                    interaction,
                    "❌ Forbidden (permissions/hierarchy).",
                    ephemeral=True,
                )
            except Exception as e:
                return await safe_followup(
                    interaction,
                    f"❌ Error while banning: {e}",
                    ephemeral=True,
                )

        return await safe_followup(interaction, "❌ Invalid ban toggle state.", ephemeral=True)

    # ============================================================
    # /mod_timeout
    # ============================================================
    @tree.command(
        name="mod_timeout",
        description="(Staff) Timeout a member.",
    )
    @app_commands.describe(
        member="Mention, ID, username, or display name of the member to timeout",
        minutes="Minutes (default MOD_TIMEOUT_MINUTES)",
        reason="Reason (optional)",
    )
    @app_commands.autocomplete(member=member_autocomplete)
    async def mod_timeout_slash(
        interaction: discord.Interaction,
        member: str,
        minutes: Optional[int] = None,
        reason: Optional[str] = None,
    ):
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
            return await safe_followup(
                interaction,
                "❌ I lack **Moderate Members** permission.",
                ephemeral=True,
            )

        mins = int(minutes or MOD_TIMEOUT_MINUTES)
        mins = max(1, min(60 * 24 * 28, mins))

        try:
            if int(target.id) == int(interaction.user.id):
                return await safe_followup(
                    interaction,
                    "❌ You can’t use this command on yourself.",
                    ephemeral=True,
                )
        except Exception:
            pass

        try:
            if int(target.id) == int(me.id):
                return await safe_followup(
                    interaction,
                    "❌ I can’t timeout myself.",
                    ephemeral=True,
                )
        except Exception:
            pass

        try:
            if me.top_role <= target.top_role and not me.guild_permissions.administrator:
                return await safe_followup(
                    interaction,
                    "❌ I can’t timeout that member (role hierarchy).",
                    ephemeral=True,
                )
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
                extra=detail,
                color=discord.Color.gold(),
            )

            return await safe_followup(
                interaction,
                f"⏳ Timed out {target.mention} for {mins} minutes.{_modlog_suffix(logged)}",
                ephemeral=True,
            )

        except discord.Forbidden:
            return await safe_followup(
                interaction,
                "❌ Forbidden (permissions/hierarchy).",
                ephemeral=True,
            )
        except Exception as e:
            return await safe_followup(
                interaction,
                f"❌ Error: {e}",
                ephemeral=True,
            )

    # ============================================================
    # /debug_intents
    # ============================================================
    @tree.command(
        name="debug_intents",
        description="Check bot intents and member visibility",
    )
    async def debug_intents(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        await safe_defer(interaction, ephemeral=True)

        guild = interaction.guild
        if guild is None:
            return await safe_followup(interaction, "❌ Invalid context.", ephemeral=True)

        lines = []
        lines.append(f"**Bot User:** {bot.user}")
        lines.append(f"**Guild ID:** {guild.id}")
        lines.append(f"**Guild Name:** {guild.name}")

        intents = bot.intents
        lines.append("**Intents:**")
        lines.append(f"- Guilds: {intents.guilds}")
        lines.append(f"- Members: {intents.members}")
        lines.append(f"- Presence: {intents.presences}")
        lines.append(f"- Message Content: {intents.message_content}")

        try:
            me = guild.me
            if me:
                lines.append(f"**Bot Member:** {me} (roles: {len(me.roles)})")
            else:
                lines.append("**Bot Member:** not resolved")

            members = [m for m in guild.members[:5]]
            lines.append(
                "**First few members in cache:** "
                + (", ".join(str(m) for m in members) if members else "none")
            )
        except Exception as e:
            lines.append(f"❌ Error accessing members: {e}")

        await safe_followup(interaction, "\n".join(lines), ephemeral=True)
