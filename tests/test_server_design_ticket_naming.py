from stoney_verify.services.server_design_ticket_naming import (
    build_ticket_channel_name,
    plain_ticket_channel_name,
)


def test_plain_ticket_name_without_design_options():
    assert plain_ticket_channel_name(219) == "ticket-0219"
    assert plain_ticket_channel_name(219, closed=True) == "closed-0219"
    assert build_ticket_channel_name(219, options=None) == "ticket-0219"


def test_ticket_created_name_uses_dank_design_options():
    name = build_ticket_channel_name(
        219,
        options={
            "theme_id": "gothic_clean",
            "strength": 5,
            "font": "fraktur",
            "separator_id": "bar_heavy",
        },
    )

    assert name.startswith("🎫┃")
    assert "ticket-0219" not in name
    assert ("𝔱" in name or "𝖙" in name)


def test_ticket_closed_name_uses_archive_icon_and_style():
    name = build_ticket_channel_name(
        219,
        closed=True,
        options={
            "theme_id": "gothic_clean",
            "strength": 5,
            "font": "fraktur",
            "separator_id": "bar_heavy",
        },
    )

    assert name.startswith("📦┃")
    assert "closed-0219" not in name
    assert ("𝔠" in name or "𝖈" in name)


def test_category_saved_rule_overrides_current_theme():
    name = build_ticket_channel_name(
        219,
        options={
            "theme_id": "support_ticket",
            "strength": 2,
            "category_format_locks": {
                "123": {
                    "enabled": True,
                    "theme_id": "gothic_clean",
                    "strength": 5,
                    "font": "fraktur",
                    "separator_id": "bar_heavy",
                }
            },
        },
        parent_category_id=123,
    )

    assert name.startswith("🎫┃")
    assert ("𝔱" in name or "𝖙" in name)


def test_global_saved_rule_applies_when_category_has_no_rule():
    name = build_ticket_channel_name(
        7,
        options={
            "theme_id": "support_ticket",
            "strength": 2,
            "format_lock_global": {
                "enabled": True,
                "theme_id": "gothic_clean",
                "strength": 5,
                "font": "fraktur",
                "separator_id": "bar_thin",
            },
        },
    )

    assert name.startswith("🎫│")
    assert ("𝔱" in name or "𝖙" in name)
