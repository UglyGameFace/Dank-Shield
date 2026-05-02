from __future__ import annotations

"""Public /verify command family.

Production command rules:
- One top-level command: /verify
- Member-targeting tools use Discord's native member picker
- Heavy repair tools acknowledge immediately so Discord never shows
  "The application did not respond"
- Existing customer roles are valid. Stoney does not require roles to have been
  created by Stoney; it can discover usable roles by name and save them.
- No-role users are never left stuck because a server forgot to save/create an
  Unverified role. Repair tools discover one, or create a safe one if needed.
- Uses per-guild DB config from guild_configs through guild_config.py
"""

import asyncio
import re
from typing import Any, Dict, Optional

import discord
from discord import app_commands

from ..globals import now_utc
from ..guild_config import get_guild_config, upsert_guild_config
from ..tickets import is_verification_ticket_channel
from ..tickets_new.service import find_open_ticket_for_owner
from ..transcripts import ensure_verify_ui_present
from .common import _staff_check


verify_group = app_commands.Group(
    name="verify",
    description="Verification status and role repair tools.",
)

_REGISTERED = False

_ROLE_ALIASES: dict[str, tuple[str, ...]] = {
    "unverified": (
        "unverified",
        "not verified",
        "un-verified",
        "pending verification",
        "pending",
        "verify required",
        "needs verification",
        "new member",
        "newcomer",
    ),
    "verified": (
        "verified",
        "verify complete",
        "verified member",
    ),
    "resident": (
        "resident",
        "member",
        "members",
        "community member",
    ),
    "staff": (
        "staff",
        "ticket staff",
        "support team",
        "support",
        "mod",
        "moderator",
        "admin",
        "dickheads",
    ),
    "vc_staff": (
        "vc staff",
        "voice staff",
        "ticket staff",
        "support team",
        "support",
        "staff",
    ),
}

_ROLE_CONFIG_KEYS: dict[str, str] = {
    "unverified": "unverified_role_id",
    "verified": "verified_role_id",
    "resident": "resident_role_id",
    "staff": "staff_role_id",
    "vc_staff": "vc_staff_role_id",
}


# ============================================================
# Shared helpers
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
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _truncate(value: Any, limit: int = 1000) -> str:
    text = _safe_str(value)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _cfg_value(cfg: Any, key: str) -> Any:
    try:
        if hasattr(cfg, "get"):
            return cfg.get(key)
    except Exception:
        pass
    try:
        return getattr(cfg, key, None)
    except Exception:
        return None


def _normalize_role_name(name: Any) -> str:
    text = _safe_str(name).lower()
    text = re.sub(r"[^a-z0-9\s_\-]+", " ", text)
    text = re.sub(r"[\s_\-]+", " ", text).strip()
    return text


def _find_existing_role_by_alias(guild: discord.Guild, logical_name: str) -> Optional[discord.Role]:
    aliases = tuple(_normalize_role_name(x) for x in _ROLE_ALIASES.get(logical_name, (logical_name,)) if _safe_str(x))
    aliases = tuple(x for x in aliases if x)
    if not aliases:
        return None

    try:
        roles = [role for role in guild.roles if not getattr(role, "is_default", lambda: False)()]
    except Exception:
        roles = list(getattr(guild, "roles", []) or [])

    # Exact normalized name first.
    for role in roles:
        role_name = _normalize_role_name(getattr(role, "name", ""))
        if role_name in aliases:
            return role

    # Then contains. This supports names like "❌ Unverified" or "✅ Verified".
    for role in roles:
        role_name = _normalize_role_name(getattr(role, "name", ""))
        if any(alias and alias in role_name for alias in aliases):
            return role

    return None


async def _ack(interaction: discord.Interaction) -> None:
    """Acknowledge fast enough to avoid Discord interaction timeouts."""
    try:
        if not interaction.response.is_done():
            try:
                await interaction.response.defer(ephemeral=True, thinking=True)
            except TypeError:
                await interaction.response.defer(ephemeral=True)
    except Exception as e:
        try:
            print(f"⚠️ public_verify_group defer failed: {e!r}")
        except Exception:
            pass


