from __future__ import annotations

"""Durable service boundary for Dank Shield Community Hub.

Discord callbacks and Gateway listeners must not perform ad-hoc table writes.
This module owns Community Hub persistence, DB atomic transitions, aggregate
analytics reads, managed-resource provenance, notification preferences, event
records, and partner-link records.

Supabase's Python client is synchronous, so every call is moved off the Discord
event loop with asyncio.to_thread.
"""

import asyncio
import hashlib
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .globals import get_supabase


ACTIVE_SESSION_STATES = (
    "creating",
    "open",
    "forming",
    "ready",
    "active",
    "paused",
    "ending",
    "interrupted",
    "recovering",
)
JOINABLE_SESSION_STATES = ("open", "forming", "ready", "active")
ENDED_SESSION_STATES = ("ended", "archived", "cleaned")
RECOVERY_RESOURCE_STATES = ("planned", "active", "unresolved", "failed")

SESSION_STATES = frozenset(
    {
        "creating",
        "open",
        "forming",
        "ready",
        "active",
        "paused",
        "ending",
        "ended",
        "archived",
        "cleaned",
        "abandoned",
        "interrupted",
        "recovering",
        "failed",
    }
)

_SAFE_GAME_RE = re.compile(r"[^\w\s+:#'().&/-]", re.UNICODE)
_SAFE_NOTE_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_HUBLINK_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_HUBLINK_LENGTH = 8


class CommunityHubError(RuntimeError):
    pass


class CommunityStorageUnavailable(CommunityHubError):
    pass


class CommunityNotFound(CommunityHubError):
    pass


class CommunityConflict(CommunityHubError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return int(default)
        return int(str(value).strip())
    except Exception:
        return int(default)


def _safe_str(value: Any, default: str = "") -> str:
    try:
        text = str(value or "").strip()
        return text or default
    except Exception:
        return default


def normalize_game_name(value: Any) -> str:
    text = _SAFE_GAME_RE.sub("", _safe_str(value))
    text = " ".join(text.split())
    if not text:
        raise CommunityHubError("Enter a game name.")
    return text[:80]


def game_key(value: Any) -> str:
    text = normalize_game_name(value).casefold()
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"[^a-z0-9+#.&/-]", "", text)
    return (text or "game")[:80]


def normalize_notes(value: Any) -> str:
    text = _SAFE_NOTE_RE.sub("", _safe_str(value))
    return text[:500]


def normalize_hublink_code(value: Any) -> str:
    compact = re.sub(r"[^A-Z0-9]", "", _safe_str(value).upper())
    if compact.startswith("DANK"):
        compact = compact[4:]
    if len(compact) != _HUBLINK_LENGTH or any(ch not in _HUBLINK_ALPHABET for ch in compact):
        raise CommunityHubError("Enter a valid HubLink code, like DANK-ABCD-2345.")
    return compact


def format_hublink_code(value: Any) -> str:
    compact = normalize_hublink_code(value)
    return f"DANK-{compact[:4]}-{compact[4:]}"


def _hublink_digest(value: Any) -> str:
    compact = normalize_hublink_code(value)
    return hashlib.sha256(compact.encode("ascii")).hexdigest()


def _uuid_text(value: Any) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except Exception as exc:
        raise CommunityHubError("Invalid Community Hub session identifier.") from exc


def _require_supabase() -> Any:
    try:
        sb = get_supabase()
    except Exception as exc:
        raise CommunityStorageUnavailable("Community Hub storage is unavailable.") from exc
    if sb is None:
        raise CommunityStorageUnavailable("Community Hub storage is unavailable.")
    return sb


def _rows(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None) or []
    return [dict(row) for row in data if isinstance(row, dict)]


def _one(response: Any) -> dict[str, Any] | None:
    rows = _rows(response)
    return rows[0] if rows else None


def _rpc_payload(response: Any) -> dict[str, Any]:
    data = getattr(response, "data", None)
    if isinstance(data, dict):
        return dict(data)
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return dict(data[0])
    raise CommunityHubError("Community Hub did not receive a valid database result.")


async def _db_call(factory) -> Any:
    try:
        return await asyncio.to_thread(factory)
    except CommunityHubError:
        raise
    except Exception as exc:
        text = str(exc or "")
        lowered = text.lower()
        if "pgrst205" in lowered or "schema cache" in lowered or "could not find the table" in lowered:
            raise CommunityStorageUnavailable(
                "Community Hub database migration is not available yet."
            ) from exc
        if any(
            marker in lowered
            for marker in (
                "session not found",
                "member is not in this session",
            )
        ):
            raise CommunityNotFound(text[:300]) from exc
        if any(
            marker in lowered
            for marker in (
                "limit reached",
                "not joinable",
                "session is locked",
                "authority required",
                "invalid session transition",
                "cannot be finalized",
                "only an ended session",
                "only the previous host",
                "not accepting new sessions",
                "creation is disabled",
                "outside this server",
                "match safety exclusion",
                "idempotency key belongs",
                "minimum players to start",
                "all active players must be ready",
                "hourly session creation limit",
                "session creation cooldown",
                "hourly Community Hub report limit",
                "Only a participant can report",
                "only a participant can report",
                "has not enabled partner discovery",
                "choose a different partner server",
                "community event is no longer accepting responses",
                "session replay history has expired",
                "hublink",
            )
        ):
            raise CommunityConflict(text[:300]) from exc
        raise CommunityHubError(text[:500] or type(exc).__name__) from exc


async def get_settings(guild_id: int | str, *, create: bool = True) -> dict[str, Any]:
    gid = _safe_str(guild_id)
    if not gid:
        raise CommunityHubError("Guild is required.")

    def _read() -> dict[str, Any] | None:
        sb = _require_supabase()
        response = (
            sb.table("dank_community_hub_settings")
            .select("*")
            .eq("guild_id", gid)
            .limit(1)
            .execute()
        )
        return _one(response)

    row = await _db_call(_read)
    if row or not create:
        return row or {}

    def _create() -> dict[str, Any]:
        sb = _require_supabase()
        response = (
            sb.table("dank_community_hub_settings")
            .upsert({"guild_id": gid}, on_conflict="guild_id")
            .execute()
        )
        return _one(response) or {"guild_id": gid}

    return await _db_call(_create)


_ALLOWED_SETTINGS = frozenset(
    {
        "enabled",
        "mode",
        "maintenance_mode",
        "parent_category_id",
        "hub_channel_id",
        "staff_log_channel_id",
        "cleanup_grace_seconds",
        "idle_timeout_seconds",
        "max_session_lifetime_seconds",
        "max_active_sessions",
        "max_active_sessions_per_member",
        "session_creation_cooldown_seconds",
        "max_session_creations_per_hour_per_member",
        "max_reports_per_hour_per_member",
        "max_temporary_voice_rooms",
        "max_session_capacity",
        "minimum_players_to_start",
        "require_ready_to_start",
        "ready_timeout_seconds",
        "notifications_enabled",
        "max_notifications_per_hour",
        "notification_cooldown_seconds",
        "max_notification_targets_per_dispatch",
        "allow_member_creation",
        "auto_create_thread",
        "auto_create_voice",
        "archive_threads_on_end",
        "analytics_enabled",
        "presence_analytics_enabled",
        "partner_discovery_enabled",
        "detailed_retention_days",
        "aggregate_retention_days",
    }
)


async def update_settings(
    guild_id: int | str,
    patch: dict[str, Any],
    *,
    actor_id: int | str | None = None,
) -> dict[str, Any]:
    gid = _safe_str(guild_id)
    clean = {key: value for key, value in dict(patch or {}).items() if key in _ALLOWED_SETTINGS}
    if not clean:
        return await get_settings(gid)
    clean["guild_id"] = gid
    clean["updated_by"] = _safe_str(actor_id) or None
    clean["updated_at"] = utc_now_iso()

    def _write() -> dict[str, Any]:
        sb = _require_supabase()
        response = (
            sb.table("dank_community_hub_settings")
            .upsert(clean, on_conflict="guild_id")
            .execute()
        )
        return _one(response) or clean

    row = await _db_call(_write)
    await record_event(
        gid,
        "settings.updated",
        actor_id=actor_id,
        metadata={"keys": sorted(key for key in clean if key not in {"guild_id", "updated_by", "updated_at"})},
    )
    return row


