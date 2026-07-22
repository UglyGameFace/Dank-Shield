from __future__ import annotations

from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
STATS = ROOT / "stoney_verify/security_stats.py"
EVENTS = ROOT / "stoney_verify/tickets_new/event_service.py"
EXISTING_TESTS = ROOT / "tests/test_security_stats_channels.py"
NEW_TESTS = ROOT / "tests/test_ticket_stats_lifecycle_refresh_behavior.py"
SELF = Path(__file__).resolve()


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly 1 match, found {count}")
    return source.replace(old, new, 1)


stats = STATS.read_text(encoding="utf-8")
events = EVENTS.read_text(encoding="utf-8")
existing_tests = EXISTING_TESTS.read_text(encoding="utf-8")

old_counting = '''def _query_ticket_status_counts_sync(guild_id: int) -> Optional[Dict[str, int]]:
    """Read current ticket lifecycle totals from the canonical tickets table."""
    sb = get_supabase()
    if sb is None:
        return None

    response = (
        sb.table("tickets")
        .select("status,claimed_by,assigned_to")
        .eq("guild_id", str(int(guild_id)))
        .execute()
    )
    rows = getattr(response, "data", None)
    if rows is None:
        return None

    counts = dict(DEFAULT_TICKET_STATUS_COUNTS)
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        status = str(row.get("status") or "").strip().lower()
        if status in {"active", "reopened"}:
            status = "open"
        if status == "open":
            claimed_by = str(row.get("claimed_by") or "").strip()
            assigned_to = str(row.get("assigned_to") or "").strip()
            if claimed_by or assigned_to:
                status = "claimed"

        if status == "open":
            counts["open_tickets"] += 1
        elif status == "claimed":
            counts["claimed_tickets"] += 1
        elif status == "closed":
            counts["closed_tickets"] += 1
    return counts
'''

new_counting = '''def _ticket_has_assignee(row: Mapping[str, Any]) -> bool:
    for key in ("claimed_by", "assigned_to"):
        text = str(row.get(key) or "").strip().lower()
        if text and text not in {"0", "none", "null"}:
            return True
    return False


def _ticket_status_counts_from_rows(rows: Any) -> Dict[str, int]:
    """Count active tickets and the claimed subset from stored lifecycle rows."""
    counts = dict(DEFAULT_TICKET_STATUS_COUNTS)
    try:
        iterable = list(rows or [])
    except Exception:
        return counts

    for row in iterable:
        if not isinstance(row, Mapping):
            continue

        status = str(row.get("status") or "").strip().lower()
        if status in {"active", "reopened"}:
            status = "open"

        if status in {"open", "claimed"}:
            # Claimed tickets remain open; Claimed is a subset, not a separate
            # lifecycle bucket that should disappear from the active total.
            counts["open_tickets"] += 1
            if status == "claimed" or _ticket_has_assignee(row):
                counts["claimed_tickets"] += 1
        elif status == "closed":
            counts["closed_tickets"] += 1

    return counts


def _query_ticket_status_counts_sync(guild_id: int) -> Optional[Dict[str, int]]:
    """Read current ticket lifecycle totals from the canonical tickets table."""
    sb = get_supabase()
    if sb is None:
        return None

    response = (
        sb.table("tickets")
        .select("status,claimed_by,assigned_to")
        .eq("guild_id", str(int(guild_id)))
        .execute()
    )
    rows = getattr(response, "data", None)
    if rows is None:
        return None

    return _ticket_status_counts_from_rows(rows)
'''
stats = replace_once(stats, old_counting, new_counting, "ticket status counting owner")

