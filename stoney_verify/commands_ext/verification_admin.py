from __future__ import annotations

import os
import asyncio
from typing import Optional, Tuple, Dict, Any, List

import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403
from ..globals import get_supabase, now_utc

from ..tickets import (
    is_verification_ticket_channel,
    find_ticket_owner_retry,
)

from ..verify_ui import post_or_replace_verify_ui

from ..tickets_new.service import (
    create_ticket_channel,
    find_open_ticket_for_owner,
)

from ..transcripts import (
    check_bot_can_assign_roles,
    ensure_verify_ui_present,
)

from .common import (
    RUNTIME_STATS,
    _staff_check,
    reply_once,
    token_is_expired,
)

from .kick_timers import (
    _cancel_kick_timer,
    kick_timer_persist_delete,
)

from .vc_flow import (
    _get_vc_channel,
    _can_manage_channel,
    _cleanup_vc_permissions,
)


# ============================================================
# STORE / SB HELPERS
# ============================================================
try:
    from ..store import (
        sb_get_token_info,
        sb_set_submitted,
        sb_mark_decision,
        sb_set_used,
        sb_set_submitted_at,
        sb_set_decided_by,
    )
except Exception:
    def sb_get_token_info(token: str) -> Optional[Dict[str, Any]]:  # type: ignore
        return None

    def sb_set_submitted(token: str) -> None:  # type: ignore
        return None

    def sb_mark_decision(token: str, decision: str, staff_id: int, approved_user_id: Optional[int] = None) -> None:  # type: ignore
        return None

    def sb_set_used(token: str, used: bool = True) -> None:  # type: ignore
        return None

    def sb_set_submitted_at(token: str, submitted_at=None) -> None:  # type: ignore
        return None

    def sb_set_decided_by(token: str, staff_id: int) -> None:  # type: ignore
        return None


# ============================================================
# MEMBERS / ROLES
# ============================================================
def _role_by_id(guild: discord.Guild, role_id: int) -> Optional[discord.Role]:
    try:
        if not guild or not role_id or int(role_id) <= 0:
            return None
        r = guild.get_role(int(role_id))
        return r if isinstance(r, discord.Role) else None
    except Exception:
        return None


def _member_has_role(member: discord.Member, role_id: int) -> bool:
    role = _role_by_id(member.guild, int(role_id or 0))
    return bool(role and role in member.roles)


def _member_needs_verification(member: Optional[discord.Member]) -> bool:
    try:
        if not isinstance(member, discord.Member):
            return False

        guild = member.guild
        unverified_role = _role_by_id(guild, int(UNVERIFIED_ROLE_ID or 0))
        verified_role = _role_by_id(guild, int(VERIFIED_ROLE_ID or 0))
        resident_role = _role_by_id(guild, int(RESIDENT_ROLE_ID or 0))

        has_unverified = bool(unverified_role and unverified_role in member.roles)
        has_verified = bool(verified_role and verified_role in member.roles)
        has_resident = bool(resident_role and resident_role in member.roles)

        if has_verified or has_resident:
            return False

        return has_unverified
    except Exception:
        return False


