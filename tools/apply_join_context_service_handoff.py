#!/usr/bin/env python3
from __future__ import annotations

"""Physically route events.py join attribution into members_new.join_context_service.

Run after stoney_verify/members_new/join_context_service.py exists.
This script replaces the service-owned join-context bodies in events.py with thin
delegates so events.py stops owning invite attribution and join persistence.
"""

import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / "stoney_verify" / "events.py"
JOIN_CONTEXT_SERVICE = ROOT / "stoney_verify" / "members_new" / "join_context_service.py"

JOIN_TRUTH_DELEGATE = '''def _join_truth_quality(entry_method: str, *, invite_code: Optional[str] = None, invited_by: Optional[str] = None) -> Tuple[str, int, str]:
    from .members_new.join_context_service import join_truth_quality

    return join_truth_quality(entry_method, invite_code=invite_code, invited_by=invited_by)


'''

BUILD_CONTEXT_DELEGATE = '''def _build_join_context(
    *,
    entry_method: str,
    join_source: str,
    verification_source: str,
    invite_code: Optional[str] = None,
    invited_by: Optional[str] = None,
    invited_by_name: Optional[str] = None,
    vouched_by: Optional[str] = None,
    vouched_by_name: Optional[str] = None,
    approved_by: Optional[str] = None,
    approved_by_name: Optional[str] = None,
    entry_reason: Optional[str] = None,
    approval_reason: Optional[str] = None,
    join_note: Optional[str] = None,
    channel_id: Optional[str] = None,
    channel_name: Optional[str] = None,
    vanity_used: bool = False,
    source_ticket_id: Optional[str] = None,
) -> Dict[str, Any]:
    from .members_new.join_context_service import build_join_context

    return build_join_context(
        entry_method=entry_method,
        join_source=join_source,
        verification_source=verification_source,
        invite_code=invite_code,
        invited_by=invited_by,
        invited_by_name=invited_by_name,
        vouched_by=vouched_by,
        vouched_by_name=vouched_by_name,
        approved_by=approved_by,
        approved_by_name=approved_by_name,
        entry_reason=entry_reason,
        approval_reason=approval_reason,
        join_note=join_note,
        channel_id=channel_id,
        channel_name=channel_name,
        vanity_used=vanity_used,
        source_ticket_id=source_ticket_id,
    )


'''

REFRESH_CACHE_DELEGATE = '''async def _refresh_guild_invite_cache(guild: discord.Guild) -> bool:
    from .members_new.join_context_service import warm_invite_cache_for_guild

    return await warm_invite_cache_for_guild(guild)


'''

DETECT_CONTEXT_DELEGATE = '''async def _detect_join_entry_context(member: discord.Member) -> Dict[str, Any]:
    from .members_new.join_context_service import detect_join_entry_context

    return await detect_join_entry_context(member)


'''

PERSIST_CONTEXT_DELEGATE = '''async def _persist_member_join_context(
    member: discord.Member,
    risk_profile: Optional[Dict[str, Any]] = None,
) -> None:
    from .members_new.join_context_service import persist_member_join_context

    await persist_member_join_context(member, risk_profile=risk_profile)


'''

REQUIRED_SERVICE_MARKERS = (
    "def join_truth_quality",
    "def build_join_context",
    "async def warm_invite_cache_for_guild",
    "async def detect_join_entry_context",
    "async def persist_member_join_context",
)

FORBIDDEN_EVENTS_MARKERS = (
    "sb.table(\"member_joins\").insert(payload).execute()",
    "sb.table(\"member_events\").insert(payload).execute()",
    "[JOIN-CONTEXT] member_joins insert failed",
    "[JOIN-CONTEXT] member_events insert failed",
    "join detect invite fetch failed guild=",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def die(message: str) -> None:
    print(f"❌ {message}")
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"✅ {message}")


def replace_block(text: str, *, start_marker: str, end_marker: str, replacement: str, label: str) -> tuple[str, bool]:
    start = text.find(start_marker)
    if start < 0:
        if replacement.strip() in text:
            ok(f"{label} already applied")
            return text, False
        die(f"Could not find start marker for {label}: {start_marker!r}")
    end = text.find(end_marker, start)
    if end < 0:
        die(f"Could not find end marker for {label}: {end_marker!r}")
    current = text[start:end]
    if replacement.strip() in current:
        ok(f"{label} already applied")
        return text, False
    return text[:start] + replacement + text[end:], True


def verify_service_ready() -> None:
    service = read(JOIN_CONTEXT_SERVICE)
    missing = [m for m in REQUIRED_SERVICE_MARKERS if m not in service]
    if missing:
        print("❌ join_context_service is not ready:")
        for marker in missing:
            print(" -", marker)
        raise SystemExit(1)


def main() -> int:
    if not EVENTS.exists():
        die(f"Missing {EVENTS}")
    if not JOIN_CONTEXT_SERVICE.exists():
        die(f"Missing {JOIN_CONTEXT_SERVICE}")

    verify_service_ready()

    text = read(EVENTS)
    changed = False

    text, did = replace_block(
        text,
        start_marker="def _join_truth_quality(",
        end_marker="def _build_join_context(\n",
        replacement=JOIN_TRUTH_DELEGATE,
        label="events._join_truth_quality service delegate",
    )
    changed = changed or did

    text, did = replace_block(
        text,
        start_marker="def _build_join_context(\n",
        end_marker="async def _refresh_guild_invite_cache(guild: discord.Guild) -> bool:\n",
        replacement=BUILD_CONTEXT_DELEGATE,
        label="events._build_join_context service delegate",
    )
    changed = changed or did

    text, did = replace_block(
        text,
        start_marker="async def _refresh_guild_invite_cache(guild: discord.Guild) -> bool:\n",
        end_marker="async def _warm_all_guild_invite_caches() -> None:\n",
        replacement=REFRESH_CACHE_DELEGATE,
        label="events._refresh_guild_invite_cache service delegate",
    )
    changed = changed or did

    text, did = replace_block(
        text,
        start_marker="async def _detect_join_entry_context(member: discord.Member) -> Dict[str, Any]:\n",
        end_marker="async def _persist_member_join_context(\n",
        replacement=DETECT_CONTEXT_DELEGATE,
        label="events._detect_join_entry_context service delegate",
    )
    changed = changed or did

    text, did = replace_block(
        text,
        start_marker="async def _persist_member_join_context(\n",
        end_marker="# ============================================================\n# VC session helpers\n",
        replacement=PERSIST_CONTEXT_DELEGATE,
        label="events._persist_member_join_context service delegate",
    )
    changed = changed or did

    offenders = [m for m in FORBIDDEN_EVENTS_MARKERS if m in text]
    if offenders:
        print("❌ events.py still contains join-context ownership markers:")
        for marker in offenders:
            print(" -", marker)
        return 1

    if changed:
        write(EVENTS, text)
        ok("Updated stoney_verify/events.py join-context ownership")
    else:
        ok("events.py join-context delegates already present")

    py_compile.compile(str(EVENTS), doraise=True)
    py_compile.compile(str(JOIN_CONTEXT_SERVICE), doraise=True)
    ok("Compiled events.py and join_context_service.py")

    print("\nNext commands:")
    print("  git diff -- stoney_verify/events.py")
    print("  python tools/apply_join_context_service_handoff.py")
    print("  python -m py_compile stoney_verify/events.py stoney_verify/members_new/join_context_service.py")
    print("  git add stoney_verify/events.py")
    print('  git commit -m "Physically hand off join context events"')
    print("  git push origin main")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
