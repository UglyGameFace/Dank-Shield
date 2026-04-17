from __future__ import annotations

from typing import Optional, List, Any
from datetime import timedelta

import re

import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403
from ..globals import now_utc

from .common import _staff_check, RUNTIME_STATS


def _normalize_lookup_text(value: object) -> str:
    try:
        return str(value or "").strip().lower()
    except Exception:
        return ""


def _parse_member_id_from_target(target: str) -> int:
    text = str(target or "").strip()
    if not text:
        return 0

    mention_match = re.search(r"<@!?(\d+)>", text)
    if mention_match:
        try:
            return int(mention_match.group(1))
        except Exception:
            return 0

    if text.isdigit():
        try:
            return int(text)
        except Exception:
            return 0

    return 0


async def _resolve_member_any(guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
    try:
        member = guild.get_member(int(user_id))
        if member is not None:
            return member
    except Exception:
        pass

    try:
        return await guild.fetch_member(int(user_id))
    except Exception:
        return None


async def _resolve_member_from_target(
    guild: discord.Guild,
    target: str,
) -> Optional[discord.Member]:
    raw = str(target or "").strip()
    if not raw:
        return None

    user_id = _parse_member_id_from_target(raw)
    if user_id > 0:
        member = await _resolve_member_any(guild, user_id)
        if member is not None:
            return member

    lowered = _normalize_lookup_text(raw)
    if not lowered:
        return None

    try:
        member_list = list(guild.members or [])
    except Exception:
        member_list = []

    def _candidate_strings(member: discord.Member) -> List[str]:
        vals = [
            getattr(member, "name", None),
            getattr(member, "display_name", None),
            getattr(member, "global_name", None),
            str(member),
        ]
        out: List[str] = []
        for v in vals:
            norm = _normalize_lookup_text(v)
            if norm and norm not in out:
                out.append(norm)
        return out

    for member in member_list:
        try:
            if lowered in _candidate_strings(member):
                return member
        except Exception:
            continue

    for member in member_list:
        try:
            tag = f"{member.name}#{member.discriminator}" if getattr(member, "discriminator", "0") != "0" else member.name
            if _normalize_lookup_text(tag) == lowered:
                return member
        except Exception:
            continue

    startswith_hits: List[discord.Member] = []
    for member in member_list:
        try:
            values = _candidate_strings(member)
            if any(v.startswith(lowered) for v in values):
                startswith_hits.append(member)
        except Exception:
            continue

    if len(startswith_hits) == 1:
        return startswith_hits[0]

    contains_hits: List[discord.Member] = []
    for member in member_list:
        try:
            values = _candidate_strings(member)
            if any(lowered in v for v in values):
                contains_hits.append(member)
        except Exception:
            continue

    if len(contains_hits) == 1:
        return contains_hits[0]

    try:
        await guild.chunk(cache=True)
        member_list = list(guild.members or [])
    except Exception:
        member_list = member_list

    for member in member_list:
        try:
            if lowered in _candidate_strings(member):
                return member
        except Exception:
            continue

    return None


async def _require_target_member(
    interaction: discord.Interaction,
    target: str,
) -> Optional[discord.Member]:
    guild = interaction.guild
    if guild is None:
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ This command must be used in a server.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "❌ This command must be used in a server.",
                    ephemeral=True,
                )
        except Exception:
            pass
        return None

    member = await _resolve_member_from_target(guild, target)
    if member is None:
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "❌ I could not resolve that member.\nUse a mention, raw user ID, exact username, or exact display name.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "❌ I could not resolve that member.\nUse a mention, raw user ID, exact username, or exact display name.",
                    ephemeral=True,
                )
        except Exception:
            pass
        return None

    return member


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
    async def mod_kick_slash(
        interaction: discord.Interaction,
        member: str,
        reason: Optional[str] = None,
    ):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if not guild or not guild.me:
            return await interaction.followup.send("❌ Invalid context.", ephemeral=True)

        target = await _require_target_member(interaction, member)
        if target is None:
            return

        me = guild.me
        if not me.guild_permissions.kick_members:
            return await interaction.followup.send(
                "❌ I lack **Kick Members** permission.",
                ephemeral=True,
            )

        try:
            if int(target.id) == int(interaction.user.id):
                return await interaction.followup.send(
                    "❌ You can’t use this command on yourself.",
                    ephemeral=True,
                )
        except Exception:
            pass

        try:
            if int(target.id) == int(me.id):
                return await interaction.followup.send(
                    "❌ I can’t kick myself.",
                    ephemeral=True,
                )
        except Exception:
            pass

        try:
            if me.top_role <= target.top_role and not me.guild_permissions.administrator:
                return await interaction.followup.send(
                    "❌ I can’t kick that member (role hierarchy).",
                    ephemeral=True,
                )
        except Exception:
            pass

        try:
            await guild.kick(
                target,
                reason=reason or f"Kick by {interaction.user} ({interaction.user.id})",
            )
            try:
                RUNTIME_STATS["mod_actions"] += 1
            except Exception:
                pass

            return await interaction.followup.send(
                f"👢 Kicked {target.mention}.",
                ephemeral=True,
            )

        except discord.Forbidden:
            return await interaction.followup.send(
                "❌ Forbidden (permissions/hierarchy).",
                ephemeral=True,
            )
        except Exception as e:
            return await interaction.followup.send(
                f"❌ Error: {e}",
                ephemeral=True,
            )

    # ============================================================
    # /mod_ban
    # ============================================================
    @tree.command(
        name="mod_ban",
        description="(Staff) Ban a member.",
    )
    @app_commands.describe(
        member="Mention, ID, username, or display name of the member to ban",
        reason="Reason (optional)",
        delete_message_days="Delete message days (0-7)",
    )
    async def mod_ban_slash(
        interaction: discord.Interaction,
        member: str,
        reason: Optional[str] = None,
        delete_message_days: Optional[int] = 0,
    ):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if not guild or not guild.me:
            return await interaction.followup.send("❌ Invalid context.", ephemeral=True)

        target = await _require_target_member(interaction, member)
        if target is None:
            return

        me = guild.me
        if not me.guild_permissions.ban_members:
            return await interaction.followup.send(
                "❌ I lack **Ban Members** permission.",
                ephemeral=True,
            )

        try:
            if int(target.id) == int(interaction.user.id):
                return await interaction.followup.send(
                    "❌ You can’t use this command on yourself.",
                    ephemeral=True,
                )
        except Exception:
            pass

        try:
            if int(target.id) == int(me.id):
                return await interaction.followup.send(
                    "❌ I can’t ban myself.",
                    ephemeral=True,
                )
        except Exception:
            pass

        try:
            if me.top_role <= target.top_role and not me.guild_permissions.administrator:
                return await interaction.followup.send(
                    "❌ I can’t ban that member (role hierarchy).",
                    ephemeral=True,
                )
        except Exception:
            pass

        dmd = int(delete_message_days or 0)
        dmd = max(0, min(7, dmd))

        try:
            await guild.ban(
                target,
                reason=reason or f"Ban by {interaction.user} ({interaction.user.id})",
                delete_message_days=dmd,
            )
            try:
                RUNTIME_STATS["mod_actions"] += 1
            except Exception:
                pass

            return await interaction.followup.send(
                f"🔨 Banned {target.mention}.",
                ephemeral=True,
            )

        except discord.Forbidden:
            return await interaction.followup.send(
                "❌ Forbidden (permissions/hierarchy).",
                ephemeral=True,
            )
        except Exception as e:
            return await interaction.followup.send(
                f"❌ Error: {e}",
                ephemeral=True,
            )

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
    async def mod_timeout_slash(
        interaction: discord.Interaction,
        member: str,
        minutes: Optional[int] = None,
        reason: Optional[str] = None,
    ):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        guild = interaction.guild
        if not guild or not guild.me:
            return await interaction.followup.send("❌ Invalid context.", ephemeral=True)

        target = await _require_target_member(interaction, member)
        if target is None:
            return

        me = guild.me
        if not me.guild_permissions.moderate_members:
            return await interaction.followup.send(
                "❌ I lack **Moderate Members** permission.",
                ephemeral=True,
            )

        mins = int(minutes or MOD_TIMEOUT_MINUTES)
        mins = max(1, min(60 * 24 * 28, mins))

        try:
            if int(target.id) == int(interaction.user.id):
                return await interaction.followup.send(
                    "❌ You can’t use this command on yourself.",
                    ephemeral=True,
                )
        except Exception:
            pass

        try:
            if int(target.id) == int(me.id):
                return await interaction.followup.send(
                    "❌ I can’t timeout myself.",
                    ephemeral=True,
                )
        except Exception:
            pass

        try:
            if me.top_role <= target.top_role and not me.guild_permissions.administrator:
                return await interaction.followup.send(
                    "❌ I can’t timeout that member (role hierarchy).",
                    ephemeral=True,
                )
        except Exception:
            pass

        until = now_utc() + timedelta(minutes=mins)

        try:
            await target.timeout(
                until,
                reason=reason or f"Timeout by {interaction.user} ({interaction.user.id})",
            )
            try:
                RUNTIME_STATS["mod_actions"] += 1
            except Exception:
                pass

            return await interaction.followup.send(
                f"⏳ Timed out {target.mention} for {mins} minutes.",
                ephemeral=True,
            )

        except discord.Forbidden:
            return await interaction.followup.send(
                "❌ Forbidden (permissions/hierarchy).",
                ephemeral=True,
            )
        except Exception as e:
            return await interaction.followup.send(
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

        await interaction.response.defer(ephemeral=True)

        lines = []
        lines.append(f"**Bot User:** {bot.user}")
        lines.append(f"**Guild ID:** {interaction.guild.id}")
        lines.append(f"**Guild Name:** {interaction.guild.name}")

        intents = bot.intents
        lines.append("**Intents:**")
        lines.append(f"- Guilds: {intents.guilds}")
        lines.append(f"- Members: {intents.members}")
        lines.append(f"- Presence: {intents.presences}")
        lines.append(f"- Message Content: {intents.message_content}")

        try:
            me = interaction.guild.me
            lines.append(f"**Bot Member:** {me} (roles: {len(me.roles)})")

            members = [m for m in interaction.guild.members[:5]]
            lines.append(
                "**First few members in cache:** "
                + (", ".join(str(m) for m in members) if members else "none")
            )
        except Exception as e:
            lines.append(f"❌ Error accessing members: {e}")

        await interaction.followup.send("\n".join(lines), ephemeral=True)
