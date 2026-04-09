# stoney_verify/tickets_new/sla_service.py
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ..globals import *  # noqa: F401,F403
from .repository import (
    get_ticket_by_any_channel_id,
    list_open_tickets_for_guild,
    update_ticket_by_channel_id,
)

# ============================================================
# tickets_new/sla_service.py
# ------------------------------------------------------------
# Purpose:
# - centralize ticket SLA deadline calculation
# - keep SLA logic dashboard-friendly
# - use existing tickets.sla_deadline column safely
# - avoid requiring new tables right now
# - work for open / claimed ticket states
# - support future staff metrics + event service wiring
#
# Notes:
# - We only rely on the existing tickets table for now.
# - No new schema is required to use this file.
# - This file is safe to create now and wire later.
# ============================================================

ACTIVE_STATUSES = {"open", "claimed"}
INACTIVE_STATUSES = {"closed", "deleted"}

VALID_PRIORITIES = {"low", "medium", "high", "urgent"}

DEFAULT_FIRST_RESPONSE_MINUTES = {
    "low": 24 * 60,      # 24h
    "medium": 8 * 60,    # 8h
    "high": 2 * 60,      # 2h
    "urgent": 30,        # 30m
}

DEFAULT_CLAIMED_FOLLOW_UP_MINUTES = {
    "low": 48 * 60,      # 48h
    "medium": 24 * 60,   # 24h
    "high": 8 * 60,      # 8h
    "urgent": 2 * 60,    # 2h
}

DEFAULT_WARNING_THRESHOLD_PERCENT = 20
DEFAULT_TRACK_GHOST_TICKETS = False


# ============================================================
# Small helpers
# ============================================================

def _sla_debug(msg: str) -> None:
    try:
        print(f"🧩 ticket_sla {msg}")
    except Exception:
        pass


def _safe_str(value: Any) -> str:
    try:
        return str(value)
    except Exception:
        return ""


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or isinstance(value, bool):
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
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
        return default
    except Exception:
        return default


def _now_utc_dt() -> datetime:
    try:
        current = now_utc()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


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


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    try:
        text = str(value or "").strip()
        if not text:
            return None

        if text.endswith("Z"):
            text = text[:-1] + "+00:00"

        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)

        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _coalesce_datetime(*values: Any) -> Optional[datetime]:
    for value in values:
        parsed = _parse_iso_datetime(value)
        if parsed is not None:
            return parsed
    return None


def _normalize_priority(value: Any) -> str:
    text = _safe_str(value).strip().lower()
    return text if text in VALID_PRIORITIES else "medium"


def _normalize_status(value: Any) -> str:
    return _safe_str(value).strip().lower() or "open"


def _priority_minutes_from_globals(
    *,
    prefix_candidates: List[str],
    priority: str,
    default_minutes: int,
) -> int:
    priority_key = _normalize_priority(priority).upper()

    for prefix in prefix_candidates:
        try:
            key = f"{prefix}_{priority_key}_MINUTES"
            raw = globals().get(key)
            minutes = _safe_int(raw, 0)
            if minutes > 0:
                return minutes
        except Exception:
            continue

    return int(default_minutes)


def _first_response_minutes(priority: str) -> int:
    norm = _normalize_priority(priority)
    return _priority_minutes_from_globals(
        prefix_candidates=[
            "TICKET_SLA_FIRST_RESPONSE",
            "SLA_FIRST_RESPONSE",
        ],
        priority=norm,
        default_minutes=DEFAULT_FIRST_RESPONSE_MINUTES[norm],
    )


def _claimed_follow_up_minutes(priority: str) -> int:
    norm = _normalize_priority(priority)
    return _priority_minutes_from_globals(
        prefix_candidates=[
            "TICKET_SLA_CLAIMED_FOLLOW_UP",
            "SLA_CLAIMED_FOLLOW_UP",
            "TICKET_SLA_RESOLUTION",
            "SLA_RESOLUTION",
        ],
        priority=norm,
        default_minutes=DEFAULT_CLAIMED_FOLLOW_UP_MINUTES[norm],
    )


def _warning_threshold_percent() -> int:
    for key in (
        "TICKET_SLA_WARNING_THRESHOLD_PERCENT",
        "SLA_WARNING_THRESHOLD_PERCENT",
    ):
        try:
            value = _safe_int(globals().get(key), 0)
            if value > 0:
                return min(value, 95)
        except Exception:
            continue

    return DEFAULT_WARNING_THRESHOLD_PERCENT


