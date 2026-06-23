from pathlib import Path


VERIFY_UI = Path("stoney_verify/verify_ui.py").read_text(encoding="utf-8")


def test_verify_panel_view_is_persistent():
    assert "class VerifyView" in VERIFY_UI
    assert "super().__init__(timeout=None)" in VERIFY_UI
    assert 'custom_id="sv:verify:get"' in VERIFY_UI
    assert 'custom_id="sv:verify:vc"' in VERIFY_UI
    assert 'custom_id="sv:verify:raw"' in VERIFY_UI


def test_verify_interaction_acknowledges_before_owner_lookup():
    start = VERIFY_UI.index("async def maybe_handle_verify_ui_interaction(")
    end = VERIFY_UI.index("__all__", start)
    body = VERIFY_UI[start:end]

    assert "await _ack_verify_ui_interaction(interaction)" in body
    assert body.index("await _ack_verify_ui_interaction(interaction)") < body.index("owner_id = await _resolve_ticket_owner_id")


def test_verify_interaction_does_not_direct_response_after_ack():
    start = VERIFY_UI.index("async def maybe_handle_verify_ui_interaction(")
    end = VERIFY_UI.index("__all__", start)
    body = VERIFY_UI[start:end]

    assert "await interaction.response.send_message(" not in body
    assert "await _reply_verify_ui(interaction," in body


def test_verify_embed_explains_link_expires_panel_stays_active():
    assert "This panel stays active" in VERIFY_UI
    assert "Generated links expire" in VERIFY_UI