async def create_session(
    *,
    guild_id: int | str,
    host_id: int | str,
    idempotency_key: str,
    game_name: str,
    notes: str = "",
    capacity: int = 6,
    mic_preference: str = "optional",
    play_style: str = "casual",
    privacy: str = "public",
) -> dict[str, Any]:
    gid = _safe_str(guild_id)
    uid = _safe_str(host_id)
    game = normalize_game_name(game_name)
    clean_notes = normalize_notes(notes)
    cap = max(2, min(99, _safe_int(capacity, 6)))
    if mic_preference not in {"optional", "preferred", "required", "no_mic"}:
        mic_preference = "optional"
    if play_style not in {"casual", "competitive", "beginner", "any"}:
        play_style = "casual"
    if privacy not in {"public", "invite_only", "locked"}:
        privacy = "public"
    key = _safe_str(idempotency_key)[:240]
    if not key:
        raise CommunityHubError("A session idempotency key is required.")

    def _write() -> dict[str, Any]:
        sb = _require_supabase()
        response = sb.rpc(
            "community_hub_create_session",
            {
                "p_guild_id": gid,
                "p_host_id": uid,
                "p_idempotency_key": key,
                "p_game_name": game,
                "p_notes": clean_notes,
                "p_capacity": cap,
                "p_mic_preference": mic_preference,
                "p_play_style": play_style,
                "p_privacy": privacy,
            },
        ).execute()
        return _rpc_payload(response)

    return await _db_call(_write)


async def get_session(session_id: str, *, guild_id: int | str | None = None) -> dict[str, Any]:
    sid = _uuid_text(session_id)
    gid = _safe_str(guild_id)

    def _read() -> dict[str, Any] | None:
        sb = _require_supabase()
        query = sb.table("dank_community_sessions").select("*").eq("id", sid)
        if gid:
            query = query.eq("guild_id", gid)
        return _one(query.limit(1).execute())

    row = await _db_call(_read)
    if not row:
        raise CommunityNotFound("Community Hub session not found.")
    return row


async def get_session_by_panel_message(
    guild_id: int | str,
    message_id: int | str,
) -> dict[str, Any] | None:
    gid = _safe_str(guild_id)
    mid = _safe_str(message_id)
    if not gid or not mid:
        return None

    def _read() -> dict[str, Any] | None:
        sb = _require_supabase()
        return _one(
            sb.table("dank_community_sessions")
            .select("*")
            .eq("guild_id", gid)
            .eq("panel_message_id", mid)
            .limit(1)
            .execute()
        )

    return await _db_call(_read)


async def list_active_sessions(guild_id: int | str, *, limit: int = 25) -> list[dict[str, Any]]:
    gid = _safe_str(guild_id)
    safe_limit = max(1, min(100, _safe_int(limit, 25)))

    def _read() -> list[dict[str, Any]]:
        sb = _require_supabase()
        return _rows(
            sb.table("dank_community_sessions")
            .select("*")
            .eq("guild_id", gid)
            .in_("state", list(ACTIVE_SESSION_STATES))
            .order("last_activity_at", desc=True)
            .limit(safe_limit)
            .execute()
        )

    return await _db_call(_read)


async def list_user_sessions(
    guild_id: int | str,
    user_id: int | str,
    *,
    active_only: bool = True,
    limit: int = 25,
) -> list[dict[str, Any]]:
    gid = _safe_str(guild_id)
    uid = _safe_str(user_id)
    safe_limit = max(1, min(100, _safe_int(limit, 25)))

    def _read() -> list[dict[str, Any]]:
        sb = _require_supabase()
        memberships = _rows(
            sb.table("dank_community_session_members")
            .select("session_id,role,ready,joined_at,left_at")
            .eq("guild_id", gid)
            .eq("user_id", uid)
            .is_("left_at", "null")
            .order("joined_at", desc=True)
            .limit(safe_limit)
            .execute()
        )
        session_ids = [_safe_str(row.get("session_id")) for row in memberships if row.get("session_id")]
        if not session_ids:
            return []
        query = (
            sb.table("dank_community_sessions")
            .select("*")
            .eq("guild_id", gid)
            .in_("id", session_ids)
        )
        if active_only:
            query = query.in_("state", list(ACTIVE_SESSION_STATES))
        sessions = _rows(query.order("last_activity_at", desc=True).limit(safe_limit).execute())
        member_by_session = {_safe_str(row.get("session_id")): row for row in memberships}
        for session in sessions:
            session["membership"] = member_by_session.get(_safe_str(session.get("id")), {})
        return sessions

    return await _db_call(_read)


async def list_session_members(session_id: str) -> list[dict[str, Any]]:
    sid = _uuid_text(session_id)

    def _read() -> list[dict[str, Any]]:
        sb = _require_supabase()
        return _rows(
            sb.table("dank_community_session_members")
            .select("*")
            .eq("session_id", sid)
            .is_("left_at", "null")
            .order("joined_at")
            .execute()
        )

    return await _db_call(_read)


async def quick_match(
    guild_id: int | str,
    user_id: int | str,
    game_name: str,
    *,
    idempotency_key: str,
) -> dict[str, Any] | None:
    gid = _safe_str(guild_id)
    uid = _safe_str(user_id)
    game = normalize_game_name(game_name)
    key = _safe_str(idempotency_key)[:240]
    if not key:
        raise CommunityHubError("A Quick Match idempotency key is required.")

    def _write() -> dict[str, Any] | None:
        sb = _require_supabase()
        response = sb.rpc(
            "community_hub_quick_match",
            {
                "p_guild_id": gid,
                "p_user_id": uid,
                "p_game_name": game,
                "p_idempotency_key": key,
            },
        ).execute()
        data = getattr(response, "data", None)
        if data is None:
            return None
        if isinstance(data, dict):
            return dict(data)
        if isinstance(data, list):
            if not data:
                return None
            if data[0] is None:
                return None
            if isinstance(data[0], dict):
                return dict(data[0])
        return None

    return await _db_call(_write)


async def normalize_session_formation(
    session_id: str,
    guild_id: int | str,
) -> dict[str, Any]:
    sid = _uuid_text(session_id)
    gid = _safe_str(guild_id)

    def _write() -> dict[str, Any]:
        sb = _require_supabase()
        return _rpc_payload(
            sb.rpc(
                "community_hub_normalize_formation",
                {"p_session_id": sid, "p_guild_id": gid},
            ).execute()
        )

    return await _db_call(_write)


async def join_session(session_id: str, guild_id: int | str, user_id: int | str) -> dict[str, Any]:
    sid = _uuid_text(session_id)
    gid = _safe_str(guild_id)
    uid = _safe_str(user_id)

    def _write() -> dict[str, Any]:
        sb = _require_supabase()
        return _rpc_payload(
            sb.rpc(
                "community_hub_join_session",
                {"p_session_id": sid, "p_guild_id": gid, "p_user_id": uid},
            ).execute()
        )

    return await _db_call(_write)


async def leave_session(session_id: str, guild_id: int | str, user_id: int | str) -> dict[str, Any]:
    sid = _uuid_text(session_id)
    gid = _safe_str(guild_id)
    uid = _safe_str(user_id)

    def _write() -> dict[str, Any]:
        sb = _require_supabase()
        return _rpc_payload(
            sb.rpc(
                "community_hub_leave_session",
                {"p_session_id": sid, "p_guild_id": gid, "p_user_id": uid},
            ).execute()
        )

    return await _db_call(_write)


