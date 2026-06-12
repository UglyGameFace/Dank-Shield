from __future__ import annotations

"""
Public/beta access-control hardening.

Role tiers used by Dank Shield:

- server-control role: the owner/admin-level bot control role for setup/config,
  destructive cleanup, and production switches. Private servers may call this a
  perm/admin role, but public servers configure it per guild.
- ticket-staff role: daily staff/support role for ticket handling and normal
  moderation workflow. Public servers configure this per guild.

This module intentionally keeps the resolver per-guild. No private legacy role
ID is baked into the code, and no public guild can inherit another guild's
server-control role from env unless guild_config explicitly allows env fallback.
"""

import os
import time
from typing import Any, Mapping, Optional

import discord

from .common import reply_once, safe_defer


_PATCHED = False
_ATTACHED = False
_CONTROL_CACHE_TTL_SECONDS = 60.0
_CONTROL_CACHE: dict[int, tuple[float, set[int]]] = {}

_CONTROL_ROLE_KEYS: tuple[str, ...] = (
    "server_control_role_id",
    "control_role_id",
    "perm_role_id",
    "top_level_role_id",
    "bot_admin_role_id",
    "bot_owner_role_id",
    "admin_role_id",
    "owner_role_id",
)

_CONTROL_ROLE_LIST_KEYS: tuple[str, ...] = (
    "server_control_role_ids",
    "control_role_ids",
    "perm_role_ids",
    "bot_admin_role_ids",
)

_ENV_CONTROL_ROLE_KEYS: tuple[str, ...] = (
    "STONEY_SERVER_CONTROL_ROLE_ID",
    "STONEY_CONTROL_ROLE_ID",
    "SERVER_CONTROL_ROLE_ID",
    "CONTROL_ROLE_ID",
    "PERM_ROLE_ID",
    "BOT_ADMIN_ROLE_ID",
    "BOT_OWNER_ROLE_ID",
)

_ENV_CONTROL_ROLE_LIST_KEYS: tuple[str, ...] = (
    "STONEY_SERVER_CONTROL_ROLE_IDS",
    "STONEY_CONTROL_ROLE_IDS",
    "SERVER_CONTROL_ROLE_IDS",
    "CONTROL_ROLE_IDS",
    "PERM_ROLE_IDS",
)

_SETUP_PERMISSION_MODULES: tuple[str, ...] = (
    "stoney_verify.commands_ext.public_setup_group",
    "stoney_verify.commands_ext.public_setup_logs",
    "stoney_verify.commands_ext.public_setup_by_id",
    "stoney_verify.commands_ext.public_setup_picker",
    "stoney_verify.commands_ext.public_setup_find",
    "stoney_verify.commands_ext.public_setup_review",
)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        text = str(value).strip()
        return int(text) if text else int(default)
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text or default
    except Exception:
        return default


def _table_name() -> str:
    try:
        return (os.getenv("STONEY_GUILD_CONFIG_TABLE") or "guild_configs").strip() or "guild_configs"
    except Exception:
        return "guild_configs"


def _csv_ints(value: object) -> set[int]:
    out: set[int] = set()
    try:
        raw = str(value or "")
        for part in raw.replace(";", ",").split(","):
            item = _safe_int(part, 0)
            if item > 0:
                out.add(item)
    except Exception:
        pass
    return out


def _member_role_ids(member: discord.Member) -> set[int]:
    ids: set[int] = set()
    try:
        for role in getattr(member, "roles", []) or []:
            rid = _safe_int(getattr(role, "id", 0), 0)
            if rid > 0:
                ids.add(rid)
    except Exception:
        pass
    return ids


