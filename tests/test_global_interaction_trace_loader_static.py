from pathlib import Path


LOADER = Path("stoney_verify/startup_guards/__init__.py").read_text()


def test_global_interaction_trace_guard_is_loaded_early():
    assert '"stoney_verify.startup_guards.global_interaction_trace_guard"' in LOADER

    command_safety = LOADER.find('"stoney_verify.startup_guards.command_safety"')
    trace_guard = LOADER.find('"stoney_verify.startup_guards.global_interaction_trace_guard"')

    assert command_safety != -1
    assert trace_guard != -1
    assert command_safety < trace_guard or trace_guard < LOADER.find('"stoney_verify.startup_guards.slash_command_cleanup"')
