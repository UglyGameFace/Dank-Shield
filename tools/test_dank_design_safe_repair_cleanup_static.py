from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = (ROOT / "stoney_verify/commands_ext/public_design_studio.py").read_text(encoding="utf-8")
MAJORITY_GUARD = (ROOT / "stoney_verify/startup_guards/server_design_majority_layout_guard.py").read_text(encoding="utf-8")
ENHANCEMENTS = (ROOT / "stoney_verify/commands_ext/public_design_enhancements.py").read_text(encoding="utf-8")
STARTUP = (ROOT / "stoney_verify/startup_guards/__init__.py").read_text(encoding="utf-8")


def test_native_design_owns_enhancements_not_startup_loading() -> None:
    assert "activate_public_design_enhancements" in ENHANCEMENTS
    assert "This is not a startup guard" in ENHANCEMENTS
    assert "server_design_majority_layout_guard" in ENHANCEMENTS
    assert "server_design_majority_layout_guard" not in STARTUP
    assert "server_design_strict_layout_guard" not in STARTUP


def test_fix_mismatched_names_copy_mentions_saved_rules_win() -> None:
    assert "Saved rules win" in PUBLIC
    assert "Live Majority is preview-only when locks exist" in PUBLIC
    assert "reviews saved rules first" in PUBLIC
    assert "copies the live server style" not in PUBLIC
    assert "ignores saved rules" not in PUBLIC


def test_native_majority_context_respects_locks() -> None:
    assert "respect_locks=True" in PUBLIC
    assert "repair_options = majority.apply_majority_to_options(studio, options, analysis, respect_locks=True)" in PUBLIC


def test_live_majority_apply_requires_high_confidence_and_no_saved_rules() -> None:
    assert "Preview Live Majority" in MAJORITY_GUARD
    assert "live_apply_allowed" in MAJORITY_GUARD
    assert "bool(confidence.get(\"apply_allowed\"))" in MAJORITY_GUARD
    assert "saved_rules == 0" in MAJORITY_GUARD
    assert "DesignPreviewView(can_apply=live_apply_allowed)" in MAJORITY_GUARD
    assert "__live_majority_apply_disabled_by_saved_rules" in MAJORITY_GUARD


def test_majority_plan_respects_saved_locks_when_present() -> None:
    assert "respect_saved_locks = bool(_saved_rule_count(options))" in MAJORITY_GUARD
    assert "respect_locks=respect_saved_locks" in MAJORITY_GUARD
    assert "respect_locks=False" not in MAJORITY_GUARD


if __name__ == "__main__":
    for test in (
        test_native_design_owns_enhancements_not_startup_loading,
        test_fix_mismatched_names_copy_mentions_saved_rules_win,
        test_native_majority_context_respects_locks,
        test_live_majority_apply_requires_high_confidence_and_no_saved_rules,
        test_majority_plan_respects_saved_locks_when_present,
    ):
        test()
        print(f"PASS {test.__name__}")
