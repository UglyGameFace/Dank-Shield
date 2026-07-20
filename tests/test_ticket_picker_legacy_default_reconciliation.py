from __future__ import annotations

from stoney_verify.commands_ext import public_setup_solid as setup
from stoney_verify.commands_ext import public_tickettool_parity_polish as ticket_menu
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


def test_legacy_managed_starter_set_is_recognized() -> None:
    assert ticket_menu._looks_like_legacy_managed_default_rows(_legacy_managed_rows()) is True


def test_legacy_managed_picker_regains_richer_builtin_categories() -> None:
    rows = ticket_menu._effective_ticket_rows(
        _legacy_managed_rows(),
        panel._DEFAULT_BOOTSTRAP_CATEGORIES,
    )
    keys = [ticket_menu._canonical_category_key(row) for row in rows]

    assert "partnership" in keys
    assert "cod-services" in keys
    assert "account-access" in keys
    assert "payments-refunds" in keys
    assert "staff-complaint" in keys
    assert "vouch-referral" in keys
    assert "giveaway-reward" in keys
    assert "content-media" in keys
    assert len(keys) == len(set(keys))


def test_setup_recommended_categories_share_the_rich_ticket_catalog() -> None:
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


def test_ticket_select_exposes_partnership_for_legacy_managed_set() -> None:
    rows = ticket_menu._effective_ticket_rows(
        _legacy_managed_rows(),
        panel._DEFAULT_BOOTSTRAP_CATEGORIES,
    )
    select = ticket_menu.TicketCategorySelect(rows)
    labels = [option.label for option in select.options]

    assert "Partnership" in labels
    assert "COD Services" in labels
    assert "Account / Access" in labels
    assert "Payments / Refunds" in labels


def test_category_manager_button_uses_canonical_completeness() -> None:
    # Simulate the database after recommended seeding starts from the legacy
    # managed set. Canonical duplicates such as verification/verification_issue
    # and bug/technical_support are intentionally not inserted twice.
    rows = [dict(row) for row in _legacy_managed_rows()]
    existing_keys = {ticket_menu._canonical_category_key(row) for row in rows}

    for item in setup.RECOMMENDED_CATEGORIES:
        key = ticket_menu._canonical_category_key(item)
        if key in existing_keys:
            continue
        rows.append(dict(item))
        existing_keys.add(key)

    view = setup.CategoryManagerView(rows=rows)
    seed_button = next(
        child
        for child in view.children
        if getattr(child, "custom_id", "") == "stoney_solid:cat_seed"
    )

    assert seed_button.label == "Check Recommended Options"
