from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List, Callable, Tuple

import discord

from ..globals import bot, get_supabase, reset_supabase
from ..members_new.service import (
    sync_all_members,
    reconcile_departed_members,
    sync_role_members,
)

# NEW ticket system
from ..tickets_new.service import create_ticket_channel
from ..tickets_new.transcript_service import delete_ticket_with_optional_transcript
from ..tickets_new.sync_service import (
    sync_active_ticket_channels_for_guild,
    sync_one_ticket_channel,
)

# Verify / role UI helpers
from ..transcripts import (
    ensure_verify_ui_present,
    post_or_replace_verification_staff_panel,
)

# NEW verification service parity layer
from ..verification_new.service import (
    approve_verification,
    deny_verification,
)

POLL_INTERVAL = 3
COMMAND_STALE_PROCESSING_SECONDS = 300

_WORKER_TASK: Optional[asyncio.Task] = None


def now_iso() -> str:
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


def _safe_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = _safe_str(value).lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _get_global_int(name: str, default: int = 0) -> int:
    try:
        from .. import globals as g
        return int(getattr(g, name, default) or default)
    except Exception:
        return default


def _get_verified_role_id() -> int:
    return _get_global_int("VERIFIED_ROLE_ID", 0)


def _get_unverified_role_id() -> int:
    return _get_global_int("UNVERIFIED_ROLE_ID", 0)


def _get_resident_role_id() -> int:
    return _get_global_int("RESIDENT_ROLE_ID", 0)


def _get_stoner_role_id() -> int:
    return _get_global_int("STONER_ROLE_ID", 0)


def _get_drunken_role_id() -> int:
    return _get_global_int("DRUNKEN_ROLE_ID", 0)


def _member_has_any_role(member: Optional[discord.Member], role_ids: List[int]) -> bool:
    try:
        if not isinstance(member, discord.Member):
            return False
        wanted = {int(r) for r in role_ids if int(r) > 0}
        if not wanted:
            return False
        return any(int(getattr(role, "id", 0) or 0) in wanted for role in (member.roles or []))
    except Exception:
        return False


def _member_already_verified(member: Optional[discord.Member]) -> bool:
    try:
        if not isinstance(member, discord.Member):
            return False

        verified_ids = [
            _get_verified_role_id(),
            _get_resident_role_id(),
            _get_stoner_role_id(),
            _get_drunken_role_id(),
        ]
        verified_ids = [rid for rid in verified_ids if int(rid) > 0]

        has_verified = _member_has_any_role(member, verified_ids)

        unverified_id = _get_unverified_role_id()
        has_unverified = _member_has_any_role(member, [unverified_id]) if unverified_id > 0 else False

        return bool(has_verified and not has_unverified)
    except Exception:
        return False


def _is_retryable_db_error(error: Exception) -> bool:
    text = repr(error).lower()
    markers = (
        "remoteprotocolerror",
        "server disconnected",
        "connection reset",
        "connection aborted",
        "temporarily unavailable",
        "timeout",
        "timed out",
        "eof",
        "network",
        "closed connection",
        "connection refused",
        "connection terminated",
        "httpcore",
        "httpx",
        "broken pipe",
        "connection pool",
        "stream closed",
        "too many requests",
        "try again",
    )
    return any(marker in text for marker in markers)


def _sleep_backoff(attempt: int) -> None:
    base = min(0.35 * (2 ** max(0, attempt - 1)), 3.0)
    jitter = random.uniform(0.05, 0.25)
    time.sleep(base + jitter)


def _execute_db_op(op_name: str, executor: Callable[[], Any], max_attempts: int = 5):
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            return executor()
        except Exception as e:
            last_error = e
            if _is_retryable_db_error(e) and attempt < max_attempts:
                try:
                    reset_supabase()
                except Exception:
                    pass

                print(
                    f"⚠️ {op_name}: transient DB error on attempt "
                    f"{attempt}/{max_attempts}: {repr(e)}"
                )
                _sleep_backoff(attempt)
                continue
            raise

    raise last_error


async def _run_db_op(
    op_name: str,
    executor: Callable[[], Any],
    max_attempts: int = 5,
):
    return await asyncio.to_thread(_execute_db_op, op_name, executor, max_attempts)


def _best_staff_text(
    staff_member: Optional[discord.Member],
    staff_id: Optional[str],
) -> Optional[str]:
    if staff_member is not None:
        try:
            return str(staff_member.id)
        except Exception:
            pass

    if staff_id:
        return str(staff_id)

    return None


def _best_staff_name(
    staff_member: Optional[discord.Member],
    staff_name: Optional[str],
) -> Optional[str]:
    if staff_member is not None:
        try:
            return (
                getattr(staff_member, "display_name", None)
                or getattr(staff_member, "name", None)
                or staff_name
            )
        except Exception:
            pass

    if staff_name:
        return str(staff_name)

    return None


