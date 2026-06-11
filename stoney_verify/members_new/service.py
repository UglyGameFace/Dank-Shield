from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import discord

from .. import role_truth
from ..globals import get_supabase, now_utc
from .sync_service import (
    mark_member_left as sync_service_mark_member_left,
    run_departed_reconciliation_for_guild as sync_service_run_departed_reconciliation_for_guild,
    run_full_member_sync_for_guild as sync_service_run_full_member_sync_for_guild,
    sync_member_to_supabase,
)

# ============================================================
# Member sync service for the NEW structure
# ------------------------------------------------------------
# Real orchestrator for member truth.
# ============================================================


def _utc_iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        try:
            return dt.isoformat()
        except Exception:
            return None


def _safe_str(x: Any) -> str:
    try:
        return str(x or "")
    except Exception:
        return ""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _truthy(value: Any, default: bool = False) -> bool:
    try:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        return bool(value)
    except Exception:
        return default


def _display_avatar_url(member: discord.abc.User) -> Optional[str]:
    try:
        if getattr(member, "display_avatar", None):
            return str(member.display_avatar.url)  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        if getattr(member, "avatar", None):
            return str(member.avatar.url)  # type: ignore[attr-defined]
    except Exception:
        pass
    return None


def _serialize_role_ids(member: discord.Member) -> List[int]:
    out: List[int] = []
    try:
        for role in member.roles:
            try:
                if role.name == "@everyone":
                    continue
                out.append(int(role.id))
            except Exception:
                continue
    except Exception:
        pass
    return out


def _serialize_role_names(member: discord.Member) -> List[str]:
    out: List[str] = []
    try:
        for role in member.roles:
            try:
                if role.name == "@everyone":
                    continue
                out.append(str(role.name))
            except Exception:
                continue
    except Exception:
        pass
    return out


def _role_summary(member: discord.Member) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        for role in member.roles:
            try:
                if role.name == "@everyone":
                    continue
                out.append(
                    {
                        "id": str(role.id),
                        "name": role.name,
                        "position": int(getattr(role, "position", 0)),
                        "color": getattr(role.color, "value", 0) if getattr(role, "color", None) else 0,
                    }
                )
            except Exception:
                continue
    except Exception:
        pass
    return out


def _member_has_role_id(member: discord.Member, role_id: int) -> bool:
    return bool(role_truth.member_has_role_id(member, role_id))


def _member_role_flags(member: discord.Member) -> Dict[str, bool]:
    truth = role_truth.member_role_truth(member)
    return {
        "has_any_role": bool(truth.get("has_any_role")),
        "has_unverified": bool(truth.get("has_unverified")),
        "has_verified_role": bool(truth.get("has_verified_role")),
        "has_staff_role": bool(truth.get("has_staff_role")),
        "has_secondary_verified_role": bool(truth.get("has_secondary_verified_role")),
        "has_cosmetic_only": bool(truth.get("has_cosmetic_only")),
    }


def _role_state(member: discord.Member) -> Dict[str, str]:
    state, reason = role_truth.role_state_from_truth(member)
    return {"role_state": state, "role_state_reason": reason}


def _has_verified_role(member: discord.Member) -> bool:
    try:
        truth = role_truth.member_role_truth(member)
        return bool(truth.get("has_verified_role") and not truth.get("has_unverified"))
    except Exception:
        return False


def _member_row(member: discord.Member, *, active: bool = True, departed: bool = False) -> Dict[str, Any]:
    now = now_utc()
    role_flags = _member_role_flags(member)
    role_state = _role_state(member)

    row: Dict[str, Any] = {
        "id": str(member.id),
        "discord_id": str(member.id),
        "user_id": str(member.id),
        "username": _safe_str(member),
        "display_name": _safe_str(getattr(member, "display_name", "")),
        "name": _safe_str(getattr(member, "name", "")),
        "global_name": _safe_str(getattr(member, "global_name", "")),
        "avatar": _display_avatar_url(member),
        "avatar_url": _display_avatar_url(member),
        "verified": _has_verified_role(member),
        "roles": _serialize_role_ids(member),
        "role_ids": _serialize_role_ids(member),
        "role_names": _serialize_role_names(member),
        "role_summary": _role_summary(member),
        "guild_id": str(member.guild.id) if getattr(member, "guild", None) else None,
        "joined_at": _utc_iso(getattr(member, "joined_at", None)),
        "created_at_discord": _utc_iso(getattr(member, "created_at", None)),
        "bot": bool(getattr(member, "bot", False)),
        "is_bot": bool(getattr(member, "bot", False)),
        "active": bool(active and not departed),
        "departed": bool(departed),
        "in_guild": bool(active and not departed),
        "last_synced_at": _utc_iso(now),
        "synced_at": _utc_iso(now),
        "updated_at": _utc_iso(now),
        "last_seen_at": _utc_iso(now),
        "last_seen_username": _safe_str(member),
        "last_seen_display_name": _safe_str(getattr(member, "display_name", "")),
        "last_seen_nickname": _safe_str(getattr(member, "nick", "")),
    }

    try:
        row["nick"] = _safe_str(getattr(member, "nick", "")) or None
    except Exception:
        row["nick"] = None

    row.update(role_flags)
    row.update(role_state)
    return row


