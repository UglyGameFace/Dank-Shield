#!/usr/bin/env python3
from __future__ import annotations

"""Remove dead events.py leftovers after physical service handoffs.

This script removes helper/import blocks when their identifiers are no longer
referenced outside the removable block. It is conservative but allows partial
cleanup: if one block is still referenced, that block is skipped instead of
blocking unrelated cleanup.
"""

import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVENTS = ROOT / "stoney_verify" / "events.py"

TICKET_IMPORT_START = "try:\n    from .tickets_new.service import (\n"
TICKET_IMPORT_END = "try:\n    from .channel_cleanup import ensure_channel_cleanup_worker_started\n"

RISK_HELPER_START = "def _as_float(v: Any, default: float = 0.0) -> float:\n"
RISK_HELPER_END = "def _startup_task_running(attr_name: str) -> bool:\n"

TICKET_IMPORT_NAMES = (
    "find_open_ticket_for_owner",
    "tickets_mark_ticket_closed",
    "tickets_mark_ticket_deleted",
)

RISK_HELPER_NAMES = (
    "_as_float",
    "_safe_string_list",
    "_safe_json_object_list",
    "_derive_alt_cluster_key_from_profile",
    "_derive_suspicion_flags_from_profile",
    "_sync_iso_now",
    "_build_risk_payload_from_profile",
    "_extract_existing_risk_payload",
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def count_outside_block(text: str, start: int, end: int, name: str) -> int:
    outside = text[:start] + text[end:]
    return outside.count(name)


def remove_block_if_unused(
    text: str,
    *,
    start_marker: str,
    end_marker: str,
    names: tuple[str, ...],
    label: str,
    skip_if_still_used: bool = True,
) -> tuple[str, bool, bool]:
    start = text.find(start_marker)
    if start < 0:
        print(f"✅ {label} already removed")
        return text, False, False

    end = text.find(end_marker, start)
    if end < 0:
        print(f"❌ {label}: could not find end marker {end_marker!r}")
        raise SystemExit(1)

    leaked = []
    for name in names:
        if count_outside_block(text, start, end, name) > 0:
            leaked.append(name)

    if leaked:
        print(f"⚠️ skipped {label}; still referenced outside removable block:")
        for name in leaked:
            print(" -", name)
        if skip_if_still_used:
            return text, False, True
        raise SystemExit(1)

    print(f"✅ removed {label}")
    return text[:start] + text[end:], True, False


def main() -> int:
    if not EVENTS.exists():
        print(f"❌ missing {EVENTS}")
        return 1

    text = read(EVENTS)
    changed = False
    skipped = False

    text, did, was_skipped = remove_block_if_unused(
        text,
        start_marker=TICKET_IMPORT_START,
        end_marker=TICKET_IMPORT_END,
        names=TICKET_IMPORT_NAMES,
        label="dead ticket cleanup imports",
    )
    changed = changed or did
    skipped = skipped or was_skipped

    text, did, was_skipped = remove_block_if_unused(
        text,
        start_marker=RISK_HELPER_START,
        end_marker=RISK_HELPER_END,
        names=RISK_HELPER_NAMES,
        label="dead risk payload helper block",
    )
    changed = changed or did
    skipped = skipped or was_skipped

    if changed:
        write(EVENTS, text)
        print("✅ updated stoney_verify/events.py")
    elif skipped:
        print("✅ no safe removable blocks left; skipped still-used blocks")
    else:
        print("✅ no dead extraction leftovers found")

    py_compile.compile(str(EVENTS), doraise=True)
    print("✅ compiled stoney_verify/events.py")
    print("\nNext commands:")
    print("  git diff -- stoney_verify/events.py")
    print("  python tools/cleanup_events_extraction_leftovers.py")
    print("  python -m py_compile stoney_verify/events.py")
    print("  git add stoney_verify/events.py")
    print('  git commit -m "Remove dead event extraction leftovers"')
    print("  git push origin main")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
