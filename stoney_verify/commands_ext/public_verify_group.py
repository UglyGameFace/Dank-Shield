from __future__ import annotations

"""Public /verify command family.

Boring/professional command strategy:
- One top-level command: /verify
- Verification role tools live as subcommands
- Uses per-guild DB config from guild_configs through guild_config.py
- Does not rely on deployment .env role IDs for public servers
"""

import asyncio
from typing import Any, Dict, Optional

import discord
from discord import app_commands

from ..globals import now_utc
from ..guild_config import get_guild_config
from ..tickets_new.service import find_open_ticket_for_owner
from ..tickets import find_ticket_owner_retry, is_verification_ticket_channel
from ..transcripts import ensure_verify_ui_present
from .common import _staff_check, require_target_member, reply_once, safe_defer, safe_followup


verify_group = app_commands.Group(
    name="verify",
    description="Verification status and role repair tools.",
)

_REGISTERED = False


# ============================================================
# Helpers
# ============================================================

def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text if text else default
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _truncate(value: Any, limit: int = 1000) -> str:
    text = _safe_str(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


async def _staff_only(interaction: discord.Interaction) -> bool:
    if _staff_check(interaction):
        return True
    await reply_once(interaction, {"content": "❌ Staff only.", "ephemeral": True})
    return False


async def _guild_or_reply(interaction: discord.Interaction) -> Optional[discord.Guild]:
    guild = interaction.guild
    if guild is None:
        await reply_once(interaction, {"content": "❌ This command must be used inside a server.", "ephemeral": True})
        return None
    return guild


async def _guild_roles(guild: discord.Guild) -> Dict[str, Optional[discord.Role]]:
    cfg = await get_guild_config(guild.id, refresh=True)

    def role_for(key: str) -> Optional[discord.Role]:
        rid = _safe_int(cfg.get(key), 0)
        if rid <= 0:
            return None
        role = guild.get_role(rid)
        return role if isinstance(role, discord.Role) else None

    return {
        "unverified": role_for("unverified_role_id"),
        "verified": role_for("verified_role_id"),
        "resident": role_for("resident_role_id"),
        "staff": role_for("staff_role_id"),
        "vc_staff": role_for("vc_staff_role_id"),
    }


def _role_status(member: discord.Member, role: Optional[discord.Role]) -> bool:
    try:
        return bool(role and role in member.roles)
    except Exception:
        return False


def _needs_verification(member: discord.Member, roles: Dict[str, Optional[discord.Role]]) -> bool:
    has_uv = _role_status(member, roles.get("unverified"))
    has_v = _role_status(member, roles.get("verified"))
    has_r = _role_status(member, roles.get("resident"))
    return bool(has_uv and not has_v and not has_r)


def _bot_can_manage_role(guild: discord.Guild, role: Optional[discord.Role]) -> tuple[bool, str]:
    if role is None:
        return False, "role is not configured or was not found"

    me = guild.me
    if me is None:
        return False, "bot member could not be resolved"

    if not me.guild_permissions.manage_roles:
        return False, "bot is missing Manage Roles"

    try:
        if me.top_role <= role and not me.guild_permissions.administrator:
            return False, f"bot role hierarchy is below {role.name}"
    except Exception:
        pass

    return True, "ok"


async def _apply_role_change(
    *,
    interaction: discord.Interaction,
    member: discord.Member,
    role: discord.Role,
    enable: bool,
    reason: str,
) -> None:
    guild = interaction.guild
    if guild is None:
        return await reply_once(interaction, {"content": "❌ Guild only.", "ephemeral": True})

    ok, why = _bot_can_manage_role(guild, role)
    if not ok:
        return await reply_once(interaction, {"content": f"❌ I cannot manage that role: {why}.", "ephemeral": True})

    try:
        if enable:
            if role in member.roles:
                return await reply_once(interaction, {"content": f"✅ {member.mention} already has {role.mention}.", "ephemeral": True})
            await member.add_roles(role, reason=reason)
            return await reply_once(interaction, {"content": f"✅ Added {role.mention} to {member.mention}.", "ephemeral": True})

        if role not in member.roles:
            return await reply_once(interaction, {"content": f"✅ {member.mention} does not have {role.mention}.", "ephemeral": True})
        await member.remove_roles(role, reason=reason)
        return await reply_once(interaction, {"content": f"✅ Removed {role.mention} from {member.mention}.", "ephemeral": True})
    except discord.Forbidden:
        return await reply_once(interaction, {"content": "❌ Forbidden. Check Manage Roles and role hierarchy.", "ephemeral": True})
    except Exception as e:
        return await reply_once(interaction, {"content": f"❌ Failed: {_truncate(e, 300)}", "ephemeral": True})


async def _resolve_open_ticket_channel_for_owner(guild: discord.Guild, owner_id: int) -> Optional[discord.TextChannel]:
    try:
        row = await find_open_ticket_for_owner(guild_id=guild.id, owner_id=owner_id)
    except Exception:
        row = None

    if not isinstance(row, dict):
        return None

    ch_id = _safe_int(row.get("discord_thread_id") or row.get("channel_id"), 0)
    if ch_id <= 0:
        return None

    ch = guild.get_channel(ch_id)
    if isinstance(ch, discord.TextChannel):
        return ch

    try:
        fetched = await guild.fetch_channel(ch_id)
        return fetched if isinstance(fetched, discord.TextChannel) else None
    except Exception:
        return None


async def _maybe_repair_verify_ui_for_member(guild: discord.Guild, member: discord.Member) -> Optional[discord.TextChannel]:
    ticket_ch = await _resolve_open_ticket_channel_for_owner(guild, int(member.id))
    if ticket_ch is None:
        return None

    try:
        if is_verification_ticket_channel(ticket_ch):
            await ensure_verify_ui_present(ticket_ch, reason="verify_group_repair")
    except Exception:
        pass

    return ticket_ch


def _status_lines(member: discord.Member, roles: Dict[str, Optional[discord.Role]]) -> list[str]:
    uv = roles.get("unverified")
    verified = roles.get("verified")
    resident = roles.get("resident")
    staff = roles.get("staff")

    return [
        f"👤 {member.mention} (`{member.id}`)",
        f"⚠️ Unverified: {'YES' if _role_status(member, uv) else 'NO'}" + (f" ({uv.mention})" if uv else " (not configured)"),
        f"✅ Verified: {'YES' if _role_status(member, verified) else 'NO'}" + (f" ({verified.mention})" if verified else " (not configured)"),
        f"🏠 Resident: {'YES' if _role_status(member, resident) else 'NO'}" + (f" ({resident.mention})" if resident else " (not configured)"),
        f"🛡️ Staff: {'YES' if _role_status(member, staff) else 'NO'}" + (f" ({staff.mention})" if staff else " (not configured)"),
        f"🧭 Needs verification: {'YES' if _needs_verification(member, roles) else 'NO'}",
    ]


# ============================================================
# /verify subcommands
# ============================================================

@verify_group.command(name="status", description="Show a member's verification/resident status.")
@app_commands.describe(user="Mention, ID, username, or display name to inspect")
async def verify_status(interaction: discord.Interaction, user: str) -> None:
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return

    member = await require_target_member(interaction, user)
    if member is None:
        return

    roles = await _guild_roles(guild)
    await reply_once(interaction, {"content": "\n".join(_status_lines(member, roles)), "ephemeral": True})


@verify_group.command(name="diagnose", description="Deep verification diagnostics for a member.")
@app_commands.describe(user="Mention, ID, username, or display name to inspect")
async def verify_diagnose(interaction: discord.Interaction, user: str) -> None:
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return

    member = await require_target_member(interaction, user)
    if member is None:
        return

    await safe_defer(interaction, ephemeral=True)
    roles = await _guild_roles(guild)
    open_ticket = await _resolve_open_ticket_channel_for_owner(guild, int(member.id))
    me = guild.me

    embed = discord.Embed(title="🩺 Verification Diagnose", color=discord.Color.blurple(), timestamp=now_utc())
    embed.add_field(name="Member", value=f"{member.mention} (`{member.id}`)", inline=False)

    for line in _status_lines(member, roles)[1:]:
        name, _, value = line.partition(":")
        embed.add_field(name=name.strip(), value=value.strip() or "unknown", inline=True)

    embed.add_field(name="Open Ticket", value=open_ticket.mention if open_ticket else "None", inline=False)
    embed.add_field(name="Bot Manage Roles", value="YES" if bool(me and me.guild_permissions.manage_roles) else "NO", inline=True)
    embed.add_field(name="Bot Kick Members", value="YES" if bool(me and me.guild_permissions.kick_members) else "NO", inline=True)

    hierarchy_lines = []
    for label, role in roles.items():
        if role is None:
            hierarchy_lines.append(f"{label}: not configured")
            continue
        ok, why = _bot_can_manage_role(guild, role)
        hierarchy_lines.append(f"{label}: {'OK' if ok else why}")
    embed.add_field(name="Role Hierarchy", value=_truncate("\n".join(hierarchy_lines), 1024), inline=False)

    await safe_followup(interaction, embed=embed, ephemeral=True)


@verify_group.command(name="set-verified", description="Add or remove the Verified role.")
@app_commands.describe(user="Mention, ID, username, or display name", enable="True to add Verified; False to remove")
async def verify_set_verified(interaction: discord.Interaction, user: str, enable: bool) -> None:
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return
    member = await require_target_member(interaction, user)
    if member is None:
        return
    roles = await _guild_roles(guild)
    role = roles.get("verified")
    if role is None:
        return await reply_once(interaction, {"content": "❌ Verified role is not configured for this server.", "ephemeral": True})
    await _apply_role_change(interaction=interaction, member=member, role=role, enable=enable, reason=f"/verify set-verified by {interaction.user} ({interaction.user.id})")


@verify_group.command(name="set-resident", description="Add or remove the Resident role.")
@app_commands.describe(user="Mention, ID, username, or display name", enable="True to add Resident; False to remove")
async def verify_set_resident(interaction: discord.Interaction, user: str, enable: bool) -> None:
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return
    member = await require_target_member(interaction, user)
    if member is None:
        return
    roles = await _guild_roles(guild)
    role = roles.get("resident")
    if role is None:
        return await reply_once(interaction, {"content": "❌ Resident role is not configured for this server.", "ephemeral": True})
    await _apply_role_change(interaction=interaction, member=member, role=role, enable=enable, reason=f"/verify set-resident by {interaction.user} ({interaction.user.id})")


@verify_group.command(name="grant", description="Grant Verified + Resident and remove Unverified.")
@app_commands.describe(user="Mention, ID, username, or display name")
async def verify_grant(interaction: discord.Interaction, user: str) -> None:
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return
    member = await require_target_member(interaction, user)
    if member is None:
        return

    await safe_defer(interaction, ephemeral=True)
    roles = await _guild_roles(guild)
    verified = roles.get("verified")
    resident = roles.get("resident")
    unverified = roles.get("unverified")

    missing = []
    if verified is None:
        missing.append("Verified")
    if resident is None:
        missing.append("Resident")
    if missing:
        return await safe_followup(interaction, f"❌ Missing configured role(s): {', '.join(missing)}.", ephemeral=True)

    for role in (verified, resident, unverified):
        if role is None:
            continue
        ok, why = _bot_can_manage_role(guild, role)
        if not ok:
            return await safe_followup(interaction, f"❌ I cannot manage {role.mention}: {why}.", ephemeral=True)

    added: list[discord.Role] = []
    removed: list[discord.Role] = []

    try:
        to_add = [role for role in (verified, resident) if role is not None and role not in member.roles]
        if to_add:
            await member.add_roles(*to_add, reason=f"/verify grant by {interaction.user} ({interaction.user.id})")
            added.extend(to_add)

        if unverified is not None and unverified in member.roles:
            await member.remove_roles(unverified, reason=f"/verify grant cleanup by {interaction.user} ({interaction.user.id})")
            removed.append(unverified)

        ticket_ch = await _maybe_repair_verify_ui_for_member(guild, member)

        lines = [f"✅ Updated {member.mention}."]
        lines.append("Added: " + (", ".join(r.mention for r in added) if added else "nothing; already had roles"))
        lines.append("Removed: " + (", ".join(r.mention for r in removed) if removed else "nothing"))
        if ticket_ch:
            lines.append(f"Ticket: {ticket_ch.mention}")
        await safe_followup(interaction, "\n".join(lines), ephemeral=True)
    except discord.Forbidden:
        await safe_followup(interaction, "❌ Forbidden. Check Manage Roles and role hierarchy.", ephemeral=True)
    except Exception as e:
        await safe_followup(interaction, f"❌ Failed: {_truncate(e, 300)}", ephemeral=True)


@verify_group.command(name="fix-member", description="Re-add Unverified to a member when it is missing.")
@app_commands.describe(user="Mention, ID, username, or display name", remove_conflicts="Also remove Verified/Resident while restoring Unverified")
async def verify_fix_member(interaction: discord.Interaction, user: str, remove_conflicts: bool = False) -> None:
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return
    member = await require_target_member(interaction, user)
    if member is None:
        return

    await safe_defer(interaction, ephemeral=True)
    roles = await _guild_roles(guild)
    unverified = roles.get("unverified")
    verified = roles.get("verified")
    resident = roles.get("resident")

    if unverified is None:
        return await safe_followup(interaction, "❌ Unverified role is not configured for this server.", ephemeral=True)

    for role in (unverified, verified, resident):
        if role is None:
            continue
        ok, why = _bot_can_manage_role(guild, role)
        if not ok:
            return await safe_followup(interaction, f"❌ I cannot manage {role.mention}: {why}.", ephemeral=True)

    added: list[discord.Role] = []
    removed: list[discord.Role] = []

    try:
        if remove_conflicts:
            to_remove = [role for role in (verified, resident) if role is not None and role in member.roles]
            if to_remove:
                await member.remove_roles(*to_remove, reason=f"/verify fix-member conflict cleanup by {interaction.user} ({interaction.user.id})")
                removed.extend(to_remove)

        if unverified not in member.roles:
            await member.add_roles(unverified, reason=f"/verify fix-member by {interaction.user} ({interaction.user.id})")
            added.append(unverified)

        ticket_ch = await _maybe_repair_verify_ui_for_member(guild, member)

        lines = [f"✅ Unverified repair complete for {member.mention}."]
        lines.append("Added: " + (", ".join(r.mention for r in added) if added else "nothing"))
        lines.append("Removed: " + (", ".join(r.mention for r in removed) if removed else "nothing"))
        if ticket_ch:
            lines.append(f"Ticket: {ticket_ch.mention}")
        await safe_followup(interaction, "\n".join(lines), ephemeral=True)
    except discord.Forbidden:
        await safe_followup(interaction, "❌ Forbidden. Check Manage Roles and role hierarchy.", ephemeral=True)
    except Exception as e:
        await safe_followup(interaction, f"❌ Failed: {_truncate(e, 300)}", ephemeral=True)


@verify_group.command(name="repair-unverified", description="Assign Unverified to members who are not Verified/Resident.")
async def verify_repair_unverified(interaction: discord.Interaction) -> None:
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return

    await safe_defer(interaction, ephemeral=True)
    roles = await _guild_roles(guild)
    unverified = roles.get("unverified")
    verified = roles.get("verified")
    resident = roles.get("resident")
    staff = roles.get("staff")

    if unverified is None:
        return await safe_followup(interaction, "❌ Unverified role is not configured for this server.", ephemeral=True)

    ok, why = _bot_can_manage_role(guild, unverified)
    if not ok:
        return await safe_followup(interaction, f"❌ I cannot manage Unverified: {why}.", ephemeral=True)

    try:
        await guild.chunk(cache=True)
    except Exception:
        pass

    added = 0
    skipped_verified = 0
    skipped_resident = 0
    skipped_staff = 0
    skipped_bots = 0
    already_ok = 0
    failed = 0

    for index, member in enumerate(list(guild.members or []), start=1):
        try:
            if getattr(member, "bot", False):
                skipped_bots += 1
                continue
            if staff and staff in member.roles:
                skipped_staff += 1
                continue
            if verified and verified in member.roles:
                skipped_verified += 1
                continue
            if resident and resident in member.roles:
                skipped_resident += 1
                continue
            if unverified in member.roles:
                already_ok += 1
                continue
            await member.add_roles(unverified, reason=f"/verify repair-unverified by {interaction.user} ({interaction.user.id})")
            added += 1
        except Exception:
            failed += 1

        if index % 10 == 0:
            try:
                await asyncio.sleep(0.4)
            except Exception:
                pass

    await safe_followup(
        interaction,
        "✅ **Unverified repair complete**\n"
        f"- Added Unverified: **{added}**\n"
        f"- Already had Unverified: **{already_ok}**\n"
        f"- Skipped Verified: **{skipped_verified}**\n"
        f"- Skipped Resident: **{skipped_resident}**\n"
        f"- Skipped Staff: **{skipped_staff}**\n"
        f"- Skipped Bots: **{skipped_bots}**\n"
        f"- Failed: **{failed}**",
        ephemeral=True,
    )


# ============================================================
# Registration
# ============================================================

def register_public_verify_group_commands(bot: Any, tree: Any) -> None:
    global _REGISTERED
    _ = bot
    if _REGISTERED:
        return

    try:
        existing = tree.get_command("verify", guild=None)
    except Exception:
        existing = None

    if existing is None:
        tree.add_command(verify_group)
        _REGISTERED = True
        try:
            print("✅ public_verify_group: registered /verify grouped commands")
        except Exception:
            pass
        return

    _REGISTERED = True
    try:
        print("✅ public_verify_group: /verify already registered")
    except Exception:
        pass


__all__ = ["register_public_verify_group_commands", "verify_group"]
