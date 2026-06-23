from pathlib import Path


HELPER = Path("stoney_verify/panel_lifecycle.py").read_text(encoding="utf-8")
VERIFY = Path("stoney_verify/verify_ui.py").read_text(encoding="utf-8")
PROFILE = Path("stoney_verify/commands_ext/public_self_roles_group.py").read_text(encoding="utf-8")
TICKET = Path("stoney_verify/commands_ext/public_ticket_panel_clean.py").read_text(encoding="utf-8")


def test_shared_lifecycle_helper_exists():
    assert "PUBLIC_PANEL_LIFECYCLE_TEXT" in HELPER
    assert "Public panel" in HELPER
    assert "temporary by design" in HELPER
    assert "cannot inspect old dismissed/expired private menus" in HELPER


def test_verify_panel_explains_public_panel_vs_private_links():
    assert "public_panel_lifecycle_text" in VERIFY
    assert "Verify panel" in VERIFY
    assert "Private upload links" in VERIFY


def test_profile_panels_explain_public_panel_vs_private_menus():
    assert "public_panel_lifecycle_text" in PROFILE
    assert "Profile Panel" in PROFILE
    assert "Private profile menus/dropdowns" in PROFILE
    assert "Profile Builder" in PROFILE or "Profile Builder result panel" in PROFILE
    assert "Advanced role panel" in PROFILE


def test_ticket_panel_health_explains_public_panel_vs_private_menus():
    assert "public_panel_lifecycle_text" in TICKET
    assert "Create Ticket panel" in TICKET
    assert "Private ticket type menus/confirm screens" in TICKET


def test_public_panels_still_use_persistent_views():
    assert "super().__init__(timeout=None)" in VERIFY
    assert "super().__init__(timeout=None)" in PROFILE
    assert "class PublicCreateTicketPanelView" in TICKET
    ticket_start = TICKET.index("class PublicCreateTicketPanelView")
    ticket_end = TICKET.index("async def _component_fallback_listener", ticket_start)
    assert "super().__init__(timeout=None)" in TICKET[ticket_start:ticket_end]