def _nested_settings(row: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    try:
        for key in ("settings", "config", "metadata", "meta"):
            value = row.get(key)
            if isinstance(value, Mapping):
                merged.update(dict(value))
        merged.update(dict(row))
    except Exception:
        try:
            merged.update(dict(row))
        except Exception:
            pass
    return merged


def _fetch_config_row_sync(guild_id: int) -> Optional[dict[str, Any]]:
    try:
        from ..globals import get_supabase

        sb = get_supabase()
        if sb is None:
            return None
        response = (
            sb.table(_table_name())
            .select("*")
            .eq("guild_id", str(int(guild_id)))
            .limit(1)
            .execute()
        )
        rows = getattr(response, "data", None) or []
        if not rows:
            return None
        first = rows[0]
        return dict(first) if isinstance(first, Mapping) else None
    except Exception:
        return None


def _env_control_role_ids_for_guild(guild_id: int) -> set[int]:
    try:
        from ..guild_config import env_fallback_allowed_for_guild

        if not env_fallback_allowed_for_guild(int(guild_id)):
            return set()
    except Exception:
        return set()

    ids: set[int] = set()
    for key in _ENV_CONTROL_ROLE_KEYS:
        ids.add(_safe_int(os.getenv(key), 0))
    for key in _ENV_CONTROL_ROLE_LIST_KEYS:
        ids.update(_csv_ints(os.getenv(key)))
    return {rid for rid in ids if rid > 0}


def _extract_control_role_ids(row: Optional[Mapping[str, Any]]) -> set[int]:
    if not row:
        return set()
    data = _nested_settings(row)
    ids: set[int] = set()
    for key in _CONTROL_ROLE_KEYS:
        ids.add(_safe_int(data.get(key), 0))
    for key in _CONTROL_ROLE_LIST_KEYS:
        ids.update(_csv_ints(data.get(key)))
    return {rid for rid in ids if rid > 0}


def invalidate_access_control_cache(guild_id: int | str | None = None) -> None:
    gid = _safe_int(guild_id, 0)
    if gid > 0:
        _CONTROL_CACHE.pop(gid, None)
    else:
        _CONTROL_CACHE.clear()


def configured_control_role_ids_for_guild(guild_id: int | str | None) -> set[int]:
    gid = _safe_int(guild_id, 0)
    if gid <= 0:
        return set()

    now = time.monotonic()
    cached = _CONTROL_CACHE.get(gid)
    if cached:
        loaded_at, ids = cached
        if now - loaded_at <= _CONTROL_CACHE_TTL_SECONDS:
            return set(ids)

    ids = _extract_control_role_ids(_fetch_config_row_sync(gid))
    ids.update(_env_control_role_ids_for_guild(gid))
    ids = {rid for rid in ids if rid > 0}
    _CONTROL_CACHE[gid] = (time.monotonic(), set(ids))
    return ids


def _configured_staff_role_ids(member: discord.Member) -> set[int]:
    try:
        from ..guild_config import get_cached_guild_config

        guild_id = _safe_int(getattr(getattr(member, "guild", None), "id", 0), 0)
        cfg = get_cached_guild_config(guild_id)
        ids = {
            _safe_int(getattr(cfg, "staff_role_id", 0), 0),
            _safe_int(getattr(cfg, "vc_staff_role_id", 0), 0),
            _safe_int(getattr(cfg, "effective_vc_staff_role_id", 0), 0),
        }
        return {rid for rid in ids if rid > 0}
    except Exception:
        return set()


def _guild_owner_id(member: discord.Member) -> int:
    try:
        return _safe_int(getattr(getattr(member, "guild", None), "owner_id", 0), 0)
    except Exception:
        return 0


def _is_guild_owner(member: discord.Member) -> bool:
    try:
        owner_id = _guild_owner_id(member)
        return owner_id > 0 and int(member.id) == owner_id
    except Exception:
        return False


def _is_administrator(member: discord.Member) -> bool:
    try:
        return bool(getattr(member.guild_permissions, "administrator", False))
    except Exception:
        return False


def _can_manage_guild(member: discord.Member) -> bool:
    try:
        return bool(getattr(member.guild_permissions, "manage_guild", False))
    except Exception:
        return False


def scoped_is_server_control(member: object) -> bool:
    """Owner/admin/configured control role. Manage Server bootstraps before a control role exists."""
    if not isinstance(member, discord.Member):
        return False

    if _is_guild_owner(member) or _is_administrator(member):
        return True

    guild_id = _safe_int(getattr(getattr(member, "guild", None), "id", 0), 0)
    control_ids = configured_control_role_ids_for_guild(guild_id)
    if control_ids:
        return bool(_member_role_ids(member).intersection(control_ids))

    # Bootstrap path for brand-new public guilds. Once a server-control role is
    # saved, Manage Server alone no longer counts as full bot control.
    return _can_manage_guild(member)


def scoped_is_ticket_staff(member: object) -> bool:
    """Ticket staff OR server-control. Manage Server bootstrap does not count as ticket staff."""
    if not isinstance(member, discord.Member):
        return False

    if _is_guild_owner(member) or _is_administrator(member):
        return True

    guild_id = _safe_int(getattr(getattr(member, "guild", None), "id", 0), 0)
    allowed_ids = set(_configured_staff_role_ids(member))
    allowed_ids.update(configured_control_role_ids_for_guild(guild_id))
    if not allowed_ids:
        return False

    return bool(_member_role_ids(member).intersection(allowed_ids))


async def require_server_control(interaction: discord.Interaction) -> bool:
    if interaction.guild is None or not isinstance(interaction.user, discord.Member):
        await reply_once(interaction, {"content": "❌ This command must be used inside a server.", "ephemeral": True})
        return False

    if scoped_is_server_control(interaction.user):
        return True

    control_ids = configured_control_role_ids_for_guild(interaction.guild.id)
    if control_ids:
        roles = [interaction.guild.get_role(rid) for rid in sorted(control_ids)]
        role_text = ", ".join(role.mention for role in roles if role is not None)
        role_text = role_text or "the configured server-control role"
        msg = f"❌ Server setup requires {role_text} or **Administrator**."
    else:
        msg = "❌ Server setup requires **Manage Server** or **Administrator**. Open `/dank setup` and choose a server-control role to lock setup access down after bootstrap."

    await reply_once(interaction, {"content": msg, "ephemeral": True})
    return False


async def _setup_access_callback(
    interaction: discord.Interaction,
    control_role: discord.Role,
    ticket_staff_role: Optional[discord.Role] = None,
    vc_staff_role: Optional[discord.Role] = None,
) -> None:
    if not await require_server_control(interaction):
        return

    await safe_defer(interaction, ephemeral=True)

    guild = interaction.guild
    if guild is None:
        return await interaction.followup.send("❌ This command must be used inside a server.", ephemeral=True)

    blockers: list[str] = []
    warnings: list[str] = []
    ok: list[str] = []

    if control_role.is_default():
        blockers.append("Server-control role cannot be @everyone.")
    else:
        ok.append(f"Server-control role set: {control_role.mention}.")

    if ticket_staff_role is not None:
        if ticket_staff_role.is_default():
            blockers.append("Ticket staff role cannot be @everyone.")
        else:
            ok.append(f"Ticket staff role set: {ticket_staff_role.mention}.")

    if vc_staff_role is not None:
        if vc_staff_role.is_default():
            blockers.append("VC staff role cannot be @everyone.")
        else:
            ok.append(f"VC staff role set: {vc_staff_role.mention}.")

    if control_role.managed:
        warnings.append(f"{control_role.mention} is a managed/integration role. That can work, but a normal Discord role is usually easier to maintain.")

    if blockers:
        embed = discord.Embed(
            title="🚫 Access Setup Blocked",
            description="Setup was not saved because one or more selected roles are unsafe.",
            color=discord.Color.red(),
        )
        embed.add_field(name="Blockers", value="\n".join(blockers)[:1024] or "Unknown blocker.", inline=False)
        if warnings:
            embed.add_field(name="Warnings", value="\n".join(warnings)[:1024], inline=False)
        if ok:
            embed.add_field(name="Passing Checks", value="\n".join(ok)[:1024], inline=False)
        return await interaction.followup.send(embed=embed, ephemeral=True)

    try:
        from .public_setup_group import _config_embed, _role_value, _upsert_config, _utc_iso
        from ..guild_config import get_guild_config, invalidate_guild_config
    except Exception as e:
        return await interaction.followup.send(f"❌ Access setup dependencies are unavailable: `{e}`", ephemeral=True)

    updates: dict[str, Any] = {
        "server_control_role_id": _role_value(control_role),
        "control_role_id": _role_value(control_role),
        "perm_role_id": _role_value(control_role),
        "configured_by_id": str(interaction.user.id),
        "configured_by_name": str(interaction.user),
        "configured_at": _utc_iso(),
    }

    if ticket_staff_role is not None:
        updates["staff_role_id"] = _role_value(ticket_staff_role)
    if vc_staff_role is not None:
        updates["vc_staff_role_id"] = _role_value(vc_staff_role)
    elif ticket_staff_role is not None:
        updates["vc_staff_role_id"] = _role_value(ticket_staff_role)

    try:
        await _upsert_config(guild.id, updates)
        invalidate_guild_config(guild.id)
        invalidate_access_control_cache(guild.id)
        cfg = await get_guild_config(guild.id, refresh=True)
    except Exception as e:
        return await interaction.followup.send(f"❌ Failed saving access setup: `{e}`", ephemeral=True)

    embed = _config_embed(guild, cfg, title="✅ Access Setup Saved")
    embed.add_field(
        name="Access Control",
        value=(
            f"Server-control role: {control_role.mention} (`{control_role.id}`)\n"
            f"Ticket staff role: {(ticket_staff_role.mention if ticket_staff_role else 'unchanged')}"
            f"{f' (`{ticket_staff_role.id}`)' if ticket_staff_role else ''}\n"
            f"VC staff role: {(vc_staff_role.mention if vc_staff_role else (ticket_staff_role.mention if ticket_staff_role else 'unchanged'))}"
            f"{f' (`{vc_staff_role.id}`)' if vc_staff_role else (f' (`{ticket_staff_role.id}`)' if ticket_staff_role else '')}"
        ),
        inline=False,
    )
    if warnings:
        embed.add_field(name="Warnings", value="\n".join(warnings)[:1024], inline=False)
    if ok:
        embed.add_field(name="Passing Checks", value="\n".join(ok)[:1024], inline=False)
    await interaction.followup.send(embed=embed, ephemeral=True)


def _attach_setup_access_command() -> None:
    global _ATTACHED
    if _ATTACHED:
        return
    try:
        from .public_setup_group import stoney_group

        @stoney_group.command(name="setup-access", description="Set the role allowed to configure Dank Shield")
        async def setup_access(
            interaction: discord.Interaction,
            control_role: discord.Role,
            ticket_staff_role: Optional[discord.Role] = None,
            vc_staff_role: Optional[discord.Role] = None,
        ) -> None:
            await _setup_access_callback(interaction, control_role, ticket_staff_role, vc_staff_role)

        _ATTACHED = True
        print("✅ public_access_control setup-access command attached.")
    except Exception as e:
        print("⚠️ public_access_control setup-access attach failed:", repr(e))


def install_public_access_control() -> bool:
    global _PATCHED
    if _PATCHED:
        return True

    for mod_name in _SETUP_PERMISSION_MODULES:
        try:
            module = __import__(mod_name, fromlist=["*"])
            setattr(module, "_require_setup_permission", require_server_control)
        except Exception:
            continue

    _attach_setup_access_command()
    _PATCHED = True
    try:
        print("🛡️ public_access_control loaded; server-control role gate active")
    except Exception:
        pass
    return True


install_public_access_control()

__all__ = [
    "configured_control_role_ids_for_guild",
    "invalidate_access_control_cache",
    "install_public_access_control",
    "require_server_control",
    "scoped_is_server_control",
    "scoped_is_ticket_staff",
]
