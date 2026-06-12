#!/usr/bin/env python3
from __future__ import annotations

"""Physically replace legacy events.py service-owned bodies.

This is intentionally small and marker-based because stoney_verify/events.py is
large. Runtime ownership is already routed by startup_guards/event_safety.py;
this script removes the duplicate legacy bodies from events.py when run from a
real checkout.
"""

import py_compile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / "stoney_verify" / "events.py"
AUDIT_ROLE_TRUTH = ROOT / "tools" / "audit_role_truth.py"
AUDIT_EVENT_BOUNDARY = ROOT / "tools" / "audit_event_boundary.py"


def die(message: str) -> None:
    print(f"❌ {message}")
    raise SystemExit(1)


def ok(message: str) -> None:
    print(f"✅ {message}")


def replace_function_block(text: str, *, start_marker: str, end_marker: str, replacement: str, label: str) -> tuple[str, bool]:
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


def main() -> int:
    if not EVENTS.exists():
        die(f"Missing {EVENTS}")

    text = EVENTS.read_text(encoding="utf-8")
    original = text

    member_snapshot_replacement = '''def _member_role_snapshot(member: discord.Member) -> Dict[str, Any]:
    return role_truth.build_member_role_snapshot(member)


'''
    text, changed_snapshot = replace_function_block(
        text,
        start_marker="def _member_role_snapshot(member: discord.Member) -> Dict[str, Any]:",
        end_marker="def _minimal_member_payload(",
        replacement=member_snapshot_replacement,
        label="events._member_role_snapshot role_truth handoff",
    )

    fail_closed_replacement = '''async def _handle_join_verification_failure(member: discord.Member, reason: str) -> None:
    from .members_new.join_removal_safety import handle_join_verification_failure

    await handle_join_verification_failure(member, reason)


'''
    text, changed_fail_closed = replace_function_block(
        text,
        start_marker="async def _handle_join_verification_failure(member: discord.Member, reason: str) -> None:",
        end_marker="async def _ensure_unverified_on_join(member: discord.Member) -> bool:",
        replacement=fail_closed_replacement,
        label="events._handle_join_verification_failure native safety handoff",
    )

    if text == original:
        ok("No events.py changes needed")
    else:
        EVENTS.write_text(text, encoding="utf-8")
        ok(f"Updated {EVENTS.relative_to(ROOT)}")

    py_compile.compile(str(EVENTS), doraise=True)
    ok("Compiled stoney_verify/events.py")

    for audit in (AUDIT_ROLE_TRUTH, AUDIT_EVENT_BOUNDARY):
        if audit.exists():
            py_compile.compile(str(audit), doraise=True)
            ok(f"Compiled {audit.relative_to(ROOT)}")

    print("\nChanged blocks:")
    print(f" - member snapshot handoff: {changed_snapshot}")
    print(f" - fail-closed handler handoff: {changed_fail_closed}")
    print("\nNext commands:")
    print("  git diff -- stoney_verify/events.py")
    print("  python tools/audit_role_truth.py")
    print("  python tools/audit_event_boundary.py")
    print("  git add stoney_verify/events.py")
    print('  git commit -m "Physically hand off event service logic"')
    print("  git push")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
