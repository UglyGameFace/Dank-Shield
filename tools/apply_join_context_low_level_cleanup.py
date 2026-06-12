#!/usr/bin/env python3
from __future__ import annotations

"""Remove low-level join-context DB writers from events.py.

This is a small companion cleanup for apply_join_context_service_handoff.py.
After members_new.join_context_service.py owns member_joins/member_events writes,
events.py should no longer define those low-level insert wrappers.
"""

import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / "stoney_verify" / "events.py"
JOIN_CONTEXT_SERVICE = ROOT / "stoney_verify" / "members_new" / "join_context_service.py"

START = "def _member_joins_insert_sync(sb: Any, payload: Dict[str, Any]):\n"
END = "def _guild_members_select_guild_rows_sync(sb: Any, guild_id: str):\n"
FORBIDDEN = (
    'sb.table("member_joins").insert(payload).execute()',
    'sb.table("member_events").insert(payload).execute()',
)
REQUIRED_SERVICE = (
    "def _member_joins_insert_sync",
    "def _member_events_insert_sync",
    "async def persist_member_join_context",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def main() -> int:
    if not EVENTS.exists():
        print(f"❌ missing {EVENTS}")
        return 1
    if not JOIN_CONTEXT_SERVICE.exists():
        print(f"❌ missing {JOIN_CONTEXT_SERVICE}")
        return 1

    service = read(JOIN_CONTEXT_SERVICE)
    missing = [marker for marker in REQUIRED_SERVICE if marker not in service]
    if missing:
        print("❌ join_context_service is not ready for low-level writer cleanup:")
        for marker in missing:
            print(" -", marker)
        return 1

    text = read(EVENTS)
    start = text.find(START)
    if start >= 0:
        end = text.find(END, start)
        if end < 0:
            print(f"❌ could not find cleanup end marker: {END!r}")
            return 1
        text = text[:start] + text[end:]
        write(EVENTS, text)
        print("✅ removed events.py low-level join-context DB writers")
    else:
        print("✅ events.py low-level join-context DB writers already removed")

    text = read(EVENTS)
    offenders = [marker for marker in FORBIDDEN if marker in text]
    if offenders:
        print("❌ events.py still contains low-level join-context DB writer markers:")
        for marker in offenders:
            print(" -", marker)
        return 1

    py_compile.compile(str(EVENTS), doraise=True)
    py_compile.compile(str(JOIN_CONTEXT_SERVICE), doraise=True)
    print("✅ compiled events.py and join_context_service.py")
    print("\nNext commands:")
    print("  python tools/apply_join_context_service_handoff.py")
    print("  git diff -- stoney_verify/events.py")
    print("  python -m py_compile stoney_verify/events.py stoney_verify/members_new/join_context_service.py")
    print("  git add stoney_verify/events.py")
    print('  git commit -m "Physically hand off join context events"')
    print("  git push origin main")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
