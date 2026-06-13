#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import py_compile
import sys

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "stoney_verify/workers/bot_command_worker.py"

SNIPPETS = [
    'action == "create_ticket"',
    "member_snapshot",
    "dashboard_context",
    "category_slug",
    "matched_category_slug",
    "intake_type",
    "parent_category_id",
    "staff_role_ids",
    "get_open_ticket_for_user",
    "duplicate",
    "create_ticket_channel",
    "sync_one_ticket_channel",
    "bot_command_create_ticket_backfill",
    "dashboard_create_ticket",
    "worker_source",
    "member_message",
]


def main() -> int:
    if not WORKER.exists():
        print("missing stoney_verify/workers/bot_command_worker.py", file=sys.stderr)
        return 1

    try:
        py_compile.compile(str(WORKER), doraise=True)
    except py_compile.PyCompileError as exc:
        print(f"compile failed stoney_verify/workers/bot_command_worker.py: {exc}", file=sys.stderr)
        return 1

    text = WORKER.read_text(encoding="utf-8")
    for snippet in SNIPPETS:
        if snippet not in text:
            print(f"bot command create_ticket worker missing {snippet}", file=sys.stderr)
            return 1

    print("Bot command create_ticket worker audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
