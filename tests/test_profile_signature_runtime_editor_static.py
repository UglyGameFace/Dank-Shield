from pathlib import Path


def test_profile_channel_fallback_requires_image_attachment_permission():
    source = Path("stoney_verify/commands_ext/public_profile_cards_core.py").read_text(encoding="utf-8")
    assert 'missing.append("Attach Files")' in source


def test_live_runtime_does_not_send_explicit_null_view():
    source = Path("stoney_verify/profile_card_runtime.py").read_text(encoding="utf-8")
    assert 'if rendered.view is not None:' in source
    assert 'payload["view"] = rendered.view' in source
    assert '"view": rendered.view' not in source


def test_live_runtime_logs_send_failures_instead_of_swallowing_them():
    source = Path("stoney_verify/profile_card_runtime.py").read_text(encoding="utf-8")
    assert "live_profile_card send failed" in source
    assert "live_profile_card state write failed" in source
