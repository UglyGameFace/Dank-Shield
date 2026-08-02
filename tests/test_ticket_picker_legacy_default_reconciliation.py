from __future__ import annotations

from stoney_verify.commands_ext import public_setup_solid as setup
from stoney_verify.commands_ext import public_tickettool_parity_polish as ticket_menu
from stoney_verify.tickets_new import managed_category_service as categories
from stoney_verify.tickets_new import panel


def _legacy_managed_rows():
    return [
        {
            "slug": "support",
            "name": "Support",
            "description": "General help and support tickets.",
            "intake_type": "support",
            "is_default": True,
            "sort_order": 10,
        },
        {
            "slug": "verification",
            "name": "Verification Help",
            "description": "Help for users stuck during verification.",
            "intake_type": "verification",
            "sort_order": 20,
        },
        {
            "slug": "appeal",
            "name": "Appeal",
            "description": "Appeals for moderation actions.",
            "intake_type": "appeal",
            "sort_order": 30,
        },
        {
            "slug": "report",
            "name": "Report User",
            "description": "Report a member or rule violation.",
            "intake_type": "report",
            "sort_order": 40,
        },
        {
            "slug": "question",
            "name": "Question",
            "description": "General questions.",
            "intake_type": "question",
            "sort_order": 50,
        },
        {
            "slug": "bug",
            "name": "Bug Report",
            "description": "Report a workflow bug.",
            "intake_type": "bug",
            "sort_order": 60,
        },
        {
            "slug": "custom",
            "name": "Other",
            "description": "Anything else.",
            "intake_type": "custom",
            "sort_order": 70,
        },
    ]


def test_legacy_managed_starter_set_is_still_recognized_for_migration() -> None:
    assert ticket_menu._looks_like_legacy_managed_default_rows(_legacy_managed_rows()) is True


def test_legacy_managed_picker_does_not_auto_enable_richer_builtin_categories() -> None:
    rows = ticket_menu._effective_ticket_rows(
        _legacy_managed_rows(),
        panel._DEFAULT_BOOTSTRAP_CATEGORIES,
    )
    keys = [ticket_menu._canonical_category_key(row) for row in rows]

    # The full catalog remains available in setup, but the live member picker
    # must never silently add every built-in option to an existing guild.
    assert "partnership" not in keys
    assert "cod-services" not in keys
    assert "account-access" not in keys
    assert "payments-refunds" not in keys
    assert "staff-complaint" not in keys
    assert "vouch-referral" not in keys
    assert "giveaway-reward" not in keys
    assert "content-media" not in keys
    assert len(keys) == len(set(keys))


def test_setup_catalog_still_contains_every_available_builtin_choice() -> None:
    keys = [ticket_menu._canonical_category_key(row) for row in setup.RECOMMENDED_CATEGORIES]

    assert "partnership" in keys
    assert "cod-services" in keys
    assert "account-access" in keys
    assert "payments-refunds" in keys
    assert "staff-complaint" in keys
    assert "vouch-referral" in keys
    assert "giveaway-reward" in keys
    assert "content-media" in keys


def test_custom_owner_category_set_remains_authoritative() -> None:
    custom_rows = [
        *_legacy_managed_rows(),
        {
            "slug": "vip_concierge",
            "name": "VIP Concierge",
            "description": "Private VIP help.",
            "intake_type": "custom",
            "sort_order": 15,
        },
    ]

    assert ticket_menu._looks_like_legacy_managed_default_rows(custom_rows) is False

    rows = ticket_menu._effective_ticket_rows(
        custom_rows,
        panel._DEFAULT_BOOTSTRAP_CATEGORIES,
    )
    keys = [ticket_menu._canonical_category_key(row) for row in rows]

    assert "vip-concierge" in keys
    assert "partnership" not in keys
    assert "cod-services" not in keys


def test_ticket_select_shows_only_the_legacy_guilds_current_enabled_set() -> None:
    rows = ticket_menu._effective_ticket_rows(
        _legacy_managed_rows(),
        panel._DEFAULT_BOOTSTRAP_CATEGORIES,
    )
    select = ticket_menu.TicketCategorySelect(rows)
    labels = [option.label for option in select.options]

    assert "Partnership" not in labels
    assert "COD Services" not in labels
    assert "Account / Access" not in labels
    assert "Payments / Refunds" not in labels
    assert "Support" in labels


def test_category_manager_requires_explicit_selection_instead_of_seed_all() -> None:
    rows = categories.catalog_category_rows()
    state = categories.CategorySetupState(
        rows=rows,
        active_rows=[row for row in rows if row["category_key"] in {"report", "support"}],
        selected_keys=("report", "support"),
        required=True,
        reason="Confirm which built-in choices this server should show.",
        version=0,
    )

    view = setup.CategoryManagerView(state=state)
    custom_ids = {str(getattr(child, "custom_id", "")) for child in view.children}

    assert "dank_ticket_category_setup:managed_selection" in custom_ids
    assert "stoney_solid:cat_seed" not in custom_ids