async def set_ready(
    session_id: str,
    guild_id: int | str,
    user_id: int | str,
    ready: bool,
) -> dict[str, Any]:
    sid = _uuid_text(session_id)
    gid = _safe_str(guild_id)
    uid = _safe_str(user_id)

    def _write() -> dict[str, Any]:
        sb = _require_supabase()
        return _rpc_payload(
            sb.rpc(
                "community_hub_set_ready",
                {
                    "p_session_id": sid,
                    "p_guild_id": gid,
                    "p_user_id": uid,
                    "p_ready": bool(ready),
                },
            ).execute()
        )

    return await _db_call(_write)


async def transition_session(
    session_id: str,
    guild_id: int | str,
    actor_id: int | str,
    action: str,
    *,
    staff_override: bool = False,
    reason: str = "",
) -> dict[str, Any]:
    sid = _uuid_text(session_id)
    gid = _safe_str(guild_id)
    uid = _safe_str(actor_id)
    clean_action = _safe_str(action).lower()
    if clean_action not in {"publish", "start", "pause", "resume", "lock", "unlock", "extend", "begin_end"}:
        raise CommunityHubError("Unsupported Community Hub session action.")

    def _write() -> dict[str, Any]:
        sb = _require_supabase()
        return _rpc_payload(
            sb.rpc(
                "community_hub_transition_session",
                {
                    "p_session_id": sid,
                    "p_guild_id": gid,
                    "p_actor_id": uid,
                    "p_action": clean_action,
                    "p_staff_override": bool(staff_override),
                    "p_reason": normalize_notes(reason),
                },
            ).execute()
        )

    return await _db_call(_write)



async def assign_member_role(
    session_id: str,
    guild_id: int | str,
    actor_id: int | str,
    target_user_id: int | str,
    role: str,
    *,
    staff_override: bool = False,
) -> dict[str, Any]:
    sid = _uuid_text(session_id)
    gid = _safe_str(guild_id)
    actor = _safe_str(actor_id)
    target = _safe_str(target_user_id)
    clean_role = _safe_str(role).lower()
    if clean_role not in {"host", "cohost", "member"}:
        raise CommunityHubError("Invalid session role.")

    def _write() -> dict[str, Any]:
        sb = _require_supabase()
        return _rpc_payload(
            sb.rpc(
                "community_hub_assign_member_role",
                {
                    "p_session_id": sid,
                    "p_guild_id": gid,
                    "p_actor_id": actor,
                    "p_target_user_id": target,
                    "p_role": clean_role,
                    "p_staff_override": bool(staff_override),
                },
            ).execute()
        )

    return await _db_call(_write)



async def finalize_end_session(
    session_id: str,
    guild_id: int | str,
    actor_id: int | str,
    *,
    cleanup_state: str = "ended",
) -> dict[str, Any]:
    sid = _uuid_text(session_id)
    gid = _safe_str(guild_id)
    uid = _safe_str(actor_id)
    if cleanup_state not in {"ended", "archived", "cleaned", "failed"}:
        cleanup_state = "ended"

    def _write() -> dict[str, Any]:
        sb = _require_supabase()
        return _rpc_payload(
            sb.rpc(
                "community_hub_finalize_end_session",
                {
                    "p_session_id": sid,
                    "p_guild_id": gid,
                    "p_actor_id": uid,
                    "p_cleanup_state": cleanup_state,
                },
            ).execute()
        )

    return await _db_call(_write)


