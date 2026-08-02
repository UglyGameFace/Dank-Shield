#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import py_compile
import sys

ROOT = Path(__file__).resolve().parents[1]

FILES = [
    "stoney_verify/commands_ext/public_ticket_panel_clean.py",
    "stoney_verify/startup_guards/public_ticket_panel_clean_hardening.py",
    "stoney_verify/startup_guards/ticket_category_setup_guard.py",
    "stoney_verify/startup_guards/ticket_form_default_templates_guard.py",
    "stoney_verify/startup_guards/__init__.py",
    "stoney_verify/tickets_new/managed_category_service.py",
    "stoney_verify/tickets_new/intake_service.py",
    "stoney_verify/tickets_new/panel.py",
    "supabase/migrations/202607310001_managed_ticket_category_catalog.sql",
    "supabase/migrations/202608020001_ticket_category_setup_selection.sql",
    "supabase/migrations/202608020002_ticket_category_setup_completion_compat.sql",
    "supabase/migrations/202608020003_ticket_category_custom_preservation.sql",
    "supabase/migrations/202608020004_ticket_category_selection_custom_only.sql",
]

CHECKS = {
    "stoney_verify/commands_ext/public_ticket_panel_clean.py": [
        'sb.table("ticket_categories").select("*")',
        "TicketSelectView(rows)",
        "reserve_persistent_ticket_number",
        "return await reserve_persistent_ticket_number",
    ],
    "stoney_verify/startup_guards/public_ticket_panel_clean_hardening.py": [
        "_INTERACTION_TTL_SECONDS",
        "_interaction_key",
        "_handle_once",
        "_remove_redundant_fallback",
        "persistent view owns Create Ticket",
        "single-interaction owner active",
        "ticket allocator remain untouched",
    ],
    "stoney_verify/tickets_new/managed_category_service.py": [
        "CATEGORY_SETUP_VERSION = 2",
        "SAFE_STARTER_KEYS",
        "CATEGORY_CATALOG",
        "canonical_category_key",
        "dedupe_category_rows",
        "ensure_category_setup_state_sync",
        "save_category_selection_sync",
        "allow_empty",
        "_set_custom_default_fallback_sync",
        "require_dank_ticket_category_setup",
    ],
    "stoney_verify/startup_guards/ticket_category_setup_guard.py": [
        "Single owner for ticket category catalog",
        "ManagedCategorySelection",
        "Choose every built-in ticket option this server should show",
        "Use Custom Choices Only",
        "_setup_category_load",
        "_seed_catalog_without_enabling_everything",
        "_clean_panel_load_rows",
        "_install_live_loaders",
        "_install_setup_owner",
        "custom-only support",
    ],
    "stoney_verify/startup_guards/ticket_form_default_templates_guard.py": [
        '"cod-services": "cod"',
        '"game-services": "game_services"',
        '"game_services": [',
        "Which game is this for?",
    ],
    "supabase/migrations/202608020001_ticket_category_setup_selection.sql": [
        "ticket_category_setup_required",
        "ticket_category_setup_selected_keys",
        "game-services",
        "require_dank_ticket_category_setup",
        "save_dank_ticket_category_selection",
        "p_reset_to_starter",
        "managed_enabled >= 10",
        "alter column ticket_category_setup_required set default true",
    ],
    "supabase/migrations/202608020003_ticket_category_custom_preservation.sql": [
        "Your custom ticket choices were preserved",
        "set is_enabled = false",
        "managed_by_dank = false",
    ],
    "supabase/migrations/202608020004_ticket_category_selection_custom_only.sql": [
        "custom choices only",
        "custom_row.is_default = true",
        "managed_row.managed_category_key = any(selected_keys)",
        "managed_row.is_enabled = true",
    ],
    "stoney_verify/startup_guards/__init__.py": [
        "public_ticket_panel_clean_hardening",
        "ticket_category_setup_guard",
    ],
}

OBSOLETE_FILES = (
    "stoney_verify/startup_guards/ticket_category_cod_services_guard.py",
    "stoney_verify/startup_guards/ticket_category_game_services_guard.py",
)

FORBIDDEN_STARTUP_GUARDS = (
    "ticket_category_cod_services_guard",
    "ticket_category_game_services_guard",
)

FORBIDDEN_HARDENING_OVERRIDES = (
    "panel_mod._next_number =",
    "panel_mod._rows =",
    "panel_mod._load_rows =",
    "panel_mod._ticket_num =",
    "_MENU_SESSION_SECONDS",
    "You already have a ticket type menu open",
    "_clean_public_rows",
)

ORDERED_STARTUP_SNIPPETS = [
    "public_ticket_panel_clean_hardening",
    "ticket_category_setup_guard",
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

    for path in OBSOLETE_FILES:
        if (ROOT / path).exists():
            print(f"obsolete category owner still exists: {path}", file=sys.stderr)
            return 1

    for path, snippets in CHECKS.items():
        text = (ROOT / path).read_text(encoding="utf-8")
        for snippet in snippets:
            if snippet not in text:
                print(f"{path} missing {snippet}", file=sys.stderr)
                return 1

    hardening_text = (
        ROOT / "stoney_verify/startup_guards/public_ticket_panel_clean_hardening.py"
    ).read_text(encoding="utf-8")
    for snippet in FORBIDDEN_HARDENING_OVERRIDES:
        if snippet in hardening_text:
            print(
                "public_ticket_panel_clean_hardening.py restored stale ownership: "
                f"{snippet}",
                file=sys.stderr,
            )
            return 1

    startup_text = (ROOT / "stoney_verify/startup_guards/__init__.py").read_text(encoding="utf-8")
    for snippet in FORBIDDEN_STARTUP_GUARDS:
        if snippet in startup_text:
            print(f"obsolete category guard still loaded: {snippet}", file=sys.stderr)
            return 1

    positions = [startup_text.find(snippet) for snippet in ORDERED_STARTUP_SNIPPETS]
    if any(pos < 0 for pos in positions) or positions != sorted(positions):
        print("startup guard order is wrong for ticket category setup", file=sys.stderr)
        return 1

    print("Ticket category menu audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
