from pathlib import Path


SOURCE = Path("stoney_verify/startup_guards/global_interaction_trace_guard.py").read_text()


def test_global_trace_logs_all_interactions_not_only_design():
    assert "interaction_trace" in SOURCE
    assert "cmd={names}" in SOURCE
    assert "_on_interaction" in SOURCE
    assert 'bot.add_listener(_on_interaction, "on_interaction")' in SOURCE


def test_global_trace_captures_app_command_errors():
    assert "_tree_on_error" in SOURCE
    assert "tree_error" in SOURCE
    assert "traceback_start" in SOURCE
    assert "tree.on_error = chained_on_error" in SOURCE


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


def test_trace_uses_dank_shield_public_name():
    assert "Dank Shield interaction evidence logger active" in SOURCE
    assert "STONEY" not in SOURCE


def test_guard_is_evidence_only():
    assert "does not fix behavior" in SOURCE
    assert "evidence-only" in SOURCE.lower()
