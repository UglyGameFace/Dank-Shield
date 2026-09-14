#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import py_compile
import sys

ROOT = Path(__file__).resolve().parents[1]

FILES = [
    "stoney_verify/commands_ext/public_setup_compact.py",
    "stoney_verify/commands_ext/public_ticket_panel_clean.py",
    "stoney_verify/startup_guards/auto_schema_bootstrap.py",
    "stoney_verify/startup_guards/ticket_category_schema_bootstrap_guard.py",
    "stoney_verify/startup_guards/ticket_category_setup_guard.py",
    "stoney_verify/startup_guards/ticket_form_default_templates_guard.py",
    "stoney_verify/tickets_new/managed_category_service.py",
    "stoney_verify/tickets_new/intake_service.py",
    "stoney_verify/tickets_new/panel.py",
    "supabase/migrations/202607310001_managed_ticket_category_catalog.sql",
    "supabase/migrations/20260802042000_ticket_category_setup_selection.sql",
    "supabase/migrations/20260807215900_prepare_managed_ticket_category_repair.sql",
    "supabase/migrations/20260807220000_repair_managed_ticket_category_duplicates.sql",
    "supabase/migrations/20260911113000_restore_rich_ticket_category_selection.sql",
]

CHECKS = {
    "stoney_verify/commands_ext/public_setup_compact.py": [
        "from stoney_verify.startup_guards import ticket_category_setup_guard as _ticket_category_guard",
    ],
    "stoney_verify/commands_ext/public_ticket_panel_clean.py": [
        "managed_category_service as managed_categories",
        "ensure_category_setup_state(guild_id)",
        "dedupe_category_rows",
        "starter_category_rows",
        "TicketSelectView(rows, member.id, session_id)",
        "reserve_persistent_ticket_number",
        "return await reserve_persistent_ticket_number",
        "_PANEL_INTERACTION_LOCKS",
        "_MENU_SESSIONS",
        "Newest menu wins.",
        "loaded canonical ticket choices",
    ],
    "stoney_verify/startup_guards/ticket_category_schema_bootstrap_guard.py": [
        'MIGRATION_FILE = "20260802042000_ticket_category_setup_selection.sql"',
        'REPAIR_PREP_MIGRATION_FILE = "20260807215900_prepare_managed_ticket_category_repair.sql"',
        'REPAIR_MIGRATION_FILE = "20260807220000_repair_managed_ticket_category_duplicates.sql"',
        'RICH_SELECTION_RECOVERY_MIGRATION_FILE = "20260911113000_restore_rich_ticket_category_selection.sql"',
        "MIGRATION_FILES",
        "Migration execution belongs to the Supabase migration pipeline",
        "return True",
    ],
    "stoney_verify/startup_guards/auto_schema_bootstrap.py": [
        '"20260802042000_ticket_category_setup_selection.sql"',
        '"20260807215900_prepare_managed_ticket_category_repair.sql"',
        '"20260807220000_repair_managed_ticket_category_duplicates.sql"',
        '"20260911113000_restore_rich_ticket_category_selection.sql"',
        "Resolve migration guidance without executing any SQL",
        'SCHEMA_SQL = ""',
    ],
    "stoney_verify/tickets_new/managed_category_service.py": [
        "CATEGORY_SETUP_VERSION = 2",
        "MANAGED_CATALOG_VERSION = 4",
        "SAFE_STARTER_KEYS",
        "CATEGORY_CATALOG",
        "canonical_category_key",
        "_managed_row_shape_matches",
        "_visible_label_key",
        "different internal keys may never render the same visible label",
        "setup/editor inventory deliberately skips this phase",
        "prepare_dank_ticket_category_repair",
        "dedupe_category_rows",
        "_catalog_reconcile_needed",
        "_configured_selected_keys",
        "_saved_selection_reconcile_needed",
        "ticket_category_setup_selected_keys",
        "saved-selection drift",
        "_claim_reconcile_window",
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
        'custom_id="dank_ticket_category_setup:custom_only"',
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
    "supabase/migrations/20260802042000_ticket_category_setup_selection.sql": [
        "ticket_category_setup_required",
        "ticket_category_setup_selected_keys",
        "game-services",
        "require_dank_ticket_category_setup",
        "save_dank_ticket_category_selection",
        "p_reset_to_starter",
        "managed_enabled >= 10",
        "alter column ticket_category_setup_required set default true",
        "Your custom ticket choices were preserved",
        "custom-only selection",
        "managed_row.managed_category_key = any(selected_keys)",
        "managed_row.is_enabled = true",
    ],
    "supabase/migrations/20260807215900_prepare_managed_ticket_category_repair.sql": [
        "prepare_dank_ticket_category_repair",
        "managed_category_key = null",
        "managed_catalog_version = null",
        "dank_ticket_category_key(tc.slug, null)",
        "deletes nothing",
        "service_role",
    ],
    "supabase/migrations/20260807220000_repair_managed_ticket_category_duplicates.sql": [
        "catalog_version integer",
        "false,3)",
        "dank_ticket_category_repair_key",
        "A reserved slug is stronger evidence than a stale stored key",
        "Unknown custom slugs are never adopted from display name",
        "use_saved_selection",
        "cfg_version >= 2 and cfg_required = false",
        "Repair every existing guild immediately",
        "reconcile_dank_ticket_categories(null)",
        "completed v2 selection is never invalidated",
    ],
    "supabase/migrations/20260911113000_restore_rich_ticket_category_selection.sql": [
        "COD Modding Services",
        "Report Staff",
        "recover_dank_ticket_category_selection_from_history",
        "guild_config_versions",
        "ticket_category_setup_selected_keys",
        "reconcile_dank_ticket_categories(null)",
    ],
}

OBSOLETE_FILES = (
    "stoney_verify/startup_guards/public_ticket_panel_clean_hardening.py",
    "stoney_verify/startup_guards/ticket_category_cod_services_guard.py",
    "stoney_verify/startup_guards/ticket_category_game_services_guard.py",
    "supabase/migrations/202608020002_ticket_category_setup_completion_compat.sql",
    "supabase/migrations/202608020003_ticket_category_custom_preservation.sql",
    "supabase/migrations/202608020004_ticket_category_selection_custom_only.sql",
)


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
            print(f"obsolete category/panel owner still exists: {path}", file=sys.stderr)
            return 1

    for path, snippets in CHECKS.items():
        data = (ROOT / path).read_text(encoding="utf-8")
        for snippet in snippets:
            if snippet not in data:
                print(f"{path} missing {snippet}", file=sys.stderr)
                return 1

    compat_text = (
        ROOT / "stoney_verify/startup_guards/ticket_category_schema_bootstrap_guard.py"
    ).read_text(encoding="utf-8")
    if "from . import auto_schema_bootstrap" in compat_text or "bootstrap._BOOTSTRAP_MIGRATION_FILES" in compat_text:
        print("ticket category compatibility manifest still mutates schema bootstrap ownership", file=sys.stderr)
        return 1
    if "\napply()\n" in compat_text:
        print("ticket category compatibility manifest still has an import-time apply side effect", file=sys.stderr)
        return 1

    clean_panel_text = (
        ROOT / "stoney_verify/commands_ext/public_ticket_panel_clean.py"
    ).read_text(encoding="utf-8")
    if 'table("ticket_categories")' in clean_panel_text:
        print(
            "clean ticket panel bypasses canonical managed category service",
            file=sys.stderr,
        )
        return 1

    setup_compact_text = (
        ROOT / "stoney_verify/commands_ext/public_setup_compact.py"
    ).read_text(encoding="utf-8")
    if "ticket_category_setup_guard as _ticket_category_guard" not in setup_compact_text:
        print("ticket category setup has no explicit live setup owner", file=sys.stderr)
        return 1

    print("Ticket category menu audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
