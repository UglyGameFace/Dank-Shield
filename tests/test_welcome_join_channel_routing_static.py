from pathlib import Path


RUNTIME = Path("stoney_verify/startup_guards/welcome_member_events_guard.py").read_text()
SETUP = Path("stoney_verify/welcome_event_services.py").read_text()


def test_join_channel_selection_is_authoritative():
    assert "def _join_channel_resolution" in RUNTIME
    assert 'explicit_id = _safe_int(_cfg_value(cfg, "join_welcome_channel_id", None), 0)' in RUNTIME
    assert "do not silently post somewhere else" in RUNTIME
    assert "configured Join channel" in RUNTIME


def test_send_join_uses_resolution_reason_not_silent_fallback():
    assert "channel, route_reason = _join_channel_resolution(member.guild, cfg)" in RUNTIME
    assert "welcome_member_events join target unavailable" in RUNTIME
    assert "reason={route_reason}" in RUNTIME


def test_legacy_fallback_only_when_no_explicit_join_channel_selected():
    assert "Legacy compatibility only when no explicit Join channel was selected." in RUNTIME
    assert "legacy fallback via" in RUNTIME
    assert "legacy fallback by name" in RUNTIME


def test_setup_copy_explains_exact_join_channel_behavior():
    assert "Join channel selection is exact" in SETUP
    assert "join welcomes pause instead of posting somewhere else" in SETUP
    assert "Join welcomes will pause instead of posting to another channel." in SETUP
