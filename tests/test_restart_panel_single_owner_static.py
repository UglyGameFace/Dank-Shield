from pathlib import Path


CLEAN = Path(
    "stoney_verify/commands_ext/public_ticket_panel_clean.py"
).read_text(encoding="utf-8")

LEGACY = Path(
    "stoney_verify/tickets_new/panel.py"
).read_text(encoding="utf-8")

STARTUP = Path(
    "stoney_verify/startup_guards/__init__.py"
).read_text(encoding="utf-8")


def _legacy_ticket_view_block() -> str:
    start = LEGACY.index(
        "class TicketPanelView(discord.ui.View):"
    )
    end = LEGACY.index(
        "class StaffGhostTicketView(discord.ui.View):",
        start,
    )
    return LEGACY[start:end]


def test_clean_module_exposes_one_canonical_click_handler():
    assert "async def handle_public_ticket_panel_click(" in CLEAN
    assert "await _handle_panel_button(interaction)" in CLEAN


def test_old_ticket_button_remains_restart_compatible():
    block = _legacy_ticket_view_block()

    assert 'custom_id="ticket_create"' in block
    assert "handle_public_ticket_panel_click" in block


def test_old_ticket_button_no_longer_owns_ticket_creation():
    block = _legacy_ticket_view_block()

    assert "_create_ticket_for_member" not in block
    assert "_fetch_dashboard_ticket_categories" not in block
    assert "TicketReasonModal" not in block
    assert "_ticket_panel_guild_context" not in block


def test_obsolete_ticket_panel_monkey_patch_is_not_loaded():
    assert (
        "startup_guards.legacy_public_ticket_panel_disable"
        not in STARTUP
    )


def test_clean_and_legacy_ids_remain_distinct():
    assert (
        'PANEL_BUTTON_CUSTOM_ID = '
        '"sv:ticket:panel:create:clean:v1"'
        in CLEAN
    )
    assert 'custom_id="ticket_create"' in _legacy_ticket_view_block()
