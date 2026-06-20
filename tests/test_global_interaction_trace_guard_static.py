from pathlib import Path


SOURCE = Path("stoney_verify/startup_guards/global_interaction_trace_guard.py").read_text()


def test_elite_logger_name_and_safe_defaults():
    assert "Dank Shield Elite Error Logger" in SOURCE
    assert 'DANK_SHIELD_ELITE_ERROR_LOGGER' in SOURCE
    assert 'DANK_SHIELD_INTERACTION_TRACE' in SOURCE
    assert 'DANK_SHIELD_COMPONENT_TRACE' in SOURCE
    assert 'os.getenv("DANK_SHIELD_INTERACTION_TRACE", "true")' not in SOURCE
    assert 'os.getenv("DANK_SHIELD_INTERACTION_TRACE", "false")' not in SOURCE


def test_trace_logs_are_optional_but_errors_are_supported():
    assert "def _trace_enabled" in SOURCE
    assert "def _error_logger_enabled" in SOURCE
    assert "🚨 dank_error" in SOURCE
    assert "DANK-" in SOURCE
    assert "Error ID" in SOURCE


def test_global_trace_wraps_discord_dispatch_layer():
    required = [
        "_patch_framework_dispatch",
        "CommandTree",
        "_call",
        "_invoke_with_namespace",
        "_do_call",
        "tree_call_start",
        "command_invoke_start",
        "command_do_call_start",
        "tree_call_slow_unacked",
    ]

    for phrase in required:
        assert phrase in SOURCE


def test_component_trace_wraps_view_dispatch():
    required = [
        "_patch_component_dispatch",
        "_scheduled_task",
        "view_item_start",
        "view_item_slow_unacked",
        "view_item_exception",
        "component_view_item_exception",
        "dank_component_trace",
    ]

    for phrase in required:
        assert phrase in SOURCE


def test_no_risky_full_rebrand():
    assert "from stoney_verify import client" in SOURCE
    assert "Internal package imports stay unchanged for safety." in SOURCE


def test_button_spam_tracking_is_evidence_only():
    required = [
        "DANK_SHIELD_BUTTON_SPAM_WATCH",
        "DANK_SHIELD_BUTTON_SPAM_WINDOW_SECONDS",
        "DANK_SHIELD_BUTTON_SPAM_THRESHOLD",
        "DANK_SHIELD_BUTTON_SPAM_LOG_COOLDOWN_SECONDS",
        "_track_button_spam",
        "dank_button_spam",
        "This does not block, defer, punish, or mutate behavior.",
    ]

    for phrase in required:
        assert phrase in SOURCE

    assert "_track_button_spam(interaction)" in SOURCE