async def _send(interaction: discord.Interaction, content: Optional[str] = None, **kwargs: Any) -> None:
    payload = dict(kwargs)
    if content is not None:
        payload["content"] = content
    payload.setdefault("ephemeral", True)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(**payload)
        else:
            await interaction.response.send_message(**payload)
    except Exception:
        try:
            await interaction.followup.send(**payload)
        except Exception:
            pass


async def _staff_only(interaction: discord.Interaction) -> bool:
    try:
        if _staff_check(interaction):
            return True
    except Exception:
        pass
    await _send(interaction, "❌ Staff only.")
    return False


async def _guild_or_reply(interaction: discord.Interaction) -> Optional[discord.Guild]:
    guild = interaction.guild
    if guild is None:
        await _send(interaction, "❌ This command must be used inside a server.")
        return None
    return guild


async def _save_discovered_role(guild: discord.Guild, config_key: str, role: discord.Role, *, source: str) -> None:
    try:
        await upsert_guild_config(guild.id, {config_key: str(role.id)})
        try:
            print(
                "✅ public_verify_group saved verification role "
                f"guild={guild.id} key={config_key} role={role.name}:{role.id} source={source}"
            )
        except Exception:
            pass
    except Exception as e:
        try:
            print(
                "⚠️ public_verify_group could not save verification role "
                f"guild={guild.id} key={config_key} role={role.name}:{role.id} source={source} error={e!r}"
            )
        except Exception:
            pass


async def _guild_roles(guild: discord.Guild) -> Dict[str, Optional[discord.Role]]:
    """Resolve configured roles, then discover existing customer-created roles.

    Stoney must not require roles to have been created by Stoney. If a server
    already has an Unverified/Verified/Resident/etc. role, the repair tools can
    use it and save the discovered role id back to guild_configs.
    """
    cfg = await get_guild_config(guild.id, refresh=True)
    discovered_patch: dict[str, str] = {}
    discovery_notes: list[str] = []

    def role_for_config_key(key: str) -> Optional[discord.Role]:
        rid = _safe_int(_cfg_value(cfg, key), 0)
        if rid <= 0:
            return None
        role = guild.get_role(rid)
        return role if isinstance(role, discord.Role) else None

    roles: Dict[str, Optional[discord.Role]] = {}
    for logical_name, config_key in _ROLE_CONFIG_KEYS.items():
        role = role_for_config_key(config_key)
        if role is None:
            discovered = _find_existing_role_by_alias(guild, logical_name)
            if discovered is not None:
                role = discovered
                discovered_patch[config_key] = str(discovered.id)
                discovery_notes.append(f"{logical_name}={discovered.name}:{discovered.id}")
        roles[logical_name] = role

    # If VC staff is missing but staff exists, use staff as the safe fallback.
    if roles.get("vc_staff") is None and roles.get("staff") is not None:
        staff_role = roles.get("staff")
        roles["vc_staff"] = staff_role
        if staff_role is not None:
            discovered_patch.setdefault("vc_staff_role_id", str(staff_role.id))

    if discovered_patch:
        try:
            await upsert_guild_config(guild.id, discovered_patch)
            try:
                print(
                    "✅ public_verify_group saved discovered existing verification roles "
                    f"guild={guild.id} patch={discovered_patch} notes={discovery_notes}"
                )
            except Exception:
                pass
        except Exception as e:
            try:
                print(
                    "⚠️ public_verify_group discovered existing verification roles but could not save "
                    f"guild={guild.id} patch={discovered_patch} error={e!r}"
                )
            except Exception:
                pass

    return roles