refresh_anchor = '''    return changed


@tasks.loop(minutes=10)
'''
refresh_replacement = '''    return changed


async def refresh_ticket_stats_for_guild_id(guild_id: int) -> bool:
    """Force the enabled live stats display to reflect a ticket transition."""
    gid = _safe_int(guild_id, 0)
    if gid <= 0:
        return False

    try:
        guild = bot.get_guild(gid)
    except Exception:
        guild = None
    if guild is None:
        return False

    try:
        return await refresh_security_stats_display(guild, force=True)
    except Exception as exc:
        try:
            print(
                f"⚠️ security_stats ticket refresh failed guild={gid} "
                f"error={type(exc).__name__}"
            )
        except Exception:
            pass
        return False


@tasks.loop(minutes=10)
'''
stats = replace_once(stats, refresh_anchor, refresh_replacement, "ticket lifecycle refresh helper")

stats = replace_once(
    stats,
    '''    "record_spam_guard_action",
    "refresh_security_stats_display",
]''',
    '''    "record_spam_guard_action",
    "refresh_security_stats_display",
    "refresh_ticket_stats_for_guild_id",
]''',
    "security stats public export",
)

constants_anchor = '''_EVENT_DEDUP_WINDOW_SECONDS = 3.0
_EVENT_DEDUP_CACHE: Dict[str, float] = {}
'''
constants_replacement = '''_EVENT_DEDUP_WINDOW_SECONDS = 3.0
_EVENT_DEDUP_CACHE: Dict[str, float] = {}

_TICKET_STATS_REFRESH_EVENTS = frozenset(
    {
        "ticket_created",
        "ticket_claimed",
        "ticket_unclaimed",
        "ticket_closed",
        "ticket_reopened",
        "ticket_deleted",
    }
)
'''
events = replace_once(events, constants_anchor, constants_replacement, "ticket lifecycle refresh event set")

log_return = '''    return await log_activity_event(
        guild_id=resolved_guild_id,
        title=title,
        description=description,
        event_type=event_type,
        event_family="ticket",
        source=source,
        actor_user_id=actor_user_id,
        actor_name=actor_name,
        target_user_id=resolved_target_user_id,
        target_name=resolved_target_name,
        channel_id=resolved_channel_id,
        channel_name=resolved_channel_name,
        ticket_id=resolved_ticket_id,
        ticket_message_id=ticket_message_id,
        related_id=resolved_ticket_id or resolved_channel_id,
        related_table="tickets",
        reason=reason,
        metadata=meta,
        extra=extra,
    )
'''
log_replacement = '''    logged = await log_activity_event(
        guild_id=resolved_guild_id,
        title=title,
        description=description,
        event_type=event_type,
        event_family="ticket",
        source=source,
        actor_user_id=actor_user_id,
        actor_name=actor_name,
        target_user_id=resolved_target_user_id,
        target_name=resolved_target_name,
        channel_id=resolved_channel_id,
        channel_name=resolved_channel_name,
        ticket_id=resolved_ticket_id,
        ticket_message_id=ticket_message_id,
        related_id=resolved_ticket_id or resolved_channel_id,
        related_table="tickets",
        reason=reason,
        metadata=meta,
        extra=extra,
    )

    normalized_event = _normalize_event_type(event_type)
    if normalized_event in _TICKET_STATS_REFRESH_EVENTS:
        try:
            from ..security_stats import refresh_ticket_stats_for_guild_id

            await refresh_ticket_stats_for_guild_id(int(resolved_guild_id))
        except Exception as exc:
            _debug(
                "ticket stats refresh skipped "
                f"guild={resolved_guild_id} event={normalized_event} "
                f"error={type(exc).__name__}"
            )

    return logged
'''
events = replace_once(events, log_return, log_replacement, "central lifecycle stats refresh hook")

existing_tests = replace_once(
    existing_tests,
    '''    assert security_stats._query_ticket_status_counts_sync(777) == {
        "open_tickets": 2,
        "claimed_tickets": 3,
        "closed_tickets": 2,
    }
''',
    '''    assert security_stats._query_ticket_status_counts_sync(777) == {
        "open_tickets": 5,
        "claimed_tickets": 3,
        "closed_tickets": 2,
    }
''',
    "authoritative lifecycle count expectation",
)

