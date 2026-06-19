from stoney_verify.startup_guards import _ALWAYS_SHOW_PREFIXES, _STARTUP_GUARDS


def test_majority_layout_guard_loads_after_strict_layout_guard():
    guards = list(_STARTUP_GUARDS)
    strict = "stoney_verify.startup_guards.server_design_strict_layout_guard"
    majority = "stoney_verify.startup_guards.server_design_majority_layout_guard"

    assert strict in guards
    assert majority in guards
    assert guards.index(strict) < guards.index(majority)


def test_majority_layout_guard_startup_log_is_visible():
    assert "✅ server_design_majority_layout_guard active" in _ALWAYS_SHOW_PREFIXES