async def _ensure_unverified_role(guild: discord.Guild, roles: Dict[str, Optional[discord.Role]]) -> tuple[Optional[discord.Role], str, bool]:
    """Return a usable Unverified role, creating one if the server has none.

    The purpose of /verify repair-unverified is to fix no-role members. That
    should not fail just because the Unverified role was never saved or never
    existed. If discovery fails, create a plain @Unverified role, save it, and
    let the command assign it.
    """
    existing = roles.get("unverified")
    if isinstance(existing, discord.Role):
        return existing, f"using existing role {existing.mention}", False

    # Try one last live discovery in case cache/config was stale.
    discovered = _find_existing_role_by_alias(guild, "unverified")
    if isinstance(discovered, discord.Role):
        roles["unverified"] = discovered
        await _save_discovered_role(guild, "unverified_role_id", discovered, source="verify_repair_live_discovery")
        return discovered, f"auto-discovered existing role {discovered.mention}", False

    me = guild.me
    if me is None:
        return None, "bot member could not be resolved, so I could not create @Unverified", False
    if not me.guild_permissions.manage_roles:
        return None, "bot is missing Manage Roles, so I could not create @Unverified", False

    try:
        created = await guild.create_role(
            name="Unverified",
            permissions=discord.Permissions.none(),
            mentionable=False,
            hoist=False,
            reason="Stoney Verify repair created missing Unverified role for no-role members",
        )
        roles["unverified"] = created
        await _save_discovered_role(guild, "unverified_role_id", created, source="verify_repair_auto_create")
        return created, f"created and saved {created.mention}", True
    except discord.Forbidden:
        return None, "Forbidden while creating @Unverified. Move Stoney's bot role higher and grant Manage Roles.", False
    except Exception as e:
        return None, f"failed to create @Unverified: {type(e).__name__}: {_truncate(e, 240)}", False


def _role_status(member: discord.Member, role: Optional[discord.Role]) -> bool:
    try:
        return bool(role and role in member.roles)
    except Exception:
        return False


def _needs_verification(member: discord.Member, roles: Dict[str, Optional[discord.Role]]) -> bool:
    has_unverified = _role_status(member, roles.get("unverified"))
    has_verified = _role_status(member, roles.get("verified"))
    has_resident = _role_status(member, roles.get("resident"))
    return bool(has_unverified and not has_verified and not has_resident)


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
        return await _send(interaction, "❌ Guild only.")

    ok, why = _bot_can_manage_role(guild, role)
    if not ok:
        return await _send(interaction, f"❌ I cannot manage {role.mention}: {why}.")

    try:
        if enable:
            if role in member.roles:
                return await _send(interaction, f"✅ {member.mention} already has {role.mention}.")
            await member.add_roles(role, reason=reason)
            return await _send(interaction, f"✅ Added {role.mention} to {member.mention}.")

        if role not in member.roles:
            return await _send(interaction, f"✅ {member.mention} does not have {role.mention}.")
        await member.remove_roles(role, reason=reason)
        return await _send(interaction, f"✅ Removed {role.mention} from {member.mention}.")
    except discord.Forbidden:
        return await _send(interaction, "❌ Forbidden. Check Manage Roles and role hierarchy.")
    except Exception as e:
        return await _send(interaction, f"❌ Failed: {_truncate(e, 300)}")


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
        f"⚠️ Unverified: {'YES' if _role_status(member, uv) else 'NO'}" + (f" ({uv.mention})" if uv else " (not configured / no matching existing role found)"),
        f"✅ Verified: {'YES' if _role_status(member, verified) else 'NO'}" + (f" ({verified.mention})" if verified else " (not configured / no matching existing role found)"),
        f"🏠 Resident: {'YES' if _role_status(member, resident) else 'NO'}" + (f" ({resident.mention})" if resident else " (not configured / no matching existing role found)"),
        f"🛡️ Staff: {'YES' if _role_status(member, staff) else 'NO'}" + (f" ({staff.mention})" if staff else " (not configured / no matching existing role found)"),
        f"🧭 Needs verification: {'YES' if _needs_verification(member, roles) else 'NO'}",
    ]


def _missing_role_message(role_name: str, aliases: tuple[str, ...]) -> str:
    sample = ", ".join(f"`{x}`" for x in aliases[:5])
    return (
        f"❌ {role_name} role is not configured and I could not auto-discover an existing role.\n"
        f"Rename/select a role like {sample}, then run `/stoney setup` → Existing Server, or run this command again."
    )