def _normalize_jsonish(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {str(k): _normalize_jsonish(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_normalize_jsonish(v) for v in value]
    return _safe_str(value) or None


# --------------------------------------------------
# DB HELPERS: BOT COMMANDS
# --------------------------------------------------


async def fetch_pending_command() -> Optional[Dict[str, Any]]:
    def _read() -> Optional[Dict[str, Any]]:
        sb = get_supabase()
        if sb is None:
            return None

        res = (
            sb.table("bot_commands")
            .select("*")
            .eq("status", "pending")
            .order("created_at", desc=False)
            .limit(1)
            .execute()
        )

        rows = getattr(res, "data", None) or []
        if not rows:
            return None
        return rows[0]

    try:
        return await _run_db_op("fetch bot command", _read)
    except Exception as e:
        print("❌ Failed fetching bot command:", repr(e))
        return None


async def claim_command(cmd_id: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Atomically claim a command only if it is still pending.

    Returns:
        (claimed, row)
    """
    claim_ts = now_iso()

    def _write():
        sb = get_supabase()
        if sb is None:
            return None

        (
            sb.table("bot_commands")
            .update(
                {
                    "status": "processing",
                    "picked_up_at": claim_ts,
                }
            )
            .eq("id", cmd_id)
            .eq("status", "pending")
            .execute()
        )

        res = (
            sb.table("bot_commands")
            .select("*")
            .eq("id", cmd_id)
            .eq("status", "processing")
            .eq("picked_up_at", claim_ts)
            .limit(1)
            .execute()
        )

        rows = getattr(res, "data", None) or []
        if not rows:
            return None
        return rows[0]

    try:
        row = await _run_db_op("claim bot command", _write)
        return (row is not None, row)
    except Exception as e:
        print("❌ Failed claiming bot command:", repr(e))
        return (False, None)


async def mark_complete(cmd_id: str, result: Dict[str, Any]):
    clean_result = _normalize_jsonish(result) or {}

    def _write():
        sb = get_supabase()
        if sb is None:
            return
        (
            sb.table("bot_commands")
            .update(
                {
                    "status": "completed",
                    "result": clean_result,
                    "completed_at": now_iso(),
                }
            )
            .eq("id", cmd_id)
            .execute()
        )

    try:
        await _run_db_op("mark bot command complete", _write)
    except Exception as e:
        print("❌ Failed marking complete:", repr(e))


async def mark_failed(cmd_id: str, err: str):
    error_text = _safe_str(err) or "Unknown worker error"

    def _write():
        sb = get_supabase()
        if sb is None:
            return
        (
            sb.table("bot_commands")
            .update(
                {
                    "status": "failed",
                    "error": error_text,
                    "completed_at": now_iso(),
                }
            )
            .eq("id", cmd_id)
            .execute()
        )

    try:
        await _run_db_op("mark bot command failed", _write)
    except Exception as e:
        print("❌ Failed marking failed:", repr(e))


# --------------------------------------------------
# DB HELPERS: TICKETS
# --------------------------------------------------


async def get_ticket_by_channel_id(channel_id: int) -> Optional[Dict[str, Any]]:
    channel_text = str(channel_id)

    def _read() -> Optional[Dict[str, Any]]:
        sb = get_supabase()
        if sb is None:
            return None

        res = (
            sb.table("tickets")
            .select("*")
            .or_(f"channel_id.eq.{channel_text},discord_thread_id.eq.{channel_text}")
            .order("created_at", desc=False)
            .limit(1)
            .execute()
        )

        rows = getattr(res, "data", None) or []
        if rows:
            return rows[0]
        return None

    try:
        return await _run_db_op("get ticket by channel_id", _read)
    except Exception as e:
        print("⚠️ Failed fetching ticket by channel_id:", repr(e))
        return None


async def update_ticket_by_channel_id(
    channel_id: int,
    patch: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    channel_text = str(channel_id)
    clean_patch = dict(patch or {})
    clean_patch["updated_at"] = now_iso()

    def _write() -> Optional[Dict[str, Any]]:
        sb = get_supabase()
        if sb is None:
            return None

        (
            sb.table("tickets")
            .update(clean_patch)
            .or_(f"channel_id.eq.{channel_text},discord_thread_id.eq.{channel_text}")
            .execute()
        )

        res = (
            sb.table("tickets")
            .select("*")
            .or_(f"channel_id.eq.{channel_text},discord_thread_id.eq.{channel_text}")
            .limit(1)
            .execute()
        )

        rows = getattr(res, "data", None) or []
        if rows:
            return rows[0]
        return None

    try:
        return await _run_db_op("update ticket by channel_id", _write)
    except Exception as e:
        print("⚠️ Failed updating ticket by channel_id:", repr(e))
        return None


async def update_ticket_by_id(
    ticket_id: str,
    patch: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    clean_patch = dict(patch or {})
    clean_patch["updated_at"] = now_iso()

    def _write() -> Optional[Dict[str, Any]]:
        sb = get_supabase()
        if sb is None:
            return None

        (
            sb.table("tickets")
            .update(clean_patch)
            .eq("id", str(ticket_id))
            .execute()
        )

        res = (
            sb.table("tickets")
            .select("*")
            .eq("id", str(ticket_id))
            .limit(1)
            .execute()
        )

        rows = getattr(res, "data", None) or []
        if rows:
            return rows[0]
        return None

    try:
        return await _run_db_op("update ticket by id", _write)
    except Exception as e:
        print("⚠️ Failed updating ticket by id:", repr(e))
        return None


async def insert_ticket_note(
    *,
    ticket_id: str,
    staff_id: Optional[str],
    staff_name: Optional[str],
    content: str,
) -> None:
    payload_with_updated = {
        "ticket_id": str(ticket_id),
        "staff_id": _safe_str(staff_id) or None,
        "staff_name": _safe_str(staff_name) or None,
        "content": _safe_str(content),
        "created_at": now_iso(),
        "updated_at": now_iso(),
    }

    payload_without_updated = {
        "ticket_id": payload_with_updated["ticket_id"],
        "staff_id": payload_with_updated["staff_id"],
        "staff_name": payload_with_updated["staff_name"],
        "content": payload_with_updated["content"],
        "created_at": payload_with_updated["created_at"],
    }

    def _write():
        sb = get_supabase()
        if sb is None:
            return

        try:
            sb.table("ticket_notes").insert(payload_with_updated).execute()
        except Exception as e:
            text = repr(e).lower()
            if "updated_at" in text and ("column" in text or "schema" in text):
                sb.table("ticket_notes").insert(payload_without_updated).execute()
            else:
                raise

    try:
        await _run_db_op("insert ticket note", _write)
    except Exception as e:
        print("⚠️ Failed inserting ticket note:", repr(e))


# --------------------------------------------------
# DB HELPERS: MEMBER EVENTS / ENTRY FIELDS
# --------------------------------------------------


async def insert_member_event(
    *,
    guild_id: str,
    user_id: str,
    actor_id: Optional[str],
    actor_name: Optional[str],
    event_type: str,
    title: Optional[str] = None,
    reason: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    if not _safe_str(guild_id) or not _safe_str(user_id) or not _safe_str(event_type):
        return

    payload = {
        "guild_id": _safe_str(guild_id),
        "user_id": _safe_str(user_id),
        "actor_id": _safe_str(actor_id) or None,
        "actor_name": _safe_str(actor_name) or None,
        "event_type": _safe_str(event_type),
        "title": _safe_str(title) or None,
        "reason": _safe_str(reason) or None,
        "metadata": _normalize_jsonish(metadata or {}),
        "created_at": now_iso(),
    }

    def _write():
        sb = get_supabase()
        if sb is None:
            return
        sb.table("member_events").insert(payload).execute()

    try:
        await _run_db_op("insert member event", _write)
    except Exception as e:
        print("⚠️ Failed inserting member event:", repr(e))


async def patch_guild_member_entry_fields(
    *,
    guild_id: str,
    user_id: str,
    invited_by: Optional[str] = None,
    invited_by_name: Optional[str] = None,
    invite_code: Optional[str] = None,
    vouched_by: Optional[str] = None,
    vouched_by_name: Optional[str] = None,
    approved_by: Optional[str] = None,
    approved_by_name: Optional[str] = None,
    verification_ticket_id: Optional[str] = None,
    source_ticket_id: Optional[str] = None,
    entry_method: Optional[str] = None,
    verification_source: Optional[str] = None,
    entry_reason: Optional[str] = None,
    approval_reason: Optional[str] = None,
) -> None:
    if not _safe_str(guild_id) or not _safe_str(user_id):
        return

    patch = {
        "invited_by": _safe_str(invited_by) or None,
        "invited_by_name": _safe_str(invited_by_name) or None,
        "invite_code": _safe_str(invite_code) or None,
        "vouched_by": _safe_str(vouched_by) or None,
        "vouched_by_name": _safe_str(vouched_by_name) or None,
        "approved_by": _safe_str(approved_by) or None,
        "approved_by_name": _safe_str(approved_by_name) or None,
        "verification_ticket_id": _safe_str(verification_ticket_id) or None,
        "source_ticket_id": _safe_str(source_ticket_id) or None,
        "entry_method": _safe_str(entry_method) or None,
        "verification_source": _safe_str(verification_source) or None,
        "entry_reason": _safe_str(entry_reason) or None,
        "approval_reason": _safe_str(approval_reason) or None,
        "updated_at": now_iso(),
        "last_seen_at": now_iso(),
    }

    def _write():
        sb = get_supabase()
        if sb is None:
            return
        (
            sb.table("guild_members")
            .update(patch)
            .eq("guild_id", _safe_str(guild_id))
            .eq("user_id", _safe_str(user_id))
            .execute()
        )

    try:
        await _run_db_op("patch guild_members entry fields", _write)
    except Exception as e:
        print("⚠️ Failed patching guild_members entry fields:", repr(e))


async def patch_latest_member_join_context(
    *,
    guild_id: str,
    user_id: str,
    username: Optional[str] = None,
    display_name: Optional[str] = None,
    avatar_url: Optional[str] = None,
    invited_by: Optional[str] = None,
    invited_by_name: Optional[str] = None,
    invite_code: Optional[str] = None,
    entry_method: Optional[str] = None,
    verification_source: Optional[str] = None,
    vouched_by: Optional[str] = None,
    vouched_by_name: Optional[str] = None,
    approved_by: Optional[str] = None,
    approved_by_name: Optional[str] = None,
    source_ticket_id: Optional[str] = None,
    join_note: Optional[str] = None,
) -> None:
    guild_id_text = _safe_str(guild_id)
    user_id_text = _safe_str(user_id)

    if not guild_id_text or not user_id_text:
        return

    def _write():
        sb = get_supabase()
        if sb is None:
            return

        read_res = (
            sb.table("member_joins")
            .select("id")
            .eq("guild_id", guild_id_text)
            .eq("user_id", user_id_text)
            .order("joined_at", desc=True)
            .limit(1)
            .execute()
        )

        rows = getattr(read_res, "data", None) or []
        if not rows:
            return

        row_id = rows[0].get("id")
        if not row_id:
            return

        patch = {
            "username": _safe_str(username) or None,
            "display_name": _safe_str(display_name) or None,
            "avatar_url": _safe_str(avatar_url) or None,
            "invited_by": _safe_str(invited_by) or None,
            "invited_by_name": _safe_str(invited_by_name) or None,
            "invite_code": _safe_str(invite_code) or None,
            "entry_method": _safe_str(entry_method) or None,
            "verification_source": _safe_str(verification_source) or None,
            "vouched_by": _safe_str(vouched_by) or None,
            "vouched_by_name": _safe_str(vouched_by_name) or None,
            "approved_by": _safe_str(approved_by) or None,
            "approved_by_name": _safe_str(approved_by_name) or None,
            "source_ticket_id": _safe_str(source_ticket_id) or None,
            "join_note": _safe_str(join_note) or None,
        }

        (
            sb.table("member_joins")
            .update(patch)
            .eq("id", row_id)
            .execute()
        )

    try:
        await _run_db_op("patch latest member_joins context", _write)
    except Exception as e:
        print("⚠️ Failed patching latest member_joins context:", repr(e))


# --------------------------------------------------
# COMMAND EXECUTION HELPERS
# --------------------------------------------------


async def safe_fetch_channel(guild: discord.Guild, channel_id: int):
    channel = guild.get_channel(channel_id)
    if channel:
        return channel

    try:
        channel = await bot.fetch_channel(channel_id)
        return channel
    except Exception:
        return None


async def _persist_closed_ticket(
    *,
    channel_id: int,
    staff_member: Optional[discord.Member],
    staff_id: Optional[str],
    reason: str,
) -> None:
    await update_ticket_by_channel_id(
        channel_id,
        {
            "status": "closed",
            "closed_at": now_iso(),
            "closed_by": _best_staff_text(staff_member, staff_id),
            "closed_reason": reason or "Resolved",
        },
    )


async def _persist_reopened_ticket(
    *,
    channel_id: int,
) -> None:
    await update_ticket_by_channel_id(
        channel_id,
        {
            "status": "open",
            "reopened_at": now_iso(),
        },
    )


async def _persist_assigned_ticket(
    *,
    channel_id: int,
    staff_member: Optional[discord.Member],
    staff_id: str,
) -> None:
    assigned_value = _best_staff_text(staff_member, staff_id) or str(staff_id)

    await update_ticket_by_channel_id(
        channel_id,
        {
            "status": "claimed",
            "claimed_by": assigned_value,
            "assigned_to": assigned_value,
        },
    )


async def _record_verification_context(
    *,
    guild_id: str,
    user_id: str,
    username: Optional[str],
    display_name: Optional[str],
    avatar_url: Optional[str],
    invited_by: Optional[str],
    invited_by_name: Optional[str],
    invite_code: Optional[str],
    vouched_by: Optional[str],
    vouched_by_name: Optional[str],
    approved_by: Optional[str],
    approved_by_name: Optional[str],
    verification_ticket_id: Optional[str],
    source_ticket_id: Optional[str],
    entry_method: Optional[str],
    verification_source: Optional[str],
    entry_reason: Optional[str],
    approval_reason: Optional[str],
) -> None:
    await patch_guild_member_entry_fields(
        guild_id=guild_id,
        user_id=user_id,
        invited_by=invited_by,
        invited_by_name=invited_by_name,
        invite_code=invite_code,
        vouched_by=vouched_by,
        vouched_by_name=vouched_by_name,
        approved_by=approved_by,
        approved_by_name=approved_by_name,
        verification_ticket_id=verification_ticket_id,
        source_ticket_id=source_ticket_id,
        entry_method=entry_method,
        verification_source=verification_source,
        entry_reason=entry_reason,
        approval_reason=approval_reason,
    )

    await patch_latest_member_join_context(
        guild_id=guild_id,
        user_id=user_id,
        username=username,
        display_name=display_name,
        avatar_url=avatar_url,
        invited_by=invited_by,
        invited_by_name=invited_by_name,
        invite_code=invite_code,
        entry_method=entry_method,
        verification_source=verification_source,
        vouched_by=vouched_by,
        vouched_by_name=vouched_by_name,
        approved_by=approved_by,
        approved_by_name=approved_by_name,
        source_ticket_id=source_ticket_id,
        join_note=entry_reason or approval_reason,
    )


async def _safe_send_channel_message(channel: Optional[discord.abc.Messageable], content: str) -> None:
    try:
        if channel is not None:
            await channel.send(content)
    except Exception:
        pass


async def _log_verification_note_and_event(
    *,
    ticket_id: Optional[str],
    guild_id: str,
    user_id: str,
    actor_id: Optional[str],
    actor_name: Optional[str],
    note: Optional[str],
    event_type: str,
    title: str,
    reason: Optional[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    if ticket_id:
        await insert_ticket_note(
            ticket_id=ticket_id,
            staff_id=actor_id,
            staff_name=actor_name,
            content=note or title,
        )

    await insert_member_event(
        guild_id=guild_id,
        user_id=user_id,
        actor_id=actor_id,
        actor_name=actor_name,
        event_type=event_type,
        title=title,
        reason=reason,
        metadata=metadata or {},
    )


# --------------------------------------------------
# COMMAND EXECUTION
# --------------------------------------------------


async def execute_command(cmd: Dict[str, Any]):
    action = _safe_str(cmd.get("action"))
    payload = cmd.get("payload") or {}

    guild_id = _safe_str(cmd.get("guild_id"))
    guild = bot.get_guild(_safe_int(guild_id))

    if guild is None:
        raise RuntimeError("Guild not found")

    print(f"⚙️ Executing bot command: {action}")

    if action == "create_ticket":
        user_id = _safe_int(payload.get("user_id"))
        member = guild.get_member(user_id)

        if member is None:
            raise RuntimeError("User not found in guild")

        category = _safe_str(payload.get("category")) or "support"
        ghost = _safe_bool(payload.get("ghost"), False)
        opening_message = _safe_str(payload.get("opening_message"))
        priority = _safe_str(payload.get("priority")) or "medium"

        parent_category_id = _safe_int(payload.get("parent_category_id"), 0) or None

        staff_role_ids_raw = payload.get("staff_role_ids")
        staff_role_ids: List[int] = []
        if isinstance(staff_role_ids_raw, list):
            for v in staff_role_ids_raw:
                rid = _safe_int(v, 0)
                if rid > 0:
                    staff_role_ids.append(rid)

        channel = None

        try:
            channel = await create_ticket_channel(
                guild=guild,
                owner=member,
                category=category,
                source="dashboard_create_ticket",
                is_ghost=ghost,
                parent_category_id=parent_category_id,
                staff_role_ids=staff_role_ids,
                opening_message=opening_message or None,
                priority=priority,
            )
        except TypeError:
            channel = None
        except Exception as e:
            print("⚠️ Preferred create_ticket_channel signature failed:", repr(e))
            channel = None

        if channel is None:
            try:
                channel = await create_ticket_channel(
                    guild=guild,
                    member=member,
                    category=category,
                    ghost=ghost,
                )
            except Exception as e:
                print("⚠️ Fallback create_ticket_channel signature failed:", repr(e))
                raise RuntimeError("Ticket creation failed")

        if not channel:
            raise RuntimeError("Ticket creation failed")

        ticket_row = await get_ticket_by_channel_id(int(channel.id))

        if ticket_row:
            verification_ticket_id = (
                _safe_str(payload.get("verification_ticket_id"))
                or _safe_str(payload.get("source_ticket_id"))
                or _safe_str(ticket_row.get("id"))
            )
            source_ticket_id = (
                _safe_str(payload.get("source_ticket_id"))
                or _safe_str(ticket_row.get("id"))
            )

            await _record_verification_context(
                guild_id=guild_id,
                user_id=str(member.id),
                username=getattr(member, "name", None),
                display_name=getattr(member, "display_name", None),
                avatar_url=getattr(getattr(member, "display_avatar", None), "url", None),
                invited_by=_safe_str(payload.get("invited_by")) or None,
                invited_by_name=_safe_str(payload.get("invited_by_name")) or None,
                invite_code=_safe_str(payload.get("invite_code")) or None,
                vouched_by=_safe_str(payload.get("vouched_by")) or None,
                vouched_by_name=_safe_str(payload.get("vouched_by_name")) or None,
                approved_by=_safe_str(payload.get("approved_by")) or None,
                approved_by_name=_safe_str(payload.get("approved_by_name")) or None,
                verification_ticket_id=verification_ticket_id,
                source_ticket_id=source_ticket_id,
                entry_method=_safe_str(payload.get("entry_method")) or "ticket",
                verification_source=_safe_str(payload.get("verification_source")) or "dashboard_create_ticket",
                entry_reason=_safe_str(payload.get("entry_reason")) or None,
                approval_reason=_safe_str(payload.get("approval_reason")) or None,
            )

            await insert_member_event(
                guild_id=guild_id,
                user_id=str(member.id),
                actor_id=_safe_str(cmd.get("requested_by")) or None,
                actor_name="Dashboard",
                event_type="ticket_created",
                title="Ticket Created",
                reason=opening_message or f"Created {category} ticket from dashboard.",
                metadata={
                    "ticket_id": _safe_str(ticket_row.get("id")) or None,
                    "ticket_number": ticket_row.get("ticket_number"),
                    "channel_id": str(channel.id),
                    "category": category,
                    "priority": priority,
                    "entry_method": _safe_str(payload.get("entry_method")) or None,
                    "verification_source": _safe_str(payload.get("verification_source")) or None,
                    "source": "bot_command_worker",
                },
            )

        return {
            "created": True,
            "channel_id": str(channel.id),
            "channel_name": getattr(channel, "name", None),
        }

    if action == "close_ticket":
        channel_id = _safe_int(payload.get("channel_id"))
        reason = _safe_str(payload.get("reason")) or "Resolved"
        staff_id = _safe_str(payload.get("staff_id")) or None

        channel = await safe_fetch_channel(guild, channel_id)
        staff_member = guild.get_member(_safe_int(staff_id)) if staff_id else None

        if channel is None:
            await _persist_closed_ticket(
                channel_id=channel_id,
                staff_member=staff_member,
                staff_id=staff_id,
                reason=reason,
            )
            return {"closed": False, "reason": "channel_missing_but_db_closed"}

        try:
            await channel.send("🔒 Ticket closed by staff.")
        except Exception:
            pass

        await _persist_closed_ticket(
            channel_id=channel_id,
            staff_member=staff_member,
            staff_id=staff_id,
            reason=reason,
        )

        ticket_row = await get_ticket_by_channel_id(channel_id)
        if ticket_row:
            await insert_member_event(
                guild_id=guild_id,
                user_id=_safe_str(ticket_row.get("user_id")),
                actor_id=_best_staff_text(staff_member, staff_id),
                actor_name=_best_staff_name(staff_member, None),
                event_type="ticket_closed",
                title="Ticket Closed",
                reason=reason,
                metadata={
                    "ticket_id": _safe_str(ticket_row.get("id")) or None,
                    "ticket_number": ticket_row.get("ticket_number"),
                    "channel_id": str(channel_id),
                    "source": "bot_command_worker",
                },
            )

        return {
            "closed": True,
            "channel_id": str(channel_id),
            "closed_reason": reason,
            "closed_by": _best_staff_text(staff_member, staff_id),
        }

    if action == "delete_ticket":
        channel_id = _safe_int(payload.get("channel_id"))
        force_transcript = _safe_bool(payload.get("force_transcript"))
        ghost = _safe_bool(payload.get("ghost"))
        reason = _safe_str(payload.get("reason")) or "Deleted from dashboard"
        staff_id = _safe_str(payload.get("staff_id")) or None

        channel = await safe_fetch_channel(guild, channel_id)
        staff_member = guild.get_member(_safe_int(staff_id)) if staff_id else None

        if channel is None:
            updated_row = await update_ticket_by_channel_id(
                channel_id,
                {
                    "status": "deleted",
                    "deleted_at": now_iso(),
                    "deleted_by": _best_staff_text(staff_member, staff_id),
                    "closed_reason": reason,
                },
            )
            if updated_row:
                await insert_member_event(
                    guild_id=guild_id,
                    user_id=_safe_str(updated_row.get("user_id")),
                    actor_id=_best_staff_text(staff_member, staff_id),
                    actor_name=_best_staff_name(staff_member, None),
                    event_type="ticket_deleted",
                    title="Ticket Deleted",
                    reason=reason,
                    metadata={
                        "ticket_id": _safe_str(updated_row.get("id")) or None,
                        "ticket_number": updated_row.get("ticket_number"),
                        "channel_id": str(channel_id),
                        "channel_missing": True,
                        "source": "bot_command_worker",
                    },
                )
            return {"deleted": False, "reason": "channel_missing_but_db_marked_deleted"}

        result = await delete_ticket_with_optional_transcript(
            channel=channel,
            deleted_by=staff_member,
            is_ghost=ghost,
            force_transcript_for_ghost=force_transcript,
            reason=reason,
        )

        if result.get("ok"):
            updated_row = await update_ticket_by_channel_id(
                channel_id,
                {
                    "status": "deleted",
                    "deleted_at": now_iso(),
                    "deleted_by": _best_staff_text(staff_member, staff_id),
                    "closed_reason": reason,
                    "transcript_url": result.get("transcript_url"),
                    "transcript_message_id": (
                        str(result.get("transcript_message_id"))
                        if result.get("transcript_message_id") is not None
                        else None
                    ),
                    "transcript_channel_id": (
                        str(result.get("transcript_channel_id"))
                        if result.get("transcript_channel_id") is not None
                        else None
                    ),
                },
            )

            if updated_row:
                await insert_member_event(
                    guild_id=guild_id,
                    user_id=_safe_str(updated_row.get("user_id")),
                    actor_id=_best_staff_text(staff_member, staff_id),
                    actor_name=_best_staff_name(staff_member, None),
                    event_type="ticket_deleted",
                    title="Ticket Deleted",
                    reason=reason,
                    metadata={
                        "ticket_id": _safe_str(updated_row.get("id")) or None,
                        "ticket_number": updated_row.get("ticket_number"),
                        "channel_id": str(channel_id),
                        "transcript_url": result.get("transcript_url"),
                        "source": "bot_command_worker",
                    },
                )

        return result

    if action == "reopen_ticket":
        channel_id = _safe_int(payload.get("channel_id"))
        channel = await safe_fetch_channel(guild, channel_id)

        if channel is not None:
            try:
                await channel.send("♻️ Ticket reopened.")
            except Exception:
                pass

        await _persist_reopened_ticket(channel_id=channel_id)

        ticket_row = await get_ticket_by_channel_id(channel_id)
        if ticket_row:
            await insert_member_event(
                guild_id=guild_id,
                user_id=_safe_str(ticket_row.get("user_id")),
                actor_id=_safe_str(cmd.get("requested_by")) or None,
                actor_name="Dashboard",
                event_type="ticket_reopened",
                title="Ticket Reopened",
                reason="Ticket reopened from dashboard.",
                metadata={
                    "ticket_id": _safe_str(ticket_row.get("id")) or None,
                    "ticket_number": ticket_row.get("ticket_number"),
                    "channel_id": str(channel_id),
                    "source": "bot_command_worker",
                },
            )

        return {
            "reopened": True,
            "channel_id": str(channel_id),
        }

    if action == "assign_ticket":
        staff_id = _safe_str(payload.get("staff_id"))
        channel_id = _safe_int(payload.get("channel_id"))

        staff = guild.get_member(_safe_int(staff_id))
        channel = await safe_fetch_channel(guild, channel_id)

        if not staff_id:
            return {"assigned": False, "reason": "missing_staff_id"}

        if channel is not None and staff is not None:
            try:
                await channel.send(f"👮 Assigned to {staff.mention}")
            except Exception:
                pass

        await _persist_assigned_ticket(
            channel_id=channel_id,
            staff_member=staff,
            staff_id=staff_id,
        )

        ticket_row = await get_ticket_by_channel_id(channel_id)
        if ticket_row:
            await insert_member_event(
                guild_id=guild_id,
                user_id=_safe_str(ticket_row.get("user_id")),
                actor_id=_best_staff_text(staff, staff_id),
                actor_name=_best_staff_name(staff, None),
                event_type="ticket_assigned",
                title="Ticket Assigned",
                reason=f"Ticket assigned to {staff_id}.",
                metadata={
                    "ticket_id": _safe_str(ticket_row.get("id")) or None,
                    "ticket_number": ticket_row.get("ticket_number"),
                    "channel_id": str(channel_id),
                    "assigned_to": staff_id,
                    "source": "bot_command_worker",
                },
            )

        return {
            "assigned": True,
            "staff_id": staff_id,
            "channel_id": str(channel_id),
        }

    if action == "sync_active_tickets":
        include_closed_visible_channels = _safe_bool(
            payload.get("include_closed_visible_channels"),
            True,
        )
        dry_run = _safe_bool(payload.get("dry_run"), False)

        summary = await sync_active_ticket_channels_for_guild(
            guild,
            source="bot_command_sync_active_tickets",
            include_closed_visible_channels=include_closed_visible_channels,
            dry_run=dry_run,
        )

        return {
            "synced": True,
            "summary": summary,
        }

    if action == "sync_single_ticket":
        channel_id = _safe_int(payload.get("channel_id"))
        dry_run = _safe_bool(payload.get("dry_run"), False)

        if channel_id <= 0:
            return {
                "synced": False,
                "reason": "missing_channel_id",
            }

        channel = await safe_fetch_channel(guild, channel_id)
        if channel is None or not isinstance(channel, discord.TextChannel):
            return {
                "synced": False,
                "reason": "channel_missing",
                "channel_id": str(channel_id),
            }

        summary = await sync_one_ticket_channel(
            channel,
            source="bot_command_sync_single_ticket",
            dry_run=dry_run,
        )

        return {
            "synced": True,
            "channel_id": str(channel_id),
            "summary": summary,
        }

    if action == "portal_ticket_reply":
        channel_id = _safe_int(payload.get("channel_id"))
        user_id = _safe_str(payload.get("user_id"))
        username = _safe_str(payload.get("username")) or "Member"
        content = _safe_str(payload.get("content"))
        ticket_id = _safe_str(payload.get("ticket_id"))
        message_id = _safe_str(payload.get("message_id"))
        staff_name = _safe_str(payload.get("staff_name"))

        if not channel_id:
            return {
                "mirrored": False,
                "reason": "missing_channel_id",
                "ticket_id": ticket_id or None,
            }

        if not content:
            return {
                "mirrored": False,
                "reason": "missing_content",
                "ticket_id": ticket_id or None,
            }

        channel = await safe_fetch_channel(guild, channel_id)

        if channel is None:
            return {
                "mirrored": False,
                "reason": "channel_missing",
                "ticket_id": ticket_id or None,
                "channel_id": str(channel_id),
            }

        author_label = staff_name or username or "Staff"
        header = f"💬 **Dashboard Reply** from **{author_label}**"
        body = content

        try:
            sent = await channel.send(f"{header}\n\n{body}")

            await _persist_reopened_ticket(channel_id=channel_id)

            if ticket_id:
                await insert_member_event(
                    guild_id=guild_id,
                    user_id=user_id,
                    actor_id=_safe_str(cmd.get("requested_by")) or None,
                    actor_name=author_label,
                    event_type="ticket_reply_mirrored",
                    title="Dashboard Reply Mirrored",
                    reason=content[:240],
                    metadata={
                        "ticket_id": ticket_id,
                        "channel_id": str(channel_id),
                        "portal_message_id": message_id or None,
                        "discord_message_id": str(getattr(sent, "id", "")) or None,
                        "source": "bot_command_worker",
                    },
                )

            return {
                "mirrored": True,
                "ticket_id": ticket_id or None,
                "channel_id": str(channel_id),
                "portal_message_id": message_id or None,
                "discord_message_id": str(getattr(sent, "id", "")) or None,
            }
        except Exception as e:
            return {
                "mirrored": False,
                "reason": repr(e),
                "ticket_id": ticket_id or None,
                "channel_id": str(channel_id),
            }

    if action == "approve_verification":
        ticket_id = _safe_str(payload.get("ticket_id"))
        channel_id = _safe_int(payload.get("channel_id"))
        user_id = _safe_int(payload.get("user_id"))
        username = _safe_str(payload.get("username")) or str(user_id or "Member")
        staff_id = _safe_str(payload.get("staff_id"))
        staff_name = _safe_str(payload.get("staff_name")) or "Dashboard Staff"
        reason = _safe_str(payload.get("reason")) or "Approved by staff review"

        invited_by = _safe_str(payload.get("invited_by")) or None
        invited_by_name = _safe_str(payload.get("invited_by_name")) or None
        invite_code = _safe_str(payload.get("invite_code")) or None
        vouched_by = _safe_str(payload.get("vouched_by")) or None
        vouched_by_name = _safe_str(payload.get("vouched_by_name")) or None
        entry_method = _safe_str(payload.get("entry_method")) or "verification"
        verification_source = (
            _safe_str(payload.get("verification_source"))
            or "dashboard_approve_verification"
        )
        entry_reason = _safe_str(payload.get("entry_reason")) or reason
        approval_reason = _safe_str(payload.get("approval_reason")) or reason

        member = guild.get_member(user_id)
        channel = await safe_fetch_channel(guild, channel_id) if channel_id else None
        staff_member = guild.get_member(_safe_int(staff_id)) if staff_id else None

        if member is None:
            return {"approved": False, "reason": "member_missing", "user_id": str(user_id)}

        if _member_already_verified(member):
            if ticket_id:
                await insert_ticket_note(
                    ticket_id=ticket_id,
                    staff_id=staff_id,
                    staff_name=staff_name,
                    content=(
                        f"Duplicate approval blocked.\n"
                        f"Member: {username} ({member.id})\n"
                        f"Reason: member already appears verified before worker execution."
                    ),
                )
                await update_ticket_by_id(
                    ticket_id,
                    {
                        "status": "closed",
                        "closed_reason": "Duplicate approval blocked: already verified",
                        "closed_by": staff_id or None,
                        "claimed_by": staff_id or None,
                        "assigned_to": staff_id or None,
                        "closed_at": now_iso(),
                    },
                )

            return {
                "approved": False,
                "skipped": True,
                "already_verified": True,
                "reason": "member_already_verified",
                "user_id": str(member.id),
                "ticket_id": ticket_id or None,
            }

        service_result = await approve_verification(
            guild=guild,
            channel=channel if isinstance(channel, discord.TextChannel) else None,
            token=_safe_str(payload.get("token")),
            staff_member=staff_member if isinstance(staff_member, discord.Member) else None,  # type: ignore[arg-type]
            decision_text="APPROVED",
            close_after=True,
            owner=member,
        ) if isinstance(staff_member, discord.Member) and _safe_str(payload.get("token")) else None

        if service_result and service_result.get("already_verified"):
            if ticket_id:
                await insert_ticket_note(
                    ticket_id=ticket_id,
                    staff_id=staff_id,
                    staff_name=staff_name,
                    content=(
                        f"Duplicate approval blocked.\n"
                        f"Member: {username} ({member.id})\n"
                        f"Reason: verification service detected already-verified state."
                    ),
                )
                await update_ticket_by_id(
                    ticket_id,
                    {
                        "status": "closed",
                        "closed_reason": "Duplicate approval blocked: already verified",
                        "closed_by": staff_id or None,
                        "claimed_by": staff_id or None,
                        "assigned_to": staff_id or None,
                        "closed_at": now_iso(),
                    },
                )

            return {
                "approved": False,
                "skipped": True,
                "already_verified": True,
                "reason": "member_already_verified",
                "user_id": str(member.id),
                "ticket_id": ticket_id or None,
            }

        if service_result and not service_result.get("ok"):
            if ticket_id:
                await insert_ticket_note(
                    ticket_id=ticket_id,
                    staff_id=staff_id,
                    staff_name=staff_name,
                    content=(
                        "Verification approval failed.\n"
                        f"Member: {username} ({member.id})\n"
                        f"Reason: {_safe_str(service_result.get('message')) or reason}"
                    ),
                )

            return {
                "approved": False,
                "reason": _safe_str(service_result.get("message")) or "approval_failed",
                "user_id": str(member.id),
                "ticket_id": ticket_id or None,
            }

        added_role_ids: List[str] = []
        removed_role_ids: List[str] = []

        if service_result and service_result.get("ok"):
            added_role_ids = [
                str(getattr(role, "id", role))
                for role in (service_result.get("roles") or [])
            ]
        else:
            # Fallback parity path if no token/service route is available
            verified_role = guild.get_role(_get_verified_role_id()) if _get_verified_role_id() > 0 else None
            resident_role = guild.get_role(_get_resident_role_id()) if _get_resident_role_id() > 0 else None
            unverified_role = guild.get_role(_get_unverified_role_id()) if _get_unverified_role_id() > 0 else None

            added = []
            if verified_role is not None and verified_role not in member.roles:
                try:
                    await member.add_roles(
                        verified_role,
                        reason=f"Verification approved by {staff_name} ({staff_id})",
                    )
                    added.append(int(verified_role.id))
                except Exception as e:
                    return {
                        "approved": False,
                        "reason": f"failed_add_verified_role:{repr(e)}",
                        "user_id": str(member.id),
                        "ticket_id": ticket_id or None,
                    }

            if resident_role is not None and resident_role not in member.roles:
                try:
                    await member.add_roles(
                        resident_role,
                        reason=f"Verification approved by {staff_name} ({staff_id})",
                    )
                    added.append(int(resident_role.id))
                except Exception:
                    pass

            removed = []
            if unverified_role is not None and unverified_role in member.roles:
                try:
                    await member.remove_roles(
                        unverified_role,
                        reason=f"Verification approved by {staff_name} ({staff_id})",
                    )
                    removed.append(int(unverified_role.id))
                except Exception:
                    pass

            added_role_ids = [str(x) for x in added]
            removed_role_ids = [str(x) for x in removed]

        if channel is not None:
            await _safe_send_channel_message(
                channel,
                f"✅ {member.mention} was approved by **{staff_name}**.\nReason: {reason}",
            )

        if ticket_id:
            await insert_ticket_note(
                ticket_id=ticket_id,
                staff_id=staff_id,
                staff_name=staff_name,
                content=(
                    f"Verification approved.\n"
                    f"Member: {username} ({member.id})\n"
                    f"Reason: {reason}\n"
                    f"Added roles: {added_role_ids or []}\n"
                    f"Removed roles: {removed_role_ids or []}"
                ),
            )
            await update_ticket_by_id(
                ticket_id,
                {
                    "status": "closed",
                    "closed_reason": reason,
                    "closed_by": staff_id or None,
                    "claimed_by": staff_id or None,
                    "assigned_to": staff_id or None,
                    "closed_at": now_iso(),
                },
            )

        await _record_verification_context(
            guild_id=guild_id,
            user_id=str(member.id),
            username=getattr(member, "name", None) or username,
            display_name=getattr(member, "display_name", None) or username,
            avatar_url=getattr(getattr(member, "display_avatar", None), "url", None),
            invited_by=invited_by,
            invited_by_name=invited_by_name,
            invite_code=invite_code,
            vouched_by=vouched_by,
            vouched_by_name=vouched_by_name,
            approved_by=staff_id or None,
            approved_by_name=staff_name,
            verification_ticket_id=ticket_id or None,
            source_ticket_id=ticket_id or None,
            entry_method=entry_method,
            verification_source=verification_source,
            entry_reason=entry_reason,
            approval_reason=approval_reason,
        )

        await insert_member_event(
            guild_id=guild_id,
            user_id=str(member.id),
            actor_id=staff_id or None,
            actor_name=staff_name,
            event_type="verification_approved",
            title="Verification Approved",
            reason=reason,
            metadata={
                "ticket_id": ticket_id or None,
                "channel_id": str(channel_id) if channel_id else None,
                "added_role_ids": added_role_ids,
                "removed_role_ids": removed_role_ids,
                "invited_by": invited_by,
                "invited_by_name": invited_by_name,
                "invite_code": invite_code,
                "vouched_by": vouched_by,
                "vouched_by_name": vouched_by_name,
                "entry_method": entry_method,
                "verification_source": verification_source,
                "source": "bot_command_worker",
            },
        )

        return {
            "approved": True,
            "user_id": str(member.id),
            "added_role_ids": added_role_ids,
            "removed_role_ids": removed_role_ids,
            "ticket_id": ticket_id or None,
            "service_used": bool(service_result is not None),
        }

    if action == "deny_verification":
        ticket_id = _safe_str(payload.get("ticket_id"))
        channel_id = _safe_int(payload.get("channel_id"))
        user_id = _safe_int(payload.get("user_id"))
        staff_id = _safe_str(payload.get("staff_id"))
        staff_name = _safe_str(payload.get("staff_name")) or "Dashboard Staff"
        reason = _safe_str(payload.get("reason")) or "Denied by staff review"

        invited_by = _safe_str(payload.get("invited_by")) or None
        invited_by_name = _safe_str(payload.get("invited_by_name")) or None
        invite_code = _safe_str(payload.get("invite_code")) or None
        vouched_by = _safe_str(payload.get("vouched_by")) or None
        vouched_by_name = _safe_str(payload.get("vouched_by_name")) or None
        entry_method = _safe_str(payload.get("entry_method")) or "verification"
        verification_source = (
            _safe_str(payload.get("verification_source"))
            or "dashboard_deny_verification"
        )

        member = guild.get_member(user_id)
        channel = await safe_fetch_channel(guild, channel_id) if channel_id else None
        staff_member = guild.get_member(_safe_int(staff_id)) if staff_id else None

        service_result = await deny_verification(
            guild=guild,
            channel=channel if isinstance(channel, discord.TextChannel) else None,
            token=_safe_str(payload.get("token")),
            staff_member=staff_member if isinstance(staff_member, discord.Member) else None,  # type: ignore[arg-type]
            decision_text="DENIED",
            close_after=True,
        ) if isinstance(staff_member, discord.Member) and _safe_str(payload.get("token")) else None

        if service_result and not service_result.get("ok"):
            if ticket_id:
                await insert_ticket_note(
                    ticket_id=ticket_id,
                    staff_id=staff_id,
                    staff_name=staff_name,
                    content=(
                        "Verification denial failed.\n"
                        f"Member: {user_id}\n"
                        f"Reason: {_safe_str(service_result.get('message')) or reason}"
                    ),
                )

            return {
                "denied": False,
                "reason": _safe_str(service_result.get("message")) or "deny_failed",
                "user_id": str(user_id),
                "ticket_id": ticket_id or None,
            }

        if channel is not None:
            target = member.mention if member is not None else f"`{user_id}`"
            await _safe_send_channel_message(
                channel,
                f"❌ Verification denied for {target} by **{staff_name}**.\nReason: {reason}",
            )

        if ticket_id:
            await insert_ticket_note(
                ticket_id=ticket_id,
                staff_id=staff_id,
                staff_name=staff_name,
                content=(
                    f"Verification denied.\n"
                    f"Member: {user_id}\n"
                    f"Reason: {reason}"
                ),
            )
            await update_ticket_by_id(
                ticket_id,
                {
                    "status": "closed",
                    "closed_reason": reason,
                    "closed_by": staff_id or None,
                    "claimed_by": staff_id or None,
                    "assigned_to": staff_id or None,
                    "closed_at": now_iso(),
                },
            )

        await insert_member_event(
            guild_id=guild_id,
            user_id=str(user_id),
            actor_id=staff_id or None,
            actor_name=staff_name,
            event_type="verification_denied",
            title="Verification Denied",
            reason=reason,
            metadata={
                "ticket_id": ticket_id or None,
                "channel_id": str(channel_id) if channel_id else None,
                "invited_by": invited_by,
                "invited_by_name": invited_by_name,
                "invite_code": invite_code,
                "vouched_by": vouched_by,
                "vouched_by_name": vouched_by_name,
                "entry_method": entry_method,
                "verification_source": verification_source,
                "source": "bot_command_worker",
            },
        )

        return {
            "denied": True,
            "user_id": str(user_id),
            "ticket_id": ticket_id or None,
            "reason_text": reason,
            "service_used": bool(service_result is not None),
        }

    if action == "remove_unverified_role":
        ticket_id = _safe_str(payload.get("ticket_id"))
        user_id = _safe_int(payload.get("user_id"))
        staff_id = _safe_str(payload.get("staff_id"))
        staff_name = _safe_str(payload.get("staff_name")) or "Dashboard Staff"
        reason = _safe_str(payload.get("reason")) or "Unverified role removed by staff"

        member = guild.get_member(user_id)
        if member is None:
            return {"removed": False, "reason": "member_missing", "user_id": str(user_id)}

        if _member_already_verified(member):
            if ticket_id:
                await insert_ticket_note(
                    ticket_id=ticket_id,
                    staff_id=staff_id,
                    staff_name=staff_name,
                    content=(
                        f"Skipped removing Unverified.\n"
                        f"Member: {member.id}\n"
                        f"Reason: member already appears fully verified."
                    ),
                )

            return {
                "removed": False,
                "skipped": True,
                "reason": "member_already_verified",
                "user_id": str(member.id),
                "ticket_id": ticket_id or None,
            }

        unverified_role = guild.get_role(_get_unverified_role_id()) if _get_unverified_role_id() > 0 else None
        removed: List[int] = []

        if unverified_role is not None and unverified_role in member.roles:
            try:
                await member.remove_roles(
                    unverified_role,
                    reason=f"{reason} by {staff_name} ({staff_id})",
                )
                removed.append(int(unverified_role.id))
            except Exception as e:
                return {
                    "removed": False,
                    "reason": f"failed_remove_unverified:{repr(e)}",
                    "user_id": str(member.id),
                    "ticket_id": ticket_id or None,
                }

        if ticket_id:
            await insert_ticket_note(
                ticket_id=ticket_id,
                staff_id=staff_id,
                staff_name=staff_name,
                content=(
                    f"Removed unverified role.\n"
                    f"Member: {member.id}\n"
                    f"Reason: {reason}\n"
                    f"Removed roles: {removed or []}"
                ),
            )

        await insert_member_event(
            guild_id=guild_id,
            user_id=str(member.id),
            actor_id=staff_id or None,
            actor_name=staff_name,
            event_type="unverified_role_removed",
            title="Unverified Role Removed",
            reason=reason,
            metadata={
                "ticket_id": ticket_id or None,
                "removed_role_ids": [str(x) for x in removed],
                "source": "bot_command_worker",
            },
        )

        return {
            "removed": True,
            "user_id": str(member.id),
            "removed_role_ids": [str(x) for x in removed],
            "ticket_id": ticket_id or None,
        }

    if action == "post_verification_staff_panel":
        ticket_id = _safe_str(payload.get("ticket_id"))
        channel_id = _safe_int(payload.get("channel_id"))
        user_id = _safe_int(payload.get("user_id"))
        username = _safe_str(payload.get("username")) or str(user_id or "Member")
        reason = _safe_str(payload.get("reason")) or "Verification submission received from website."
        submitted_from = _safe_str(payload.get("source")) or "website_submission"
        staff_id = _safe_str(payload.get("staff_id"))
        staff_name = _safe_str(payload.get("staff_name")) or "System"

        if not channel_id:
            return {
                "posted": False,
                "reason": "missing_channel_id",
                "ticket_id": ticket_id or None,
            }

        channel = await safe_fetch_channel(guild, channel_id)
        if channel is None or not isinstance(channel, discord.TextChannel):
            return {
                "posted": False,
                "reason": "channel_missing",
                "channel_id": str(channel_id),
                "ticket_id": ticket_id or None,
            }

        member = guild.get_member(user_id) if user_id else None

        result = ""
        try:
            result = await post_or_replace_verification_staff_panel(
                channel,
                member=member,
                user_id=user_id if user_id else None,
                username=username,
                submitted_from=submitted_from,
                reason=reason,
            )
        except Exception as e:
            print("⚠️ post_or_replace_verification_staff_panel failed:", repr(e))
            result = ""

        if ticket_id:
            await insert_ticket_note(
                ticket_id=ticket_id,
                staff_id=staff_id or None,
                staff_name=staff_name or None,
                content=(
                    "Website verification submission received.\n"
                    "Posted or updated staff review panel in the same verification ticket.\n"
                    f"Result: {result or 'failed'}\n"
                    f"Reason: {reason}"
                ),
            )

        if member is not None:
            await insert_member_event(
                guild_id=guild_id,
                user_id=str(member.id),
                actor_id=staff_id or None,
                actor_name=staff_name,
                event_type="verification_submission_received",
                title="Verification Submission Received",
                reason=reason,
                metadata={
                    "ticket_id": ticket_id or None,
                    "channel_id": str(channel_id),
                    "source": submitted_from,
                    "worker_action": "post_verification_staff_panel",
                },
            )

        return {
            "posted": bool(result),
            "result": result or None,
            "channel_id": str(channel_id),
            "ticket_id": ticket_id or None,
            "user_id": str(user_id) if user_id else None,
        }

    if action == "repost_verify_ui":
        ticket_id = _safe_str(payload.get("ticket_id"))
        channel_id = _safe_int(payload.get("channel_id"))
        staff_id = _safe_str(payload.get("staff_id"))
        staff_name = _safe_str(payload.get("staff_name")) or "Dashboard Staff"

        if not channel_id:
            return {"reposted": False, "reason": "missing_channel_id"}

        channel = await safe_fetch_channel(guild, channel_id)
        if channel is None:
            return {"reposted": False, "reason": "channel_missing", "channel_id": str(channel_id)}

        ok = False
        try:
            ok = await ensure_verify_ui_present(
                channel,
                reason=f"dashboard_repost:{staff_id or 'staff'}",
            )
        except Exception as e:
            print("⚠️ ensure_verify_ui_present failed:", repr(e))
            ok = False

        if ticket_id:
            await insert_ticket_note(
                ticket_id=ticket_id,
                staff_id=staff_id,
                staff_name=staff_name,
                content="Reposted verify UI from dashboard.",
            )

        return {
            "reposted": bool(ok),
            "channel_id": str(channel_id),
            "ticket_id": ticket_id or None,
        }

    if action == "sync_members":
        summary = await sync_all_members(guild)
        return summary

    if action == "reconcile_departed_members":
        summary = await reconcile_departed_members(guild)
        return summary

    if action == "sync_role_members":
        role_id = _safe_int(payload.get("role_id"))
        role = guild.get_role(role_id)

        if role is None:
            return {"synced": False, "reason": "role_missing"}

        summary = await sync_role_members(role)
        return summary

    raise RuntimeError(f"Unknown action: {action}")


# --------------------------------------------------
# WORKER LOOP
# --------------------------------------------------


async def worker_loop():
    await bot.wait_until_ready()

    print("🤖 Bot command worker started.")

    while not bot.is_closed():
        cmd = await fetch_pending_command()

        if not cmd:
            await asyncio.sleep(POLL_INTERVAL)
            continue

        cmd_id = _safe_str(cmd.get("id"))
        if not cmd_id:
            await asyncio.sleep(POLL_INTERVAL)
            continue

        claimed, claimed_row = await claim_command(cmd_id)
        if not claimed:
            await asyncio.sleep(0.5)
            continue

        active_cmd = claimed_row or cmd
        started_at = time.monotonic()

        try:
            result = await execute_command(active_cmd)
            duration_ms = int((time.monotonic() - started_at) * 1000)

            await mark_complete(
                cmd_id,
                {
                    **(_normalize_jsonish(result) or {}),
                    "worker_duration_ms": duration_ms,
                    "processed_at": now_iso(),
                },
            )

            print(f"✅ Command completed: {cmd_id} ({duration_ms}ms)")

        except Exception as e:
            duration_ms = int((time.monotonic() - started_at) * 1000)
            print("❌ Command failed:", repr(e))
            await mark_failed(
                cmd_id,
                f"{str(e)} | worker_duration_ms={duration_ms}"
            )

        await asyncio.sleep(1)


def start_worker():
    global _WORKER_TASK

    try:
        if _WORKER_TASK is not None and not _WORKER_TASK.done():
            print("ℹ️ Bot command worker already running; skipping duplicate start.")
            return _WORKER_TASK

        loop = bot.loop
        _WORKER_TASK = loop.create_task(worker_loop(), name="bot-command-worker")
        return _WORKER_TASK
    except Exception as e:
        print("❌ Failed to start bot command worker:", repr(e))
        return None
