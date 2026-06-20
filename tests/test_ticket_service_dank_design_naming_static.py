from pathlib import Path


SOURCE = Path("stoney_verify/tickets_new/service.py").read_text()


def test_ticket_service_imports_dank_design_ticket_naming_adapter():
    assert "from ..services.server_design_ticket_naming import build_ticket_channel_name" in SOURCE


def test_ticket_create_uses_design_aware_name_helper():
    assert "await _format_ticket_channel_name_for_guild(guild, number, closed=False, parent=parent)" in SOURCE


def test_ticket_identity_uses_design_aware_name_helper():
    assert "await _format_ticket_channel_name_for_guild(" in SOURCE
    assert "closed=closed" in SOURCE


def test_ticket_close_uses_design_aware_closed_name():
    assert "closed=True" in SOURCE
    assert "Ticket closed" in SOURCE