# ============================================================
# /verify subcommands
# ============================================================

@verify_group.command(name="status", description="Show a member's verification/resident status.")
@app_commands.describe(user="Choose the member to inspect")
async def verify_status(interaction: discord.Interaction, user: discord.Member) -> None:
    await _ack(interaction)
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return
    roles = await _guild_roles(guild)
    await _send(interaction, "\n".join(_status_lines(user, roles)))


@verify_group.command(name="diagnose", description="Deep verification diagnostics for a member.")
@app_commands.describe(user="Choose the member to inspect")
async def verify_diagnose(interaction: discord.Interaction, user: discord.Member) -> None:
    await _ack(interaction)
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return

    roles = await _guild_roles(guild)
    open_ticket = await _resolve_open_ticket_channel_for_owner(guild, int(user.id))
    me = guild.me

    embed = discord.Embed(title="🩺 Verification Diagnose", color=discord.Color.blurple(), timestamp=now_utc())
    embed.add_field(name="Member", value=f"{user.mention} (`{user.id}`)", inline=False)
    for line in _status_lines(user, roles)[1:]:
        name, _, value = line.partition(":")
        embed.add_field(name=name.strip(), value=value.strip() or "unknown", inline=True)
    embed.add_field(name="Open Ticket", value=open_ticket.mention if open_ticket else "None", inline=False)
    embed.add_field(name="Bot Manage Roles", value="YES" if bool(me and me.guild_permissions.manage_roles) else "NO", inline=True)
    embed.add_field(name="Bot Kick Members", value="YES" if bool(me and me.guild_permissions.kick_members) else "NO", inline=True)

    hierarchy_lines = []
    for label, role in roles.items():
        if role is None:
            hierarchy_lines.append(f"{label}: not configured / not found")
            continue
        ok, why = _bot_can_manage_role(guild, role)
        hierarchy_lines.append(f"{label}: {'OK' if ok else why}")
    embed.add_field(name="Role Hierarchy", value=_truncate("\n".join(hierarchy_lines), 1024), inline=False)
    await _send(interaction, embed=embed)


@verify_group.command(name="set-verified", description="Add or remove the Verified role.")
@app_commands.describe(user="Choose the member", enable="True to add Verified; False to remove")
async def verify_set_verified(interaction: discord.Interaction, user: discord.Member, enable: bool) -> None:
    await _ack(interaction)
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return
    role = (await _guild_roles(guild)).get("verified")
    if role is None:
        return await _send(interaction, _missing_role_message("Verified", _ROLE_ALIASES["verified"]))
    await _apply_role_change(interaction=interaction, member=user, role=role, enable=enable, reason=f"/verify set-verified by {interaction.user} ({interaction.user.id})")


@verify_group.command(name="set-resident", description="Add or remove the Resident role.")
@app_commands.describe(user="Choose the member", enable="True to add Resident; False to remove")
async def verify_set_resident(interaction: discord.Interaction, user: discord.Member, enable: bool) -> None:
    await _ack(interaction)
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return
    role = (await _guild_roles(guild)).get("resident")
    if role is None:
        return await _send(interaction, _missing_role_message("Resident/Member", _ROLE_ALIASES["resident"]))
    await _apply_role_change(interaction=interaction, member=user, role=role, enable=enable, reason=f"/verify set-resident by {interaction.user} ({interaction.user.id})")


