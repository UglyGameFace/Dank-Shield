from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

import discord

from ..globals import get_supabase, now_utc


# ============================================================
# Member sync service for the NEW structure
# ------------------------------------------------------------
# IMPORTANT:
# Your real Supabase schema uses `guild_members`, not `members`.
#
# This file keeps the richer helper structure you already added,
# but the actual live sync/write operations delegate to your
# EXISTING legacy sync logic in stoney_verify/events.py.
#
# Why:
# - preserves your real schema
# - preserves guild_members history logic
# - avoids breaking current dashboard expectations
# - keeps helper serialization available for future dashboard use
# ============================================================


def _get_legacy_events_module():
    """
    Lazy import avoids circular imports during startup.
    """
    from .. import events as legacy_events
    return legacy_events


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
        return str(x)
    except Exception:
        return ""


def _display_avatar_url(member: discord.abc.User) -> Optional[str]:
    try:
        if getattr(member, "display_avatar", None):
            return member.display_avatar.url  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        if getattr(member, "avatar", None):
            return member.avatar.url  # type: ignore[attr-defined]
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
    """
    Dashboard-friendly role payload for future interactive role/member views.
    Kept here intentionally for later dashboard-side enrichment.
    """
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


def _has_verified_role(member: discord.Member) -> bool:
    try:
        for role in member.roles:
            try:
                if str(role.name).strip().lower() == "verified":
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _member_row(member: discord.Member, *, active: bool = True, departed: bool = False) -> Dict[str, Any]:
    """
    Canonical future-facing member payload.

    NOTE:
    This is intentionally retained for future dashboard enrichment, but the
    current live database write path delegates to legacy guild_members sync.
    """
    now = now_utc()

    row: Dict[str, Any] = {
        "id": str(member.id),
        "discord_id": str(member.id),
        "username": _safe_str(member),
        "display_name": _safe_str(getattr(member, "display_name", "")),
        "name": _safe_str(getattr(member, "name", "")),
        "global_name": _safe_str(getattr(member, "global_name", "")),
        "avatar": _display_avatar_url(member),
        "verified": _has_verified_role(member),
        "roles": _serialize_role_ids(member),
        "role_names": _serialize_role_names(member),
        "role_summary": _role_summary(member),
        "guild_id": str(member.guild.id) if getattr(member, "guild", None) else None,
        "joined_at": _utc_iso(getattr(member, "joined_at", None)),
        "created_at_discord": _utc_iso(getattr(member, "created_at", None)),
        "bot": bool(getattr(member, "bot", False)),
        "active": bool(active),
        "departed": bool(departed),
        "last_synced_at": _utc_iso(now),
    }

    try:
        row["nick"] = _safe_str(getattr(member, "nick", "")) or None
    except Exception:
        row["nick"] = None

    return row


def _upsert(table: str, payload: Dict[str, Any], *, on_conflict: str = "id") -> bool:
    """
    Retained for future use.

    IMPORTANT:
    This helper is no longer used by the live member sync flow because your
    real schema does NOT use a `members` table.
    """
    sb = get_supabase()
    if sb is None:
        print(f"⚠️ Supabase unavailable; skipped upsert into {table}.")
        return False

    try:
        sb.table(table).upsert(payload, on_conflict=on_conflict).execute()
        return True
    except TypeError:
        try:
            sb.table(table).upsert(payload).execute()
            return True
        except Exception as e:
            print(f"❌ Upsert failed for {table}:", repr(e))
            return False
    except Exception as e:
        print(f"❌ Upsert failed for {table}:", repr(e))
        return False


def _update_member_departed(member_id: int | str, *, active: bool, departed: bool) -> bool:
    """
    Retained for future use.

    IMPORTANT:
    This helper is no longer used by the live member sync flow because your
    real schema does NOT use a `members` table.
    """
    sb = get_supabase()
    if sb is None:
        print("⚠️ Supabase unavailable; skipped departed member update.")
        return False

    payload = {
        "active": bool(active),
        "departed": bool(departed),
        "last_synced_at": _utc_iso(now_utc()),
    }

    try:
        sb.table("members").update(payload).eq("id", str(member_id)).execute()
        return True
    except Exception as e:
        print("❌ Member departed update failed:", repr(e))
        return False


def _load_live_members(guild: discord.Guild) -> List[discord.Member]:
    """
    Best-effort cached member list only.
    Async fetching happens in the async callers.
    """
    try:
        return list(guild.members)
    except Exception:
        return []


async def _ensure_member_list(guild: discord.Guild) -> List[discord.Member]:
    """
    Best-effort member resolution with cache first, then fetch_members fallback.
    """
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
    """
    Read tracked guild_members rows for this guild.
    """
    sb = get_supabase()
    if sb is None:
        return []

    try:
        res = (
            sb.table("guild_members")
            .select("guild_id,user_id,in_guild")
            .eq("guild_id", str(guild_id))
            .execute()
        )
        rows = getattr(res, "data", None) or []
        return [r for r in rows if isinstance(r, dict)]
    except Exception as e:
        print(f"❌ Failed reading guild_members for guild {guild_id}:", repr(e))
        return []