def _load_live_members(guild: discord.Guild) -> List[discord.Member]:
    try:
        return list(guild.members)
    except Exception:
        return []


async def _ensure_member_list(guild: discord.Guild) -> List[discord.Member]:
    try:
        if not guild.chunked:
            try:
                await guild.chunk(cache=True)
            except Exception:
                pass
    except Exception:
        pass

    members = _load_live_members(guild)
    if members:
        return members

    out: List[discord.Member] = []
    try:
        async for m in guild.fetch_members(limit=None):
            out.append(m)
    except Exception as e:
        print("❌ fetch_members failed:", repr(e))
    return out


def _guild_member_rows_for_guild(guild_id: int) -> List[Dict[str, Any]]:
    sb = get_supabase()
    if sb is None:
        return []

    try:
        res = (
            sb.table("guild_members")
            .select("*")
            .eq("guild_id", str(guild_id))
            .execute()
        )
        rows = getattr(res, "data", None) or []
        return [dict(r) for r in rows if isinstance(r, dict)]
    except Exception as e:
        print(f"❌ Failed reading guild_members for guild {guild_id}:", repr(e))
        return []


def _best_effort_mark_departed_by_user_id_sync(
    *,
    guild_id: int,
    user_id: int,
    username: Optional[str] = None,
    avatar_url: Optional[str] = None,
    is_bot: bool = False,
) -> bool:
    sb = get_supabase()
    if sb is None:
        print("⚠️ Supabase unavailable; skipped best-effort departed update.")
        return False

    now_iso = _utc_iso(now_utc())
    if not now_iso:
        now_iso = datetime.now(timezone.utc).isoformat()

    existing: Optional[Dict[str, Any]] = None
    try:
        res = (
            sb.table("guild_members")
            .select("*")
            .eq("guild_id", str(guild_id))
            .eq("user_id", str(user_id))
            .limit(1)
            .execute()
        )
        rows = getattr(res, "data", None) or []
        if rows:
            existing = dict(rows[0])
    except Exception as e:
        print(
            f"❌ Failed reading existing guild_members row for departed user {user_id} "
            f"in guild {guild_id}: {repr(e)}"
        )
        existing = None

    if existing:
        times_left = _safe_int(existing.get("times_left"), 0)
        if _truthy(existing.get("in_guild"), False):
            times_left += 1

        payload: Dict[str, Any] = {
            "username": username or existing.get("username") or existing.get("last_seen_username") or str(user_id),
            "display_name": existing.get("display_name") or username or str(user_id),
            "avatar_url": avatar_url or existing.get("avatar_url"),
            "in_guild": False,
            "active": False,
            "departed": True,
            "data_health": "left_guild",
            "synced_at": now_iso,
            "updated_at": now_iso,
            "last_seen_at": now_iso,
            "left_at": now_iso,
            "times_left": times_left,
            "role_ids": [],
            "role_names": [],
            "roles": [],
            "role_summary": [],
            "has_any_role": False,
            "has_unverified": False,
            "has_verified_role": False,
            "has_staff_role": False,
            "has_secondary_verified_role": False,
            "has_cosmetic_only": False,
            "role_state": "left_guild",
            "role_state_reason": "Member left or was removed from guild.",
            "is_bot": bool(existing.get("is_bot")) or bool(is_bot),
            "verified": False,
        }

        try:
            sb.table("guild_members").update(payload).eq("guild_id", str(guild_id)).eq("user_id", str(user_id)).execute()
            return True
        except Exception as e:
            print(
                f"❌ Failed updating existing guild_members departed row for user {user_id} "
                f"in guild {guild_id}: {repr(e)}"
            )
            return False

    payload = {
        "guild_id": str(guild_id),
        "user_id": str(user_id),
        "discord_id": str(user_id),
        "username": username or str(user_id),
        "display_name": username or str(user_id),
        "avatar_url": avatar_url,
        "in_guild": False,
        "active": False,
        "departed": True,
        "data_health": "left_guild",
        "synced_at": now_iso,
        "updated_at": now_iso,
        "last_seen_at": now_iso,
        "left_at": now_iso,
        "times_joined": 1,
        "times_left": 1,
        "role_ids": [],
        "role_names": [],
        "roles": [],
        "role_summary": [],
        "has_any_role": False,
        "has_unverified": False,
        "has_verified_role": False,
        "has_staff_role": False,
        "has_secondary_verified_role": False,
        "has_cosmetic_only": False,
        "role_state": "left_guild",
        "role_state_reason": "Member left or was removed from guild.",
        "is_bot": bool(is_bot),
        "bot": bool(is_bot),
        "verified": False,
        "created_at": now_iso,
        "first_seen_at": now_iso,
        "last_seen_username": username or str(user_id),
        "last_seen_display_name": username or str(user_id),
        "last_seen_nickname": "",
    }

    try:
        try:
            sb.table("guild_members").upsert(payload, on_conflict="guild_id,user_id").execute()
        except TypeError:
            sb.table("guild_members").upsert(payload).execute()
        return True
    except Exception as e:
        print(
            f"❌ Failed inserting best-effort departed guild_members row for user {user_id} "
            f"in guild {guild_id}: {repr(e)}"
        )
        return False


