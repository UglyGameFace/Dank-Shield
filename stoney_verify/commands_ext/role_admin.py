        from __future__ import annotations

from typing import Optional, Tuple, List

import asyncio
import re

import discord
from discord import app_commands

from ..globals import *  # noqa: F401,F403
from ..globals import now_utc
from ..tickets import find_ticket_owner_retry, is_verification_ticket_channel
from ..tickets_new.service import find_open_ticket_for_owner
from ..transcripts import ensure_verify_ui_present

from .common import _staff_check, reply_once

try:
    from .vc_flow import _get_vc_channel, _can_manage_channel
except Exception:
    def _get_vc_channel(guild: discord.Guild):  # type: ignore
        return None

    def _can_manage_channel(me: discord.Member, channel) -> tuple[bool, str]:  # type: ignore
        return False, "vc_flow import unavailable"


def register_role_admin_commands(bot, tree) -> None:
    # ============================================================
    # Internal helpers
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

    async def _resolve_member_from_target(
        guild: discord.Guild,
        target: str,
    ) -> Optional[discord.Member]:
        raw = str(target or "").strip()
        if not raw:
            return None

        # mention or raw numeric id
        user_id = _parse_member_id_from_target(raw)
        if user_id > 0:
            member = await _resolve_member_any(guild, user_id)
            if member is not None:
                return member

        lowered = _normalize_lookup_text(raw)
        if not lowered:
            return None

        # Try cache first
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

        # exact match pass
        for member in member_list:
            try:
                if lowered in _candidate_strings(member):
                    return member
            except Exception:
                continue

        # discriminator form exact
        for member in member_list:
            try:
                tag = f"{member.name}#{member.discriminator}" if getattr(member, "discriminator", "0") != "0" else member.name
                if _normalize_lookup_text(tag) == lowered:
                    return member
            except Exception:
                continue

        # startswith pass
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

        # contains pass
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

        # final attempt: chunk and retry
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
            await reply_once(
                interaction,
                {"content": "❌ This command must be used in a server.", "ephemeral": True},
            )
            return None

        member = await _resolve_member_from_target(guild, target)
        if member is None:
            await reply_once(
                interaction,
                {
                    "content": (
                        "❌ I could not resolve that member.\n"
                        "Use a mention, raw user ID, exact username, or exact display name."
                    ),
                    "ephemeral": True,
                },
            )
            return None

        return member

    async def _resolve_open_ticket_channel_for_owner(
        guild: discord.Guild,
        owner_id: int,
    ) -> Optional[discord.TextChannel]:
        try:
            row = await find_open_ticket_for_owner(
                guild_id=guild.id,
                owner_id=owner_id,
            )
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

    async def _resolve_ticket_owner_from_channel(
        channel: discord.TextChannel,
    ) -> Optional[discord.Member]:
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

    async def _maybe_post_verify_ui_for_channel(ch, *, reason: str) -> bool:
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
            me = interaction.guild.me if interaction.guild else None
            if not me or not me.guild_permissions.manage_roles:
                await reply_once(
                    interaction,
                    {
                        "content": "❌ I need **Manage Roles** permission to do that.",
                        "ephemeral": True,
                    },
                )
                return

            try:
                if me.top_role <= role and not me.guild_permissions.administrator:
                    await reply_once(
                        interaction,
                        {
                            "content": "❌ I can’t manage that role due to **role hierarchy** (move my role above it).",
                            "ephemeral": True,
                        },
                    )
                    return
            except Exception:
                pass

            if enable:
                if role in member.roles:
                    await reply_once(
                        interaction,
                        {
                            "content": f"✅ {member.mention} already has {role.mention}.",
                            "ephemeral": True,
                        },
                    )
                    return

                await member.add_roles(role, reason=reason)
                await reply_once(
                    interaction,
                    {
                        "content": f"✅ Added {role.mention} to {member.mention}.",
                        "ephemeral": True,
                    },
                )
            else:
                if role not in member.roles:
                    await reply_once(
                        interaction,
                        {
                            "content": f"✅ {member.mention} doesn’t have {role.mention}.",
                            "ephemeral": True,
                        },
                    )
                    return

                await member.remove_roles(role, reason=reason)
                await reply_once(
                    interaction,
                    {
                        "content": f"✅ Removed {role.mention} from {member.mention}.",
                        "ephemeral": True,
                    },
                )

        except discord.Forbidden:
            await reply_once(
                interaction,
                {
                    "content": "❌ Forbidden (role hierarchy or missing permissions).",
                    "ephemeral": True,
                },
            )
        except Exception as e:
            await reply_once(
                interaction,
                {
                    "content": f"❌ Failed: {e}",
                    "ephemeral": True,
                },
            )

    # ============================================================
    # /fix_unverified
    # ============================================================
    @tree.command(
        name="fix_unverified",
        description="(Staff) Assign Unverified to members who are not Verified.",
    )
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
            return await interaction.followup.send(
                "❌ UNVERIFIED_ROLE_ID is not set.",
                ephemeral=True,
            )

        guild = interaction.guild
        if not guild:
            return await interaction.followup.send(
                "❌ Invalid context (no guild).",
                ephemeral=True,
            )

        uv_role = guild.get_role(int(uv_id))
        if not uv_role:
            return await interaction.followup.send(
                f"❌ Role with ID {uv_id} not found.",
                ephemeral=True,
            )

        verified_role = guild.get_role(int(v_id)) if v_id else None
        staff_role = guild.get_role(int(staff_id)) if staff_id else None

        me = guild.me
        if not me or not me.guild_permissions.manage_roles:
            return await interaction.followup.send(
                "❌ I lack **Manage Roles** permission.",
                ephemeral=True,
            )

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

                await member.add_roles(
                    uv_role,
                    reason=f"fix_unverified by {interaction.user} ({interaction.user.id})",
                )
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
    # /set_verified
    # ============================================================
    @tree.command(
        name="set_verified",
        description="(Staff) Add/remove the Verified role (break-glass fix).",
    )
    @app_commands.describe(
        user="Mention, ID, username, or display name to update",
        enable="True to add Verified; False to remove",
    )
    async def set_verified_slash(
        interaction: discord.Interaction,
        user: str,
        enable: bool,
    ):
        if not _staff_check(interaction):
            await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
            return

        if not interaction.guild:
            await reply_once(
                interaction,
                {"content": "❌ This command must be used in a server.", "ephemeral": True},
            )
            return

        member = await _require_target_member(interaction, user)
        if member is None:
            return

        role = _role_by_id(interaction.guild, int(VERIFIED_ROLE_ID or 0))
        if not role:
            await reply_once(
                interaction,
                {
                    "content": "❌ VERIFIED_ROLE_ID is not configured or role not found.",
                    "ephemeral": True,
                },
            )
            return

        await _apply_role_change(
            interaction=interaction,
            member=member,
            role=role,
            enable=bool(enable),
            reason=f"Manual verified role change by {interaction.user} ({interaction.user.id})",
        )

    # ============================================================
    # /set_resident
    # ============================================================
    @tree.command(
        name="set_resident",
        description="(Staff) Add/remove the Resident role (break-glass fix).",
    )
    @app_commands.describe(
        user="Mention, ID, username, or display name to update",
        enable="True to add Resident; False to remove",
    )
    async def set_resident_slash(
        interaction: discord.Interaction,
        user: str,
        enable: bool,
    ):
        if not _staff_check(interaction):
            await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
            return

        if not interaction.guild:
            await reply_once(
                interaction,
                {"content": "❌ This command must be used in a server.", "ephemeral": True},
            )
            return

        member = await _require_target_member(interaction, user)
        if member is None:
            return

        role = _role_by_id(interaction.guild, int(RESIDENT_ROLE_ID or 0))
        if not role:
            await reply_once(
                interaction,
                {
                    "content": "❌ RESIDENT_ROLE_ID is not configured or role not found.",
                    "ephemeral": True,
                },
            )
            return

        await _apply_role_change(
            interaction=interaction,
            member=member,
            role=role,
            enable=bool(enable),
            reason=f"Manual resident role change by {interaction.user} ({interaction.user.id})",
        )

    # ============================================================
    # /grant_vr
    # ============================================================
    @tree.command(
        name="grant_vr",
        description="(Staff) Grant Verified + Resident, with optional Stoner/Drunken roles.",
    )
    @app_commands.describe(
        user="Mention, ID, username, or display name to update",
        stoner="Also add the Stoner role (optional)",
        drunken="Also add the Drunken role (optional)",
    )
    async def grant_vr_slash(
        interaction: discord.Interaction,
        user: str,
        stoner: bool = False,
        drunken: bool = False,
    ):
        if not _staff_check(interaction):
            await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
            return

        if not interaction.guild:
            await reply_once(
                interaction,
                {"content": "❌ This command must be used in a server.", "ephemeral": True},
            )
            return

        guild = interaction.guild
        member = await _require_target_member(interaction, user)
        if member is None:
            return

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
            return await interaction.followup.send(
                "❌ I need **Manage Roles** permission.",
                ephemeral=True,
            )

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
            add_list = [r for r in to_add if r and r not in member.roles]
            remove_list = []
            if unverified_role and unverified_role in member.roles:
                remove_list.append(unverified_role)

            if add_list:
                await member.add_roles(
                    *add_list,
                    reason=f"grant_vr by {interaction.user} ({interaction.user.id})",
                )

            if remove_list:
                await member.remove_roles(
                    *remove_list,
                    reason=f"grant_vr cleanup by {interaction.user} ({interaction.user.id})",
                )

            lines = [f"✅ Updated {member.mention}:"]
            lines.append(
                f"- Added: {', '.join(r.mention for r in add_list) if add_list else 'none (already had)'}"
            )
            if unverified_role:
                lines.append(f"- Removed Unverified: {'YES' if remove_list else 'NO'}")
            if stoner and not stoner_role:
                lines.append("- Note: STONER_ROLE_ID not set, skipped.")
            if drunken and not drunken_role:
                lines.append("- Note: DRUNKEN_ROLE_ID not set, skipped.")

            return await interaction.followup.send("\n".join(lines), ephemeral=True)

        except discord.Forbidden:
            return await interaction.followup.send(
                "❌ Forbidden (role hierarchy or missing permissions).",
                ephemeral=True,
            )
        except Exception as e:
            return await interaction.followup.send(f"❌ Failed: {e}", ephemeral=True)

    # ============================================================
    # /verify_diagnose
    # ============================================================
    @tree.command(
        name="verify_diagnose",
        description="(Staff) Deep verification diagnostics for a member.",
    )
    @app_commands.describe(user="Mention, ID, username, or display name to inspect")
    async def verify_diagnose_slash(
        interaction: discord.Interaction,
        user: str,
    ):
        if not _staff_check(interaction):
            await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
            return

        guild = interaction.guild
        if guild is None:
            await reply_once(
                interaction,
                {"content": "❌ This command must be used in a server.", "ephemeral": True},
            )
            return

        member = await _require_target_member(interaction, user)
        if member is None:
            return

        await interaction.response.defer(ephemeral=True)

        verified_role = _role_by_id(guild, int(VERIFIED_ROLE_ID or 0))
        resident_role = _role_by_id(guild, int(RESIDENT_ROLE_ID or 0))
        unverified_role = _role_by_id(guild, int(UNVERIFIED_ROLE_ID or 0))
        staff_role = _role_by_id(guild, int(STAFF_ROLE_ID or 0))

        open_ticket = await _resolve_open_ticket_channel_for_owner(guild, int(member.id))
        vc_channel = _get_vc_channel(guild)
        me = guild.me or await _resolve_member_any(
            guild,
            int(getattr(getattr(bot, "user", None), "id", 0) or 0),
        )

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
        embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=False)
        embed.add_field(
            name="Unverified",
            value="YES" if (unverified_role and unverified_role in member.roles) else "NO",
            inline=True,
        )
        embed.add_field(
            name="Verified",
            value="YES" if (verified_role and verified_role in member.roles) else "NO",
            inline=True,
        )
        embed.add_field(
            name="Resident",
            value="YES" if (resident_role and resident_role in member.roles) else "NO",
            inline=True,
        )
        embed.add_field(
            name="Staff",
            value="YES" if (staff_role and staff_role in member.roles) else "NO",
            inline=True,
        )
        embed.add_field(
            name="Needs Verification",
            value="YES" if _member_needs_verification(member) else "NO",
            inline=True,
        )
        embed.add_field(
            name="Open Ticket",
            value=(open_ticket.mention if open_ticket else "None"),
            inline=False,
        )
        embed.add_field(
            name="Verify Site URL",
            value=f"`{bool(VERIFY_SITE_URL)}`",
            inline=True,
        )
        embed.add_field(
            name="VC Verify Channel",
            value=(vc_channel.mention if vc_channel else "Not configured"),
            inline=True,
        )
        embed.add_field(
            name="Bot Manage Roles",
            value="YES" if can_manage_roles else "NO",
            inline=True,
        )
        embed.add_field(
            name="Bot Kick Members",
            value="YES" if can_kick else "NO",
            inline=True,
        )
        embed.add_field(
            name="Bot Manage VC",
            value=("YES" if vc_manage_ok else f"NO — {vc_manage_reason}"),
            inline=False,
        )

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ============================================================
    # /fix_unverified_member
    # ============================================================
    @tree.command(
        name="fix_unverified_member",
        description="(Staff) Re-add Unverified to a member when it is missing.",
    )
    @app_commands.describe(
        user="Mention, ID, username, or display name to repair",
        remove_verified_conflicts="Also remove Verified/Resident while restoring Unverified",
    )
    async def fix_unverified_member_slash(
        interaction: discord.Interaction,
        user: str,
        remove_verified_conflicts: bool = False,
    ):
        if not _staff_check(interaction):
            await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
            return

        guild = interaction.guild
        if guild is None:
            await reply_once(
                interaction,
                {"content": "❌ This command must be used in a server.", "ephemeral": True},
            )
            return

        member = await _require_target_member(interaction, user)
        if member is None:
            return

        uv_role = _role_by_id(guild, int(UNVERIFIED_ROLE_ID or 0))
        verified_role = _role_by_id(guild, int(VERIFIED_ROLE_ID or 0))
        resident_role = _role_by_id(guild, int(RESIDENT_ROLE_ID or 0))

        if not uv_role:
            await reply_once(
                interaction,
                {
                    "content": "❌ UNVERIFIED_ROLE_ID is not configured or role not found.",
                    "ephemeral": True,
                },
            )
            return

        await interaction.response.defer(ephemeral=True)

        added: list[str] = []
        removed: list[str] = []

        try:
            if remove_verified_conflicts:
                for role in (verified_role, resident_role):
                    if role and role in member.roles:
                        await member.remove_roles(
                            role,
                            reason=f"Fix Unverified by {interaction.user} ({interaction.user.id})",
                        )
                        removed.append(role.name)

            if uv_role not in member.roles:
                await member.add_roles(
                    uv_role,
                    reason=f"Fix Unverified by {interaction.user} ({interaction.user.id})",
                )
                added.append(uv_role.name)

            ticket_ch = await _resolve_open_ticket_channel_for_owner(guild, int(member.id))
            if ticket_ch:
                try:
                    await ensure_verify_ui_present(ticket_ch, reason="fix_unverified_slash")
                except Exception:
                    pass

            lines = [f"✅ Unverified repair complete for {member.mention}."]
            lines.append(f"Added: {', '.join(added) if added else 'nothing'}")
            lines.append(f"Removed: {', '.join(removed) if removed else 'nothing'}")
            if ticket_ch:
                lines.append(f"Ticket: {ticket_ch.mention}")

            await interaction.followup.send("\n".join(lines), ephemeral=True)

        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Forbidden. Check Manage Roles + hierarchy.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ Failed to repair Unverified: {e}",
                ephemeral=True,
            )

    # ============================================================
    # /verify_status
    # ============================================================
    @tree.command(
        name="verify_status",
        description="(Staff) Show a member's verification/resident status.",
    )
    @app_commands.describe(user="Mention, ID, username, or display name to inspect")
    async def verify_status_slash(
        interaction: discord.Interaction,
        user: str,
    ):
        if not _staff_check(interaction):
            await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
            return

        if not interaction.guild:
            await reply_once(
                interaction,
                {"content": "❌ This command must be used in a server.", "ephemeral": True},
            )
            return

        member = await _require_target_member(interaction, user)
        if member is None:
            return

        verified_role = _role_by_id(interaction.guild, int(VERIFIED_ROLE_ID or 0))
        resident_role = _role_by_id(interaction.guild, int(RESIDENT_ROLE_ID or 0))
        unverified_role = _role_by_id(interaction.guild, int(UNVERIFIED_ROLE_ID or 0))

        has_v = bool(verified_role and verified_role in member.roles)
        has_r = bool(resident_role and resident_role in member.roles)
        has_uv = bool(unverified_role and unverified_role in member.roles)

        lines = [
            f"👤 {member.mention} (`{member.id}`)",
            f"⚠️ Unverified role: {'YES' if has_uv else 'NO'}" + (f" ({unverified_role.mention})" if unverified_role else " (not configured)"),
            f"✅ Verified role: {'YES' if has_v else 'NO'}" + (f" ({verified_role.mention})" if verified_role else " (not configured)"),
            f"🏠 Resident role: {'YES' if has_r else 'NO'}" + (f" ({resident_role.mention})" if resident_role else " (not configured)"),
        ]

        await reply_once(
            interaction,
            {"content": "\n".join(lines), "ephemeral": True},
        )