@verify_group.command(name="grant-vr", description="Grant Verified + Resident and remove Unverified.")
@app_commands.describe(user="Choose the member")
async def verify_grant_vr(interaction: discord.Interaction, user: discord.Member) -> None:
    await _ack(interaction)
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return

    roles = await _guild_roles(guild)
    verified = roles.get("verified")
    resident = roles.get("resident")
    unverified = roles.get("unverified")
    missing = []
    if verified is None:
        missing.append("Verified")
    if resident is None:
        missing.append("Resident/Member")
    if missing:
        return await _send(
            interaction,
            "❌ Missing configured/discoverable role(s): " + ", ".join(missing) + ".\n"
            "Use `/stoney setup` → Existing Server to choose your existing roles, or rename roles to common names like `Verified`, `Resident`, and `Unverified`.",
        )

    for role in (verified, resident, unverified):
        if role is None:
            continue
        ok, why = _bot_can_manage_role(guild, role)
        if not ok:
            return await _send(interaction, f"❌ I cannot manage {role.mention}: {why}.")

    added: list[discord.Role] = []
    removed: list[discord.Role] = []
    try:
        to_add = [role for role in (verified, resident) if role is not None and role not in user.roles]
        if to_add:
            await user.add_roles(*to_add, reason=f"/verify grant-vr by {interaction.user} ({interaction.user.id})")
            added.extend(to_add)
        if unverified is not None and unverified in user.roles:
            await user.remove_roles(unverified, reason=f"/verify grant-vr cleanup by {interaction.user} ({interaction.user.id})")
            removed.append(unverified)
        ticket_ch = await _maybe_repair_verify_ui_for_member(guild, user)
        lines = [f"✅ Updated {user.mention}."]
        lines.append("Added: " + (", ".join(r.mention for r in added) if added else "nothing; already had roles"))
        lines.append("Removed: " + (", ".join(r.mention for r in removed) if removed else "nothing"))
        if ticket_ch:
            lines.append(f"Ticket: {ticket_ch.mention}")
        await _send(interaction, "\n".join(lines))
    except discord.Forbidden:
        await _send(interaction, "❌ Forbidden. Check Manage Roles and role hierarchy.")
    except Exception as e:
        await _send(interaction, f"❌ Failed: {_truncate(e, 300)}")


@verify_group.command(name="fix-member", description="Re-add Unverified to a member when it is missing.")
@app_commands.describe(user="Choose the member", remove_conflicts="Also remove Verified/Resident while restoring Unverified")
async def verify_fix_member(interaction: discord.Interaction, user: discord.Member, remove_conflicts: bool = False) -> None:
    await _ack(interaction)
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return

    roles = await _guild_roles(guild)
    unverified, role_detail, created_role = await _ensure_unverified_role(guild, roles)
    verified = roles.get("verified")
    resident = roles.get("resident")
    if unverified is None:
        return await _send(interaction, f"❌ I could not prepare an Unverified role: {role_detail}")

    for role in (unverified, verified, resident):
        if role is None:
            continue
        ok, why = _bot_can_manage_role(guild, role)
        if not ok:
            return await _send(interaction, f"❌ I cannot manage {role.mention}: {why}.")

    added: list[discord.Role] = []
    removed: list[discord.Role] = []
    try:
        if remove_conflicts:
            to_remove = [role for role in (verified, resident) if role is not None and role in user.roles]
            if to_remove:
                await user.remove_roles(*to_remove, reason=f"/verify fix-member conflict cleanup by {interaction.user} ({interaction.user.id})")
                removed.extend(to_remove)
        if unverified not in user.roles:
            await user.add_roles(unverified, reason=f"/verify fix-member by {interaction.user} ({interaction.user.id})")
            added.append(unverified)
        ticket_ch = await _maybe_repair_verify_ui_for_member(guild, user)
        lines = [f"✅ Unverified repair complete for {user.mention}."]
        lines.append("Using role: " + unverified.mention)
        lines.append("Role source: " + role_detail)
        if created_role:
            lines.append("Created role: yes")
        lines.append("Added: " + (", ".join(r.mention for r in added) if added else "nothing"))
        lines.append("Removed: " + (", ".join(r.mention for r in removed) if removed else "nothing"))
        if ticket_ch:
            lines.append(f"Ticket: {ticket_ch.mention}")
        await _send(interaction, "\n".join(lines))
    except discord.Forbidden:
        await _send(interaction, "❌ Forbidden. Check Manage Roles and role hierarchy.")
    except Exception as e:
        await _send(interaction, f"❌ Failed: {_truncate(e, 300)}")


