from __future__ import annotations

"""Public /verify command family.

Production command rules:
- One top-level command: /verify
- Member-targeting tools use Discord's native member picker
- Role-targeting repair tools use Discord's native role picker
- Heavy repair tools acknowledge immediately so Discord never shows
  "The application did not respond"
- Existing customer roles are valid. Stoney does not require roles to have been
  created by Stoney, and staff can explicitly pick any role their server uses.
- Setup builder stays the source of truth. Saved config wins unless staff uses
  an explicit role picker override, which is saved back to guild_configs.
- No-role users are never left stuck because a server forgot to save/create a
  Pending / Unverified role. Repair tools use saved config, selected role,
  discovery, or safe auto-create in that order.
"""

import asyncio
import re
from typing import Any, Dict, Optional

import discord
from discord import app_commands

from ..globals import now_utc
from ..guild_config import get_guild_config, invalidate_guild_config, upsert_guild_config
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
    "pending": (
        "unverified",
        "not verified",
        "un-verified",
        "pending verification",
        "pending",
        "verify required",
        "needs verification",
        "new member",
        "newcomer",
        "guest",
        "visitor",
        "lobby",
        "prospect",
        "awaiting approval",
        "needs vetting",
        "screening",
    ),
    "verified": (
        "verified",
        "verify complete",
        "verified member",
        "approved",
        "vetted",
    ),
    "resident": (
        "resident",
        "member",
        "members",
        "community member",
        "full access",
        "regular",
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
    "pending": "unverified_role_id",
    "verified": "verified_role_id",
    "resident": "resident_role_id",
    "staff": "staff_role_id",
    "vc_staff": "vc_staff_role_id",
}

_ROLE_LABELS: dict[str, str] = {
    "pending": "Pending / Unverified",
    "verified": "Verified",
    "resident": "Member / Resident",
    "staff": "Staff / Support",
    "vc_staff": "VC Staff / Support",
}


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
        roles = [role for role in guild.roles if not role.is_default()]
    except Exception:
        roles = list(getattr(guild, "roles", []) or [])

    for role in roles:
        role_name = _normalize_role_name(getattr(role, "name", ""))
        if role_name in aliases:
            return role

    for role in roles:
        role_name = _normalize_role_name(getattr(role, "name", ""))
        if any(alias and alias in role_name for alias in aliases):
            return role

    return None


async def _ack(interaction: discord.Interaction) -> None:
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
    if payload.get("allowed_mentions") is None:
        payload["allowed_mentions"] = discord.AllowedMentions.none()

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
    await _send(interaction, "❌ Staff only.\n\nWhat to do next: ask a server admin to give you Stoney setup/staff access, then try again.")
    return False


async def _guild_or_reply(interaction: discord.Interaction) -> Optional[discord.Guild]:
    guild = interaction.guild
    if guild is None:
        await _send(interaction, "❌ This command must be used inside a server.")
        return None
    return guild


async def _save_role_config(
    guild: discord.Guild,
    config_key: str,
    role: discord.Role,
    *,
    source: str,
    explicit_override: bool = False,
) -> None:
    patch: dict[str, Any] = {
        config_key: str(role.id),
        "__config_write_source": source,
    }

    if explicit_override:
        patch["__config_write_mode"] = "explicit_override"
        patch["__config_write_allow_keys"] = [config_key]
    else:
        patch["__config_write_mode"] = "runtime_discovery"

    try:
        await upsert_guild_config(guild.id, patch)
        invalidate_guild_config(guild.id)
        try:
            print(
                "✅ public_verify_group saved verification role "
                f"guild={guild.id} key={config_key} role={role.name}:{role.id} "
                f"source={source} explicit={explicit_override}"
            )
        except Exception:
            pass
    except Exception as e:
        try:
            print(
                "⚠️ public_verify_group could not save verification role "
                f"guild={guild.id} key={config_key} role={role.name}:{role.id} "
                f"source={source} explicit={explicit_override} error={e!r}"
            )
        except Exception:
            pass


