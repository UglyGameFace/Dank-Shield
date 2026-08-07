from pathlib import Path


RUNTIME = Path("stoney_verify/welcome_card_runtime.py").read_text(encoding="utf-8")
SETUP = Path("stoney_verify/welcome_event_services.py").read_text(encoding="utf-8")


def test_join_card_channel_selection_is_authoritative() -> None:
    assert "def resolve_join_card_channel(" in RUNTIME
    assert 'explicit_id = _safe_int(_cfg_value(cfg, "join_welcome_channel_id", None), 0)' in RUNTIME
    assert "A stale explicit channel never silently reroutes" in RUNTIME
    assert "configured join-card channel" in RUNTIME


def test_live_send_uses_resolution_reason_not_silent_fallback() -> None:
    assert "channel, route_reason = resolve_join_card_channel(member.guild, cfg)" in RUNTIME
    assert "delivery skipped" in RUNTIME
    assert "reason={route_reason}" in RUNTIME


def test_legacy_fallback_only_when_no_dedicated_join_channel_selected() -> None:
    assert "static welcome channel compatibility fallback" in RUNTIME
    explicit_index = RUNTIME.index("if explicit_id > 0:")
    legacy_index = RUNTIME.index("legacy_id =", explicit_index)
    assert explicit_index < legacy_index
    assert "return None, f\"configured join-card channel {explicit_id} is missing\"" in RUNTIME


def test_setup_copy_explains_exact_join_channel_behavior() -> None:
    assert "Join channel selection is exact" in SETUP
    assert "join announcements pause instead of posting somewhere else" in SETUP