@verify_group.command(name="repair-unverified", description="Assign Unverified to members who are not Verified/Resident.")
async def verify_repair_unverified(interaction: discord.Interaction) -> None:
    await _ack(interaction)
    try:
        if not await _staff_only(interaction):
            return
        guild = await _guild_or_reply(interaction)
        if guild is None:
            return

        roles = await _guild_roles(guild)
        unverified, role_detail, created_role = await _ensure_unverified_role(guild, roles)
        verified = roles.get("verified")
        resident = roles.get("resident")
        staff = roles.get("staff")

        if unverified is None:
            return await _send(interaction, f"❌ I could not prepare an Unverified role: {role_detail}")

        ok, why = _bot_can_manage_role(guild, unverified)
        if not ok:
            return await _send(interaction, f"❌ I found {unverified.mention}, but I cannot manage it: {why}.")

        chunk_ok = False
        try:
            await asyncio.wait_for(guild.chunk(cache=True), timeout=6.0)
            chunk_ok = True
        except Exception as e:
            try:
                print(f"⚠️ /verify repair-unverified member chunk failed guild={guild.id}: {e!r}")
            except Exception:
                pass

        members = list(guild.members or [])
        added = 0
        skipped_verified = 0
        skipped_resident = 0
        skipped_staff = 0
        skipped_bots = 0
        already_ok = 0
        failed = 0

        for index, member in enumerate(members, start=1):
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

            if index % 5 == 0:
                await asyncio.sleep(0.35)

        cache_note = "complete/member chunk succeeded" if chunk_ok else "partial/member chunk failed or timed out"
        await _send(
            interaction,
            "✅ **Unverified repair complete**\n"
            f"- Using role: {unverified.mention}\n"
            f"- Role source: **{role_detail}**\n"
            f"- Created role: **{'yes' if created_role else 'no'}**\n"
            f"- Members scanned: **{len(members)}** (`{cache_note}`)\n"
            f"- Added Unverified: **{added}**\n"
            f"- Already had Unverified: **{already_ok}**\n"
            f"- Skipped Verified: **{skipped_verified}**\n"
            f"- Skipped Resident: **{skipped_resident}**\n"
            f"- Skipped Staff: **{skipped_staff}**\n"
            f"- Skipped Bots: **{skipped_bots}**\n"
            f"- Failed: **{failed}**"
            + ("\n\n⚠️ Member cache may be partial. Use `/verify fix-member` for a specific user if needed." if not chunk_ok else ""),
        )
    except Exception as e:
        await _send(interaction, f"❌ Repair crashed before completion: `{type(e).__name__}: {_truncate(e, 300)}`")


# ============================================================
# Registration
# ============================================================

def register_public_verify_group_commands(bot: Any, tree: Any) -> None:
    global _REGISTERED
    _ = bot
    if _REGISTERED:
        return

    removed: list[str] = []
    for old_name in (
        "fix_unverified",
        "set_verified",
        "set_resident",
        "grant_vr",
        "verify_diagnose",
        "fix_unverified_member",
        "verify_status",
    ):
        try:
            if tree.get_command(old_name, guild=None) is not None:
                tree.remove_command(old_name, guild=None)
                removed.append(old_name)
        except Exception:
            pass

    try:
        existing = tree.get_command("verify", guild=None)
    except Exception:
        existing = None

    if existing is None:
        tree.add_command(verify_group)
        _REGISTERED = True
        try:
            suffix = f" removed_legacy={removed}" if removed else ""
            print(f"✅ public_verify_group: registered /verify grouped commands with no-role auto-repair{suffix}")
        except Exception:
            pass
        return

    _REGISTERED = True
    try:
        suffix = f" removed_legacy={removed}" if removed else ""
        print(f"✅ public_verify_group: /verify already registered with no-role auto-repair{suffix}")
    except Exception:
        pass


__all__ = ["register_public_verify_group_commands", "verify_group"]
