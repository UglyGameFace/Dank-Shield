from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import discord

from .globals import get_supabase

PROFILE_PANEL_KEYS = {
    "pronouns",
    "interests",
    "pings",
    "gaming",
    "vibes",
    "privacy",
}

AGGREGATE_PANEL_KEYS = {"", "profile_customizer", "all"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_str(value: Any) -> str:
    try:
        return str(value or "").strip()
    except Exception:
        return ""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value or "0").strip() or 0)
    except Exception:
        return default


def _normalize_panel_key(value: Any) -> str:
    text = _safe_str(value).lower().replace("-", "_").replace(" ", "_")
    return text if text in PROFILE_PANEL_KEYS or text in AGGREGATE_PANEL_KEYS else ""


def _db_schema_hint(error: Exception) -> str:
    text = repr(error).lower()
    if "profile_customizer" in text or "profile_role" in text or "member_profile_role" in text:
        return "Profile Customizer tables are missing. Run the dashboard migration supabase/20260616_profile_customizer.sql first."
    return _safe_str(error) or repr(error)


async def _run_db(label: str, fn):
    try:
        return await asyncio.to_thread(fn)
    except Exception as exc:
        raise RuntimeError(f"{label}: {_db_schema_hint(exc)}") from exc


def _role_is_manageable(guild: discord.Guild, role: discord.Role) -> Tuple[bool, str]:
    try:
        me = guild.me
        if me is None:
            return False, "bot_member_missing"
        if role.managed:
            return False, "role_is_managed_by_integration"
        if role >= me.top_role:
            return False, "role_above_or_equal_to_bot_top_role"
        perms = getattr(me, "guild_permissions", None)
        if not getattr(perms, "manage_roles", False) and not getattr(perms, "administrator", False):
            return False, "bot_lacks_manage_roles"
        return True, ""
    except Exception as exc:
        return False, f"role_manage_check_failed:{repr(exc)}"


def _find_role(guild: discord.Guild, *, role_id: Any = None, role_name: Any = None) -> Optional[discord.Role]:
    rid = _safe_int(role_id, 0)
    if rid > 0:
        role = guild.get_role(rid)
        if isinstance(role, discord.Role):
            return role

    wanted = _safe_str(role_name).lower()
    if wanted:
        for role in getattr(guild, "roles", []) or []:
            if _safe_str(getattr(role, "name", "")).lower() == wanted:
                return role

    return None


async def _fetch_profile_panels(guild_id: str, panel_key: str = "") -> List[Dict[str, Any]]:
    normalized_key = _normalize_panel_key(panel_key)

    def _read() -> List[Dict[str, Any]]:
        sb = get_supabase()
        if sb is None:
            return []

        panel_query = (
            sb.table("profile_role_panels")
            .select("*")
            .eq("guild_id", str(guild_id))
            .eq("enabled", True)
            .order("sort_order", desc=False)
        )
        if normalized_key and normalized_key not in AGGREGATE_PANEL_KEYS:
            panel_query = panel_query.eq("panel_key", normalized_key)

        panel_res = panel_query.execute()
        panels = list(getattr(panel_res, "data", None) or [])
        if not panels:
            return []

        option_res = (
            sb.table("profile_role_options")
            .select("*")
            .eq("guild_id", str(guild_id))
            .eq("enabled", True)
            .order("sort_order", desc=False)
            .execute()
        )
        options = list(getattr(option_res, "data", None) or [])
        options_by_panel: Dict[str, List[Dict[str, Any]]] = {}
        for option in options:
            pid = _safe_str(option.get("panel_id"))
            if not pid:
                continue
            options_by_panel.setdefault(pid, []).append(dict(option))

        out: List[Dict[str, Any]] = []
        for panel in panels:
            row = dict(panel)
            row["options"] = options_by_panel.get(_safe_str(row.get("id")), [])
            out.append(row)
        return out

    return await _run_db("fetch profile customizer panels", _read)


async def _fetch_active_member_choices(
    guild_id: str,
    *,
    user_id: Optional[str] = None,
    panel_key: str = "",
) -> List[Dict[str, Any]]:
    normalized_key = _normalize_panel_key(panel_key)

    def _read() -> List[Dict[str, Any]]:
        sb = get_supabase()
        if sb is None:
            return []

        query = (
            sb.table("member_profile_role_choices")
            .select("*")
            .eq("guild_id", str(guild_id))
            .is_("removed_at", "null")
            .order("created_at", desc=False)
        )
        if user_id:
            query = query.eq("user_id", str(user_id))
        if normalized_key and normalized_key not in AGGREGATE_PANEL_KEYS:
            query = query.eq("panel_key", normalized_key)

        res = query.execute()
        return [dict(row) for row in (getattr(res, "data", None) or [])]

    return await _run_db("fetch active profile role choices", _read)


