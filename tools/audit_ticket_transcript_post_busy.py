#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import py_compile
import sys

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "stoney_verify/startup_guards/ticket_transcript_post_busy_guard.py"
LOADER = ROOT / "stoney_verify/startup_guards/ticket_panel_doctor_production_wording.py"

TARGET_SNIPPETS = [
    "sv:ticket:transcript",
    "_TRANSCRIPT_POST_LOCKS",
    "_ticket_has_transcript",
    "_safe_defer_ephemeral",
    "StaffClosedTicketView",
    "_TICKET_TRANSCRIPT_POST_BUSY_GUARD_APPLIED",
]
LOADER_SNIPPETS = [
    "ticket_transcript_post_busy_guard",
    "ticket transcript post busy guard",
]


def main() -> int:
    for path in (TARGET, LOADER):
        if not path.exists():
            print(f"missing {path.relative_to(ROOT)}", file=sys.stderr)
            return 1
        try:
            py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            print(f"compile failed {path.relative_to(ROOT)}: {exc}", file=sys.stderr)
            return 1

    text = TARGET.read_text(encoding="utf-8")
    for snippet in TARGET_SNIPPETS:
        if snippet not in text:
            print(f"transcript busy audit missing {snippet}", file=sys.stderr)
            return 1

    loader_text = LOADER.read_text(encoding="utf-8")
    for snippet in LOADER_SNIPPETS:
        if snippet not in loader_text:
            print(f"loader missing {snippet}", file=sys.stderr)
            return 1

    print("Ticket transcript post busy audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