async def _guild_roles(guild: discord.Guild) -> Dict[str, Optional[discord.Role]]:
    cfg = await get_guild_config(guild.id, refresh=True)
    roles: Dict[str, Optional[discord.Role]] = {}
    discovered_patch: dict[str, str] = {}
    discovery_notes: list[str] = []

    def role_for_config_key(key: str) -> Optional[discord.Role]:
        rid = _safe_int(_cfg_value(cfg, key), 0)
        if rid <= 0:
            return None
        role = guild.get_role(rid)
        return role if isinstance(role, discord.Role) else None

    for logical_name, config_key in _ROLE_CONFIG_KEYS.items():
        role = role_for_config_key(config_key)
        if role is None:
            discovered = _find_existing_role_by_alias(guild, logical_name)
            if discovered is not None:
                role = discovered
                discovered_patch[config_key] = str(discovered.id)
                discovery_notes.append(f"{logical_name}={discovered.name}:{discovered.id}")
        roles[logical_name] = role

    if roles.get("vc_staff") is None and roles.get("staff") is not None:
        staff_role = roles.get("staff")
        roles["vc_staff"] = staff_role
        if staff_role is not None:
            discovered_patch.setdefault("vc_staff_role_id", str(staff_role.id))

    if discovered_patch:
        patch: dict[str, Any] = {
            **discovered_patch,
            "__config_write_mode": "runtime_discovery",
            "__config_write_source": "/verify role discovery",
        }
        try:
            await upsert_guild_config(guild.id, patch)
            invalidate_guild_config(guild.id)
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


def _bot_can_manage_role(guild: discord.Guild, role: Optional[discord.Role]) -> tuple[bool, str]:
    if role is None:
        return False, "role is not configured or was not found"
    if role.is_default():
        return False, "@everyone cannot be used as a verification role"
    if getattr(role, "managed", False):
        return False, f"{role.name} is managed by an integration and cannot be assigned manually"

    me = guild.me
    if me is None:
        return False, "bot member could not be resolved"
    if not me.guild_permissions.manage_roles:
        return False, "bot is missing Manage Roles"
    try:
        if me.top_role <= role and not me.guild_permissions.administrator:
            return False, f"Stoney's highest role is below or equal to {role.name}"
    except Exception:
        pass
    return True, "ok"


async def _selected_role_override(
    guild: discord.Guild,
    roles: Dict[str, Optional[discord.Role]],
    logical_name: str,
    role: Optional[discord.Role],
    *,
    source: str,
) -> tuple[Optional[discord.Role], Optional[str]]:
    if role is None:
        return None, None

    label = _ROLE_LABELS.get(logical_name, logical_name.title())
    config_key = _ROLE_CONFIG_KEYS[logical_name]

    ok, why = _bot_can_manage_role(guild, role)
    if not ok:
        return None, (
            f"❌ I cannot use {role.mention} as the **{label}** role: {why}.\n\n"
            "What to do next: move Stoney's bot role above that role, enable Manage Roles, "
            "or pick a different role."
        )

    roles[logical_name] = role
    await _save_role_config(guild, config_key, role, source=source, explicit_override=True)
    return role, None


async def _ensure_pending_role(
    guild: discord.Guild,
    roles: Dict[str, Optional[discord.Role]],
    *,
    selected_role: Optional[discord.Role] = None,
    create_if_missing: bool = True,
    source: str,
) -> tuple[Optional[discord.Role], str, bool]:
    selected, error = await _selected_role_override(
        guild,
        roles,
        "pending",
        selected_role,
        source=f"{source}:role_picker_override",
    )
    if error:
        return None, error, False
    if selected is not None:
        return selected, f"selected by staff and saved as {selected.mention}", False

    existing = roles.get("pending")
    if isinstance(existing, discord.Role):
        return existing, f"using saved/discovered setup role {existing.mention}", False

    discovered = _find_existing_role_by_alias(guild, "pending")
    if isinstance(discovered, discord.Role):
        roles["pending"] = discovered
        await _save_role_config(
            guild,
            "unverified_role_id",
            discovered,
            source=f"{source}:live_discovery",
            explicit_override=False,
        )
        return discovered, f"auto-discovered existing role {discovered.mention}", False

    if not create_if_missing:
        return None, (
            "No Pending / Unverified role is saved or discoverable. "
            "Pick one with the `role` option, or run `/stoney setup` → Existing Server."
        ), False

    me = guild.me
    if me is None:
        return None, "bot member could not be resolved, so I could not create a Pending / Unverified role", False
    if not me.guild_permissions.manage_roles:
        return None, "bot is missing Manage Roles, so I could not create a Pending / Unverified role", False

    try:
        created = await guild.create_role(
            name="Unverified",
            permissions=discord.Permissions.none(),
            mentionable=False,
            hoist=False,
            reason="Stoney Verify repair created missing Pending / Unverified role for no-role members",
        )
        roles["pending"] = created
        await _save_role_config(
            guild,
            "unverified_role_id",
            created,
            source=f"{source}:auto_create",
            explicit_override=False,
        )
        return created, f"created and saved default Pending / Unverified role {created.mention}", True
    except discord.Forbidden:
        return None, "Forbidden while creating Pending / Unverified role. Move Stoney's bot role higher and grant Manage Roles.", False
    except Exception as e:
        return None, f"failed to create Pending / Unverified role: {type(e).__name__}: {_truncate(e, 240)}", False