async def _mark_choices_removed(choice_ids: List[str], *, actor_id: Optional[str] = None) -> None:
    clean_ids = [_safe_str(item) for item in choice_ids if _safe_str(item)]
    if not clean_ids:
        return

    def _write() -> None:
        sb = get_supabase()
        if sb is None:
            return
        patch = {
            "removed_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        if actor_id:
            patch["actor_id"] = str(actor_id)
        sb.table("member_profile_role_choices").update(patch).in_("id", clean_ids).execute()

    await _run_db("mark profile choices removed", _write)


async def _write_audit_event(
    *,
    guild_id: str,
    user_id: Optional[str],
    actor_id: Optional[str],
    actor_name: Optional[str],
    event_type: str,
    title: str,
    reason: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    def _write() -> None:
        sb = get_supabase()
        if sb is None:
            return
        payload = {
            "guild_id": str(guild_id),
            "user_id": str(user_id) if user_id else None,
            "actor_id": str(actor_id) if actor_id else None,
            "actor_name": _safe_str(actor_name) or None,
            "event_type": event_type,
            "title": title,
            "reason": reason,
            "metadata": metadata or {},
            "created_at": _now_iso(),
        }
        sb.table("member_events").insert(payload).execute()

    try:
        await _run_db("write profile customizer audit event", _write)
    except Exception as exc:
        print("⚠️ Profile Customizer audit event skipped:", repr(exc))


def _panel_embed(guild: discord.Guild, panels: List[Dict[str, Any]]) -> discord.Embed:
    embed = discord.Embed(
        title="Customize Your Server Profile",
        description=(
            "These roles are optional. Pick what you want shown, skip what you do not, "
            "and use Privacy & Reset anytime to clear profile roles."
        ),
        color=discord.Color.green(),
    )
    embed.set_footer(text="Dank Shield Profile Customizer • Optional after verification")

    for panel in panels[:10]:
        title = _safe_str(panel.get("title")) or _safe_str(panel.get("panel_key")) or "Profile Panel"
        description = _safe_str(panel.get("description"))
        options = panel.get("options") if isinstance(panel.get("options"), list) else []
        option_lines = []
        for option in options[:12]:
            emoji = _safe_str(option.get("emoji"))
            label = _safe_str(option.get("label")) or _safe_str(option.get("option_key"))
            detail = _safe_str(option.get("description"))
            line = f"{emoji + ' ' if emoji else ''}**{label}**"
            if detail:
                line += f" — {detail}"
            option_lines.append(line)

        value = "\n".join(option_lines) or description or "No options configured yet."
        if len(value) > 1000:
            value = value[:997] + "..."
        embed.add_field(name=title[:256], value=value, inline=False)

    if guild.icon:
        try:
            embed.set_thumbnail(url=guild.icon.url)
        except Exception:
            pass

    return embed


async def post_profile_customizer_panel(
    guild: discord.Guild,
    *,
    channel_id: int,
    panel_key: str = "profile_customizer",
    requested_by: Optional[str] = None,
) -> Dict[str, Any]:
    if channel_id <= 0:
        return {"posted": False, "reason": "missing_channel_id"}

    channel = guild.get_channel(channel_id)
    if channel is None:
        try:
            channel = await guild.fetch_channel(channel_id)  # type: ignore[attr-defined]
        except Exception:
            channel = None

    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        return {"posted": False, "reason": "channel_missing_or_not_text", "channel_id": str(channel_id)}

    panels = await _fetch_profile_panels(str(guild.id), panel_key=panel_key)
    if not panels:
        return {
            "posted": False,
            "reason": "profile_customizer_not_seeded",
            "message": "Seed Profile Customizer defaults from the dashboard before posting the panel.",
        }

    embed = _panel_embed(guild, panels)
    sent = await channel.send(embed=embed)

    def _write() -> None:
        sb = get_supabase()
        if sb is None:
            return
        now = _now_iso()
        sb.table("profile_customizer_settings").upsert(
            {
                "guild_id": str(guild.id),
                "channel_id": str(channel_id),
                "panel_message_id": str(sent.id),
                "show_after_verification": True,
                "require_before_access": False,
                "updated_at": now,
            },
            on_conflict="guild_id",
        ).execute()

        panel_update = {
            "channel_id": str(channel_id),
            "message_id": str(sent.id),
            "updated_at": now,
        }
        query = sb.table("profile_role_panels").update(panel_update).eq("guild_id", str(guild.id))
        normalized_key = _normalize_panel_key(panel_key)
        if normalized_key and normalized_key not in AGGREGATE_PANEL_KEYS:
            query = query.eq("panel_key", normalized_key)
        query.execute()

    await _run_db("persist profile customizer panel message", _write)

    await _write_audit_event(
        guild_id=str(guild.id),
        user_id=None,
        actor_id=requested_by,
        actor_name="Dashboard",
        event_type="profile_customizer_panel_posted",
        title="Profile Customizer Panel Posted",
        reason=f"Posted optional profile customizer panel in #{getattr(channel, 'name', channel_id)}.",
        metadata={
            "channel_id": str(channel_id),
            "message_id": str(sent.id),
            "panel_key": _safe_str(panel_key) or "profile_customizer",
            "source": "bot_command_worker",
        },
    )

    return {
        "posted": True,
        "channel_id": str(channel_id),
        "message_id": str(sent.id),
        "panel_count": len(panels),
        "interactive_controls": False,
        "note": "Panel posted from database configuration. Interaction controls require the dedicated profile interaction handler pass.",
    }


async def sync_profile_roles(
    guild: discord.Guild,
    *,
    dry_run: bool = False,
    requested_by: Optional[str] = None,
) -> Dict[str, Any]:
    choices = await _fetch_active_member_choices(str(guild.id))
    summary: Dict[str, Any] = {
        "synced": True,
        "dry_run": bool(dry_run),
        "choices_checked": len(choices),
        "roles_added": [],
        "already_ok": [],
        "missing_members": [],
        "missing_roles": [],
        "blocked_roles": [],
    }

    for choice in choices:
        user_id = _safe_str(choice.get("user_id"))
        role = _find_role(guild, role_id=choice.get("role_id"), role_name=choice.get("role_name"))
        if not user_id:
            continue

        member = guild.get_member(_safe_int(user_id))
        if member is None:
            try:
                member = await guild.fetch_member(_safe_int(user_id))
            except Exception:
                member = None
        if member is None:
            summary["missing_members"].append(user_id)
            continue

        if role is None:
            summary["missing_roles"].append({
                "user_id": user_id,
                "panel_key": _safe_str(choice.get("panel_key")),
                "option_key": _safe_str(choice.get("option_key")),
                "role_id": _safe_str(choice.get("role_id")) or None,
                "role_name": _safe_str(choice.get("role_name")) or None,
            })
            continue

        manageable, reason = _role_is_manageable(guild, role)
        if not manageable:
            summary["blocked_roles"].append({
                "user_id": user_id,
                "role_id": str(role.id),
                "role_name": role.name,
                "reason": reason,
            })
            continue

        if role in getattr(member, "roles", []):
            summary["already_ok"].append({"user_id": user_id, "role_id": str(role.id)})
            continue

        if not dry_run:
            await member.add_roles(role, reason=f"Profile Customizer sync requested by {requested_by or 'dashboard'}")

        summary["roles_added"].append({
            "user_id": user_id,
            "role_id": str(role.id),
            "role_name": role.name,
            "dry_run": bool(dry_run),
        })

    return summary


async def reset_member_profile_roles(
    guild: discord.Guild,
    *,
    user_id: str,
    panel_key: str = "",
    requested_by: Optional[str] = None,
) -> Dict[str, Any]:
    clean_user_id = _safe_str(user_id)
    if not clean_user_id:
        return {"reset": False, "reason": "missing_user_id"}

    member = guild.get_member(_safe_int(clean_user_id))
    if member is None:
        try:
            member = await guild.fetch_member(_safe_int(clean_user_id))
        except Exception:
            member = None

    if member is None:
        return {"reset": False, "reason": "member_missing", "user_id": clean_user_id}

    choices = await _fetch_active_member_choices(str(guild.id), user_id=clean_user_id, panel_key=panel_key)
    removed_choice_ids: List[str] = []
    removed_roles: List[Dict[str, Any]] = []
    missing_roles: List[Dict[str, Any]] = []
    blocked_roles: List[Dict[str, Any]] = []

    for choice in choices:
        role = _find_role(guild, role_id=choice.get("role_id"), role_name=choice.get("role_name"))
        choice_id = _safe_str(choice.get("id"))

        if role is None:
            missing_roles.append({
                "choice_id": choice_id,
                "panel_key": _safe_str(choice.get("panel_key")),
                "option_key": _safe_str(choice.get("option_key")),
                "role_id": _safe_str(choice.get("role_id")) or None,
                "role_name": _safe_str(choice.get("role_name")) or None,
            })
            if choice_id:
                removed_choice_ids.append(choice_id)
            continue

        manageable, reason = _role_is_manageable(guild, role)
        if not manageable:
            blocked_roles.append({
                "choice_id": choice_id,
                "role_id": str(role.id),
                "role_name": role.name,
                "reason": reason,
            })
            continue

        if role in getattr(member, "roles", []):
            await member.remove_roles(role, reason=f"Profile Customizer reset requested by {requested_by or 'dashboard'}")
            removed_roles.append({"role_id": str(role.id), "role_name": role.name})

        if choice_id:
            removed_choice_ids.append(choice_id)

    await _mark_choices_removed(removed_choice_ids, actor_id=requested_by)

    await _write_audit_event(
        guild_id=str(guild.id),
        user_id=clean_user_id,
        actor_id=requested_by,
        actor_name="Dashboard",
        event_type="profile_roles_reset",
        title="Profile Roles Reset",
        reason="Optional profile roles were reset for this member.",
        metadata={
            "panel_key": _safe_str(panel_key) or None,
            "removed_choice_count": len(removed_choice_ids),
            "removed_roles": removed_roles,
            "missing_roles": missing_roles,
            "blocked_roles": blocked_roles,
            "source": "bot_command_worker",
        },
    )

    return {
        "reset": True,
        "user_id": clean_user_id,
        "panel_key": _safe_str(panel_key) or None,
        "active_choices_found": len(choices),
        "choices_cleared": len(removed_choice_ids),
        "roles_removed": removed_roles,
        "missing_roles": missing_roles,
        "blocked_roles": blocked_roles,
    }