def _track_ghost_tickets() -> bool:
    for key in (
        "TICKET_SLA_TRACK_GHOST_TICKETS",
        "SLA_TRACK_GHOST_TICKETS",
    ):
        try:
            raw = globals().get(key)
            if raw is not None:
                return _safe_bool(raw, DEFAULT_TRACK_GHOST_TICKETS)
        except Exception:
            continue

    return DEFAULT_TRACK_GHOST_TICKETS


def _is_ghost_ticket(row: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(row, dict):
        return False
    return _safe_bool(row.get("is_ghost"), False)


def _has_assignee(row: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(row, dict):
        return False

    assigned_to = _safe_str(row.get("assigned_to")).strip()
    claimed_by = _safe_str(row.get("claimed_by")).strip()
    return bool(assigned_to or claimed_by)


def _ticket_created_at(row: Optional[Dict[str, Any]]) -> Optional[datetime]:
    if not isinstance(row, dict):
        return None
    return _coalesce_datetime(row.get("created_at"))


def _ticket_last_activity_at(row: Optional[Dict[str, Any]]) -> Optional[datetime]:
    if not isinstance(row, dict):
        return None

    return _coalesce_datetime(
        row.get("last_activity_at"),
        row.get("updated_at"),
        row.get("created_at"),
    )


def _ticket_existing_deadline(row: Optional[Dict[str, Any]]) -> Optional[datetime]:
    if not isinstance(row, dict):
        return None
    return _coalesce_datetime(row.get("sla_deadline"))


def _ticket_channel_id(row: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(row, dict):
        return None

    channel_id = _safe_str(row.get("channel_id")).strip()
    if channel_id:
        return channel_id

    thread_id = _safe_str(row.get("discord_thread_id")).strip()
    return thread_id or None


def _deadline_basis(row: Dict[str, Any]) -> str:
    status = _normalize_status(row.get("status"))

    if status not in ACTIVE_STATUSES:
        return "inactive"

    if _is_ghost_ticket(row) and not _track_ghost_tickets():
        return "ghost_exempt"

    if _has_assignee(row) or status == "claimed":
        return "claimed_follow_up"

    return "first_response"


def _deadline_anchor(row: Dict[str, Any], basis: str) -> Optional[datetime]:
    if basis == "first_response":
        return _coalesce_datetime(
            row.get("created_at"),
            row.get("updated_at"),
            row.get("last_activity_at"),
        )

    if basis == "claimed_follow_up":
        return _coalesce_datetime(
            row.get("last_activity_at"),
            row.get("updated_at"),
            row.get("created_at"),
        )

    return None


def _deadline_window_minutes(row: Dict[str, Any], basis: str) -> int:
    priority = _normalize_priority(row.get("priority"))

    if basis == "first_response":
        return _first_response_minutes(priority)

    if basis == "claimed_follow_up":
        return _claimed_follow_up_minutes(priority)

    return 0


def _minutes_from_timedelta(delta: timedelta) -> int:
    seconds = delta.total_seconds()

    if seconds == 0:
        return 0

    if seconds > 0:
        return int(math.floor(seconds / 60))

    return -int(math.ceil(abs(seconds) / 60))


# ============================================================
# Core SLA calculation
# ============================================================

def calculate_ticket_sla_deadline(
    ticket_row: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    current_time = now or _now_utc_dt()
    basis = _deadline_basis(ticket_row)
    priority = _normalize_priority(ticket_row.get("priority"))
    status = _normalize_status(ticket_row.get("status"))

    if basis == "inactive":
        return {
            "ok": True,
            "status": status,
            "priority": priority,
            "basis": basis,
            "deadline": None,
            "deadline_iso": None,
            "anchor_at": None,
            "anchor_at_iso": None,
            "window_minutes": 0,
            "ghost_exempt": False,
        }

    if basis == "ghost_exempt":
        return {
            "ok": True,
            "status": status,
            "priority": priority,
            "basis": basis,
            "deadline": None,
            "deadline_iso": None,
            "anchor_at": None,
            "anchor_at_iso": None,
            "window_minutes": 0,
            "ghost_exempt": True,
        }

    anchor_at = _deadline_anchor(ticket_row, basis)
    if anchor_at is None:
        anchor_at = current_time

    window_minutes = _deadline_window_minutes(ticket_row, basis)
    deadline = anchor_at + timedelta(minutes=window_minutes)

    return {
        "ok": True,
        "status": status,
        "priority": priority,
        "basis": basis,
        "deadline": deadline,
        "deadline_iso": _utc_iso(deadline),
        "anchor_at": anchor_at,
        "anchor_at_iso": _utc_iso(anchor_at),
        "window_minutes": int(window_minutes),
        "ghost_exempt": False,
    }


def build_ticket_sla_snapshot(
    ticket_row: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    current_time = now or _now_utc_dt()
    calc = calculate_ticket_sla_deadline(ticket_row, now=current_time)

    deadline = calc.get("deadline")
    anchor_at = calc.get("anchor_at")
    window_minutes = _safe_int(calc.get("window_minutes"), 0)

    state = "no_sla"
    is_overdue = False
    minutes_remaining: Optional[int] = None
    overdue_minutes = 0
    percent_remaining: Optional[float] = None
    warning = False

    if isinstance(deadline, datetime):
        delta = deadline - current_time
        minutes_remaining = _minutes_from_timedelta(delta)

        total_window_seconds = 0.0
        if isinstance(anchor_at, datetime):
            total_window_seconds = max((deadline - anchor_at).total_seconds(), 0.0)

        remaining_seconds = max(delta.total_seconds(), 0.0)
        if total_window_seconds > 0:
            percent_remaining = (remaining_seconds / total_window_seconds) * 100.0

        if delta.total_seconds() < 0:
            state = "overdue"
            is_overdue = True
            overdue_minutes = abs(_minutes_from_timedelta(delta))
        else:
            warning_cutoff = float(_warning_threshold_percent())
            if percent_remaining is not None and percent_remaining <= warning_cutoff:
                state = "warning"
                warning = True
            else:
                state = "on_time"

    ticket_id = _safe_str(ticket_row.get("id")).strip() or None
    channel_id = _ticket_channel_id(ticket_row)

    return {
        "ok": True,
        "ticket_id": ticket_id,
        "channel_id": channel_id,
        "guild_id": _safe_str(ticket_row.get("guild_id")).strip() or None,
        "user_id": _safe_str(ticket_row.get("user_id")).strip() or None,
        "username": _safe_str(ticket_row.get("username")).strip() or None,
        "title": _safe_str(ticket_row.get("title")).strip() or None,
        "category": _safe_str(ticket_row.get("category")).strip() or None,
        "status": calc.get("status"),
        "priority": calc.get("priority"),
        "basis": calc.get("basis"),
        "assigned_to": _safe_str(ticket_row.get("assigned_to")).strip() or None,
        "claimed_by": _safe_str(ticket_row.get("claimed_by")).strip() or None,
        "is_ghost": _is_ghost_ticket(ticket_row),
        "ghost_exempt": bool(calc.get("ghost_exempt", False)),
        "created_at": _utc_iso(_ticket_created_at(ticket_row)),
        "last_activity_at": _utc_iso(_ticket_last_activity_at(ticket_row)),
        "existing_sla_deadline": _utc_iso(_ticket_existing_deadline(ticket_row)),
        "sla_deadline": calc.get("deadline_iso"),
        "anchor_at": calc.get("anchor_at_iso"),
        "window_minutes": window_minutes,
        "state": state,
        "warning": warning,
        "is_overdue": is_overdue,
        "minutes_remaining": minutes_remaining,
        "overdue_minutes": overdue_minutes,
        "percent_remaining": round(percent_remaining, 2) if percent_remaining is not None else None,
        "dashboard": {
            "sla_state": state,
            "sla_deadline": calc.get("deadline_iso"),
            "sla_basis": calc.get("basis"),
            "sla_warning": warning,
            "sla_overdue": is_overdue,
            "sla_minutes_remaining": minutes_remaining,
            "sla_overdue_minutes": overdue_minutes,
        },
    }


# ============================================================
# Public sync helpers
# ============================================================

async def sync_ticket_sla(
    *,
    channel_id: int | str,
    force_write: bool = False,
) -> Dict[str, Any]:
    row = await get_ticket_by_any_channel_id(channel_id)
    if not row:
        return {
            "ok": False,
            "channel_id": _safe_str(channel_id) or None,
            "reason": "ticket_not_found",
        }

    snapshot = build_ticket_sla_snapshot(row)
    desired_deadline = snapshot.get("sla_deadline")
    current_deadline = snapshot.get("existing_sla_deadline")

    should_write = force_write or (desired_deadline != current_deadline)

    if should_write:
        updated_row = await update_ticket_by_channel_id(
            channel_id,
            {"sla_deadline": desired_deadline},
            allow_thread_fallback=True,
        )
        if updated_row is not None:
            snapshot = build_ticket_sla_snapshot(updated_row)
            snapshot["updated"] = True
            return snapshot

        snapshot["updated"] = False
        snapshot["ok"] = False
        snapshot["reason"] = "write_failed"
        return snapshot

    snapshot["updated"] = False
    return snapshot


async def clear_ticket_sla(
    *,
    channel_id: int | str,
) -> bool:
    updated_row = await update_ticket_by_channel_id(
        channel_id,
        {"sla_deadline": None},
        allow_thread_fallback=True,
    )
    return updated_row is not None


async def sync_open_ticket_slas_for_guild(
    *,
    guild_id: int | str,
    category: Optional[str] = None,
) -> Dict[str, Any]:
    rows = await list_open_tickets_for_guild(
        guild_id=guild_id,
        category=category,
        statuses=["open", "claimed"],
    )

    summary: Dict[str, Any] = {
        "ok": True,
        "guild_id": _safe_str(guild_id),
        "category": _safe_str(category) or None,
        "tickets_seen": len(rows),
        "updated": 0,
        "unchanged": 0,
        "on_time": 0,
        "warning": 0,
        "overdue": 0,
        "no_sla": 0,
        "ghost_exempt": 0,
        "errors": 0,
        "rows": [],
    }

    for row in rows:
        channel_id = _ticket_channel_id(row)
        if not channel_id:
            summary["errors"] += 1
            summary["rows"].append(
                {
                    "ok": False,
                    "ticket_id": _safe_str(row.get("id")).strip() or None,
                    "channel_id": None,
                    "reason": "missing_channel_id",
                }
            )
            continue

        result = await sync_ticket_sla(channel_id=channel_id, force_write=False)
        summary["rows"].append(result)

        if not result.get("ok"):
            summary["errors"] += 1
            continue

        if result.get("updated"):
            summary["updated"] += 1
        else:
            summary["unchanged"] += 1

        state = _safe_str(result.get("state")).strip().lower()
        if state == "on_time":
            summary["on_time"] += 1
        elif state == "warning":
            summary["warning"] += 1
        elif state == "overdue":
            summary["overdue"] += 1
        else:
            summary["no_sla"] += 1

        if _safe_bool(result.get("ghost_exempt"), False):
            summary["ghost_exempt"] += 1

    return summary


# ============================================================
# Public query helpers for dashboard / staff views
# ============================================================

async def get_ticket_sla(
    *,
    channel_id: int | str,
) -> Optional[Dict[str, Any]]:
    row = await get_ticket_by_any_channel_id(channel_id)
    if not row:
        return None
    return build_ticket_sla_snapshot(row)


async def list_overdue_tickets_for_guild(
    *,
    guild_id: int | str,
    category: Optional[str] = None,
) -> List[Dict[str, Any]]:
    rows = await list_open_tickets_for_guild(
        guild_id=guild_id,
        category=category,
        statuses=["open", "claimed"],
    )

    out: List[Dict[str, Any]] = []
    for row in rows:
        snapshot = build_ticket_sla_snapshot(row)
        if snapshot.get("state") == "overdue":
            out.append(snapshot)

    out.sort(
        key=lambda item: (
            -_safe_int(item.get("overdue_minutes"), 0),
            _safe_str(item.get("created_at")),
        )
    )
    return out


async def list_warning_tickets_for_guild(
    *,
    guild_id: int | str,
    category: Optional[str] = None,
) -> List[Dict[str, Any]]:
    rows = await list_open_tickets_for_guild(
        guild_id=guild_id,
        category=category,
        statuses=["open", "claimed"],
    )

    out: List[Dict[str, Any]] = []
    for row in rows:
        snapshot = build_ticket_sla_snapshot(row)
        if snapshot.get("state") == "warning":
            out.append(snapshot)

    out.sort(
        key=lambda item: (
            _safe_int(item.get("minutes_remaining"), 10**9),
            _safe_str(item.get("created_at")),
        )
    )
    return out


async def build_staff_sla_summary_for_guild(
    *,
    guild_id: int | str,
    category: Optional[str] = None,
) -> Dict[str, Any]:
    rows = await list_open_tickets_for_guild(
        guild_id=guild_id,
        category=category,
        statuses=["open", "claimed"],
    )

    staff_map: Dict[str, Dict[str, Any]] = {}
    unassigned: Dict[str, Any] = {
        "staff_id": None,
        "active_tickets": 0,
        "warning_tickets": 0,
        "overdue_tickets": 0,
        "urgent_tickets": 0,
        "high_tickets": 0,
        "medium_tickets": 0,
        "low_tickets": 0,
        "tickets": [],
    }

    for row in rows:
        snapshot = build_ticket_sla_snapshot(row)
        assigned_to = _safe_str(snapshot.get("assigned_to")).strip() or None
        priority = _normalize_priority(snapshot.get("priority"))
        state = _safe_str(snapshot.get("state")).strip().lower()

        bucket = unassigned
        if assigned_to:
            if assigned_to not in staff_map:
                staff_map[assigned_to] = {
                    "staff_id": assigned_to,
                    "active_tickets": 0,
                    "warning_tickets": 0,
                    "overdue_tickets": 0,
                    "urgent_tickets": 0,
                    "high_tickets": 0,
                    "medium_tickets": 0,
                    "low_tickets": 0,
                    "tickets": [],
                }
            bucket = staff_map[assigned_to]

        bucket["active_tickets"] += 1
        bucket[f"{priority}_tickets"] += 1

        if state == "warning":
            bucket["warning_tickets"] += 1
        elif state == "overdue":
            bucket["overdue_tickets"] += 1

        bucket["tickets"].append(snapshot)

    staff_rows = list(staff_map.values())
    staff_rows.sort(
        key=lambda item: (
            -_safe_int(item.get("overdue_tickets"), 0),
            -_safe_int(item.get("warning_tickets"), 0),
            -_safe_int(item.get("active_tickets"), 0),
            _safe_str(item.get("staff_id")),
        )
    )

    return {
        "ok": True,
        "guild_id": _safe_str(guild_id),
        "category": _safe_str(category) or None,
        "staff": staff_rows,
        "unassigned": unassigned,
        "totals": {
            "staff_members_with_tickets": len(staff_rows),
            "unassigned_active_tickets": unassigned["active_tickets"],
            "active_tickets": sum(_safe_int(row.get("active_tickets"), 0) for row in staff_rows) + unassigned["active_tickets"],
            "warning_tickets": sum(_safe_int(row.get("warning_tickets"), 0) for row in staff_rows) + unassigned["warning_tickets"],
            "overdue_tickets": sum(_safe_int(row.get("overdue_tickets"), 0) for row in staff_rows) + unassigned["overdue_tickets"],
        },
    }


async def build_dashboard_sla_snapshot_for_guild(
    *,
    guild_id: int | str,
    category: Optional[str] = None,
) -> Dict[str, Any]:
    rows = await list_open_tickets_for_guild(
        guild_id=guild_id,
        category=category,
        statuses=["open", "claimed"],
    )

    snapshots: List[Dict[str, Any]] = []
    totals = {
        "active": 0,
        "on_time": 0,
        "warning": 0,
        "overdue": 0,
        "no_sla": 0,
        "ghost_exempt": 0,
        "claimed": 0,
        "unclaimed": 0,
    }

    for row in rows:
        snap = build_ticket_sla_snapshot(row)
        snapshots.append(snap)

        totals["active"] += 1

        state = _safe_str(snap.get("state")).strip().lower()
        if state == "on_time":
            totals["on_time"] += 1
        elif state == "warning":
            totals["warning"] += 1
        elif state == "overdue":
            totals["overdue"] += 1
        else:
            totals["no_sla"] += 1

        if _safe_bool(snap.get("ghost_exempt"), False):
            totals["ghost_exempt"] += 1

        if _has_assignee(row):
            totals["claimed"] += 1
        else:
            totals["unclaimed"] += 1

    snapshots.sort(
        key=lambda item: (
            0 if _safe_str(item.get("state")) == "overdue" else 1,
            0 if _safe_str(item.get("state")) == "warning" else 1,
            _safe_int(item.get("minutes_remaining"), 10**9),
            _safe_str(item.get("created_at")),
        )
    )

    return {
        "ok": True,
        "guild_id": _safe_str(guild_id),
        "category": _safe_str(category) or None,
        "totals": totals,
        "tickets": snapshots,
    }


__all__ = [
    "ACTIVE_STATUSES",
    "INACTIVE_STATUSES",
    "VALID_PRIORITIES",
    "calculate_ticket_sla_deadline",
    "build_ticket_sla_snapshot",
    "sync_ticket_sla",
    "clear_ticket_sla",
    "sync_open_ticket_slas_for_guild",
    "get_ticket_sla",
    "list_overdue_tickets_for_guild",
    "list_warning_tickets_for_guild",
    "build_staff_sla_summary_for_guild",
    "build_dashboard_sla_snapshot_for_guild",
]