async def _ensure_named_role(
    guild: discord.Guild,
    roles: Dict[str, Optional[discord.Role]],
    logical_name: str,
    *,
    selected_role: Optional[discord.Role],
    source: str,
) -> tuple[Optional[discord.Role], str]:
    selected, error = await _selected_role_override(
        guild,
        roles,
        logical_name,
        selected_role,
        source=f"{source}:role_picker_override",
    )
    if error:
        return None, error
    if selected is not None:
        return selected, f"selected by staff and saved as {selected.mention}"

    existing = roles.get(logical_name)
    if isinstance(existing, discord.Role):
        return existing, f"using saved/discovered setup role {existing.mention}"

    discovered = _find_existing_role_by_alias(guild, logical_name)
    if isinstance(discovered, discord.Role):
        roles[logical_name] = discovered
        await _save_role_config(
            guild,
            _ROLE_CONFIG_KEYS[logical_name],
            discovered,
            source=f"{source}:live_discovery",
            explicit_override=False,
        )
        return discovered, f"auto-discovered existing role {discovered.mention}"

    label = _ROLE_LABELS.get(logical_name, logical_name.title())
    aliases = ", ".join(f"`{x}`" for x in _ROLE_ALIASES.get(logical_name, ())[:5])
    return None, (
        f"❌ {label} role is not configured and I could not auto-discover one.\n\n"
        f"What to do next: use the command's role picker option, or run `/stoney setup` → Existing Server. "
        f"Common names I can auto-detect include: {aliases}."
    )


def _role_status(member: discord.Member, role: Optional[discord.Role]) -> bool:
    try:
        return bool(role and role in member.roles)
    except Exception:
        return False


def _needs_verification(member: discord.Member, roles: Dict[str, Optional[discord.Role]]) -> bool:
    has_pending = _role_status(member, roles.get("pending"))
    has_verified = _role_status(member, roles.get("verified"))
    has_resident = _role_status(member, roles.get("resident"))
    return bool(has_pending and not has_verified and not has_resident)


def _is_staff_member(member: discord.Member, roles: Dict[str, Optional[discord.Role]]) -> bool:
    try:
        if member.guild_permissions.administrator or member.guild_permissions.manage_guild or member.guild_permissions.manage_channels:
            return True
    except Exception:
        pass
    for key in ("staff", "vc_staff"):
        role = roles.get(key)
        if role is not None and role in member.roles:
            return True
    return False


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
        return await _send(
            interaction,
            f"❌ I cannot manage {role.mention}: {why}.\n\n"
            "What to do next: move Stoney's bot role higher than that role and make sure Manage Roles is enabled.",
        )

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
        return await _send(
            interaction,
            "❌ Forbidden. Discord blocked the role change.\n\n"
            "What to do next: check **Manage Roles** and make sure Stoney's bot role is above the target role.",
        )
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
    pending = roles.get("pending")
    verified = roles.get("verified")
    resident = roles.get("resident")
    staff = roles.get("staff")

    return [
        f"👤 {member.mention} (`{member.id}`)",
        f"⏳ Pending / Unverified: {'YES' if _role_status(member, pending) else 'NO'}" + (f" ({pending.mention})" if pending else " (not configured / no matching existing role found)"),
        f"✅ Verified: {'YES' if _role_status(member, verified) else 'NO'}" + (f" ({verified.mention})" if verified else " (not configured / no matching existing role found)"),
        f"🏠 Member / Resident: {'YES' if _role_status(member, resident) else 'NO'}" + (f" ({resident.mention})" if resident else " (not configured / no matching existing role found)"),
        f"🛡️ Staff / Support: {'YES' if _role_status(member, staff) else 'NO'}" + (f" ({staff.mention})" if staff else " (not configured / no matching existing role found)"),
        f"🧭 Needs verification: {'YES' if _needs_verification(member, roles) else 'NO'}",
    ]


