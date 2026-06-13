#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import py_compile
import sys

ROOT = Path(__file__).resolve().parents[1]

FILES = [
    "stoney_verify/startup_guards/public_ticket_panel_clean_hardening.py",
    "stoney_verify/startup_guards/ticket_category_cod_services_guard.py",
    "stoney_verify/startup_guards/ticket_form_default_templates_guard.py",
    "stoney_verify/startup_guards/external_ticket_history_sequence_guard.py",
    "stoney_verify/startup_guards/__init__.py",
    "stoney_verify/commands_ext/ticket_category_admin.py",
    "stoney_verify/tickets_new/intake_service.py",
]

CHECKS = {
    "stoney_verify/startup_guards/public_ticket_panel_clean_hardening.py": [
        "cod_services",
        "Call of Duty Services",
        "picker dedupe",
        "_MENU_SESSION_SECONDS",
        "You already have a ticket type menu open",
        "_clean_public_rows",
    ],
    "stoney_verify/startup_guards/ticket_category_cod_services_guard.py": [
        "cod_services",
        "Call of Duty Services",
        "_install_setup_category",
        "_install_category_admin_type",
        "_install_intake_service_type",
        "Which COD game?",
        "Platform / console",
    ],
    "stoney_verify/startup_guards/ticket_form_default_templates_guard.py": [
        "custom COD/modded-lobby categories are recognized",
        '"cod"',
        "Which COD game?",
        "Platform / console",
    ],
    "stoney_verify/startup_guards/external_ticket_history_sequence_guard.py": [
        "ignored external/imported ticket history",
        "ticket_counters",
        "_trusted_db_highest",
        "_next_number",
    ],
    "stoney_verify/startup_guards/__init__.py": [
        "public_ticket_panel_clean_hardening",
        "ticket_category_cod_services_guard",
        "external_ticket_history_sequence_guard",
    ],
    "stoney_verify/commands_ext/ticket_category_admin.py": [
        "_ALLOWED_INTAKE_TYPES",
        "_governance_warnings",
        "_duplicate_slugs",
        "_verification_like_categories",
    ],
    "stoney_verify/tickets_new/intake_service.py": [
        "_reason_has_cod_legacy_signals",
        "_default_questions_for_intake_type",
        "score_reason_against_category",
    ],
}

ORDERED_STARTUP_SNIPPETS = [
    "public_ticket_panel_clean_hardening",
    "ticket_category_cod_services_guard",
    "external_ticket_history_sequence_guard",
]


def main() -> int:
    for path in FILES:
        target = ROOT / path
        if not target.exists():
            print(f"missing {path}", file=sys.stderr)
            return 1
        if path.endswith(".py"):
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

    startup_text = (ROOT / "stoney_verify/startup_guards/__init__.py").read_text(encoding="utf-8")
    positions = [startup_text.find(snippet) for snippet in ORDERED_STARTUP_SNIPPETS]
    if any(pos < 0 for pos in positions) or positions != sorted(positions):
        print("startup guard order is wrong for ticket category hardening", file=sys.stderr)
        return 1

    print("Ticket category menu audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