async def _resolve_member_any(guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
    member = guild.get_member(int(user_id))
    if member is None:
        try:
            member = await guild.fetch_member(int(user_id))
        except Exception:
            member = None
    return member


async def _resolve_open_ticket_channel_for_owner(
    guild: discord.Guild,
    owner_id: int,
) -> Optional[discord.TextChannel]:
    try:
        row = await find_open_ticket_for_owner(guild_id=guild.id, owner_id=owner_id)
    except Exception:
        row = None

    if not isinstance(row, dict):
        return None

    ch_id = int(str(row.get("discord_thread_id") or row.get("channel_id") or "0") or 0)
    if ch_id <= 0:
        return None

    ch = guild.get_channel(ch_id)
    if isinstance(ch, discord.TextChannel):
        return ch

    try:
        fetched = await guild.fetch_channel(ch_id)
        if isinstance(fetched, discord.TextChannel):
            return fetched
    except Exception:
        pass

    return None


async def _resolve_ticket_owner_from_channel(channel: discord.TextChannel) -> Optional[discord.Member]:
    try:
        owner = await find_ticket_owner_retry(channel)
        if isinstance(owner, discord.Member):
            return owner
    except Exception:
        pass
    return None


async def _remove_unverified_role_if_present(
    member: Optional[discord.Member],
    *,
    reason: str,
) -> tuple[bool, Optional[str]]:
    try:
        if not isinstance(member, discord.Member):
            return False, None

        unverified_role = _role_by_id(member.guild, int(UNVERIFIED_ROLE_ID or 0))
        if not unverified_role or unverified_role not in member.roles:
            return False, None

        await member.remove_roles(unverified_role, reason=reason)
        return True, None
    except discord.Forbidden:
        return False, "I can't remove the Unverified role. Check role hierarchy and Manage Roles."
    except Exception as e:
        return False, str(e)


async def _maybe_post_verify_ui_for_channel(ch: discord.TextChannel, *, reason: str) -> bool:
    try:
        if not is_verification_ticket_channel(ch):
            return False

        owner = await find_ticket_owner_retry(ch)
        if not isinstance(owner, discord.Member):
            return False

        if not _member_needs_verification(owner):
            return False

        await ensure_verify_ui_present(ch, reason=reason)
        return True
    except Exception:
        return False


async def _apply_role_change(
    *,
    interaction: discord.Interaction,
    member: discord.Member,
    role: discord.Role,
    enable: bool,
    reason: str,
) -> None:
    try:
        me = interaction.guild.me if interaction.guild else None  # type: ignore[union-attr]
        if not me or not me.guild_permissions.manage_roles:
            await reply_once(interaction, {"content": "❌ I need **Manage Roles** permission to do that.", "ephemeral": True})
            return

        try:
            if me.top_role <= role and not me.guild_permissions.administrator:
                await reply_once(interaction, {"content": "❌ I can’t manage that role due to **role hierarchy** (move my role above it).", "ephemeral": True})
                return
        except Exception:
            pass

        if enable:
            if role in member.roles:
                await reply_once(interaction, {"content": f"✅ {member.mention} already has {role.mention}.", "ephemeral": True})
                return
            await member.add_roles(role, reason=reason)
            await reply_once(interaction, {"content": f"✅ Added {role.mention} to {member.mention}.", "ephemeral": True})
        else:
            if role not in member.roles:
                await reply_once(interaction, {"content": f"✅ {member.mention} doesn’t have {role.mention}.", "ephemeral": True})
                return
            await member.remove_roles(role, reason=reason)
            await reply_once(interaction, {"content": f"✅ Removed {role.mention} from {member.mention}.", "ephemeral": True})
    except discord.Forbidden:
        await reply_once(interaction, {"content": "❌ Forbidden (role hierarchy or missing permissions).", "ephemeral": True})
    except Exception as e:
        await reply_once(interaction, {"content": f"❌ Failed: {e}", "ephemeral": True})


# ============================================================
# VERIFY STATS HELPERS
# ============================================================
def _sb_tokens_table():
    try:
        sb = get_supabase()
        if not sb:
            return None
        table = (os.getenv("SUPABASE_TOKEN_TABLE", "") or "").strip() or "verification_tokens"
        return sb.table(table)
    except Exception:
        return None


def _sb_apply_scope(q):
    try:
        col = (os.getenv("SUPABASE_SCOPE_COLUMN", "") or "").strip()
        val = (os.getenv("SUPABASE_SCOPE_VALUE", "") or "").strip()
        if col and val:
            q = q.eq(col, val)
    except Exception:
        pass
    return q


def _sb_count_eq_sync(**eq_filters) -> int:
    tbl = _sb_tokens_table()
    if not tbl:
        return 0
    try:
        q = tbl.select("token", count="exact")
        q = _sb_apply_scope(q)
        for k, v in eq_filters.items():
            q = q.eq(k, v)
        r = q.execute()
        return int(getattr(r, "count", 0) or 0)
    except Exception:
        return 0


def _sb_count_ilike_sync(col: str, pattern: str) -> int:
    tbl = _sb_tokens_table()
    if not tbl:
        return 0
    try:
        q = tbl.select("token", count="exact")
        q = _sb_apply_scope(q)
        q = q.ilike(col, pattern)
        r = q.execute()
        return int(getattr(r, "count", 0) or 0)
    except Exception:
        return 0


def _sb_count_expired_unused_sync() -> int:
    tbl = _sb_tokens_table()
    if not tbl:
        return 0
    try:
        now_iso = now_utc().isoformat()
        q = tbl.select("token", count="exact")
        q = _sb_apply_scope(q)
        q = q.lt("expires_at", now_iso).eq("used", False)
        r = q.execute()
        return int(getattr(r, "count", 0) or 0)
    except Exception:
        return 0


async def _sb_count_eq_async(**eq_filters) -> int:
    return await asyncio.to_thread(_sb_count_eq_sync, **eq_filters)


async def _sb_count_ilike_async(col: str, pattern: str) -> int:
    return await asyncio.to_thread(_sb_count_ilike_sync, col, pattern)


async def _sb_count_expired_unused_async() -> int:
    return await asyncio.to_thread(_sb_count_expired_unused_sync)


# ============================================================
# COMMANDS
# ============================================================
def register_verification_admin_commands(bot, tree) -> None:
    # ============================================================
    # force_verify_ui
    # ============================================================
    @tree.command(
        name="force_verify_ui",
        description="Force-create verification ticket and UI for a user.",
    )
    @app_commands.describe(user="Member to repair verification for")
    async def force_verify_ui(interaction: discord.Interaction, user: discord.Member):
        guild = interaction.guild

        if guild is None:
            await interaction.response.send_message(
                "❌ Invalid context (no guild).",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            uv_id = int(UNVERIFIED_ROLE_ID or 0)
            if uv_id:
                uv_role = guild.get_role(uv_id)
                if uv_role and uv_role not in user.roles:
                    await user.add_roles(
                        uv_role,
                        reason=f"Verification repair command by {interaction.user} ({interaction.user.id})",
                    )

            ticket = None
            try:
                ticket = await find_open_ticket_for_owner(
                    guild_id=guild.id,
                    owner_id=user.id,
                )
            except Exception:
                ticket = None

            channel: Optional[discord.abc.GuildChannel] = None

            if ticket:
                ch_id = int(ticket.get("discord_thread_id") or ticket.get("channel_id") or 0)
                if ch_id:
                    channel = guild.get_channel(ch_id)
                    if channel is None:
                        try:
                            channel = await guild.fetch_channel(ch_id)
                        except Exception:
                            channel = None

            if not isinstance(channel, discord.TextChannel):
                channel = await create_ticket_channel(
                    guild=guild,
                    owner=user,
                    category="verification_issue",
                    source="manual_force_verify",
                    is_ghost=False,
                )

            if not isinstance(channel, discord.TextChannel):
                await interaction.followup.send(
                    "❌ Could not create verification ticket.",
                    ephemeral=True,
                )
                return

            token = await post_or_replace_verify_ui(
                channel,
                requester_id=user.id,
                reason=f"force_verify_ui_by_staff:{interaction.user.id}",
                site_url=VERIFY_SITE_URL,
                ttl_minutes=int(TOKEN_TTL_MINUTES or 20),
                allow_regen=True,
            )

            await interaction.followup.send(
                f"✅ Verification repaired for {user.mention}\n"
                f"Ticket: {channel.mention}\n"
                f"Token created: `{bool(token)}`",
                ephemeral=True,
            )

        except Exception as e:
            await interaction.followup.send(
                f"❌ Repair failed: `{e}`",
                ephemeral=True,
            )

    # ============================================================
    # verify_token
    # ============================================================
    @tree.command(name="verify_token", description="(Staff) Show status for a verification token.")
    @app_commands.describe(token="Token string (the part after t: )")
    async def verify_token_slash(interaction: discord.Interaction, token: str):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        t = (token or "").strip()
        ti = sb_get_token_info(t)
        if not ti:
            return await interaction.followup.send("❌ Token not found.", ephemeral=True)

        try:
            from ..globals import _parse_iso_datetime
            exp = _parse_iso_datetime(ti.get("expires_at"))
        except Exception:
            exp = None

        embed = discord.Embed(title="Verification Token Status", color=discord.Color.dark_green())
        embed.add_field(name="Token", value=f"`{t}`", inline=False)
        embed.add_field(name="Used", value=f"`{ti.get('used', False)}`", inline=True)
        embed.add_field(name="Submitted", value=f"`{ti.get('submitted', False)}`", inline=True)
        embed.add_field(name="Decision", value=f"`{ti.get('decision')}`", inline=False)
        embed.add_field(name="Channel ID", value=f"`{ti.get('channel_id')}`", inline=True)
        embed.add_field(name="Guild ID", value=f"`{ti.get('guild_id')}`", inline=True)
        embed.add_field(name="Requester", value=f"`{ti.get('requester_id') or ti.get('user_id')}`", inline=True)
        embed.add_field(name="Expires", value=f"`{exp.isoformat() if exp else ti.get('expires_at')}`", inline=False)
        embed.add_field(name="Expired?", value=f"`{token_is_expired(ti)}`", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ============================================================
    # verify_stats
    # ============================================================
    @tree.command(name="verify_stats", description="(Staff) Live verification stats (Supabase) + runtime counters.")
    async def verify_stats_slash(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        total = await _sb_count_eq_async()
        used = await _sb_count_eq_async(used=True)
        pending = await _sb_count_eq_async(used=False)
        submitted = await _sb_count_eq_async(submitted=True)
        expired_unused = await _sb_count_expired_unused_async()

        approved = await _sb_count_eq_async(decision="APPROVED")
        denied = await _sb_count_eq_async(decision="DENIED")
        resubmit = await _sb_count_eq_async(decision="RESUBMIT REQUESTED")

        vc_approved = await _sb_count_ilike_async("decision", "%(VC)%APPROVED%")
        vc_denied = await _sb_count_ilike_async("decision", "%(VC)%DENIED%")

        emb = discord.Embed(
            title="📊 Verify Stats",
            description="**Live counts from Supabase** (not affected by restarts)",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        emb.add_field(name="Total Tokens", value=str(total), inline=True)
        emb.add_field(name="Pending (unused)", value=str(pending), inline=True)
        emb.add_field(name="Used", value=str(used), inline=True)
        emb.add_field(name="Submitted", value=str(submitted), inline=True)
        emb.add_field(name="Expired (unused)", value=str(expired_unused), inline=True)
        emb.add_field(name="Decisions", value=f"✅ {approved} | ❌ {denied}", inline=True)
        emb.add_field(name="Resubmit Requested", value=str(resubmit), inline=True)
        emb.add_field(name="VC Decisions", value=f"✅ {vc_approved} | ❌ {vc_denied}", inline=True)

        try:
            if isinstance(RUNTIME_STATS, dict) and len(RUNTIME_STATS) > 0:
                keys = sorted(list(RUNTIME_STATS.keys()))
                preview = keys[:18]
                runtime_lines = [f"{k}={RUNTIME_STATS.get(k)}" for k in preview]
                if len(keys) > len(preview):
                    runtime_lines.append(f"... +{len(keys) - len(preview)} more")
                emb.add_field(
                    name="Runtime Counters (session)",
                    value="`" + " | ".join(runtime_lines) + "`",
                    inline=False,
                )
        except Exception:
            pass

        await interaction.followup.send(embed=emb, ephemeral=True)

    # ============================================================
    # fix_unverified
    # ============================================================
    @tree.command(name="fix_unverified", description="(Staff) Assign Unverified to members who are not Verified.")
    async def fix_unverified(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        try:
            uv_id = int(UNVERIFIED_ROLE_ID or 0)
        except Exception:
            uv_id = 0
        try:
            v_id = int(VERIFIED_ROLE_ID or 0)
        except Exception:
            v_id = 0
        try:
            staff_id = int(STAFF_ROLE_ID or 0)
        except Exception:
            staff_id = 0

        if not uv_id:
            return await interaction.followup.send("❌ UNVERIFIED_ROLE_ID is not set.", ephemeral=True)

        guild = interaction.guild
        if not guild:
            return await interaction.followup.send("❌ Invalid context (no guild).", ephemeral=True)

        uv_role = guild.get_role(int(uv_id))
        if not uv_role:
            return await interaction.followup.send(f"❌ Role with ID {uv_id} not found.", ephemeral=True)

        verified_role = guild.get_role(int(v_id)) if v_id else None
        staff_role = guild.get_role(int(staff_id)) if staff_id else None

        me = guild.me
        if not me or not me.guild_permissions.manage_roles:
            return await interaction.followup.send("❌ I lack **Manage Roles** permission.", ephemeral=True)

        try:
            if me.top_role <= uv_role and not me.guild_permissions.administrator:
                return await interaction.followup.send(
                    "❌ My role is not high enough to assign **Unverified** (role hierarchy).",
                    ephemeral=True,
                )
        except Exception:
            pass

        try:
            await guild.chunk(cache=True)
        except Exception:
            pass

        added = 0
        skipped_verified = 0
        skipped_staff = 0
        skipped_bots = 0
        already_ok = 0
        failed = 0

        i = 0
        for member in list(guild.members or []):
            i += 1
            try:
                if getattr(member, "bot", False):
                    skipped_bots += 1
                    continue
                if staff_role and staff_role in member.roles:
                    skipped_staff += 1
                    continue
                if verified_role and verified_role in member.roles:
                    skipped_verified += 1
                    continue
                if uv_role in member.roles:
                    already_ok += 1
                    continue

                await member.add_roles(uv_role, reason=f"fix_unverified by {interaction.user} ({interaction.user.id})")
                added += 1
            except discord.Forbidden:
                failed += 1
            except Exception:
                failed += 1

            if i % 10 == 0:
                try:
                    await asyncio.sleep(0.4)
                except Exception:
                    pass

        await interaction.followup.send(
            "✅ **fix_unverified complete**\n"
            f"- Added Unverified: **{added}**\n"
            f"- Already had Unverified: **{already_ok}**\n"
            f"- Skipped (Verified): **{skipped_verified}**\n"
            f"- Skipped (Staff): **{skipped_staff}**\n"
            f"- Skipped (Bots): **{skipped_bots}**\n"
            f"- Failed: **{failed}**",
            ephemeral=True,
        )

    # ============================================================
    # set_verified
    # ============================================================
    @tree.command(name="set_verified", description="(Staff) Add/remove the Verified role (break-glass fix).")
    @app_commands.describe(user="Member to update", enable="True to add Verified; False to remove")
    async def set_verified_slash(interaction: discord.Interaction, user: discord.Member, enable: bool):
        if not _staff_check(interaction):
            await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
            return
        if not interaction.guild:
            await reply_once(interaction, {"content": "❌ This command must be used in a server.", "ephemeral": True})
            return

        role = _role_by_id(interaction.guild, int(VERIFIED_ROLE_ID or 0))
        if not role:
            await reply_once(interaction, {"content": "❌ VERIFIED_ROLE_ID is not configured or role not found.", "ephemeral": True})
            return

        await _apply_role_change(
            interaction=interaction,
            member=user,
            role=role,
            enable=bool(enable),
            reason=f"Manual verified role change by {interaction.user} ({interaction.user.id})",
        )

    # ============================================================
    # set_resident
    # ============================================================
    @tree.command(name="set_resident", description="(Staff) Add/remove the Resident role (break-glass fix).")
    @app_commands.describe(user="Member to update", enable="True to add Resident; False to remove")
    async def set_resident_slash(interaction: discord.Interaction, user: discord.Member, enable: bool):
        if not _staff_check(interaction):
            await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
            return
        if not interaction.guild:
            await reply_once(interaction, {"content": "❌ This command must be used in a server.", "ephemeral": True})
            return

        role = _role_by_id(interaction.guild, int(RESIDENT_ROLE_ID or 0))
        if not role:
            await reply_once(interaction, {"content": "❌ RESIDENT_ROLE_ID is not configured or role not found.", "ephemeral": True})
            return

        await _apply_role_change(
            interaction=interaction,
            member=user,
            role=role,
            enable=bool(enable),
            reason=f"Manual resident role change by {interaction.user} ({interaction.user.id})",
        )

    # ============================================================
    # grant_vr
    # ============================================================
    @tree.command(name="grant_vr", description="(Staff) Grant Verified + Resident, with optional Stoner/Drunken roles.")
    @app_commands.describe(
        user="Member to update",
        stoner="Also add the Stoner role (optional)",
        drunken="Also add the Drunken role (optional)",
    )
    async def grant_vr_slash(
        interaction: discord.Interaction,
        user: discord.Member,
        stoner: bool = False,
        drunken: bool = False,
    ):
        if not _staff_check(interaction):
            await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
            return
        if not interaction.guild:
            await reply_once(interaction, {"content": "❌ This command must be used in a server.", "ephemeral": True})
            return

        guild = interaction.guild
        await interaction.response.defer(ephemeral=True)

        verified_role = _role_by_id(guild, int(VERIFIED_ROLE_ID or 0))
        resident_role = _role_by_id(guild, int(RESIDENT_ROLE_ID or 0))
        stoner_role = _role_by_id(guild, int(globals().get("STONER_ROLE_ID") or 0))
        drunken_role = _role_by_id(guild, int(globals().get("DRUNKEN_ROLE_ID") or 0))
        unverified_role = _role_by_id(guild, int(UNVERIFIED_ROLE_ID or 0))

        missing = []
        if not verified_role:
            missing.append("VERIFIED_ROLE_ID")
        if not resident_role:
            missing.append("RESIDENT_ROLE_ID")
        if missing:
            return await interaction.followup.send(
                f"❌ Missing/invalid role config: {', '.join(missing)}",
                ephemeral=True,
            )

        me = guild.me
        if not me or not me.guild_permissions.manage_roles:
            return await interaction.followup.send("❌ I need **Manage Roles** permission.", ephemeral=True)

        to_add = [verified_role, resident_role]
        if stoner and stoner_role:
            to_add.append(stoner_role)
        if drunken and drunken_role:
            to_add.append(drunken_role)

        for r in to_add + ([unverified_role] if unverified_role else []):
            if not r:
                continue
            try:
                if me.top_role <= r and not me.guild_permissions.administrator:
                    return await interaction.followup.send(
                        f"❌ I can’t manage {r.mention} due to **role hierarchy** (move my role above it).",
                        ephemeral=True,
                    )
            except Exception:
                pass

        try:
            add_list = [r for r in to_add if r and r not in user.roles]
            remove_list = []
            if unverified_role and unverified_role in user.roles:
                remove_list.append(unverified_role)

            if add_list:
                await user.add_roles(*add_list, reason=f"grant_vr by {interaction.user} ({interaction.user.id})")
            if remove_list:
                await user.remove_roles(*remove_list, reason=f"grant_vr cleanup by {interaction.user} ({interaction.user.id})")

            lines = [f"✅ Updated {user.mention}:"]
            lines.append(f"- Added: {', '.join(r.mention for r in add_list) if add_list else 'none (already had)'}")
            if unverified_role:
                lines.append(f"- Removed Unverified: {'YES' if remove_list else 'NO'}")
            if stoner and not stoner_role:
                lines.append("- Note: STONER_ROLE_ID not set, skipped.")
            if drunken and not drunken_role:
                lines.append("- Note: DRUNKEN_ROLE_ID not set, skipped.")

            return await interaction.followup.send("\n".join(lines), ephemeral=True)

        except discord.Forbidden:
            return await interaction.followup.send("❌ Forbidden (role hierarchy or missing permissions).", ephemeral=True)
        except Exception as e:
            return await interaction.followup.send(f"❌ Failed: {e}", ephemeral=True)

    # ============================================================
    # verify_diagnose
    # ============================================================
    @tree.command(name="verify_diagnose", description="(Staff) Deep verification diagnostics for a member.")
    @app_commands.describe(user="Member to inspect")
    async def verify_diagnose_slash(interaction: discord.Interaction, user: discord.Member):
        if not _staff_check(interaction):
            await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
            return

        guild = interaction.guild
        if guild is None:
            await reply_once(interaction, {"content": "❌ This command must be used in a server.", "ephemeral": True})
            return

        await interaction.response.defer(ephemeral=True)

        verified_role = _role_by_id(guild, int(VERIFIED_ROLE_ID or 0))
        resident_role = _role_by_id(guild, int(RESIDENT_ROLE_ID or 0))
        unverified_role = _role_by_id(guild, int(UNVERIFIED_ROLE_ID or 0))
        staff_role = _role_by_id(guild, int(STAFF_ROLE_ID or 0))

        open_ticket = await _resolve_open_ticket_channel_for_owner(guild, int(user.id))
        vc_channel = _get_vc_channel(guild)
        me = guild.me or await _resolve_member_any(guild, int(getattr(getattr(bot, "user", None), "id", 0) or 0))

        can_manage_roles = bool(me and me.guild_permissions.manage_roles)
        can_kick = bool(me and me.guild_permissions.kick_members)

        vc_manage_ok = False
        vc_manage_reason = "VC verify channel not configured."
        if vc_channel and me:
            vc_manage_ok, vc_manage_reason = _can_manage_channel(me, vc_channel)

        embed = discord.Embed(
            title="🩺 Verification Diagnose",
            color=discord.Color.blurple(),
            timestamp=now_utc(),
        )
        embed.add_field(name="Member", value=f"{user.mention} (`{user.id}`)", inline=False)
        embed.add_field(name="Unverified", value="YES" if (unverified_role and unverified_role in user.roles) else "NO", inline=True)
        embed.add_field(name="Verified", value="YES" if (verified_role and verified_role in user.roles) else "NO", inline=True)
        embed.add_field(name="Resident", value="YES" if (resident_role and resident_role in user.roles) else "NO", inline=True)
        embed.add_field(name="Staff", value="YES" if (staff_role and staff_role in user.roles) else "NO", inline=True)
        embed.add_field(name="Needs Verification", value="YES" if _member_needs_verification(user) else "NO", inline=True)
        embed.add_field(name="Open Ticket", value=(open_ticket.mention if open_ticket else "None"), inline=False)
        embed.add_field(name="Verify Site URL", value=f"`{bool(VERIFY_SITE_URL)}`", inline=True)
        embed.add_field(name="VC Verify Channel", value=(vc_channel.mention if vc_channel else "Not configured"), inline=True)
        embed.add_field(name="Bot Manage Roles", value="YES" if can_manage_roles else "NO", inline=True)
        embed.add_field(name="Bot Kick Members", value="YES" if can_kick else "NO", inline=True)
        embed.add_field(name="Bot Manage VC", value=("YES" if vc_manage_ok else f"NO — {vc_manage_reason}"), inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ============================================================
    # fix_unverified_member
    # ============================================================
    @tree.command(name="fix_unverified_member", description="(Staff) Re-add Unverified to a member when it is missing.")
    @app_commands.describe(
        user="Member to repair",
        remove_verified_conflicts="Also remove Verified/Resident while restoring Unverified",
    )
    async def fix_unverified_member_slash(
        interaction: discord.Interaction,
        user: discord.Member,
        remove_verified_conflicts: bool = False,
    ):
        if not _staff_check(interaction):
            await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
            return

        guild = interaction.guild
        if guild is None:
            await reply_once(interaction, {"content": "❌ This command must be used in a server.", "ephemeral": True})
            return

        uv_role = _role_by_id(guild, int(UNVERIFIED_ROLE_ID or 0))
        verified_role = _role_by_id(guild, int(VERIFIED_ROLE_ID or 0))
        resident_role = _role_by_id(guild, int(RESIDENT_ROLE_ID or 0))

        if not uv_role:
            await reply_once(interaction, {"content": "❌ UNVERIFIED_ROLE_ID is not configured or role not found.", "ephemeral": True})
            return

        await interaction.response.defer(ephemeral=True)

        added: list[str] = []
        removed: list[str] = []

        try:
            if remove_verified_conflicts:
                for role in (verified_role, resident_role):
                    if role and role in user.roles:
                        await user.remove_roles(role, reason=f"Fix Unverified by {interaction.user} ({interaction.user.id})")
                        removed.append(role.name)

            if uv_role not in user.roles:
                await user.add_roles(uv_role, reason=f"Fix Unverified by {interaction.user} ({interaction.user.id})")
                added.append(uv_role.name)

            ticket_ch = await _resolve_open_ticket_channel_for_owner(guild, int(user.id))
            if ticket_ch:
                try:
                    await ensure_verify_ui_present(ticket_ch, reason="fix_unverified_slash")
                except Exception:
                    pass

            lines = [f"✅ Unverified repair complete for {user.mention}."]
            lines.append(f"Added: {', '.join(added) if added else 'nothing'}")
            lines.append(f"Removed: {', '.join(removed) if removed else 'nothing'}")
            if ticket_ch:
                lines.append(f"Ticket: {ticket_ch.mention}")

            await interaction.followup.send("\n".join(lines), ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ Forbidden. Check Manage Roles + hierarchy.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to repair Unverified: {e}", ephemeral=True)

    # ============================================================
    # verify_status
    # ============================================================
    @tree.command(name="verify_status", description="(Staff) Show a member's verification/resident status.")
    @app_commands.describe(user="Member to inspect")
    async def verify_status_slash(interaction: discord.Interaction, user: discord.Member):
        if not _staff_check(interaction):
            await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
            return
        if not interaction.guild:
            await reply_once(interaction, {"content": "❌ This command must be used in a server.", "ephemeral": True})
            return

        verified_role = _role_by_id(interaction.guild, int(VERIFIED_ROLE_ID or 0))
        resident_role = _role_by_id(interaction.guild, int(RESIDENT_ROLE_ID or 0))

        has_v = bool(verified_role and verified_role in user.roles)
        has_r = bool(resident_role and resident_role in user.roles)

        lines = [
            f"👤 {user.mention} (`{user.id}`)",
            f"✅ Verified role: {'YES' if has_v else 'NO'}" + (f" ({verified_role.mention})" if verified_role else " (not configured)"),
            f"🏠 Resident role: {'YES' if has_r else 'NO'}" + (f" ({resident_role.mention})" if resident_role else " (not configured)"),
        ]
        await reply_once(interaction, {"content": "\n".join(lines), "ephemeral": True})

    # ============================================================
    # ticket_owner
    # ============================================================
    @tree.command(name="ticket_owner", description="(Staff) Show who owns the current ticket and what row was resolved.")
    @app_commands.describe(channel="Optional ticket channel (defaults to current channel)")
    async def ticket_owner_slash(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        ch = channel or interaction.channel
        if not isinstance(ch, discord.TextChannel):
            return await interaction.response.send_message("❌ Invalid channel.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        owner = await _resolve_ticket_owner_from_channel(ch)
        is_verify = is_verification_ticket_channel(ch)

        lines = [
            f"🎫 Channel: {ch.mention} (`{ch.id}`)",
            f"Verification ticket: {'YES' if is_verify else 'NO'}",
            f"Owner: {owner.mention if owner else 'Unknown'}" + (f" (`{owner.id}`)" if owner else ""),
        ]
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    # ============================================================
    # vc_cleanup
    # ============================================================
    @tree.command(name="vc_cleanup", description="(Staff) Remove stale non-staff permission overwrites from the VC verify channel.")
    async def vc_cleanup_slash(interaction: discord.Interaction):
        if not _staff_check(interaction):
            return await interaction.response.send_message("❌ Staff only.", ephemeral=True)

        guild = interaction.guild
        if guild is None:
            return await interaction.response.send_message("❌ Invalid context.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        try:
            await _cleanup_vc_permissions(guild, keep_member=None)
            await interaction.followup.send("✅ VC verify channel cleanup complete.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ VC cleanup failed: {e}", ephemeral=True)