@verify_group.command(name="status", description="Show a member's verification/member status.")
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
        display = _ROLE_LABELS.get(label, label)
        if role is None:
            hierarchy_lines.append(f"{display}: not configured / not found")
            continue
        ok, why = _bot_can_manage_role(guild, role)
        hierarchy_lines.append(f"{display}: {'OK' if ok else why}")

    embed.add_field(name="Role Hierarchy", value=_truncate("\n".join(hierarchy_lines), 1024), inline=False)
    embed.add_field(
        name="What To Press Next",
        value=(
            "Use `/verify fix-member` for one user, `/verify repair-unverified` for all no-role/pending users, "
            "or `/stoney setup` → Existing Server to pick your exact roles/channels."
        ),
        inline=False,
    )
    await _send(interaction, embed=embed)


@verify_group.command(name="set-verified", description="Add or remove the Verified role.")
@app_commands.describe(
    user="Choose the member",
    enable="True to add Verified; False to remove",
    role="Optional custom Verified role to save/use for this server",
)
async def verify_set_verified(
    interaction: discord.Interaction,
    user: discord.Member,
    enable: bool,
    role: Optional[discord.Role] = None,
) -> None:
    await _ack(interaction)
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return

    roles = await _guild_roles(guild)
    verified, detail = await _ensure_named_role(
        guild,
        roles,
        "verified",
        selected_role=role,
        source="/verify set-verified",
    )
    if verified is None:
        return await _send(interaction, detail)

    await _apply_role_change(
        interaction=interaction,
        member=user,
        role=verified,
        enable=enable,
        reason=f"/verify set-verified by {interaction.user} ({interaction.user.id})",
    )


@verify_group.command(name="set-resident", description="Add or remove the Member / Resident role.")
@app_commands.describe(
    user="Choose the member",
    enable="True to add Member/Resident; False to remove",
    role="Optional custom Member / Resident role to save/use for this server",
)
async def verify_set_resident(
    interaction: discord.Interaction,
    user: discord.Member,
    enable: bool,
    role: Optional[discord.Role] = None,
) -> None:
    await _ack(interaction)
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return

    roles = await _guild_roles(guild)
    resident, detail = await _ensure_named_role(
        guild,
        roles,
        "resident",
        selected_role=role,
        source="/verify set-resident",
    )
    if resident is None:
        return await _send(interaction, detail)

    await _apply_role_change(
        interaction=interaction,
        member=user,
        role=resident,
        enable=enable,
        reason=f"/verify set-resident by {interaction.user} ({interaction.user.id})",
    )


