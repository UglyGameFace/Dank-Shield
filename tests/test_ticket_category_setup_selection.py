from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from stoney_verify.commands_ext import public_setup_recommend as recommend
from stoney_verify.commands_ext import public_setup_solid as solid
from stoney_verify.commands_ext import public_ticket_panel_clean as clean_panel
from stoney_verify.startup_guards import _STARTUP_GUARDS
from stoney_verify.startup_guards import auto_schema_bootstrap
from stoney_verify.startup_guards import ticket_category_schema_bootstrap_guard as schema_guard
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
    keys = _keys(categories.dedupe_category_rows(rows, enabled_only=True))
    assert "support" in keys
    assert "custom:clan-application" in keys
    assert "custom:creator-portfolio" in keys
    assert len(keys) == 3


def test_current_catalog_stays_on_read_only_path() -> None:
    rows = categories.catalog_category_rows()
    assert categories._catalog_reconcile_needed(rows) is False
    assert categories._catalog_reconcile_needed(rows[:-1]) is True
    legacy_alias = dict(rows[0])
    legacy_alias["managed_by_dank"] = False
    assert categories._catalog_reconcile_needed([legacy_alias, *rows[1:]]) is True


def test_reconcile_window_debounces_repeated_menu_opens() -> None:
    categories._RECONCILE_NOT_BEFORE.clear()
    assert categories._claim_reconcile_window(1234, now=100.0) is True
    assert categories._claim_reconcile_window(1234, now=101.0) is False
    assert categories._claim_reconcile_window(1234, now=401.0) is True
    categories._RECONCILE_NOT_BEFORE.clear()


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


def test_guided_ticket_setup_routes_required_selection_into_original_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guild = SimpleNamespace(id=777, me=SimpleNamespace(guild_permissions=SimpleNamespace()))
    cfg = {"setup_choice": "help_desk"}

    async def get_config(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return cfg

    async def required_categories(*args: Any, **kwargs: Any) -> solid.CategoryLoad:
        return solid.CategoryLoad([], "Ticket Menu Setup Required")

    monkeypatch.setattr(recommend, "get_guild_config", get_config)
    monkeypatch.setattr(
        recommend,
        "_selected_setup_services",
        lambda _cfg: {
            "tickets": True,
            "verify": False,
            "basic_verify": False,
            "voice": False,
            "id": False,
            "spam_guard": False,
            "logs": False,
        },
    )
    monkeypatch.setattr(recommend, "_missing_setup_permissions", lambda *args, **kwargs: [])
    monkeypatch.setattr(recommend, "_has_role", lambda *args, **kwargs: True)
    monkeypatch.setattr(recommend, "_has_channel", lambda *args, **kwargs: True)
    monkeypatch.setattr(recommend.solid, "_category_load", required_categories)

    target = asyncio.run(recommend._guided_setup_target(guild))

    assert target == (
        "ticket_choices",
        "Create Ticket Choices",
        "Choose what members can request when they open a ticket.",
        "ticket_choices",
    )


def test_guided_ticket_choice_step_opens_the_category_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    async def open_ticket_menu(*args: Any, **kwargs: Any) -> None:
        events.append("ticket_menu")

    monkeypatch.setattr(recommend, "_open_ticket_menu", open_ticket_menu)

    asyncio.run(
        recommend._open_guided_target(
            SimpleNamespace(),
            "ticket_choices",
            "ticket_choices",
        )
    )

    assert events == ["ticket_menu"]


def test_category_migration_is_registered_for_direct_dsn_startup() -> None:
    assert schema_guard.MIGRATION_FILE in auto_schema_bootstrap._BOOTSTRAP_MIGRATION_FILES
    assert "stoney_verify.startup_guards.ticket_category_schema_bootstrap_guard" in _STARTUP_GUARDS
    assert _STARTUP_GUARDS.index("stoney_verify.startup_guards.auto_schema_bootstrap") < _STARTUP_GUARDS.index(
        "stoney_verify.startup_guards.ticket_category_schema_bootstrap_guard"
    )


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