async def sync_member(member: discord.Member, *, active: bool = True, departed: bool = False) -> bool:
    try:
        await sync_member_to_supabase(
            member,
            in_guild=bool(active and not departed),
        )
        print(f"✅ members_new.sync_member → {member} ({member.id})")
        return True
    except Exception as e:
        print(f"❌ members_new.sync_member failed for {member} ({member.id}):", repr(e))
        return False


async def sync_member_remove(
    member_or_user: discord.Member | discord.User | discord.Object | Any,
    guild: Optional[discord.Guild] = None,
) -> bool:
    try:
        if isinstance(member_or_user, discord.Member):
            await sync_service_mark_member_left(member_or_user)
            print(f"✅ members_new.sync_member_remove → {member_or_user} ({member_or_user.id})")
            return True

        if guild is None:
            print("⚠️ sync_member_remove received non-Member without guild.")
            return False

        user_id = _safe_int(getattr(member_or_user, "id", None), 0)
        if user_id <= 0:
            print("⚠️ sync_member_remove could not resolve user id.")
            return False

        cached_member = None
        try:
            cached_member = guild.get_member(user_id)
        except Exception:
            cached_member = None

        if isinstance(cached_member, discord.Member):
            await sync_service_mark_member_left(cached_member)
            print(f"✅ members_new.sync_member_remove cached-member → {cached_member} ({cached_member.id})")
            return True

        username = None
        try:
            username = _safe_str(getattr(member_or_user, "name", None) or getattr(member_or_user, "global_name", None) or member_or_user)
        except Exception:
            username = str(user_id)

        avatar_url = None
        try:
            avatar_url = _display_avatar_url(member_or_user)  # type: ignore[arg-type]
        except Exception:
            avatar_url = None

        is_bot = bool(getattr(member_or_user, "bot", False))

        ok = _best_effort_mark_departed_by_user_id_sync(
            guild_id=int(guild.id),
            user_id=user_id,
            username=username,
            avatar_url=avatar_url,
            is_bot=is_bot,
        )

        if ok:
            print(f"✅ members_new.sync_member_remove best-effort → {username} ({user_id})")
        else:
            print(f"❌ members_new.sync_member_remove best-effort failed → {username} ({user_id})")
        return ok

    except Exception as e:
        try:
            subject = f"{member_or_user} ({getattr(member_or_user, 'id', 'unknown')})"
        except Exception:
            subject = "unknown-member"
        print(f"❌ members_new.sync_member_remove failed for {subject}:", repr(e))
        return False


async def sync_all_members(guild: discord.Guild) -> Dict[str, int]:
    try:
        raw = await sync_service_run_full_member_sync_for_guild(guild)
        active_synced = _safe_int(raw.get("active_members_synced"), 0)
        marked_departed = _safe_int(raw.get("marked_departed"), 0)
        errors = _safe_int(raw.get("errors"), 0)
        checked = _safe_int(raw.get("checked"), active_synced)

        summary = {
            "processed": active_synced,
            "failed": errors,
            "total_seen": checked,
            "marked_departed": marked_departed,
        }
        print("🧩 Full member sync summary:", summary)
        return summary
    except Exception as e:
        print(f"❌ Full member sync failed for guild {guild.id}:", repr(e))
        return {
            "processed": 0,
            "failed": 1,
            "total_seen": 0,
            "marked_departed": 0,
        }


async def reconcile_departed_members(guild: discord.Guild) -> Dict[str, int]:
    try:
        raw = await sync_service_run_departed_reconciliation_for_guild(guild)
        summary = {
            "checked": _safe_int(raw.get("checked"), 0),
            "marked_departed": _safe_int(raw.get("marked_departed"), 0),
        }
        print(
            f"🧹 Departed reconciliation complete for guild {guild.id}: "
            f"checked={summary['checked']} marked_departed={summary['marked_departed']}"
        )
        return summary
    except Exception as e:
        print("❌ Failed to run departed reconciliation:", repr(e))
        return {
            "checked": 0,
            "marked_departed": 0,
        }


async def sync_role_members(role: discord.Role) -> Dict[str, int]:
    processed = 0
    failed = 0

    try:
        for member in role.members:
            try:
                ok = await sync_member(member, active=True, departed=False)
                if ok:
                    processed += 1
                else:
                    failed += 1
            except Exception as e:
                failed += 1
                print(f"❌ Role member sync failed for {member} ({member.id}):", repr(e))
    except Exception as e:
        print("❌ sync_role_members failed:", repr(e))

    summary = {
        "role_id": int(role.id),
        "role_name": role.name,
        "processed": processed,
        "failed": failed,
    }
    print("🎭 Role member sync summary:", summary)
    return summary


__all__ = [
    "sync_member",
    "sync_member_remove",
    "sync_all_members",
    "reconcile_departed_members",
    "sync_role_members",
    "_member_row",
]
