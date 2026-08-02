from __future__ import annotations

from stoney_verify.commands_ext import public_setup_solid as solid
from stoney_verify.commands_ext import public_ticket_panel_clean as clean_panel
from stoney_verify.startup_guards import _STARTUP_GUARDS
from stoney_verify.startup_guards import ticket_category_setup_guard as setup_guard
from stoney_verify.startup_guards import ticket_form_default_templates_guard as forms
from stoney_verify.tickets_new import managed_category_service as categories


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


def test_custom_only_selection_is_exposed_when_custom_rows_exist() -> None:
    custom = {
        "id": "custom-1",
        "slug": "clan_application",
        "name": "Clan Application",
        "is_enabled": True,
        "is_default": True,
        "managed_by_dank": False,
    }
    state = categories.CategorySetupState(
        rows=[custom],
        active_rows=[custom],
        selected_keys=(),
        required=True,
        reason="Confirm choices.",
        version=0,
    )

    view = setup_guard.CategorySetupManagerView(state=state)
    custom_ids = {str(getattr(child, "custom_id", "")) for child in view.children}

    assert "dank_ticket_category_setup:custom_only" in custom_ids
    assert categories._normalize_selected_keys((), allow_empty=True) == ()


def test_managed_multi_select_defaults_match_saved_selection() -> None:
    rows = categories.catalog_category_rows()
    state = categories.CategorySetupState(
        rows=rows,
        active_rows=[row for row in rows if row["category_key"] in {"report", "support"}],
        selected_keys=("report", "support"),
        required=False,
        reason="",
        version=categories.CATEGORY_SETUP_VERSION,
    )

    select = setup_guard.ManagedCategorySelection(state)
    defaults = {option.value for option in select.options if option.default}

    assert defaults == {"report", "support"}
    assert select.min_values == 1
    assert select.max_values == len(categories.CATEGORY_CATALOG)


def test_cod_and_game_services_keep_distinct_native_forms() -> None:
    assert forms._template_key({"managed_category_key": "cod-services"}) == "cod"
    assert forms._template_key({"managed_category_key": "game-services"}) == "game_services"
    assert forms.DEFAULT_TEMPLATES["cod"][0]["label"] == "Which COD game?"
    assert forms.DEFAULT_TEMPLATES["game_services"][0]["label"] == "Which game is this for?"


def test_single_runtime_owner_is_installed_on_every_picker_path() -> None:
    assert "stoney_verify.startup_guards.ticket_category_setup_guard" in _STARTUP_GUARDS
    assert "stoney_verify.startup_guards.ticket_category_cod_services_guard" not in _STARTUP_GUARDS
    assert "stoney_verify.startup_guards.ticket_category_game_services_guard" not in _STARTUP_GUARDS
    assert clean_panel._load_rows is setup_guard._clean_panel_load_rows
    assert solid._category_load is setup_guard._setup_category_load
    assert solid._build_category_manager_payload is setup_guard._build_category_manager_payload