new_tests = '''from __future__ import annotations

import asyncio

from stoney_verify import security_stats
from stoney_verify.tickets_new import event_service


def test_single_claimed_ticket_counts_as_open_and_claimed() -> None:
    counts = security_stats._ticket_status_counts_from_rows(
        [
            {
                "status": "claimed",
                "claimed_by": "55",
                "assigned_to": "55",
            }
        ]
    )

    assert counts == {
        "open_tickets": 1,
        "claimed_tickets": 1,
        "closed_tickets": 0,
    }


def test_open_ticket_with_assignee_remains_in_active_total() -> None:
    counts = security_stats._ticket_status_counts_from_rows(
        [
            {
                "status": "open",
                "claimed_by": None,
                "assigned_to": "88",
            },
            {
                "status": "open",
                "claimed_by": "0",
                "assigned_to": None,
            },
        ]
    )

    assert counts == {
        "open_tickets": 2,
        "claimed_tickets": 1,
        "closed_tickets": 0,
    }


def test_lifecycle_ticket_event_forces_live_stats_refresh(monkeypatch) -> None:
    refreshes: list[int] = []

    async def fake_log_activity_event(**_kwargs) -> bool:
        return True

    async def fake_refresh(guild_id: int) -> bool:
        refreshes.append(guild_id)
        return True

    monkeypatch.setattr(event_service, "log_activity_event", fake_log_activity_event)
    monkeypatch.setattr(
        security_stats,
        "refresh_ticket_stats_for_guild_id",
        fake_refresh,
    )

    result = asyncio.run(
        event_service.log_ticket_event(
            guild_id=777,
            event_type="ticket_claimed",
            actor_user_id=55,
            actor_name="Staff",
            channel_id=999,
            ticket_row={
                "id": "123",
                "guild_id": "777",
                "status": "claimed",
                "claimed_by": "55",
                "assigned_to": "55",
                "channel_id": "999",
            },
        )
    )

    assert result is True
    assert refreshes == [777]


def test_non_lifecycle_ticket_event_does_not_refresh_stats(monkeypatch) -> None:
    refreshes: list[int] = []

    async def fake_log_activity_event(**_kwargs) -> bool:
        return True

    async def fake_refresh(guild_id: int) -> bool:
        refreshes.append(guild_id)
        return True

    monkeypatch.setattr(event_service, "log_activity_event", fake_log_activity_event)
    monkeypatch.setattr(
        security_stats,
        "refresh_ticket_stats_for_guild_id",
        fake_refresh,
    )

    result = asyncio.run(
        event_service.log_ticket_event(
            guild_id=777,
            event_type="ticket_note_added",
            actor_user_id=55,
            actor_name="Staff",
            channel_id=999,
            ticket_row={
                "id": "123",
                "guild_id": "777",
                "status": "claimed",
                "channel_id": "999",
            },
        )
    )

    assert result is True
    assert refreshes == []
'''

if NEW_TESTS.exists():
    raise RuntimeError(f"new regression file already exists: {NEW_TESTS}")

for path, text in (
    (STATS, stats),
    (EVENTS, events),
    (EXISTING_TESTS, existing_tests),
    (NEW_TESTS, new_tests),
):
    compile(text, str(path), "exec")

STATS.write_text(stats, encoding="utf-8")
EVENTS.write_text(events, encoding="utf-8")
EXISTING_TESTS.write_text(existing_tests, encoding="utf-8")
NEW_TESTS.write_text(new_tests, encoding="utf-8")
SELF.unlink()

subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)

print("✅ Claimed tickets now remain inside the Open Tickets total.")
print("✅ Claimed Tickets remains the claimed subset of active tickets.")
print("✅ Create/claim/unclaim/close/reopen/delete force a live stats refresh.")
print("✅ Added exact regression coverage for Open 1 / Claimed 1.")
print("✅ Generated Python compiles.")
print("✅ Temporary helper removed from the working tree.")
print("✅ git diff --check passed.")