async def sync_member(member: discord.Member, *, active: bool = True, departed: bool = False) -> bool:
    """
    Sync one member using the EXISTING legacy guild_members logic.

    Called on:
    - member join
    - member update
    - manual sync / reconciliation
    """
    try:
        legacy_events = _get_legacy_events_module()

        if hasattr(legacy_events, "_sync_member_to_supabase"):
            await legacy_events._sync_member_to_supabase(
                member,
                in_guild=bool(active and not departed),
            )
            print(f"✅ members_new.sync_member delegated → {member} ({member.id})")
            return True

        print("⚠️ Legacy _sync_member_to_supabase helper not found.")
        return False

    except Exception as e:
        print(f"❌ members_new.sync_member failed for {member} ({member.id}):", repr(e))
        return False


async def sync_member_remove(member: discord.Member) -> bool:
    """
    Mark a member as departed instead of hard-deleting them.

    This preserves history for:
    - ghost/member archive views
    - kicked/banned lookup
    - previously used usernames when they rejoin

    Uses the EXISTING legacy guild_members logic.
    """
    try:
        legacy_events = _get_legacy_events_module()

        if hasattr(legacy_events, "_mark_member_left"):
            await legacy_events._mark_member_left(member)
            print(f"✅ members_new.sync_member_remove delegated → {member} ({member.id})")
            return True

        if hasattr(legacy_events, "_sync_member_to_supabase"):
            await legacy_events._sync_member_to_supabase(member, in_guild=False)
            print(f"✅ members_new.sync_member_remove fallback delegated → {member} ({member.id})")
            return True

        print("⚠️ Legacy member-left helpers not found.")
        return False

    except Exception as e:
        print(f"❌ members_new.sync_member_remove failed for {member} ({member.id}):", repr(e))
        return False


async def sync_all_members(guild: discord.Guild) -> Dict[str, int]:
    """
    Full reconciliation pass using your EXISTING guild_members sync.
    Safe for startup or manual dashboard-triggered sync.

    Returns summary counts.
    """
    processed = 0
    failed = 0

    members = await _ensure_member_list(guild)

    for member in members:
        try:
            ok = await sync_member(member, active=True, departed=False)
            if ok:
                processed += 1
            else:
                failed += 1
        except Exception as e:
            failed += 1
            print(f"❌ Full member sync failed for {member} ({member.id}):", repr(e))

    summary = {"processed": processed, "failed": failed, "total_seen": len(members)}
    print("🧩 Full member sync summary:", summary)
    return summary


async def reconcile_departed_members(guild: discord.Guild) -> Dict[str, int]:
    """
    Lightweight departed-member reconciliation.

    IMPORTANT:
    This must NOT call legacy_events._initial_member_sync_sweep(),
    because that re-runs the entire full sync and causes duplicate
    startup work / event loop blocking.

    What this does:
    - loads current live guild member IDs
    - loads tracked DB rows from guild_members
    - marks rows missing from the live guild as departed via legacy
      _mark_member_left when possible
    """
    checked = 0
    marked_departed = 0

    try:
        members = await _ensure_member_list(guild)
        active_ids: Set[int] = set()

        for member in members:
            try:
                active_ids.add(int(member.id))
            except Exception:
                continue

        rows = _guild_member_rows_for_guild(int(guild.id))
        checked = len(rows)

        legacy_events = _get_legacy_events_module()
        can_mark_left = hasattr(legacy_events, "_mark_member_left")

        for row in rows:
            try:
                user_id = int(str(row.get("user_id") or "0") or 0)
            except Exception:
                user_id = 0

            if user_id <= 0:
                continue

            if user_id in active_ids:
                continue

            # Already departed in DB — do not churn it again
            try:
                in_guild = row.get("in_guild")
                if in_guild is False:
                    continue
            except Exception:
                pass

            fake_member: Optional[discord.Member] = None
            try:
                fake_member = guild.get_member(user_id)
            except Exception:
                fake_member = None

            # Normal path: if somehow cached, use real member object
            if isinstance(fake_member, discord.Member):
                try:
                    await sync_member_remove(fake_member)
                    marked_departed += 1
                except Exception as e:
                    print(f"❌ Failed departed sync for cached member {user_id}:", repr(e))
                continue

            # Fallback path: direct DB update without doing a full legacy sweep
            try:
                sb = get_supabase()
                if sb is None:
                    continue

                now_iso = _utc_iso(now_utc())
                payload = {
                    "in_guild": False,
                    "data_health": "left_guild",
                    "synced_at": now_iso,
                    "updated_at": now_iso,
                    "left_at": row.get("left_at") or now_iso,
                }

                # Best effort times_left increment
                try:
                    times_left = int(row.get("times_left") or 0) + 1
                    payload["times_left"] = times_left
                except Exception:
                    pass

                sb.table("guild_members").update(payload).eq("guild_id", str(guild.id)).eq("user_id", str(user_id)).execute()
                marked_departed += 1
            except Exception as e:
                print(f"❌ Failed lightweight departed update for user {user_id}:", repr(e))

        print(
            f"🧹 Departed reconciliation complete for guild {guild.id}: "
            f"checked={checked} marked_departed={marked_departed}"
        )
        return {
            "checked": checked,
            "marked_departed": marked_departed,
        }

    except Exception as e:
        print("❌ Failed to run departed reconciliation:", repr(e))
        return {"checked": checked, "marked_departed": marked_departed}


async def sync_role_members(role: discord.Role) -> Dict[str, int]:
    """
    Force-resync all members who currently have a given role.
    Useful for interactive dashboard role refresh actions.

    Uses the EXISTING legacy guild_members sync.
    """
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