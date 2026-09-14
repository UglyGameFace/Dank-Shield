from pathlib import Path


LOADER = Path("stoney_verify/startup_guards/__init__.py").read_text()


def test_global_interaction_trace_guard_remains_historical_only():
    assert '"stoney_verify.startup_guards.global_interaction_trace_guard"' in LOADER
    assert '"stoney_verify.startup_guards.command_safety"' not in LOADER