@verify_group.command(name="grant-vr", description="Grant Verified + Member/Resident and remove Pending/Unverified.")
@app_commands.describe(
    user="Choose the member",
    verified_role="Optional custom Verified role to save/use",
    resident_role="Optional custom Member / Resident role to save/use",
    pending_role="Optional custom Pending / Unverified role to save/use for removal",
)
async def verify_grant_vr(
    interaction: discord.Interaction,
    user: discord.Member,
    verified_role: Optional[discord.Role] = None,
    resident_role: Optional[discord.Role] = None,
    pending_role: Optional[discord.Role] = None,
) -> None:
    await _ack(interaction)
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return

    roles = await _guild_roles(guild)

    verified, verified_detail = await _ensure_named_role(
        guild,
        roles,
        "verified",
        selected_role=verified_role,
        source="/verify grant-vr verified_role",
    )
    if verified is None:
        return await _send(interaction, verified_detail)

    resident, resident_detail = await _ensure_named_role(
        guild,
        roles,
        "resident",
        selected_role=resident_role,
        source="/verify grant-vr resident_role",
    )
    if resident is None:
        return await _send(interaction, resident_detail)

    pending, pending_detail, _ = await _ensure_pending_role(
        guild,
        roles,
        selected_role=pending_role,
        create_if_missing=False,
        source="/verify grant-vr pending_role",
    )

    for target_role in (verified, resident, pending):
        if target_role is None:
            continue
        ok, why = _bot_can_manage_role(guild, target_role)
        if not ok:
            return await _send(
                interaction,
                f"❌ I cannot manage {target_role.mention}: {why}.\n\n"
                "What to do next: move Stoney's bot role above that role, then try again.",
            )

    added: list[discord.Role] = []
    removed: list[discord.Role] = []
    try:
        to_add = [target_role for target_role in (verified, resident) if target_role is not None and target_role not in user.roles]
        if to_add:
            await user.add_roles(*to_add, reason=f"/verify grant-vr by {interaction.user} ({interaction.user.id})")
            added.extend(to_add)

        if pending is not None and pending in user.roles:
            await user.remove_roles(pending, reason=f"/verify grant-vr cleanup by {interaction.user} ({interaction.user.id})")
            removed.append(pending)

        ticket_ch = await _maybe_repair_verify_ui_for_member(guild, user)

        lines = [f"✅ Updated {user.mention}."]
        lines.append("Verified role: " + verified.mention + f" ({verified_detail})")
        lines.append("Member / Resident role: " + resident.mention + f" ({resident_detail})")
        lines.append("Pending / Unverified role: " + (pending.mention + f" ({pending_detail})" if pending else "not configured; nothing to remove"))
        lines.append("Added: " + (", ".join(r.mention for r in added) if added else "nothing; already had roles"))
        lines.append("Removed: " + (", ".join(r.mention for r in removed) if removed else "nothing"))
        if ticket_ch:
            lines.append(f"Ticket: {ticket_ch.mention}")
        await _send(interaction, "\n".join(lines))
    except discord.Forbidden:
        await _send(
            interaction,
            "❌ Forbidden. Check Manage Roles and role hierarchy.\n\n"
            "What to do next: move Stoney's bot role above the roles it grants/removes.",
        )
    except Exception as e:
        await _send(interaction, f"❌ Failed: {_truncate(e, 300)}")


@verify_group.command(name="fix-member", description="Add the Pending / Unverified role to one member.")
@app_commands.describe(
    user="Choose the member",
    role="Optional custom Pending / Unverified role to save/use for this server",
    remove_conflicts="Also remove Verified/Member roles while restoring Pending/Unverified",
    create_missing_role="Create a default role only if no saved/picked/discovered role exists",
)
async def verify_fix_member(
    interaction: discord.Interaction,
    user: discord.Member,
    role: Optional[discord.Role] = None,
    remove_conflicts: bool = False,
    create_missing_role: bool = True,
) -> None:
    await _ack(interaction)
    if not await _staff_only(interaction):
        return
    guild = await _guild_or_reply(interaction)
    if guild is None:
        return

    roles = await _guild_roles(guild)
    pending, role_detail, created_role = await _ensure_pending_role(
        guild,
        roles,
        selected_role=role,
        create_if_missing=create_missing_role,
        source="/verify fix-member",
    )
    verified = roles.get("verified")
    resident = roles.get("resident")

    if pending is None:
        return await _send(interaction, f"❌ I could not prepare a Pending / Unverified role: {role_detail}")

    for target_role in (pending, verified, resident):
        if target_role is None:
            continue
        ok, why = _bot_can_manage_role(guild, target_role)
        if not ok:
            return await _send(
                interaction,
                f"❌ I cannot manage {target_role.mention}: {why}.\n\n"
                "What to do next: move Stoney's bot role above that role and try again.",
            )

    added: list[discord.Role] = []
    removed: list[discord.Role] = []
    try:
        if remove_conflicts:
            to_remove = [target_role for target_role in (verified, resident) if target_role is not None and target_role in user.roles]
            if to_remove:
                await user.remove_roles(*to_remove, reason=f"/verify fix-member conflict cleanup by {interaction.user} ({interaction.user.id})")
                removed.extend(to_remove)

        if pending not in user.roles:
            await user.add_roles(pending, reason=f"/verify fix-member by {interaction.user} ({interaction.user.id})")
            added.append(pending)

        ticket_ch = await _maybe_repair_verify_ui_for_member(guild, user)

        lines = [f"✅ Pending / Unverified repair complete for {user.mention}."]
        lines.append("Using role: " + pending.mention)
        lines.append("Role source: " + role_detail)
        if created_role:
            lines.append("Created role: yes")
        lines.append("Added: " + (", ".join(r.mention for r in added) if added else "nothing"))
        lines.append("Removed: " + (", ".join(r.mention for r in removed) if removed else "nothing"))
        if ticket_ch:
            lines.append(f"Ticket: {ticket_ch.mention}")
        await _send(interaction, "\n".join(lines))
    except discord.Forbidden:
        await _send(
            interaction,
            "❌ Forbidden. Check Manage Roles and role hierarchy.\n\n"
            "What to do next: move Stoney's bot role above the target role and try again.",
        )
    except Exception as e:
        await _send(interaction, f"❌ Failed: {_truncate(e, 300)}")


