#!/usr/bin/env python3
from __future__ import annotations

"""Physically route departed-member ticket cleanup out of events.py.

Run after stoney_verify/tickets_new/departed_member_cleanup_service.py exists.
"""

import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / "stoney_verify" / "events.py"
SERVICE = ROOT / "stoney_verify" / "tickets_new" / "departed_member_cleanup_service.py"

AUTO_CLOSE_DELEGATE = '''async def _auto_close_verification_ticket_for_departed_member(
    member: discord.Member,
    *,
    leave_reason: str,
) -> None:
    from .tickets_new.departed_member_cleanup_service import close_verification_ticket_for_departed_member

    await close_verification_ticket_for_departed_member(member, leave_reason=leave_reason)


'''

RECONCILE_DELEGATE = '''async def _reconcile_stale_open_verification_tickets() -> None:
    from .tickets_new.departed_member_cleanup_service import reconcile_stale_open_verification_tickets

    guilds = list(getattr(bot, "guilds", []) or [])
    await reconcile_stale_open_verification_tickets(guilds)


'''

REQUIRED_SERVICE_MARKERS = (
    "async def close_verification_ticket_for_departed_member",
    "async def reconcile_stale_open_verification_tickets",
    "find_open_ticket_for_owner",
    "list_open_tickets_for_guild",
    "mark_ticket_deleted",
    "mark_ticket_closed",
)

FORBIDDEN_EVENTS_MARKERS = (
    "Auto-closing verification ticket for departed member",
    "Startup transcript repair failed",
    "Startup mark_ticket_deleted failed",
    "Startup mark_ticket_closed fallback failed",
    "Stale verification ticket reconciliation complete",
    "stale verification ticket query failed",
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
        die(f"could not find start marker for {label}: {start_marker!r}")
    end = text.find(end_marker, start)
    if end < 0:
        die(f"could not find end marker for {label}: {end_marker!r}")
    current = text[start:end]
    if replacement.strip() in current:
        ok(f"{label} already applied")
        return text, False
    return text[:start] + replacement + text[end:], True


def verify_service_ready() -> None:
    service = read(SERVICE)
    missing = [marker for marker in REQUIRED_SERVICE_MARKERS if marker not in service]
    if missing:
        print("❌ departed member cleanup service is not ready:")
        for marker in missing:
            print(" -", marker)
        raise SystemExit(1)


def main() -> int:
    if not EVENTS.exists():
        die(f"missing {EVENTS}")
    if not SERVICE.exists():
        die(f"missing {SERVICE}")

    verify_service_ready()
    text = read(EVENTS)
    changed = False

    text, did = replace_block(
        text,
        start_marker="async def _auto_close_verification_ticket_for_departed_member(\n",
        end_marker="async def _reconcile_stale_open_verification_tickets() -> None:\n",
        replacement=AUTO_CLOSE_DELEGATE,
        label="events._auto_close_verification_ticket_for_departed_member service delegate",
    )
    changed = changed or did

    text, did = replace_block(
        text,
        start_marker="async def _reconcile_stale_open_verification_tickets() -> None:\n",
        end_marker="# ============================================================\n# Dashboard / Supabase member sync helpers\n",
        replacement=RECONCILE_DELEGATE,
        label="events._reconcile_stale_open_verification_tickets service delegate",
    )
    changed = changed or did

    offenders = [marker for marker in FORBIDDEN_EVENTS_MARKERS if marker in text]
    if offenders:
        print("❌ events.py still contains departed-ticket cleanup ownership markers:")
        for marker in offenders:
            print(" -", marker)
        return 1

    if changed:
        write(EVENTS, text)
        ok("updated stoney_verify/events.py departed-ticket cleanup ownership")
    else:
        ok("events.py departed-ticket cleanup delegates already present")

    py_compile.compile(str(EVENTS), doraise=True)
    py_compile.compile(str(SERVICE), doraise=True)
    ok("compiled events.py and departed_member_cleanup_service.py")

    print("\nNext commands:")
    print("  git diff -- stoney_verify/events.py")
    print("  python tools/apply_departed_ticket_cleanup_handoff.py")
    print("  python -m py_compile stoney_verify/events.py stoney_verify/tickets_new/departed_member_cleanup_service.py")
    print("  git add stoney_verify/events.py")
    print('  git commit -m "Physically hand off departed ticket cleanup"')
    print("  git push origin main")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
