from __future__ import annotations

from pathlib import Path

from stoney_verify.startup_guards import ticket_form_default_templates_guard as forms
from stoney_verify.tickets_new import managed_category_service as categories


ROOT = Path(__file__).resolve().parents[1]
STARTUP = ROOT / "stoney_verify" / "startup_guards" / "__init__.py"
GUARD = ROOT / "stoney_verify" / "startup_guards" / "ticket_category_setup_guard.py"
MIGRATION = ROOT / "supabase" / "migrations" / "202608020001_ticket_category_setup_selection.sql"
CUSTOM_MIGRATION = ROOT / "supabase" / "migrations" / "202608020003_ticket_category_custom_preservation.sql"


def _keys(rows):
    return [categories.canonical_category_key(row) for row in rows]


def test_exact_alias_duplicates_collapse_without_cross_category_collisions() -> None:
    rows = [
        {"slug": "support", "name": "Support", "sort_order": 999, "is_enabled": True},
        {"slug": "general-support", "name": "Support", "sort_order": 998, "is_enabled": True},
        {"slug": "report", "name": "Report a Member", "sort_order": 50, "is_enabled": True},
        {"slug": "user-report", "name": "Report a Member", "sort_order": 51, "is_enabled": True},
        {"slug": "service_request", "name": "Service Requests", "sort_order": 100, "is_enabled": True},
        {"slug": "staff_complaint", "name": "Staff Complaint", "sort_order": 60, "is_enabled": True},
    ]

    deduped = categories.dedupe_category_rows(rows, enabled_only=True)
    keys = _keys(deduped)

    assert keys.count("support") == 1
    assert keys.count("report") == 1
    assert keys.count("service-request") == 1
    assert keys.count("staff-complaint") == 1
    assert len(keys) == 4


def test_disabled_catalog_rows_never_reach_member_visible_results() -> None:
    rows = categories.catalog_category_rows()
    visible = categories.dedupe_category_rows(rows, enabled_only=True)

    assert set(_keys(visible)) == set(categories.SAFE_STARTER_KEYS)
    assert "cod-services" not in _keys(visible)
    assert "game-services" not in _keys(visible)


def test_safe_starter_is_small_and_has_one_default() -> None:
    rows = categories.starter_category_rows()

    assert set(_keys(rows)) == {"report", "appeal", "support"}
    defaults = [row for row in rows if row.get("is_default")]
    assert len(defaults) == 1
    assert categories.canonical_category_key(defaults[0]) == "support"


def test_old_everything_enabled_shape_requires_reset() -> None:
    rows = categories.catalog_category_rows()
    for row in rows:
        row["is_enabled"] = True

    reason, reset = categories._shape_problem(rows, {})

    assert "old setup" in reason.lower()
    assert reset is True


def test_smaller_managed_only_shape_requires_confirmation_without_reset() -> None:
    rows = categories.catalog_category_rows()[:5]
    for row in rows:
        row["is_enabled"] = True

    reason, reset = categories._shape_problem(rows, {})

    assert "confirmation" in reason.lower()
    assert reset is False


def test_unknown_custom_rows_are_preserved_as_distinct_choices() -> None:
    rows = [
        {"slug": "support", "name": "Support", "is_enabled": True},
        {"slug": "clan_application", "name": "Clan Application", "is_enabled": True},
        {"slug": "creator_portfolio", "name": "Creator Portfolio", "is_enabled": True},
    ]

    deduped = categories.dedupe_category_rows(rows, enabled_only=True)
    keys = _keys(deduped)

    assert "support" in keys
    assert "custom:clan-application" in keys
    assert "custom:creator-portfolio" in keys
    assert len(keys) == 3


def test_cod_and_game_services_keep_distinct_native_forms() -> None:
    assert forms._template_key({"managed_category_key": "cod-services"}) == "cod"
    assert forms._template_key({"managed_category_key": "game-services"}) == "game_services"
    assert forms.DEFAULT_TEMPLATES["cod"][0]["label"] == "Which COD game?"
    assert forms.DEFAULT_TEMPLATES["game_services"][0]["label"] == "Which game is this for?"


def test_single_startup_owner_replaces_old_category_patch_stack() -> None:
    startup = STARTUP.read_text(encoding="utf-8")
    guard = GUARD.read_text(encoding="utf-8")

    assert "ticket_category_setup_guard" in startup
    assert "ticket_category_cod_services_guard" not in startup
    assert "ticket_category_game_services_guard" not in startup
    assert "ManagedCategorySelection" in guard
    assert "_seed_catalog_without_enabling_everything" in guard
    assert "clean._load_rows = _clean_panel_load_rows" in guard
    assert "solid._category_load = _setup_category_load" in guard


def test_migration_forces_bad_existing_setups_and_preserves_explicit_selection() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    custom_sql = CUSTOM_MIGRATION.read_text(encoding="utf-8")

    assert "managed_enabled >= 10" in sql
    assert "p_reset_to_starter" in sql
    assert "ticket_category_setup_required = true" in sql
    assert "ticket_category_setup_required = false" in sql
    assert "save_dank_ticket_category_selection" in sql
    assert "is_enabled = managed_category_key = any(selected_keys)" in sql
    assert "managed_by_dank = false" in sql
    assert "Your custom ticket choices were preserved" in custom_sql
    assert "set is_enabled = false" in custom_sql