@verify_group.command(name="repair-unverified", description="Assign Pending / Unverified to members missing safe roles.")
@app_commands.describe(
    role="Optional custom Pending / Unverified role to save/use for this server",
    create_missing_role="Create a default role only if no saved/picked/discovered role exists",
)
async def verify_repair_unverified(
    interaction: discord.Interaction,
    role: Optional[discord.Role] = None,
    create_missing_role: bool = True,
) -> None:
    await _ack(interaction)
    try:
        if not await _staff_only(interaction):
            return
        guild = await _guild_or_reply(interaction)
        if guild is None:
            return

        roles = await _guild_roles(guild)
        pending, role_detail, created_role = await _ensure_pending_role(
            guild,
            roles,
            selected_role=role,
            create_if_missing=create_missing_role,
            source="/verify repair-unverified",
        )
        verified = roles.get("verified")
        resident = roles.get("resident")

        if pending is None:
            return await _send(interaction, f"❌ I could not prepare a Pending / Unverified role: {role_detail}")

        ok, why = _bot_can_manage_role(guild, pending)
        if not ok:
            return await _send(
                interaction,
                f"❌ I found {pending.mention}, but I cannot manage it: {why}.\n\n"
                "What to do next: move Stoney's bot role above that role, or use the `role` option to pick a manageable role.",
            )

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
                if _is_staff_member(member, roles):
                    skipped_staff += 1
                    continue
                if verified and verified in member.roles:
                    skipped_verified += 1
                    continue
                if resident and resident in member.roles:
                    skipped_resident += 1
                    continue
                if pending in member.roles:
                    already_ok += 1
                    continue

                await member.add_roles(pending, reason=f"/verify repair-unverified by {interaction.user} ({interaction.user.id})")
                added += 1
            except Exception:
                failed += 1

            if index % 5 == 0:
                await asyncio.sleep(0.35)

        cache_note = "complete/member chunk succeeded" if chunk_ok else "partial/member chunk failed or timed out"
        await _send(
            interaction,
            "✅ **Pending / Unverified repair complete**\n"
            f"- Using role: {pending.mention}\n"
            f"- Role source: **{role_detail}**\n"
            f"- Created role: **{'yes' if created_role else 'no'}**\n"
            f"- Members scanned: **{len(members)}** (`{cache_note}`)\n"
            f"- Added Pending / Unverified: **{added}**\n"
            f"- Already had Pending / Unverified: **{already_ok}**\n"
            f"- Skipped Verified: **{skipped_verified}**\n"
            f"- Skipped Member / Resident: **{skipped_resident}**\n"
            f"- Skipped Staff: **{skipped_staff}**\n"
            f"- Skipped Bots: **{skipped_bots}**\n"
            f"- Failed: **{failed}**"
            + ("\n\n⚠️ Member cache may be partial. Use `/verify fix-member` for a specific user if needed." if not chunk_ok else ""),
        )
    except Exception as e:
        await _send(interaction, f"❌ Repair crashed before completion: `{type(e).__name__}: {_truncate(e, 300)}`")


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
            print(f"✅ public_verify_group: registered /verify grouped commands with custom role picker repair{suffix}")
        except Exception:
            pass
        return

    _REGISTERED = True
    try:
        suffix = f" removed_legacy={removed}" if removed else ""
        print(f"✅ public_verify_group: /verify already registered with custom role picker repair{suffix}")
    except Exception:
        pass


__all__ = ["register_public_verify_group_commands", "verify_group"]
