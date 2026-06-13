#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import py_compile
import sys

ROOT = Path(__file__).resolve().parents[1]

FILES = [
    "stoney_verify/startup_guards/setup_check_existing_server_inference_guard.py",
    "stoney_verify/startup_guards/ticket_panel_doctor_production_wording.py",
    "stoney_verify/commands_ext/public_setup_recommend.py",
]

CHECKS = {
    "stoney_verify/startup_guards/setup_check_existing_server_inference_guard.py": [
        "_infer_choice",
        "Help desk (inferred from saved setup)",
        "Health Check can infer Existing Server",
        "Choose a setup type first, or finish Ticket Basics",
        "_SETUP_CHECK_EXISTING_SERVER_INFERENCE_GUARD",
    ],
    "stoney_verify/startup_guards/ticket_panel_doctor_production_wording.py": [
        "setup_check_existing_server_inference_guard",
        "setup-check inference guard",
    ],
    "stoney_verify/commands_ext/public_setup_recommend.py": [
        "Choose a setup type first. Press **Choose Setup Type**.",
        "Setup Check",
        "Ticket menu has",
    ],
}


def main() -> int:
    for path in FILES:
        target = ROOT / path
        if not target.exists():
            print(f"missing {path}", file=sys.stderr)
            return 1
        try:
            py_compile.compile(str(target), doraise=True)
        except py_compile.PyCompileError as exc:
            print(f"compile failed {path}: {exc}", file=sys.stderr)
            return 1

    for path, snippets in CHECKS.items():
        text = (ROOT / path).read_text(encoding="utf-8")
        for snippet in snippets:
            if snippet not in text:
                print(f"{path} missing {snippet}", file=sys.stderr)
                return 1

    print("Setup check inference audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