async def restart_session(
    session_id: str,
    guild_id: int | str,
    actor_id: int | str,
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    sid = _uuid_text(session_id)
    gid = _safe_str(guild_id)
    uid = _safe_str(actor_id)
    key = _safe_str(idempotency_key)[:240]

    def _write() -> dict[str, Any]:
        sb = _require_supabase()
        return _rpc_payload(
            sb.rpc(
                "community_hub_restart_session",
                {
                    "p_session_id": sid,
                    "p_guild_id": gid,
                    "p_actor_id": uid,
                    "p_idempotency_key": key,
                },
            ).execute()
        )

    return await _db_call(_write)


async def update_session_refs(
    session_id: str,
    guild_id: int | str,
    **refs: Any,
) -> dict[str, Any]:
    sid = _uuid_text(session_id)
    gid = _safe_str(guild_id)
    allowed = {"panel_channel_id", "panel_message_id", "thread_id", "voice_channel_id"}
    patch = {key: (_safe_str(value) or None) for key, value in refs.items() if key in allowed}
    if not patch:
        return await get_session(sid, guild_id=gid)
    patch["updated_at"] = utc_now_iso()

    def _write() -> dict[str, Any]:
        sb = _require_supabase()
        return _one(
            sb.table("dank_community_sessions")
            .update(patch)
            .eq("id", sid)
            .eq("guild_id", gid)
            .execute()
        ) or {}

    row = await _db_call(_write)
    if not row:
        raise CommunityNotFound("Community Hub session not found.")
    return row


async def plan_resource(
    *,
    guild_id: int | str,
    session_id: str | None,
    resource_type: str,
    parent_discord_id: int | str | None = None,
) -> dict[str, Any]:
    gid = _safe_str(guild_id)
    sid = _uuid_text(session_id) if session_id else None
    if resource_type not in {"panel_message", "thread", "voice_channel", "category"}:
        raise CommunityHubError("Unsupported managed resource type.")
    token = f"ch-{uuid.uuid4().hex}"

    payload = {
        "guild_id": gid,
        "session_id": sid,
        "resource_type": resource_type,
        "ownership_token": token,
        "parent_discord_id": _safe_str(parent_discord_id) or None,
        "state": "planned",
    }

    def _write() -> dict[str, Any]:
        sb = _require_supabase()
        return _one(sb.table("dank_community_managed_resources").insert(payload).execute()) or payload

    return await _db_call(_write)


async def finalize_resource(
    resource_id: str,
    *,
    discord_id: int | str,
    state: str = "active",
    parent_discord_id: int | str | None = None,
) -> dict[str, Any]:
    rid = _uuid_text(resource_id)
    if state not in {"active", "archived", "deleted", "unresolved", "failed", "converted"}:
        state = "active"
    patch: dict[str, Any] = {
        "discord_id": _safe_str(discord_id),
        "state": state,
        "updated_at": utc_now_iso(),
        "last_error": None,
    }
    if parent_discord_id is not None:
        patch["parent_discord_id"] = _safe_str(parent_discord_id)

    def _write() -> dict[str, Any]:
        sb = _require_supabase()
        return _one(
            sb.table("dank_community_managed_resources")
            .update(patch)
            .eq("id", rid)
            .execute()
        ) or {}

    return await _db_call(_write)


async def mark_resource_state(
    resource_id: str,
    state: str,
    *,
    error: str = "",
) -> dict[str, Any]:
    rid = _uuid_text(resource_id)
    if state not in {"planned", "active", "archived", "deleted", "unresolved", "failed", "converted"}:
        raise CommunityHubError("Invalid managed-resource state.")
    patch: dict[str, Any] = {
        "state": state,
        "updated_at": utc_now_iso(),
        "last_error": _safe_str(error)[:500] or None,
    }
    if state == "deleted":
        patch["deleted_at"] = utc_now_iso()

    def _write() -> dict[str, Any]:
        sb = _require_supabase()
        return _one(
            sb.table("dank_community_managed_resources")
            .update(patch)
            .eq("id", rid)
            .execute()
        ) or {}

    return await _db_call(_write)


async def list_session_resources(session_id: str) -> list[dict[str, Any]]:
    sid = _uuid_text(session_id)

    def _read() -> list[dict[str, Any]]:
        sb = _require_supabase()
        return _rows(
            sb.table("dank_community_managed_resources")
            .select("*")
            .eq("session_id", sid)
            .order("created_at")
            .execute()
        )

    return await _db_call(_read)


async def list_recovery_resources(*, limit: int = 200) -> list[dict[str, Any]]:
    safe_limit = max(1, min(1000, _safe_int(limit, 200)))

    def _read() -> list[dict[str, Any]]:
        sb = _require_supabase()
        return _rows(
            sb.table("dank_community_managed_resources")
            .select("*")
            .in_("state", list(RECOVERY_RESOURCE_STATES))
            .order("created_at")
            .limit(safe_limit)
            .execute()
        )

    return await _db_call(_read)


async def record_event(
    guild_id: int | str,
    event_type: str,
    *,
    session_id: str | None = None,
    actor_id: int | str | None = None,
    subject_user_id: int | str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    gid = _safe_str(guild_id)
    payload = {
        "guild_id": gid,
        "session_id": _uuid_text(session_id) if session_id else None,
        "event_type": _safe_str(event_type)[:120],
        "actor_id": _safe_str(actor_id) or None,
        "subject_user_id": _safe_str(subject_user_id) or None,
        "metadata": dict(metadata or {}),
        "created_at": utc_now_iso(),
    }

    def _write() -> None:
        sb = _require_supabase()
        sb.table("dank_community_session_events").insert(payload).execute()

    try:
        await _db_call(_write)
    except CommunityHubError:
        return


def _hour_bucket(moment: datetime | None = None) -> str:
    now = moment or utc_now()
    return now.replace(minute=0, second=0, microsecond=0).isoformat()


async def clear_stale_ready(
    guild_id: int | str,
    *,
    cutoff: datetime,
) -> int:
    gid = _safe_str(guild_id)
    cutoff_iso = cutoff.astimezone(timezone.utc).isoformat()

    def _write() -> int:
        sb = _require_supabase()
        response = sb.rpc(
            "community_hub_clear_stale_ready",
            {"p_guild_id": gid, "p_cutoff": cutoff_iso},
        ).execute()
        data = getattr(response, "data", None)
        if isinstance(data, (int, float, str)):
            return max(0, _safe_int(data, 0))
        if isinstance(data, list) and data:
            return max(0, _safe_int(data[0], 0))
        return 0

    try:
        return await _db_call(_write)
    except CommunityHubError:
        return 0


async def bump_hourly_metric(
    guild_id: int | str,
    metric: str,
    amount: int = 1,
    *,
    peak: bool = False,
) -> None:
    allowed = {
        "sessions_created",
        "sessions_started",
        "sessions_ended",
        "joins",
        "leaves",
        "completed_sessions",
        "abandoned_sessions",
        "cleanup_failures",
        "voice_participants_peak",
        "gaming_presence_peak",
        "online_presence_peak",
    }
    if metric not in allowed:
        return
    gid = _safe_str(guild_id)
    bucket = _hour_bucket()
    value = max(0, _safe_int(amount, 0))

    def _write() -> None:
        sb = _require_supabase()
        sb.rpc(
            "community_hub_bump_metric",
            {
                "p_guild_id": gid,
                "p_bucket_start": bucket,
                "p_metric": metric,
                "p_amount": value,
                "p_peak": bool(peak),
            },
        ).execute()

    try:
        await _db_call(_write)
    except CommunityHubError:
        return


async def bump_game_metric(
    guild_id: int | str,
    game_name: str,
    metric: str,
    amount: int = 1,
    *,
    peak: bool = False,
) -> None:
    if metric not in {"active_players_peak", "sessions_created", "joins"}:
        return
    gid = _safe_str(guild_id)
    game = normalize_game_name(game_name)
    key = game_key(game)
    bucket = _hour_bucket()
    value = max(0, _safe_int(amount, 0))

    def _write() -> None:
        sb = _require_supabase()
        sb.rpc(
            "community_hub_bump_game_metric",
            {
                "p_guild_id": gid,
                "p_bucket_start": bucket,
                "p_game_key": key,
                "p_game_name": game,
                "p_metric": metric,
                "p_amount": value,
                "p_peak": bool(peak),
            },
        ).execute()

    try:
        await _db_call(_write)
    except CommunityHubError:
        return


async def analytics_summary(guild_id: int | str, *, days: int = 7) -> dict[str, Any]:
    gid = _safe_str(guild_id)
    safe_days = max(1, min(365, _safe_int(days, 7)))
    since = (utc_now() - timedelta(days=safe_days)).isoformat()

    def _read() -> dict[str, Any]:
        sb = _require_supabase()
        sessions = _rows(
            sb.table("dank_community_sessions")
            .select("id,game_name,state,created_at,started_at,ended_at")
            .eq("guild_id", gid)
            .gte("created_at", since)
            .order("created_at", desc=True)
            .limit(5000)
            .execute()
        )
        metrics = _rows(
            sb.table("dank_community_metrics_hourly")
            .select("*")
            .eq("guild_id", gid)
            .gte("bucket_start", since)
            .order("bucket_start")
            .limit(10000)
            .execute()
        )
        games = _rows(
            sb.table("dank_community_game_metrics_hourly")
            .select("*")
            .eq("guild_id", gid)
            .gte("bucket_start", since)
            .order("bucket_start")
            .limit(10000)
            .execute()
        )
        events = _rows(
            sb.table("dank_community_session_events")
            .select("event_type,created_at")
            .eq("guild_id", gid)
            .gte("created_at", since)
            .order("created_at", desc=True)
            .limit(10000)
            .execute()
        )
        return {"sessions": sessions, "metrics": metrics, "games": games, "events": events}

    raw = await _db_call(_read)
    sessions = raw["sessions"]
    metrics = raw["metrics"]
    games = raw["games"]
    events = raw["events"]

    created = len(sessions)
    completed = sum(1 for row in sessions if row.get("state") in ENDED_SESSION_STATES)
    abandoned = sum(1 for row in sessions if row.get("state") == "abandoned")
    active = sum(1 for row in sessions if row.get("state") in ACTIVE_SESSION_STATES)
    durations: list[float] = []
    for row in sessions:
        try:
            start = datetime.fromisoformat(_safe_str(row.get("started_at")).replace("Z", "+00:00"))
            end = datetime.fromisoformat(_safe_str(row.get("ended_at")).replace("Z", "+00:00"))
            durations.append(max(0.0, (end - start).total_seconds()))
        except Exception:
            continue

    joins = sum(_safe_int(row.get("joins"), 0) for row in metrics)
    leaves = sum(_safe_int(row.get("leaves"), 0) for row in metrics)
    cleanup_failures = sum(_safe_int(row.get("cleanup_failures"), 0) for row in metrics)
    voice_peak = max((_safe_int(row.get("voice_participants_peak"), 0) for row in metrics), default=0)
    online_peak = max((_safe_int(row.get("online_presence_peak"), 0) for row in metrics), default=0)
    gaming_peak = max((_safe_int(row.get("gaming_presence_peak"), 0) for row in metrics), default=0)

    game_totals: dict[str, dict[str, Any]] = {}
    for row in games:
        key = _safe_str(row.get("game_key"), "game")
        bucket = game_totals.setdefault(
            key,
            {"name": _safe_str(row.get("game_name"), "Game"), "sessions": 0, "joins": 0, "peak": 0},
        )
        bucket["sessions"] += _safe_int(row.get("sessions_created"), 0)
        bucket["joins"] += _safe_int(row.get("joins"), 0)
        bucket["peak"] = max(bucket["peak"], _safe_int(row.get("active_players_peak"), 0))

    top_games = sorted(
        game_totals.values(),
        key=lambda row: (row["joins"], row["sessions"], row["peak"]),
        reverse=True,
    )[:10]

    event_counts: dict[str, int] = {}
    for row in events:
        name = _safe_str(row.get("event_type"), "unknown")
        event_counts[name] = event_counts.get(name, 0) + 1

    return {
        "days": safe_days,
        "sessions_created": created,
        "sessions_completed": completed,
        "sessions_abandoned": abandoned,
        "sessions_active": active,
        "completion_rate": (completed / created) if created else 0.0,
        "average_duration_seconds": (sum(durations) / len(durations)) if durations else 0.0,
        "joins": joins or event_counts.get("session.joined", 0),
        "leaves": leaves or event_counts.get("session.left", 0),
        "cleanup_failures": cleanup_failures,
        "voice_participants_peak": voice_peak,
        "online_presence_peak": online_peak,
        "gaming_presence_peak": gaming_peak,
        "top_games": top_games,
        "event_counts": event_counts,
    }


async def set_notification_preference(
    guild_id: int | str,
    user_id: int | str,
    game_name: str,
    *,
    enabled: bool = True,
    notify_open_groups: bool = True,
    notify_events: bool = True,
) -> dict[str, Any]:
    gid = _safe_str(guild_id)
    uid = _safe_str(user_id)
    game = normalize_game_name(game_name)
    payload = {
        "guild_id": gid,
        "user_id": uid,
        "game_key": game_key(game),
        "game_name": game,
        "enabled": bool(enabled),
        "notify_open_groups": bool(notify_open_groups),
        "notify_events": bool(notify_events),
        "updated_at": utc_now_iso(),
    }

    def _write() -> dict[str, Any]:
        sb = _require_supabase()
        return _one(
            sb.table("dank_community_notification_prefs")
            .upsert(payload, on_conflict="guild_id,user_id,game_key")
            .execute()
        ) or payload

    return await _db_call(_write)


async def list_notification_targets(
    guild_id: int | str,
    game_name: str,
    *,
    for_event: bool = False,
    limit: int = 1000,
) -> list[str]:
    gid = _safe_str(guild_id)
    key = game_key(game_name)
    safe_limit = max(1, min(5000, _safe_int(limit, 1000)))

    def _read() -> list[str]:
        sb = _require_supabase()
        query = (
            sb.table("dank_community_notification_prefs")
            .select("user_id")
            .eq("guild_id", gid)
            .eq("game_key", key)
            .eq("enabled", True)
            .eq("notify_events" if for_event else "notify_open_groups", True)
            .limit(safe_limit)
        )
        return [_safe_str(row.get("user_id")) for row in _rows(query.execute()) if row.get("user_id")]

    return await _db_call(_read)


async def reserve_notification(
    guild_id: int | str,
    user_id: int | str,
    *,
    max_per_hour: int,
    cooldown_seconds: int,
) -> bool:
    gid = _safe_str(guild_id)
    uid = _safe_str(user_id)
    now = utc_now_iso()

    def _write() -> bool:
        sb = _require_supabase()
        response = sb.rpc(
            "community_hub_reserve_notification",
            {
                "p_guild_id": gid,
                "p_user_id": uid,
                "p_now": now,
                "p_max_per_hour": max(1, min(50, _safe_int(max_per_hour, 5))),
                "p_cooldown_seconds": max(60, min(3600, _safe_int(cooldown_seconds, 300))),
            },
        ).execute()
        data = getattr(response, "data", None)
        if isinstance(data, bool):
            return data
        if isinstance(data, list) and data:
            return bool(data[0])
        return str(data).strip().lower() in {"true", "1"}

    return bool(await _db_call(_write))


async def mark_notification_failure(
    guild_id: int | str,
    user_id: int | str,
    *,
    blocked_until: datetime,
) -> None:
    gid = _safe_str(guild_id)
    uid = _safe_str(user_id)
    until = blocked_until.astimezone(timezone.utc).isoformat()

    def _write() -> None:
        sb = _require_supabase()
        sb.rpc(
            "community_hub_mark_notification_failure",
            {
                "p_guild_id": gid,
                "p_user_id": uid,
                "p_blocked_until": until,
            },
        ).execute()

    try:
        await _db_call(_write)
    except CommunityHubError:
        return


async def set_availability(
    guild_id: int | str,
    user_id: int | str,
    game_name: str,
    *,
    duration_hours: int = 2,
    play_style: str = "any",
    mic_preference: str = "optional",
    note: str = "",
) -> dict[str, Any]:
    gid = _safe_str(guild_id)
    uid = _safe_str(user_id)
    game = normalize_game_name(game_name)
    hours = max(1, min(8, _safe_int(duration_hours, 2)))
    if play_style not in {"casual", "competitive", "beginner", "any"}:
        play_style = "any"
    if mic_preference not in {"optional", "preferred", "required", "no_mic"}:
        mic_preference = "optional"
    payload = {
        "guild_id": gid,
        "user_id": uid,
        "game_key": game_key(game),
        "game_name": game,
        "play_style": play_style,
        "mic_preference": mic_preference,
        "note": normalize_notes(note),
        "expires_at": (utc_now() + timedelta(hours=hours)).isoformat(),
        "updated_at": utc_now_iso(),
    }

    def _write() -> dict[str, Any]:
        sb = _require_supabase()
        return _one(
            sb.table("dank_community_availability")
            .upsert(payload, on_conflict="guild_id,user_id,game_key")
            .execute()
        ) or payload

    row = await _db_call(_write)
    await record_event(
        gid,
        "availability.updated",
        actor_id=uid,
        subject_user_id=uid,
        metadata={"game_key": payload["game_key"], "expires_at": payload["expires_at"]},
    )
    return row


async def list_user_availability(
    guild_id: int | str,
    user_id: int | str,
    *,
    limit: int = 25,
) -> list[dict[str, Any]]:
    gid = _safe_str(guild_id)
    uid = _safe_str(user_id)
    safe_limit = max(1, min(100, _safe_int(limit, 25)))
    now = utc_now_iso()

    def _read() -> list[dict[str, Any]]:
        sb = _require_supabase()
        return _rows(
            sb.table("dank_community_availability")
            .select("*")
            .eq("guild_id", gid)
            .eq("user_id", uid)
            .gt("expires_at", now)
            .order("expires_at")
            .limit(safe_limit)
            .execute()
        )

    return await _db_call(_read)


async def list_available_users(
    guild_id: int | str,
    game_name: str,
    *,
    limit: int = 250,
) -> list[dict[str, Any]]:
    gid = _safe_str(guild_id)
    key = game_key(game_name)
    safe_limit = max(1, min(250, _safe_int(limit, 50)))
    now = utc_now_iso()

    def _read() -> list[dict[str, Any]]:
        sb = _require_supabase()
        return _rows(
            sb.table("dank_community_availability")
            .select("*")
            .eq("guild_id", gid)
            .eq("game_key", key)
            .gt("expires_at", now)
            .order("updated_at", desc=True)
            .limit(safe_limit)
            .execute()
        )

    return await _db_call(_read)


async def availability_summary(
    guild_id: int | str,
    *,
    limit: int = 1000,
) -> dict[str, Any]:
    gid = _safe_str(guild_id)
    safe_limit = max(1, min(5000, _safe_int(limit, 1000)))
    now = utc_now_iso()

    def _read() -> list[dict[str, Any]]:
        sb = _require_supabase()
        return _rows(
            sb.table("dank_community_availability")
            .select("*")
            .eq("guild_id", gid)
            .gt("expires_at", now)
            .limit(safe_limit)
            .execute()
        )

    rows = await _db_call(_read)
    users = {_safe_str(row.get("user_id")) for row in rows if row.get("user_id")}
    auto_match_users = {
        _safe_str(row.get("user_id"))
        for row in rows
        if row.get("user_id") and bool(row.get("auto_match"))
    }
    games: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = _safe_str(row.get("game_key"), "game")
        bucket = games.setdefault(
            key,
            {
                "name": _safe_str(row.get("game_name"), "Game"),
                "users": set(),
                "auto_match_users": set(),
            },
        )
        uid = _safe_str(row.get("user_id"))
        if uid:
            bucket["users"].add(uid)
            if bool(row.get("auto_match")):
                bucket["auto_match_users"].add(uid)
    top = sorted(
        (
            {
                "name": value["name"],
                "count": len(value["users"]),
                "auto_match_count": len(value["auto_match_users"]),
            }
            for value in games.values()
        ),
        key=lambda row: (row["count"], row["auto_match_count"]),
        reverse=True,
    )[:10]
    return {
        "unique_users": len(users),
        "auto_match_users": len(auto_match_users),
        "top_games": top,
    }


async def set_availability_auto_match(
    guild_id: int | str,
    user_id: int | str,
    enabled: bool,
    *,
    game_name: str | None = None,
) -> list[dict[str, Any]]:
    gid = _safe_str(guild_id)
    uid = _safe_str(user_id)
    key = game_key(game_name) if _safe_str(game_name) else ""
    now = utc_now_iso()

    def _write() -> list[dict[str, Any]]:
        sb = _require_supabase()
        query = (
            sb.table("dank_community_availability")
            .update({"auto_match": bool(enabled), "updated_at": utc_now_iso()})
            .eq("guild_id", gid)
            .eq("user_id", uid)
            .gt("expires_at", now)
        )
        if key:
            query = query.eq("game_key", key)
        return _rows(query.execute())

    rows = await _db_call(_write)
    await record_event(
        gid,
        "availability.auto_match",
        actor_id=uid,
        subject_user_id=uid,
        metadata={
            "enabled": bool(enabled),
            "game_key": key or None,
            "rows": len(rows),
        },
    )
    return rows


async def clear_availability(
    guild_id: int | str,
    user_id: int | str,
    *,
    game_name: str | None = None,
) -> int:
    gid = _safe_str(guild_id)
    uid = _safe_str(user_id)
    key = game_key(game_name) if _safe_str(game_name) else ""

    def _delete() -> int:
        sb = _require_supabase()
        query = (
            sb.table("dank_community_availability")
            .delete()
            .eq("guild_id", gid)
            .eq("user_id", uid)
        )
        if key:
            query = query.eq("game_key", key)
        response = query.execute()
        return len(_rows(response))

    deleted = await _db_call(_delete)
    await record_event(
        gid,
        "availability.cleared",
        actor_id=uid,
        subject_user_id=uid,
        metadata={"game_key": key or None, "rows": deleted},
    )
    return deleted


async def delete_expired_availability(guild_id: int | str) -> int:
    gid = _safe_str(guild_id)
    cutoff = utc_now_iso()

    def _delete() -> int:
        sb = _require_supabase()
        response = (
            sb.table("dank_community_availability")
            .delete()
            .eq("guild_id", gid)
            .lte("expires_at", cutoff)
            .execute()
        )
        return len(_rows(response))

    try:
        return await _db_call(_delete)
    except CommunityHubError:
        return 0


async def set_match_block(
    guild_id: int | str,
    blocker_user_id: int | str,
    blocked_user_id: int | str,
    blocked: bool,
) -> bool:
    gid = _safe_str(guild_id)
    blocker = _safe_str(blocker_user_id)
    target = _safe_str(blocked_user_id)

    def _write() -> bool:
        sb = _require_supabase()
        response = sb.rpc(
            "community_hub_set_match_block",
            {
                "p_guild_id": gid,
                "p_blocker_user_id": blocker,
                "p_blocked_user_id": target,
                "p_blocked": bool(blocked),
            },
        ).execute()
        data = getattr(response, "data", None)
        if isinstance(data, bool):
            return data
        if isinstance(data, list) and data:
            return bool(data[0])
        return str(data).strip().lower() in {"true", "1"}

    result = bool(await _db_call(_write))
    await record_event(
        gid,
        "match_safety.blocked" if result else "match_safety.unblocked",
        actor_id=blocker,
        subject_user_id=target,
    )
    return result


async def list_match_blocks(
    guild_id: int | str,
    user_id: int | str,
    *,
    limit: int = 250,
) -> list[str]:
    gid = _safe_str(guild_id)
    uid = _safe_str(user_id)
    safe_limit = max(1, min(1000, _safe_int(limit, 250)))

    def _read() -> list[str]:
        sb = _require_supabase()
        rows = _rows(
            sb.table("dank_community_user_blocks")
            .select("blocked_user_id")
            .eq("guild_id", gid)
            .eq("blocker_user_id", uid)
            .order("created_at", desc=True)
            .limit(safe_limit)
            .execute()
        )
        return [
            _safe_str(row.get("blocked_user_id"))
            for row in rows
            if row.get("blocked_user_id")
        ]

    return await _db_call(_read)


async def submit_report(
    guild_id: int | str,
    reporter_user_id: int | str,
    *,
    session_id: str | None,
    reason: str,
    details: str = "",
) -> dict[str, Any]:
    gid = _safe_str(guild_id)
    reporter = _safe_str(reporter_user_id)
    sid = _uuid_text(session_id) if session_id else None
    clean_reason = normalize_notes(reason)[:100]
    clean_details = _SAFE_NOTE_RE.sub("", _safe_str(details))[:1000]
    if not clean_reason:
        raise CommunityHubError("Report reason is required.")

    def _write() -> dict[str, Any]:
        sb = _require_supabase()
        return _rpc_payload(
            sb.rpc(
                "community_hub_submit_report",
                {
                    "p_guild_id": gid,
                    "p_session_id": sid,
                    "p_reporter_user_id": reporter,
                    "p_reason": clean_reason,
                    "p_details": clean_details,
                },
            ).execute()
        )

    row = await _db_call(_write)
    await record_event(
        gid,
        "safety.report_submitted",
        session_id=sid,
        actor_id=reporter,
        subject_user_id=reporter,
        metadata={"report_id": row.get("id")},
    )
    return row


async def list_reports(
    guild_id: int | str,
    *,
    states: Iterable[str] = ("open", "reviewing"),
    limit: int = 50,
) -> list[dict[str, Any]]:
    gid = _safe_str(guild_id)
    allowed = {"open", "reviewing", "resolved", "dismissed"}
    clean_states = [state for state in states if state in allowed]
    if not clean_states:
        clean_states = ["open", "reviewing"]
    safe_limit = max(1, min(250, _safe_int(limit, 50)))

    def _read() -> list[dict[str, Any]]:
        sb = _require_supabase()
        return _rows(
            sb.table("dank_community_reports")
            .select("*")
            .eq("guild_id", gid)
            .in_("state", clean_states)
            .order("created_at", desc=True)
            .limit(safe_limit)
            .execute()
        )

    return await _db_call(_read)


async def update_report_state(
    report_id: str,
    guild_id: int | str,
    actor_id: int | str,
    state: str,
) -> dict[str, Any]:
    rid = _uuid_text(report_id)
    gid = _safe_str(guild_id)
    actor = _safe_str(actor_id)
    if state not in {"open", "reviewing", "resolved", "dismissed"}:
        raise CommunityHubError("Invalid Community Hub report state.")
    patch: dict[str, Any] = {
        "state": state,
        "updated_at": utc_now_iso(),
    }
    if state in {"resolved", "dismissed"}:
        patch["resolved_by"] = actor
        patch["resolved_at"] = utc_now_iso()
    else:
        patch["resolved_by"] = None
        patch["resolved_at"] = None

    def _write() -> dict[str, Any]:
        sb = _require_supabase()
        return _one(
            sb.table("dank_community_reports")
            .update(patch)
            .eq("id", rid)
            .eq("guild_id", gid)
            .execute()
        ) or {}

    row = await _db_call(_write)
    if not row:
        raise CommunityNotFound("Community Hub safety report not found.")
    await record_event(
        gid,
        f"safety.report_{state}",
        actor_id=actor,
        metadata={"report_id": rid},
    )
    return row


async def create_event(
    *,
    guild_id: int | str,
    title: str,
    description: str,
    game_name: str,
    starts_at: datetime,
    ends_at: datetime | None,
    capacity: int | None,
    created_by: int | str,
) -> dict[str, Any]:
    gid = _safe_str(guild_id)
    clean_title = normalize_notes(title)[:100]
    if not clean_title:
        raise CommunityHubError("Event title is required.")
    payload = {
        "guild_id": gid,
        "title": clean_title,
        "description": normalize_notes(description),
        "game_name": normalize_game_name(game_name) if _safe_str(game_name) else "",
        "starts_at": starts_at.astimezone(timezone.utc).isoformat(),
        "ends_at": ends_at.astimezone(timezone.utc).isoformat() if ends_at else None,
        "capacity": max(2, min(10000, _safe_int(capacity, 0))) if capacity else None,
        "state": "scheduled",
        "created_by": _safe_str(created_by),
    }

    def _write() -> dict[str, Any]:
        sb = _require_supabase()
        return _one(sb.table("dank_community_events").insert(payload).execute()) or payload

    row = await _db_call(_write)
    await record_event(gid, "event.created", actor_id=created_by, metadata={"event_id": row.get("id")})
    return row


async def list_upcoming_events(guild_id: int | str, *, limit: int = 25) -> list[dict[str, Any]]:
    gid = _safe_str(guild_id)
    safe_limit = max(1, min(100, _safe_int(limit, 25)))

    def _read() -> list[dict[str, Any]]:
        sb = _require_supabase()
        return _rows(
            sb.table("dank_community_events")
            .select("*")
            .eq("guild_id", gid)
            .in_("state", ["scheduled", "open", "active"])
            .gte("starts_at", (utc_now() - timedelta(hours=12)).isoformat())
            .order("starts_at")
            .limit(safe_limit)
            .execute()
        )

    return await _db_call(_read)


async def rsvp_event(
    event_id: str,
    guild_id: int | str,
    user_id: int | str,
    response: str,
) -> dict[str, Any]:
    eid = _uuid_text(event_id)
    gid = _safe_str(guild_id)
    uid = _safe_str(user_id)
    if response not in {"interested", "going", "declined"}:
        raise CommunityHubError("Invalid event response.")

    def _write() -> dict[str, Any]:
        sb = _require_supabase()
        return _rpc_payload(
            sb.rpc(
                "community_hub_rsvp_event",
                {
                    "p_event_id": eid,
                    "p_guild_id": gid,
                    "p_user_id": uid,
                    "p_response": response,
                },
            ).execute()
        )

    return await _db_call(_write)


async def list_user_notification_preferences(
    guild_id: int | str,
    user_id: int | str,
    *,
    limit: int = 100,
) -> list[dict[str, Any]]:
    gid = _safe_str(guild_id)
    uid = _safe_str(user_id)
    safe_limit = max(1, min(500, _safe_int(limit, 100)))

    def _read() -> list[dict[str, Any]]:
        sb = _require_supabase()
        return _rows(
            sb.table("dank_community_notification_prefs")
            .select("*")
            .eq("guild_id", gid)
            .eq("user_id", uid)
            .order("updated_at", desc=True)
            .limit(safe_limit)
            .execute()
        )

    return await _db_call(_read)


async def list_event_attendees(event_id: str) -> list[dict[str, Any]]:
    eid = _uuid_text(event_id)

    def _read() -> list[dict[str, Any]]:
        sb = _require_supabase()
        return _rows(
            sb.table("dank_community_event_attendees")
            .select("*")
            .eq("event_id", eid)
            .order("updated_at")
            .limit(10000)
            .execute()
        )

    return await _db_call(_read)



async def create_hublink_code(
    guild_id: int | str,
    actor_id: int | str,
) -> dict[str, Any]:
    gid = _safe_str(guild_id)
    actor = _safe_str(actor_id)
    if not gid or not actor:
        raise CommunityHubError("HubLink needs a server and an authorized creator.")

    compact = "".join(secrets.choice(_HUBLINK_ALPHABET) for _ in range(_HUBLINK_LENGTH))
    display_code = format_hublink_code(compact)
    digest = _hublink_digest(compact)

    def _write() -> dict[str, Any]:
        sb = _require_supabase()
        return _rpc_payload(
            sb.rpc(
                "community_hub_create_link_code",
                {
                    "p_source_guild_id": gid,
                    "p_actor_id": actor,
                    "p_code_hash": digest,
                    "p_code_hint": compact[-4:],
                },
            ).execute()
        )

    row = await _db_call(_write)
    row["code"] = display_code
    return row


async def inspect_hublink_code(
    code: str,
    *,
    target_guild_id: int | str,
) -> dict[str, Any]:
    compact = normalize_hublink_code(code)
    digest = _hublink_digest(compact)
    target = _safe_str(target_guild_id)

    def _read() -> dict[str, Any] | None:
        sb = _require_supabase()
        return _one(
            sb.table("dank_community_hub_link_codes")
            .select(
                "id,source_guild_id,created_by_user_id,code_hint,state,expires_at,"
                "redeemed_at,redeemed_by_guild_id,redeemed_by_user_id,created_at"
            )
            .eq("code_hash", digest)
            .limit(1)
            .execute()
        )

    row = await _db_call(_read)
    if not row:
        raise CommunityConflict("HubLink code is invalid or no longer available.")

    source = _safe_str(row.get("source_guild_id"))
    state = _safe_str(row.get("state"), "pending")
    if source == target:
        raise CommunityConflict("A server cannot HubLink to itself.")
    if state == "redeemed":
        if _safe_str(row.get("redeemed_by_guild_id")) != target:
            raise CommunityConflict("HubLink code was already redeemed by another server.")
        return row
    if state != "pending":
        raise CommunityConflict("HubLink code is no longer available.")

    try:
        expires = datetime.fromisoformat(_safe_str(row.get("expires_at")).replace("Z", "+00:00"))
    except Exception as exc:
        raise CommunityHubError("HubLink expiry data is invalid.") from exc
    if expires <= utc_now():
        raise CommunityConflict("HubLink code has expired.")
    return row


async def redeem_hublink_code(
    code: str,
    *,
    target_guild_id: int | str,
    actor_id: int | str,
) -> dict[str, Any]:
    compact = normalize_hublink_code(code)
    digest = _hublink_digest(compact)
    target = _safe_str(target_guild_id)
    actor = _safe_str(actor_id)

    def _write() -> dict[str, Any]:
        sb = _require_supabase()
        return _rpc_payload(
            sb.rpc(
                "community_hub_redeem_link_code",
                {
                    "p_code_hash": digest,
                    "p_target_guild_id": target,
                    "p_actor_id": actor,
                },
            ).execute()
        )

    return await _db_call(_write)


async def expire_hublink_codes(*, limit: int = 500) -> int:
    safe_limit = max(1, min(5000, _safe_int(limit, 500)))

    def _write() -> int:
        sb = _require_supabase()
        response = sb.rpc(
            "community_hub_expire_link_codes",
            {"p_limit": safe_limit},
        ).execute()
        data = getattr(response, "data", None)
        if isinstance(data, list) and data:
            return max(0, _safe_int(data[0], 0))
        return max(0, _safe_int(data, 0))

    try:
        return await _db_call(_write)
    except CommunityHubError:
        return 0


async def create_partner_request(
    guild_id: int | str,
    target_guild_id: int | str,
    actor_id: int | str,
) -> dict[str, Any]:
    source = _safe_str(guild_id)
    target = _safe_str(target_guild_id)
    actor = _safe_str(actor_id)
    if not source or not target or source == target:
        raise CommunityHubError("Choose a different server.")

    def _write() -> dict[str, Any]:
        sb = _require_supabase()
        return _rpc_payload(
            sb.rpc(
                "community_hub_request_partner",
                {
                    "p_source_guild_id": source,
                    "p_target_guild_id": target,
                    "p_actor_id": actor,
                },
            ).execute()
        )

    return await _db_call(_write)


async def update_partner_link(
    link_id: str,
    *,
    state: str,
    aggregate_activity_shared: bool | None = None,
    session_discovery_shared: bool | None = None,
) -> dict[str, Any]:
    lid = _uuid_text(link_id)
    if state not in {"pending", "active", "revoked", "rejected"}:
        raise CommunityHubError("Invalid partner-link state.")
    patch: dict[str, Any] = {"state": state, "updated_at": utc_now_iso()}
    if aggregate_activity_shared is not None:
        patch["aggregate_activity_shared"] = bool(aggregate_activity_shared)
    if session_discovery_shared is not None:
        patch["session_discovery_shared"] = bool(session_discovery_shared)

    def _write() -> dict[str, Any]:
        sb = _require_supabase()
        return _one(
            sb.table("dank_community_partner_links")
            .update(patch)
            .eq("id", lid)
            .execute()
        ) or {}

    return await _db_call(_write)


async def list_partner_links(guild_id: int | str, *, active_only: bool = True) -> list[dict[str, Any]]:
    gid = _safe_str(guild_id)

    def _read_side(column: str) -> list[dict[str, Any]]:
        sb = _require_supabase()
        query = sb.table("dank_community_partner_links").select("*").eq(column, gid)
        if active_only:
            query = query.eq("state", "active")
        return _rows(query.order("updated_at", desc=True).limit(100).execute())

    left = await _db_call(lambda: _read_side("guild_a_id"))
    right = await _db_call(lambda: _read_side("guild_b_id"))
    by_id = {_safe_str(row.get("id")): row for row in [*left, *right] if row.get("id")}
    return list(by_id.values())



async def list_partner_discovery_sessions(
    guild_id: int | str,
    *,
    limit: int = 25,
) -> list[dict[str, Any]]:
    gid = _safe_str(guild_id)
    safe_limit = max(1, min(100, _safe_int(limit, 25)))
    links = await list_partner_links(gid, active_only=True)
    partner_ids: list[str] = []
    for link in links:
        if not bool(link.get("session_discovery_shared")):
            continue
        a = _safe_str(link.get("guild_a_id"))
        b = _safe_str(link.get("guild_b_id"))
        other = b if a == gid else a
        if other and other != gid:
            partner_ids.append(other)
    if not partner_ids:
        return []

    def _read() -> list[dict[str, Any]]:
        sb = _require_supabase()
        enabled_settings = _rows(
            sb.table("dank_community_hub_settings")
            .select("guild_id,partner_discovery_enabled")
            .in_("guild_id", partner_ids)
            .eq("partner_discovery_enabled", True)
            .execute()
        )
        allowed = [_safe_str(row.get("guild_id")) for row in enabled_settings if row.get("guild_id")]
        if not allowed:
            return []
        return _rows(
            sb.table("dank_community_sessions")
            .select("id,guild_id,game_name,notes,capacity,mic_preference,play_style,privacy,state,panel_channel_id,panel_message_id,created_at,last_activity_at")
            .in_("guild_id", allowed)
            .eq("privacy", "public")
            .in_("state", ["open", "forming", "ready", "active"])
            .order("last_activity_at", desc=True)
            .limit(safe_limit)
            .execute()
        )

    return await _db_call(_read)



async def prune_session_personal_data(
    guild_id: int | str,
    *,
    retention_days: int,
) -> int:
    gid = _safe_str(guild_id)
    days = max(1, min(365, _safe_int(retention_days, 30)))
    cutoff = (utc_now() - timedelta(days=days)).isoformat()

    def _write() -> int:
        sb = _require_supabase()
        response = sb.rpc(
            "community_hub_prune_session_personal_data",
            {"p_guild_id": gid, "p_cutoff": cutoff},
        ).execute()
        data = getattr(response, "data", None)
        if isinstance(data, (int, float, str)):
            return max(0, _safe_int(data, 0))
        if isinstance(data, list) and data:
            return max(0, _safe_int(data[0], 0))
        return 0

    try:
        return await _db_call(_write)
    except CommunityHubError:
        return 0


async def delete_expired_detailed_events(guild_id: int | str, *, retention_days: int) -> int:
    gid = _safe_str(guild_id)
    days = max(1, min(365, _safe_int(retention_days, 30)))
    cutoff = (utc_now() - timedelta(days=days)).isoformat()

    def _delete() -> int:
        sb = _require_supabase()
        response = (
            sb.table("dank_community_session_events")
            .delete()
            .eq("guild_id", gid)
            .lt("created_at", cutoff)
            .execute()
        )
        return len(_rows(response))

    try:
        return await _db_call(_delete)
    except CommunityHubError:
        return 0



async def count_active_resources(
    guild_id: int | str,
    resource_type: str,
) -> int:
    gid = _safe_str(guild_id)
    if resource_type not in {"panel_message", "thread", "voice_channel", "category"}:
        return 0

    def _read() -> int:
        sb = _require_supabase()
        response = (
            sb.table("dank_community_managed_resources")
            .select("id", count="exact")
            .eq("guild_id", gid)
            .eq("resource_type", resource_type)
            .in_("state", ["planned", "active", "unresolved"])
            .execute()
        )
        count = getattr(response, "count", None)
        if count is not None:
            return max(0, _safe_int(count, 0))
        return len(_rows(response))

    return await _db_call(_read)


async def touch_session(
    session_id: str,
    guild_id: int | str,
) -> None:
    sid = _uuid_text(session_id)
    gid = _safe_str(guild_id)

    def _write() -> None:
        sb = _require_supabase()
        sb.table("dank_community_sessions").update(
            {"last_activity_at": utc_now_iso(), "updated_at": utc_now_iso()}
        ).eq("id", sid).eq("guild_id", gid).in_("state", list(ACTIVE_SESSION_STATES)).execute()

    try:
        await _db_call(_write)
    except CommunityHubError:
        return


async def list_active_resources_for_guild(
    guild_id: int | str,
    *,
    resource_type: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    gid = _safe_str(guild_id)
    safe_limit = max(1, min(1000, _safe_int(limit, 200)))

    def _read() -> list[dict[str, Any]]:
        sb = _require_supabase()
        query = (
            sb.table("dank_community_managed_resources")
            .select("*")
            .eq("guild_id", gid)
            .in_("state", ["planned", "active", "unresolved", "failed"])
        )
        if resource_type in {"panel_message", "thread", "voice_channel", "category"}:
            query = query.eq("resource_type", resource_type)
        return _rows(query.order("created_at").limit(safe_limit).execute())

    return await _db_call(_read)



def summarize_member_counts(members: Iterable[dict[str, Any]]) -> dict[str, int]:
    counts = {"host": 0, "cohost": 0, "member": 0, "waitlist": 0, "ready": 0, "active": 0}
    for row in members:
        role = _safe_str(row.get("role"), "member")
        if role in counts:
            counts[role] += 1
        if role != "waitlist":
            counts["active"] += 1
            if bool(row.get("ready")):
                counts["ready"] += 1
    return counts


__all__ = [
    "ACTIVE_SESSION_STATES",
    "JOINABLE_SESSION_STATES",
    "ENDED_SESSION_STATES",
    "SESSION_STATES",
    "CommunityHubError",
    "CommunityStorageUnavailable",
    "CommunityNotFound",
    "CommunityConflict",
    "normalize_game_name",
    "normalize_notes",
    "normalize_hublink_code",
    "format_hublink_code",
    "game_key",
    "get_settings",
    "update_settings",
    "create_session",
    "get_session",
    "get_session_by_panel_message",
    "list_active_sessions",
    "list_user_sessions",
    "list_session_members",
    "quick_match",
    "join_session",
    "leave_session",
    "set_ready",
    "transition_session",
    "assign_member_role",
    "finalize_end_session",
    "restart_session",
    "update_session_refs",
    "plan_resource",
    "finalize_resource",
    "mark_resource_state",
    "list_session_resources",
    "list_recovery_resources",
    "count_active_resources",
    "touch_session",
    "list_active_resources_for_guild",
    "record_event",
    "clear_stale_ready",
    "bump_hourly_metric",
    "bump_game_metric",
    "analytics_summary",
    "set_notification_preference",
    "list_notification_targets",
    "list_user_notification_preferences",
    "reserve_notification",
    "mark_notification_failure",
    "set_availability",
    "list_user_availability",
    "list_available_users",
    "availability_summary",
    "clear_availability",
    "delete_expired_availability",
    "set_match_block",
    "list_match_blocks",
    "submit_report",
    "list_reports",
    "update_report_state",
    "list_event_attendees",
    "create_event",
    "list_upcoming_events",
    "rsvp_event",
    "create_hublink_code",
    "inspect_hublink_code",
    "redeem_hublink_code",
    "expire_hublink_codes",
    "create_partner_request",
    "update_partner_link",
    "list_partner_links",
    "list_partner_discovery_sessions",
    "prune_session_personal_data",
    "delete_expired_detailed_events",
    "summarize_member_counts",
]
